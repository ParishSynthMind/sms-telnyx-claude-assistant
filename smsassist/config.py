"""Settings from environment variables. Nothing secret has a default."""
import os
import re
from dataclasses import dataclass, field
from datetime import time
from typing import Optional, Tuple

E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# AI disclosure (EU AI Act Art. 50): the first text the assistant sends in a conversation
# says it is an AI, and so does a hand-over to a person. Blanking AI_DISCLOSURE does not
# switch it off; only a recorded written request from the client does
# (AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST = date and who asked).
DEFAULT_AI_DISCLOSURE = "I'm an AI assistant for {business}."
AI_HANDOVER_NOTICE = "I'm an AI assistant for {business}, passing you to a person on our team."


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
    provider: str = "telnyx"                 # telnyx | twilio
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
    ai_disclosure: str = ""
    ai_disclosure_removed_by_written_request: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_api_base: str = "https://api.twilio.com/2010-04-01"
    twilio_trial_template: str = ""          # trial accounts only: see twilio_api.py
    public_base_url: str = ""                # https://<public host>; Twilio signs the URL it called
    provider_answers_keywords: str = "auto"  # auto | yes | no: does the provider answer STOP itself?
    extra: dict = field(default_factory=dict)

    @property
    def provider_replies_to_keywords(self) -> bool:
        """Twilio answers STOP/START/HELP itself on US and Canadian numbers (and anywhere
        Advanced Opt-Out is on, which the webhook then says in OptOutType). Elsewhere it
        does not, so the app sends the confirmation. 'yes'/'no' override the guess."""
        v = (self.provider_answers_keywords or "auto").strip().lower()
        if v in ("yes", "true", "1", "on"):
            return True
        if v in ("no", "false", "0", "off"):
            return False
        return self.from_number.startswith("+1")

    def public_url(self, path: str) -> str:
        return (self.public_base_url or "").rstrip("/") + path

    @property
    def disclosure_removed(self) -> bool:
        return bool((self.ai_disclosure_removed_by_written_request or "").strip())

    @property
    def disclosure(self) -> str:
        """Prefix for the first AI text in a conversation. Empty only with a recorded written request."""
        text = (self.ai_disclosure or "").strip()
        if not text and not self.disclosure_removed:
            text = DEFAULT_AI_DISCLOSURE
        return text.replace("{business}", self.business_name)

    @property
    def handover_notice(self) -> str:
        return "" if self.disclosure_removed else AI_HANDOVER_NOTICE.replace("{business}", self.business_name)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        provider = e("SMS_PROVIDER", "telnyx").strip().lower()
        if provider not in ("telnyx", "twilio"):
            raise ValueError(f"SMS_PROVIDER must be telnyx or twilio, got {provider!r}")
        twilio = provider == "twilio"
        public_base = e("PUBLIC_BASE_URL", "").rstrip("/")
        webhook_url = e("WEBHOOK_URL", "")
        if twilio and not webhook_url and public_base:
            webhook_url = public_base + "/twilio/status"  # the StatusCallback for every send
        return cls(
            provider=provider,
            telnyx_api_key=e("TELNYX_API_KEY", ""),
            telnyx_public_key=e("TELNYX_PUBLIC_KEY", ""),
            from_number=e("TWILIO_FROM_NUMBER", "") if twilio else e("TELNYX_FROM_NUMBER", ""),
            messaging_profile_id=(e("TWILIO_MESSAGING_SERVICE_SID", "") if twilio
                                  else e("TELNYX_MESSAGING_PROFILE_ID", "")),
            api_base=e("TELNYX_API_BASE", "https://api.telnyx.com/v2").rstrip("/"),
            twilio_account_sid=e("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=e("TWILIO_AUTH_TOKEN", ""),
            twilio_api_base=e("TWILIO_API_BASE", "https://api.twilio.com/2010-04-01").rstrip("/"),
            twilio_trial_template=e("TWILIO_TRIAL_TEMPLATE", ""),
            public_base_url=public_base,
            provider_answers_keywords=e("PROVIDER_ANSWERS_KEYWORDS", "auto"),
            webhook_url=webhook_url,
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
            ai_disclosure=e("AI_DISCLOSURE", ""),
            ai_disclosure_removed_by_written_request=e("AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST", ""),
        )


def normalise_number(raw: str) -> str:
    """Strip spaces, dashes, brackets and dots; insist on E.164 (+ and country code)."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if not E164.match(s):
        raise ValueError(f"not an E.164 phone number: {raw!r}")
    return s
