# SMS assistant on Twilio and Telnyx, with Claude replies

A small FastAPI service for two-way business texting through **Twilio Programmable Messaging** or the **Telnyx Messaging API** (one setting, `SMS_PROVIDER`, picks which). It sends texts from templates, receives replies and delivery receipts through **signed webhooks**, honours **STOP / START / HELP**, and lets **Claude** (Sonnet 5.5) answer inbound questions from a business facts file, inside guardrails: private data removed before the model sees it, replies capped to SMS segments, and a hand-over to a person when it should. Every outbound text first passes opt-out, quiet hours, rate limits and a daily spend cap.

**Status, honestly:**

| | Twilio | Telnyx |
|---|---|---|
| Real send to a real phone | **Proven live** 20 Nov 2026 (trial account, UK number) | Not run (account never got past sign-up) |
| Signed delivery receipts | **Proven live** (sent + delivered, X-Twilio-Signature verified) | Mocked |
| Signed inbound text, STOP, Claude reply | **Proven live**: a real SMS from the phone, Twilio-signed and verified, Claude answered, reply delivered; STOP opted the number out and the next send was blocked before reaching Twilio | Mocked |
| The app's own words on the phone | **Not possible on a Twilio trial**: it only sends Twilio's predefined template texts (error 572006). The AI disclosure, STOP wording and Claude's reply are recorded in the trail, not seen on the handset. Needs a paid account. | Mocked |
| US A2P 10DLC registration | [Written guide](docs/A2P-10DLC-GUIDE.md) only, not done | Guide only |

Everything is also covered offline by 186 tests against faithful mocks of both providers. What was checked live and what came from docs: [docs/TWILIO-FACTS.md](docs/TWILIO-FACTS.md), [docs/TELNYX-FACTS.md](docs/TELNYX-FACTS.md).

![Live on Twilio: a real phone, signed webhooks, Claude's reply, STOP](media/live-run-twilio.png)

*Twilio live run, 20 November 2026 ([details and raw output](examples/live-run/)). The green bubbles are what the app sent; on a trial account the phone receives Twilio's template text instead, shown under each.*

![A recorded local run: reminder, two-way questions answered by Claude, a card number removed, STOP](media/local-run-conversation.png)

*A recorded local run, 6 November 2026: this app and the mock Telnyx as two real HTTP servers on localhost, webhooks signed both ways, replies written live by Claude Sonnet 5.5. No real phone received these. Raw output in [`examples/local-run/`](examples/local-run/).*

## How it works

```mermaid
flowchart LR
    P[Customer's phone] <-->|SMS| T[Telnyx]
    A[This service<br/>FastAPI] -->|POST /v2/messages<br/>Bearer key, Idempotency-Key| T
    T -->|webhooks, ed25519 signed:<br/>message.received,<br/>message.sent, message.finalized| A
    A --> G{Guards<br/>opt-out, quiet hours,<br/>rate, spend cap}
    A --> C[Claude Sonnet 5.5<br/>structured JSON reply]
    A --> D[(SQLite<br/>contacts, messages,<br/>webhook ids, trail)]
```

1. **Sending.** `Messenger.send()` (CLI, `POST /send`, or code) counts segments (GSM-7 160/153, UCS-2 70/67), checks the guards, then calls `POST https://api.telnyx.com/v2/messages` with a fresh `Idempotency-Key`. 429 and 5xx answers are retried with backoff **under the same key**, so a retry can't send the text twice. Telnyx's `{"errors": [...]}` answers are recorded with their code and title.
2. **Receipts.** `message.sent` and `message.finalized` update the message's status (`delivered`, `delivery_failed` with the carrier's error, ...). A late or out-of-order "sent" never overwrites "delivered": the check and the write are one SQL statement.
3. **Inbound.** `message.received` is stored, then:
   - **STOP** (and STOPALL, STOP ALL, UNSUBSCRIBE, CANCEL, END, QUIT, OPTOUT, REVOKE): the number is opted out, one confirmation goes back, and nothing else is ever sent to it until **START**. **HELP** gets the help template. If Telnyx already auto-replied (it marks the webhook with `autoresponse_type`), no second confirmation is sent.
   - Anything else: Claude writes the reply from `data/business.md`, unless the contact is opted out or a person has taken the conversation over.
4. **Hand-over.** When Claude says a person should take it (a booking change, a complaint, a billing question, pain, someone upset, private data) the contact is flagged and the assistant stays quiet until staff run `release`.

