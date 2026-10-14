"""KBarn Health Check - the daily screen for the lab manager.  Start it with Start.bat (double-click)."""
from __future__ import annotations

import contextlib
import subprocess
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import charts  # noqa: E402
from kbarn_health import __version__  # noqa: E402
from kbarn_health.checks import ICON, LABEL, RANK, analyze, dur, when  # noqa: E402
from kbarn_health.loader import find_csvs, folder_signature, load_folder, load_sources  # noqa: E402
from kbarn_health.report import mice_view, to_html, zones_view  # noqa: E402
from kbarn_health.roster import clean_roster, load_roster, save_roster, suggest_from_data  # noqa: E402
from kbarn_health.settings import STATE_DIR, Settings, load_acks, load_settings, save_acks, save_settings  # noqa: E402

st.set_page_config(page_title="KBarn Health Check", page_icon="🐭", layout="wide")
st.markdown("""<style>
.banner{padding:18px 24px;border-radius:12px;color:#fff;font-size:1.7rem;font-weight:700;margin:4px 0 14px}
.banner small{display:block;font-size:1rem;font-weight:400;opacity:.95;margin-top:4px}
.block-container{padding-top:4.5rem}
</style>""", unsafe_allow_html=True)

SETTINGS = load_settings()
LOG_FILE = (STATE_DIR.parent if getattr(sys, "frozen", False) else STATE_DIR) / "app.log"


def log_error(where: str) -> str:
    """Write the current exception to the log file; returns the text."""
    text = traceback.format_exc()
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[{pd.Timestamp.now():%Y-%m-%d %H:%M:%S}] {where}\n{text}")
    except Exception:
        pass
    return text


@contextlib.contextmanager
def safe(where: str):
    """One broken section must never take the whole screen down: show a friendly box and keep going."""
    try:
        yield
    except Exception as e:
        if any(c.__name__ in ("ScriptControlException", "RerunException", "StopException") for c in type(e).__mro__):
            raise                                   # Streamlit's own st.rerun()/st.stop() signals
        details = log_error(where)
        st.error(f"⚠ The **{where}** section could not be shown. The rest of the program still works. "
                 f"(Details were saved to {LOG_FILE})")
        with st.expander("Technical details (send this to whoever maintains the program)"):
            st.code(details)
BANNER = {"critical": "#c62f2f", "warning": "#b87400", "ok": "#0b7a0b", "none": "#555"}


# ----------------------------------------------------------------------------- cached work
@st.cache_data(show_spinner="Reading the RFID files…")
def cached_folder_load(folder: str, signature: tuple, uid_regex: str):
    return load_folder(folder, SETTINGS)


@st.cache_data(show_spinner="Reading the uploaded files…")
def cached_upload_load(items: tuple, uid_regex: str):
    import io
    return load_sources(((n, io.BytesIO(b), "") for n, b in items), SETTINGS)


def browse_for_folder():
    """Open the Windows 'choose folder' window in a separate process (it must not run inside the web server)."""
    import tempfile
    start = st.session_state.get("folder_input") or str(Path.home())
    result = Path(tempfile.gettempdir()) / "kbarn_picked_folder.txt"
    result.unlink(missing_ok=True)
    try:
        if getattr(sys, "frozen", False):          # packaged .exe: ask the exe itself to show the dialog
            cmd = [sys.executable, "--pick-folder", start, str(result)]
        else:
            cmd = [sys.executable, str(ROOT / "launcher.py"), "--pick-folder", start, str(result)]
        subprocess.run(cmd, timeout=600)
        out = result.read_text(encoding="utf-8").strip() if result.exists() else ""
        if out:
            st.session_state["folder_input"] = str(Path(out))
    except Exception:
        st.session_state["browse_failed"] = True


# ----------------------------------------------------------------------------- sidebar: where is the data?
st.session_state.setdefault("folder_input", SETTINGS.data_dir)
with st.sidebar:
    st.markdown("## 🐭 KBarn Health Check")
    st.caption(f"Daily check of the RFID data · v{__version__}")
    st.text_input("Folder with the RFID .csv files", key="folder_input",
                  help="The folder where you saved the exported files. Sub-folders are searched too.")
    c1, c2 = st.columns(2)
    c1.button("📂 Browse…", on_click=browse_for_folder, width="stretch")
    if c2.button("🔄 Re-check", type="primary", width="stretch", help="Use after you have copied in new files."):
        st.cache_data.clear()
        st.rerun()
    if st.session_state.pop("browse_failed", False):
        st.warning("The folder window could not open - please paste the folder path into the box.")
    with st.expander("Or upload files instead"):
        uploads = st.file_uploader("RFID .csv files", type="csv", accept_multiple_files=True, label_visibility="collapsed")

