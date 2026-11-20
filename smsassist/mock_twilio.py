"""A stand-in for the parts of Twilio Programmable Messaging this app uses, so the Twilio
path is tested with no account.

It follows Twilio's documented shapes: POST /2010-04-01/Accounts/{sid}/Messages.json,
form-encoded, Basic auth; a 201 Message resource; the {"code", "message", "more_info",
"status"} error body; StatusCallback form posts (sent, then delivered or undelivered
with an ErrorCode); and inbound SMS form posts to the number's SMS URL. Every webhook is
signed with X-Twilio-Signature the way Twilio signs it (HMAC-SHA1 of URL + sorted
params with the Auth Token). It can behave like a trial account (texts only to verified
numbers, error 21608, and only Twilio's predefined template texts, error 572006), block a
number that replied STOP (21610), answer keywords itself (OptOutType), fail on demand
(429, 500, carrier failure 30003), and deliver webhooks twice or out of order.

Checked against the live trial account on 2026-11-20 where marked LIVE-CHECKED; the rest
follows Twilio's docs."""
import base64
import json
import re
import threading
import uuid
from email.utils import formatdate
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import twilio_signature
from .segments import segment_count

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
# LIVE-CHECKED 2026-11-20 on a trial account: free text is refused with 572006, and these
# template names in Body are delivered as Twilio's own text (seen on the phone / in the log).
TRIAL_TEMPLATES = {
    "sms_customer_support": "Hi, this is Support. We received your message and will assist shortly. "
                            "Reply with details. Test message from Twilio.",
    "sms_account_alerts": "Alert: Your account balance is below $100. Please deposit funds to avoid "
                          "overdraft fees. Test message from Twilio.",
}
STOP = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
START = {"START", "UNSTOP", "YES"}
HELP = {"HELP", "INFO"}


def _sid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _err(status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message,
                         "more_info": f"https://www.twilio.com/docs/errors/{code}", "status": status},
                        status_code=status)


