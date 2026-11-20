"""X-Twilio-Signature: HMAC-SHA1 of URL + sorted params, base64, keyed with the Auth Token."""
from urllib.parse import urlencode

import pytest

from smsassist import twilio_signature as ts

DOC_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
DOC_PARAMS = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+12349013030"), ("Digits", "1234"),
              ("From", "+12349013030"), ("To", "+18005551212")]


def test_matches_twilios_published_example():
    # The worked example in Twilio's webhook-security docs (token 12345).
    assert ts.compute("12345", DOC_URL, DOC_PARAMS) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_param_order_in_the_body_does_not_matter():
    assert ts.compute("12345", DOC_URL, list(reversed(DOC_PARAMS))) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_verify_accepts_a_good_signature_and_returns_params():
    body = urlencode(DOC_PARAMS).encode()
    params = ts.verify("12345", DOC_URL, body, {"X-Twilio-Signature": "0/KCTR6DLpKmkAf8muzZqo1nDgQ="})
    assert dict(params)["Digits"] == "1234"


def test_header_name_is_case_insensitive():
    body = urlencode(DOC_PARAMS).encode()
    ts.verify("12345", DOC_URL, body, {"x-twilio-signature": "0/KCTR6DLpKmkAf8muzZqo1nDgQ="})


@pytest.mark.parametrize("change", ["token", "url", "body", "query"])
def test_any_change_breaks_the_signature(change):
    token, url, params = "12345", DOC_URL, list(DOC_PARAMS)
    if change == "token":
        token = "54321"
    elif change == "url":
        url = "https://evil.example.com/myapp.php?foo=1&bar=2"
    elif change == "body":
        params[2] = ("Digits", "9999")
    else:
        url = "https://mycompany.com/myapp.php?foo=1&bar=3"
    with pytest.raises(ts.TwilioSignatureError):
        ts.verify(token, url, urlencode(params).encode(), {"X-Twilio-Signature": "0/KCTR6DLpKmkAf8muzZqo1nDgQ="})


def test_missing_header_and_missing_token_are_refused():
    body = urlencode(DOC_PARAMS).encode()
    with pytest.raises(ts.TwilioSignatureError, match="missing"):
        ts.verify("12345", DOC_URL, body, {})
    with pytest.raises(ts.TwilioSignatureError, match="no auth token"):
        ts.verify("", DOC_URL, body, {"X-Twilio-Signature": "x"})


def test_blank_values_and_unicode_are_signed_as_sent():
    params = [("Body", "Café ☕ ok?"), ("MediaUrl0", ""), ("From", "+447700900123")]
    headers = ts.sign("tok", "https://a.example/twilio/sms", params)
    ts.verify("tok", "https://a.example/twilio/sms", urlencode(params).encode(), headers)


def test_sign_sets_form_content_type():
    assert ts.sign("t", "https://x", [])["Content-Type"] == "application/x-www-form-urlencoded"
