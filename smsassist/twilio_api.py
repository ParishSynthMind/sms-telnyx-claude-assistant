"""A small client for Twilio's Programmable Messaging REST API, plus the translation of
Twilio's webhooks into the event shape the messenger already reads.

Send: POST /2010-04-01/Accounts/{AccountSid}/Messages.json, form-encoded, HTTP Basic auth
(Account SID : Auth Token). A 201 answer is the Message resource ({"sid": "SM...",
"status": "queued", "num_segments": "1", ...}); an error is {"code": 21608, "message":
"...", "more_info": "...", "status": 400}.

Retries are narrower than on Telnyx, on purpose: Twilio's Messages API takes no
idempotency key, so a request that may have reached Twilio is never sent again (that
could text the person twice). Only a 429 (Twilio refused it, nothing was sent) and a
connection that never opened are retried. A 5xx or a timeout is recorded as failed with
the reason, for a person to look at."""
import time
from typing import Dict, List, Optional

import httpx

from .errors import ProviderError
from .telnyx_api import SentMessage

API_BASE = "https://api.twilio.com/2010-04-01"

# Twilio's MessageStatus values -> the statuses the store already uses (Telnyx's words).
STATUS_MAP = {
    "accepted": "queued", "scheduled": "queued", "queued": "queued",
    "sending": "sending", "sent": "sent",
    "delivered": "delivered", "read": "read",
    "undelivered": "delivery_failed", "failed": "sending_failed", "canceled": "canceled",
    "receiving": "received", "received": "received",
}

# Codes worth knowing by name (Twilio's error dictionary).
KNOWN_ERRORS = {
    20003: "Authentication failed",
    20429: "Too many requests",
    21211: "Invalid 'To' phone number",
    21408: "Region not enabled for this account",
    21606: "The 'From' number is not a valid, SMS-capable number on this account",
    21608: "Trial accounts can only send to verified numbers",
    21610: "The recipient has replied STOP (Twilio blocks the send)",
    21614: "'To' is not a mobile number",
    21617: "Body exceeds the 1,600 character limit",
    30003: "Unreachable destination handset",
    30005: "Unknown destination handset",
    30006: "Landline or unreachable carrier",
    30007: "Carrier filtered the message",
    30008: "Unknown error from the carrier",
    30034: "US A2P 10DLC: message from an unregistered number",
    572006: "Trial accounts can only use predefined SMS templates",  # LIVE-CHECKED 2026-11-20
}

# LIVE-CHECKED 2026-11-20: a 2026 Twilio trial account refuses any free-text Body (error
# 572006). It only sends Twilio's own predefined texts, picked by putting the template's
# name in Body (the console shows e.g. "sms_appointment_reminders"). TWILIO_TRIAL_TEMPLATE
# makes the app send that name instead of its own text, so the whole loop (inbound,
# signatures, Claude, reply, receipts) can be run live on a trial; the text the app MEANT
# to send is kept in the store. Never set it on a paid account.


class TwilioError(ProviderError):
    def __init__(self, status: int, body: Optional[dict]):
        body = body or {}
        code = body.get("code")
        title = KNOWN_ERRORS.get(code, "") if isinstance(code, int) else ""
        super().__init__("Twilio", status, [{"code": code if code is not None else "",
                                             "title": title or f"HTTP {status}",
                                             "detail": body.get("message", "")}])
        self.more_info = body.get("more_info", "")


