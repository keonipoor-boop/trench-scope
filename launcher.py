"""Desktop entry point (what the downloadable app runs).

Double-click -> starts the scanner + dashboard on this computer and opens your browser.
Your data, keys and settings live in your user folder, so updating the app never loses them:
  Windows: %APPDATA%\\TrenchScope   macOS: ~/Library/Application Support/TrenchScope   Linux: ~/.local/share/TrenchScope
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    d = base / "TrenchScope"
    d.mkdir(parents=True, exist_ok=True)
    return d


def already_running(port) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return b'"ok"' in r.read()
    except Exception:
        return False


def running_version(port):
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            return json.loads(r.read()).get("version")
    except Exception:
        return None


def my_version():
    import json
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    try:
        return json.loads((base / "version.json").read_text()).get("version")
    except Exception:
        return "dev"


def stop_old_copy(port) -> bool:
    """A different version is running (e.g. you just updated): ask it to quit so we can take over."""
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST"), timeout=3)
    except Exception:
        pass
    for _ in range(30):
        time.sleep(0.5)
        if not already_running(port):
            return True
    return False


def free_port(start=8000) -> int:
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def main():
    os.environ.setdefault("TRENCH_DATA_DIR", str(data_dir()))
    if already_running(8000):
        old = running_version(8000)
        if old == my_version():
            webbrowser.open("http://127.0.0.1:8000")  # this version is already running
            return
        print(f"  Replacing the running copy ({old or 'older version'}) with {my_version()}...")
        if not stop_old_copy(8000):
            print("  Old copy did not stop (it predates auto-replace). Starting on another port; close the old window when you can.")
    port = free_port(8000)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  TRENCH SCOPE is running at {url}\n  Close this window to stop it.\n  Data folder: {os.environ['TRENCH_DATA_DIR']}\n")

    from core.config import load_config
    load_config()
    from core.db import get_db
    from core.scheduler import Scheduler
    get_db()
    Scheduler().start_background()

    def open_when_ready():
        for _ in range(60):
            if already_running(port):
                webbrowser.open(url)
                return
            time.sleep(0.5)
    threading.Thread(target=open_when_ready, daemon=True).start()

    import uvicorn
    from api.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
