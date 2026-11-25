"""SNIPER DESK: an autonomous sniper that PAPER trades (no wallet, no real orders).

Six operators run every tick (`tick()`, every `sniper.tick_s`):
  WATCHER   picks candidates: fresh launches inside the entry market-cap band (plus tracked devs / KOL buys)
  ANALYST   scores each on an evidence vector (catalyst, KOLs, dev, momentum, holders, liquidity, social, checks)
  RISK      gates it: confidence >= gate, open slots, balance, cooldown, never a coin flagged fake
  EXECUTOR  fills a paper buy at the live price + slippage + fees (mode "auto"), or queues a HANDOFF (mode "dryflip")
  REPORTER  writes every step to the desk's live log
  HUMAN     handoffs: you mirror the trade in the DryFlip extension (Axiom / GMGN overlay) and mark it done
Open positions are managed every tick: take-profit ladder, trailing stop, stop-loss, time stop.

DryFlip has no API (it overlays buttons on the trading terminals), so the bot can't press its buttons: in "dryflip"
mode it hands you the trade with the exact size and TP/SL to type into DryFlip, and tracks the same trade itself
so both books can be compared. Prices: DexScreener (live) or a deterministic random walk (demo)."""
from __future__ import annotations

import json
import math
import random
import threading
import time
from collections import deque

from core import http
from core.config import get_config
from core.db import get_db
from core.events import log

OPERATORS = ["WATCHER", "ANALYST", "RISK", "EXECUTOR", "REPORTER", "HUMAN"]
EVIDENCE = ["FLOW", "MOMENTUM", "CATALYST", "KOLS", "DEV", "HOLDERS", "LIQUIDITY", "SOCIAL", "CHECKS"]
WEIGHTS = {"FLOW": 1.4, "MOMENTUM": 1.4, "CATALYST": 1.2, "KOLS": 1.3, "DEV": 0.9, "HOLDERS": 1.0, "LIQUIDITY": 0.7,
           "SOCIAL": 0.8, "CHECKS": 1.0}
MIN_MEASURED_WEIGHT = 3.5  # a coin needs at least this much measured evidence (e.g. flow + momentum + liquidity) to trade

_lock = threading.RLock()
_log: deque = deque(maxlen=400)
_seq = [0]
_ops = {o: {"status": "idle", "value": None, "spark": deque(maxlen=40), "ts": 0} for o in OPERATORS}
_board: dict = {"candidates": [], "focus": None, "tick_ms": deque(maxlen=60), "ticks": 0, "gate_checks": 0, "gate_pass": 0}


def conf() -> dict:
    c = {"enabled": True, "mode": "auto", "start_balance_sol": 10.0, "size_sol": 0.5, "max_open": 5, "gate": 0.72,
         "entry_min_mcap": 8_000, "entry_max_mcap": 120_000, "max_age_min": 120, "tp1_x": 2.0, "tp1_sell_pct": 50,
         "tp2_x": 4.0, "trail_pct": 35, "stop_loss_pct": 35, "max_hold_min": 240, "fee_pct": 1.0, "priority_sol": 0.001,
         "slippage_pct": 2.0, "cooldown_min": 120, "tick_s": 15, "terminal": "axiom"}
    c.update(get_config().get("sniper", {}) or {})
    saved = get_db().kv_get("sniper_settings") or {}
    c.update({k: v for k, v in saved.items() if k in c})
    return c


def ensure_tables():
    db = get_db()
    db.x("""CREATE TABLE IF NOT EXISTS sniper_positions (id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, symbol TEXT, name TEXT,
            image TEXT, pair TEXT, mode TEXT, opened_ts INTEGER, closed_ts INTEGER, status TEXT, entry_mcap REAL, size_sol REAL,
            remaining REAL, peak_x REAL, last_x REAL, realized_sol REAL, fees_sol REAL, conf REAL, evidence TEXT, reason TEXT,
            exits TEXT, handoff TEXT)""")
    db.x("CREATE TABLE IF NOT EXISTS sniper_ticks (mint TEXT, ts INTEGER, mcap REAL)")
    db.x("CREATE INDEX IF NOT EXISTS ix_sniper_ticks ON sniper_ticks(mint, ts)")


