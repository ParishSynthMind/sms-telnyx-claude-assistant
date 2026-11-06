"""The whole thing end to end: our app and the mock Telnyx talking to each other,
with signed webhooks both ways."""
import json
import logging
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from smsassist import signature
from tests.conftest import CUSTOMER, OUR_NUMBER


def test_template_send_then_delivery_receipt(rig):
    r = rig.messenger.send_template(CUSTOMER, "appointment_reminder", {"name": "Sam", "when": "Tue 10 Nov, 3pm"})
    assert r["status"] == "queued"
    rig.mock.flush()
    msg = rig.outbound()[0]
    assert msg["status"] == "delivered" and msg["telnyx_id"] == r["telnyx_id"] and msg["cost_usd"] == 0.004
    assert [w["event_type"] for w in rig.mock.webhooks] == ["message.sent", "message.finalized"]
    assert all(w["status"] == 200 for w in rig.mock.webhooks)


def test_inbound_question_gets_an_ai_reply(rig):
    rig.text_in("Hi, is there parking?")
    out = rig.outbound()
    assert len(out) == 1 and "parking lot" in out[0]["text"] and out[0]["kind"] == "ai"
    assert out[0]["status"] == "delivered"
    assert rig.mock.sent[0]["to"][0]["phone_number"] == CUSTOMER


def test_stop_opts_out_confirms_once_and_blocks_everything_after(rig):
    rig.text_in("STOP")
    assert rig.store.is_opted_out(CUSTOMER)
    out = rig.outbound()
    assert len(out) == 1 and out[0]["kind"] == "template:stop_confirmation"
    r = rig.messenger.send_template(CUSTOMER, "follow_up", {"name": "Sam"})
    assert r == {**r, "status": "blocked", "reason": "opted_out"}
    rig.text_in("Is there parking?")  # they text again without opting in: no AI reply
    assert len(rig.mock.sent) == 1


def test_start_opts_back_in(rig):
    rig.text_in("stop")
    rig.text_in("START")
    assert not rig.store.is_opted_out(CUSTOMER)
    assert rig.outbound()[-1]["kind"] == "template:start_confirmation"
    assert rig.messenger.send(CUSTOMER, "Welcome back")["status"] == "queued"


def test_help_reply(rig):
    rig.text_in("HELP")
    assert rig.outbound()[0]["kind"] == "template:help"


def test_when_telnyx_already_auto_replied_we_dont_send_a_second_confirmation(rig):
    rig.mock.autorespond = True
    rig.text_in("STOP")
    assert rig.store.is_opted_out(CUSTOMER) and rig.mock.sent == []
    rig.text_in("HELP")
    assert rig.mock.sent == []


def test_handover_flags_the_contact_and_assistant_goes_quiet(rig):
    rig.text_in("I'd like to talk to a person about my bill")
    assert rig.store.contact(CUSTOMER)["handover"] == 1
    assert len(rig.mock.sent) == 1
    rig.text_in("Hello?")
    assert len(rig.mock.sent) == 1  # a person has it now
    rig.messenger.release(CUSTOMER)
    rig.text_in("Is there parking?")
    assert len(rig.mock.sent) == 2


def test_duplicate_webhooks_are_processed_once(rig):
    rig.mock.duplicate_webhooks = True
    rig.text_in("Is there parking?")
    assert len([m for m in rig.store.messages(CUSTOMER) if m["direction"] == "inbound"]) == 1
    assert len(rig.mock.sent) == 1
    attempts = [w["attempt"] for w in rig.mock.webhooks]
    assert attempts.count(2) == 3  # the inbound and both receipts were each delivered twice
    assert sum(1 for t in rig.store.trail() if t["event"] == "duplicate_webhook") == 3


def test_out_of_order_receipts_dont_undo_delivered(rig):
    rig.mock.reverse_webhooks = True
    rig.messenger.send(CUSTOMER, "Your order is ready")
    rig.mock.flush()
    assert rig.outbound()[0]["status"] == "delivered"
    assert any(t["event"] == "receipt_out_of_order" for t in rig.store.trail())


def test_carrier_failure_is_recorded_with_the_error(rig):
    rig.mock.carrier_fail = {CUSTOMER}
    rig.messenger.send(CUSTOMER, "Hello")
    rig.mock.flush()
    msg = rig.outbound()[0]
    assert msg["status"] == "delivery_failed" and "Destination unreachable" in msg["reason"]


def test_trial_account_only_reaches_the_verified_number(rig):
    rig.mock.trial_verified = {"+15550100177"}
    r = rig.messenger.send(CUSTOMER, "Hello")
    assert r["status"] == "failed" and "40300" in r["reason"]
    assert rig.messenger.send("+15550100177", "Hello")["status"] == "queued"


def test_unsigned_or_forged_webhooks_are_rejected(rig):
    env = rig.mock.envelope("message.received", {"from": {"phone_number": CUSTOMER}, "text": "STOP"})
    body = json.dumps(env).encode()
    assert rig.client.post("/telnyx/webhook", content=body).status_code == 401
    forged = signature.sign(body, Ed25519PrivateKey.generate())
    assert rig.client.post("/telnyx/webhook", content=body, headers=forged).status_code == 401
    # signed with the right key, but ten minutes ago
    assert rig.mock.send_webhook(rig.settings.webhook_url, env, sign_ts=int(time.time()) - 600) == 401
    assert not rig.store.is_opted_out(CUSTOMER)
    assert sum(1 for t in rig.store.trail() if t["event"] == "signature_rejected") == 3


