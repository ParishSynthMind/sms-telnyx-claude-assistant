"""Twilio webhook signatures (X-Twilio-Signature).

Twilio signs every webhook with the account's Auth Token: HMAC-SHA1 over the full URL it
called (scheme, host, path and query string, exactly as configured in the console or
given as StatusCallback), followed by every POST parameter sorted by name (then value), each written
as name + value with nothing in between. The result is base64. We rebuild that string
from the raw form body and compare in constant time.

The URL must be the PUBLIC one Twilio called. Behind a tunnel or proxy the app sees
http://localhost:8000/..., so the app takes the public base from PUBLIC_BASE_URL rather
than trusting request.url. Twilio has no timestamp in the signature, so replays are
stopped by the message-sid idempotency in the store, not here."""
import base64
import hashlib
import hmac
from typing import Iterable, Mapping, Tuple
from urllib.parse import parse_qsl

SIGNATURE_HEADER = "x-twilio-signature"


class TwilioSignatureError(Exception):
    pass


def parse_form(body: bytes) -> list:
    """Form body -> list of (name, value), keeping repeats and blank values."""
    return parse_qsl(body.decode("utf-8"), keep_blank_values=True)


def compute(auth_token: str, url: str, params: Iterable[Tuple[str, str]]) -> str:
    data = url + "".join(k + v for k, v in sorted(set(params)))
    digest = hmac.new(auth_token.encode(), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify(auth_token: str, url: str, body: bytes, headers: Mapping[str, str]) -> list:
    """Raise TwilioSignatureError unless the request was signed with this account's token
    for this URL. Returns the parsed form parameters."""
    if not auth_token:
        raise TwilioSignatureError("no auth token configured")
    lower = {k.lower(): v for k, v in headers.items()}
    got = lower.get(SIGNATURE_HEADER, "")
    if not got:
        raise TwilioSignatureError("missing X-Twilio-Signature")
    try:
        params = parse_form(body)
    except UnicodeDecodeError as exc:
        raise TwilioSignatureError("body is not UTF-8 form data") from exc
    want = compute(auth_token, url, params)
    if not hmac.compare_digest(got.encode(), want.encode()):
        raise TwilioSignatureError("signature does not match")
    return params


def sign(auth_token: str, url: str, params: Iterable[Tuple[str, str]]) -> dict:
    """Headers as Twilio would send them. For the mock, the tests and the signed-replay check."""
    return {"X-Twilio-Signature": compute(auth_token, url, list(params)),
            "Content-Type": "application/x-www-form-urlencoded"}
