import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from smsassist import signature as sig

BODY = b'{"data":{"event_type":"message.received","id":"e1"}}'


@pytest.fixture
def keys():
    priv = Ed25519PrivateKey.generate()
    return priv, sig.load_public_key(sig.public_key_b64(priv))


def test_valid_signature_passes(keys):
    priv, pub = keys
    headers = sig.sign(BODY, priv, 1_700_000_000)
    sig.verify(BODY, headers, pub, now=1_700_000_010)


def test_independent_computation_matches_telnyx_scheme(keys):
    """Telnyx signs "{timestamp}|{body}" with ed25519 and sends it base64 encoded."""
    priv, pub = keys
    raw = priv.sign(b"1700000000|" + BODY)
    headers = {"Telnyx-Signature-Ed25519": base64.b64encode(raw).decode(), "Telnyx-Timestamp": "1700000000"}
    sig.verify(BODY, headers, pub, now=1_700_000_000)  # header names are case-insensitive


def test_changed_body_fails(keys):
    priv, pub = keys
    headers = sig.sign(BODY, priv, 1_700_000_000)
    with pytest.raises(sig.SignatureError, match="does not match"):
        sig.verify(BODY.replace(b"e1", b"e2"), headers, pub, now=1_700_000_000)


def test_reserialised_json_fails(keys):
    priv, pub = keys
    headers = sig.sign(BODY, priv, 1_700_000_000)
    with pytest.raises(sig.SignatureError):
        sig.verify(b'{"data": {"event_type": "message.received", "id": "e1"}}', headers, pub, now=1_700_000_000)


def test_other_key_fails(keys):
    priv, _ = keys
    other = sig.load_public_key(sig.public_key_b64(Ed25519PrivateKey.generate()))
    with pytest.raises(sig.SignatureError):
        sig.verify(BODY, sig.sign(BODY, priv, 1_700_000_000), other, now=1_700_000_000)


def test_stale_and_future_timestamps_fail(keys):
    priv, pub = keys
    headers = sig.sign(BODY, priv, 1_700_000_000)
    with pytest.raises(sig.SignatureError, match="tolerance"):
        sig.verify(BODY, headers, pub, now=1_700_000_301)
    with pytest.raises(sig.SignatureError, match="tolerance"):
        sig.verify(BODY, headers, pub, now=1_699_999_699)


def test_timestamp_is_part_of_the_signature(keys):
    priv, pub = keys
    headers = sig.sign(BODY, priv, 1_700_000_000)
    headers[sig.TIMESTAMP_HEADER] = "1700000001"
    with pytest.raises(sig.SignatureError, match="does not match"):
        sig.verify(BODY, headers, pub, now=1_700_000_000)


@pytest.mark.parametrize("headers", [
    {},
    {sig.SIGNATURE_HEADER: "abc"},
    {sig.TIMESTAMP_HEADER: "1700000000"},
    {sig.SIGNATURE_HEADER: "not base64!!", sig.TIMESTAMP_HEADER: "1700000000"},
    {sig.SIGNATURE_HEADER: "AAAA", sig.TIMESTAMP_HEADER: "soon"},
])
def test_malformed_headers_fail(keys, headers):
    _, pub = keys
    with pytest.raises(sig.SignatureError):
        sig.verify(BODY, headers, pub, now=1_700_000_000)


def test_bad_public_key_is_reported():
    with pytest.raises(sig.SignatureError):
        sig.load_public_key("not-a-key")