def test_no_public_key_means_503_unless_explicitly_unsigned(settings):
    from fastapi.testclient import TestClient

    from smsassist.app import create_app
    from smsassist.service import Messenger
    from smsassist.store import Store

    settings.telnyx_public_key = ""
    m = Messenger(settings, Store(settings.db_path), None)
    body = json.dumps({"data": {"id": "e1", "event_type": "message.sent", "payload": {}}})
    assert TestClient(create_app(settings, m)).post("/telnyx/webhook", content=body).status_code == 503
    settings.allow_unsigned = True
    assert TestClient(create_app(settings, m)).post("/telnyx/webhook", content=body).status_code == 200


def test_not_an_event_is_400(rig):
    body = b'{"hello": 1}'
    headers = signature.sign(body, rig.mock.private_key)
    assert rig.client.post("/telnyx/webhook", content=body, headers=headers).status_code == 400


@pytest.mark.allow_processing_failures
def test_processing_errors_are_logged_not_silent(rig, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(rig.messenger, "_inbound", boom)
    rig.text_in("hello")
    assert any(t["event"] == "processing_failed" and "db down" in t["detail"]["error"] for t in rig.store.trail())


def test_spend_cap_stops_sending(rig):
    rig.settings.daily_spend_cap_usd = 0.01  # 0.004 a segment: two texts fit, a third doesn't
    statuses = [rig.messenger.send(CUSTOMER, f"msg {i}")["status"] for i in range(3)]
    assert statuses == ["queued", "queued", "blocked"]
    assert rig.outbound()[-1]["reason"] == "spend_cap"
    assert len(rig.mock.sent) == 2


def test_rate_limit_per_contact(rig):
    rig.settings.per_contact_per_hour = 2
    statuses = [rig.messenger.send(CUSTOMER, f"msg {i}")["status"] for i in range(3)]
    assert statuses == ["queued", "queued", "blocked"]


def test_too_long_and_empty_never_reach_telnyx(rig):
    assert rig.messenger.send(CUSTOMER, "x" * 1600)["reason"] == "too_long"
    assert rig.messenger.send(CUSTOMER, "   ")["reason"] == "empty"
    assert rig.mock.sent == []


def test_dry_run_records_but_does_not_send(rig):
    rig.settings.dry_run = True
    assert rig.messenger.send(CUSTOMER, "hello")["status"] == "dry_run"
    assert rig.mock.sent == []


def test_trail_and_logs_hold_no_full_phone_numbers(rig, caplog):
    caplog.set_level(logging.INFO)
    rig.text_in("Is there parking?")
    trail = json.dumps(rig.store.trail())
    assert CUSTOMER not in trail and "+1555***0199" in trail
    assert CUSTOMER not in caplog.text


def test_admin_endpoints_need_the_token(rig, admin):
    for path in ("/messages", "/contacts", "/trail"):
        assert rig.client.get(path).status_code == 401
        assert rig.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert rig.client.get(path, headers=admin).status_code == 200


def test_admin_send_endpoint(rig, admin):
    r = rig.client.post("/send", json={"to": CUSTOMER, "template": "follow_up", "values": {"name": "Sam"}},
                        headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert rig.client.post("/send", json={"to": "12345", "text": "hi"}, headers=admin).status_code == 422
    assert rig.client.post("/send", json={"to": CUSTOMER, "template": "follow_up"}, headers=admin).status_code == 422
    assert rig.client.post("/send", json={"to": CUSTOMER}, headers=admin).status_code == 422


def test_health(rig):
    assert rig.client.get("/health").json() == {"ok": True, "signed_webhooks": True, "ai": True, "dry_run": False,
                                             "processing": 0}


def test_from_number_is_ours(rig):
    rig.messenger.send(CUSTOMER, "hi")
    assert rig.mock.sent[0]["from"]["phone_number"] == OUR_NUMBER


def test_receipts_racing_each_other_cannot_undo_delivered(rig):
    """Two receipts processed at the same moment: 'sent' read the row before 'delivered'
    was written. Found in a real local run; the guarded UPDATE stops it."""
    r = rig.messenger.send(CUSTOMER, "Hello")
    tid = r["telnyx_id"]
    rig.mock.queued.clear()
    assert rig.store.update_status(tid, "delivered", final_statuses={"delivered"})
    # the stale writer: its read happened before 'delivered' landed
    assert not rig.store.update_status(tid, "sent", final_statuses={"delivered"})
    assert rig.store.message_by_telnyx_id(tid)["status"] == "delivered"


def test_concurrent_receipts_from_threads(rig):
    import threading

    for _ in range(5):
        rig.messenger.send(CUSTOMER, "Hello")
    batch, rig.mock.queued = rig.mock.queued, []
    threads = []
    for url, events in batch:
        for etype, payload in reversed(events):
            env = rig.mock.envelope(etype, payload)
            threads.append(threading.Thread(target=rig.messenger.handle_event, args=(env,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {m["status"] for m in rig.outbound()} == {"delivered"}