with st.sidebar.expander("🛠 Troubleshooting"):
    st.caption("If something looks wrong or an error appears, try these first.")
    if st.button("Show all hidden issues again", key="reset_acks"):
        save_acks({})
        st.rerun()
    if st.button("Restore default settings", key="reset_settings_sidebar"):
        keep = SETTINGS.data_dir
        defaults = Settings()
        defaults.data_dir = keep
        save_settings(defaults)
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Settings & roster folder: {STATE_DIR}")
    if LOG_FILE.exists() and st.checkbox("Show the error log", key="show_log"):
        st.code(LOG_FILE.read_text(encoding="utf-8", errors="replace")[-3000:])

folder = st.session_state["folder_input"].strip().strip('"')
if uploads:
    load = cached_upload_load(tuple((u.name, u.getvalue()) for u in uploads), SETTINGS.valid_uid_regex)
    source_note = f"{len(uploads)} uploaded file(s)"
elif folder and Path(folder).is_dir() and find_csvs(folder):
    load = cached_folder_load(folder, folder_signature(folder), SETTINGS.valid_uid_regex)
    source_note = f"{len(find_csvs(folder))} file(s) in {folder}"
    if folder != SETTINGS.data_dir:
        SETTINGS.data_dir = folder
        save_settings(SETTINGS)
else:
    load = None

if load is None:
    st.markdown("# 👋 Welcome")
    st.info("**Step 1.** Export the RFID files from the loggers.\n\n**Step 2.** Pick the folder that contains them in the left panel "
            "(press *Browse…* or paste the path).\n\nThe check starts automatically.")
    if folder and not Path(folder).is_dir():
        st.error(f"That folder doesn't exist: {folder}")
    elif folder:
        st.error("No .csv files were found in that folder (sub-folders were searched too).")
    st.stop()

# ----------------------------------------------------------------------------- run the checks
try:
    roster_file = load_roster()
    rep = analyze(load, SETTINGS, roster_file)
except Exception:
    details = log_error("analysis")
    st.error("⚠ The check could not be completed with these files. Nothing was changed. "
             "Open **🛠 Troubleshooting** in the left panel, or send the details below to whoever maintains the program.")
    st.code(details)
    st.stop()
acks = load_acks()
effective = {f.key for f in rep.findings if f.key in acks and RANK[f.severity] <= RANK[acks[f.key].get("severity", "critical")]}
sev = rep.by_severity(effective)
n_crit, n_warn, n_info = len(sev["critical"]), len(sev["warning"]), len(sev["info"])
now = pd.Timestamp.now()

if n_crit:
    kind, headline = "critical", f"🔴 {n_crit} problem{'s' if n_crit > 1 else ''} need{'' if n_crit > 1 else 's'} attention now"
elif n_warn:
    kind, headline = "warning", f"🟠 {n_warn} thing{'s' if n_warn > 1 else ''} to check"
else:
    kind, headline = "ok", "🟢 All clear"
sub = f"Data through {when(rep.reference_time)} · first read {when(rep.data_start)} · {source_note}"
k = rep.summary
with safe("Summary"):
    head_l, head_r = st.columns([5, 1])
    head_l.markdown(f"<div class='banner' style='background:{BANNER[kind]}'>{headline}<small>{sub}</small></div>", unsafe_allow_html=True)
    head_r.download_button("⬇ Report (HTML)", to_html(rep, {k_: acks[k_] for k_ in effective}), file_name=f"kbarn_health_{pd.Timestamp(rep.reference_time):%Y%m%d_%H%M}.html",
                           mime="text/html", help="A one-page summary you can email or print.", width="stretch")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Mice reading normally", f"{k.get('mice_ok', 0)} / {k.get('mice_expected', 0)}")
    m2.metric("Antennas OK", f"{k.get('zones_ok', 0)} / {k.get('zones_total', 0)}")
    m3.metric("Pen levels recording", f"{k.get('levels_ok', 0)} / {k.get('levels_total', 0)}")
    m4.metric("Latest read", when(rep.data_end), delta=(f"{dur((rep.reference_time - rep.data_end).total_seconds() / 3600)} before the export"
                                                              if rep.data_end is not None and (rep.reference_time - rep.data_end) > pd.Timedelta(minutes=5) else None), delta_color="off")

