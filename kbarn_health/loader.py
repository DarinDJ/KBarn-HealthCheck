"""Read the raw RFID CSV exports into one tidy table + a per-file report card.

Raw row: zone_label, uid, tag_no, timestamp (e.g. 2026-09-30_19:19:03.690).
Nothing is silently thrown away: bad rows stay in the table with valid=False and a reason,
so the Data-quality checks can report on them.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .settings import Settings

REQUIRED = ["zone_label", "uid", "tag_no", "timestamp"]
PEN_LEVEL_RE = re.compile(r"pen\s*(\d+)[\s_\-]*(lower|upper)", re.I)
EXPORT_NEW_RE = re.compile(r"(\d{8})-(\d{6})")                       # Pen1-lower-20261001-152755-rfid.csv
EXPORT_OLD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")  # rfid-Pen1-lower-2026-07-19_14-16-51.csv
TS_FORMAT = "%Y-%m-%d_%H:%M:%S.%f"


@dataclass
class LoadResult:
    reads: pd.DataFrame
    files: pd.DataFrame
    duplicates_removed: int = 0
    problems: list = field(default_factory=list)   # (file, message) that could not be read at all


def parse_filename(name: str, parent: str = ""):
    m = PEN_LEVEL_RE.search(name) or PEN_LEVEL_RE.search(parent)
    pen, level = (int(m.group(1)), m.group(2).lower()) if m else (None, None)
    export = None
    m = EXPORT_NEW_RE.search(name)
    if m:
        export = pd.to_datetime(m.group(1) + m.group(2), format="%Y%m%d%H%M%S", errors="coerce")
    else:
        m = EXPORT_OLD_RE.search(name)
        if m:
            export = pd.to_datetime(m.group(1) + " " + m.group(2).replace("-", ":"), errors="coerce")
    return pen, level, (None if export is None or pd.isna(export) else export)


def find_csvs(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*.csv") if p.is_file())


def folder_signature(folder: str | Path) -> tuple:
    """Changes whenever a file is added / replaced - used to know when to re-read."""
    return tuple((str(p), p.stat().st_size, int(p.stat().st_mtime)) for p in find_csvs(folder))


def _read_one(name: str, source, parent: str, settings: Settings):
    pen, level, export = parse_filename(name, parent)
    info = dict(file=name, pen=pen, level=level, export_time=export, rows=0, bad_lines=0,
                missing_columns="", error="", backwards_jumps=0, first_read=pd.NaT, last_read=pd.NaT)
    bad_lines = []
    try:
        raw = pd.read_csv(source, dtype=str, keep_default_na=False, engine="python",
                          on_bad_lines=lambda row: bad_lines.append(row) or None)
    except pd.errors.EmptyDataError:
        info["error"] = "The file is empty (0 bytes or no header)."
        return info, None
    except Exception as e:  # unreadable / not a csv
        info["error"] = f"Could not read the file: {e}"
        return info, None
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    info["bad_lines"] = len(bad_lines)
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        info["missing_columns"] = ", ".join(missing)
        info["error"] = f"Missing column(s): {', '.join(missing)}. Is this really an RFID export?"
        return info, None

    df = raw[REQUIRED].copy()
    for c in REQUIRED:
        df[c] = df[c].astype(str).str.strip()
    df["row_no"] = range(len(df))
    df["file"] = name
    df["pen"] = pen
    df["level"] = level
    df["ts"] = pd.to_datetime(df["timestamp"], format=TS_FORMAT, errors="coerce")
    if df["ts"].isna().mean() > 0.5:   # different time format? try the flexible parser
        df["ts"] = pd.to_datetime(df["timestamp"].str.replace("_", " ", regex=False), errors="coerce")
    df["zone"] = pd.to_numeric(df["zone_label"].str.lower().str.replace("usb", "", regex=False), errors="coerce")
    info["rows"] = len(df)
    if df["ts"].notna().any():
        # clock going backwards in the file's own row order = logger clock reset / corruption
        info["backwards_jumps"] = int((df["ts"].dropna().diff().dt.total_seconds() < -60).sum())
    return info, df


def load_sources(sources, settings: Settings) -> LoadResult:
    """sources: iterable of (display_name, path-or-file-like, parent_folder_name)."""
    infos, frames = [], []
    for name, src, parent in sources:
        info, df = _read_one(name, src, parent, settings)
        infos.append(info)
        if df is not None and len(df):
            frames.append(df)
    files = pd.DataFrame(infos)
    if not frames:
        return LoadResult(_empty_reads(), files, 0, [(i["file"], i["error"]) for i in infos if i["error"]])

    reads = pd.concat(frames, ignore_index=True)
    before = len(reads)
    # repeated exports are cumulative: identical rows are the same physical read
    reads = reads.drop_duplicates(subset=["pen", "level", "zone_label", "uid", "tag_no", "timestamp"], keep="first")
    dupes = before - len(reads)

    uid_ok = reads["uid"].str.match(settings.valid_uid_regex)
    reason = pd.Series("", index=reads.index)
    reason[reads["tag_no"] == ""] = "no tag number"
    reason[~uid_ok] = "bad tag id"
    reason[reads["zone"].isna()] = "bad zone"
    reason[reads["ts"].isna()] = "bad timestamp"
    reads["noise_reason"] = reason
    reads["valid"] = reason == ""
    reads["tag_no"] = reads["tag_no"].where(reads["tag_no"] != "", None)
    reads["pen"] = reads["pen"].astype("Int64")
    reads["zone"] = reads["zone"].astype("Int64")
    reads = reads.sort_values(["ts", "pen", "level"], kind="stable").reset_index(drop=True)

    # per-file first/last valid read
    g = reads[reads["ts"].notna()].groupby("file")["ts"].agg(["min", "max"])
    files["first_read"] = files["file"].map(g["min"])
    files["last_read"] = files["file"].map(g["max"])
    files["noise_rows"] = files["file"].map(reads[~reads["valid"]].groupby("file").size()).fillna(0).astype(int)
    return LoadResult(reads, files, dupes, [(i["file"], i["error"]) for i in infos if i["error"]])


def load_folder(folder: str | Path, settings: Settings) -> LoadResult:
    paths = find_csvs(folder)
    return load_sources(((p.name, p, p.parent.name) for p in paths), settings)


def load_uploads(uploads, settings: Settings) -> LoadResult:
    """uploads: streamlit UploadedFile objects."""
    return load_sources(((u.name, io.BytesIO(u.getvalue()), "") for u in uploads), settings)


def _empty_reads() -> pd.DataFrame:
    cols = REQUIRED + ["row_no", "file", "pen", "level", "ts", "zone", "noise_reason", "valid"]
    return pd.DataFrame({c: pd.Series(dtype="object") for c in cols}).assign(valid=pd.Series(dtype=bool))
