"""Export a live run from the local database (and Twilio's own message log) into
examples/live-run/, with every phone number masked and no keys.

    TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... python scripts/export_live_run.py var/live/sms.db examples/live-run
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import httpx

PHONE = re.compile(r"\+\d{8,15}")


def mask(s):
    return PHONE.sub(lambda m: m.group(0)[:-7] + "***" + m.group(0)[-4:], s) if isinstance(s, str) else s


def deep_mask(o):
    if isinstance(o, dict):
        return {k: deep_mask(v) for k, v in o.items()}
    if isinstance(o, list):
        return [deep_mask(v) for v in o]
    return mask(o)


def main(db, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    msgs = [deep_mask(dict(r)) for r in con.execute("SELECT * FROM messages ORDER BY id")]
    for m in msgs:
        m["provider_message_id"] = m.pop("telnyx_id")
    trail = [deep_mask(dict(r) | {"detail": json.loads(r["detail"])}) for r in con.execute("SELECT * FROM trail ORDER BY id")]
    contacts = [deep_mask(dict(r)) for r in con.execute("SELECT * FROM contacts")]
    (out / "messages.json").write_text(json.dumps(msgs, indent=2))
    (out / "trail.json").write_text(json.dumps(trail, indent=2))
    (out / "contacts.json").write_text(json.dumps(contacts, indent=2))
    sid, tok = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    if sid and tok:
        r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json?PageSize=50", auth=(sid, tok))
        r.raise_for_status()
        keep = ("sid", "direction", "from", "to", "status", "error_code", "num_segments", "price", "date_sent", "body")
        log = [deep_mask({k: m.get(k) for k in keep}) for m in r.json()["messages"]]
        (out / "twilio-message-log.json").write_text(json.dumps(log, indent=2))
    text = json.dumps([msgs, trail, contacts])
    assert not re.search(r"AC[0-9a-f]{32}", text), "account SID in export"
    print(f"{len(msgs)} messages, {len(trail)} trail events -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
