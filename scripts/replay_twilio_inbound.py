"""Replay real inbound texts from Twilio's own message log into the app, signed the way
Twilio signs (X-Twilio-Signature with the account's Auth Token, over the public URL).

Used on 20 Nov 2026 because the trial's custom inbound webhook stayed on Twilio's
standard auto-reply: the texts were real and are in Twilio's log, but Twilio never posted
them to us. The signature here is made locally with the real token, so this proves the
app's inbound path on real message records, not Twilio's own delivery of them.

    TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... python scripts/replay_twilio_inbound.py \
        https://<public host> SMxxxx [SMyyyy ...] [--twice]
"""
import os
import sys
from urllib.parse import urlencode

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from smsassist.twilio_signature import sign  # noqa: E402


def main(base, sids, twice):
    sid, tok = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    url = base.rstrip("/") + "/twilio/sms"
    for msid in sids:
        m = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{msid}.json", auth=(sid, tok)).json()
        assert m["direction"] == "inbound", m["direction"]
        params = [("ToCountry", "GB"), ("SmsMessageSid", m["sid"]), ("NumMedia", m["num_media"]),
                  ("SmsSid", m["sid"]), ("SmsStatus", "received"), ("Body", m["body"]), ("To", m["to"]),
                  ("NumSegments", m["num_segments"]), ("MessageSid", m["sid"]), ("AccountSid", sid),
                  ("From", m["from"]), ("ApiVersion", "2010-04-01")]
        for attempt in range(2 if twice else 1):
            r = httpx.post(url, content=urlencode(params).encode(), headers=sign(tok, url, params), timeout=20)
            print(msid[:10] + "...", repr(m["body"]), "attempt", attempt + 1, "->", r.status_code, r.text[:60])
        # an unsigned copy must be refused
    r = httpx.post(url, content=b"Body=hi&From=%2B10000000000", headers={"Content-Type": "application/x-www-form-urlencoded"})
    print("unsigned ->", r.status_code)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], args[1:], "--twice" in sys.argv)
