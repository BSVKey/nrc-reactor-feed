"""NRC daily power reactor status and event notification feed.

Parses two public NRC postings and builds a one-page dashboard:
  * Power Reactor Status for Last 365 Days (pipe-delimited ReportDt|Unit|Power)
  * Event Notification Report (daily HTML page)

Every source file gets a SHA-256 digest so a day's posting can be anchored as a
proof-of-publication receipt later. Standard library only.

nrc.gov sits behind a bot filter that often refuses scripts. When a fetch is
refused, open the URL in a browser, save the page, and pass --file.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

STATUS_URL = (
    "https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/"
    "power-reactor-status-reports/PowerReactorStatusForLast365Days.txt"
)
EVENTS_URL = (
    "https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/"
    "event-notification-reports/en"
)
USER_AGENT = "nrc-reactor-feed/0.1 (public data reader; embryospace@gmail.com)"


# ---------- loading ----------

def load_source(file: str | None, url: str) -> tuple[str, str]:
    """Return (text, origin). Reads --file if given, else fetches url once."""
    if file:
        # name only, so local folder paths never land in a published dashboard
        return Path(file).read_text(encoding="utf-8", errors="replace"), Path(file).name
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace"), url
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"nrc.gov refused the download (HTTP {e.code}).\n"
            f"Open this in your browser, save it (Ctrl+S), then rerun with --file <saved file>:\n  {url}"
        )


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- reactor status ----------

def parse_status(text: str) -> dict[str, dict[str, int]]:
    """{unit: {YYYY-MM-DD: power%}}"""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or parts[0] == "ReportDt":
            continue
        raw_date, unit, power = parts
        try:
            day = datetime.strptime(raw_date.split(" ")[0], "%m/%d/%Y").strftime("%Y-%m-%d")
            out[unit.strip()][day] = int(power.strip())
        except ValueError:
            continue
    return dict(out)


@dataclass
class UnitSummary:
    unit: str
    power: int
    previous: int | None
    change: int
    days_offline_365: int
    days_reduced_365: int
    avg_power_365: float
    offline_streak: int
    series: list[int] = field(repr=False)


def summarize_status(data: dict[str, dict[str, int]]) -> dict:
    days = sorted({d for s in data.values() for d in s})
    if not days:
        raise ValueError("no status rows found; is this the PowerReactorStatus file?")
    latest = days[-1]
    prev = days[-2] if len(days) > 1 else None
    units = []
    for unit in sorted(data):
        s = data[unit]
        series = [s.get(d) for d in days]
        known = [v for v in series if v is not None]
        power = s.get(latest, 0)
        previous = s.get(prev) if prev else None
        streak = 0
        for v in reversed(series):
            if v == 0:
                streak += 1
            else:
                break
        units.append(
            UnitSummary(
                unit=unit,
                power=power,
                previous=previous,
                change=power - previous if previous is not None else 0,
                days_offline_365=sum(1 for v in known if v == 0),
                days_reduced_365=sum(1 for v in known if 0 < v < 100),
                avg_power_365=round(sum(known) / len(known), 1) if known else 0.0,
                offline_streak=streak,
                series=[v if v is not None else -1 for v in series],
            )
        )
    online = [u for u in units if u.power > 0]
    return {
        "latest": latest,
        "previous": prev,
        "days": len(days),
        "units": len(units),
        "online": len(online),
        "offline": [u.unit for u in units if u.power == 0],
        "reduced": [(u.unit, u.power) for u in units if 0 < u.power < 100],
        "changes": [(u.unit, u.previous, u.power) for u in units if u.change != 0],
        "fleet_avg_power": round(sum(u.power for u in units) / len(units), 1),
        "unit_rows": units,
    }


# ---------- event notifications ----------

_FIELD_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9 #/().]*?):\s*(.*)$")


def html_to_text(raw: str) -> str:
    if "<" not in raw:
        return raw
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|td|th)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).replace("\xa0", " ")


@dataclass
class Event:
    number: str
    category: str
    fields: dict
    text: str

    @property
    def facility(self) -> str:
        return self.fields.get("Facility") or self.fields.get("Licensee") or self.fields.get("Rep Org", "")

    @property
    def state(self) -> str:
        m = re.search(r"State:\s*([A-Z]{2})", self.fields.get("City", ""))
        return m.group(1) if m else self.fields.get("State", "")


def parse_events(raw: str) -> list[Event]:
    text = html_to_text(raw)
    lines = [l.rstrip() for l in text.splitlines()]
    starts = [i for i, l in enumerate(lines) if l.strip().startswith("Event Number:")]
    events = []
    for n, i in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(lines)
        # category line ("Power Reactor", "Agreement State", ...) sits just above
        category = ""
        for j in range(i - 1, max(i - 4, -1), -1):
            if lines[j].strip():
                category = lines[j].strip()
                break
        block = lines[i:end]
        fields, body, in_text, pending = {}, [], False, None
        for l in block:
            s = l.strip()
            if s == "Event Text":
                in_text = True
                continue
            if in_text:
                if not s.startswith("Page Last Reviewed"):
                    body.append(l)
                continue
            m = _FIELD_RE.match(s)
            if m and m.group(1) not in fields:
                key, val = m.group(1).strip(), m.group(2).strip()
                fields[key] = val
                pending = key if not val else None
            elif pending and s:
                # value printed on the following line(s), e.g. "10 CFR Section:" or "Person (Organization):"
                fields[pending] = f"{fields[pending]}; {s}" if fields[pending] else s
        number = fields.get("Event Number", "").strip()
        # trailing category header of the next event leaks into the body; drop it
        while body and not body[-1].strip():
            body.pop()
        if n + 1 < len(starts) and body and body[-1].strip() == _category_above(lines, starts[n + 1]):
            body.pop()
        events.append(Event(number, category, fields, "\n".join(body).strip()))
    return events


def _category_above(lines: list[str], i: int) -> str:
    for j in range(i - 1, max(i - 4, -1), -1):
        if lines[j].strip():
            return lines[j].strip()
    return ""


# ---------- dashboard ----------

def sparkline(series: list[int], w: int = 180, h: int = 24) -> str:
    pts = [(i, v) for i, v in enumerate(series) if v >= 0]
    if len(pts) < 2:
        return ""
    n = len(series) - 1 or 1
    path = " ".join(f"{i * w / n:.1f},{h - v * h / 100:.1f}" for i, v in pts)
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
        f'<polyline points="{path}" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>'
    )


def render_dashboard(summary: dict | None, events: list[Event], receipts: dict) -> str:
    e = html.escape
    parts = []
    if summary:
        changes = "".join(
            f"<li><b>{e(u)}</b> {p if p is not None else '?'}% &rarr; {c}%</li>" for u, p, c in summary["changes"]
        ) or "<li>No changes since the previous report.</li>"
        rows = "".join(
            f"<tr class='{'off' if u.power == 0 else 'red' if u.power < 100 else ''}'>"
            f"<td>{e(u.unit)}</td><td class='num'>{u.power}%</td>"
            f"<td class='num'>{'+' if u.change > 0 else ''}{u.change if u.change else ''}</td>"
            f"<td class='num'>{u.offline_streak or ''}</td><td class='num'>{u.days_offline_365}</td>"
            f"<td class='num'>{u.avg_power_365}</td><td>{sparkline(u.series)}</td></tr>"
            for u in sorted(summary["unit_rows"], key=lambda u: (u.power, u.unit))
        )
        parts.append(f"""
