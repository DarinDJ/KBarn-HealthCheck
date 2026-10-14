"""Screenshot helper (dev only): python tests/shot.py <out.png> [tab name] [width] [height]"""
import sys
from playwright.sync_api import sync_playwright
out = sys.argv[1]; tab = sys.argv[2] if len(sys.argv) > 2 else None
w = int(sys.argv[3]) if len(sys.argv) > 3 else 1500; h = int(sys.argv[4]) if len(sys.argv) > 4 else 1100
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": w, "height": h}, color_scheme="light")
    pg.goto("http://localhost:8765"); pg.wait_for_selector("text=Mice reading normally", timeout=90000)
    if tab:
        pg.get_by_role("tab", name=tab).click(); pg.wait_for_timeout(3000)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=out, full_page=True); b.close()
