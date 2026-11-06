from datetime import datetime, time, timezone

import pytest

from smsassist import policy
from smsassist.config import Settings, parse_quiet_hours
from smsassist.store import Store

PHONE = "+15550100199"


@pytest.mark.parametrize("text,kw", [
    ("STOP", "STOP"), ("stop", "STOP"), (" Stop. ", "STOP"), ("stop all", "STOP"), ("Unsubscribe", "STOP"),
    ("cancel", "STOP"), ("END", "STOP"), ("quit", "STOP"), ("STOPALL", "STOP"), ("optout", "STOP"),
    ("START", "START"), ("unstop", "START"), ("yes", "START"),
    ("HELP", "HELP"), ("info", "HELP"),
    ("please don't stop texting me", None), ("Can I cancel my appointment?", None), ("", None),
])
def test_keywords(text, kw):
    assert policy.keyword(text) == kw


def test_quiet_hours_wrapping_midnight():
    quiet = (time(21, 0), time(8, 0))
    ny = "America/New_York"
    # 6 Nov 2026: New York is UTC-5
    assert policy.in_quiet_hours(quiet, ny, datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc))      # 22:00
    assert policy.in_quiet_hours(quiet, ny, datetime(2026, 11, 6, 12, 59, tzinfo=timezone.utc))    # 07:59
    assert not policy.in_quiet_hours(quiet, ny, datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc))  # 08:00
    assert not policy.in_quiet_hours(quiet, ny, datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc))  # 15:00


def test_quiet_hours_same_day_window_and_none():
    assert policy.in_quiet_hours((time(12, 0), time(13, 0)), "UTC", datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc))
    assert not policy.in_quiet_hours(None, "UTC")


def test_parse_quiet_hours():
    assert parse_quiet_hours("21:00-08:00") == (time(21), time(8))
    assert parse_quiet_hours("") is None
    with pytest.raises(ValueError):
        parse_quiet_hours("9pm to 8am")


@pytest.fixture
def store():
    return Store(":memory:")


def s(**kw):
    base = dict(quiet_hours=None, per_contact_per_hour=2, global_per_minute=3, daily_spend_cap_usd=0.02,
                cost_per_segment_usd=0.004)
    base.update(kw)
    return Settings(**base)


def test_opted_out_blocked_but_stop_confirmation_allowed(store):
    store.set_opt_out(PHONE, True)
    assert policy.check_send(store, s(), PHONE, 1).reason == "opted_out"
    assert policy.check_send(store, s(), PHONE, 1, transactional_reply=True).allowed


def test_quiet_hours_block_but_not_a_reply():
    st = Store(":memory:")
    night = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)
    cfg = s(quiet_hours=(time(21), time(8)), timezone="America/New_York")
    assert policy.check_send(st, cfg, PHONE, 1, now=night).reason == "quiet_hours"
    assert policy.check_send(st, cfg, PHONE, 1, conversational=True, now=night).allowed


def test_per_contact_rate_limit(store):
    for _ in range(2):
        store.add_message("outbound", PHONE, "hi", "manual", 1, "sent")
    assert policy.check_send(store, s(), PHONE, 1).reason == "rate_limited_contact"
    assert policy.check_send(store, s(), "+15550100111", 1).allowed


def test_global_rate_limit_applies_to_everyone(store):
    for i in range(3):
        store.add_message("outbound", f"+1555010020{i}", "hi", "manual", 1, "sent")
    assert policy.check_send(store, s(), PHONE, 1).reason == "rate_limited_global"
    assert policy.check_send(store, s(), PHONE, 1, transactional_reply=True).reason == "rate_limited_global"


def test_blocked_messages_dont_count_towards_limits(store):
    for _ in range(5):
        store.add_message("outbound", PHONE, "hi", "manual", 1, "blocked", reason="quiet_hours")
    assert policy.check_send(store, s(), PHONE, 1).allowed


def test_spend_cap_uses_real_cost_when_known(store):
    store.add_message("outbound", "+15550100201", "hi", "manual", 1, "sent", cost_usd=0.015)
    assert policy.check_send(store, s(), PHONE, 1).allowed          # 0.015 + 0.004 = 0.019
    assert policy.check_send(store, s(), PHONE, 2).reason == "spend_cap"  # 0.015 + 0.008 > 0.02