<section>
  <div class="tiles">
    <div class="tile"><span>Report date</span><b>{e(summary['latest'])}</b></div>
    <div class="tile"><span>Units online</span><b>{summary['online']} / {summary['units']}</b></div>
    <div class="tile"><span>Offline</span><b>{len(summary['offline'])}</b></div>
    <div class="tile"><span>Below 100%</span><b>{len(summary['reduced'])}</b></div>
    <div class="tile"><span>Fleet average</span><b>{summary['fleet_avg_power']}%</b></div>
  </div>
  <h2>Changed since {e(summary['previous'] or '')}</h2>
  <ul class="changes">{changes}</ul>
  <h2>All units</h2>
  <p class="hint">Sorted lowest power first. "Off streak" is consecutive days at 0%. Line shows the last {summary['days']} days.</p>
  <div class="scroll"><table>
    <thead><tr><th>Unit</th><th>Power</th><th>Change</th><th>Off streak</th><th>Days off (yr)</th><th>Avg % (yr)</th><th>Trend</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</section>""")
    if events:
        cards = "".join(
            f"<details><summary><b>{e(ev.number)}</b> &middot; {e(ev.category)} &middot; {e(ev.facility)}"
            f"{' (' + e(ev.state) + ')' if ev.state else ''} &middot; {e(ev.fields.get('Event Date', ''))}"
            f" &middot; {e(ev.fields.get('Emergency Class', ''))}</summary>"
            f"<p class='meta'>10 CFR: {e(ev.fields.get('10 CFR Section', '') or 'n/a')}</p>"
            f"<pre>{e(ev.text[:4000])}</pre></details>"
            for ev in events
        )
        parts.append(f"<section><h2>Event notifications ({len(events)})</h2>{cards}</section>")
    rec = "".join(f"<li><code>{e(k)}</code><br><span class='hash'>{e(v)}</span></li>" for k, v in receipts.items())
    parts.append(
        "<section><h2>Source receipts</h2><p class='hint'>SHA-256 of each NRC file as read. "
        f"Anyone can re-hash the same posting and compare.</p><ul class='rec'>{rec}</ul></section>"
    )
    return PAGE.replace("{{BODY}}", "".join(parts)).replace(
        "{{STAMP}}", e(datetime.now().strftime("%Y-%m-%d %H:%M"))
    )


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NRC Reactor Feed</title>
<style>
:root{--bg:#fbfbfa;--fg:#1d1f21;--mute:#6b6f76;--line:#e3e4e6;--card:#fff;--off:#c0392b;--red:#b7791f;--accent:#2b6cb0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121416;--fg:#e8e9ea;--mute:#9aa0a6;--line:#2a2e33;--card:#1a1d21;--off:#ef6b5b;--red:#e0a948;--accent:#6aa9ff}}
:root[data-theme="dark"]{--bg:#121416;--fg:#e8e9ea;--mute:#9aa0a6;--line:#2a2e33;--card:#1a1d21;--off:#ef6b5b;--red:#e0a948;--accent:#6aa9ff}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.hint,.meta{color:var(--mute);font-size:13px;margin:4px 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile span{display:block;color:var(--mute);font-size:12px}.tile b{font-size:20px;font-variant-numeric:tabular-nums}
.changes{padding-left:18px}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;white-space:nowrap}
th{color:var(--mute);font-weight:600;font-size:12px}.num{text-align:right;font-variant-numeric:tabular-nums}
tr.off td:nth-child(2){color:var(--off);font-weight:600}tr.red td:nth-child(2){color:var(--red);font-weight:600}
.spark{color:var(--accent);display:block}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0}
summary{cursor:pointer}pre{white-space:pre-wrap;font:13px/1.45 ui-monospace,Consolas,monospace;margin:8px 0 0}
.rec{padding-left:18px;font-size:13px}.hash{font-family:ui-monospace,Consolas,monospace;color:var(--mute);word-break:break-all}
</style></head><body><main>
<h1>NRC Reactor Feed</h1>
<p class="hint">Built {{STAMP}} from public NRC postings: daily power reactor status and event notification reports.</p>
{{BODY}}
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a dashboard from NRC reactor status and event reports.")
    p.add_argument("--status-file", help="saved PowerReactorStatusForLast365Days.txt")
    p.add_argument("--events-file", help="saved Event Notification Report page (.html or .txt)")
    p.add_argument("--no-status", action="store_true")
    p.add_argument("--no-events", action="store_true")
    p.add_argument("--out", default="nrc-feed.html")
    p.add_argument("--json", help="also write a JSON summary here")
    a = p.parse_args(argv)

    receipts, summary, events = {}, None, []
    if not a.no_status:
        text, origin = load_source(a.status_file, STATUS_URL)
        receipts[origin] = digest(text)
        summary = summarize_status(parse_status(text))
    if not a.no_events:
        raw, origin = load_source(a.events_file, EVENTS_URL)
        receipts[origin] = digest(raw)
        events = parse_events(raw)

    Path(a.out).write_text(render_dashboard(summary, events, receipts), encoding="utf-8")
    if a.json:
        out = {"receipts": receipts, "events": [asdict(ev) for ev in events]}
        if summary:
            s = dict(summary)
            s["unit_rows"] = [{k: v for k, v in asdict(u).items() if k != "series"} for u in summary["unit_rows"]]
            out["status"] = s
        Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    if summary:
        print(f"{summary['latest']}: {summary['online']}/{summary['units']} units online, "
              f"{len(summary['changes'])} changed, fleet avg {summary['fleet_avg_power']}%")
    if not a.no_events:
        print(f"{len(events)} event notifications")
    print(f"wrote {Path(a.out).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