### With Twilio

The same messenger and rules; only the edges change.

- **Send:** `POST /2010-04-01/Accounts/{sid}/Messages.json`, form-encoded, Basic auth, `StatusCallback` set to `{PUBLIC_BASE_URL}/twilio/status`. Twilio's Messages API has **no idempotency key**, so the client retries only a 429 (Twilio refused it) or a connection that never opened. A 5xx or timeout is recorded as failed for a person to check, never re-sent blindly: a double text is worse than a missed one.
- **Inbound:** Twilio posts the text as a form to `/twilio/sms`. The app checks `X-Twilio-Signature` (HMAC-SHA1 over the **public** URL plus the sorted parameters, keyed with the Auth Token), answers an empty TwiML `<Response></Response>` at once, and sends its reply through the REST API afterwards, so a slow model can't time out the webhook.
- **Receipts:** `/twilio/status` gets `sent`, `delivered`, `undelivered` (with Twilio's `ErrorCode`, e.g. 30003) and maps them onto the same statuses as Telnyx. Twilio sends no event id, so `MessageSid:status` is the id: a retried callback is ignored, the next status is not.
- **STOP:** on US and Canadian numbers Twilio answers STOP itself and blocks later sends (21610), so the app records the opt-out and sends no second confirmation (`OptOutType` on the webhook says the same where Advanced Opt-Out is on). Elsewhere (the UK number in the live test) the app sends its own one confirmation. `PROVIDER_ANSWERS_KEYWORDS=auto|yes|no`.
- **Trial accounts:** a 2026 Twilio trial refuses any free-text body (572006) and texts only verified numbers (21608). `TWILIO_TRIAL_TEMPLATE=sms_customer_support` makes the app send the name of one of Twilio's own templates instead, so the whole loop can be run live; the text the app meant to send stays in the store. Never set it on a paid account.

## Guardrails

- **Signed webhooks only.** `telnyx-signature-ed25519` is checked against the account's public key over `{telnyx-timestamp}|{raw body}`, on the raw bytes, with a 5 minute window against replays. Missing, forged or stale: 401. No public key configured: 503 (unless `ALLOW_UNSIGNED=1` for local development).
- **Answer fast, work after.** Telnyx expects a 2xx within about 2 seconds and a Claude reply can take longer, so the webhook answers 200 straight away and does the work afterwards. A failure there is written to the trail (`processing_failed`), never lost silently.
- **Retries processed once.** Telnyx retries webhooks and can deliver one twice; each event id is processed once.
- **Opt-out is enforced in this app,** not only at Telnyx, so a number that said STOP is never even sent to the API.
- **Quiet hours** (default 21:00 to 08:00 in the business's time zone) hold back texts the business starts. A direct answer to a text the person just sent isn't held back.
- **Rate limits** per contact per hour and across all contacts per minute, and a **daily spend cap** (real Telnyx cost where reported, otherwise an estimate per segment).
- **Claude never sees card or social security numbers** (removed first, and the customer is told not to text them), answers only from the facts file, treats the customer's text as data rather than instructions, and returns JSON (`reply`, `handover`, `reason`). The reply is stripped of links, markdown and number patterns and cut to `MAX_REPLY_SEGMENTS` (2). A refusal, an error or a bad answer falls back to a fixed "a team member will get back to you" and a hand-over.
- **No full phone numbers in logs or the trail** (`+1555***0199`). Admin endpoints need a bearer token.

## AI disclosure (EU AI Act Article 50)

The first AI text in each conversation starts with "I'm an AI assistant for Brightside Dental." Later texts don't repeat it. A hand-over text starts with "I'm an AI assistant for Brightside Dental, passing you to a person on our team.", and after staff `release` a contact (or the customer texts START again) the next AI text discloses again; a new or reset database counts as a new conversation. HELP replies say "(AI assistant)" and stay one segment; the disclosure may add one segment to that first reply, never more. The text is set with `AI_DISCLOSURE`; blanking it falls back to the default, and it can only be removed when `AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST` holds a reference to the client's recorded written request. This is covered by offline tests; the recorded local run below predates it, so its texts and screenshots show the old wording.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # fill it in; never commit it

uvicorn smsassist.main:app --port 8000
# Telnyx needs a public https URL: expose it (Cloudflare or ngrok tunnel) and set it as the
# messaging profile's webhook URL, or as WEBHOOK_URL for per-message receipts.
```

With Twilio:

```bash
export SMS_PROVIDER=twilio TWILIO_FROM_NUMBER=+447700900100 PUBLIC_BASE_URL=https://your-tunnel.example.com
export TWILIO_ACCOUNT_SID=$(security find-generic-password -s twilio-sid -w)     # from a secret store, never a file
export TWILIO_AUTH_TOKEN=$(security find-generic-password -s twilio-token -w)
uvicorn smsassist.main:app --port 8000
# Console: the number's "A message comes in" webhook = {PUBLIC_BASE_URL}/twilio/sms (HTTP POST)
```

```bash
python -m smsassist send --to +15550100199 --template appointment_reminder --var name=Sam --var "when=Tue 3pm"
python -m smsassist send --to +15550100199 --text "Your order is ready"
python -m smsassist segments "Café opens at 9 😀"    # {"encoding": "UCS-2", "length": 18, "segments": 1}
python -m smsassist log          # messages and their delivery status
python -m smsassist trail        # what the system did and why
python -m smsassist contacts     # opt-out and hand-over state
python -m smsassist release +15550100199
```

`DRY_RUN=1` records messages without calling Telnyx.

### Without a Telnyx account

```bash
python scripts/local_run.py                     # offline replies
ANTHROPIC_API_KEY=... python scripts/local_run.py --live-claude --out examples/local-run
```

Starts this app and `smsassist/mock_telnyx.py` as two HTTP servers and plays a scripted customer against them. The mock follows Telnyx's documented shapes and also behaves like a trial account (texts only to a verified number), answers STOP itself as Telnyx does, fails on demand (429, 500, carrier failure), and delivers webhooks twice or out of order. Where it has to guess (the exact error code a trial account gets), the code says so.

## Tests

```bash
pytest          # 186 tests, Twilio, Telnyx and Claude mocked, no keys needed
ruff check .
```

Signatures against an independent computation of Telnyx's scheme (changed body, re-serialised JSON, wrong key, stale and future timestamps, malformed headers); segment maths; every guard; templates; the Claude request shape and every fallback; the Telnyx client (auth, error bodies, no retry on 4xx, retry with the same Idempotency-Key on 429 and 500, replay instead of a second send); and the whole flow with both sides signed: STOP/START/HELP, Telnyx's own auto-reply, hand-over and release, the AI disclosure, duplicate and out-of-order webhooks, receipts processed at the same moment from several threads, carrier failure, trial restrictions, forged webhooks, admin access, and no full numbers in the logs. Every flow test also fails if any webhook failed to process behind the scenes.

Twilio (59 tests): the signature code against Twilio's own published worked example, every tampered field, the public-URL rule behind a tunnel; the REST client (Basic auth, form body, messaging service, error codes 20003/21211/21606/21608/21610/572006, retry on 429 only, no retry on 500 or a read timeout); and the full flow through the app: receipts, empty TwiML, forged and unsigned requests, duplicates, out-of-order receipts, carrier failure, STOP with and without Twilio answering, START, quiet hours, hand-over, and the trial template mode.

GitHub Actions installs the exact versions in `requirements.lock`, runs lint, tests and `pip-audit` on every push, with no secrets (0 known vulnerabilities on 20 Nov 2026, before and after pinning).

## Tested live on Twilio, 20 November 2026

Trial account, Twilio-provided UK number, a verified UK phone, the app behind a Cloudflare quick tunnel, Claude `claude-haiku-5-5`. Full story and raw data: [`examples/live-run/`](examples/live-run/).

- **Sending:** the app's own text was refused with **572006** (trial: predefined templates only) and recorded as failed. With `TWILIO_TRIAL_TEMPLATE` a send reached the phone; Twilio posted `sent` and `delivered`, both signatures verified.
- **Inbound:** a real "Is there parking?" SMS arrived at `/twilio/sms` signed by Twilio and verified. Claude answered from the facts file, starting with the AI disclosure; the reply was delivered (the phone showed Twilio's template wording, the trial limit).
- **STOP:** arrived with `OptOutType=STOP` (Twilio answered it), so the app opted the number out with no second confirmation; the next template to that phone was **blocked before reaching Twilio**.
- **A bug the live run found:** the first two messages from the phone came over **WhatsApp** to the same Twilio number (`From: whatsapp:+44...`). The first version failed on them behind the 200 (the trail logged `processing_failed`, so it wasn't silent). Fixed: non-SMS channels are recorded and ignored, with a test. Those records were replayed from Twilio's log, signed locally with the real token, through the public URL; an unsigned copy got 401.
- Cost: 2 texts delivered, from the trial's free allowance. Nothing paid.

## Tested on 6 November 2026 (local, Telnyx mocked)

[`scripts/local_run.py --live-claude`](scripts/local_run.py), output in [`examples/local-run/`](examples/local-run/):

- 6 texts accepted, 6 delivered; 21 signed webhooks, all answered 200; 3 repeat deliveries ignored.
- Claude Sonnet 5.5 answered parking, opening hours and price from the facts file; refused a discount code ("ignore your instructions..."); and on a text with a card number, never received the number (`redacted=True`), told the customer not to text it and flagged a hand-over. Four Claude calls, 4,837 input and 437 output tokens.
- A reminder at 22:00 New York time was held back (`quiet_hours`); after STOP, one confirmation went out and the next follow-up was blocked (`opted_out`) before reaching the API.
- An unsigned webhook and one signed with the wrong key both got 401, and changed nothing.
- **The first runs found two real bugs the unit tests had missed**, both fixed with tests: a receipt-logging error that only showed after the 200 (now every flow test checks the trail for processing failures), and two receipts processed at the same moment leaving a delivered text marked "sent" (now a guarded single-statement update, with locked reads on the shared database connection).

![What the guards did in the same run](media/local-run-guards.png)

## What is NOT proven yet

- **The app's own text reaching a phone through Twilio.** The trial only delivers Twilio's template texts; our wording (AI disclosure, STOP line, Claude's reply) needs a paid account to reach a handset.
- **Twilio at volume, Messaging Services, MMS, Advanced Opt-Out** and a US number (so no live 10DLC traffic).
- **A live Telnyx account.** No real text has been sent or received. The API and webhook shapes, the signature scheme, the Idempotency-Key and the opt-out behaviour come from Telnyx's docs ([what was checked](docs/TELNYX-FACTS.md)); the live test will confirm them.
- **Real carriers and phones**: delivery times, carrier filtering, how a real handset shows multi-part texts.
- **US A2P 10DLC registration.** Not done; see the [guide](docs/A2P-10DLC-GUIDE.md). Without it, texts from a US 10-digit number to US phones are blocked by the carriers (Twilio error 30034). Twilio's fees on 20 Nov 2026: brand $4.50, campaign vetting $15, then $1.50 to $10 a month.
- **UAE (+971) recipients.** Texts to UAE mobiles need a sender ID registered with the TDRA (needs a UAE trade licence, takes weeks); international long numbers are generally not delivered there. Not attempted.
- **Volume.** One conversation at a time on SQLite; a busy client would want Postgres and a proper job queue.
- **MMS** (pictures) is not built.

## Project layout

```
smsassist/
  app.py           FastAPI: /twilio/sms, /twilio/status, /telnyx/webhook, /send, /messages, /contacts, /trail, /health
  service.py       Messenger: guarded send, receipts, inbound keywords, Claude reply, hand-over
  twilio_api.py    Twilio Messages client (no blind retries), status map, webhook -> event translation
  twilio_signature.py  X-Twilio-Signature (HMAC-SHA1) check
  mock_twilio.py   the Twilio mock (trial behaviour checked live)
  telnyx_api.py    Telnyx v2 messages client (errors, backoff, Idempotency-Key)
  signature.py     ed25519 webhook verification (and signing, for the mock and tests)
  policy.py        STOP/START/HELP, quiet hours, rate limits, spend cap
  ai.py            Claude reply with redaction, output cleaning, segment cap, fallback
  segments.py      GSM-7 / UCS-2 segment counting and truncation
  templates.py     message templates, every placeholder required
  store.py         SQLite: contacts, messages, webhook ids, trail
  mock_telnyx.py   the Telnyx mock used by the tests and the local run
  cli.py           python -m smsassist ...
scripts/local_run.py, scripts/render_media.py
data/templates.json, data/business.md (a fictional dental practice)
docs/TWILIO-FACTS.md, docs/TELNYX-FACTS.md, docs/A2P-10DLC-GUIDE.md
scripts/export_live_run.py (live database -> examples/live-run/, numbers masked)
```

## Licence

MIT. Built by [ParishSynthMind](https://github.com/ParishSynthMind).