tab_today, tab_mice, tab_sensors, tab_time, tab_files, tab_roster, tab_settings, tab_help = st.tabs(
    ["✅ Today's check", "🐭 Mice", "📡 Sensors", "📈 Timeline", "🗂 Files & loggers", "👥 Roster", "⚙️ Settings", "❓ Help"])


# ----------------------------------------------------------------------------- tab: today's check
def finding_card(f, acked=False):
    with st.container(border=True):
        st.markdown(f"**{ICON[f.severity]} {f.title}**  \n<span style='opacity:.65'>{f.category}</span>", unsafe_allow_html=True)
        st.write(f.detail)
        if f.actions:
            st.markdown("**What to do**\n" + "\n".join(f"- {a}" for a in f.actions))
        if f.evidence:
            with st.expander("Details"):
                st.table(pd.DataFrame({"": list(f.evidence.keys()), " ": [str(v) for v in f.evidence.values()]}).set_index(""))
        if acked:
            st.caption(f"✔ Known issue: {acks[f.key].get('note') or '(no note)'} · marked {acks[f.key].get('date', '')}")
            if st.button("Show this again", key=f"unack_{f.key}"):
                acks.pop(f.key, None)
                save_acks(acks)
                st.rerun()
        else:
            with st.popover("✔ Known issue / ignore"):
                st.caption("Use when you already know about this (e.g. antenna is waiting for a replacement). It is hidden until it gets WORSE.")
                note = st.text_input("Note (optional)", key=f"note_{f.key}")
                if st.button("Hide this", key=f"ack_{f.key}"):
                    acks[f.key] = {"note": note, "date": f"{now:%b %d}", "severity": f.severity}
                    save_acks(acks)
                    st.rerun()


with tab_today:
    with safe("Today's check"):
        cats = st.pills("Show", ["Mouse", "Sensor", "Logger", "Data"], selection_mode="multi", default=["Mouse", "Sensor", "Logger", "Data"], key="cat_filter")
        cats = cats or ["Mouse", "Sensor", "Logger", "Data"]
        shown = 0
        for level, title in (("critical", "Needs attention now"), ("warning", "Please check"), ("info", "Good to know")):
            items = [f for f in sev[level] if f.category in cats]
            if not items:
                continue
            st.subheader(f"{ICON[level]} {title} ({len(items)})")
            for f in items:
                finding_card(f)
                shown += 1
        if not n_crit and not n_warn:
            st.success(f"Nothing needs attention. {k.get('mice_expected', 0)} mice and {k.get('zones_total', 0)} antennas were read normally "
                       f"up to {when(rep.reference_time)}.")
        known = [f for f in rep.findings if f.key in effective]
        if known:
            with st.expander(f"✔ Known issues you chose to hide ({len(known)})"):
                for f in known:
                    finding_card(f, acked=True)
        with st.expander("What does this check look at?"):
            st.markdown("""
- **Mice** - never read · not read for too long (compared with how long *that* mouse is normally quiet) · stuck at one antenna for hours (possible dead mouse or fallen tag) · much quieter than pen-mates · read in two pens at once · two mice sharing a tag number · unknown tags.
- **Antennas (sensors)** - never read anything · reads far less than the same antenna in the other pens · silent for hours while the rest of the level is busy · "chattering" · electrical noise.
- **Loggers** - file missing or empty · stopped recording before the export · a whole pen silent while other pens kept recording · whole system silent · clock jumped back, ahead, or reset.
- **Data** - unreadable/damaged files · noise rate · overlapping exports (merged automatically) · data much older than today.
""")


