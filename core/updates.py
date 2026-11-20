"""Version info + "update available" check against the GitHub repo's latest release.
version.json is rewritten by the build workflow (version, repo, build time)."""
from __future__ import annotations

import json
import threading
import time

from core.config import ROOT

_state = {"checked": 0, "latest": None, "url": None, "error": None}
_lock = threading.Lock()


def info() -> dict:
    try:
        return json.loads((ROOT / "version.json").read_text())
    except Exception:
        return {"version": "dev", "repo": ""}


def _check(repo: str):
    from core import http
    try:
        r = http.get(f"https://api.github.com/repos/{repo}/releases/latest", retries=1,
                     headers={"Accept": "application/vnd.github+json"})
        _state.update(latest=r.get("tag_name"), url=r.get("html_url"), error=None)
    except Exception as e:
        _state["error"] = str(e)[:120]


def status() -> dict:
    v = info()
    repo = v.get("repo")
    with _lock:
        if repo and time.time() - _state["checked"] > 6 * 3600:
            _state["checked"] = time.time()
            threading.Thread(target=_check, args=(repo,), daemon=True).start()
    latest = _state["latest"]
    cur = v.get("version", "dev")
    return {**v, "latest": latest, "release_url": _state["url"],
            "update_available": bool(latest and cur not in ("dev", latest)),
            "hosted": bool(__import__("os").getenv("RENDER") or __import__("os").getenv("TRENCH_HOSTED"))}
