"""FastAPI backend + static dashboard."""
from __future__ import annotations

import json
import statistics
import threading
import time
from collections import defaultdict
from pathlib import Path

import os
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core import pipeline, thesis
from core import llm
from core.config import ROOT, SECRET_KEYS, env, get_config, load_settings, save_settings
from core import updates
from core.db import get_db

WEB = ROOT / "web"
app = FastAPI(title="Trench Scope")
_job_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def slim(c: dict) -> dict:
    return {k: c.get(k) for k in ("mint", "name", "symbol", "image", "created_ts", "ath_mcap", "usd_market_cap",
                                  "ts_100k", "time_to_100k_min", "is_real", "primary_catalyst", "themes",
                                  "holders", "volume_24h", "price_change_24h")}


def summary():
    cfg = get_config()
    db = get_db()
    now = time.time()
    since = now - cfg.get_path("pipeline.lookback_days", 7) * 86400
    runners = db.coins("is_runner=1 AND created_ts >= ?", (int(since),), order="ath_mcap DESC")
    launches = db.q("SELECT COUNT(*) n FROM coins WHERE created_ts >= ?", (int(since),))[0]["n"]
    days = defaultdict(lambda: {"real": 0, "other": 0, "launches": 0})
    for r in db.q("SELECT created_ts FROM coins WHERE created_ts >= ?", (int(since),)):
        days[time.strftime("%m-%d", time.localtime(r["created_ts"]))]["launches"] += 1
    for c in runners:
        d = time.strftime("%m-%d", time.localtime(thesis.event_ts(c)))
        days[d]["real" if c.get("is_real") else "other"] += 1
    ttt = [c["time_to_100k_min"] for c in runners if c.get("time_to_100k_min") is not None]
    real = [c for c in runners if c.get("is_real")]
    order = [time.strftime("%m-%d", time.localtime(now - i * 86400)) for i in range(6, -1, -1)]
    return {
        "runners": len(runners), "launches": launches,
        "hit_rate": round(len(runners) / launches * 100, 2) if launches else 0,
        "real_pct": round(len(real) / len(runners) * 100) if runners else 0,
        "avg_ath": statistics.mean([c["ath_mcap"] for c in runners]) if runners else 0,
        "max_ath": runners[0]["ath_mcap"] if runners else 0,
        "median_ttt": statistics.median(ttt) if ttt else None,
        "per_day": [{"day": d, **days[d]} for d in order],
    }


def tape(limit=40):
    since = time.time() - get_config().get_path("pipeline.lookback_days", 7) * 86400
    rs = get_db().coins("is_runner=1 AND created_ts >= ?", (int(since),), order="COALESCE(ts_100k, created_ts) DESC",
                        limit=limit)
    return [slim(c) for c in rs]


def themes_live():
    tc = get_config().get("thesis", {})
    d = thesis.compute(tc.get("daily_window_h", 24), tc.get("daily_baseline_h", 72))
    h = thesis.compute(tc.get("hourly_window_h", 1), tc.get("hourly_baseline_h", 6))
    hmap = {r["theme"]: r for r in h["themes"]}
    rows = [{**r, "h_runners": hmap[r["theme"]]["runners"], "h_share": hmap[r["theme"]]["share"],
             "h_momentum": hmap[r["theme"]]["momentum"]} for r in d["themes"]]
    return {"daily": {k: v for k, v in d.items() if k != "themes"}, "hourly_leader": h["leader"], "themes": rows}


def rows(sql, args=()):
    return [dict(r) for r in get_db().q(sql, args)]


@app.get("/api/state")
def state():
    cfg = get_config()
    return {
        "mode": cfg["mode"], "now": time.time(),
        "last_scan": get_db().kv_get("last_scan"),
        "summary": cached("summary", 10, summary),
        "tape": cached("tape", 5, tape),
        "themes": cached("themes", 30, themes_live),
        "daily": thesis.latest("daily"),
        "hourly": thesis.latest("hourly"),
        "agents": rows("SELECT * FROM agents"),
        "sources": rows("SELECT * FROM source_status ORDER BY agent, name"),
        "busy": _job_lock.locked(),
    }


@app.get("/api/events")
def events(after: int = 0, limit: int = 120):
    if after:
        r = rows("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after, limit))
    else:
        r = list(reversed(rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))))
    for e in r:
        e.pop("data", None)
    return r


@app.get("/api/coin/{mint}")
def coin(mint: str):
    c = get_db().get_coin(mint)
    if not c:
        raise HTTPException(404, "unknown mint")
    c["snapshots"] = rows("SELECT ts, mcap, holders FROM snapshots WHERE mint=? ORDER BY ts", (mint,))
    return c


@app.get("/api/runners")
def runners(limit: int = 100):
    return tape(limit)


@app.get("/api/theses")
def theses(kind: str = "daily", limit: int = 20):
    out = rows("SELECT id, kind, ts, leader, meta_shift, text FROM theses WHERE kind=? ORDER BY ts DESC LIMIT ?", (kind, limit))
    return out


def _bg(fn):
    def go():
        with _job_lock:
            fn()
        _cache.clear()
    if _job_lock.locked():
        return {"started": False, "reason": "a job is already running"}
    threading.Thread(target=go, daemon=True).start()
    return {"started": True}


@app.post("/api/scan")
def trigger_scan():
    return _bg(pipeline.scan)


@app.post("/api/thesis/{kind}")
def trigger_thesis(kind: str):
    if kind not in ("daily", "hourly"):
        raise HTTPException(400, "kind must be daily or hourly")
    return _bg(lambda: thesis.generate(kind))


# ---- settings (keys + mode) ----------------------------------------------------
def _can_edit(req: Request) -> bool:
    """Desktop (localhost) can always edit. A hosted copy needs ADMIN_PASSWORD."""
    pw = os.getenv("ADMIN_PASSWORD")
    if pw:
        return req.headers.get("x-admin-password") == pw
    host = req.client.host if req.client else ""
    return host in ("127.0.0.1", "::1", "localhost") and not req.headers.get("x-forwarded-for")


@app.get("/api/settings")
def get_settings(req: Request):
    s = load_settings()
    return {
        "mode": get_config()["mode"],
        "keys": {k: bool(env(k)) for k in SECRET_KEYS},
        "editable": _can_edit(req),
        "needs_password": bool(os.getenv("ADMIN_PASSWORD")),
        "llm": llm.available(),
        "saved_mode": s.get("mode"),
    }


@app.post("/api/settings")
async def post_settings(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    body = await req.json()
    upd = {k: (body.get(k) or "").strip() for k in SECRET_KEYS if k in body}
    mode = body.get("mode")
    restart = mode in ("live", "demo") and mode != get_config()["mode"]
    if mode in ("live", "demo"):
        upd["mode"] = mode
    save_settings(upd)
    _cache.clear()
    if restart:  # mode switches database + sources: restart the process cleanly
        threading.Timer(0.5, _restart).start()
    return {"ok": True, "restarting": restart}


def _restart():
    args = sys.argv if getattr(sys, "frozen", False) else [sys.executable] + sys.argv
    os.execv(sys.executable, args)


@app.get("/api/version")
def version():
    return updates.status()


@app.post("/api/shutdown")
def shutdown(req: Request):
    """Used by a newer desktop launcher to replace an older copy. Localhost only, never hosted."""
    if os.getenv("TRENCH_HOSTED") or not _can_edit(req):
        raise HTTPException(403, "not allowed")
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "mode": get_config()["mode"], "last_scan": get_db().kv_get("last_scan")}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
