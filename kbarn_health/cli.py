from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checks import ICON, analyze
from .loader import load_folder
from .report import to_html
from .roster import load_roster
from .settings import load_acks, load_settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KBarn Health Check - exit code 0 = all clear, 1 = things to check, 2 = problems")
    ap.add_argument("folder", nargs="?", help="folder with the exported RFID .csv files (default: the one saved in settings)")
    ap.add_argument("--html", help="also write an HTML report to this path")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # Windows consoles choke on emoji otherwise
    s = load_settings()
    folder = a.folder or s.data_dir
    if not folder or not Path(folder).is_dir():
        print("Data folder not found. Pass it as the first argument.")
        return 3
    rep = analyze(load_folder(folder, s), s, load_roster())
    acks = load_acks()
    sev = rep.by_severity(acks)
    for level in ("critical", "warning", "info"):
        for f in sev[level]:
            print(f"{ICON[level]} [{f.category}] {f.title}")
    print(f"\n{len(sev['critical'])} problem(s), {len(sev['warning'])} to check, {len(sev['info'])} note(s)")
    if a.html:
        Path(a.html).write_text(to_html(rep, acks), encoding="utf-8")
        print("Report written to", a.html)
    return 2 if sev["critical"] else 1 if sev["warning"] else 0
