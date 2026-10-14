"""Multi-pen roster ("4,3"), pen moves, and the 'hide an issue' crash seen on the lab PC."""
import json

import pandas as pd
import pytest

from kbarn_health import roster as roster_mod
from kbarn_health import settings as settings_mod
from kbarn_health.checks import analyze
from kbarn_health.loader import load_folder
from kbarn_health.report import to_html
from kbarn_health.roster import parse_pens
from kbarn_health.settings import Settings, load_acks

from .synth import END, make_colony

S = Settings()
NOW = END + pd.Timedelta(minutes=5)


def roster(pens_for_401=None, **extra):
    tags = [str(t) for p in (1, 2, 3, 4) for t in range(p * 100, p * 100 + 8)]
    df = pd.DataFrame({"tag_no": tags, "home_pen": [t[0] for t in tags], "expected": True})
    if pens_for_401 is not None:
        df.loc[df.tag_no == "401", "home_pen"] = pens_for_401
    return df


def run(tmp_path, faults, ros):
    folder = make_colony(tmp_path / "d", faults=faults)
    return analyze(load_folder(folder, S), S, ros, clock_now=NOW)


def keys(rep, sev=None):
    return {f.key for f in rep.findings if sev is None or f.severity == sev}


@pytest.mark.parametrize("raw,expected", [("4,3", [4, 3]), ("4;3", [4, 3]), ("4 3", [4, 3]), (" 4 , 3 ", [4, 3]), (4, [4]), ("4.0", [4]),
                                          ("", []), (None, []), (float("nan"), []), ("x,2,,y", [2]), ("3,3,4", [3, 4]), ("0,-1", [])])
def test_parse_pens(raw, expected):
    assert parse_pens(raw) == expected


def test_moved_mouse_flagged_when_only_one_pen_allowed(tmp_path):
    assert "mouse_multipen|401" in keys(run(tmp_path, ("moved_mouse",), roster("4")))


def test_two_allowed_pens_clear_the_alert_and_log_the_move(tmp_path):
    rep = run(tmp_path, ("moved_mouse",), roster("4,2"))
    assert "mouse_multipen|401" not in keys(rep)
    assert "mouse_moved|401" in keys(rep, "info")
    row = rep.mice.set_index("tag_no").loc["401"]
    assert row["home_pen"] == "4,2" and row["current_pen"] == 2 and row["pens_allowed"] == [4, 2]
    assert "Pen 4 -> Pen 2" in next(f.detail for f in rep.findings if f.key == "mouse_moved|401")


def test_a_third_pen_is_still_flagged(tmp_path):
    assert "mouse_multipen|401" in keys(run(tmp_path, ("moved_mouse",), roster("4,3")))


def test_simultaneous_reads_in_two_allowed_pens_stay_critical(tmp_path):
    # 'multipen' copies mouse 401's reads into pen 2 at the SAME times: impossible even if both pens are allowed
    assert "mouse_multipen|401" in keys(run(tmp_path, ("multipen",), roster("4,2")), "critical")


def test_without_roster_pens_the_move_is_flagged_not_crashing(tmp_path):
    assert "mouse_multipen|401" in keys(run(tmp_path, ("moved_mouse",), None))


def test_roster_file_round_trip_keeps_multiple_pens(tmp_path):
    p = tmp_path / "roster.csv"
    roster_mod.save_roster(roster("4,3"), p)
    assert roster_mod.load_roster(p).set_index("tag_no").loc["401", "home_pen"] == "4,3"
    assert roster_mod.load_roster(p).set_index("tag_no").loc["101", "home_pen"] == "1"


@pytest.mark.parametrize("acks", [{}, None, set(), {"mouse_missing|105"}, {"mouse_missing|105": {"note": "ok <b>x</b>"}}, {"mouse_missing|105": "garbage"}, ["mouse_missing|105"]])
def test_html_report_accepts_any_form_of_hidden_issues(tmp_path, acks):
    """Regression: the lab PC crashed with "TypeError: 'set' object is not subscriptable" in to_html."""
    rep = run(tmp_path, ("missing_mouse",), None)
    html = to_html(rep, acks)
    assert "<html" in html
    if isinstance(acks, dict) and isinstance(acks.get("mouse_missing|105"), dict):
        assert "&lt;b&gt;x&lt;/b&gt;" in html          # note is escaped


def test_damaged_hidden_issues_file_is_ignored(tmp_path):
    p = tmp_path / "ack.json"
    p.write_text(json.dumps({"a": {"note": "n"}, "b": "bad", "c": 5}))
    assert load_acks(p) == {"a": {"note": "n"}}
    p.write_text("{not json")
    assert load_acks(p) == {}


def _app(folder):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"), default_timeout=180)
    at.session_state["folder_input"] = str(folder)
    return at


def test_app_survives_hiding_an_issue_and_reloading(tmp_path, monkeypatch):
    """The exact lab-PC scenario: press 'Hide this' on an issue, then the program must keep working - also after a restart."""
    state = tmp_path / "state"
    monkeypatch.setattr(settings_mod, "STATE_DIR", state)
    monkeypatch.setattr(roster_mod, "STATE_DIR", state)
    folder = make_colony(tmp_path / "d", faults=("missing_mouse", "dead_zone"))
    at = _app(folder).run()
    assert not at.exception, [e.value for e in at.exception]
    btn = [b for b in at.button if b.key == "ack_mouse_missing|105"]
    assert btn, "hide button not found"
    btn[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "mouse_missing|105" in json.loads((state / "acknowledged.json").read_text())
    # fresh start with the saved hidden-issue file (this is what crashed on every launch)
    at2 = _app(folder).run()
    assert not at2.exception, [e.value for e in at2.exception]
    assert any("Known issues" in (e.label or "") for e in at2.expander)


def test_app_survives_garbage_in_state_files(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "acknowledged.json").write_text('["a", "b"]')
    (state / "roster.csv").write_text("this,is\nnot,a roster")
    (state / "settings.yaml").write_text("pens: [oops")
    monkeypatch.setattr(settings_mod, "STATE_DIR", state)
    monkeypatch.setattr(roster_mod, "STATE_DIR", state)
    at = _app(make_colony(tmp_path / "d")).run()
    assert not at.exception, [e.value for e in at.exception]
