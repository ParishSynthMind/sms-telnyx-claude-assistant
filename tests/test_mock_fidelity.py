"""The mock must look like Telnyx, or the other tests prove nothing."""
import json

from fastapi.testclient import TestClient

from smsassist import signature
from smsassist.mock_telnyx import MockTelnyx


def test_webhook_envelope_matches_telnyx_docs():
    m = MockTelnyx()
    got = []
    m.deliver = lambda url, body, headers: got.append((body, headers)) or 200
    m.inbound("https://x/hook", "+15550100199", "+15550100100", "hi")
    body, headers = got[0]
    env = json.loads(body)
    assert set(env) == {"data", "meta"}
    assert set(env["data"]) == {"event_type", "id", "occurred_at", "payload", "record_type"}
    assert env["data"]["record_type"] == "event" and env["meta"] == {"attempt": 1, "delivered_to": "https://x/hook"}
    p = env["data"]["payload"]
    assert p["direction"] == "inbound" and p["from"]["phone_number"] == "+15550100199"
    assert p["to"][0]["status"] == "webhook_delivered"
    assert set(headers) >= {"telnyx-signature-ed25519", "telnyx-timestamp"}
    signature.verify(body, headers, signature.load_public_key(m.public_key_b64))


def test_send_answer_and_receipts_match_telnyx_docs():
    m = MockTelnyx(numbers=["+15550100100"], profile_webhook_url="https://x/hook")
    got = []
    m.deliver = lambda url, body, headers: got.append(json.loads(body)) or 200
    r = TestClient(m.app).post("/v2/messages", headers={"Authorization": "Bearer KEYmock_test"},
                               json={"from": "+15550100100", "to": "+15550100199", "text": "Hi"})
    data = r.json()["data"]
    assert r.status_code == 200 and data["record_type"] == "message" and data["to"][0]["status"] == "queued"
    assert data["cost"]["currency"] == "USD" and data["parts"] == 1 and data["encoding"] == "GSM-7"
    assert got == []  # receipts come after the answer
    m.flush()
    assert [e["data"]["event_type"] for e in got] == ["message.sent", "message.finalized"]
    assert [e["data"]["payload"]["to"][0]["status"] for e in got] == ["sent", "delivered"]


def test_error_body_shape():
    r = TestClient(MockTelnyx().app).post("/v2/messages", json={"to": "x"})
    assert r.status_code == 401 and set(r.json()["errors"][0]) >= {"code", "title", "detail"}
