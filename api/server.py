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
from api.images import router as _images  # noqa: E402  (coin pictures, KOL pfps, source logos)
app.include_router(_images)
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
    return {"fake_headline": (c.get("fake") or {}).get("headline"), **{k: c.get(k) for k in ("mint", "name", "symbol", "image", "created_ts", "ath_mcap", "usd_market_cap",
                                  "ts_100k", "time_to_100k_min", "is_real", "primary_catalyst", "themes",
                                  "holders", "volume_24h", "price_change_24h")}}


# Timeframes for the dashboard's TF bar (seconds). Runners count in a window by the moment they
# crossed the runner bar (ts_100k, else launch time); launches by launch time.
TIMEFRAMES = {"7D": 7 * 86400, "3D": 3 * 86400, "24H": 86400, "12H": 43200, "6H": 21600, "4H": 14400,
              "1H": 3600}  # 1H is the shortest window


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


REAL_FILTERS = {"real": "is_real=1", "fake": "is_real=0", "pending": "is_real IS NULL", "all": "1=1"}


def tape(tf: str = "7D", limit=40, real: str = "all"):
    tf, w = _window(tf)
    now = time.time()
    lookback = get_config().get_path("pipeline.lookback_days", 7) * 86400
    rs = get_db().coins(f"is_runner=1 AND created_ts >= ? AND COALESCE(ts_100k, created_ts) >= ? AND {REAL_FILTERS.get(real, '1=1')}",
                        (int(now - max(lookback, w)), int(now - w)),
                        order="COALESCE(ts_100k, created_ts) DESC", limit=limit)
    return [slim(c) for c in rs]


def tape_counts(tf: str = "7D") -> dict:
    tf, w = _window(tf)
    now = time.time()
    lookback = get_config().get_path("pipeline.lookback_days", 7) * 86400
    r = get_db().q("""SELECT SUM(is_real=1) real, SUM(is_real=0) fake, SUM(is_real IS NULL) pending, COUNT(*) total FROM coins
                      WHERE is_runner=1 AND created_ts >= ? AND COALESCE(ts_100k, created_ts) >= ?""",
                   (int(now - max(lookback, w)), int(now - w)))[0]
    return {"real": r["real"] or 0, "fake": r["fake"] or 0, "pending": r["pending"] or 0, "all": r["total"] or 0}


def themes_live(tf: str = "24H"):
    """Theme stats for the window (vs a 3x baseline before it), plus a shorter 'tail' window
    (24H -> last 1H; shorter windows -> last 1/6th) for the rotation chart and the 1H-leader slot."""
    tf, w = _window(tf)
    wh = w / 3600
    tail_h = max(wh / 24 if w >= 86400 else wh / 6, 1 / 3600)
    # compare against the 3x window before it, but only as far back as data is kept (lookback + 4 days)
    kept_h = (get_config().get_path("pipeline.lookback_days", 7) + 4) * 24
    d = thesis.compute(wh, max(min(wh * 3, kept_h - wh), wh * 0.25))
    h = thesis.compute(tail_h, tail_h * 6)
    hmap = {r["theme"]: r for r in h["themes"]}
    rows = [{**r, "h_runners": hmap[r["theme"]]["runners"], "h_share": hmap[r["theme"]]["share"],
             "h_momentum": hmap[r["theme"]]["momentum"]} for r in d["themes"]]
    return {"tf": tf, "tail_s": int(tail_h * 3600), "daily": {k: v for k, v in d.items() if k != "themes"},
            "hourly_leader": h["leader"], "themes": rows}


def rows(sql, args=()):
    return [dict(r) for r in get_db().q(sql, args)]


