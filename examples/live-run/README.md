# Twilio live run, 20 November 2026

Twilio **trial** account, a Twilio-provided UK number, Parish's own verified UK phone, this app behind a Cloudflare quick tunnel, Claude `claude-haiku-5-5`. Phone numbers masked; no keys.

What happened, in order (times Dubai):

1. 10:21 and 10:23: the app's own text was refused by Twilio, error **572006** ("Trial accounts can only use predefined SMS templates"). Recorded as `failed`, nothing sent.
2. 10:25: with `TWILIO_TRIAL_TEMPLATE=sms_customer_support`, a send reached the phone. Twilio posted `sent` then `delivered` to `/twilio/status`; both **X-Twilio-Signature** checks passed.
3. 10:37: the first two messages from the phone came over **WhatsApp**, not SMS, so Twilio answered them itself. Replaying them from Twilio's log (signed locally with the real token) showed the app **failing behind the 200** on a `whatsapp:` sender. Fixed (non-SMS channels are recorded and ignored) with a test.
4. 12:09: a real SMS "Is there parking?" arrived at `/twilio/sms`, signed by Twilio, verified. Claude answered from the facts file with the AI disclosure first; the reply went out through the REST API and was `delivered` (signed callbacks again). **The phone showed Twilio's template text, not Claude's words** (trial limit); `messages.json` has what the app meant to send.
5. 12:09: "STOP" arrived with `OptOutType=STOP` (Twilio answered it itself), so the app opted the number out without a second confirmation.
6. 12:10: a follow-up template to the same phone was **blocked (`opted_out`) before reaching Twilio**.

Files: `messages.json` (the app's store), `trail.json` (every decision), `contacts.json`, `twilio-message-log.json` (Twilio's own log for the same period, incl. the WhatsApp messages), `summary.json`.
