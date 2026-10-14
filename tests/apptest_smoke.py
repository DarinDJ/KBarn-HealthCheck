"""Headless smoke test of the Streamlit screen: python tests/apptest_smoke.py <folder with RFID .csv files>"""
import sys
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
if len(sys.argv) < 2:
    sys.exit("usage: python tests/apptest_smoke.py <folder with RFID .csv files>")
folder = sys.argv[1]
at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=180)
at.session_state["folder_input"] = folder
t = time.time()
at.run()
print("run seconds:", round(time.time() - t, 1))
print("exceptions:", [e.value for e in at.exception])
print("errors:", [e.value for e in at.error])
print("metrics:", [(m.label, m.value) for m in at.metric])
print("banner:", [m.value[:140] for m in at.markdown if "banner" in m.value])
print("dataframes:", len(at.dataframe), "tabs:", len(at.tabs))
