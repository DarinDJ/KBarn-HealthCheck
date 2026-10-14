import pandas as pd
import pytest

from kbarn_health.checks import analyze
from kbarn_health.loader import load_folder
from kbarn_health.settings import Settings

from .synth import END, make_colony

S = Settings()
NOW = END + pd.Timedelta(minutes=5)


def run(tmp_path, *faults, **kw):
    folder = make_colony(tmp_path / "d", faults=faults, **kw)
    return analyze(load_folder(folder, S), S, None, clock_now=NOW)


def keys(rep, severity=None):
    return {f.key for f in rep.findings if severity is None or f.severity == severity}


def has(rep, prefix, severity=None):
    return any(k.startswith(prefix) for k in keys(rep, severity))


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    return run(tmp_path_factory.mktemp("healthy"))


def test_healthy_colony_is_all_clear(healthy):
    bad = [f.title for f in healthy.findings if f.severity in ("critical", "warning")]
    assert bad == []
    assert healthy.summary["mice_expected"] == 32 and healthy.summary["zones_ok"] == healthy.summary["zones_total"]


def test_dead_antenna(tmp_path):
    assert "zone_dead|2|upper|3" in keys(run(tmp_path, "dead_zone"), "critical")


def test_weak_antenna_vs_other_pens(tmp_path):
    assert has(run(tmp_path, "weak_zone"), "zone_weak|3|lower|5")


def test_missing_mouse(tmp_path):
    rep = run(tmp_path, "missing_mouse")
    assert "mouse_missing|105" in keys(rep, "critical")
    m = rep.mice.set_index("tag_no").loc["105"]
    assert 9.5 < m["hours_since"] < 10.5 and m["status"] == "critical"


def test_stuck_mouse(tmp_path):
    assert "mouse_stuck|106" in keys(run(tmp_path, "stuck_mouse"), "critical")


def test_logger_stopped(tmp_path):
    assert "logger_stale|3|lower" in keys(run(tmp_path, "logger_stopped"), "critical")


def test_pen_outage_detected_and_not_blamed_on_other_pens(tmp_path):
    rep = run(tmp_path, "pen_outage")
    assert has(rep, "outage_pen|1|", "critical")
    assert not has(rep, "outage_pen|2|") and not has(rep, "outage_all")


def test_mouse_in_two_pens(tmp_path):
    assert "mouse_multipen|401" in keys(run(tmp_path, "multipen"))


def test_noisy_file(tmp_path):
    assert has(run(tmp_path, "noise"), "noise_file|Pen4-upper")


def test_clock_goes_backwards(tmp_path):
    assert has(run(tmp_path, "clock_back"), "file_clockback|Pen2-lower")


def test_missing_file(tmp_path):
    assert "level_nofile|4|lower" in keys(run(tmp_path, "missing_file"), "critical")


def test_roster_catches_mouse_that_never_appears(tmp_path):
    folder = make_colony(tmp_path / "d", faults=("never_seen",))
    roster = pd.DataFrame({"tag_no": [str(t) for p in (1, 2, 3, 4) for t in range(p * 100, p * 100 + 8)], "expected": True})
    rep = analyze(load_folder(folder, S), S, roster, clock_now=NOW)
    assert "mouse_never|107" in keys(rep, "critical")
    assert not rep.roster_learned


def test_unknown_tag_flagged_when_roster_exists(tmp_path):
    folder = make_colony(tmp_path / "d")
    roster = pd.DataFrame({"tag_no": [str(t) for p in (1, 2, 3, 4) for t in range(p * 100, p * 100 + 7)], "expected": True})
    assert "mouse_unknown|107" in keys(analyze(load_folder(folder, S), S, roster, clock_now=NOW), "warning")


def test_removed_mouse_is_ignored(tmp_path):
    folder = make_colony(tmp_path / "d", faults=("missing_mouse",))
    roster = pd.DataFrame({"tag_no": [str(t) for p in (1, 2, 3, 4) for t in range(p * 100, p * 100 + 8)], "expected": True})
    roster.loc[roster.tag_no == "105", "expected"] = False
    rep = analyze(load_folder(folder, S), S, roster, clock_now=NOW)
    assert not has(rep, "mouse_missing|105")


def test_old_export_warns(tmp_path):
    folder = make_colony(tmp_path / "d")
    rep = analyze(load_folder(folder, S), S, None, clock_now=END + pd.Timedelta(days=3))
    assert "data_old" in keys(rep, "warning")


def test_empty_folder_is_a_clear_problem(tmp_path):
    (tmp_path / "e").mkdir()
    rep = analyze(load_folder(tmp_path / "e", S), S, None, clock_now=NOW)
    assert "data_empty" in keys(rep, "critical")


def test_overlapping_exports_are_deduplicated(tmp_path):
    folder = make_colony(tmp_path / "d")
    src = next(folder.glob("Pen1-lower*.csv"))
    (folder / "Pen1-lower-20260930-120000-rfid.csv").write_text(src.read_text())   # same reads again
    load = load_folder(folder, S)
    assert load.duplicates_removed > 1000