# ----------------------------------------------------------------------------- tab: mice
with tab_mice:
    with safe("Mice"):
        mv = rep.mice.copy()
        f1, f2, f3 = st.columns([1, 1, 2])
        only_problems = f1.toggle("Only show problems", value=False)
        pens = sorted({p for lst in mv["pens_allowed"] for p in lst} | {int(p) for p in mv["current_pen"].dropna()})
        pen_pick = f2.multiselect("Pen", pens, default=pens)
        if only_problems:
            mv = mv[mv["status"].isin(["warning", "critical"])]
        mv = mv[[(not lst) or any(p in pen_pick for p in lst) or (pd.notna(c) and int(c) in pen_pick)
                     for lst, c in zip(mv["pens_allowed"], mv["current_pen"])]]
        mv = mv.assign(_r=mv["status"].map(lambda s: RANK.get(s, -1))).sort_values("_r", ascending=False, kind="stable")
        table = pd.DataFrame({
            "Status": [f"{ICON.get(s, '⚪')} {LABEL.get(s, 'Removed')}" for s in mv["status"]],
            "Mouse": mv["tag_no"], "Name": mv["name"], "Allowed pen(s)": mv["home_pen"], "Now in pen": mv["current_pen"],
            "Last read": mv["last_seen"].map(when), "Hours ago": mv["hours_since"].round(1),
            "Last place": mv["last_location"], "At that spot (h)": mv["at_spot_h"].round(1),
            "Reads (all)": mv["reads_total"], f"Reads (last {dur(SETTINGS.activity_window_h)})": mv["reads_window"],
            "Longest quiet spell (h)": mv["longest_quiet_h"].round(1),
        })
        st.dataframe(table, hide_index=True, width="stretch", height=min(780, 38 + 35 * len(table)))
        st.caption("Sort by clicking a column. 'Longest quiet spell' is the longest this mouse has gone without a read in this data - "
                   "the alert thresholds adapt to it.")
        st.subheader("Look at one mouse")
        options = [t for t in rep.mice["tag_no"] if rep.mice.set_index("tag_no").loc[t, "reads_total"] > 0]
        if options:
            pick = st.selectbox("Mouse", options, format_func=lambda t: f"Mouse {t}" + (f" - {rep.mice.set_index('tag_no').loc[t, 'name']}" if rep.mice.set_index('tag_no').loc[t, 'name'] else ""))
            st.plotly_chart(charts.mouse_track(load.reads, pick, rep), width="stretch", theme=None)


# ----------------------------------------------------------------------------- tab: sensors
with tab_sensors:
    with safe("Sensors"):
        st.markdown("Every antenna at a glance. **Hover a tile** for details.")
        st.html(charts.sensor_grid_html(rep))
        with st.expander("Table of all antennas"):
            zt = zones_view(rep)
            if st.toggle("Only show problems", key="zone_problems", value=False):
                zt = zt[zt["Status"].str.contains("Check|Problem")]
            st.dataframe(zt, hide_index=True, width="stretch")


# ----------------------------------------------------------------------------- tab: timeline
with tab_time:
    with safe("Timeline"):
        st.plotly_chart(charts.mouse_heatmap(load.reads, rep), width="stretch", theme=None)
        st.plotly_chart(charts.activity_lines(load.reads, rep), width="stretch", theme=None)


# ----------------------------------------------------------------------------- tab: files & loggers
with tab_files:
    with safe("Files & loggers"):
        st.subheader("Each pen level (one logger file each)")
        lv = rep.levels
        st.dataframe(pd.DataFrame({
            "Status": [f"{ICON[s]} {LABEL[s]}" for s in lv["status"]],
            "Pen": lv["pen"], "Level": lv["level"], "Reads": lv["rows"], "First read": lv["first_read"].map(when),
            "Last read": lv["last_read"].map(when), "Exported": lv["export_time"].map(when),
            "Minutes between last read and export": lv["minutes_since"].round(0),
            "Longest quiet spell (min)": lv["longest_gap_min"].round(0), "Noise reads (%)": lv["noise_pct"].round(2)}),
            hide_index=True, width="stretch")
        st.subheader("Files")
        ft = rep.files.copy()
        ft["export_time"] = ft["export_time"].map(when)
        ft["first_read"], ft["last_read"] = ft["first_read"].map(when), ft["last_read"].map(when)
        st.dataframe(ft, hide_index=True, width="stretch")
        st.caption(f"{k.get('rows', 0):,} rows read · {k.get('valid_rows', 0):,} valid · {k.get('noise_rows', 0):,} noise · "
                   f"{k.get('duplicates_removed', 0):,} repeated rows merged.")
        st.download_button("⬇ Mice table (CSV)", mice_view(rep).to_csv(index=False), "mice_status.csv", "text/csv")
        st.download_button("⬇ Antenna table (CSV)", zones_view(rep).to_csv(index=False), "antenna_status.csv", "text/csv")