def _cost(price) -> Optional[float]:
    # Twilio reports price as a negative string ("-0.00790") once known, null before.
    if price in (None, ""):
        return None
    return abs(float(price))


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, base_url: str = API_BASE,
                 http: Optional[httpx.Client] = None, max_retries: int = 3, backoff: float = 1.0, sleep=time.sleep,
                 trial_template: str = ""):
        if not account_sid or not auth_token:
            raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must both be set")
        self.account_sid = account_sid
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=15)
        self.auth = (account_sid, auth_token)
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep
        self.trial_template = trial_template.strip()

    def _request(self, method: str, path: str, form: Optional[Dict[str, str]] = None) -> dict:
        url = f"{self.base_url}/Accounts/{self.account_sid}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.request(method, url, data=form, auth=self.auth, headers={"Accept": "application/json"})
            except httpx.ConnectError:
                # The connection never opened, so Twilio never saw the request: safe to retry.
                if attempt == self.max_retries:
                    raise
                self.sleep(self.backoff * 2 ** attempt)
                continue
            if r.status_code < 300:
                return r.json() if r.content else {}
            if r.status_code == 429 and attempt < self.max_retries:
                wait = r.headers.get("Retry-After")
                self.sleep(float(wait) if wait and wait.isdigit() else self.backoff * 2 ** attempt)
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"message": r.text[:200]}
            raise TwilioError(r.status_code, body)
        raise RuntimeError("unreachable")

    def send_message(self, to: str, text: str, from_number: str = "", messaging_profile_id: str = "",
                     webhook_url: str = "", idempotency_key: str = "") -> SentMessage:
        """Same signature as TelnyxClient.send_message. messaging_profile_id is a Twilio
        Messaging Service SID (MG...); webhook_url becomes StatusCallback. Twilio has no
        idempotency key, so idempotency_key is accepted and not sent."""
        if not from_number and not messaging_profile_id:
            raise ValueError("need a from number or a messaging service sid")
        form = {"To": to, "Body": self.trial_template or text}
        if messaging_profile_id:
            form["MessagingServiceSid"] = messaging_profile_id
        else:
            form["From"] = from_number
        if webhook_url:
            form["StatusCallback"] = webhook_url
        data = self._request("POST", "/Messages.json", form)
        return SentMessage(id=data["sid"], status=STATUS_MAP.get(data.get("status", ""), data.get("status", "queued")),
                           parts=int(data.get("num_segments") or 1), cost_usd=_cost(data.get("price")), raw=data)

    def get_message(self, sid: str) -> dict:
        return self._request("GET", f"/Messages/{sid}.json")


# webhooks -> the messenger's event shape

def _one(params: List[tuple]) -> Dict[str, str]:
    return {k: v for k, v in params}


def inbound_envelope(params: List[tuple], provider_answers_keywords: bool) -> dict:
    """An incoming SMS (Twilio's form post to the number's SMS URL) as a message.received event.

    MessageSid is the event id, so Twilio's retry of the same text is seen as a duplicate.
    Twilio answers STOP/START/HELP itself when its opt-out handling is on (Advanced
    Opt-Out says so in OptOutType; US/Canada long codes do it by default), so
    autoresponse_type is set and the messenger does not send a second confirmation."""
    p = _one(params)
    body = p.get("Body", "")
    channel = p.get("From", "").split(":", 1)[0] if ":" in p.get("From", "") else "sms"
    if channel != "sms":
        # LIVE-CHECKED 2026-11-20: the same Twilio number also takes WhatsApp, and those
        # arrive as From "whatsapp:+44...". This is an SMS assistant: it records and ignores
        # them rather than answering a WhatsApp chat by SMS.
        mid = p.get("MessageSid") or p.get("SmsSid", "")
        return {"data": {"id": mid, "event_type": f"message.received.{channel}",
                         "payload": {"id": mid, "channel": channel}}, "meta": {"provider": "twilio"}}
    payload = {"id": p.get("MessageSid") or p.get("SmsSid", ""),
               "from": {"phone_number": p.get("From", "")},
               "to": [{"phone_number": p.get("To", "")}],
               "text": body, "parts": int(p.get("NumSegments") or 1)}
    opt = (p.get("OptOutType") or "").upper()
    if opt in ("STOP", "START", "HELP"):
        payload["autoresponse_type"] = opt
    elif provider_answers_keywords:
        from .policy import keyword

        kw = keyword(body)
        if kw:
            payload["autoresponse_type"] = kw
    return {"data": {"id": payload["id"], "event_type": "message.received", "payload": payload},
            "meta": {"provider": "twilio"}}


def status_envelope(params: List[tuple]) -> dict:
    """A StatusCallback as a message.sent / message.finalized event. Twilio sends no event
    id, so sid + status is the id: a retry of the same callback is a duplicate, the next
    status for the same message is not."""
    p = _one(params)
    sid = p.get("MessageSid") or p.get("SmsSid", "")
    raw = (p.get("MessageStatus") or p.get("SmsStatus") or "").lower()
    status = STATUS_MAP.get(raw, raw)
    payload = {"id": sid, "to": [{"phone_number": p.get("To", ""), "status": status}], "errors": []}
    code = p.get("ErrorCode")
    if code:
        title = KNOWN_ERRORS.get(int(code), "carrier error") if code.isdigit() else "carrier error"
        payload["errors"] = [{"code": code, "title": title}]
    from .service import FINAL

    event_type = "message.finalized" if status in FINAL else "message.sent"
    return {"data": {"id": f"{sid}:{raw}", "event_type": event_type, "payload": payload},
            "meta": {"provider": "twilio"}}
