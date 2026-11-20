"""The whole app with Twilio as the provider: signed form webhooks in, REST sends out, the
same STOP, quiet-hours, hand-over and AI-disclosure rules as on Telnyx."""
from datetime import time as dtime
from urllib.parse import urlencode

import pytest

from smsassist import twilio_signature
from smsassist.config import Settings

from .conftest import CUSTOMER, OUR_NUMBER, TW_BASE, TW_TOKEN, TwilioRig


def events(rig, name):
    return [t for t in rig.store.trail(1000) if t["event"] == name]


def test_template_send_is_delivered_and_receipts_are_signed(twrig):
    r = twrig.messenger.send_template(CUSTOMER, "missed_call")
    assert r["status"] == "queued" and r["telnyx_id"].startswith("SM")
    twrig.mock.flush()
    assert all(w["status"] == 204 for w in twrig.mock.webhooks)
    assert twrig.outbound()[0]["status"] == "delivered"
    assert "StatusCallback=https%3A%2F%2Fsms.example.com%2Ftwilio%2Fstatus" in twrig.mock.requests[0]["body"]


def test_inbound_question_gets_one_ai_reply_with_the_disclosure(twrig):
    twrig.text_in("Is there parking?")
    out = twrig.outbound()
    assert len(out) == 1 and out[0]["kind"] == "ai"
    assert out[0]["text"].startswith("I'm an AI assistant for Brightside Dental.")
    assert out[0]["status"] == "delivered"
    twrig.text_in("What are your opening hours?")
    assert not twrig.outbound()[1]["text"].startswith("I'm an AI assistant")


def test_inbound_answer_is_empty_twiml(twrig):
    params = [("MessageSid", "SM" + "a" * 32), ("From", CUSTOMER), ("To", OUR_NUMBER), ("Body", "hi")]
    url = TW_BASE + "/twilio/sms"
    resp = twrig.client.post("/twilio/sms", content=urlencode(params).encode(),
                             headers=twilio_signature.sign(TW_TOKEN, url, params))
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/xml")
    assert resp.text.endswith("<Response></Response>")


