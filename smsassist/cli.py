"""Command line: python -m smsassist <command>

  send --to +1555... --text "..."                 send one text (all the guards apply)
  send --to +1555... --template appointment_reminder --var name=Sam --var "when=Tue 3pm"
  segments "some text"                             how many segments and which encoding
  log [--phone +1555...] [--limit 20]              messages with their status
  trail [--limit 30]                               what the system did and why
  contacts                                         opt-out and handover state
  opt-out +1555... | opt-in +1555...               change opt-out by hand
  release +1555...                                 hand a conversation back to the assistant
  templates                                        list the templates
"""
import argparse
import json
import sys

from .app import build_messenger
from .config import Settings, normalise_number
from .segments import encoding, segment_count, units


def _vars(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--var needs key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="smsassist", description="Two-way SMS on Telnyx with Claude replies")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--to", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--template")
    s.add_argument("--var", action="append")
    seg = sub.add_parser("segments")
    seg.add_argument("text")
    lg = sub.add_parser("log")
    lg.add_argument("--phone")
    lg.add_argument("--limit", type=int, default=20)
    tr = sub.add_parser("trail")
    tr.add_argument("--limit", type=int, default=30)
    sub.add_parser("contacts")
    sub.add_parser("templates")
    for name in ("opt-out", "opt-in", "release"):
        sub.add_parser(name).add_argument("phone")
    a = ap.parse_args(argv)

    if a.cmd == "segments":
        print(json.dumps({"encoding": encoding(a.text), "length": units(a.text), "segments": segment_count(a.text)}))
        return 0

    settings = Settings.from_env()
    settings.ai_enabled = False  # the CLI never generates replies
    m = build_messenger(settings)
    if a.cmd == "send":
        try:
            r = m.send_template(a.to, a.template, _vars(a.var)) if a.template else m.send(a.to, a.text)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(r))
        return 0 if r["status"] not in ("blocked", "failed") else 1
    if a.cmd == "log":
        phone = normalise_number(a.phone) if a.phone else None
        for row in reversed(m.store.messages(phone, a.limit)):
            arrow = "->" if row["direction"] == "outbound" else "<-"
            extra = f" ({row['reason']})" if row["reason"] else ""
            print(f"{row['created_at']} {arrow} {row['phone']} [{row['status']}{extra}] {row['kind']}: {row['text']}")
    elif a.cmd == "trail":
        for t in m.store.trail(a.limit):
            print(f"{t['at']} {t['event']} {json.dumps(t['detail'], sort_keys=True)}")
    elif a.cmd == "contacts":
        for c in m.store.contacts():
            print(f"{c['phone']} opted_out={bool(c['opted_out'])} handover={bool(c['handover'])}")
    elif a.cmd == "templates":
        for k, v in sorted(m.templates.items()):
            print(f"{k}: {v}")
    elif a.cmd in ("opt-out", "opt-in"):
        m.store.set_opt_out(normalise_number(a.phone), a.cmd == "opt-out")
        m.store.log("manual_" + a.cmd.replace("-", "_"))
        print("ok")
    elif a.cmd == "release":
        m.release(a.phone)
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
