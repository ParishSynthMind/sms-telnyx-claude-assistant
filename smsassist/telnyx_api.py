"""A small client for Telnyx's Messaging API v2 (POST /v2/messages).

Auth is a bearer API key. A 2xx answer is {"data": {...message...}}; an error is
{"errors": [{"code", "title", "detail", ...}]}. Rate limits (429) and server errors
(5xx) are retried with backoff, under one Idempotency-Key so a retry can't send twice; other 4xx errors are not, because sending the same
bad request again only repeats the failure (or, worse, the text)."""
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx


class TelnyxError(Exception):
    def __init__(self, status: int, errors: List[dict]):
        self.status = status
        self.errors = errors or [{"title": f"HTTP {status}"}]
        first = self.errors[0]
        self.code = str(first.get("code", ""))
        self.title = first.get("title", "")
        self.detail = first.get("detail", "")
        super().__init__(f"Telnyx {status} {self.code} {self.title}: {self.detail}".strip())


@dataclass
class SentMessage:
    id: str
    status: str
    parts: int
    cost_usd: Optional[float]
    raw: dict


class TelnyxClient:
    def __init__(self, api_key: str, base_url: str = "https://api.telnyx.com/v2", http: Optional[httpx.Client] = None,
                 max_retries: int = 3, backoff: float = 1.0, sleep=time.sleep):
        if not api_key:
            raise ValueError("TELNYX_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=15)
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                        "Accept": "application/json"}
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep

    def _request(self, method: str, path: str, json_body: Optional[dict] = None,
                 idempotency_key: str = "") -> dict:
        url = f"{self.base_url}{path}"
        headers = dict(self.headers)
        if idempotency_key:
            # The same key on every retry: if the first attempt did reach Telnyx, the
            # retry gets the original answer back instead of a second text.
            headers["Idempotency-Key"] = idempotency_key
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.request(method, url, json=json_body, headers=headers)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self.sleep(self.backoff * 2 ** attempt)
                continue
            if r.status_code < 300:
                return r.json() if r.content else {}
            retryable = r.status_code == 429 or r.status_code >= 500
            if retryable and attempt < self.max_retries:
                wait = r.headers.get("Retry-After")
                self.sleep(float(wait) if wait and wait.isdigit() else self.backoff * 2 ** attempt)
                continue
            try:
                errors = r.json().get("errors", [])
            except ValueError:
                errors = [{"title": r.text[:200]}]
            raise TelnyxError(r.status_code, errors)
        raise RuntimeError("unreachable")

    def send_message(self, to: str, text: str, from_number: str = "", messaging_profile_id: str = "",
                     webhook_url: str = "", idempotency_key: str = "") -> SentMessage:
        if not from_number and not messaging_profile_id:
            raise ValueError("need a from number or a messaging profile id")
        body = {"to": to, "text": text}
        if from_number:
            body["from"] = from_number
        if messaging_profile_id:
            body["messaging_profile_id"] = messaging_profile_id
        if webhook_url:
            body["webhook_url"] = webhook_url
        data = self._request("POST", "/messages", body, idempotency_key).get("data", {})
        to_status = (data.get("to") or [{}])[0].get("status", "queued")
        cost = (data.get("cost") or {}).get("amount")
        return SentMessage(id=data["id"], status=to_status, parts=int(data.get("parts") or 1),
                           cost_usd=float(cost) if cost not in (None, "") else None, raw=data)

    def get_message(self, message_id: str) -> dict:
        return self._request("GET", f"/messages/{message_id}").get("data", {})
