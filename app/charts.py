"""Charts and the sensor tile map. Status colours always come with a glyph + word, never colour alone."""
from __future__ import annotations

from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from kbarn_health.checks import LABEL, HealthReport, dur, when

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#1a1a19", "#6b6b66", "#e6e6e1"
BLUE, ORANGE = "#2a78d6", "#d9730d"            # lower / upper level
TILE = {   # background, border, glyph
    "ok": ("#e3f4e3", "#0ca30c", "✓"),
    "info": ("#e0ecfb", "#2a78d6", "ℹ"),
    "warning": ("#fff0c9", "#c98a00", "⚠"),
    "critical": ("#f8d9d9", "#d03b3b", "✕"),
}


def _style(fig: go.Figure, height: int, **kw) -> go.Figure:
    kw.setdefault("margin", dict(l=10, r=10, t=40, b=10))
    fig.update_layout(template="plotly_white", paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, height=height,
                      font=dict(color=INK, size=12), **kw)
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# ----------------------------------------------------------------------------- sensor tile map
def sensor_grid_html(rep: HealthReport) -> str:
    s = rep.settings
    zones = rep.zones.set_index(["pen", "level", "zone"])
    maxz = max([s.zones_per_level.get(l, 0) for l in s.levels] + [1])
    rows = []
    for p in s.pens:
        for l in s.levels:
            cells = []
            for z in range(1, maxz + 1):
                if z > s.zones_per_level.get(l, 0):
                    cells.append("<div class='tile none'></div>")
                    continue
                r = zones.loc[(p, l, z)] if (p, l, z) in zones.index else None
                if r is None:
                    cells.append("<div class='tile none'>?</div>")
                    continue
                bg, bd, glyph = TILE[r["status"]]
                ago = dur(r["hours_since"]) if pd.notna(r["hours_since"]) else "never"
                tip = (f"Pen {p} {l}, zone {z}\n{LABEL[r['status']]}\nReads: {int(r['reads']):,}\nLast read: {when(r['last_read'])} ({ago} ago)"
                       + (f"\nvs same antenna in other pens: {r['vs_other_pens']:.0%}" if pd.notna(r["vs_other_pens"]) else ""))
                cells.append(f"<div class='tile' style='background:{bg};border-color:{bd}' title='{escape(tip, quote=True).replace(chr(10), '&#10;')}'>"
                             f"<div class='g'>{glyph} <b>{z}</b></div><div class='s'>{escape(ago)}</div>"
                             f"<div class='n'>{int(r['reads']):,}</div></div>")
            rows.append(f"<div class='lab'>Pen {p}<br><span>{l}</span></div>" + "".join(cells))
    css = f"""<style>
    .sg{{display:grid;grid-template-columns:78px repeat({maxz},minmax(64px,1fr));gap:6px;align-items:stretch}}
    .lab{{font-weight:600;align-self:center;line-height:1.1}} .lab span{{font-weight:400;opacity:.7;font-size:.85em}}
    .tile{{border:2px solid;border-radius:8px;padding:4px 6px;color:{INK};min-height:62px}}
    .tile.none{{border:1px dashed #9996;background:transparent;min-height:62px}}
    .tile .g{{font-size:1.05em}} .tile .s{{font-size:.78em}} .tile .n{{font-size:.72em;opacity:.65}}
    .leg span{{display:inline-block;margin-right:14px}}
    </style>"""
    legend = "<div class='leg' style='margin:8px 0 12px'>" + "".join(
        f"<span><span style='display:inline-block;width:14px;height:14px;border-radius:4px;background:{TILE[k][0]};border:2px solid {TILE[k][1]};vertical-align:-2px'></span> {TILE[k][2]} {LABEL[k]}</span>"
        for k in ("ok", "info", "warning", "critical")) + "<span style='opacity:.7'>Each tile: zone number · time since its last read · total reads</span></div>"
    return css + legend + "<div class='sg'>" + "".join(rows) + "</div>"


# ----------------------------------------------------------------------------- time axis helpers
def _night_bands(start, end, s):
    bands = []
    d = start.normalize() - pd.Timedelta(days=1)
    while d <= end:
        a = d + pd.Timedelta(hours=s.lights_off_hour)
        b = d + pd.Timedelta(days=1, hours=s.lights_on_hour) if s.lights_off_hour > s.lights_on_hour else d + pd.Timedelta(hours=s.lights_on_hour)
        if b > start and a < end:
            bands.append((max(a, start), min(b, end)))
        d += pd.Timedelta(days=1)
    return bands


