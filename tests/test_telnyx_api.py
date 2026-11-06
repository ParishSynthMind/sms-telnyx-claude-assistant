import json

import httpx
import pytest
from fastapi.testclient import TestClient

from smsassist.mock_telnyx import MockTelnyx
from smsassist.telnyx_api import TelnyxClient, TelnyxError

OURS, THEIRS = "+15550100100", "+15550100199"


@pytest.fixture
def mock():
    return MockTelnyx(numbers=[OURS])


def client(mock, key="KEYmock_test", sleeps=None):
    return TelnyxClient(key, "http://testserver/v2", http=TestClient(mock.app),
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def test_send_returns_id_status_parts_and_cost(mock):
    m = client(mock).send_message(THEIRS, "Hello", from_number=OURS)
    assert m.status == "queued" and m.parts == 1 and m.cost_usd == pytest.approx(0.004)
    assert m.raw["record_type"] == "message" and m.raw["direction"] == "outbound"


def test_request_has_bearer_auth_json_and_idempotency_key(mock):
    client(mock).send_message(THEIRS, "Hi", from_number=OURS, webhook_url="https://x.example/hook",
                              idempotency_key="k-1")
    req = mock.requests[0]
    assert req["headers"]["authorization"] == "Bearer KEYmock_test"
    assert req["headers"]["idempotency-key"] == "k-1"
    assert json.loads(req["body"]) == {"to": THEIRS, "text": "Hi", "from": OURS, "webhook_url": "https://x.example/hook"}


def test_wrong_key_is_401_with_telnyx_error(mock):
    with pytest.raises(TelnyxError) as e:
        client(mock, key="KEYwrong").send_message(THEIRS, "Hi", from_number=OURS)
    assert e.value.status == 401 and e.value.title == "Authentication failed"


def test_number_we_dont_own_is_40300(mock):
    with pytest.raises(TelnyxError) as e:
        client(mock).send_message(THEIRS, "Hi", from_number="+15550100999")
    assert e.value.code == "40300" and "not assigned to a messaging profile" in e.value.detail


def test_bad_destination_is_not_retried(mock):
    sleeps = []
    with pytest.raises(TelnyxError) as e:
        client(mock, sleeps=sleeps).send_message("5550100199", "Hi", from_number=OURS)
    assert e.value.status == 422 and sleeps == [] and len(mock.requests) == 1


def test_429_and_500_are_retried_with_the_same_idempotency_key(mock):
    mock.fail_next = [429, 500]
    sleeps = []
    m = client(mock, sleeps=sleeps).send_message(THEIRS, "Hi", from_number=OURS, idempotency_key="k-9")
    assert m.id and len(mock.requests) == 3 and len(mock.sent) == 1
    assert {r["headers"]["idempotency-key"] for r in mock.requests} == {"k-9"}
    assert sleeps == [1.0, 2.0]  # Retry-After from the 429, then backoff


def test_gives_up_after_max_retries(mock):
    mock.fail_next = [500] * 10
    with pytest.raises(TelnyxError) as e:
        client(mock).send_message(THEIRS, "Hi", from_number=OURS)
    assert e.value.status == 500 and len(mock.requests) == 4


def test_idempotency_key_replays_instead_of_sending_twice(mock):
    c = client(mock)
    a = c.send_message(THEIRS, "Hi", from_number=OURS, idempotency_key="same")
    b = c.send_message(THEIRS, "Hi", from_number=OURS, idempotency_key="same")
    assert a.id == b.id and len(mock.sent) == 1


def test_transport_errors_are_retried_then_raised():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    c = TelnyxClient("KEYx", "http://t/v2", http=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, max_retries=2)
    with pytest.raises(httpx.ConnectError):
        c.send_message(THEIRS, "Hi", from_number=OURS)
    assert len(calls) == 3


def test_needs_a_sender_and_a_key(mock):
    with pytest.raises(ValueError):
        client(mock).send_message(THEIRS, "Hi")
    with pytest.raises(ValueError):
        TelnyxClient("")


def test_get_message(mock):
    c = client(mock)
    m = c.send_message(THEIRS, "Hi", from_number=OURS)
    assert c.get_message(m.id)["id"] == m.id
