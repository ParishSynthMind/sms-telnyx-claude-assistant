"""The Twilio REST client against the mock: auth, form body, errors, the narrow retry rule."""
import httpx
import pytest
from fastapi.testclient import TestClient

from smsassist import twilio_api
from smsassist.mock_twilio import MockTwilio
from smsassist.twilio_api import TwilioClient, TwilioError

SID, TOKEN, OUR, THEM = "AC" + "2" * 32, "tok", "+15550100100", "+15550100199"


def client(mock, **kw):
    return TwilioClient(SID, TOKEN, "http://tw/2010-04-01", http=TestClient(mock.app, base_url="http://tw"),
                        sleep=lambda s: None, **kw)


@pytest.fixture
def mock():
    return MockTwilio(account_sid=SID, auth_token=TOKEN, numbers=[OUR])


def test_send_posts_a_form_with_basic_auth(mock):
    sent = client(mock).send_message(THEM, "hello", from_number=OUR, webhook_url="https://x/twilio/status")
    assert sent.id.startswith("SM") and sent.status == "queued" and sent.parts == 1
    req = mock.requests[0]
    assert req["headers"]["authorization"].startswith("Basic ")
    assert "application/x-www-form-urlencoded" in req["headers"]["content-type"]
    assert "StatusCallback=https" in req["body"] and "Body=hello" in req["body"]


def test_messaging_service_replaces_from(mock):
    client(mock).send_message(THEM, "hi", messaging_profile_id="MG" + "3" * 32)
    assert "MessagingServiceSid=MG" in mock.requests[0]["body"] and "From=" not in mock.requests[0]["body"]


def test_needs_a_sender(mock):
    with pytest.raises(ValueError):
        client(mock).send_message(THEM, "hi")


def test_needs_credentials():
    with pytest.raises(ValueError):
        TwilioClient("", "tok")


def test_bad_credentials_raise_20003(mock):
    c = TwilioClient(SID, "wrong", "http://tw/2010-04-01", http=TestClient(mock.app, base_url="http://tw"))
    with pytest.raises(TwilioError) as e:
        c.send_message(THEM, "hi", from_number=OUR)
    assert e.value.status == 401 and e.value.code == "20003" and e.value.provider == "Twilio"


@pytest.mark.parametrize("to,frm,code", [("555", OUR, "21211"), (THEM, "+15559999999", "21606")])
def test_validation_errors_carry_twilios_code(mock, to, frm, code):
    with pytest.raises(TwilioError) as e:
        client(mock).send_message(to, "hi", from_number=frm)
    assert e.value.code == code and e.value.status == 400


def test_trial_refuses_unverified_numbers_21608(mock):
    mock.trial_verified = {"+447700900123"}
    with pytest.raises(TwilioError) as e:
        client(mock).send_message(THEM, "hi", from_number=OUR)
    assert e.value.code == "21608"
    assert "verified" in e.value.title


def test_trial_refuses_free_text_572006(mock):
    mock.trial_verified = {THEM}
    with pytest.raises(TwilioError) as e:
        client(mock).send_message(THEM, "our own words", from_number=OUR)
    assert e.value.code == "572006" and "predefined" in e.value.title


def test_trial_template_mode_sends_the_template_name(mock):
    mock.trial_verified = {THEM}
    client(mock, trial_template="sms_customer_support").send_message(THEM, "our own words", from_number=OUR)
    assert "Body=sms_customer_support" in mock.requests[0]["body"]
    assert mock.sent[0]["body"].startswith("Hi, this is Support.")


def test_opted_out_recipient_21610(mock):
    mock.opted_out.add(THEM)
    with pytest.raises(TwilioError) as e:
        client(mock).send_message(THEM, "hi", from_number=OUR)
    assert e.value.code == "21610"


def test_429_is_retried_and_then_sends_once(mock):
    mock.fail_next = [429, 429]
    client(mock).send_message(THEM, "hi", from_number=OUR)
    assert len(mock.sent) == 1 and len(mock.requests) == 3


def test_500_is_not_retried_because_twilio_has_no_idempotency_key(mock):
    mock.fail_next = [500]
    with pytest.raises(TwilioError) as e:
        client(mock).send_message(THEM, "hi", from_number=OUR)
    assert e.value.status == 500 and len(mock.requests) == 1


def test_429_gives_up_after_max_retries(mock):
    mock.fail_next = [429] * 5
    with pytest.raises(TwilioError) as e:
        client(mock, max_retries=2).send_message(THEM, "hi", from_number=OUR)
    assert e.value.status == 429 and len(mock.requests) == 3


def test_connect_error_is_retried_but_read_timeout_is_not():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(201, json={"sid": "SMx", "status": "queued", "num_segments": "1"})

    c = TwilioClient(SID, TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert c.send_message(THEM, "hi", from_number=OUR).id == "SMx" and calls["n"] == 2

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    c2 = TwilioClient(SID, TOKEN, http=httpx.Client(transport=httpx.MockTransport(timeout)), sleep=lambda s: None)
    with pytest.raises(httpx.ReadTimeout):
        c2.send_message(THEM, "hi", from_number=OUR)


def test_price_is_read_as_a_positive_cost():
    def handler(request):
        return httpx.Response(201, json={"sid": "SMp", "status": "sent", "num_segments": "2", "price": "-0.01580"})

    c = TwilioClient(SID, TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))
    sent = c.send_message(THEM, "hi", from_number=OUR)
    assert sent.cost_usd == pytest.approx(0.0158) and sent.parts == 2 and sent.status == "sent"


def test_get_message(mock):
    c = client(mock)
    sid = c.send_message(THEM, "hi", from_number=OUR).id
    assert c.get_message(sid)["sid"] == sid


@pytest.mark.parametrize("twilio,ours", [("undelivered", "delivery_failed"), ("failed", "sending_failed"),
                                         ("delivered", "delivered"), ("accepted", "queued"), ("sent", "sent")])
def test_status_words_map_to_the_stores_words(twilio, ours):
    assert twilio_api.STATUS_MAP[twilio] == ours


def test_status_envelope_ids_split_by_status_and_carry_errors():
    a = twilio_api.status_envelope([("MessageSid", "SM1"), ("MessageStatus", "sent")])
    b = twilio_api.status_envelope([("MessageSid", "SM1"), ("MessageStatus", "undelivered"), ("ErrorCode", "30003")])
    assert a["data"]["id"] != b["data"]["id"]
    assert a["data"]["event_type"] == "message.sent" and b["data"]["event_type"] == "message.finalized"
    assert b["data"]["payload"]["errors"][0]["title"] == "Unreachable destination handset"


def test_inbound_envelope_reads_opt_out_type_and_the_us_default():
    p = [("MessageSid", "SM9"), ("From", THEM), ("To", OUR), ("Body", "stop")]
    assert "autoresponse_type" not in twilio_api.inbound_envelope(p, False)["data"]["payload"]
    assert twilio_api.inbound_envelope(p, True)["data"]["payload"]["autoresponse_type"] == "STOP"
    q = p[:-1] + [("Body", "STOP"), ("OptOutType", "STOP")]
    assert twilio_api.inbound_envelope(q, False)["data"]["payload"]["autoresponse_type"] == "STOP"
