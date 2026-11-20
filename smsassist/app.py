"""FastAPI: the provider webhooks (Telnyx or Twilio), plus a few admin endpoints.

Telnyx wants a 2xx within about 2 seconds and Twilio within 15, and a Claude reply can
take longer, so each webhook verifies the signature, answers straight away and does the
work after the response. The reply to a text is sent through the REST API, never inside
Twilio's TwiML answer, so a slow model can't time the webhook out."""
import hmac
import json
import logging
import threading
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from . import signature, twilio_api, twilio_signature
from .ai import ClaudeReplier
from .config import Settings
from .service import Messenger
from .store import Store
from .telnyx_api import TelnyxClient
from .twilio_api import TwilioClient

log = logging.getLogger("smsassist")
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def build_provider(settings: Settings):
    if settings.provider == "twilio":
        if settings.twilio_account_sid and settings.twilio_auth_token:
            return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_api_base,
                                trial_template=settings.twilio_trial_template)
        return None
    return TelnyxClient(settings.telnyx_api_key, settings.api_base) if settings.telnyx_api_key else None


def build_messenger(settings: Settings) -> Messenger:
    store = Store(settings.db_path)
    telnyx = build_provider(settings)
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
    app = FastAPI(title=f"SMS assistant ({settings.provider})")
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

    def twilio_params(request: Request, body: bytes, path: str) -> list:
        """Check X-Twilio-Signature against the public URL Twilio called; 401 if it fails."""
        if settings.provider != "twilio":
            raise HTTPException(404, "Twilio is not the configured provider")
        query = request.url.query
        url = settings.public_url(path) if settings.public_base_url else str(request.url).split("?")[0]
        if query:
            url += "?" + query
        if not settings.twilio_auth_token:
            if not settings.allow_unsigned:
                raise HTTPException(503, "TWILIO_AUTH_TOKEN not configured")
            return twilio_signature.parse_form(body)
        try:
            return twilio_signature.verify(settings.twilio_auth_token, url, body, request.headers)
        except twilio_signature.TwilioSignatureError as exc:
            messenger.store.log("signature_rejected", provider="twilio", reason=str(exc))
            raise HTTPException(401, "invalid signature")

    def queue(background: BackgroundTasks, envelope: dict) -> None:
        with inflight_lock:
            inflight["n"] += 1
        background.add_task(process, envelope)

    @app.post("/twilio/sms")
    async def twilio_sms(request: Request, background: BackgroundTasks):
        params = twilio_params(request, await request.body(), "/twilio/sms")
        envelope = twilio_api.inbound_envelope(params, settings.provider_replies_to_keywords)
        if not envelope["data"]["id"]:
            raise HTTPException(400, "not a Twilio message")
        queue(background, envelope)
        # Empty TwiML: Twilio sends nothing itself; our answer goes out through the REST API.
        return Response(EMPTY_TWIML, media_type="application/xml")

    @app.post("/twilio/status")
    async def twilio_status(request: Request, background: BackgroundTasks):
        params = twilio_params(request, await request.body(), "/twilio/status")
        envelope = twilio_api.status_envelope(params)
        if not envelope["data"]["payload"]["id"]:
            raise HTTPException(400, "not a Twilio status callback")
        queue(background, envelope)
        return Response(status_code=204)

    @app.get("/health")
    def health():
        signed = public_key is not None if settings.provider == "telnyx" else bool(settings.twilio_auth_token)
        return {"ok": True, "provider": settings.provider, "signed_webhooks": signed, "ai": bool(messenger.replier),
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
