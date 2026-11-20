# Twilio facts this build relies on (checked 20 November 2026)

Two kinds of fact, marked:
- **LIVE** - seen on our own Twilio trial account on 20 Nov 2026 (`examples/live-run/`).
- **DOCS** - from Twilio's documentation or help centre on 20 Nov 2026. Fees and rules change: check again before quoting a client.

## Sending
- `POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json`, form-encoded, HTTP Basic auth (Account SID : Auth Token). Answer 201 with the Message resource (`sid` "SM...", `status` "queued", `num_segments`). **LIVE**
- `StatusCallback` on the send makes Twilio post `sent` then `delivered` (or `undelivered`/`failed` with `ErrorCode`) to that URL. **LIVE** (sent and delivered both arrived within 4 seconds, both signed).
- Errors come back as `{"code", "message", "more_info", "status"}`. **LIVE** (572006).
- The Messages API takes no idempotency key, so this app never retries a send that may have reached Twilio (only 429 and connections that never opened). **DOCS**

## Trial accounts (the 2026 "free units" trial)
- **Free text is refused**: error **572006** "Invalid template name. Trial accounts can only use predefined SMS templates." **LIVE**
- The trial only sends Twilio's own texts, chosen by putting a template name in `Body` (`sms_appointment_reminders`, `sms_customer_support`, `sms_order_confirmation`, `sms_account_alerts`, ...). `sms_customer_support` arrives as "Hi, this is Support. We received your message and will assist shortly. Reply with details. Test message from Twilio." **LIVE**
- No "Sent from your Twilio trial account" prefix was added to the template text. **LIVE**
- Texts go only to verified numbers (error 21608 otherwise). **DOCS**
- The trial gives a Twilio-provided number (ours is a UK +44 number) that is NOT listed under IncomingPhoneNumbers; its inbound texts go to a webhook set in the console (Try SMS, Receive, Auto-reply: Custom). **LIVE**
- Consequence: on a trial, the app can prove sending, receipts, inbound, signatures, STOP and Claude's answer, but the words that reach the phone are Twilio's template, not ours. `TWILIO_TRIAL_TEMPLATE` exists only for that. On a paid account, leave it empty and the app's own text (AI disclosure, STOP wording, Claude's reply) is sent.

## Webhook signatures
- `X-Twilio-Signature` = base64(HMAC-SHA1(Auth Token, full URL + every POST param sorted by name, name then value)). **DOCS**, and the code reproduces Twilio's published example (`0/KCTR6DLpKmkAf8muzZqo1nDgQ=`). **LIVE**: every real status callback and inbound text verified.
- The URL is the public one Twilio called, so behind a tunnel the app uses `PUBLIC_BASE_URL`, never `request.url`.
- The inbound webhook must answer with XML (TwiML) or Twilio logs error 12300 (invalid content type): this app answers an empty `<Response></Response>` and sends its reply through the REST API. **LIVE** (12300 seen on the console's own default handler on 18 Nov).

## WhatsApp on the same number
- The trial number also takes WhatsApp. Messages sent to it from WhatsApp arrive with `From: whatsapp:+44...`, go to Twilio's own WhatsApp auto-reply rather than the SMS webhook, and would reach an SMS webhook with that prefix. **LIVE**. The app records and ignores non-SMS channels.

## Error 12300 on the trial
- Twilio logged 12300 (invalid content type) on every inbound message to the trial number, including on 18 Nov with Twilio's own standard handler and on 20 Nov while our app answered `application/xml`. **LIVE, observed**; the cause is on the trial's side as far as we can tell, and the messages were processed.

## Opt-out
- **LIVE (UK trial number):** STOP reached our webhook with `OptOutType=STOP`, i.e. Twilio handled it; after a STOP over WhatsApp, Twilio refused its own next reply with 21610.
- Twilio answers STOP/START/HELP itself on US and Canadian long codes and toll-free numbers, and blocks later sends to that person (error 21610). With Advanced Opt-Out the inbound webhook carries `OptOutType`. **DOCS**. Outside the US/Canada that default does not apply, so the app sends its own one confirmation (`PROVIDER_ANSWERS_KEYWORDS=auto` decides by the sending number; `yes`/`no` override).

## US A2P 10DLC (Twilio, help centre, 20 Nov 2026) - DOCS
- Brand registration $4.50 one-time (Standard brand with secondary vetting $46 in total).
- Campaign vetting $15 one-time, not refunded if denied.
- Monthly campaign fee: low-volume mixed $1.50, sole proprietor $2, standard $10.
- Unregistered texts from US 10-digit numbers are blocked (error 30034). Full steps: [A2P-10DLC-GUIDE.md](A2P-10DLC-GUIDE.md).

## UAE (+971) recipients
- Texts to UAE mobiles need an alphanumeric **sender ID registered with the TDRA** (the UAE telecoms regulator), which needs a UAE trade licence and takes weeks; long numbers are generally not delivered. Plan for it before promising a UAE client two-way SMS: replies from UAE phones to an international long number are often not possible, so the usual design there is one-way alerts from a registered sender ID plus WhatsApp for conversation.
