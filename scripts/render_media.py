"""Render the README images headless with Playwright, from the recorded local run in
examples/local-run/ (real Claude replies, mock Telnyx).

    python scripts/render_media.py   (needs: pip install playwright && playwright install chromium)
"""
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "examples" / "local-run"
MEDIA = ROOT / "media"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'SF Mono', Menlo, Consolas, monospace"
STYLE = f"""<style>
 body{{margin:0;background:#0f1115;font-family:{FONT};color:#e8e8e8}}
 .wrap{{display:inline-block;width:1060px;padding:26px 30px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#9aa3ad;font-size:14px;margin-bottom:14px;line-height:1.4}}
 .cols{{display:flex;gap:26px;align-items:flex-start}}
 .phone{{width:430px;background:#1a1d24;border-radius:26px;padding:18px 16px;border:1px solid #2b303b}}
 .bar{{text-align:center;color:#9aa3ad;font-size:13px;margin-bottom:10px}}
 .b{{max-width:80%;padding:9px 13px;border-radius:18px;margin:7px 0;font-size:14.5px;line-height:1.35;clear:both}}
 .in{{background:#2c313c;float:left;border-bottom-left-radius:5px}}
 .out{{background:#2f7d5b;float:right;border-bottom-right-radius:5px}}
 .blocked{{background:none;border:1px dashed #c2564b;color:#e7a39b;float:right}}
 .tag{{display:block;font-size:10.5px;color:#cfe;opacity:.75;margin-top:3px;font-family:{MONO}}}
 .in .tag{{color:#aab}}
 .clear{{clear:both}}
 .side{{flex:1}} .ev{{background:#171a21;border-left:4px solid #e0a43a;border-radius:8px;padding:8px 12px;margin:7px 0;
   font-size:13px;line-height:1.4}} .ev b{{color:#ffd58a}} .ok{{border-left-color:#3ecf8e}} .ok b{{color:#7fe3b5}}
 .bad{{border-left-color:#c2564b}} .bad b{{color:#f29b90}}
 .mono{{font-family:{MONO};font-size:12px;color:#c9d1d9}}
 .stat{{display:inline-block;background:#171a21;border-radius:8px;padding:8px 12px;margin:0 8px 8px 0;font-size:13px}}
 .stat b{{color:#3ecf8e;font-size:16px}}
</style>"""


def bubble(m: dict) -> str:
    text = html.escape(m["text"])
    if m["direction"] == "inbound":
        return f"<div class='b in'>{text}<span class='tag'>customer</span></div>"
    if m["status"] == "blocked":
        return (f"<div class='b blocked'>{text}<span class='tag'>NOT SENT: {html.escape(m['reason'])} "
                f"({html.escape(m['kind'])})</span></div>")
    who = "Claude" if m["kind"] == "ai" else m["kind"].replace("template:", "template ")
    return (f"<div class='b out'>{text}<span class='tag'>{html.escape(who)} &middot; {m['status']} &middot; "
            f"{m['segments']} seg</span></div>")


def page(body: str) -> str:
    return f"<html><head><meta charset='utf-8'>{STYLE}</head><body><div class='wrap'>{body}</div></body></html>"


def conversation(msgs, summary) -> str:
    half = len(msgs) // 2 + 1
    first, second = msgs[:half], msgs[half:]
    phone = lambda ms, title: (f"<div class='phone'><div class='bar'>{title}</div>"  # noqa: E731
                               + "".join(bubble(m) for m in ms) + "<div class='clear'></div></div>")
    return page(
        "<h1>Two-way SMS with Claude replies, on Telnyx's API shape</h1>"
        f"<div class='sub'>A recorded local run, {summary['date'][:10]}: this app and a faithful Telnyx mock as two real "
        f"HTTP servers with signed webhooks. Replies written live by {summary['model']}. Telnyx itself was not "
        "used (no account yet), so no real phone received these.</div>"
        f"<div class='cols'>{phone(first, 'Brightside Dental (demo) &middot; part 1')}"
        f"{phone(second, 'part 2')}</div>")


def guards(summary, trail) -> str:
    ev = {e["event"]: e for e in trail}
    redacted = next(e for e in trail if e["event"] == "ai_reply" and e["detail"]["redacted"])
    stats = "".join(f"<span class='stat'>{k}<br><b>{v}</b></span>" for k, v in [
        ("texts accepted by Telnyx (mock)", summary["telnyx_messages_accepted"]),
        ("signed webhooks, HTTP 200", summary["webhooks_by_http_status"].get("200", 0)),
        ("duplicate deliveries ignored", summary["duplicate_deliveries_ignored"]),
        ("held back by a guard", summary["outbound_by_status"].get("blocked", 0)),
        ("Claude tokens in / out", f"{summary['claude_tokens_in_out'][0]} / {summary['claude_tokens_in_out'][1]}"),
    ])
    rows = [
        ("ok", "Every webhook signed and checked",
         "ed25519 over <span class='mono'>timestamp|raw body</span>, Telnyx's scheme; stale or forged: 401"),
        ("bad", f"Unsigned and forged webhooks: HTTP {summary['forged_webhook_status'][0]} and "
                f"{summary['forged_webhook_status'][1]}",
         html.escape(", ".join(e["detail"]["reason"] for e in trail if e["event"] == "signature_rejected"))),
        ("ok", "Card number removed before Claude saw it",
         f"redacted={redacted['detail']['redacted']}, handover={redacted['detail']['handover']}: "
         + html.escape(redacted["detail"]["reason"])),
        ("", "Handed to a person", "the assistant stayed quiet on the next text until staff released it"),
        ("", "Quiet hours", "a reminder at 22:00 New York time: <span class='mono'>blocked (quiet_hours)</span>"),
        ("bad", "STOP", "opted out, one confirmation sent, the next follow-up "
                        "<span class='mono'>blocked (opted_out)</span> before reaching Telnyx"),
        ("ok", "Webhook retries", f"{summary['duplicate_deliveries_ignored']} repeat deliveries "
                                  "(same event id) processed once"),
    ]
    assert "duplicate_webhook" in ev and "handover" in ev
    body = "".join(f"<div class='ev {c}'><b>{html.escape(t)}</b><br>{d}</div>" for c, t, d in rows)
    return page("<h1>What the guards did in the same run</h1>"
                "<div class='sub'>From the run's own trail (examples/local-run/trail.json). Phone numbers are masked "
                "in the trail and logs.</div>" + stats + body)


def shoot(pw, markup: str, name: str) -> None:
    browser = pw.chromium.launch()
    p = browser.new_page(viewport={"width": 1120, "height": 600}, device_scale_factor=2)
    p.set_content(markup)
    p.locator(".wrap").screenshot(path=str(MEDIA / name))
    browser.close()


def main():
    MEDIA.mkdir(exist_ok=True)
    msgs = json.loads((RUN / "conversation.json").read_text())
    summary = json.loads((RUN / "summary.json").read_text())
    trail = json.loads((RUN / "trail.json").read_text())
    with sync_playwright() as pw:
        shoot(pw, conversation(msgs, summary), "local-run-conversation.png")
        shoot(pw, guards(summary, trail), "local-run-guards.png")
    print("wrote", [p.name for p in MEDIA.glob("*.png")])


if __name__ == "__main__":
    main()