# ----------------------------------------------------------------------------- tab: roster
with tab_roster:
    with safe("Roster"):
        st.markdown("The roster is the list of mice that **should** be in the experiment. Because of it, a mouse that vanishes "
                   "(or never shows up) is flagged. **Untick _Expected_** for a mouse you removed on purpose. "
                    "**Allowed pen(s):** type one pen (`4`) or several separated by commas (`4,3`) when a mouse may be in either; "
                    "a read in any other pen is flagged.")
        base = roster_file if len(roster_file) else suggest_from_data(load.reads, SETTINGS.min_reads_real_mouse)
        if not len(roster_file):
            st.info("No roster saved yet - this is the list found in the data. Check it and press **Save roster**.")
        edited = st.data_editor(
            base, num_rows="dynamic", hide_index=True, width="stretch", key=f"roster_editor_{len(base)}",
            column_config={"tag_no": st.column_config.TextColumn("Mouse (tag number)", required=True),
                           "name": st.column_config.TextColumn("Name / group"),
                           "home_pen": st.column_config.TextColumn(
                           "Allowed pen(s)", help="The pen this mouse lives in. If it may be in several pens, type them separated by "
                                                  "commas, e.g. 4,3 (= pen 4 or pen 3). Leave empty to learn it from the data."),
                           "expected": st.column_config.CheckboxColumn("Expected", default=True),
                           "notes": st.column_config.TextColumn("Notes")})
        b1, b2, _ = st.columns([1, 2, 3])
        if b1.button("💾 Save roster", type="primary"):
            save_roster(edited)
            st.rerun()
        new = suggest_from_data(load.reads, SETTINGS.min_reads_real_mouse)
        missing = new[~new["tag_no"].isin(set(clean_roster(edited)["tag_no"]))]
        if len(missing) and b2.button(f"➕ Add the {len(missing)} mice found in the data that are not listed"):
            save_roster(pd.concat([clean_roster(edited), missing]))
            st.rerun()


