import os

import pytest
from fastapi.testclient import TestClient

from smsassist.ai import FakeReplier
from smsassist.app import create_app
from smsassist.config import Settings
from smsassist.mock_telnyx import MockTelnyx
from smsassist.service import Messenger
from smsassist.store import Store
from smsassist.telnyx_api import TelnyxClient

ROOT = os.path.dirname(os.path.dirname(__file__))
OUR_NUMBER = "+15550100100"
CUSTOMER = "+15550100199"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        telnyx_api_key="KEYmock_test", from_number=OUR_NUMBER, db_path=str(tmp_path / "sms.db"),
        webhook_url="http://testserver/telnyx/webhook", quiet_hours=None, admin_token="admin-secret",
        templates_path=os.path.join(ROOT, "data", "templates.json"),
        knowledge_path=os.path.join(ROOT, "data", "business.md"),
        api_base="http://testserver/v2",
    )


class Rig:
    """Our app and the mock Telnyx, wired to each other the way the real thing is."""

    def __init__(self, settings):
        self.mock = MockTelnyx(numbers=[OUR_NUMBER])
        settings.telnyx_public_key = self.mock.public_key_b64
        self.settings = settings
        self.telnyx_http = TestClient(self.mock.app)
        self.telnyx = TelnyxClient(settings.telnyx_api_key, settings.api_base, http=self.telnyx_http,
                                   sleep=lambda s: None)
        self.store = Store(settings.db_path)
        self.replier = FakeReplier(settings.max_reply_segments)
        self.messenger = Messenger(settings, self.store, self.telnyx, self.replier)
        self.app = create_app(settings, self.messenger)
        self.client = TestClient(self.app)
        self.mock.deliver = lambda url, body, headers: self.client.post(
            "/telnyx/webhook", content=body, headers=headers).status_code

    def text_in(self, text, frm=CUSTOMER):
        """A customer texts us; then any receipts for our answers come back."""
        self.mock.inbound(self.settings.webhook_url, frm, OUR_NUMBER, text)
        self.mock.flush()

    def outbound(self, phone=CUSTOMER):
        return [m for m in reversed(self.store.messages(phone, 200)) if m["direction"] == "outbound"]


@pytest.fixture
def rig(settings, request):
    r = Rig(settings)
    yield r
    # Every flow test also checks that no webhook failed to process behind the scenes
    # (processing happens after the 200, so a failure there is otherwise invisible).
    if "allow_processing_failures" not in request.keywords:
        failed = [t for t in r.store.trail(1000) if t["event"] == "processing_failed"]
        assert failed == [], failed


@pytest.fixture
def admin():
    return {"Authorization": "Bearer admin-secret"}
