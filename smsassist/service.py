"""The messenger: every outbound text goes through send(), every Telnyx webhook through
handle_event(). Both write to the store, so there is one record of what happened."""
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from . import policy, templates
from .config import Settings, normalise_number
from .segments import segment_count
from .store import Store
from .telnyx_api import TelnyxError

MAX_SEGMENTS = 10  # Telnyx rejects messages longer than this
FINAL = {"delivered", "delivery_failed", "sending_failed", "delivery_unconfirmed", "expired", "read"}


def mask(phone: str) -> str:
    """+15550100199 -> +1555***0199, for the trail and logs."""
    return phone[:-7] + "***" + phone[-4:] if len(phone) > 8 else "***"


class Messenger:
    def __init__(self, settings: Settings, store: Store, telnyx, replier=None, clock=None):
        self.s = settings
        self.store = store
        self.telnyx = telnyx
        self.replier = replier
        self.clock = clock
        self.templates = templates.load(settings.templates_path) if Path(settings.templates_path).exists() else {}

    def _now(self) -> Optional[datetime]:
        return self.clock() if self.clock else None

    # outbound
    def send(self, to: str, text: str, kind: str = "manual", transactional: bool = False,
             conversational: bool = False) -> dict:
        phone = normalise_number(to)
        text = text.strip()
        segments = segment_count(text)
        if not text:
            decision = policy.Decision(False, "empty")
        elif self.telnyx is None and not self.s.dry_run:
            decision = policy.Decision(False, "no_telnyx_key")
        elif segments > MAX_SEGMENTS:
            decision = policy.Decision(False, "too_long")
        else:
            decision = policy.check_send(self.store, self.s, phone, segments, transactional_reply=transactional,
                                         conversational=conversational, now=self._now())
        if not decision.allowed:
            mid = self.store.add_message("outbound", phone, text, kind, segments, "blocked", reason=decision.reason)
            self.store.log("send_blocked", to=mask(phone), kind=kind, reason=decision.reason)
            return {"id": mid, "status": "blocked", "reason": decision.reason, "segments": segments}
        if self.s.dry_run:
            mid = self.store.add_message("outbound", phone, text, kind, segments, "dry_run")
            self.store.log("send_dry_run", to=mask(phone), kind=kind, segments=segments)
            return {"id": mid, "status": "dry_run", "segments": segments}
        key = str(uuid.uuid4())
        try:
            sent = self.telnyx.send_message(phone, text, from_number=self.s.from_number,
                                            messaging_profile_id=self.s.messaging_profile_id,
                                            webhook_url=self.s.webhook_url, idempotency_key=key)
        except TelnyxError as exc:
            reason = f"{exc.status} {exc.code} {exc.title}".strip()
            mid = self.store.add_message("outbound", phone, text, kind, segments, "failed", reason=reason)
            self.store.log("send_failed", to=mask(phone), kind=kind, error=reason, detail=exc.detail[:200])
            return {"id": mid, "status": "failed", "reason": reason, "segments": segments}
        mid = self.store.add_message("outbound", phone, text, kind, max(segments, sent.parts), sent.status,
                                     telnyx_id=sent.id, cost_usd=sent.cost_usd)
        self.store.log("sent", to=mask(phone), kind=kind, telnyx_id=sent.id, segments=sent.parts, status=sent.status)
        return {"id": mid, "status": sent.status, "telnyx_id": sent.id, "segments": max(segments, sent.parts)}

    def render(self, name: str, values: Optional[Dict[str, str]] = None) -> str:
        values = {"business": self.s.business_name, "phone": "(555) 010-0199", "email": "hello@brightside.example",
                  **(values or {})}
        return templates.render(self.templates, name, values)

    def send_template(self, to: str, name: str, values: Optional[Dict[str, str]] = None,
                      transactional: bool = False) -> dict:
        return self.send(to, self.render(name, values), kind=f"template:{name}", transactional=transactional)

    # inbound and receipts
    def handle_event(self, envelope: dict) -> str:
        data = envelope.get("data") or {}
        event_id, event_type = data.get("id"), data.get("event_type", "")
        payload = data.get("payload") or {}
        if not event_id:
            return "ignored: no event id"
        if not self.store.first_delivery(event_id, event_type):
            self.store.log("duplicate_webhook", event_id=event_id, event_type=event_type,
                           attempt=(envelope.get("meta") or {}).get("attempt"))
            return "duplicate"
        if event_type in ("message.sent", "message.finalized"):
            return self._receipt(payload, event_type)
        if event_type == "message.received":
            return self._inbound(payload)
        self.store.log("webhook_ignored", event_type=event_type)
        return f"ignored: {event_type}"

    def _receipt(self, payload: dict, event_type: str) -> str:
        telnyx_id = payload.get("id", "")
        status = (payload.get("to") or [{}])[0].get("status", "")
        errors = payload.get("errors") or []
        reason = None
        if errors:
            e = errors[0]
            reason = f"{e.get('code', '')} {e.get('title', '')}".strip()
        cost = (payload.get("cost") or {}).get("amount")
        current = self.store.message_by_telnyx_id(telnyx_id)
        if current is None:
            self.store.log("receipt_unknown_message", telnyx_id=telnyx_id, status=status)
            return "unknown message"
        if current["status"] in FINAL and status not in FINAL:
            # Webhooks can arrive out of order; a late "sent" must not undo "delivered".
            self.store.log("receipt_out_of_order", telnyx_id=telnyx_id, kept=current["status"], late=status)
            return f"kept {current['status']}"
        updated = self.store.update_status(telnyx_id, status, reason,
                                           float(cost) if cost not in (None, "") else None, FINAL)
        if not updated:
            kept = self.store.message_by_telnyx_id(telnyx_id)["status"]
            self.store.log("receipt_out_of_order", telnyx_id=telnyx_id, kept=kept, late=status)
            return f"kept {kept}"
        self.store.log("receipt", telnyx_id=telnyx_id, webhook=event_type, status=status, error=reason)
        return status

    def _inbound(self, payload: dict) -> str:
        phone = normalise_number((payload.get("from") or {}).get("phone_number", ""))
        text = (payload.get("text") or "").strip()
        self.store.add_message("inbound", phone, text, "inbound", segment_count(text), "received",
                               telnyx_id=payload.get("id"))
        kw = policy.keyword(text)
        telnyx_replied = bool(payload.get("autoresponse_type"))
        self.store.log("received", frm=mask(phone), keyword=kw, telnyx_autoresponse=payload.get("autoresponse_type"))
        if kw == "STOP":
            self.store.set_opt_out(phone, True)
            if not telnyx_replied:
                self.send_template(phone, "stop_confirmation", transactional=True)
            return "opted out"
        if kw == "START":
            self.store.set_opt_out(phone, False)
            self.store.set_handover(phone, False)
            if not telnyx_replied:
                self.send_template(phone, "start_confirmation", transactional=True)
            return "opted in"
        if kw == "HELP":
            if not telnyx_replied:
                self.send_template(phone, "help", transactional=True)
            return "help sent"
        if self.store.is_opted_out(phone):
            self.store.log("no_reply_opted_out", frm=mask(phone))
            return "no reply: opted out"
        if self.store.contact(phone)["handover"]:
            self.store.log("no_reply_with_person", frm=mask(phone))
            return "no reply: a person has this conversation"
        if not (self.s.ai_enabled and self.replier):
            return "no reply: AI off"
        history = self.store.history(phone)[:-1]  # the message just stored is passed separately
        reply = self.replier.reply(text, history)
        self.store.log("ai_reply", frm=mask(phone), source=reply.source, handover=reply.handover,
                       redacted=reply.redacted, reason=reply.reason, tokens_in=reply.input_tokens,
                       tokens_out=reply.output_tokens)
        result = self.send(phone, reply.text, kind="ai", conversational=True)
        if reply.handover:
            self.store.set_handover(phone, True)
            self.store.log("handover", frm=mask(phone), reason=reply.reason)
        return "replied" + (" + handover" if reply.handover else "") + f" ({result['status']})"

    def release(self, phone: str) -> None:
        """A person has finished; the assistant may answer this contact again."""
        self.store.set_handover(normalise_number(phone), False)
        self.store.log("handover_released", frm=mask(normalise_number(phone)))
