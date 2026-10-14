"""Synthetic colony generator with switchable faults - lets us prove each detector fires (and stays quiet when healthy)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2026-09-28 19:00:00")
END = pd.Timestamp("2026-09-30 19:00:00")        # = export time
ZONES = {"lower": 9, "upper": 4}
FMT = "%Y-%m-%d_%H:%M:%S.%f"


def uid_for(tag: int) -> str:
    return f"000C12{tag:06X}"


def make_colony(out: Path, faults=(), seed=1, mice_per_pen=8, rate_per_h=60.0) -> Path:
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    rows = []   # (pen, level, zone, tag, ts)
    hours = (END - START).total_seconds() / 3600
    for pen in (1, 2, 3, 4):
        for k in range(mice_per_pen):
            tag = pen * 100 + k
            n = rng.poisson(rate_per_h * hours)
            offs = np.sort(rng.uniform(0, hours, n))
            ts = START + pd.to_timedelta(offs, unit="h")
            locs = [(rng.choice(["lower", "upper"]), int(rng.integers(1, 4)))]
            for _ in range(n - 1):
                if rng.random() < 0.7:
                    locs.append(locs[-1])
                else:
                    lv = "lower" if rng.random() < 0.55 else "upper"
                    locs.append((lv, int(rng.integers(1, ZONES[lv] + 1))))
            df = pd.DataFrame({"pen": pen, "tag": tag, "ts": ts, "level": [l for l, _ in locs], "zone": [z for _, z in locs]})
            if "missing_mouse" in faults and tag == 105:
                df = df[df["ts"] < END - pd.Timedelta(hours=10)]
            if "stuck_mouse" in faults and tag == 106:
                cut = END - pd.Timedelta(hours=9)
                df = df[df["ts"] < cut]
                st = pd.date_range(cut, END - pd.Timedelta(minutes=2), freq="40s")
                df = pd.concat([df, pd.DataFrame({"pen": pen, "tag": tag, "ts": st, "level": "lower", "zone": 2})])
            if "never_seen" in faults and tag == 107:
                continue
            rows.append(df)
    d = pd.concat(rows, ignore_index=True)
    if "dead_zone" in faults:
        d = d[~((d.pen == 2) & (d.level == "upper") & (d.zone == 3))]
    if "weak_zone" in faults:
        m = (d.pen == 3) & (d.level == "lower") & (d.zone == 5)
        d = d[~(m & (rng.random(len(d)) < 0.97))]
    if "logger_stopped" in faults:
        d = d[~((d.pen == 3) & (d.level == "lower") & (d.ts > END - pd.Timedelta(hours=4)))]
    if "pen_outage" in faults:
        t0 = START + pd.Timedelta(hours=20)
        d = d[~((d.pen == 1) & (d.ts > t0) & (d.ts < t0 + pd.Timedelta(hours=2)))]
    if "moved_mouse" in faults:      # mouse 401 is carried from pen 4 to pen 2 for the last 24 h (no overlap in time)
        d.loc[(d.tag == 401) & (d.ts > END - pd.Timedelta(hours=24)), "pen"] = 2
    if "multipen" in faults:
        mine = d[d.tag == 401].copy()
        mine["pen"] = 2
        d = pd.concat([d, mine.sample(frac=0.2, random_state=1)])
    d = d.sort_values("ts")
    for (pen, level), g in d.groupby(["pen", "level"]):
        txt = pd.DataFrame({"zone_label": g["zone"].astype(str), "uid": g["tag"].map(uid_for), "tag_no": g["tag"].astype(str),
                            "timestamp": g["ts"].dt.strftime(FMT).str[:-3]})
        if "noise" in faults and pen == 4 and level == "upper":
            extra = pd.DataFrame({"zone_label": "2", "uid": "F000000000F0", "tag_no": "",
                                  "timestamp": pd.date_range(START, END, periods=int(len(txt) * 0.08)).strftime(FMT).str[:-3]})
            txt = pd.concat([txt, extra]).sort_values("timestamp")
        if "clock_back" in faults and pen == 2 and level == "lower":
            txt = pd.concat([txt.iloc[:300], txt.iloc[100:200], txt.iloc[300:]])  # time jumps backwards at row 300
        name = f"Pen{pen}-{level}-{END:%Y%m%d-%H%M%S}-rfid.csv"
        if "missing_file" in faults and pen == 4 and level == "lower":
            continue
        txt.to_csv(out / name, index=False)
    return out
