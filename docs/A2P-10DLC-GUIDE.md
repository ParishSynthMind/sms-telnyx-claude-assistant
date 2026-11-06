# US A2P 10DLC registration: a plain-English guide

**This is documentation, not a claim.** I have not registered a brand or campaign for anyone yet. Everything below comes from Telnyx's own docs and support articles, read on 6 November 2026 (see [TELNYX-FACTS.md](TELNYX-FACTS.md)). Fees and timelines change, so check them again before quoting a client.

## What it is

- **A2P** means application-to-person: texts sent by software (reminders, alerts, replies from a bot), as opposed to a person texting from their phone.
- **10DLC** means a normal 10-digit US phone number (a "long code") used for business texting.
- **Registration** means telling the US carriers (AT&T, T-Mobile, Verizon and others) who the business is (the **brand**) and what it will text about (the **campaign**), through The Campaign Registry (TCR). Telnyx submits it for you from its portal or API.

## Why it matters

Unregistered business texting from US 10-digit numbers is blocked by the carriers (Telnyx's FAQ: from 3 February 2025). Registered traffic also gets better throughput: the higher the brand's trust score, the more texts a minute AT&T allows and the higher T-Mobile's daily cap.

## The steps in Telnyx

1. **Account tier.** On Telnyx's newer account tiers, 10DLC needs a **Verified** account (a Paid account can't use it). Older accounts call this Level 2.
2. **Create the brand** (Portal, Messaging, 10DLC, Brands): legal company name, EIN (US tax ID) or the equivalent, address, website, contact, vertical. A business without an EIN can register as a **sole proprietor** (one campaign, one number, verified by a code sent to a US or Canadian mobile).
3. **Vet the brand** (optional but usual): an outside vetting gives a trust score from 0 to 100. Telnyx says 1 to 7 business days.
4. **Create the campaign:** the use case (for example "customer care" or "account notifications"), a description, sample messages, how people opt in, and the opt-out and help wording. Then carrier review.
5. **Assign the numbers** (which must already be on a messaging profile) to the campaign.

## What the campaign needs to pass (common reasons for rejection)

- A clear, honest opt-in: the customer gave their number for these texts (a form with a consent line, a text-to-join keyword, and so on). Describe it exactly.
- Sample messages that match the use case and include the business name.
- Opt-out wording ("Reply STOP to opt out") and HELP wording. This repo's templates already do this, and the app honours STOP, START and HELP.
- A working website that matches the brand, with a privacy policy that says mobile numbers aren't shared or sold.
- No banned content (for example cannabis, debt relief, some loan types) under the wrong use case.

## Fees (Telnyx, which says it passes TCR fees through with no markup)

| Item | Fee |
|---|---|
| Brand registration | $4.50 one-off |
| Campaign review | $15 per submission (a resubmission is charged again) |
| Campaign monthly fee | $1.50 (low volume mixed) to $10 (standard), billed three months up front |
| Sole proprietor | $4.50 brand, $15 campaign review, $2 a month |
| Per text (US) | $0.004 per segment plus a carrier fee |

Brand vetting may carry its own fee; Telnyx's fee article didn't list one.

## Timeline to expect

Brand creation is instant; vetting 1 to 7 business days; campaign approval then depends on the carriers (Telnyx's pages say a few days). Plan on one to two weeks before a client can text at volume, longer if the campaign is rejected and resubmitted.

## Alternatives

- **Toll-free numbers** need their own verification (Telnyx: typically 1 to 2 weeks) and then allow up to 20 messages a second.
- **Short codes** are fast but expensive and slow to get.
- **Outside the US** none of this applies, but each country has its own rules (for example, the UAE bans promotional texts between 21:00 and 07:00 and has paused new alphanumeric sender registrations).
