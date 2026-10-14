"""The health checks. Every finding says WHAT is wrong, the evidence, and WHAT TO DO about it."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .loader import LoadResult
from .roster import clean_roster, parse_pens, pens_text, suggest_from_data
from .settings import Settings

RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}
ICON = {"ok": "🟢", "info": "🔵", "warning": "🟠", "critical": "🔴"}
LABEL = {"ok": "OK", "info": "Note", "warning": "Check", "critical": "Problem"}


# ----------------------------------------------------------------------------- helpers
def dur(hours) -> str:
    """Human duration from hours: '45 min', '2 h 18 min', '3.2 days'."""
    if hours is None or pd.isna(hours):
        return "-"
    m = int(round(float(hours) * 60))
    if m < 60:
        return f"{m} min"
    if m >= 48 * 60:
        return f"{m / 1440:.1f} days"
    h, mm = divmod(m, 60)
    return f"{h} h {mm:02d} min" if mm else f"{h} h"


def when(ts) -> str:
    return "-" if ts is None or pd.isna(ts) else f"{pd.Timestamp(ts):%b %d, %H:%M}"


def where(pen, level, zone=None) -> str:
    s = f"Pen {pen} {level}"
    return s if zone is None or pd.isna(zone) else f"{s}, zone {int(zone)}"


@dataclass
class Finding:
    key: str                      # stable id - used to remember "known issue" acknowledgements
    severity: str                 # critical | warning | info
    category: str                 # Mouse | Sensor | Logger | Data
    title: str
    detail: str
    actions: list = field(default_factory=list)
    target: tuple = ()            # ("mouse", tag) | ("zone", pen, level, zone) | ("level", pen, level) | ...
    evidence: dict = field(default_factory=dict)


@dataclass
class HealthReport:
    findings: list
    mice: pd.DataFrame
    zones: pd.DataFrame
    levels: pd.DataFrame
    files: pd.DataFrame
    reference_time: pd.Timestamp | None
    data_start: pd.Timestamp | None
    data_end: pd.Timestamp | None
    roster_learned: bool
    summary: dict
    settings: Settings

    def by_severity(self, acked=()):
        live = [f for f in self.findings if f.key not in acked]
        return {s: [f for f in live if f.severity == s] for s in ("critical", "warning", "info")}


def _worst(findings, target) -> str:
    sev = "ok"
    for f in findings:
        if f.target == target and RANK[f.severity] > RANK[sev]:
            sev = f.severity
    return sev


# ----------------------------------------------------------------------------- main entry
def analyze(load: LoadResult, settings: Settings, roster: pd.DataFrame | None = None,
            clock_now: pd.Timestamp | None = None) -> HealthReport:
    now = pd.Timestamp(clock_now) if clock_now is not None else pd.Timestamp.now()
    reads, files = load.reads, load.files
    F: list[Finding] = []
    s = settings

    timed = reads[reads["ts"].notna()] if len(reads) else reads
    valid = reads[reads["valid"]] if len(reads) else reads
    data_start = timed["ts"].min() if len(timed) else None
    data_end = timed["ts"].max() if len(timed) else None

    exports = files["export_time"].dropna() if len(files) else pd.Series(dtype="datetime64[ns]")
    if s.reference_time == "clock" or (not len(exports) and data_end is None):
        ref = now
    elif len(exports):
        ref = max(exports.max(), data_end) if data_end is not None else exports.max()
    else:
        ref = data_end

    # ---------------- nothing readable at all
    if len(valid) == 0:
        why = "; ".join(f"{f}: {m}" for f, m in load.problems) or "no readable rows were found"
        F.append(Finding("data_empty", "critical", "Data", "No usable RFID reads were found",
                         f"The check could not find any valid reads ({why}).",
                         ["Make sure you picked the folder that contains the exported .csv files.",
                          "Open one of the files in Excel - it should have the columns zone_label, uid, tag_no, timestamp.",
                          "If the files are empty, check that the KBarn logger was running and re-export."],
                         ("system",)))
        return HealthReport(F, _empty_mice(), _empty_zones(), _empty_levels(), files, ref, data_start, data_end,
                            False, {}, s)

    roster_given = roster is not None and len(roster) > 0
    roster = clean_roster(roster) if roster_given else suggest_from_data(reads, s.min_reads_real_mouse)

    _check_files(F, files, reads, load, s, now, ref)
    levels, level_bad = _check_loggers(F, files, reads, s, ref)
    zones = _check_sensors(F, reads, valid, s, ref, level_bad)
    mice = _check_mice(F, reads, valid, roster, roster_given, s, ref)

    # ---------------- statuses back into the tables
    mice["status"] = [_worst(F, ("mouse", t)) if e else "removed" for t, e in zip(mice["tag_no"], mice["expected"])]
    zones["status"] = [_worst(F, ("zone", p, l, z)) for p, l, z in zip(zones["pen"], zones["level"], zones["zone"])]
    levels["status"] = [max((_worst(F, ("level", p, l)), _worst(F, ("pen", p))), key=RANK.get)
                        for p, l in zip(levels["pen"], levels["level"])]
    if not roster_given:
        F.append(Finding("roster_learned", "info", "Mouse", "No mouse roster saved yet",
                         f"The check used the {int(mice['expected'].sum())} mice it found in this data as the expected list. "
                         "Because of that it cannot tell if a mouse is completely missing from the experiment.",
                         ["Open the Roster tab, check the list, give mice names if you like, and press Save. "
                          "From then on, a mouse that stops appearing is flagged."], ("system",)))

    F.sort(key=lambda f: (-RANK[f.severity], f.category, f.title))
    exp = mice[mice["expected"]]
    zexp = zones[zones["expected"]]
    summary = dict(
        mice_expected=len(exp), mice_ok=int(exp["status"].isin(["ok", "info"]).sum()),
        mice_recent=int(((exp["hours_since"] < s.mouse_warn_h) & exp["hours_since"].notna()).sum()),
        zones_total=len(zexp), zones_ok=int(zexp["status"].isin(["ok", "info"]).sum()),
        levels_total=len(levels), levels_ok=int(levels["status"].isin(["ok", "info"]).sum()),
        files=len(files), rows=int(len(reads)), valid_rows=int(len(valid)),
        duplicates_removed=load.duplicates_removed, noise_rows=int((~reads["valid"]).sum()),
    )
    return HealthReport(F, mice, zones, levels, files, ref, data_start, data_end, not roster_given, summary, s)


# ----------------------------------------------------------------------------- files & data quality
def _check_files(F, files, reads, load, s, now, ref):
    for _, f in files.iterrows():
        name = f["file"]
        if f["error"]:
            F.append(Finding(f"file_error|{name}", "critical", "Data", f"File could not be used: {name}", f["error"],
                             ["Open the file in Excel/Notepad to see if it is damaged.",
                              "Re-export it from the logger computer and replace the file."], ("file", name)))
            continue
        lvl_target = ("level", f["pen"], f["level"]) if pd.notna(f["pen"]) and f["level"] else ("file", name)
        if pd.isna(f["pen"]) or not f["level"]:
            F.append(Finding(f"file_nopen|{name}", "warning", "Data", f"Cannot tell which pen/level this file is: {name}",
                             "The file name (or its folder) does not contain 'PenN-lower' or 'PenN-upper', so its reads were not "
                             "assigned to a pen.", ["Rename the file like Pen2-upper-....csv or keep it in a folder with that name."],
                             ("file", name)))
        if f["bad_lines"]:
            F.append(Finding(f"file_badlines|{name}", "warning", "Data", f"{int(f['bad_lines'])} damaged line(s) skipped in {name}",
                             "Some lines had the wrong number of columns (often a half-written line when the logger was stopped).",
                             ["Usually harmless if it is only a few lines. If many, re-export the file."], ("file", name)))
        if f["backwards_jumps"]:
            n = int(f["backwards_jumps"])
            F.append(Finding(f"file_clockback|{name}", "critical" if n > 10 else "warning", "Logger",
                             f"Clock went backwards {n} time(s) in {name}",
                             "Timestamps jump to an earlier time in the middle of the file. The logger computer's clock was "
                             "reset or changed, so timing (and who was together when) is unreliable around those moments.",
                             ["Check the date/time and time-zone settings on that logger computer (turn on automatic time sync).",
                              "Note the date in your lab book - analyses across the jump should be treated carefully."], lvl_target))
        if f["rows"]:
            pct = 100.0 * f.get("noise_rows", 0) / f["rows"]
            if pct >= s.noise_warn_pct:
                F.append(Finding(f"noise_file|{name}", "critical" if pct >= s.noise_crit_pct else "warning", "Data",
                                 f"{pct:.1f}% of reads are electrical noise in {name}",
                                 "Reads with an invalid tag ID or no tag number are antenna noise or half-read tags. A few are normal; "
                                 "a lot means interference or a loose cable.",
                                 ["Look at the 'Sensors' tab for the noisiest zone.",
                                  "Check that antenna cables are plugged in firmly and away from power adapters/motors."],
                                 lvl_target, {"noise rows": int(f["noise_rows"]), "total rows": int(f["rows"])}))
    timed = reads[reads["ts"].notna()]
    future = timed[timed["ts"] > now + pd.Timedelta(minutes=10)]
    if len(future):
        F.append(Finding("clock_future", "critical", "Logger", f"{len(future)} read(s) are timestamped in the future",
                         f"The newest is {when(future['ts'].max())} but this computer's time is {when(now)}. A logger clock is ahead.",
                         ["Fix the date/time on the logger computer(s) (automatic time sync).",
                          "Tell whoever analyses the data - those times cannot be trusted."], ("system",),
                         {"affected files": ", ".join(sorted(future["file"].unique())[:6])}))
    old = timed[timed["ts"].dt.year < 2020]
    if len(old):
        F.append(Finding("clock_reset", "critical", "Logger", f"{len(old)} read(s) have a year before 2020",
                         "That usually means the logger computer's clock battery died and the date reset.",
                         ["Set the correct date/time on the logger computer and replace its clock battery if it happens again."],
                         ("system",), {"affected files": ", ".join(sorted(old["file"].unique())[:6])}))
    if s.reference_time == "export" and ref is not None and (now - ref) > pd.Timedelta(hours=s.stale_data_h):
        F.append(Finding("data_old", "warning", "Data", f"Newest data is {dur((now - ref).total_seconds() / 3600)} old",
                         f"The files were exported {when(ref)} but it is now {when(now)}. You may be looking at an old export.",
                         ["If you wanted today's status, export fresh files from the loggers and reload.",
                          "If you are intentionally re-checking old data, tick 'Known issue' or switch Settings > Reference time."],
                         ("system",)))
    if load.duplicates_removed:
        F.append(Finding("dupes", "info", "Data", f"{load.duplicates_removed:,} repeated rows removed",
                         "Each export contains everything recorded so far, so overlapping files repeat the same reads. "
                         "They were merged - nothing is lost.", [], ("system",)))
    nonum = reads[reads["noise_reason"].eq("no tag number") & reads["uid"].str.match(s.valid_uid_regex)]
    for uid, g in nonum.groupby("uid"):
        if len(g) >= 3:
            F.append(Finding(f"tag_nonumber|{uid}", "warning" if len(g) >= 10 else "info", "Mouse", f"Tag {uid} was read {len(g)} times but has no tag number",
                             "The reader sees a genuine tag, but it was never entered in the system, so its reads are ignored.",
                             ["Is this a new mouse? Register its tag in KBarn.", "If it is a spare/lost tag, remove it from the cage."],
                             ("system",), {"first read": when(g["ts"].min()), "last read": when(g["ts"].max())}))


# ----------------------------------------------------------------------------- loggers
def _check_loggers(F, files, reads, s, ref):
    rows, level_bad = [], set()
    timed = reads[reads["ts"].notna() & reads["pen"].notna()]
    expected = [(p, l) for p in s.pens for l in s.levels]
    seen = {(int(p), l) for p, l in zip(timed["pen"], timed["level"])}
    for p, l in expected + sorted(seen - set(expected)):
        fl = files[(files["pen"] == p) & (files["level"] == l)] if len(files) else files
        g = timed[(timed["pen"] == p) & (timed["level"] == l)]
        tgt = ("level", p, l)
        row = dict(pen=p, level=l, expected=(p, l) in expected, files=len(fl), rows=len(g), first_read=pd.NaT, last_read=pd.NaT,
                   export_time=pd.NaT, minutes_since=np.nan, longest_gap_min=np.nan, noise_pct=np.nan)
        if (p, l) in expected and len(fl) == 0:
            F.append(Finding(f"level_nofile|{p}|{l}", "critical", "Logger", f"No data file for {where(p, l)}",
                             "Nothing was loaded for this pen level. Its mice/antennas cannot be checked.",
                             ["Export the file from that logger computer and put it in the data folder.",
                              "If this pen level is intentionally not recorded, remove it in Settings > Colony layout."], tgt))
            level_bad.add((p, l)); rows.append(row); continue
        if len(g) == 0:
            if len(fl):
                F.append(Finding(f"level_empty|{p}|{l}", "critical", "Logger", f"{where(p, l)} file has no reads",
                                 "The file exists but is empty - the logger was not recording, or nothing was ever detected.",
                                 ["Check the logger computer: is KBarn running? Is the USB hub powered?",
                                  "Restart logging, then re-export."], tgt))
            level_bad.add((p, l)); rows.append(row); continue
        ts = g["ts"].sort_values()
        exp_t = fl["export_time"].dropna().max() if len(fl) and fl["export_time"].notna().any() else ref
        gaps = ts.diff().dt.total_seconds().dropna() / 60
        own_max = float(gaps.max()) if len(gaps) else 0.0
        since = max(0.0, (exp_t - ts.iloc[-1]).total_seconds() / 60) if exp_t is not None else 0.0
        warn_t = max(s.logger_warn_min, s.logger_adapt_warn * own_max)
        crit_t = max(s.logger_crit_min, s.logger_adapt_crit * own_max)
        row.update(first_read=ts.iloc[0], last_read=ts.iloc[-1], export_time=exp_t, minutes_since=since,
                   longest_gap_min=own_max, noise_pct=100 * (1 - g["valid"].mean()))
        if since >= warn_t:
            level_bad.add((p, l))
            F.append(Finding(f"logger_stale|{p}|{l}", "critical" if since >= crit_t else "warning", "Logger",
                             f"{where(p, l)} stopped recording {dur(since / 60)} before the export",
                             f"The last read on this level was at {when(ts.iloc[-1])}, but the file was exported {when(exp_t)}. "
                             f"In this data the longest normal quiet spell on this level was {dur(own_max / 60)}.",
                             ["Go to that logger computer: is KBarn still running and is the screen not asleep/frozen?",
                              "Check the USB hub/reader power and cables, then restart logging.",
                              "Mice are probably fine - but nothing is being recorded until this is fixed."],
                             tgt, {"last read": when(ts.iloc[-1]), "exported": when(exp_t),
                                   "normal longest quiet spell": dur(own_max / 60)}))
        rows.append(row)

    # ---- silences inside the recording: whole system, or one pen
    allts = timed["ts"].sort_values().reset_index(drop=True)
    pen_ts = {p: np.sort(timed.loc[timed["pen"] == p, "ts"].values) for p in s.pens}
    gaps = allts.diff().dt.total_seconds() / 60
    for i in gaps[gaps >= s.outage_warn_min].sort_values(ascending=False).index[:5]:
        t0, t1, m = allts[i - 1], allts[i], gaps[i]
        F.append(Finding(f"outage_all|{t0:%Y%m%d%H%M}", "critical" if m >= s.outage_crit_min else "warning", "Logger",
                         f"All pens went silent for {dur(m / 60)} ({when(t0)} to {when(t1)})",
                         "No pen recorded anything. That points to a shared problem: the whole logging computer, "
                         "a power cut, network/time-sync, or data merged from computers that were off.",
                         ["Check power/network at that time (was there a power cut or a computer restart?).",
                          "If you did this on purpose (cleaning, cage change), tick 'Known issue' and ignore it."],
                         ("system",), {"from": when(t0), "to": when(t1)}))
    for p in s.pens:
        a = pen_ts[p]
        if len(a) < 2:
            continue
        d = np.diff(a).astype("timedelta64[s]").astype(float) / 60
        idx = np.where(d >= s.outage_warn_min)[0]
        reported = 0
        for j in idx[np.argsort(-d[idx])]:
            t0, t1 = a[j], a[j + 1]
            others = [p2 for p2 in s.pens if p2 != p and len(pen_ts[p2])]
            active = sum(1 for p2 in others
                         if (np.searchsorted(pen_ts[p2], t1) - np.searchsorted(pen_ts[p2], t0)) >= 3)
            if not others or active < max(1, min(2, len(others))):
                continue   # everybody silent -> already reported as a system-wide silence
            if reported >= 5:
                break
            reported += 1
            m = d[j]
            F.append(Finding(f"outage_pen|{p}|{pd.Timestamp(t0):%Y%m%d%H%M}", "critical" if m >= s.outage_crit_min else "warning",
                             "Logger", f"Pen {p} went silent for {dur(m / 60)} ({when(t0)} to {when(t1)})",
                             f"Other pens kept recording during this time but neither level of Pen {p} produced a single read. "
                             "With several mice in a pen this is almost never real - a logger or reader was down.",
                             [f"Check Pen {p}'s logger computer and reader power around that time.",
                              "If the mice were moved or the cage cleaned then, mark it as a known issue."],
                             ("pen", p), {"from": when(t0), "to": when(t1)}))
            level_bad.add((p, "lower")); level_bad.add((p, "upper"))
    return pd.DataFrame(rows), level_bad


# ----------------------------------------------------------------------------- sensors / zones
def _check_sensors(F, reads, valid, s, ref, level_bad):
    rows = []
    v = valid[valid["pen"].notna()].sort_values("ts")
    lvl_total = v.groupby(["pen", "level"]).size()
    zc = v.groupby(["pen", "level", "zone"])
    cnt, lastz = zc.size(), zc["ts"].max()
    nz_rows = reads[reads["zone"].notna() & reads["pen"].notna()]
    noise_all = nz_rows[~nz_rows["valid"]].groupby(["pen", "level", "zone"]).size()
    all_zone_rows = nz_rows.groupby(["pen", "level", "zone"]).size()

    # repeats: the same tag read again within 0.5 s at the same antenna
    rep = v.groupby(["pen", "level", "zone", "tag_no"])["ts"].diff().dt.total_seconds() < 0.5
    rep_frac = rep.groupby([v["pen"], v["level"], v["zone"]]).mean()

    share = {k: cnt[k] / lvl_total.get((k[0], k[1]), np.nan) for k in cnt.index}
    lvl_ts = {k: g["ts"].to_numpy() for k, g in v.groupby(["pen", "level"])}
    expected = {(p, l, z) for p in s.pens for l in s.levels for z in s.level_zones(l)}
    for p, l, z in sorted(set(cnt.index) - expected):
        F.append(Finding(f"zone_unexpected|{p}|{l}|{z}", "info", "Sensor", f"Unexpected antenna: {where(p, l, z)}",
                         f"Reads arrive from zone {int(z)} on {where(p, l)} but the layout says that level has "
                         f"{s.zones_per_level.get(l, 0)} zones.", ["If the layout changed, update Settings > Colony layout."],
                         ("zone", p, l, z)))

    for p, l, z in sorted(expected | set(cnt.index)):
        key = (p, l, z)
        n = int(cnt.get(key, 0))
        last = lastz.get(key, pd.NaT)
        lvl_has_data = lvl_total.get((p, l), 0) > 0
        tgt = ("zone", p, l, z)
        own_gap = np.nan
        if n >= 5:
            tz = v.loc[(v["pen"] == p) & (v["level"] == l) & (v["zone"] == z), "ts"]
            own_gap = tz.diff().dt.total_seconds().max() / 3600
        since = (ref - last).total_seconds() / 3600 if pd.notna(last) and ref is not None else np.nan
        peers = [share[(p2, l, z)] for p2 in s.pens if p2 != p and (p2, l, z) in share]
        peer_med = float(np.median(peers)) if len(peers) >= 2 else np.nan
        my_share = share.get(key, 0.0 if lvl_has_data else np.nan)
        ratio = my_share / peer_med if pd.notna(peer_med) and peer_med >= 0.01 and pd.notna(my_share) else np.nan
        row = dict(pen=p, level=l, zone=z, expected=key in expected, reads=n, last_read=last, hours_since=since,
                   longest_gap_h=own_gap, share_pct=100 * my_share if pd.notna(my_share) else np.nan,
                   vs_other_pens=ratio, noise_reads=int(noise_all.get(key, 0)), repeat_pct=100 * float(rep_frac.get(key, 0.0)))
        rows.append(row)
        if key not in expected:
            continue
        if n == 0 and lvl_has_data:
            F.append(Finding(f"zone_dead|{p}|{l}|{z}", "critical", "Sensor", f"{where(p, l, z)}: antenna never read anything",
                             "No tag was detected at this antenna during the whole recording, while the rest of the level was working.",
                             ["Check the antenna cable and its USB port/hub on the logger computer.",
                              "Check in the video whether mice visit that spot - if they do, the antenna is dead.",
                              "Compare with the zone map: is this zone labelled with the right number?"], tgt,
                             {"reads": 0, "other zones on this level": f"{int(lvl_total.get((p, l), 0)):,} reads"}))
            continue
        weak = pd.notna(ratio) and ratio < s.weak_share_ratio
        if weak:
            F.append(Finding(f"zone_weak|{p}|{l}|{z}", "critical" if ratio < s.weak_share_ratio / 5 else "warning", "Sensor",
                             f"{where(p, l, z)}: antenna reads far less than the same antenna in other pens",
                             f"It got {n:,} reads ({100 * my_share:.1f}% of its level) versus a typical {100 * peer_med:.1f}% for this "
                             f"antenna in the other pens (only {ratio:.0%} as much). Either the antenna is weak/loose, or mice really "
                             "avoid that spot in this pen.",
                             ["Check that antenna's cable and its position/height versus the same one in the other pens.",
                              "Look at the video: do mice walk over that spot? If yes, the antenna is missing them.",
                              "If this pen is set up differently on purpose (moved nest/food), tick 'Known issue'."], tgt,
                             {"reads": n, "share of level": f"{100 * my_share:.1f}%",
                              "typical share (other pens)": f"{100 * peer_med:.1f}%"}))
        elif n >= 20 and pd.notna(since) and (p, l) not in level_bad:
            og = 0 if pd.isna(own_gap) else own_gap
            warn_t = max(s.zone_silent_warn_h, s.zone_adapt_warn * og)
            crit_t = max(s.zone_silent_crit_h, s.zone_adapt_crit * og)
            arr = lvl_ts.get((p, l), np.array([], dtype="datetime64[ns]"))
            level_reads_since = len(arr) - int(np.searchsorted(arr, np.datetime64(last), side="right"))
            expected_reads = my_share * level_reads_since
            if since >= warn_t and expected_reads >= s.zone_min_expected_reads:
                F.append(Finding(f"zone_silent|{p}|{l}|{z}", "critical" if since >= crit_t else "warning", "Sensor",
                                 f"{where(p, l, z)}: no read for {dur(since)}",
                                 f"Last read {when(last)}. In that time the rest of this level had {level_reads_since:,} reads, so this antenna "
                                 f"should normally have caught about {expected_reads:.0f}. Its longest normal quiet spell in this data was {dur(og)}.",
                                 ["Check the antenna cable/USB port - it may have come loose or the hub reset.",
                                  "Look at the video: if a mouse sat or walked there with no read, the antenna failed."], tgt,
                                 {"last read": when(last), "normal longest quiet spell": dur(og),
                                  "reads expected meanwhile": f"{expected_reads:.0f}"}))
        if n >= 200 and row["repeat_pct"] >= 100 * s.chatter_repeat_frac:
            F.append(Finding(f"zone_chatter|{p}|{l}|{z}", "warning", "Sensor", f"{where(p, l, z)}: antenna may be 'chattering'",
                             f"{row['repeat_pct']:.0f}% of its reads are the same tag read again within half a second. A mouse sleeping "
                             "on the antenna does this - but so does a faulty reader.",
                             ["Check on video whether a mouse is simply resting there.", "If not, power-cycle that reader."], tgt))
        nz, tot_rows = row["noise_reads"], int(all_zone_rows.get(key, 0))
        if nz >= 10 and tot_rows and 100 * nz / tot_rows >= s.noise_warn_pct:
            F.append(Finding(f"zone_noise|{p}|{l}|{z}", "warning", "Sensor", f"{where(p, l, z)}: antenna is picking up electrical noise",
                             f"{nz} of its {tot_rows:,} reads ({100 * nz / tot_rows:.1f}%) are garbage tag IDs.",
                             ["Move that antenna's cable away from power supplies/motors; check the connector."], tgt))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- mice
def _check_mice(F, reads, valid, roster, roster_given, s, ref):
    v = valid[valid["pen"].notna()].sort_values("ts")
    level_idx = {l: i for i, l in enumerate(s.levels)}
    v = v.assign(_loc=v["pen"].astype(int) * 10000 + v["level"].map(level_idx).fillna(9).astype(int) * 100 + v["zone"].astype(int))
    by = {t: g for t, g in v.groupby("tag_no")}
    tags = sorted(set(roster["tag_no"]) | set(by),
                  key=lambda t: (not str(t).isdigit(), int(t) if str(t).isdigit() else 0, str(t)))
    rmap = roster.set_index("tag_no")
    span_h = (v["ts"].max() - v["ts"].min()).total_seconds() / 3600
    win_start = ref - pd.Timedelta(hours=s.activity_window_h)
    rows, allowed_of, win_pen, win_reads = [], {}, {}, {}

    for t in tags:
        g = by.get(t)
        r = rmap.loc[t] if t in rmap.index else None
        expected = bool(r["expected"]) if r is not None else (g is not None and len(g) >= s.min_reads_real_mouse)
        name = (str(r["name"]) if r is not None else "") or ""
        rec_pens = parse_pens(r["home_pen"]) if r is not None else []     # pens the roster says it may be in
        row = dict(tag_no=t, name=name, home_pen=pens_text(rec_pens), pens_allowed=rec_pens, current_pen=pd.NA,
                   expected=expected, in_roster=r is not None, reads_total=0,
                   first_seen=pd.NaT, last_seen=pd.NaT, hours_since=np.nan, last_location="-", at_spot_h=np.nan,
                   reads_window=0, longest_quiet_h=np.nan, longest_stay_h=np.nan)
        if g is not None and len(g):
            ts = g["ts"].reset_index(drop=True)
            loc = g["_loc"].to_numpy()
            gap_s = ts.diff().dt.total_seconds().to_numpy()[1:]
            brk = np.concatenate([[True], (loc[1:] != loc[:-1]) | (gap_s > 1800)])
            starts = ts.groupby(np.cumsum(brk)).transform("min")
            stay = (ts - starts).dt.total_seconds() / 3600
            last = ts.iloc[-1]
            data_home = int(g["pen"].value_counts().idxmax())
            allowed_of[t] = rec_pens or [data_home]
            cur = int(g["pen"].iloc[-1])
            in_win = g[g["ts"] >= win_start]
            win_pen[t] = int(in_win["pen"].value_counts().idxmax()) if len(in_win) else cur
            row.update(reads_total=len(g), first_seen=ts.iloc[0], last_seen=last, current_pen=cur,
                       hours_since=max(0.0, (ref - last).total_seconds() / 3600),
                       last_location=where(g["pen"].iloc[-1], g["level"].iloc[-1], g["zone"].iloc[-1]),
                       at_spot_h=float(stay.iloc[-1]), longest_stay_h=float(stay.max()),
                       longest_quiet_h=(float(np.max(gap_s)) / 3600 if len(gap_s) >= 4 else np.nan),
                       reads_window=int((ts >= win_start).sum()))
            if not rec_pens:
                row["home_pen"], row["pens_allowed"] = str(data_home), [data_home]
        elif rec_pens:
            allowed_of[t] = rec_pens
        win_reads[t] = row["reads_window"]
        rows.append(row)
    mice = pd.DataFrame(rows)
    mice["current_pen"] = mice["current_pen"].astype("Int64")

    # ---------------- per-mouse checks
    for _, m in mice.iterrows():
        t, tgt = m["tag_no"], ("mouse", m["tag_no"])
        label = f"Mouse {t}" + (f" ({m['name']})" if m["name"] else "")
        if not m["expected"]:
            continue
        if m["reads_total"] == 0:
            F.append(Finding(f"mouse_never|{t}", "critical", "Mouse", f"{label} was never read",
                             "This mouse is on the roster but not a single read of its tag exists in this data.",
                             ["Check the cage: is the mouse there and alive?",
                              "Check its tag with the handheld reader (it may have fallen out or died).",
                              "If it was removed from the experiment, untick 'Expected' in the Roster tab."], tgt))
            continue
        og = 0 if pd.isna(m["longest_quiet_h"]) else m["longest_quiet_h"]
        warn_t = max(s.mouse_warn_h, s.mouse_adapt_warn * og)
        crit_t = max(s.mouse_crit_h, s.mouse_adapt_crit * og)
        h = m["hours_since"]
        if h >= warn_t:
            F.append(Finding(f"mouse_missing|{t}", "critical" if h >= crit_t else "warning", "Mouse",
                             f"{label} not read for {dur(h)}",
                             f"Last read {when(m['last_seen'])} at {m['last_location']}. The longest it was ever quiet before in this "
                             f"data is {dur(og) if og else 'unknown'}.",
                             ["Look at the cage (or the video of that pen) - is the mouse alive and moving?",
                              "If it is alive but not read, its tag may have fallen out: scan it with the handheld reader.",
                              "If it was removed on purpose, untick 'Expected' in the Roster tab so this stops appearing."], tgt,
                             {"last read": when(m["last_seen"]), "last place": m["last_location"], "quiet for": dur(h),
                              "longest quiet spell so far": dur(og)}))
        elif h <= 1.0 and m["at_spot_h"] >= s.stuck_warn_h:
            since_t = m["last_seen"] - pd.Timedelta(hours=m["at_spot_h"])
            F.append(Finding(f"mouse_stuck|{t}", "critical" if m["at_spot_h"] >= s.stuck_crit_h else "warning", "Mouse",
                             f"{label} has stayed at one spot for {dur(m['at_spot_h'])}",
                             f"It is still being read, but only at {m['last_location']} since {when(since_t)}. "
                             "A mouse that has died (or a tag lying on the floor) keeps being read by the same antenna.",
                             ["Look at the camera/cage NOW: is this mouse moving? A sleeping mouse is fine; a motionless one is not.",
                              "If the mouse is fine and just sleeps on an antenna, tick 'Known issue'."], tgt,
                             {"at this spot since": when(since_t), "longest stay earlier": dur(m["longest_stay_h"])}))
    # activity drop vs pen-mates
    if span_h >= 1.5 * s.activity_window_h:
        flagged = {f.target for f in F}
        for _, m in mice[mice["expected"] & (mice["reads_total"] > 0)].iterrows():
            t = m["tag_no"]
            if ("mouse", t) in flagged:
                continue
            mates = [win_reads[o] for o in mice[mice["expected"]]["tag_no"] if o != t and win_pen.get(o) == win_pen.get(t)]
            if len(mates) >= 2 and np.median(mates) >= 30 and win_reads[t] < s.activity_drop_ratio * np.median(mates):
                F.append(Finding(f"mouse_lowact|{t}", "warning", "Mouse", f"Mouse {t}: much quieter than its pen-mates",
                                 f"In the last {dur(s.activity_window_h)} it had {win_reads[t]} reads; its pen-mates had a median of "
                                 f"{int(np.median(mates))}. It is still in the data but barely moving or visiting antennas.",
                                 ["Look at the mouse: is it sick, injured, or hiding?",
                                  "Check the nest/food zones' antennas are working."],
                                 ("mouse", t), {"reads in window": win_reads[t], "pen-mates median": int(np.median(mates))}))

    # ---------------- tag identity problems
    idv = reads[reads["uid"].str.match(s.valid_uid_regex) & reads["tag_no"].notna()]
    for col, other, what, noun in (("tag_no", "uid", "Tag number", "tag ID"), ("uid", "tag_no", "Tag ID", "tag number")):
        c = idv.groupby([col, other]).size().reset_index(name="n")
        for k, g in c.groupby(col):
            g = g.sort_values("n", ascending=False)
            if len(g) > 1 and g["n"].iloc[1] >= 3:
                shown = ", ".join(f"{r[other]} ({r['n']} reads)" for _, r in g.head(4).iterrows())
                F.append(Finding(f"tag_conflict|{col}|{k}", "critical", "Mouse", f"{what} {k} belongs to more than one {noun}",
                                 f"Seen with: {shown}. Two mice may share one number, or a tag was re-assigned.",
                                 ["Check the registration table in KBarn - every tag must have its own number.",
                                  "Until fixed, results for these animals are mixed up."],
                                 ("mouse", str(k)) if col == "tag_no" else ("system",)))
    # ---------------- appears in more than one pen (pens are not connected)
    if not s.pens_connected:
        for t, g in by.items():
            if g["pen"].nunique() < 2:
                continue
            listed = parse_pens(rmap.loc[t, "home_pen"]) if t in rmap.index else []
            allowed = listed or allowed_of.get(t) or [int(g["pen"].value_counts().idxmax())]
            out = g[~g["pen"].isin(allowed)]
            nxt = g["pen"].shift(-1)
            dt = (g["ts"].shift(-1) - g["ts"]).dt.total_seconds()
            overlap = int(((nxt != g["pen"]) & nxt.notna() & (dt < 60)).sum())   # two pens within a minute = impossible
            counts = g["pen"].value_counts()
            by_pen = ", ".join(f"Pen {p}: {n}" for p, n in counts.items())
            if overlap or len(out) >= 3:
                where_txt = f"Allowed pen(s) for this mouse: {pens_text(allowed)}" + ("" if listed else " (learned from the data)")
                F.append(Finding(f"mouse_multipen|{t}", "critical" if overlap or len(out) > 0.5 * len(g) else "warning", "Mouse",
                                 f"Mouse {t} was read in a pen it is not allowed in" if len(out) >= 3 else f"Mouse {t} was read in two pens at once",
                                 f"Reads by pen - {by_pen}. {where_txt}. " + (f"{overlap} times it was read in two different pens within one "
                                                                              "minute, which one mouse cannot do - almost certainly two mice sharing "
                                                                              "one tag number." if overlap else "The pens are not connected."),
                                 ["Was the mouse moved on purpose? Then open the Roster tab and type every pen it may be in, e.g. 4,3, and save.",
                                  "If not, check that two mice do not carry tags with the same number."], ("mouse", t),
                                 {"allowed pens": pens_text(allowed), "reads outside allowed pens": len(out),
                                  "reads in two pens within 1 min": overlap,
                                  "first outside read": when(out["ts"].min()) if len(out) else "-"}))
            elif listed and len(listed) > 1:
                moves = _pen_moves(g)
                if moves:
                    F.append(Finding(f"mouse_moved|{t}", "info", "Mouse", f"Mouse {t} changed pens ({len(moves)}x)",
                                     "It moved between pens it is allowed in: " + "; ".join(f"Pen {a} -> Pen {b} around {when(w)}" for a, b, w in moves[:5]) + ".",
                                     [], ("mouse", t), {"reads by pen": by_pen}))
    # ---------------- tags nobody registered
    if roster_given:
        known = set(roster["tag_no"])
        strays = {t: len(g) for t, g in by.items() if t not in known}
        for t, n in strays.items():
            if n >= s.min_reads_real_mouse:
                g = by[t]
                F.append(Finding(f"mouse_unknown|{t}", "warning", "Mouse", f"Unknown mouse {t} ({n:,} reads) is not on the roster",
                                 f"Seen in {', '.join(f'Pen {p}' for p in sorted(g['pen'].unique()))} from {when(g['ts'].min())} to {when(g['ts'].max())}.",
                                 ["New mouse added? Add it in the Roster tab.", "Wrong tag number typed in? Fix it in KBarn."], ("mouse", t)))
        few = {t: n for t, n in strays.items() if 3 <= n < s.min_reads_real_mouse}
        if few:
            F.append(Finding("mouse_strays", "info", "Mouse", f"{len(few)} stray tag number(s) with only a few reads",
                             "Seen only a handful of times and not on the roster: " + ", ".join(f"{t} ({n})" for t, n in sorted(few.items())),
                             ["Usually a tag misread or a mouse that was handled. Ignore unless it keeps growing."], ("system",)))
    return mice


def _pen_moves(g: pd.DataFrame, min_reads: int = 3):
    """[(from_pen, to_pen, time_of_first_read_in_new_pen)] - ignores isolated reads (misreads) shorter than `min_reads`."""
    runs = (pd.DataFrame({"pen": g["pen"].to_numpy(), "ts": g["ts"].to_numpy(), "run": (g["pen"] != g["pen"].shift()).cumsum().to_numpy()})
            .groupby("run").agg(pen=("pen", "first"), start=("ts", "min"), n=("pen", "size")))
    runs = runs[runs["n"] >= min_reads]
    moves, prev = [], None
    for pen, start in zip(runs["pen"], runs["start"]):
        if prev is not None and pen != prev:
            moves.append((int(prev), int(pen), pd.Timestamp(start)))
        prev = pen
    return moves


# ----------------------------------------------------------------------------- empty frames
def _empty_mice():
    return pd.DataFrame(columns=["tag_no", "name", "home_pen", "pens_allowed", "current_pen", "expected", "in_roster", "reads_total", "first_seen", "last_seen",
                                 "hours_since", "last_location", "at_spot_h", "reads_window", "longest_quiet_h", "longest_stay_h", "status"])


def _empty_zones():
    return pd.DataFrame(columns=["pen", "level", "zone", "expected", "reads", "last_read", "hours_since", "longest_gap_h",
                                 "share_pct", "vs_other_pens", "noise_reads", "repeat_pct", "status"])


def _empty_levels():
    return pd.DataFrame(columns=["pen", "level", "expected", "files", "rows", "first_read", "last_read", "export_time",
                                 "minutes_since", "longest_gap_min", "noise_pct", "status"])
