"""AI disclosure (EU AI Act Art. 50): the first AI text in a conversation says it is an AI,
later texts don't repeat it, and a hand-over says an AI is passing the customer to a person."""
import sqlite3

from smsassist.ai import SYSTEM_PROMPT
from smsassist.config import Settings
from smsassist.segments import segment_count
from smsassist.store import Store
from tests.conftest import CUSTOMER, Rig

DISCLOSURE = "I'm an AI assistant for Brightside Dental."
HANDOVER = "I'm an AI assistant for Brightside Dental, passing you to a person on our team."


def ai_texts(rig, phone=CUSTOMER):
    return [m["text"] for m in rig.outbound(phone) if m["kind"] == "ai"]


def test_first_ai_reply_discloses_and_the_second_does_not(rig):
    rig.text_in("Hi, is there parking?")
    rig.text_in("What are your opening hours?")
    first, second = ai_texts(rig)
    assert first.startswith(DISCLOSURE) and "parking lot" in first
    assert "AI assistant" not in second and second.startswith("We're open")
    assert rig.mock.sent[0]["text"].startswith(DISCLOSURE)  # what actually went to Telnyx


def test_each_new_conversation_gets_the_disclosure(rig):
    other = "+15550100177"
    rig.text_in("Is there parking?")
    rig.text_in("Is there parking?", frm=other)
    assert ai_texts(rig, other)[0].startswith(DISCLOSURE)


def test_a_reset_store_counts_as_a_new_conversation(settings, tmp_path):
    rig = Rig(settings)
    rig.text_in("Is there parking?")
    settings.db_path = str(tmp_path / "fresh.db")
    fresh = Rig(settings)
    fresh.text_in("Is there parking?")
    assert ai_texts(fresh)[0].startswith(DISCLOSURE)


def test_handover_says_an_ai_is_passing_them_to_a_person(rig):
    rig.text_in("Is there parking?")
    rig.text_in("I'd like to talk to a person")
    assert ai_texts(rig)[-1].startswith(HANDOVER)
    assert rig.store.contact(CUSTOMER)["handover"] == 1


def test_after_release_the_assistant_discloses_again(rig):
    rig.text_in("Can I speak to a human?")
    rig.messenger.release(CUSTOMER)
    rig.text_in("Is there parking?")
    rig.text_in("Opening hours?")
    texts = ai_texts(rig)
    assert texts[0].startswith(HANDOVER) and texts[1].startswith(DISCLOSURE)
    assert "AI assistant" not in texts[2]


def test_start_begins_a_new_conversation(rig):
    rig.text_in("Is there parking?")
    rig.text_in("STOP")
    rig.text_in("START")
    rig.text_in("Is there parking?")
    assert ai_texts(rig)[-1].startswith(DISCLOSURE)


def test_a_blocked_first_reply_does_not_count_as_disclosed(rig):
    rig.settings.global_per_minute = 0
    rig.text_in("Is there parking?")
    assert [m["status"] for m in rig.outbound()] == ["blocked"]
    rig.settings.global_per_minute = 30
    rig.text_in("Is there parking?")
    delivered = [m for m in rig.outbound() if m["kind"] == "ai" and m["status"] != "blocked"]
    assert delivered[0]["text"].startswith(DISCLOSURE)


def test_disclosed_reply_stays_within_one_extra_segment(rig):
    rig.text_in("Is there parking?")
    assert segment_count(ai_texts(rig)[0]) <= rig.settings.max_reply_segments + 1


def test_help_mentions_ai_and_stays_one_segment(rig):
    rig.text_in("HELP")
    help_text = rig.outbound()[0]["text"]
    assert "AI assistant" in help_text and "STOP" in help_text and segment_count(help_text) == 1


def test_blank_disclosure_without_written_request_still_discloses(monkeypatch):
    monkeypatch.setenv("AI_DISCLOSURE", "  ")
    monkeypatch.delenv("AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST", raising=False)
    s = Settings.from_env()
    assert s.disclosure == "I'm an AI assistant for Brightside Dental."
    assert s.handover_notice.startswith("I'm an AI assistant")


def test_blank_disclosure_in_a_live_conversation_still_discloses(settings):
    settings.ai_disclosure = ""
    rig = Rig(settings)
    rig.text_in("Is there parking?")
    assert ai_texts(rig)[0].startswith(DISCLOSURE)


def test_removed_only_with_a_recorded_written_request(settings):
    settings.ai_disclosure = ""
    settings.ai_disclosure_removed_by_written_request = "2026-11-17 email from J. Client"
    rig = Rig(settings)
    rig.text_in("Is there parking?")
    rig.text_in("Can I talk to a person?")
    assert all("AI assistant" not in t for t in ai_texts(rig))


def test_custom_disclosure_text(settings):
    settings.ai_disclosure = "Automated AI reply from {business}:"
    rig = Rig(settings)
    rig.text_in("Is there parking?")
    assert ai_texts(rig)[0].startswith("Automated AI reply from Brightside Dental: Yes")


def test_prompt_tells_claude_it_is_an_ai():
    assert "never claim to be human" in SYSTEM_PROMPT


def test_old_database_gets_the_new_column(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE contacts (phone TEXT PRIMARY KEY, opted_out INTEGER NOT NULL DEFAULT 0, "
               "opted_out_at TEXT, handover INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
    db.execute("INSERT INTO contacts(phone, updated_at) VALUES('+15550100199', 'x')")
    db.commit()
    db.close()
    assert Store(path).contact("+15550100199")["ai_disclosed"] == 0