class MockTwilio:
    def __init__(self, account_sid: str = "AC" + "0" * 32, auth_token: str = "mock-auth-token",
                 numbers: Optional[List[str]] = None, sms_url: str = "",
                 deliver: Optional[Callable[[str, bytes, dict], int]] = None):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.numbers = set(numbers or ["+15550100100"])
        self.sms_url = sms_url
        self.deliver = deliver or self._http_deliver
        self.trial_verified: Optional[set] = None   # set of numbers = behave like a trial account
        self.opted_out: set = set()                 # Twilio's own STOP list (21610)
        self.autorespond = False                    # answer keywords itself, OptOutType on the webhook
        self.fail_next: List[int] = []
        self.carrier_fail: set = set()
        self.duplicate_webhooks = False
        self.reverse_webhooks = False
        self.sent: List[dict] = []
        self.requests: List[dict] = []
        self.webhooks: List[dict] = []
        self.auto_replies: List[dict] = []
        self.queued: List[tuple] = []
        self.lock = threading.Lock()
        self.app = self._build()

    def _http_deliver(self, url: str, body: bytes, headers: dict) -> int:
        return httpx.post(url, content=body, headers=headers, timeout=10).status_code

    def post_webhook(self, url: str, params: Dict[str, str], retry_of: str = "") -> int:
        items = list(params.items())
        headers = twilio_signature.sign(self.auth_token, url, items)
        headers["I-Twilio-Idempotency-Token"] = retry_of or str(uuid.uuid4())
        status = self.deliver(url, urlencode(items).encode(), headers)
        self.webhooks.append({"url": url, "status": status, "params": dict(params)})
        return status

    def _post_twice_if_needed(self, url: str, params: Dict[str, str]) -> None:
        status = self.post_webhook(url, params)
        if self.duplicate_webhooks or status >= 300:
            self.post_webhook(url, params, retry_of="retry")

    def inbound(self, frm: str, to: str, text: str, url: str = "") -> dict:
        """A text from a phone to one of our numbers, as Twilio posts it to the SMS URL."""
        url = url or self.sms_url
        params = {"ToCountry": "US", "SmsMessageSid": "", "NumMedia": "0", "SmsSid": "", "SmsStatus": "received",
                  "Body": text, "To": to, "NumSegments": str(segment_count(text)), "MessageSid": "",
                  "AccountSid": self.account_sid, "From": frm, "ApiVersion": "2010-04-01"}
        sid = _sid("SM")
        params.update(SmsMessageSid=sid, SmsSid=sid, MessageSid=sid)
        word = " ".join(text.strip().strip(".!").split()).upper()
        kw = "STOP" if word in STOP or word == "STOP ALL" else ("START" if word in START else
                                                                ("HELP" if word in HELP else ""))
        if kw == "STOP":
            self.opted_out.add(frm)
        elif kw == "START":
            self.opted_out.discard(frm)
        if self.autorespond and kw:
            params["OptOutType"] = kw
            self.auto_replies.append({"to": frm, "keyword": kw})
        self._post_twice_if_needed(url, params)
        return params

    def _build(self) -> FastAPI:
        app = FastAPI(title="Mock Twilio Messaging")
        base = "/2010-04-01/Accounts/{sid}"

        def authed(sid: str, authorization: str) -> bool:
            want = "Basic " + base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
            return sid == self.account_sid and authorization == want

        @app.post(base + "/Messages.json")
        async def send(sid: str, request: Request, authorization: str = Header(default="")):
            raw = await request.body()
            self.requests.append({"headers": dict(request.headers), "body": raw.decode(errors="replace")})
            if not authed(sid, authorization):
                return _err(401, 20003, "Authenticate")
            if self.fail_next:
                status = self.fail_next.pop(0)
                if status == 429:
                    r = _err(429, 20429, "Too Many Requests")
                    r.headers["Retry-After"] = "1"
                    return r
                return _err(status, 20500, "An internal server error has occurred")
            form = dict(parse_qsl(raw.decode(), keep_blank_values=True))
            to, frm, body = form.get("To", ""), form.get("From", ""), form.get("Body", "")
            if not to:
                return _err(400, 21604, "A 'To' phone number is required.")
            if not E164.match(to):
                return _err(400, 21211, f"The 'To' number {to} is not a valid phone number.")
            if not frm and not form.get("MessagingServiceSid"):
                return _err(400, 21603, "A 'From' phone number is required.")
            if frm and frm not in self.numbers:
                return _err(400, 21606, f"The From phone number {frm} is not a valid, SMS-capable inbound "
                                        "phone number or short code for your account.")
            if not body:
                return _err(400, 21602, "Message body is required.")
            if len(body) > 1600:
                return _err(400, 21617, "The concatenated message body exceeds the 1600 character limit.")
            if self.trial_verified is not None and to not in self.trial_verified:
                return _err(400, 21608, f"The number {to} is unverified. Trial accounts cannot send messages "
                                        "to unverified numbers; verify it, or purchase a Twilio number.")
            if to in self.opted_out:
                return _err(400, 21610, "Attempt to send to unsubscribed recipient")
            if self.trial_verified is not None:
                if body not in TRIAL_TEMPLATES:
                    return _err(400, 572006, "Invalid template name. Trial accounts can only use predefined "
                                             "SMS templates.")
                body = TRIAL_TEMPLATES[body]
            msg_sid = _sid("SM")
            now = formatdate(usegmt=True)
            msg = {"sid": msg_sid, "account_sid": self.account_sid, "to": to,
                   "from": frm or sorted(self.numbers)[0], "messaging_service_sid": form.get("MessagingServiceSid"),
                   "body": body, "status": "queued", "num_segments": str(segment_count(body)), "num_media": "0",
                   "direction": "outbound-api", "api_version": "2010-04-01", "price": None, "price_unit": "USD",
                   "error_code": None, "error_message": None, "date_created": now, "date_updated": now,
                   "date_sent": None, "uri": f"/2010-04-01/Accounts/{self.account_sid}/Messages/{msg_sid}.json"}
            with self.lock:
                self.sent.append(msg)
            self._after_send(form.get("StatusCallback", ""), msg)
            return JSONResponse(msg, status_code=201)

        @app.get(base + "/Messages/{msg_sid}.json")
        def get(sid: str, msg_sid: str, authorization: str = Header(default="")):
            if not authed(sid, authorization):
                return _err(401, 20003, "Authenticate")
            for m in self.sent:
                if m["sid"] == msg_sid:
                    return m
            return _err(404, 20404, "The requested resource was not found")

        return app

    def _after_send(self, url: str, msg: dict) -> None:
        common = {"AccountSid": self.account_sid, "MessageSid": msg["sid"], "SmsSid": msg["sid"],
                  "From": msg["from"], "To": msg["to"], "ApiVersion": "2010-04-01"}
        sent = {**common, "MessageStatus": "sent", "SmsStatus": "sent"}
        if msg["to"] in self.carrier_fail:
            final = {**common, "MessageStatus": "undelivered", "SmsStatus": "undelivered", "ErrorCode": "30003"}
        else:
            final = {**common, "MessageStatus": "delivered", "SmsStatus": "delivered"}
        with self.lock:
            self.queued.append((url, [sent, final]))

    def flush(self) -> int:
        with self.lock:
            batch, self.queued = self.queued, []
        for url, events in batch:
            if not url:
                continue
            if self.reverse_webhooks:
                events = list(reversed(events))
            for params in events:
                self._post_twice_if_needed(url, params)
        return len(batch)

    def dump(self) -> str:
        return json.dumps(self.sent, indent=2)
