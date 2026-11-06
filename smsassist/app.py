"""FastAPI: the Telnyx webhook, plus a few admin endpoints.

Telnyx wants a 2xx within about 2 seconds, and a Claude reply can take longer, so the
webhook verifies the signature, checks the event is new, answers 200 straight away and
does the work after the response."""
import hmac
import json
import logging
import threading
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from . import signature
from .ai import ClaudeReplier
from .config import Settings
from .service import Messenger
from .store import Store
from .telnyx_api import TelnyxClient

log = logging.getLogger("smsassist")


def build_messenger(settings: Settings) -> Messenger:
    store = Store(settings.db_path)
    telnyx = TelnyxClient(settings.telnyx_api_key, settings.api_base) if settings.telnyx_api_key else None
    replier = None
    if settings.ai_enabled:
        from pathlib import Path

        facts = Path(settings.knowledge_path).read_text() if Path(settings.knowledge_path).exists() else ""
        replier = ClaudeReplier(settings.business_name, facts, settings.ai_model, settings.max_reply_segments)
    return Messenger(settings, store, telnyx, replier)


class SendBody(BaseModel):
    to: str
    text: Optional[str] = None
    template: Optional[str] = None
    values: dict = {}


def create_app(settings: Optional[Settings] = None, messenger: Optional[Messenger] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    messenger = messenger or build_messenger(settings)
    public_key = signature.load_public_key(settings.telnyx_public_key) if settings.telnyx_public_key else None
    app = FastAPI(title="SMS assistant on Telnyx")
    app.state.messenger = messenger
    inflight = {"n": 0}
    inflight_lock = threading.Lock()

    def admin(authorization: str = Header(default="")) -> None:
        token = settings.admin_token
        if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "admin token required")

    def process(envelope: dict) -> None:
        try:
            outcome = messenger.handle_event(envelope)
            log.info("event %s %s: %s", envelope["data"].get("event_type"), envelope["data"].get("id"), outcome)
        except Exception as exc:  # logged to the trail so a failure is never silent
            messenger.store.log("processing_failed", event_id=envelope["data"].get("id"), error=repr(exc)[:300])
            log.exception("processing failed")
        finally:
            with inflight_lock:
                inflight["n"] -= 1

    @app.post("/telnyx/webhook")
    async def webhook(request: Request, background: BackgroundTasks):
        body = await request.body()
        if public_key is None:
            if not settings.allow_unsigned:
                raise HTTPException(503, "TELNYX_PUBLIC_KEY not configured")
        else:
            try:
                signature.verify(body, request.headers, public_key)
            except signature.SignatureError as exc:
                messenger.store.log("signature_rejected", reason=str(exc))
                raise HTTPException(401, "invalid signature")
        try:
            envelope = json.loads(body)
            envelope["data"]["id"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "not a Telnyx event")
        with inflight_lock:
            inflight["n"] += 1
        background.add_task(process, envelope)
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True, "signed_webhooks": public_key is not None, "ai": bool(messenger.replier),
                "dry_run": settings.dry_run, "processing": inflight["n"]}

    @app.post("/send", dependencies=[Depends(admin)])
    def send(body: SendBody):
        if bool(body.text) == bool(body.template):
            raise HTTPException(422, "give text or template, not both")
        try:
            if body.template:
                return messenger.send_template(body.to, body.template, body.values)
            return messenger.send(body.to, body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/messages", dependencies=[Depends(admin)])
    def messages(phone: Optional[str] = None, limit: int = 50):
        return messenger.store.messages(phone, limit)

    @app.get("/contacts", dependencies=[Depends(admin)])
    def contacts():
        return messenger.store.contacts()

    @app.get("/trail", dependencies=[Depends(admin)])
    def trail(limit: int = 100):
        return messenger.store.trail(limit)

    return app