def emit(op: str, msg: str, kind: str = "info", value=None):
    """REPORTER: every operator step lands in the desk's live log."""
    with _lock:
        _seq[0] += 1
        _log.append({"id": _seq[0], "ts": time.time(), "op": op, "msg": msg, "kind": kind})
        o = _ops[op]
        o["status"], o["ts"] = kind, time.time()
        if value is not None:
            o["value"] = value
            o["spark"].append(value)


def balance() -> float:
    db = get_db()
    b = db.kv_get("sniper_balance")
    if b is None:
        b = conf()["start_balance_sol"]
        db.kv_set("sniper_balance", b)
    return float(b)


def _set_balance(v: float):
    get_db().kv_set("sniper_balance", round(v, 6))


# ---------------------------------------------------------------- prices
def _demo_mcap(mint: str, base: float, t: float) -> float:
    """Deterministic random walk around the coin's stored market cap (demo mode)."""
    seed = sum(ord(ch) for ch in mint)
    r = random.Random(seed)
    drift = r.uniform(-0.25, 0.45)
    vol = r.uniform(0.05, 0.22)
    k = t / 300  # 5-minute steps
    x = drift * math.sin(k / 7 + seed) + vol * math.sin(k * 1.7 + seed * 0.3) + 0.6 * vol * math.sin(k * 4.1 + seed)
    return max(1500.0, base * math.exp(x))