@app.get("/api/state")
def state(tf: str = "7D", tape_filter: str = "real"):
    cfg = get_config()
    tf, w = _window(tf)
    ttl = 3 if w <= 300 else 10  # short windows refresh faster
    return {
        "mode": cfg["mode"], "now": time.time(),
        "last_scan": get_db().kv_get("last_scan"),
        "tf": tf, "timeframes": list(TIMEFRAMES),
        "summary": cached(f"summary:{tf}", ttl, lambda: summary(tf)),
        "tape": cached(f"tape:{tf}:{tape_filter}", min(ttl, 5), lambda: tape(tf, 40, tape_filter)),
        "tape_filter": tape_filter if tape_filter in REAL_FILTERS else "all",
        "tape_counts": cached(f"tapec:{tf}", min(ttl, 5), lambda: tape_counts(tf)),
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


@app.get("/api/coin/{mint}/x")
def coin_x(mint: str):
    """𝕏 tab: posts that boosted the coin (linked tweet, tweets in its description, X search), ranked."""
    from core import xposts
    c = get_db().get_coin(mint)
    if not c:
        raise HTTPException(404, "unknown mint")
    return xposts.posts_for(c)


@app.get("/api/coins")
def coins_list(tf: str = "7D", theme: str | None = None, catalyst: str | None = None, real: str | None = None,
               fail: str | None = None, kols: int = 0, sort: str = "ath", limit: int = 60):
    """Runners in a timeframe, filtered for the Synapse popovers.
    theme=<name>|Unthemed, catalyst=<key>, real=true|false|pending, fail=<signal key>|any, kols=1 (KOL-bought)."""
    tf, w = _window(tf)
    now = time.time()
    lookback = get_config().get_path("pipeline.lookback_days", 7) * 86400
    where, args = ["is_runner=1", "created_ts >= ?", "COALESCE(ts_100k, created_ts) >= ?"], [int(now - max(lookback, w)), int(now - w)]
    if theme == "Unthemed":
        where.append("(themes IS NULL OR themes='[]')")
    elif theme:
        where.append("themes LIKE ?")
        args.append(f'%{json.dumps(theme)}%')
    if catalyst:
        where.append("primary_catalyst = ?")
        args.append(catalyst)
    if real in ("true", "false"):
        where.append("is_real = ?")
        args.append(1 if real == "true" else 0)
    elif real == "pending":
        where.append("is_real IS NULL")
    order = {"ath": "ath_mcap DESC", "new": "COALESCE(ts_100k, created_ts) DESC",
             "holders": "CAST(json_extract(data,'$.holders') AS INTEGER) DESC"}.get(sort, "ath_mcap DESC")
    rows = get_db().coins(" AND ".join(where), tuple(args), order=order, limit=None if (fail or kols) else min(limit, 200))
    out = []
    for c in rows:
        fails = [{"key": k, "label": v.get("label"), "note": v.get("note"), "required": v.get("required")}
                 for k, v in (c.get("signals") or {}).items() if v.get("passed") is False]
        if fail and not any(f["key"] == fail or fail == "any" for f in fails):
            continue
        if kols and not c.get("kols"):
            continue
        out.append({**slim(c), "top10_pct": c.get("top10_pct"), "unique_traders": c.get("unique_traders"),
                    "fails": fails, "kols": [k.get("name") for k in (c.get("kols") or [])][:5]})
        if len(out) >= min(limit, 200):
            break
    return {"tf": tf, "count": len(out), "coins": out}


LEADER_PERIODS = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400}


@app.get("/api/leaders")
def leaders(period: str = "day", real_only: int = 0):
    """Runner of the day/week/month (by peak market cap), overall and per narrative. A coin counts for the
    period in which it crossed the runner bar (ts_100k, else launch)."""
    period = period if period in LEADER_PERIODS else "day"
    since = int(time.time() - LEADER_PERIODS[period])
    where = "is_runner=1 AND COALESCE(ts_100k, created_ts) >= ?" + (" AND is_real=1" if real_only else "")
    rows = get_db().coins(where, (since,), order="ath_mcap DESC", limit=5000)
    def card(c):
        return {**slim(c), "top10_pct": c.get("top10_pct"), "why": (c.get("why") or c.get("why_template") or [])[:3]}
    by = defaultdict(list)
    for c in rows:
        for t in (c.get("themes") or ["Unthemed"]):
            by[t].append(c)
    themes = sorted(({"theme": t, "runners": len(cs), "real": sum(1 for c in cs if c.get("is_real")),
                      "top": [card(c) for c in cs[:3]]} for t, cs in by.items()),
                    key=lambda x: -(x["top"][0]["ath_mcap"] or 0))
    first = get_db().q("SELECT MIN(created_ts) t FROM coins")[0]["t"]
    return {"period": period, "since": since, "count": len(rows), "overall": [card(c) for c in rows[:10]],
            "by_theme": themes, "tracking_since": get_db().kv_get("first_scan_ts") or first}


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


