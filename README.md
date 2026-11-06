# SMS assistant on Telnyx, with Claude replies

A small FastAPI service for two-way business texting through the **Telnyx Messaging API**. It sends texts from templates, receives replies and delivery receipts through **signed webhooks**, honours **STOP / START / HELP**, and lets **Claude** (Sonnet 5.5) answer inbound questions from a business facts file, inside guardrails: private data removed before the model sees it, replies capped to SMS segments, and a hand-over to a person when it should. Every outbound text first passes opt-out, quiet hours, rate limits and a daily spend cap.

**Status, honestly:** built and tested against a faithful Telnyx mock (Telnyx's documented request, response, error and webhook shapes, signed with ed25519 the way Telnyx signs). The Claude replies are real. **It has not yet sent a text through a live Telnyx account**; that test is pending an account. US A2P 10DLC registration is covered as a [written guide](docs/A2P-10DLC-GUIDE.md), not as something done.

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

## Guardrails

- **Signed webhooks only.** `telnyx-signature-ed25519` is checked against the account's public key over `{telnyx-timestamp}|{raw body}`, on the raw bytes, with a 5 minute window against replays. Missing, forged or stale: 401. No public key configured: 503 (unless `ALLOW_UNSIGNED=1` for local development).
- **Answer fast, work after.** Telnyx expects a 2xx within about 2 seconds and a Claude reply can take longer, so the webhook answers 200 straight away and does the work afterwards. A failure there is written to the trail (`processing_failed`), never lost silently.
- **Retries processed once.** Telnyx retries webhooks and can deliver one twice; each event id is processed once.
- **Opt-out is enforced in this app,** not only at Telnyx, so a number that said STOP is never even sent to the API.
- **Quiet hours** (default 21:00 to 08:00 in the business's time zone) hold back texts the business starts. A direct answer to a text the person just sent isn't held back.
- **Rate limits** per contact per hour and across all contacts per minute, and a **daily spend cap** (real Telnyx cost where reported, otherwise an estimate per segment).
- **Claude never sees card or social security numbers** (removed first, and the customer is told not to text them), answers only from the facts file, treats the customer's text as data rather than instructions, and returns JSON (`reply`, `handover`, `reason`). The reply is stripped of links, markdown and number patterns and cut to `MAX_REPLY_SEGMENTS` (2). A refusal, an error or a bad answer falls back to a fixed "a team member will get back to you" and a hand-over.
- **No full phone numbers in logs or the trail** (`+1555***0199`). Admin endpoints need a bearer token.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # fill it in; never commit it

uvicorn smsassist.main:app --port 8000
# Telnyx needs a public https URL: expose it (Cloudflare or ngrok tunnel) and set it as the
# messaging profile's webhook URL, or as WEBHOOK_URL for per-message receipts.
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
pytest          # 112 tests, Telnyx and Claude mocked, no keys needed
ruff check .
```

Signatures against an independent computation of Telnyx's scheme (changed body, re-serialised JSON, wrong key, stale and future timestamps, malformed headers); segment maths; every guard; templates; the Claude request shape and every fallback; the Telnyx client (auth, error bodies, no retry on 4xx, retry with the same Idempotency-Key on 429 and 500, replay instead of a second send); and the whole flow with both sides signed: STOP/START/HELP, Telnyx's own auto-reply, hand-over and release, duplicate and out-of-order webhooks, receipts processed at the same moment from several threads, carrier failure, trial restrictions, forged webhooks, admin access, and no full numbers in the logs. Every flow test also fails if any webhook failed to process behind the scenes.

GitHub Actions runs lint and tests on every push, with no secrets.

## Tested on 6 November 2026 (local, Telnyx mocked)

[`scripts/local_run.py --live-claude`](scripts/local_run.py), output in [`examples/local-run/`](examples/local-run/):

- 6 texts accepted, 6 delivered; 21 signed webhooks, all answered 200; 3 repeat deliveries ignored.
- Claude Sonnet 5.5 answered parking, opening hours and price from the facts file; refused a discount code ("ignore your instructions..."); and on a text with a card number, never received the number (`redacted=True`), told the customer not to text it and flagged a hand-over. Four Claude calls, 4,837 input and 437 output tokens.
- A reminder at 22:00 New York time was held back (`quiet_hours`); after STOP, one confirmation went out and the next follow-up was blocked (`opted_out`) before reaching the API.
- An unsigned webhook and one signed with the wrong key both got 401, and changed nothing.
- **The first runs found two real bugs the unit tests had missed**, both fixed with tests: a receipt-logging error that only showed after the 200 (now every flow test checks the trail for processing failures), and two receipts processed at the same moment leaving a delivered text marked "sent" (now a guarded single-statement update, with locked reads on the shared database connection).

![What the guards did in the same run](media/local-run-guards.png)

## What is NOT proven yet

- **A live Telnyx account.** No real text has been sent or received. The API and webhook shapes, the signature scheme, the Idempotency-Key and the opt-out behaviour come from Telnyx's docs ([what was checked](docs/TELNYX-FACTS.md)); the live test will confirm them.
- **Real carriers and phones**: delivery times, carrier filtering, how a real handset shows multi-part texts.
- **US A2P 10DLC registration.** Not done; see the [guide](docs/A2P-10DLC-GUIDE.md). Without it, texts from a US 10-digit number to US phones are blocked by the carriers.
- **Volume.** One conversation at a time on SQLite; a busy client would want Postgres and a proper job queue.
- **MMS** (pictures) is not built.

## Project layout

```
smsassist/
  app.py           FastAPI: /telnyx/webhook, /send, /messages, /contacts, /trail, /health
  service.py       Messenger: guarded send, receipts, inbound keywords, Claude reply, hand-over
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
docs/TELNYX-FACTS.md, docs/A2P-10DLC-GUIDE.md
```

## Licence

MIT. Built by [ParishSynthMind](https://github.com/ParishSynthMind).
