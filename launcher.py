"""KBarn Health Check launcher - the entry point of the packaged KBarnHealthCheck.exe (also works from source).

One program, three jobs, chosen by the first argument:
  (none)                       start the web server in a hidden child process and show the app in its own window
  --server <port> <parent-pid> run the Streamlit server (child process; exits when the window's process is gone)
  --pick-folder <start> <out>  show the 'choose folder' dialog and write the answer to <out>
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APPDATA = Path(os.environ.get("APPDATA", Path.home())) / "KBarnHealthCheck"
LOG = APPDATA / "app.log"
TITLE = "KBarn Health Check"
NO_WINDOW = 0x08000000   # subprocess.CREATE_NO_WINDOW


def log(msg: str) -> None:
    try:
        APPDATA.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def message_box(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, TITLE, 0x10)
    except Exception:
        print(text)


# ----------------------------------------------------------------------------- job 1: folder dialog
def pick_folder(start: str, out_file: str) -> None:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = filedialog.askdirectory(initialdir=start if Path(start).is_dir() else str(Path.home()),
                                     title="Choose the folder with the RFID .csv files")
    Path(out_file).write_text(chosen or "", encoding="utf-8")


# ----------------------------------------------------------------------------- job 2: the server
def _parent_alive(pid: int) -> bool:
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not handle:
        return False
    alive = ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258   # WAIT_TIMEOUT = still running
    ctypes.windll.kernel32.CloseHandle(handle)
    return alive


def run_server(port: int, parent_pid: int) -> None:
    def watchdog():            # never leave an invisible server running if the window was killed
        while _parent_alive(parent_pid):
            time.sleep(2)
        os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()
    if sys.stdout is None or sys.stderr is None:      # windowed exe has no console
        APPDATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    os.chdir(BASE)
    sys.path.insert(0, str(BASE))
    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", str(BASE / "app" / "streamlit_app.py"),
                "--server.port", str(port), "--server.address", "127.0.0.1", "--server.headless", "true",
                "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false",
                "--global.developmentMode", "false", "--theme.primaryColor", "#2a78d6"]
    sys.exit(stcli.main())


# ----------------------------------------------------------------------------- job 3: the window
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_ready(port: int, child: subprocess.Popen, timeout: float = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if child.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def show_window(url: str) -> None:
    try:
        import webview     # native window using the Edge WebView2 that ships with Windows 10/11
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(TITLE, url, width=1440, height=920, min_size=(1000, 650))
        APPDATA.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(APPDATA / "webview"))
        return
    except Exception as e:
        log(f"native window unavailable ({e!r}); falling back to the default browser")
    import webbrowser
    import tkinter as tk
    webbrowser.open(url)
    root = tk.Tk()
    root.title(TITLE)
    root.geometry("380x120")
    tk.Label(root, text="KBarn Health Check is open in your browser.\nClose this window to stop the program.",
             pady=18).pack()
    tk.Button(root, text="Stop program", command=root.destroy, width=16).pack()
    root.mainloop()


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--pick-folder"]:
        pick_folder(a[1], a[2])
        return 0
    if a[:1] == ["--server"]:
        run_server(int(a[1]), int(a[2]))
        return 0

    APPDATA.mkdir(parents=True, exist_ok=True)
    port = free_port()
    cmd = [sys.executable] + ([] if FROZEN else [str(Path(__file__).resolve())]) + ["--server", str(port), str(os.getpid())]
    logfile = open(LOG, "a", encoding="utf-8", buffering=1)
    child = subprocess.Popen(cmd, stdout=logfile, stderr=logfile, creationflags=NO_WINDOW if os.name == "nt" else 0)
    try:
        if not wait_until_ready(port, child):
            message_box(f"KBarn Health Check could not start.\n\nDetails were written to:\n{LOG}")
            return 1
        show_window(f"http://127.0.0.1:{port}")
    finally:
        child.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