@pytest.mark.parametrize("path", ["/twilio/sms", "/twilio/status"])
def test_unsigned_and_forged_requests_are_401(twrig, path):
    params = [("MessageSid", "SM" + "b" * 32), ("From", CUSTOMER), ("To", OUR_NUMBER), ("Body", "hi"),
              ("MessageStatus", "delivered")]
    body = urlencode(params).encode()
    assert twrig.client.post(path, content=body,
                             headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 401
    forged = twilio_signature.sign("not-the-token", TW_BASE + path, params)
    assert twrig.client.post(path, content=body, headers=forged).status_code == 401
    assert len(events(twrig, "signature_rejected")) == 2
    assert twrig.store.messages() == []


def test_signature_uses_the_public_url_not_the_local_one(twrig):
    """Behind a tunnel the app sees localhost; a signature made for that URL must fail."""
    params = [("MessageSid", "SM" + "c" * 32), ("From", CUSTOMER), ("To", OUR_NUMBER), ("Body", "hi")]
    local = twilio_signature.sign(TW_TOKEN, "http://127.0.0.1:8000/twilio/sms", params)
    assert twrig.client.post("/twilio/sms", content=urlencode(params).encode(), headers=local).status_code == 401


def test_duplicate_inbound_is_answered_once(twrig):
    twrig.mock.duplicate_webhooks = True
    twrig.text_in("Do you take walk-ins?")
    assert len(twrig.outbound()) == 1
    assert events(twrig, "duplicate_webhook")


def test_out_of_order_receipts_keep_delivered(twrig):
    twrig.mock.reverse_webhooks = True
    twrig.messenger.send_template(CUSTOMER, "follow_up", {"name": "Sam"})
    twrig.mock.flush()
    assert twrig.outbound()[0]["status"] == "delivered"
    assert events(twrig, "receipt_out_of_order")


def test_carrier_failure_is_recorded_with_twilios_code(twrig):
    twrig.mock.carrier_fail.add(CUSTOMER)
    twrig.messenger.send_template(CUSTOMER, "missed_call")
    twrig.mock.flush()
    m = twrig.outbound()[0]
    assert m["status"] == "delivery_failed" and m["reason"].startswith("30003")


def test_stop_on_a_uk_number_sends_our_one_confirmation_and_blocks_after(twrig):
    twrig.settings.provider_answers_keywords = "no"  # outside US/Canada Twilio does not answer STOP
    twrig.text_in("STOP")
    out = twrig.outbound()
    assert len(out) == 1 and "unsubscribed" in out[0]["text"]
    assert twrig.store.is_opted_out(CUSTOMER)
    r = twrig.messenger.send_template(CUSTOMER, "follow_up", {"name": "Sam"})
    assert r == {"id": r["id"], "status": "blocked", "reason": "opted_out", "segments": r["segments"]}
    assert len(twrig.mock.requests) == 1  # the blocked text never reached Twilio


def test_stop_when_twilio_answered_itself_sends_nothing_more(twrig):
    twrig.mock.autorespond = True
    twrig.text_in("stop")
    assert twrig.outbound() == [] and twrig.store.is_opted_out(CUSTOMER)
    assert twrig.mock.auto_replies == [{"to": CUSTOMER, "keyword": "STOP"}]


def test_us_number_defaults_to_twilio_handling_keywords(settings):
    settings.from_number = "+15550100100"
    assert settings.provider_replies_to_keywords
    settings.from_number = "+447700900100"
    assert not settings.provider_replies_to_keywords
    settings.provider_answers_keywords = "yes"
    assert settings.provider_replies_to_keywords


def test_start_after_stop_opts_back_in_and_discloses_again(twrig):
    twrig.text_in("Is there parking?")
    twrig.text_in("STOP")
    twrig.text_in("START")
    assert not twrig.store.is_opted_out(CUSTOMER)
    twrig.text_in("Are you open today?")
    assert twrig.outbound()[-1]["text"].startswith("I'm an AI assistant for")


def test_twilios_own_stop_block_21610_is_recorded_not_crashed(twrig):
    twrig.mock.opted_out.add(CUSTOMER)  # opted out at Twilio, not (yet) in our store
    r = twrig.messenger.send_template(CUSTOMER, "follow_up", {"name": "Sam"})
    assert r["status"] == "failed" and r["reason"].startswith("400 21610")


def test_quiet_hours_hold_marketing_but_not_replies(settings):
    settings.quiet_hours = (dtime(0, 0), dtime(23, 59))
    rig = TwilioRig(settings)
    assert rig.messenger.send_template(CUSTOMER, "follow_up", {"name": "Sam"})["reason"] == "quiet_hours"
    rig.text_in("Is there parking?")
    assert rig.outbound()[-1]["kind"] == "ai" and rig.outbound()[-1]["status"] == "delivered"


def test_handover_stops_the_ai_until_released(twrig):
    twrig.text_in("I want to speak to a person")
    assert twrig.store.contact(CUSTOMER)["handover"]
    n = len(twrig.outbound())
    twrig.text_in("hello?")
    assert len(twrig.outbound()) == n
    twrig.messenger.release(CUSTOMER)
    twrig.text_in("hello again")
    assert len(twrig.outbound()) == n + 1


def test_trial_without_template_mode_records_572006(settings):
    rig = TwilioRig(settings)
    rig.mock.trial_verified = {CUSTOMER}
    r = rig.messenger.send_template(CUSTOMER, "missed_call")
    assert r["status"] == "failed" and "572006" in r["reason"]
    assert events(rig, "send_failed")[0]["detail"]["detail"].startswith("Invalid template name")


def test_trial_template_mode_runs_the_whole_loop_and_keeps_our_text(settings):
    rig = TwilioRig(settings, trial_template="sms_customer_support")
    rig.mock.trial_verified = {CUSTOMER}
    rig.text_in("Is there parking?")
    out = rig.outbound()
    assert out[0]["status"] == "delivered" and out[0]["text"].startswith("I'm an AI assistant")
    assert rig.mock.sent[0]["body"].startswith("Hi, this is Support.")


def test_trial_refuses_texts_to_unverified_numbers(settings):
    rig = TwilioRig(settings, trial_template="sms_customer_support")
    rig.mock.trial_verified = {"+447700900999"}
    r = rig.messenger.send_template(CUSTOMER, "missed_call")
    assert r["status"] == "failed" and "21608" in r["reason"]


def test_twilio_routes_404_when_the_provider_is_telnyx(rig):
    assert rig.client.post("/twilio/sms", content=b"Body=hi").status_code == 404


def test_health_reports_the_provider(twrig):
    h = twrig.client.get("/health").json()
    assert h["provider"] == "twilio" and h["signed_webhooks"] is True


def test_settings_from_env_twilio(monkeypatch):
    for k, v in {"SMS_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "ACx", "TWILIO_AUTH_TOKEN": "t",
                 "TWILIO_FROM_NUMBER": "+447700900100", "PUBLIC_BASE_URL": "https://p.example/",
                 "TWILIO_TRIAL_TEMPLATE": "sms_customer_support"}.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.provider == "twilio" and s.from_number == "+447700900100"
    assert s.webhook_url == "https://p.example/twilio/status" and s.public_url("/twilio/sms") == \
        "https://p.example/twilio/sms"
    assert s.twilio_trial_template == "sms_customer_support"


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "carrier-pigeon")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_a_whatsapp_message_on_the_same_number_is_recorded_and_ignored(twrig):
    """Found live on 20 Nov 2026: a WhatsApp message to the trial number arrived as
    From 'whatsapp:+44...' and the first version failed on it behind the 200."""
    params = [("MessageSid", "SM" + "d" * 32), ("From", "whatsapp:" + CUSTOMER), ("To", "whatsapp:" + OUR_NUMBER),
              ("Body", "Is there parking?")]
    url = TW_BASE + "/twilio/sms"
    r = twrig.client.post("/twilio/sms", content=urlencode(params).encode(),
                          headers=twilio_signature.sign(TW_TOKEN, url, params))
    assert r.status_code == 200
    assert twrig.outbound() == [] and twrig.store.messages() == []
    assert events(twrig, "webhook_ignored")[0]["detail"]["event_type"] == "message.received.whatsapp"
