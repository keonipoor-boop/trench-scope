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


# Timeframes for the dashboard's TF bar (seconds). Runners count in a window by the moment they
# crossed the runner bar (ts_100k, else launch time); launches by launch time.
TIMEFRAMES = {"7D": 7 * 86400, "3D": 3 * 86400, "24H": 86400, "12H": 43200, "6H": 21600, "4H": 14400,
              "1H": 3600, "30M": 1800, "15M": 900, "5M": 300, "1M": 60, "30S": 30, "15S": 15, "1S": 1}


def _window(tf: str | None) -> tuple[str, int]:
    tf = (tf or "7D").upper()
    return (tf, TIMEFRAMES[tf]) if tf in TIMEFRAMES else ("7D", TIMEFRAMES["7D"])


def _bins(now: float, w: int) -> list[tuple[float, float, str]]:
    n = 7 if w >= 7 * 86400 else 6 if w >= 3 * 86400 else 12
    size = w / n
    out = []
    for i in range(n):
        a = now - w + i * size
        if size >= 86400:
            lbl = time.strftime("%m-%d", time.localtime(a))
        elif size >= 60:
            lbl = time.strftime("%H:%M", time.localtime(a))
        elif size >= 1:
            lbl = f"-{int(now - a)}s"
        else:
            lbl = f"-{(now - a) * 1000:.0f}ms"
        out.append((a, a + size, lbl))
    return out


def _window_coins(w: int):
    """(runners crossing inside the window, launches inside the window)"""
    db, now = get_db(), time.time()
    lookback = get_config().get_path("pipeline.lookback_days", 7) * 86400
    coins = db.light_coins(int(now - max(lookback, w)))
    start = now - w
    runners = [c for c in coins if c.get("is_runner") and start <= thesis.event_ts(c) <= now]
    launches = [c for c in coins if start <= (c.get("created_ts") or 0) <= now]
    return runners, launches


def summary(tf: str = "7D"):
    tf, w = _window(tf)
    now = time.time()
    runners, launches = _window_coins(w)
    runners.sort(key=lambda c: -(c.get("ath_mcap") or 0))
    bins = []
    for a, b, lbl in _bins(now, w):
        rs = [c for c in runners if a <= thesis.event_ts(c) < b or (b >= now and thesis.event_ts(c) == now)]
        bins.append({"day": lbl, "real": sum(1 for c in rs if c.get("is_real")),
                     "other": sum(1 for c in rs if not c.get("is_real")),
                     "launches": sum(1 for c in launches if a <= (c.get("created_ts") or 0) < b)})
    ttt = [c["time_to_100k_min"] for c in runners if c.get("time_to_100k_min") is not None]
    real = [c for c in runners if c.get("is_real")]
    return {
        "tf": tf, "window_s": w,
        "runners": len(runners), "launches": len(launches),
        "hit_rate": round(len(runners) / len(launches) * 100, 2) if launches else 0,
        "real_pct": round(len(real) / len(runners) * 100) if runners else 0,
        "avg_ath": statistics.mean([c["ath_mcap"] for c in runners]) if runners else 0,
        "max_ath": runners[0]["ath_mcap"] if runners else 0,
        "median_ttt": statistics.median(ttt) if ttt else None,
        "per_day": bins,
    }


def tape(tf: str = "7D", limit=40):
    tf, w = _window(tf)
    now = time.time()
    lookback = get_config().get_path("pipeline.lookback_days", 7) * 86400
    rs = get_db().coins("is_runner=1 AND created_ts >= ? AND COALESCE(ts_100k, created_ts) >= ?",
                        (int(now - max(lookback, w)), int(now - w)),
                        order="COALESCE(ts_100k, created_ts) DESC", limit=limit)
    return [slim(c) for c in rs]


def themes_live(tf: str = "24H"):
    """Theme stats for the window (vs a 3x baseline before it), plus a shorter 'tail' window
    (24H -> last 1H; shorter windows -> last 1/6th) for the rotation chart and the 1H-leader slot."""
    tf, w = _window(tf)
    wh = w / 3600
    tail_h = max(wh / 24 if w >= 86400 else wh / 6, 1 / 3600)
    d = thesis.compute(wh, wh * 3)
    h = thesis.compute(tail_h, tail_h * 6)
    hmap = {r["theme"]: r for r in h["themes"]}
    rows = [{**r, "h_runners": hmap[r["theme"]]["runners"], "h_share": hmap[r["theme"]]["share"],
             "h_momentum": hmap[r["theme"]]["momentum"]} for r in d["themes"]]
    return {"tf": tf, "tail_s": int(tail_h * 3600), "daily": {k: v for k, v in d.items() if k != "themes"},
            "hourly_leader": h["leader"], "themes": rows}


def rows(sql, args=()):
    return [dict(r) for r in get_db().q(sql, args)]


@app.get("/api/state")
def state(tf: str = "7D"):
    cfg = get_config()
    tf, w = _window(tf)
    ttl = 3 if w <= 300 else 10  # short windows refresh faster
    return {
        "mode": cfg["mode"], "now": time.time(),
        "last_scan": get_db().kv_get("last_scan"),
        "tf": tf, "timeframes": list(TIMEFRAMES),
        "summary": cached(f"summary:{tf}", ttl, lambda: summary(tf)),
        "tape": cached(f"tape:{tf}", min(ttl, 5), lambda: tape(tf)),
        "themes": cached(f"themes:{tf}", ttl if w <= 300 else 30, lambda: themes_live(tf)),
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
def runners(tf: str = "7D", limit: int = 100):
    return tape(tf, limit)


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
