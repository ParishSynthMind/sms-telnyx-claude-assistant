"""Telnyx webhook signatures (ed25519).

Telnyx signs every webhook with its private key. Two headers come with the request:
  telnyx-signature-ed25519   base64 signature
  telnyx-timestamp           unix seconds when it was signed
The signed message is f"{timestamp}|{raw body}". We verify it with the account's public
key (Mission Control, Keys & Credentials, Public Key, base64) on the raw bytes, never on
re-serialised JSON, and refuse timestamps too far from now so an old request can't be
replayed."""
import base64
import binascii
import time
from typing import Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNATURE_HEADER = "telnyx-signature-ed25519"
TIMESTAMP_HEADER = "telnyx-timestamp"
DEFAULT_TOLERANCE = 300  # seconds


class SignatureError(Exception):
    pass


def load_public_key(b64: str) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("public key is not a base64 ed25519 key") from exc


def signed_message(timestamp: str, body: bytes) -> bytes:
    return timestamp.encode() + b"|" + body


def verify(body: bytes, headers: Mapping[str, str], public_key: Ed25519PublicKey,
           tolerance: int = DEFAULT_TOLERANCE, now: Optional[float] = None) -> None:
    """Raise SignatureError unless the request is signed by Telnyx and recent."""
    lower = {k.lower(): v for k, v in headers.items()}
    sig_b64 = lower.get(SIGNATURE_HEADER)
    ts = lower.get(TIMESTAMP_HEADER)
    if not sig_b64 or not ts:
        raise SignatureError("missing signature headers")
    if not ts.strip().isdigit():
        raise SignatureError("bad timestamp")
    now = time.time() if now is None else now
    if abs(now - int(ts)) > tolerance:
        raise SignatureError("timestamp outside tolerance")
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except binascii.Error as exc:
        raise SignatureError("signature is not base64") from exc
    try:
        public_key.verify(sig, signed_message(ts.strip(), body))
    except InvalidSignature as exc:
        raise SignatureError("signature does not match") from exc


def sign(body: bytes, private_key: Ed25519PrivateKey, timestamp: Optional[int] = None) -> dict:
    """Headers as Telnyx would send them. For the mock and the tests only."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    sig = private_key.sign(signed_message(ts, body))
    return {SIGNATURE_HEADER: base64.b64encode(sig).decode(), TIMESTAMP_HEADER: ts}


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()