def prices(coins: list[dict]) -> dict[str, dict]:
    """{mint: {mcap, liq, pair}} for these coins, right now."""
    out = {}
    if get_config().demo:
        now, db = time.time(), get_db()
        for c in coins:  # same base for a coin whether it's a candidate or an open position
            full = c if c.get("_candidate") else (db.get_coin(c["mint"]) or {})
            base = min(full.get("usd_market_cap") or full.get("ath_mcap") or 30_000, 60_000)
            m = _demo_mcap(c["mint"], base, now)
            out[c["mint"]] = {"mcap": m, "liq": base * 0.2, "pair": full.get("pair_address"), "vol": m * 0.6,
                              "txns": int(m / 150), "buys": int(m / 260), "sells": int(m / 360), "dex": full.get("dex_id")}
        return out
    mints = [c["mint"] for c in coins]
    for i in range(0, len(mints), 30):
        try:
            pairs = http.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[i:i + 30]),
                             rate_key="dexscreener", per_min=55, retries=1)
        except Exception:
            continue
        for p in pairs if isinstance(pairs, list) else []:
            m = (p.get("baseToken") or {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m and (m not in out or liq > out[m]["liq"]):
                tx = (p.get("txns") or {}).get("h24") or {}
                out[m] = {"mcap": p.get("marketCap") or p.get("fdv"), "liq": liq, "pair": p.get("pairAddress"),
                          "vol": (p.get("volume") or {}).get("h24"), "txns": (tx.get("buys") or 0) + (tx.get("sells") or 0),
                          "buys": tx.get("buys"), "sells": tx.get("sells"), "dex": p.get("dexId")}
    return {m: v for m, v in out.items() if v.get("mcap")}


def _record_ticks(px: dict):
    db, now = get_db(), int(time.time())
    with db._lock:
        for m, p in px.items():
            db.conn.execute("INSERT INTO sniper_ticks(mint, ts, mcap) VALUES(?,?,?)", (m, now, p["mcap"]))
        db.conn.execute("DELETE FROM sniper_ticks WHERE ts < ?", (now - 4 * 3600,))
        db.conn.commit()


def _history(mint: str, since_s: int = 1800) -> list[tuple[int, float]]:
    return [(r["ts"], r["mcap"]) for r in get_db().q("SELECT ts, mcap FROM sniper_ticks WHERE mint=? AND ts>=? ORDER BY ts",
                                                     (mint, int(time.time()) - since_s))]


# ---------------------------------------------------------------- WATCHER + ANALYST
def candidates(c: dict) -> list[dict]:
    from core import axiom_filters
    db, now = get_db(), time.time()
    fw = axiom_filters.window_s(axiom_filters.load())  # the Axiom preset's max age wins (e.g. 4 minutes for New Pairs)
    window = 24 * 3600 if get_config().demo else (fw or c["max_age_min"] * 60)
    rows = db.coins("created_ts >= ?", (int(now - window),), order="created_ts DESC", limit=1500)
    from core import devs
    tracked = {w for t in devs.tracked() for w in t["wallets"]}
    held = {r["mint"] for r in db.q("SELECT mint FROM sniper_positions WHERE status IN ('open','handoff') OR closed_ts > ?",
                                     (int(now - c["cooldown_min"] * 60),))}

    def pre(x):  # cheap pre-score: what's worth a live price check
        s = 0
        s += 3 if (x.get("creator") or x.get("dev_wallet")) in tracked else 0
        s += 2 * len(x.get("kols") or [])
        s += 1 if x.get("twitter") or x.get("twitter_dex") else 0
        s += 1 if x.get("primary_catalyst") not in (None, "meme") else 0
        s += 1 if (x.get("holders") or 0) > 100 else 0
        return s
    pool = [x for x in rows if x["mint"] not in held and x.get("is_real") is not False]
    pool.sort(key=lambda x: (-pre(x), -(x.get("created_ts") or 0)))
    pool = pool[:90]
    for x in pool:
        x["_candidate"] = True
        x["_tracked_dev"] = (x.get("creator") or x.get("dev_wallet")) in tracked
    return pool


def evidence(x: dict, px: dict) -> dict:
    """Each feature 0..1, or None when it isn't measured yet (a 2-minute-old pair has no holder count or KOL buys).
    Unmeasured evidence is left out of the confidence, never scored as zero."""
    hist = _history(x["mint"], 900)
    mom = None
    if len(hist) >= 2 and hist[0][1]:
        ch = px["mcap"] / hist[0][1] - 1  # change since we first saw it (up to 15 min)
        mom = max(0.0, min(1.0, 0.5 + ch * 1.5))
    buys, sells = px.get("buys"), px.get("sells")
    flow = None
    if buys is not None and sells is not None and buys + sells >= 5:
        ratio = buys / (buys + sells)  # 0.5 = balanced, 0.7+ = strong buying
        age_min = max(1.0, (time.time() - (x.get("created_ts") or time.time())) / 60)
        pace = min(1.0, (buys + sells) / age_min / 20)  # trades per minute
        flow = max(0.0, min(1.0, (ratio - 0.4) / 0.35)) * 0.7 + pace * 0.3
    if not x.get("catalysts"):
        try:
            from catalysts.base import classify, load_rules
            _, x["catalysts"] = classify(x, _rules(), {"cfg": get_config(), "celebrities": set()})
        except Exception:
            pass
    cat = max([h.get("weight", 0) for h in x.get("catalysts") or [] if h.get("category") != "meme"] + [0])
    age_min = max(1, (time.time() - (x.get("created_ts") or time.time())) / 60)
    tw = x.get("twitter") or x.get("twitter_dex")
    ev = {
        "FLOW": flow,
        "MOMENTUM": mom,
        "CATALYST": min(1.0, cat / 85) if cat else (0.15 if x.get("catalysts") else None),
        "KOLS": min(1.0, len(x["kols"]) / 2) if x.get("kols") else None,
        "DEV": 1.0 if x.get("_tracked_dev") else (min(1.0, x["dev_migrations"] / x["dev_launches"] * 4) if x.get("dev_launches") else None),
        "HOLDERS": min(1.0, x["holders"] / max(30, age_min * 6)) if x.get("holders") else None,
        "LIQUIDITY": min(1.0, (px.get("liq") or 0) / max(1, px["mcap"]) * 4) if px.get("liq") else None,
        "SOCIAL": min(1.0, ((1 if tw else 0) + (0.5 if x.get("website") else 0) + (x.get("x_mentions") or 0) / 20) / 1.5),
        "CHECKS": {True: 1.0, False: 0.0}.get(x.get("is_real")),
    }
    return {k: (round(v, 2) if v is not None else None) for k, v in ev.items()}


_RULES = []


def _rules():
    if not _RULES:
        from catalysts.base import load_rules
        _RULES.extend(load_rules())
    return _RULES


def confidence(ev: dict) -> float:
    measured = {k: v for k, v in ev.items() if v is not None}
    w = sum(WEIGHTS[k] for k in measured)
    if not w:
        return 0.0
    conf_ = sum(v * WEIGHTS[k] for k, v in measured.items()) / w
    return round(conf_ * min(1.0, w / MIN_MEASURED_WEIGHT), 3)  # thin evidence is discounted


# ---------------------------------------------------------------- EXECUTOR
def _fill_cost(c: dict, sol: float) -> float:
    return sol * c["fee_pct"] / 100 + c["priority_sol"]


def term_url(c: dict, mint: str, pair: str | None) -> dict:
    return {"axiom": f"https://axiom.trade/meme/{pair or mint}", "gmgn": f"https://gmgn.ai/sol/token/{mint}",
            "dexscreener": f"https://dexscreener.com/solana/{pair or mint}"}


def open_position(c: dict, x: dict, px: dict, ev: dict, cf: float) -> int:
    db = get_db()
    size = min(c["size_sol"], balance())
    slip = c["slippage_pct"] / 100
    entry = px["mcap"] * (1 + slip)  # we pay up: effective entry market cap
    fees = _fill_cost(c, size)
    mode = c["mode"]
    handoff = None
    if mode == "dryflip":
        handoff = {"status": "pending", "created": int(time.time()), "urls": term_url(c, x["mint"], px.get("pair")),
                   "instructions": {"buy_sol": size, "take_profit_x": c["tp1_x"], "take_profit_2_x": c["tp2_x"],
                                    "stop_loss_pct": c["stop_loss_pct"], "slippage_pct": c["slippage_pct"]}}
    _set_balance(balance() - size - fees)
    with db._lock:
        return _insert(db, x, px, mode, entry, size, fees, cf, ev, handoff)


def _insert(db, x, px, mode, entry, size, fees, cf, ev, handoff) -> int:
    cur = db.conn.execute(
        """INSERT INTO sniper_positions(mint,symbol,name,image,pair,mode,opened_ts,status,entry_mcap,size_sol,remaining,peak_x,last_x,
           realized_sol,fees_sol,conf,evidence,reason,exits,handoff) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (x["mint"], x.get("symbol"), x.get("name"), x.get("image"), px.get("pair"), mode, int(time.time()),
         "handoff" if mode == "dryflip" else "open", entry, size, 1.0, 1.0, 1.0, 0.0, fees, cf, json.dumps(ev),
         _why(x, ev), "[]", json.dumps(handoff) if handoff else None))
    db.conn.commit()
    return cur.lastrowid


def _why(x: dict, ev: dict) -> str:
    top = sorted([kv for kv in ev.items() if kv[1] is not None], key=lambda kv: -kv[1] * WEIGHTS[kv[0]])[:3]
    return ", ".join(f"{k.lower()} {v:.2f}" for k, v in top)


def _sell(c: dict, p: dict, frac_of_remaining: float, x_now: float, why: str) -> float:
    """Sell a fraction of what's left at x_now (multiple of entry). Returns realized SOL (after fees + slippage)."""
    part = p["remaining"] * frac_of_remaining
    gross = p["size_sol"] * part * x_now * (1 - c["slippage_pct"] / 100)
    fees = _fill_cost(c, gross)
    proceeds = gross - fees
    pnl = proceeds - p["size_sol"] * part
    exits = json.loads(p["exits"] or "[]")
    exits.append({"ts": int(time.time()), "x": round(x_now, 3), "part": round(part, 3), "sol": round(proceeds, 4),
                  "pnl": round(pnl, 4), "why": why})
    p["remaining"] -= part
    p["realized_sol"] += pnl
    p["fees_sol"] += fees
    p["exits"] = json.dumps(exits)
    _set_balance(balance() + proceeds)
    return pnl


def manage(c: dict, open_pos: list[dict], px: dict):
    db = get_db()
    for p in open_pos:
        q = px.get(p["mint"])
        if not q:
            continue
        x_now = q["mcap"] / p["entry_mcap"]
        p["last_x"], p["peak_x"] = x_now, max(p["peak_x"] or 1, x_now)
        held_min = (time.time() - p["opened_ts"]) / 60
        tag = f"${p['symbol']} {x_now:.2f}x"
        if p["status"] == "handoff":  # waiting on you in DryFlip: the bot's own copy still runs, same rules
            pass
        exits = json.loads(p["exits"] or "[]")
        did_tp1 = any(e["why"].startswith("TP1") for e in exits)
        if not did_tp1 and x_now >= c["tp1_x"]:
            pnl = _sell(c, p, c["tp1_sell_pct"] / 100, x_now, f"TP1 {c['tp1_x']}x")
            emit("EXECUTOR", f"SELL {c['tp1_sell_pct']}% {tag} · TP1 · {pnl:+.3f} SOL", "fill", round(x_now, 2))
        elif x_now >= c["tp2_x"]:
            pnl = _sell(c, p, 1.0, x_now, f"TP2 {c['tp2_x']}x")
            emit("EXECUTOR", f"SELL rest {tag} · TP2 · {pnl:+.3f} SOL", "fill", round(x_now, 2))
        elif did_tp1 and x_now <= p["peak_x"] * (1 - c["trail_pct"] / 100):
            pnl = _sell(c, p, 1.0, x_now, f"trail -{c['trail_pct']}% from {p['peak_x']:.2f}x")
            emit("EXECUTOR", f"SELL rest {tag} · trailing stop · {pnl:+.3f} SOL", "fill", round(x_now, 2))
        elif x_now <= 1 - c["stop_loss_pct"] / 100:
            pnl = _sell(c, p, 1.0, x_now, f"stop-loss -{c['stop_loss_pct']}%")
            emit("RISK", f"STOP {tag} · cut at -{c['stop_loss_pct']}% · {pnl:+.3f} SOL", "warn", round(x_now, 2))
        elif held_min >= c["max_hold_min"]:
            pnl = _sell(c, p, 1.0, x_now, f"time stop {c['max_hold_min']}m")
            emit("RISK", f"TIME {tag} · held {held_min:.0f}m · {pnl:+.3f} SOL", "warn", round(x_now, 2))
        if p["remaining"] <= 1e-6:
            p["status"], p["closed_ts"] = "closed", int(time.time())
            emit("REPORTER", f"CLOSED ${p['symbol']} · realized {p['realized_sol']:+.3f} SOL", "info")
        db.conn.execute("UPDATE sniper_positions SET remaining=?, peak_x=?, last_x=?, realized_sol=?, fees_sol=?, exits=?, status=?, "
                        "closed_ts=? WHERE id=?", (p["remaining"], p["peak_x"], p["last_x"], p["realized_sol"], p["fees_sol"],
                                                   p["exits"], p["status"], p.get("closed_ts"), p["id"]))
    db.conn.commit()


# ---------------------------------------------------------------- the tick
_tick_lock = threading.Lock()


def tick():
    with _tick_lock:  # never two ticks at once (a manual tick + the worker)
        _tick()


def _tick():
    c = conf()
    if not c["enabled"]:
        _ops["WATCHER"]["status"] = "paused"
        return
    ensure_tables()
    t0 = time.monotonic()
    db = get_db()
    open_pos = [dict(r) for r in db.q("SELECT * FROM sniper_positions WHERE status IN ('open','handoff')")]
    cands = candidates(c)
    emit("WATCHER", f"scanning {len(cands)} fresh launches · {len(open_pos)} open", "info", len(cands))
    px = prices(cands + open_pos)
    _record_ticks({m: v for m, v in px.items()})
    with db._lock:
        manage(c, open_pos, px)
    from core import axiom_filters
    filt = axiom_filters.load()
    scored, rejects = [], {}
    for x in cands:
        q = px.get(x["mint"])
        if not q:
            continue
        ok, why = axiom_filters.check(x, q, filt) if filt else (True, None)
        if not ok:
            key = why.split(" ")[0]
            rejects[key] = rejects.get(key, 0) + 1
            continue
        if not (c["entry_min_mcap"] <= q["mcap"] <= c["entry_max_mcap"]):
            rejects["entry band"] = rejects.get("entry band", 0) + 1
            continue
        ev = evidence(x, q)
        scored.append({"mint": x["mint"], "symbol": x.get("symbol"), "name": x.get("name"), "image": x.get("image"),
                       "mcap": q["mcap"], "liq": q.get("liq"), "pair": q.get("pair"), "ev": ev, "conf": confidence(ev),
                       "age_min": round((time.time() - (x.get("created_ts") or time.time())) / 60), "_x": x, "_px": q})
    scored.sort(key=lambda s: -s["conf"])
    if filt:
        top_r = ", ".join(f"{k} {v}" for k, v in sorted(rejects.items(), key=lambda kv: -kv[1])[:3])
        emit("WATCHER", f"Axiom filters: {len(scored)} of {len(px)} priced pass" + (f" · out: {top_r}" if top_r else ""), "info", len(scored))
    if scored:
        top = scored[0]
        emit("ANALYST", f"${top['symbol']} conf {top['conf']:.2f} · {_why(top['_x'], top['ev'])}", "active", top["conf"])
    n_open = sum(1 for p in open_pos if p["status"] in ("open", "handoff") and p["remaining"] > 1e-6)
    for s in scored[:3]:
        _board["gate_checks"] += 1
        if s["conf"] < c["gate"]:
            emit("RISK", f"HOLD ${s['symbol']} · gate {s['conf']:.2f} < {c['gate']:.2f}", "hold", s["conf"])
            continue
        if n_open >= c["max_open"]:
            emit("RISK", f"HOLD ${s['symbol']} · {n_open}/{c['max_open']} slots used", "hold", s["conf"])
            break
        if balance() < c["size_sol"] + _fill_cost(c, c["size_sol"]):
            emit("RISK", f"HOLD ${s['symbol']} · balance {balance():.2f} SOL too low", "hold", s["conf"])
            break
        if get_db().q("SELECT 1 FROM sniper_positions WHERE mint=? AND status IN ('open','handoff')", (s["mint"],)):
            continue  # already in it
        _board["gate_pass"] += 1
        emit("RISK", f"PASS ${s['symbol']} · {s['conf']:.2f} ≥ {c['gate']:.2f} · limits ok", "pass", s["conf"])
        pid = open_position(c, s["_x"], s["_px"], s["ev"], s["conf"])
        n_open += 1
        if c["mode"] == "dryflip":
            emit("HUMAN", f"HANDOFF #{pid} ${s['symbol']} · buy {c['size_sol']} SOL in DryFlip · TP {c['tp1_x']}x / SL -{c['stop_loss_pct']}%",
                 "handoff", s["conf"])
        else:
            emit("EXECUTOR", f"BUY {c['size_sol']} SOL ${s['symbol']} @ ${s['mcap'] / 1e3:,.1f}k · conf {s['conf']:.2f}", "fill", s["conf"])
        emit("REPORTER", f"position #{pid} ${s['symbol']} · evidence {_why(s['_x'], s['ev'])}", "info")
    ms = (time.monotonic() - t0) * 1000
    with _lock:
        _board.update(candidates=[{k: v for k, v in s.items() if not k.startswith("_")} for s in scored[:8]],
                      focus={k: v for k, v in scored[0].items() if not k.startswith("_")} if scored else None)
        _board["tick_ms"].append(ms)
        _board["ticks"] += 1


_started = [False]


def start_worker():
    if _started[0]:  # one sniper loop per process: two would race and double-buy
        return None
    _started[0] = True

    def loop():
        time.sleep(10)
        while True:
            try:
                tick()
            except Exception as e:
                log("SCOUT", f"sniper: {e}", "warn")
                emit("REPORTER", f"tick failed: {e}", "warn")
            time.sleep(max(5, conf()["tick_s"]))
    t = threading.Thread(target=loop, daemon=True, name="sniper")
    t.start()
    return t


# ---------------------------------------------------------------- API helpers
def state(after: int = 0) -> dict:
    ensure_tables()
    c = conf()
    db = get_db()
    rows = [dict(r) for r in db.q("SELECT * FROM sniper_positions ORDER BY id DESC LIMIT 200")]
    for r in rows:
        r["exits"] = json.loads(r["exits"] or "[]")
        r["evidence"] = json.loads(r["evidence"] or "{}")
        r["handoff"] = json.loads(r["handoff"]) if r["handoff"] else None
        r["value_sol"] = r["size_sol"] * r["remaining"] * (r["last_x"] or 1)
        r["unrealized_sol"] = r["value_sol"] - r["size_sol"] * r["remaining"] if r["status"] != "closed" else 0
    open_ = [r for r in rows if r["status"] != "closed"]
    closed = [r for r in rows if r["status"] == "closed"]
    wins = [r for r in closed if r["realized_sol"] > 0]
    equity = balance() + sum(r["value_sol"] for r in open_)
    exposure = sum(r["size_sol"] * r["remaining"] for r in open_)
    ms = sorted(_board["tick_ms"])
    with _lock:
        ops = {o: {"status": v["status"], "value": v["value"], "spark": list(v["spark"]), "ts": v["ts"]} for o, v in _ops.items()}
        logs = [e for e in _log if e["id"] > after]
    pnl_hist = []
    run = 0.0
    for r in sorted(closed, key=lambda r: r["closed_ts"] or 0):
        run += r["realized_sol"]
        pnl_hist.append([r["closed_ts"], round(run, 4)])
    return {
        "filters": __import__("core.axiom_filters", fromlist=["summary"]).summary(__import__("core.axiom_filters", fromlist=["load"]).load()),
        "settings": c, "operators": ops, "log": logs[-120:], "candidates": _board["candidates"], "focus": _board["focus"],
        "evidence_keys": EVIDENCE, "positions": open_, "closed": closed[:40], "pnl_series": pnl_hist,
        "handoffs": [r for r in open_ if r["handoff"] and r["handoff"].get("status") == "pending"],
        "kpi": {"balance_sol": round(balance(), 4), "equity_sol": round(equity, 4), "start_sol": c["start_balance_sol"],
                "pnl_sol": round(equity - c["start_balance_sol"], 4),
                "realized_sol": round(sum(r["realized_sol"] for r in rows), 4),
                "fees_sol": round(sum(r["fees_sol"] for r in rows), 4), "trades": len(rows), "open": len(open_),
                "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
                "gate_pass_pct": round(_board["gate_pass"] / _board["gate_checks"] * 100, 1) if _board["gate_checks"] else None,
                "tick_ms": round(ms[len(ms) // 2]) if ms else None, "exposure_sol": round(exposure, 4),
                "exposure_pct": round(exposure / equity * 100, 1) if equity else 0, "ticks": _board["ticks"]},
    }


def save_settings(upd: dict) -> dict:
    cur = conf()
    clean = {}
    for k, v in upd.items():
        if k not in cur or k == "start_balance_sol":
            continue
        if isinstance(cur[k], bool):
            clean[k] = bool(v)
        elif isinstance(cur[k], (int, float)):
            clean[k] = type(cur[k])(float(v))
        elif k == "mode" and v in ("auto", "dryflip"):
            clean[k] = v
        elif k == "terminal" and v in ("axiom", "gmgn", "dexscreener"):
            clean[k] = v
    db = get_db()
    db.kv_set("sniper_settings", {**(db.kv_get("sniper_settings") or {}), **clean})
    emit("HUMAN", "settings: " + ", ".join(f"{k}={v}" for k, v in clean.items()), "info")
    return conf()


def handoff_done(pid: int, action: str) -> bool:
    """You mirrored the trade in DryFlip ('done') or passed on it ('skip': the bot closes its copy at cost)."""
    db = get_db()
    r = db.q("SELECT * FROM sniper_positions WHERE id=?", (pid,))
    if not r or not r[0]["handoff"]:
        return False
    p = dict(r[0])
    h = json.loads(p["handoff"])
    h["status"], h["acted"] = ("done" if action == "done" else "skipped"), int(time.time())
    status = "open" if action == "done" else "closed"
    if action != "done":  # refund: you didn't take it
        _set_balance(balance() + p["size_sol"] * p["remaining"] + (p["fees_sol"] or 0))
        p["remaining"], p["fees_sol"] = 0, 0
    db.x("UPDATE sniper_positions SET handoff=?, status=?, remaining=?, fees_sol=?, closed_ts=? WHERE id=?",
         (json.dumps(h), status, p["remaining"], p["fees_sol"], int(time.time()) if status == "closed" else None, pid))
    emit("HUMAN", f"#{pid} ${p['symbol']} · {'mirrored in DryFlip' if action == 'done' else 'skipped'}", "handoff")
    return True


def reset():
    ensure_tables()
    db = get_db()
    db.x("DELETE FROM sniper_positions")
    db.kv_set("sniper_balance", conf()["start_balance_sol"])
    with _lock:
        _log.clear()
        _board.update(gate_checks=0, gate_pass=0, ticks=0)
    emit("HUMAN", f"desk reset · balance {conf()['start_balance_sol']} SOL", "info")
