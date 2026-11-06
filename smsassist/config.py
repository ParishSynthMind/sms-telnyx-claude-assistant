"""Settings from environment variables. Nothing secret has a default."""
import os
import re
from dataclasses import dataclass, field
from datetime import time
from typing import Optional, Tuple

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def parse_quiet_hours(spec: str) -> Optional[Tuple[time, time]]:
    """'21:00-08:00' -> (21:00, 08:00). Empty means no quiet hours."""
    spec = (spec or "").strip()
    if not spec:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", spec)
    if not m:
        raise ValueError(f"QUIET_HOURS must look like 21:00-08:00, got {spec!r}")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    return time(h1, m1), time(h2, m2)


@dataclass
class Settings:
    telnyx_api_key: str = ""
    telnyx_public_key: str = ""
    from_number: str = ""
    messaging_profile_id: str = ""
    api_base: str = "https://api.telnyx.com/v2"
    webhook_url: str = ""
    db_path: str = "var/sms.db"
    business_name: str = "Brightside Dental"
    timezone: str = "America/New_York"
    quiet_hours: Optional[Tuple[time, time]] = None
    per_contact_per_hour: int = 5
    global_per_minute: int = 30
    daily_spend_cap_usd: float = 2.00
    cost_per_segment_usd: float = 0.004
    max_reply_segments: int = 2
    ai_enabled: bool = True
    ai_model: str = "claude-sonnet-5-5"
    admin_token: str = ""
    allow_unsigned: bool = False
    dry_run: bool = False
    templates_path: str = "data/templates.json"
    knowledge_path: str = "data/business.md"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            telnyx_api_key=e("TELNYX_API_KEY", ""),
            telnyx_public_key=e("TELNYX_PUBLIC_KEY", ""),
            from_number=e("TELNYX_FROM_NUMBER", ""),
            messaging_profile_id=e("TELNYX_MESSAGING_PROFILE_ID", ""),
            api_base=e("TELNYX_API_BASE", "https://api.telnyx.com/v2").rstrip("/"),
            webhook_url=e("WEBHOOK_URL", ""),
            db_path=e("DB_PATH", "var/sms.db"),
            business_name=e("BUSINESS_NAME", "Brightside Dental"),
            timezone=e("TIMEZONE", "America/New_York"),
            quiet_hours=parse_quiet_hours(e("QUIET_HOURS", "21:00-08:00")),
            per_contact_per_hour=int(e("PER_CONTACT_PER_HOUR", "5")),
            global_per_minute=int(e("GLOBAL_PER_MINUTE", "30")),
            daily_spend_cap_usd=float(e("DAILY_SPEND_CAP_USD", "2.00")),
            cost_per_segment_usd=float(e("COST_PER_SEGMENT_USD", "0.004")),
            max_reply_segments=int(e("MAX_REPLY_SEGMENTS", "2")),
            ai_enabled=_bool("AI_ENABLED", True),
            ai_model=e("AI_MODEL", "claude-sonnet-5-5"),
            admin_token=e("ADMIN_TOKEN", ""),
            allow_unsigned=_bool("ALLOW_UNSIGNED", False),
            dry_run=_bool("DRY_RUN", False),
            templates_path=e("TEMPLATES_PATH", "data/templates.json"),
            knowledge_path=e("KNOWLEDGE_PATH", "data/business.md"),
        )


def normalise_number(raw: str) -> str:
    """Strip spaces, dashes, brackets and dots; insist on E.164 (+ and country code)."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if not E164.match(s):
        raise ValueError(f"not an E.164 phone number: {raw!r}")
    return s
