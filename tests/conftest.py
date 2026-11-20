import os

import pytest
from fastapi.testclient import TestClient

from smsassist.ai import FakeReplier
from smsassist.app import create_app
from smsassist.config import Settings
from smsassist.mock_telnyx import MockTelnyx
from smsassist.mock_twilio import MockTwilio
from smsassist.service import Messenger
from smsassist.store import Store
from smsassist.telnyx_api import TelnyxClient
from smsassist.twilio_api import TwilioClient

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


# Twilio: the same app and rules, Twilio as the provider, the mock Twilio on the other side.
TW_SID = "AC" + "1" * 32
TW_TOKEN = "mock-twilio-token"
TW_BASE = "https://sms.example.com"


class TwilioRig:
    def __init__(self, settings, trial_template=""):
        settings.provider = "twilio"
        settings.twilio_account_sid, settings.twilio_auth_token = TW_SID, TW_TOKEN
        settings.twilio_api_base = "http://twilio.test/2010-04-01"
        settings.public_base_url = TW_BASE
        settings.webhook_url = TW_BASE + "/twilio/status"
        settings.twilio_trial_template = trial_template
        self.settings = settings
        self.mock = MockTwilio(account_sid=TW_SID, auth_token=TW_TOKEN, numbers=[OUR_NUMBER],
                               sms_url=TW_BASE + "/twilio/sms")
        self.twilio = TwilioClient(TW_SID, TW_TOKEN, settings.twilio_api_base,
                                   http=TestClient(self.mock.app, base_url="http://twilio.test"),
                                   sleep=lambda s: None, trial_template=trial_template)
        self.store = Store(settings.db_path)
        self.replier = FakeReplier(settings.max_reply_segments)
        self.messenger = Messenger(settings, self.store, self.twilio, self.replier)
        self.app = create_app(settings, self.messenger)
        self.client = TestClient(self.app, base_url=TW_BASE)
        self.mock.deliver = lambda url, body, headers: self.client.post(
            url.replace(TW_BASE, ""), content=body, headers=headers).status_code

    def text_in(self, text, frm=CUSTOMER):
        self.mock.inbound(frm, OUR_NUMBER, text)
        self.mock.flush()

    def outbound(self, phone=CUSTOMER):
        return [m for m in reversed(self.store.messages(phone, 200)) if m["direction"] == "outbound"]


@pytest.fixture
def twrig(settings, request):
    r = TwilioRig(settings)
    yield r
    if "allow_processing_failures" not in request.keywords:
        failed = [t for t in r.store.trail(1000) if t["event"] == "processing_failed"]
        assert failed == [], failed
