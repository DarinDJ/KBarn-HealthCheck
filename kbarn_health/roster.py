"""The roster says which mice SHOULD be in the experiment, so a missing mouse can be noticed.

"Allowed pen(s)": one pen ("4") or several ("4,3" / "4;3" / "4 3") - the mouse may be in ANY of them.
Leave it empty and the pen is learned from the data.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .settings import STATE_DIR

COLUMNS = ["tag_no", "name", "home_pen", "expected", "notes"]


def parse_pens(value) -> list[int]:
    """'4,3' -> [4, 3]; 4 -> [4]; '4.0' -> [4]; '', None, NaN -> [];  junk is ignored, order is kept."""
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    pens: list[int] = []
    for tok in re.split(r"[,\s;/|]+", str(value).strip()):
        if not tok:
            continue
        try:
            n = int(float(tok))
        except ValueError:
            continue
        if n > 0 and n not in pens:
            pens.append(n)
    return pens


def pens_text(pens) -> str:
    return ",".join(str(p) for p in pens)


def roster_path() -> Path:
    return STATE_DIR / "roster.csv"


def empty_roster() -> pd.DataFrame:
    return pd.DataFrame({"tag_no": pd.Series(dtype="string"), "name": pd.Series(dtype="string"),
                         "home_pen": pd.Series(dtype="string"), "expected": pd.Series(dtype="bool"),
                         "notes": pd.Series(dtype="string")})


def clean_roster(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    df = df[COLUMNS]
    df["tag_no"] = df["tag_no"].astype("string").str.strip()
    df = df[df["tag_no"].notna() & (df["tag_no"] != "")].drop_duplicates("tag_no", keep="last")
    df["name"] = df["name"].astype("string").fillna("")
    df["notes"] = df["notes"].astype("string").fillna("")
    df["home_pen"] = df["home_pen"].map(lambda v: pens_text(parse_pens(v))).astype("string")
    df["expected"] = df["expected"].map(lambda v: True if pd.isna(v) else bool(v)).astype(bool)
    return df.sort_values("tag_no", key=lambda s: pd.to_numeric(s, errors="coerce")).reset_index(drop=True)


def load_roster(path: Path | None = None) -> pd.DataFrame:
    path = Path(path or roster_path())
    if not path.exists():
        return empty_roster()
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        if "expected" in raw:
            raw["expected"] = raw["expected"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
        return clean_roster(raw)
    except Exception:
        return empty_roster()


def save_roster(df: pd.DataFrame, path: Path | None = None) -> None:
    path = Path(path or roster_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_roster(df).to_csv(path, index=False)


def suggest_from_data(reads: pd.DataFrame, min_reads: int) -> pd.DataFrame:
    """Mice seen in the data with at least `min_reads` valid reads; pen = where they were read most."""
    v = reads[reads["valid"]]
    if v.empty:
        return empty_roster()
    counts = v.groupby("tag_no").size()
    tags = counts[counts >= min_reads].index
    home = v[v["tag_no"].isin(tags)].groupby("tag_no")["pen"].agg(lambda s: s.value_counts().idxmax())
    out = pd.DataFrame({"tag_no": tags.astype(str), "name": "", "home_pen": [str(int(p)) for p in home.reindex(tags).values],
                        "expected": True, "notes": ""})
    return clean_roster(out)