# ----------------------------------------------------------------------------- who was read when
def mouse_heatmap(reads: pd.DataFrame, rep: HealthReport) -> go.Figure:
    s, v = rep.settings, reads[reads["valid"] & reads["pen"].notna()]
    m = rep.mice[rep.mice["expected"] | (rep.mice["reads_total"] > 0)].copy()
    m = m.assign(_pen=m["current_pen"].astype("float").fillna(99)).sort_values(
        ["_pen", "tag_no"], key=lambda c: pd.to_numeric(c, errors="coerce") if c.name == "tag_no" else c)
    labels = [f"P{int(p) if pd.notna(p) else '?'} · {t}" + (f" {n}" if n else "") for p, t, n in zip(m["current_pen"], m["tag_no"], m["name"])]
    lab = dict(zip(m["tag_no"], labels))
    start, end = rep.data_start.floor("30min"), (rep.reference_time or rep.data_end).ceil("30min")
    bins = pd.date_range(start, end, freq="30min")
    v = v[v["tag_no"].isin(lab)].assign(bin=lambda d: d["ts"].dt.floor("30min"))
    piv = v.groupby(["tag_no", "bin"]).size().unstack(fill_value=0).reindex(index=m["tag_no"], columns=bins, fill_value=0)
    z = piv.to_numpy().astype(float)
    z[z == 0] = np.nan
    fig = go.Figure(go.Heatmap(z=z, x=bins, y=labels, colorscale=[[0, "#d3e3f8"], [1, "#0b3d82"]], zmin=1,
                               hoverongaps=False, xgap=1, ygap=1,
                               hovertemplate="%{y}<br>%{x|%b %d, %H:%M}<br><b>%{z:.0f} reads</b> in 30 min<extra></extra>",
                               colorbar=dict(title="reads / 30 min", thickness=12)))
    for a, b in _night_bands(start, end, s):
        fig.add_vrect(x0=a, x1=b, fillcolor="#000", opacity=0.06, line_width=0, layer="below")
    fig.update_yaxes(autorange="reversed", type="category")
    fig.update_xaxes(range=[start, end])
    return _style(fig, max(320, 16 * len(m) + 90), title=dict(text="Every mouse over time - blank = not read (shaded = lights off)", font=dict(size=14)))


def activity_lines(reads: pd.DataFrame, rep: HealthReport) -> go.Figure:
    s, v = rep.settings, reads[reads["ts"].notna() & reads["pen"].notna()]
    start, end = rep.data_start.floor("10min"), (rep.reference_time or rep.data_end).ceil("10min")
    bins = pd.date_range(start, end, freq="10min")
    if len(bins) > 1 and bins[-1] + pd.Timedelta(minutes=10) > end + pd.Timedelta(minutes=5):
        bins = bins[:-1]   # the last bin is only partly covered and would look like a drop to zero
    v = v.assign(bin=v["ts"].dt.floor("10min"))
    fig = make_subplots(rows=len(s.pens), cols=len(s.levels), shared_xaxes=True, vertical_spacing=0.05, horizontal_spacing=0.05,
                        subplot_titles=[f"Pen {p} · {l}" for p in s.pens for l in s.levels])
    for i, p in enumerate(s.pens, 1):
        for j, l in enumerate(s.levels, 1):
            c = v[(v["pen"] == p) & (v["level"] == l)].groupby("bin").size().reindex(bins, fill_value=0)
            fig.add_trace(go.Scatter(x=c.index, y=c.values, mode="lines", line=dict(color=BLUE if l == "lower" else ORANGE, width=1.5, shape="hv"),
                                     hovertemplate="%{x|%b %d, %H:%M}<br><b>%{y} reads</b> / 10 min<extra></extra>", showlegend=False), row=i, col=j)
    for a, b in _night_bands(start, end, s):
        fig.add_vrect(x0=a, x1=b, fillcolor="#000", opacity=0.06, line_width=0, layer="below", row="all", col="all")
    fig.update_annotations(font=dict(size=12, color=INK))
    return _style(fig, 150 * len(s.pens) + 50, title=dict(text="Reads per 10 minutes - a logger/reader failure shows up as a flat line at zero", font=dict(size=14)))


def mouse_track(reads: pd.DataFrame, tag: str, rep: HealthReport) -> go.Figure:
    s = rep.settings
    g = reads[reads["valid"] & (reads["tag_no"] == tag)].copy()
    g["loc"] = [f"Pen {p} {l} · zone {z}" for p, l, z in zip(g["pen"], g["level"], g["zone"])]
    order = sorted(g["loc"].unique(), key=lambda x: (x.split(" · ")[0], int(x.split("zone ")[1])))
    fig = go.Figure()
    for l, col in (("lower", BLUE), ("upper", ORANGE)):
        d = g[g["level"] == l]
        if len(d):
            fig.add_trace(go.Scattergl(x=d["ts"], y=d["loc"], mode="markers", name=f"{l} level", marker=dict(size=5, color=col, opacity=.75),
                                       hovertemplate="%{y}<br>%{x|%b %d, %H:%M:%S}<extra></extra>"))
    if len(g):
        for a, b in _night_bands(rep.data_start, rep.reference_time or rep.data_end, s):
            fig.add_vrect(x0=a, x1=b, fillcolor="#000", opacity=0.06, line_width=0, layer="below")
    fig.update_yaxes(categoryorder="array", categoryarray=order[::-1], type="category")
    return _style(fig, max(260, 34 * len(order) + 110), legend=dict(orientation="h", y=1.0, yanchor="bottom", x=1, xanchor="right"), margin=dict(l=10, r=10, t=70, b=10),
                  title=dict(text=f"Mouse {tag}: where it was read (shaded = lights off)", font=dict(size=14)))
