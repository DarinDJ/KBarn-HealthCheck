"""All tunable thresholds in one place. Times are in hours (h) or minutes (min)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# packaged .exe: the program folder may be read-only, so keep settings/roster in the user's AppData instead
STATE_DIR = (Path(os.environ.get("APPDATA", Path.home())) / "KBarnHealthCheck" / "state"
             if getattr(sys, "frozen", False) else ROOT / "state")


@dataclass
class Settings:
    # --- where the data is ---------------------------------------------------
    data_dir: str = ""
    # --- colony layout ---------------------------------------------------------
    pens: list = field(default_factory=lambda: [1, 2, 3, 4])
    levels: list = field(default_factory=lambda: ["lower", "upper"])
    zones_per_level: dict = field(default_factory=lambda: {"lower": 9, "upper": 4})
    pens_connected: bool = False      # False = a mouse can never legitimately appear in two pens
    lights_on_hour: int = 7
    lights_off_hour: int = 19
    # "export" = time written in the file names (best), "clock" = this computer's clock
    reference_time: str = "export"
    # --- mice ------------------------------------------------------------------------
    mouse_warn_h: float = 3.0          # not read for at least this long -> warning
    mouse_crit_h: float = 8.0          # ... -> critical
    mouse_adapt_warn: float = 1.5      # ...or this x the mouse's own longest quiet spell (whichever is larger)
    mouse_adapt_crit: float = 2.5
    stuck_warn_h: float = 4.0          # read only at ONE antenna for this long -> possible dead mouse / fallen tag
    stuck_crit_h: float = 8.0
    activity_window_h: float = 6.0     # compare the last N hours against pen-mates
    activity_drop_ratio: float = 0.2   # below 20% of pen-mates' median -> warning
    min_reads_real_mouse: int = 50     # fewer reads than this = stray tag, not a real mouse
    # --- sensors ----------------------------------------------------------------------------
    zone_silent_warn_h: float = 3.0
    zone_silent_crit_h: float = 8.0
    zone_adapt_warn: float = 2.0
    zone_adapt_crit: float = 3.0
    zone_min_expected_reads: float = 10.0   # only alarm if this many reads would normally have arrived in the quiet time
    weak_share_ratio: float = 0.15     # antenna gets < 15% of the share the same antenna gets in other pens
    chatter_repeat_frac: float = 0.4   # > 40% of reads are the same tag repeated within 0.5 s
    # --- loggers / files ------------------------------------------------------------------------
    logger_warn_min: float = 60.0      # no read at all on a level for this long at the end of the file
    logger_crit_min: float = 180.0
    logger_adapt_warn: float = 1.25
    logger_adapt_crit: float = 2.0
    outage_warn_min: float = 30.0      # whole pen silent while other pens were active
    outage_crit_min: float = 90.0
    stale_data_h: float = 6.0          # newest data older than this vs. computer clock
    noise_warn_pct: float = 1.0
    noise_crit_pct: float = 5.0
    # --- tags --------------------------------------------------------------------------------------
    valid_uid_regex: str = r"^000C12[0-9A-Fa-f]{6}$"

    def level_zones(self, level: str) -> list[int]:
        return list(range(1, int(self.zones_per_level.get(level, 0)) + 1))


def load_settings(path: Path | None = None) -> Settings:
    path = path or STATE_DIR / "settings.yaml"
    s = Settings()
    if Path(path).exists():
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            known = {f.name for f in fields(Settings)}
            for k, v in raw.items():
                if k in known:
                    setattr(s, k, v)
        except Exception:
            pass  # a damaged settings file must never stop the app: fall back to defaults
    return s


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = Path(path or STATE_DIR / "settings.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(asdict(s), sort_keys=False), encoding="utf-8")


# --- acknowledged ("known") issues ------------------------------------------------------------
def load_acks(path: Path | None = None) -> dict:
    path = Path(path or STATE_DIR / "acknowledged.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        # keep only well-formed entries: {issue-key: {"note":..., "date":..., "severity":...}}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    except Exception:
        return {}  # a damaged file must never stop the app


def save_acks(acks: dict, path: Path | None = None) -> None:
    path = Path(path or STATE_DIR / "acknowledged.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(acks, indent=2), encoding="utf-8")
