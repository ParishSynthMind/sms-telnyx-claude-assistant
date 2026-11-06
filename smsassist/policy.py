"""The rules every outbound text passes before it reaches Telnyx, and the keywords
every inbound text is checked for first.

Opt-out keywords are Telnyx's list (STOP, STOPALL, STOP ALL, UNSUBSCRIBE, CANCEL, END,
QUIT) plus OPTOUT and REVOKE from the CTIA guidance. Telnyx also
answers these keywords itself on its own numbers; this app keeps its own record so it
never even asks Telnyx to text someone who said STOP, whatever number it sends from."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"}
START_WORDS = {"START", "UNSTOP", "YES", "SUBSCRIBE"}
HELP_WORDS = {"HELP", "INFO"}


def keyword(text: str) -> Optional[str]:
    """'stop', ' Stop. ' -> 'STOP'. Only a message that IS the keyword counts, so
    'please don't stop texting me' is a normal message."""
    word = " ".join((text or "").strip().strip(".!").split()).upper()
    if word == "STOP ALL":
        return "STOP"
    if word in STOP_WORDS:
        return "STOP"
    if word in START_WORDS:
        return "START"
    if word in HELP_WORDS:
        return "HELP"
    return None


@dataclass
class Decision:
    allowed: bool
    reason: str = "ok"


def in_quiet_hours(quiet: Optional[Tuple[time, time]], tz: str, now: Optional[datetime] = None) -> bool:
    if not quiet:
        return False
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(tz)).time()
    start, end = quiet
    if start <= end:
        return start <= local < end
    return local >= start or local < end  # wraps midnight, e.g. 21:00-08:00


def check_send(store, settings, phone: str, segments: int, *, transactional_reply: bool = False,
               conversational: bool = False, now: Optional[datetime] = None) -> Decision:
    """Decide whether one outbound message may go.

    transactional_reply is for the one-off answers to STOP/START/HELP, which carriers
    expect straight away even at night and even (for STOP) to a number that just opted out.
    conversational is an answer to a text the person has just sent, so quiet hours
    (which are for messages we start) don't hold it back; every other rule still applies."""
    # `now` is the local clock for quiet hours (tests and demos can pin it); the rate and
    # spend windows always use real time, because the stored timestamps are real.
    real = datetime.now(timezone.utc)
    now = now or real
    if not transactional_reply:
        if store.is_opted_out(phone):
            return Decision(False, "opted_out")
        if not conversational and in_quiet_hours(settings.quiet_hours, settings.timezone, now):
            return Decision(False, "quiet_hours")
        hour_ago = (real - timedelta(hours=1)).isoformat(timespec="seconds")
        if store.outbound_since(hour_ago, phone) >= settings.per_contact_per_hour:
            return Decision(False, "rate_limited_contact")
    minute_ago = (real - timedelta(minutes=1)).isoformat(timespec="seconds")
    if store.outbound_since(minute_ago) >= settings.global_per_minute:
        return Decision(False, "rate_limited_global")
    day_start = real.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    spent = store.spend_since(day_start, settings.cost_per_segment_usd)
    if spent + segments * settings.cost_per_segment_usd > settings.daily_spend_cap_usd + 1e-9:
        return Decision(False, "spend_cap")
    return Decision(True)
