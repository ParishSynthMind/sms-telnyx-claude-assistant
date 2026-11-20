"""Render the Twilio live-run images (README + portfolio) headless from examples/live-run/.

    python scripts/render_live_media.py   (needs playwright + chromium)
"""
import html
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from render_media import STYLE  # noqa: E402

RUN = ROOT / "examples" / "live-run"
MEDIA = ROOT / "media"
EXTRA = """<style>
 .thumb{width:1200px;height:900px;box-sizing:border-box;padding:70px 80px;background:
   radial-gradient(circle at 85% 15%,#f22f46 0,#f22f4600 38%),linear-gradient(135deg,#0f1115 0,#1b1f2a 100%);
   display:flex;flex-direction:column;justify-content:space-between}
 .thumb h1{font-size:64px;line-height:1.08;margin:0} .thumb .k{font-size:28px;color:#c9d1d9;margin-top:22px;line-height:1.4}
 .pill{display:inline-block;border:2px solid #3ecf8e;color:#7fe3b5;border-radius:40px;padding:10px 22px;margin:0 12px 12px 0;
   font-size:24px} .pill.red{border-color:#f22f46;color:#ff8a98}
 .bub{background:#2c313c;border-radius:22px;padding:16px 22px;font-size:26px;max-width:640px;margin:10px 0}
 .bub.o{background:#2f7d5b;margin-left:auto}
</style>"""


def page(body, cls="wrap"):
    return f"<html><head><meta charset='utf-8'>{STYLE}{EXTRA}</head><body><div class='{cls}'>{body}</div></body></html>"


def shoot(pw, markup, name, sel=".wrap", vw=1120):
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": vw, "height": 900}, device_scale_factor=1 if sel == ".thumb" else 2)
    p.set_content(markup)
    p.locator(sel).screenshot(path=str(MEDIA / name))
    b.close()


def thumb(s):
    pills = "".join(f"<span class='pill{c}'>{t}</span>" for t, c in [
        ("Twilio, live", " red"), ("signed webhooks", ""), ("STOP / HELP", ""), ("Claude replies", ""),
        ("quiet hours + spend cap", ""), ("Telnyx too", "")])
    return page("<div><h1>Two-way business SMS<br>on Twilio, with Claude</h1>"
                "<div class='k'>Texts in, AI answers out, opt-outs honoured.<br>"
                f"Live-tested on a Twilio trial, {s['date']}.</div></div>"
                f"<div><div class='bub'>{html.escape(s['thumb_q'])}</div>"
                f"<div class='bub o'>{html.escape(s['thumb_a'])}</div>"
                f"</div><div>{pills}</div>", "thumb")


def phone_and_trail(s, msgs, trail):
    rows = []
    for m in msgs:
        if m["direction"] == "inbound":
            rows.append(f"<div class='b in'>{html.escape(m['text'])}<span class='tag'>from the real phone</span></div>")
        else:
            meant = html.escape(m["text"])
            got = html.escape(s["trial_template_text"]) if m["status"] not in ("blocked", "failed") else ""
            tag = f"{m['kind']} &middot; {m['status']}" + (f" &middot; {html.escape(m['reason'] or '')}" if m["reason"] else "")
            cls = "blocked" if m["status"] in ("blocked", "failed") else "out"
            extra = f"<span class='tag'>phone showed (trial): {got}</span>" if got else ""
            rows.append(f"<div class='b {cls}'>{meant}<span class='tag'>{tag}</span>{extra}</div>")
    ev = "".join(f"<div class='ev {'bad' if 'reject' in t['event'] or 'fail' in t['event'] else 'ok'}'><b>"
                 f"{html.escape(t['event'])}</b><br><span class='mono'>{html.escape(json.dumps(t['detail'])[:150])}"
                 "</span></div>" for t in trail if t["event"] not in ("duplicate_webhook",))
    return page("<h1>Live on Twilio: a real phone, real webhooks</h1>"
                f"<div class='sub'>{s['date']}, Twilio trial account, UK number. Green bubbles are what the app sent "
                "(the trail keeps its own words); a trial only lets Twilio deliver its template text, shown under "
                "each. Numbers masked.</div>"
                f"<div class='cols'><div class='phone'><div class='bar'>Brightside Dental (demo)</div>{''.join(rows)}"
                f"<div class='clear'></div></div><div class='side'>{ev}</div></div>")


def main():
    MEDIA.mkdir(exist_ok=True)
    s = json.loads((RUN / "summary.json").read_text())
    msgs = json.loads((RUN / "messages.json").read_text())
    trail = json.loads((RUN / "trail.json").read_text())
    with sync_playwright() as pw:
        shoot(pw, thumb(s), "thumbnail-4x3.png", ".thumb", 1200)
        shoot(pw, phone_and_trail(s, msgs, trail), "live-run-twilio.png")
    print("wrote thumbnail-4x3.png, live-run-twilio.png")


if __name__ == "__main__":
    main()