# ----------------------------------------------------------------------------- tab: settings
with tab_settings:
    with safe("Settings"):
        st.markdown("The defaults suit most cases. Times are in **hours** unless it says minutes.")
        with st.form("settings_form"):
            S = SETTINGS
            st.markdown("#### Colony layout")
            c = st.columns(4)
            pens_txt = c[0].text_input("Pens", ", ".join(map(str, S.pens)))
            zl = c[1].number_input("Zones - lower level", 1, 30, int(S.zones_per_level.get("lower", 9)))
            zu = c[2].number_input("Zones - upper level", 1, 30, int(S.zones_per_level.get("upper", 4)))
            conn = c[3].checkbox("Pens are connected", S.pens_connected, help="Leave OFF if mice can never walk between pens - then a mouse read in two pens is flagged.")
            c = st.columns(3)
            lon = c[0].number_input("Lights on (hour)", 0, 23, int(S.lights_on_hour))
            loff = c[1].number_input("Lights off (hour)", 0, 23, int(S.lights_off_hour))
            ref = c[2].radio("How to decide 'now'", ["export", "clock"], index=0 if S.reference_time == "export" else 1, horizontal=True,
                             format_func=lambda v: "Export time in file names" if v == "export" else "This computer's clock",
                             help="Use the export time when you check data that was exported earlier.")
            st.markdown("#### Mice")
            c = st.columns(4)
            mw = c[0].number_input("Not read for … → check", 0.5, 72.0, float(S.mouse_warn_h), 0.5)
            mc = c[1].number_input("Not read for … → problem", 1.0, 168.0, float(S.mouse_crit_h), 0.5)
            sw = c[2].number_input("Stuck at one spot … → check", 0.5, 72.0, float(S.stuck_warn_h), 0.5)
            sc = c[3].number_input("Stuck at one spot … → problem", 1.0, 168.0, float(S.stuck_crit_h), 0.5)
            st.markdown("#### Antennas")
            c = st.columns(4)
            zw = c[0].number_input("Silent for … → check", 0.5, 72.0, float(S.zone_silent_warn_h), 0.5)
            zc = c[1].number_input("Silent for … → problem", 1.0, 168.0, float(S.zone_silent_crit_h), 0.5)
            wk = c[2].number_input("Weak if below … % of other pens", 1, 90, int(S.weak_share_ratio * 100))
            ch = c[3].number_input("Chatter if repeats above … %", 5, 100, int(S.chatter_repeat_frac * 100))
            st.markdown("#### Loggers & data")
            c = st.columns(4)
            lw = c[0].number_input("Logger stopped … min → check", 5, 1440, int(S.logger_warn_min))
            lc = c[1].number_input("Logger stopped … min → problem", 10, 2880, int(S.logger_crit_min))
            ow = c[2].number_input("Pen silent … min → check", 5, 1440, int(S.outage_warn_min))
            oc = c[3].number_input("Pen silent … min → problem", 10, 2880, int(S.outage_crit_min))
            c = st.columns(4)
            nw = c[0].number_input("Noise reads … % → check", 0.1, 50.0, float(S.noise_warn_pct), 0.1)
            nc = c[1].number_input("Noise reads … % → problem", 0.5, 90.0, float(S.noise_crit_pct), 0.5)
            sd = c[2].number_input("Warn if data older than … h", 1.0, 720.0, float(S.stale_data_h), 1.0)
            if st.form_submit_button("💾 Save settings", type="primary"):
                try:
                    S.pens = [int(x) for x in pens_txt.replace(";", ",").split(",") if x.strip()]
                except ValueError:
                    st.error("Pens must be numbers like 1, 2, 3, 4.")
                    st.stop()
                S.zones_per_level = {"lower": int(zl), "upper": int(zu)}
                S.pens_connected, S.lights_on_hour, S.lights_off_hour, S.reference_time = conn, int(lon), int(loff), ref
                S.mouse_warn_h, S.mouse_crit_h, S.stuck_warn_h, S.stuck_crit_h = mw, max(mc, mw), sw, max(sc, sw)
                S.zone_silent_warn_h, S.zone_silent_crit_h = zw, max(zc, zw)
                S.weak_share_ratio, S.chatter_repeat_frac = wk / 100, ch / 100
                S.logger_warn_min, S.logger_crit_min, S.outage_warn_min, S.outage_crit_min = lw, max(lc, lw), ow, max(oc, ow)
                S.noise_warn_pct, S.noise_crit_pct, S.stale_data_h = nw, max(nc, nw), sd
                save_settings(S)
                st.cache_data.clear()
                st.rerun()
        if st.button("↩ Restore all defaults"):
            keep = SETTINGS.data_dir
            d = Settings()
            d.data_dir = keep
            save_settings(d)
            st.cache_data.clear()
            st.rerun()


# ----------------------------------------------------------------------------- tab: help
with tab_help:
    with safe("Help"):
        st.markdown("""
### Every day (2 minutes)
1. Export the files from the logger computers into one folder.
2. Open **Start.bat**. The page opens in your browser.
3. Pick the folder (left panel) → read the colour of the big banner.
   - 🟢 **All clear** - done.
   - 🟠 **Check** - look at the cards under *Please check*.
   - 🔴 **Problem** - do the *What to do* steps on each red card first.
4. If you already know about an issue, press **Known issue / ignore** - it stays hidden until it gets worse.
5. **⬇ Report (HTML)** saves a one-page summary to email or print.

### Colours
| | meaning |
|---|---|
| 🟢 | normal |
| 🔵 | worth knowing, no action needed |
| 🟠 | please check today |
| 🔴 | something is probably broken or a mouse may be in trouble |

### Good to know
- A **quiet** mouse is not always a problem - mice sleep. The check compares each mouse with *its own* usual quiet spells and with its pen-mates.
- **"Stuck at one spot"** is the early-warning for a mouse that has died or lost its tag: it keeps being read by the same antenna.
- An antenna that reads **far less than the same antenna in other pens** may be faulty, or the pens may be set up differently on purpose.
- The first time, save the **Roster** so that a mouse that disappears from the data completely is still noticed.
- Nothing here changes your data files. Settings, roster and ignored issues are stored in the `state` folder.
- Command-line version (for a scheduled daily run): `python run_check.py "<data folder>" --html report.html`
""")
