# KBarn Health Check

A daily "is everything OK?" screen for the KBarn RFID mouse-tracking data. Drop in the exported
`.csv` files and it tells you, in plain language, **what is wrong and what to do about it**:

- 🐭 **Mice** - never read, not read for too long (judged against *that mouse's* normal quiet spells), stuck at one antenna for hours (possible dead mouse / fallen tag), much quieter than pen-mates, read in two pens at once, two mice sharing a tag number, unknown tags.
- 📡 **Antennas** - never read anything, read far less than the *same antenna in other pens*, silent for hours while the rest of the level is busy, "chattering", electrical noise.
- 💾 **Loggers** - missing/empty file, stopped recording before the export, a whole pen (or the whole system) silent while it shouldn't be, clock jumped back / ahead / reset.
- 🗂 **Data** - damaged files, noise rate, overlapping exports (merged automatically), data much older than today.

## Install on any Windows PC (no Python needed)

1. Copy **`KBarnHealthCheck-Windows.zip`** to the PC and **Extract All** (right-click) - e.g. to `C:\KBarnHealthCheck`.
2. Open the folder and double-click **`KBarnHealthCheck.exe`**. It opens in its own window. Make a desktop shortcut if you like.
   - Windows may say *"Windows protected your PC"* the first time (the program is not code-signed): click **More info → Run anyway**.
   - Needs Windows 10/11 (uses the Edge WebView2 that comes with Windows; if it is missing the app opens in the normal browser instead).
3. Settings, roster and ignored issues are saved per user in `%APPDATA%\KBarnHealthCheck`. If something goes wrong, `app.log` there says why.

Rebuild after changing the code: double-click `build_exe.bat` (on a PC that has Python), then re-zip `dist\KBarnHealthCheck`.
`Start.bat` is only the developer shortcut and does need Python.

## For the lab manager (2 minutes a day)

1. Export the files from the logger computers into one folder.
2. Open **KBarnHealthCheck.exe**.
3. Press **Browse…**, pick the folder. Read the banner:
   🟢 all clear · 🟠 please check · 🔴 problem - then follow the **What to do** steps on each card.
4. Already know about an issue? **Known issue / ignore** hides it until it gets *worse*.
5. **⬇ Report (HTML)** gives a one-page summary to email or print.

First time: open the **Roster** tab, check the list of mice and press **Save roster**. After that a mouse that
disappears from the data completely is still noticed.

## Command line (e.g. a scheduled daily run)

```
.venv\Scripts\python run_check.py "D:\rfid_exports" --html today.html
```
Exit code 0 = all clear, 1 = things to check, 2 = problems.

## How the alerts decide (so you can trust or tune them)

Thresholds adapt to the data instead of being fixed numbers:

| Check | Rule (defaults, all editable in **Settings**) |
|---|---|
| Mouse missing | not read for ≥ max(3 h, 1.5 × its own longest quiet spell) → check; ≥ max(8 h, 2.5 × …) → problem |
| Mouse stuck | still read, but only at one antenna for ≥ 4 h → check, ≥ 8 h → problem |
| Quieter than pen-mates | < 20 % of the pen-mates' median reads over the last 6 h |
| Antenna weak | < 15 % of the share of reads the same antenna gets in the other pens |
| Antenna silent | no read ≥ max(3 h, 2 × its own longest gap) **and** ≥ 10 reads would normally have arrived |
| Logger stopped | last read ≥ max(60 min, 1.25 × the level's own longest gap) before the export |
| Pen silent | all of a pen's readers silent ≥ 30 min while other pens recorded |
| Noise | ≥ 1 % of a file's rows are invalid tag IDs |

Calibrated on the first real dataset (8 files, 32 mice, ~20 h): zero false alarms on a healthy colony,
and every injected fault in `tests/` (dead / weak antenna, missing / stuck mouse, stopped logger, pen outage,
duplicate tag, clock reset, noise, missing file) is detected.

## Files

```
KBarnHealthCheck.exe     (built) the packaged app - needs nothing installed
launcher.py / build_exe.bat   window launcher / packaging script
Start.bat               developer shortcut (needs Python)
app/streamlit_app.py    the screen            app/charts.py   charts + sensor tile map
kbarn_health/           loader, checks, roster, settings, HTML report, CLI
tests/                  pytest suite + synthetic colony with injectable faults
state/                  your settings, roster and ignored issues (created automatically)
```

Run the tests: `.venv\Scripts\python -m pytest -q tests`

## Input format

`zone_label, uid, tag_no, timestamp` (e.g. `3, 000C1205607B, 161, 2026-09-30_19:19:12.819`).
File names must contain `PenN-lower` / `PenN-upper` (and ideally the export time, e.g. `Pen1-lower-20261001-152755-rfid.csv`);
the older `rfid-Pen1-lower-2026-07-19_14-16-51.csv` style also works.
