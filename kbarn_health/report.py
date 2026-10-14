"""Self-contained HTML report - something the lab manager can email or print."""
from __future__ import annotations

from html import escape

import pandas as pd

from .checks import ICON, LABEL, HealthReport, dur, when

COLORS = {"critical": "#d03b3b", "warning": "#b36b00", "info": "#2a78d6", "ok": "#0b7a0b"}


def _table(df: pd.DataFrame) -> str:
    head = "".join(f"<th>{escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{escape('' if pd.isna(v) else str(v))}</td>" for v in r) + "</tr>"
                   for r in df.itertuples(index=False))
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def mice_view(rep: HealthReport) -> pd.DataFrame:
    m = rep.mice
    out = pd.DataFrame({
        "Status": [f"{ICON.get(s, '⚪')} {LABEL.get(s, 'Removed')}" for s in m["status"]],
        "Mouse": m["tag_no"], "Name": m["name"], "Allowed pen(s)": m["home_pen"], "Now in pen": m["current_pen"],
        "Last read": m["last_seen"].map(when), "Ago": m["hours_since"].map(dur),
        "Last place": m["last_location"], "At this spot for": m["at_spot_h"].map(dur),
        "Reads": m["reads_total"], "Longest quiet spell": m["longest_quiet_h"].map(dur),
    })
    return out


def zones_view(rep: HealthReport) -> pd.DataFrame:
    z = rep.zones[rep.zones["expected"] | (rep.zones["reads"] > 0)]
    return pd.DataFrame({
        "Status": [f"{ICON[s]} {LABEL[s]}" for s in z["status"]],
        "Location": [f"Pen {p} {l}, zone {int(zn)}" for p, l, zn in zip(z["pen"], z["level"], z["zone"])],
        "Reads": z["reads"], "Last read": z["last_read"].map(when), "Ago": z["hours_since"].map(dur),
        "Share of level (%)": z["share_pct"].round(1), "vs same antenna in other pens": z["vs_other_pens"].map(
            lambda v: "" if pd.isna(v) else f"{v:.0%}"),
    })


def _note(acks, key) -> str:
    """Note text for a hidden issue. `acks` may be a dict of notes or just a collection of keys - never crash on either."""
    entry = acks.get(key) if isinstance(acks, dict) else None
    return str(entry.get("note", "")) if isinstance(entry, dict) else ""


def to_html(rep: HealthReport, acks=None) -> str:
    acks = acks if acks is not None else {}
    sev = rep.by_severity(acks)
    nc, nw = len(sev["critical"]), len(sev["warning"])
    if nc:
        banner, color = f"{nc} problem(s) need attention now", COLORS["critical"]
    elif nw:
        banner, color = f"{nw} thing(s) to check", COLORS["warning"]
    else:
        banner, color = "All clear", COLORS["ok"]
    parts = [f"<h1>KBarn Health Check</h1><p>Data through <b>{when(rep.reference_time)}</b> "
             f"(first read {when(rep.data_start)}). Files: {rep.summary.get('files', 0)}.</p>",
             f"<div class='banner' style='background:{color}'>{escape(banner)}</div>"]
    for level, title in (("critical", "Problems"), ("warning", "To check"), ("info", "Notes")):
        if not sev[level]:
            continue
        parts.append(f"<h2>{ICON[level]} {title}</h2>")
        for f in sev[level]:
            acts = "".join(f"<li>{escape(a)}</li>" for a in f.actions)
            ev = "".join(f"<li><b>{escape(str(k))}:</b> {escape(str(v))}</li>" for k, v in f.evidence.items())
            parts.append(f"<div class='card' style='border-left-color:{COLORS[level]}'><b>[{f.category}] {escape(f.title)}</b>"
                         f"<p>{escape(f.detail)}</p>" + (f"<p><b>What to do</b></p><ul>{acts}</ul>" if acts else "")
                         + (f"<ul class='ev'>{ev}</ul>" if ev else "") + "</div>")
    known = [f for f in rep.findings if f.key in acks]
    if known:
        parts.append("<h2>Known issues (acknowledged)</h2><ul>" + "".join(
            f"<li>{escape(f.title)} - <i>{escape(_note(acks, f.key))}</i></li>" for f in known) + "</ul>")
    parts += ["<h2>Mice</h2>", _table(mice_view(rep)), "<h2>Antennas (zones)</h2>", _table(zones_view(rep))]
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}"
           ".banner{color:#fff;font-size:22px;font-weight:600;padding:14px 18px;border-radius:8px;margin:12px 0}"
           ".card{border:1px solid #ddd;border-left:6px solid;border-radius:6px;padding:8px 14px;margin:10px 0}"
           ".ev{color:#555;font-size:13px}table{border-collapse:collapse;font-size:13px;width:100%}"
           "th,td{border:1px solid #ddd;padding:4px 8px;text-align:left}th{background:#f3f3f3}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>KBarn Health Check</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"
