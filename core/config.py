"""Config loading: config.json <- config.local.json (deep merge) + .env secrets."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import sys

# ROOT: code + bundled defaults (read-only inside the desktop app bundle).
# DATA_DIR: everything writable (db, settings, your edited kols/narratives).
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
BUNDLED_DATA = ROOT / "data"
DATA_DIR = Path(os.getenv("TRENCH_DATA_DIR") or BUNDLED_DATA)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA = DATA_DIR
SECRETS_FILE = DATA_DIR / "settings.json"
SECRET_KEYS = ["HELIUS_API_KEY", "X_BEARER_TOKEN", "ANTHROPIC_API_KEY"]

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    load_dotenv(DATA_DIR / ".env")
except Exception:  # dotenv is optional
    pass


def load_settings() -> dict:
    """Keys/mode saved from the dashboard's Settings screen (data dir, never in git)."""
    try:
        return json.loads(SECRETS_FILE.read_text())
    except Exception:
        return {}


def save_settings(upd: dict):
    s = load_settings()
    for k, v in upd.items():
        if v is None:
            continue
        if v == "":
            s.pop(k, None)
            if k in SECRET_KEYS:
                os.environ.pop(k, None)
        else:
            s[k] = v
    SECRETS_FILE.write_text(json.dumps(s, indent=2))
    try:
        SECRETS_FILE.chmod(0o600)
    except Exception:
        pass
    _apply_settings()


def _apply_settings():
    for k in SECRET_KEYS:
        v = load_settings().get(k)
        if v:
            os.environ[k] = v


_apply_settings()


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """dict with dotted get: cfg.get_path("pipeline.runner_ath_usd")."""

    def get_path(self, path: str, default=None):
        cur = self
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def source(self, name: str) -> dict:
        return self.get("sources", {}).get(name, {})

    @property
    def demo(self) -> bool:
        return self.get("mode") == "demo"

    @property
    def db_path(self) -> Path:
        p = os.getenv("TRENCH_DB") or (self["demo_db_path"] if self.demo else self["db_path"])
        p = Path(p)
        if p.is_absolute():
            return p
        return DATA_DIR / p.name if p.parts[0] == "data" else ROOT / p


_CFG: Config | None = None


def load_config(mode: str | None = None, reload: bool = False) -> Config:
    global _CFG
    if _CFG is not None and not reload and mode is None:
        return _CFG
    cfg = json.loads((ROOT / "config.json").read_text())
    for local in (ROOT / "config.local.json", DATA_DIR / "config.local.json"):
        if local.exists():
            cfg = _deep_merge(cfg, json.loads(local.read_text()))
    cfg["mode"] = mode or os.getenv("TRENCH_MODE") or load_settings().get("mode") or cfg.get("mode") or "live"
    if cfg["mode"] == "demo":  # fake data is free: no caps, scan every minute
        cfg["scheduler"]["scan_every_min"] = cfg["sources"]["demo"].get("scan_every_min", 1)
        cfg["pipeline"].update(max_cheap_per_scan=100_000, max_deep_per_scan=2_000, cheap_scope="all")
    _CFG = Config(cfg)
    return _CFG


def get_config() -> Config:
    return _CFG or load_config()


def env(name: str) -> str | None:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else None


def load_json_data(name: str, default=None):
    """Your copy in the data dir wins; otherwise the bundled default."""
    for p in (DATA_DIR / name, BUNDLED_DATA / name):
        if p.exists():
            return json.loads(p.read_text())
    return default


def save_json_data(name: str, value):
    (DATA_DIR / name).write_text(json.dumps(value, indent=2))
