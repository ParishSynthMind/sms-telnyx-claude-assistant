"""A faithful stand-in for the parts of Telnyx this app uses, so every path can be
tested without an account.

It follows Telnyx's documented shapes: POST /v2/messages with a bearer key, the
{"data": ...} answer and the {"errors": [...]} error body, the Idempotency-Key replay,
and webhooks (message.sent, message.finalized, message.received) in Telnyx's
envelope, signed with an ed25519 key exactly as Telnyx signs them
(base64 signature over "{timestamp}|{body}"). It can also behave like a trial account
(texts only to verified numbers), answer STOP itself the way Telnyx does
(autoresponse_type), fail on demand (429, 500, carrier failure), and deliver webhooks
twice or out of order, which real Telnyx also does.

Where the mock has to guess (the exact error code a trial account gets, say), the
guess is marked in the code."""
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import signature
from .segments import encoding, segment_count

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
TELNYX_STOP = {"STOP", "STOPALL", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
TELNYX_START = {"START", "UNSTOP"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _err(status: int, code: str, title: str, detail: str, pointer: str = "") -> JSONResponse:
    e = {"code": code, "title": title, "detail": detail}
    if pointer:
        e["source"] = {"pointer": pointer}
    return JSONResponse({"errors": [e]}, status_code=status)


class MockTelnyx:
    def __init__(self, api_key: str = "KEYmock_test", numbers: Optional[List[str]] = None,
                 private_key: Optional[Ed25519PrivateKey] = None, profile_id: str = "40017e3a-mock-profile",
                 deliver: Optional[Callable[[str, bytes, dict], int]] = None, profile_webhook_url: str = ""):
        self.api_key = api_key
        self.numbers = set(numbers or ["+15550100100"])
        self.private_key = private_key or Ed25519PrivateKey.generate()
        self.public_key_b64 = signature.public_key_b64(self.private_key)
        self.profile_id = profile_id
        self.profile_webhook_url = profile_webhook_url
        self.deliver = deliver or self._http_deliver
        self.trial_verified: Optional[set] = None  # set of numbers = behave like a trial account
        self.autorespond = False                   # answer STOP/START/HELP like Telnyx does
        self.fail_next: List[int] = []             # HTTP statuses to return before succeeding
        self.carrier_fail: set = set()             # destinations whose delivery fails
        self.duplicate_webhooks = False
        self.reverse_webhooks = False
        self.sent: List[dict] = []                 # every message accepted
        self.requests: List[dict] = []             # every POST /v2/messages, with headers
        self.webhooks: List[dict] = []             # every webhook delivered: url, status, event
        self.idem: Dict[str, dict] = {}
        self.queued: List[tuple] = []              # receipts waiting for flush()
        self.async_webhooks = False                # True: flush by itself shortly after each send
        self.webhook_delay = 0.3
        self.lock = threading.Lock()
        self.app = self._build()

    # webhooks
    def _http_deliver(self, url: str, body: bytes, headers: dict) -> int:
        return httpx.post(url, content=body, headers=headers, timeout=10).status_code

    def envelope(self, event_type: str, payload: dict, attempt: int = 1, url: str = "") -> dict:
        return {"data": {"event_type": event_type, "id": str(uuid.uuid4()), "occurred_at": _now(),
                         "payload": payload, "record_type": "event"},
                "meta": {"attempt": attempt, "delivered_to": url}}

    def send_webhook(self, url: str, env: dict, sign_ts: Optional[int] = None) -> int:
        body = json.dumps(env).encode()
        headers = {"content-type": "application/json", **signature.sign(body, self.private_key, sign_ts)}
        status = self.deliver(url, body, headers)
        self.webhooks.append({"url": url, "status": status, "event_type": env["data"]["event_type"],
                              "event_id": env["data"]["id"], "attempt": env["meta"]["attempt"]})
        return status

    def _emit(self, url: str, events: List[tuple]) -> None:
        if not url:
            return
        envs = [self.envelope(t, p, url=url) for t, p in events]
        if self.reverse_webhooks:
            envs.reverse()
        for env in envs:
            status = self.send_webhook(url, env)
            if self.duplicate_webhooks or status >= 300:
                # Telnyx retries a failed delivery; it can also deliver a success twice.
                env2 = json.loads(json.dumps(env))
                env2["meta"]["attempt"] = 2
                self.send_webhook(url, env2)

    def inbound(self, url: str, frm: str, to: str, text: str) -> dict:
        """A text from a phone to one of our numbers, as Telnyx delivers it."""
        payload = {"id": str(uuid.uuid4()), "record_type": "message", "direction": "inbound", "type": "SMS",
                   "messaging_profile_id": self.profile_id,
                   "from": {"phone_number": frm, "carrier": "T-Mobile USA", "line_type": "Wireless"},
                   "to": [{"phone_number": to, "status": "webhook_delivered"}],
                   "text": text, "encoding": encoding(text), "parts": segment_count(text), "received_at": _now(),
                   "errors": []}
        word = " ".join(text.strip().split()).upper()
        if self.autorespond and (word in TELNYX_STOP or word in TELNYX_START or word == "HELP"):
            payload["autoresponse_type"] = "STOP" if word in TELNYX_STOP else ("START" if word in TELNYX_START
                                                                                 else "HELP")
        env = self.envelope("message.received", payload, url=url)
        self.send_webhook(url, env)
        if self.duplicate_webhooks:
            env2 = json.loads(json.dumps(env))
            env2["meta"]["attempt"] = 2
            self.send_webhook(url, env2)
        return payload

    # API
    def _build(self) -> FastAPI:
        app = FastAPI(title="Mock Telnyx v2")

        @app.post("/v2/messages")
        async def send(request: Request, authorization: str = Header(default=""),
                       idempotency_key: str = Header(default="")):
            raw = await request.body()
            self.requests.append({"headers": dict(request.headers), "body": raw.decode(errors="replace")})
            if authorization != f"Bearer {self.api_key}":
                return _err(401, "10009", "Authentication failed", "The API key is missing or invalid.")
            if self.fail_next:
                status = self.fail_next.pop(0)
                if status == 429:
                    r = _err(429, "10011", "Too many requests", "Rate limit exceeded.")
                    r.headers["Retry-After"] = "1"
                    return r
                return _err(status, "10007", "Unexpected error", "An unexpected error occurred.")
            with self.lock:
                if idempotency_key and idempotency_key in self.idem:
                    return JSONResponse(self.idem[idempotency_key])  # replayed, not sent again
            try:
                body = json.loads(raw)
            except ValueError:
                return _err(400, "10015", "Bad Request", "The request body is not valid JSON.")
            to, frm, text = body.get("to", ""), body.get("from", ""), body.get("text", "")
            if not to:
                return _err(422, "40002", "Missing required field", "The 'to' field is required.", "/to")
            if not E164.match(to):
                return _err(422, "40001", "Invalid destination", "'to' must be in +E.164 format.", "/to")
            if not frm and not body.get("messaging_profile_id"):
                return _err(422, "40002", "Missing required field", "Give 'from' or 'messaging_profile_id'.",
                            "/from")
            if frm and frm not in self.numbers:
                return _err(403, "40300", "Forbidden",
                            f"The from number {frm} is not assigned to a messaging profile.", "/from")
            if not text:
                return _err(422, "40002", "Missing required field", "'text' is required for SMS.", "/text")
            if len(text) > 1600:
                return _err(422, "42202", "Message body too long", "SMS max: 1,600 characters.", "/text")
            if segment_count(text) > 10:
                return _err(422, "42202", "Message body too long", "More than 10 segments.", "/text")
            if self.trial_verified is not None and to not in self.trial_verified:
                # Telnyx's docs say trial accounts can only text their verified number; the
                # exact code it returns is not documented, so this one is a stand-in.
                return _err(403, "40300", "Forbidden", "Trial accounts can only send to a verified number.", "/to")
            parts = segment_count(text)
            msg = {"record_type": "message", "direction": "outbound", "id": str(uuid.uuid4()), "type": "SMS",
                   "messaging_profile_id": body.get("messaging_profile_id") or self.profile_id,
                   "organization_id": "mock-org",
                   "from": {"phone_number": frm or sorted(self.numbers)[0], "carrier": "Telnyx", "line_type": "VoIP"},
                   "to": [{"phone_number": to, "status": "queued", "carrier": "T-Mobile USA",
                           "line_type": "Wireless"}],
                   "text": text, "num_chars": len(text), "encoding": encoding(text), "parts": parts,
                   "cost": {"amount": f"{0.004 * parts:.4f}", "currency": "USD"},
                   "received_at": _now(), "sent_at": None, "completed_at": None,
                   "webhook_url": body.get("webhook_url"), "errors": []}
            answer = {"data": msg}
            with self.lock:
                if idempotency_key:
                    self.idem[idempotency_key] = answer
                self.sent.append(msg)
            url = body.get("webhook_url") or (self.profile_webhook_url if body.get("use_profile_webhooks", True)
                                              else "")
            self._after_send(url, msg)
            return JSONResponse(answer)

        @app.get("/v2/messages/{message_id}")
        def get(message_id: str, authorization: str = Header(default="")):
            if authorization != f"Bearer {self.api_key}":
                return _err(401, "10009", "Authentication failed", "The API key is missing or invalid.")
            for m in self.sent:
                if m["id"] == message_id:
                    return {"data": m}
            return _err(404, "10005", "Resource not found", "No message with that id.")

        return app

    def _after_send(self, url: str, msg: dict) -> None:
        sent = json.loads(json.dumps(msg))
        sent["to"][0]["status"] = "sent"
        sent["sent_at"] = _now()
        final = json.loads(json.dumps(sent))
        final["completed_at"] = _now()
        if msg["to"][0]["phone_number"] in self.carrier_fail:
            final["to"][0]["status"] = "delivery_failed"
            final["errors"] = [{"code": "40300", "title": "Destination unreachable",
                                "detail": "The carrier could not deliver the message."}]
        else:
            final["to"][0]["status"] = "delivered"
        with self.lock:
            self.queued.append((url, [("message.sent", sent), ("message.finalized", final)]))
        if self.async_webhooks:
            # Like the real service: the receipts arrive a moment after the API answer.
            threading.Timer(self.webhook_delay, self.flush).start()

    def flush(self) -> int:
        """Deliver the queued delivery receipts. Returns how many messages they covered."""
        with self.lock:
            batch, self.queued = self.queued, []
        for url, events in batch:
            self._emit(url, events)
        return len(batch)
