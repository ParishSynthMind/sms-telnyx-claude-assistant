# Telnyx facts this build relies on (checked in Telnyx's own docs, 6 November 2026)

Read from developers.telnyx.com, support.telnyx.com and telnyx.com on 6 November 2026. Where two Telnyx pages disagree, both are noted. None of this has been confirmed against a live Telnyx account yet.

## Sending (`POST https://api.telnyx.com/v2/messages`)
- Auth: `Authorization: Bearer <API key>`. Optional `Idempotency-Key` header: a repeat of a successful request with the same key is replayed (not re-sent) for up to 24 hours. This build sends one with every message and reuses it on retries.
- Body: `to` (E.164), `from` (needed when sending from a number), `messaging_profile_id` (needed for number pools and alphanumeric senders), `text`, optional `webhook_url`, `webhook_failover_url`, `use_profile_webhooks`, `type`, `media_urls` (MMS), `encoding`, `send_at`.
- Answer: `{"data": {...}}` with `id`, `record_type: "message"`, `direction: "outbound"`, `to[].status` (`queued` on send), `parts`, `encoding`, `cost{amount, currency}`, `errors[]`.
- Errors: `{"errors": [{"code", "title", "detail", "source", "meta"}]}`. HTTP 429 means retry after `retry-after`. Telnyx's pages give different meanings for some codes (for example `40300`), so this build reports the code, title and detail as given rather than guessing.
- Limits: SMS up to 1,600 characters; Telnyx says it supports up to 10 segments. GSM-7 is 160 characters, 153 per segment when split (Telnyx's 10DLC FAQ). The UCS-2 figures (70, 67 per segment) are the GSM standard; not found on a Telnyx page.
- Price (US, pricing page): $0.004 per message part plus a carrier fee.

## Webhooks
- Events: `message.received` (inbound), `message.sent`, `message.finalized` (the final delivery status). Envelope: `data.event_type`, `data.id`, `data.occurred_at`, `data.payload`, `meta.attempt`, `meta.delivered_to`.
- Statuses: `queued, sending, sent, delivered, sending_failed, delivery_failed, delivery_unconfirmed` (also `expired`, `read` in the API schema).
- Retries: up to 3 attempts per URL with backoff, then the failover URL (up to 6 in all). Duplicates and out-of-order delivery happen, so dedupe on `data.id`. Any 2xx is success.
- Timeout: the webhooks page says answer within 2 seconds; the send-message guide says 5. This build answers at once and does the work (including the Claude reply) after responding.
- Signature: headers `telnyx-signature-ed25519` (base64) and `telnyx-timestamp` (unix seconds). Signed string: `{timestamp}|{raw body}`. Verify with the account's public key (Portal, Keys & Credentials, Public Key, base64), on the raw body, and reject timestamps more than 5 minutes old. Webhooks come from `192.76.120.192/27`.

## Opt-out
- Telnyx handles STOP, STOPALL, STOP ALL, UNSUBSCRIBE, CANCEL, END, QUIT (opt out), START, UNSTOP (opt in) and HELP automatically at the messaging profile level, and marks the inbound webhook with `autoresponse_type`. This build keeps its own opt-out list as well (so it never even asks Telnyx to text a number that opted out) and does not send a second confirmation when Telnyx already auto-replied.

## Accounts (the part that decides the live test)
- New accounts may start on Trial, Paid, Verified, Enterprise tiers (older accounts: Level 1 and Level 2).
- Trial: the developer docs say USD 5 testing credit; a support article says there is currently no free trial credit. Trial texts go only to **one verified number**, long code only, 100 a day; one local number **of the account's own country**; one messaging profile, one API key.
- A business email is required at sign up ("certain free email providers are disallowed").
- Paid (a card added): local numbers of the account's own country only, no 10DLC, no toll-free verification. Verified removes those limits.
- So a UAE-based account would, on these rules, need the Verified tier for a US number. That is our reading of the rules, not a quote.

## US A2P 10DLC (fees from Telnyx's 10DLC fees article)
- Brand registration $4.50; campaign review $15 per submission; monthly campaign fee from $1.50 (low volume mixed) to $10 (standard), billed three months up front; sole proprietor brand $4.50, campaign $15, $2 a month.
- Steps: create brand, vet brand (1 to 7 business days), create campaign (then carrier approval), assign numbers. One brand per EIN, up to five campaigns per brand.
- Unregistered 10DLC traffic is blocked (Telnyx FAQ: from 3 February 2025).
