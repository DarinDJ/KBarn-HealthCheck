"""Headless run of the screen on a colony with many injected faults, then 'ignore' one issue."""
import sys, tempfile, io
from pathlib import Path
from streamlit.testing.v1 import AppTest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.synth import make_colony
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

d = make_colony(Path(tempfile.mkdtemp()) / "d", faults=("dead_zone", "missing_mouse", "stuck_mouse", "logger_stopped", "pen_outage", "multipen", "noise", "clock_back", "weak_zone"))
root = Path(__file__).resolve().parent.parent
at = AppTest.from_file(str(root / "app" / "streamlit_app.py"), default_timeout=180)
at.session_state["folder_input"] = str(d)
at.run()
print("exceptions:", [e.value for e in at.exception]); print("errors:", [e.value for e in at.error])
print([m.value[:100] for m in at.markdown if "banner" in m.value])
print("metrics:", [(m.label, m.value) for m in at.metric])
titles = [m.value.split("**")[1] for m in at.markdown if m.value.startswith("**") and "  \n" in m.value]
print(len(titles), "cards"); print("\n".join(titles))

btn = [b for b in at.button if b.key == "ack_mouse_missing|105"]
print("ack button found:", bool(btn))