# ---- KOL LAB --------------------------------------------------------------------
import re as _re

_WALLET = _re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_kol_lock = threading.Lock()


def _kol_bg(name):
    from core import kol_research

    def go():
        with _kol_lock:
            kol_research.research(name)
    threading.Thread(target=go, daemon=True).start()


@app.get("/api/kols")
def kols_list():
    from core import kol_research
    out = []
    for k in kol_research.list_kols():
        p = kol_research.get_profile(k["name"]) or {}
        s = p.get("stats") or {}
        out.append({**k, "status": p.get("status") or "new", "ts": p.get("ts"), "style": p.get("style") or [],
                    "risk": p.get("risk") or [], "handle": kol_research.handle_of(k),
                    "pfp": f"/api/pfp/{kol_research.handle_of(k)}" if kol_research.handle_of(k) else None,
                    "summary": {x: s.get(x) for x in ("positions", "win_rate", "median_size_sol", "total_pnl_sol")},
                    "researched_ts": p.get("ts"), "periods": p.get("periods") or {},
                    "history_complete": (p.get("history") or {}).get("complete"),
                    "holdings": [{x: h.get(x) for x in ("mint", "symbol", "name", "image", "first_buy_ts", "invested_sol",
                                                         "unrealized_sol", "multiple", "entry_mcap", "mcap_now")}
                                 for h in (p.get("holdings") or [])][:8]})
    return out


_HANDLE = _re.compile(r"^[A-Za-z0-9_]{1,15}$")
@app.get("/api/playbook")
def playbook_get(tf: str = "7D"):
    """STRATEGY panel: the data-backed thesis (spot / buy / hold / cut)."""
    from core import playbook
    tf, w = _window(tf)
    return {"tf": tf, **playbook.compute(max(1, w / 86400))}


@app.get("/api/devs")
def devs_list(limit: int = 24):
    from core import devs
    return devs.devs_list(limit)


# ---- DEV LAB (paper deving: ideas are checked, nothing is launched) -----------------------------
@app.get("/api/devlab")
def devlab_state():
    from core import devlab
    return devlab.state()


@app.post("/api/devlab/refresh")
def devlab_refresh(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import devlab
    return {"made": devlab.generate(6), "resolved": devlab.resolve()}


@app.post("/api/devlab/ideas/{iid}/draft")
def devlab_draft(iid: int, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import devlab, drafts
    devlab.ensure_table()
    r = get_db().q("SELECT * FROM dev_ideas WHERE id=?", (iid,))
    if not r:
        raise HTTPException(404, "no such idea")
    i = dict(r[0])
    d = drafts.make("idea", {"parent": f"${i['parent_symbol']}", "name": i["name"], "ticker": i["ticker"], "keyword": i["keyword"],
                             "pitch": i["pitch"]}, manual=True)
    return d or {}


# ---- X DRAFTS (you post them; nothing is posted automatically) ---------------------------------
@app.get("/api/drafts")
def drafts_get(after: int = 0):
    from core import drafts
    return {"drafts": drafts.list_drafts(after), "settings": {**drafts.settings(), "discord_webhook": bool(drafts.settings()["discord_webhook"])},
            "triggers": drafts.TRIGGERS}


@app.post("/api/drafts/settings")
async def drafts_settings(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import drafts
    try:
        s = drafts.save_settings(await req.json())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**s, "discord_webhook": bool(s["discord_webhook"])}


@app.post("/api/drafts/{did}/{status}")
def drafts_status(did: int, status: str, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    if status not in ("posted", "dismissed", "new"):
        raise HTTPException(400, "posted | dismissed | new")
    from core import drafts
    drafts.set_status(did, status)
    return {"ok": True}


# ---- SNIPER DESK (paper trading) ---------------------------------------------------------
@app.get("/api/sniper")
def sniper_state(after: int = 0):
    from core import sniper
    return sniper.state(after)


@app.post("/api/sniper/settings")
async def sniper_settings(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import sniper
    return sniper.save_settings(await req.json())


@app.post("/api/sniper/handoff/{pid}/{action}")
def sniper_handoff(pid: int, action: str, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    if action not in ("done", "skip"):
        raise HTTPException(400, "done or skip")
    from core import sniper
    if not sniper.handoff_done(pid, action):
        raise HTTPException(404, "no such handoff")
    return {"ok": True}


@app.post("/api/sniper/filters")
async def sniper_filters(req: Request):
    """Paste an Axiom New Pairs filter preset ({"filters": {...}}); {"filters": {}} clears it."""
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import axiom_filters
    try:
        f = axiom_filters.save(await req.json())
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return axiom_filters.summary(f)


@app.post("/api/sniper/reset")
def sniper_reset(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import sniper
    sniper.reset()
    return {"ok": True}


@app.post("/api/devs")
async def dev_save(req: Request):
    """Add / edit a tracked dev: {name, twitter, wallets (list or text), old_name?}."""
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import devs
    b = await req.json()
    name = (b.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "name required")
    ws = b.get("wallets") or []
    if isinstance(ws, str):
        ws = _re.split(r"[\s,]+", ws)
    ws = list(dict.fromkeys(w.strip() for w in ws if w.strip()))
    bad = [w for w in ws if not _WALLET.match(w)]
    if bad:
        raise HTTPException(400, f"not a Solana address: {bad[0][:50]}")
    tw = (b.get("twitter") or "").strip().lstrip("@")
    if tw and not tw.startswith("http"):
        tw = f"https://x.com/{tw}"
    devs.save_tracked({"name": name, "twitter": tw or None, "wallets": ws, "old_name": b.get("old_name")})
    def go():
        for w in ws:
            try:
                devs.scan_dev(w)
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


@app.delete("/api/devs/{name}")
def dev_delete(name: str, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import devs
    devs.delete_tracked(name)
    return {"ok": True}


@app.get("/api/devs/{wallet}")
def dev_detail(wallet: str):
    from core import devs
    d = devs.dev_detail(wallet)
    if not d:
        raise HTTPException(404, "unknown dev")
    return d


@app.get("/api/strategies")
def strategies_list():
    from core import strategy, kol_research
    pf = {k["name"]: k for k in kol_research.list_kols()}
    out = strategy.library()
    for x in out:
        h = kol_research.handle_of(pf.get(x["name"]) or {})
        x["pfp"] = f"/api/pfp/{h}" if h else None
    return out


@app.get("/api/strategies/{name}")
def strategy_detail(name: str):
    from core import strategy
    v = strategy.versions(name)
    if not v:
        raise HTTPException(404, "no strategy yet")
    return {"name": name, "versions": v}


@app.get("/api/kols/{name}")
def kol_detail(name: str):
    from core import kol_research
    k = next((x for x in kol_research.list_kols() if x["name"] == name), None)
    if not k:
        raise HTTPException(404, "unknown KOL")
    return {"kol": k, "profile": kol_research.get_profile(name)}


@app.post("/api/kols")
async def kol_save(req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import kol_research
    b = await req.json()
    name = (b.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "name required")
    sides = b.get("side_wallets") or []
    if isinstance(sides, str):
        sides = _re.split(r"[\s,]+", sides)
    wallet = (b.get("wallet") or "").strip()
    sides = [w.strip() for w in sides if w.strip() and w.strip() != wallet]
    bad = [w for w in [wallet] + sides if w and not _WALLET.match(w)]
    if bad:
        raise HTTPException(400, f"not a Solana address: {bad[0][:50]}")
    if not wallet and not sides:
        raise HTTPException(400, "add at least one wallet")
    tw = (b.get("twitter") or "").strip().lstrip("@")
    if tw and not tw.startswith("http"):
        tw = f"https://x.com/{tw}"
    kol_research.save_kol({"name": name, "twitter": tw or None, "wallet": wallet or None,
                           "side_wallets": list(dict.fromkeys(sides))})
    _kol_bg(name)
    return {"ok": True, "researching": True}


@app.post("/api/kols/{name}/research")
def kol_research_now(name: str, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    _kol_bg(name)
    return {"ok": True}


@app.delete("/api/kols/{name}")
def kol_delete(name: str, req: Request):
    if not _can_edit(req):
        raise HTTPException(403, "wrong or missing admin password")
    from core import kol_research, strategy
    kol_research.delete_kol(name)
    strategy.delete(name)
    return {"ok": True}


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
