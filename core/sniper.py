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
    cols = {r["name"] for r in db.q("PRAGMA table_info(sniper_positions)")}
    for col in ("note TEXT", "user_tag TEXT", "entry_age_s INTEGER"):  # your journal + how old the coin was when we bought
        if col.split()[0] not in cols:
            db.x(f"ALTER TABLE sniper_positions ADD COLUMN {col}")
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
            r = random.Random(c["mint"])
            up = _demo_mcap(c["mint"], base, now + 3 * 3600) > m * 1.5  # demo: smart wallets tend to be early in winners
            early = [f"SMART{r.randint(0, 9)}" for _ in range(r.randint(2, 5) if up else (1 if r.random() < 0.15 else 0))] + \
                    [f"CROWD{r.randint(0, 400)}" for _ in range(r.randint(5, 15))]
            out[c["mint"]] = {"mcap": m, "liq": base * 0.2, "pair": full.get("pair_address"), "vol": m * 0.6,
                              "txns": int(m / 150), "buys": int(m / 260), "sells": int(m / 360), "dex": full.get("dex_id"), "early": early}
        return out
    # 1) the live trade stream (pushed, no polling) for coins we watch
    try:
        from sources import pumpportal
        book = pumpportal.BOOK
    except Exception:
        book = {}
    sol = _sol_usd()
    for c in coins:
        b = book.get(c["mint"])
        if b and b.get("mcap_sol") and time.time() - b.get("last_ts", 0) < 600:
            out[c["mint"]] = {"mcap": b["mcap_sol"] * sol, "liq": (b.get("liq_sol") or 0) * sol, "pair": None,
                              "vol": b["vol_sol"] * sol, "txns": b["buys"] + b["sells"], "buys": b["buys"], "sells": b["sells"],
                              "traders": len(b["traders"]), "dex": "pumpfun", "src": "stream", "early": list(b["early"])}
    # 2) DexScreener for the rest, chunks in parallel; cached 20s per coin so 1-second wake-ups never hit its rate limit
    now = time.time()
    for c in coins:
        hit = _PX_CACHE.get(c["mint"])
        if c["mint"] not in out and hit and now - hit[0] < 20:
            out[c["mint"]] = hit[1]
    mints = [c["mint"] for c in coins if c["mint"] not in out]
    chunks = [mints[i:i + 30] for i in range(0, len(mints), 30)]

    def fetch(ch):
        try:
            return http.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(ch), rate_key="dexscreener", per_min=55, retries=1)
        except Exception:
            return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(chunks)))) as ex:
        results = list(ex.map(fetch, chunks)) if chunks else []
    for pairs in results:
        for p in pairs if isinstance(pairs, list) else []:
            m = (p.get("baseToken") or {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m and (m not in out or liq > out[m]["liq"]):
                tx = (p.get("txns") or {}).get("h24") or {}
                out[m] = {"mcap": p.get("marketCap") or p.get("fdv"), "liq": liq, "pair": p.get("pairAddress"),
                          "vol": (p.get("volume") or {}).get("h24"), "txns": (tx.get("buys") or 0) + (tx.get("sells") or 0),
                          "buys": tx.get("buys"), "sells": tx.get("sells"), "dex": p.get("dexId")}
    for m in mints:
        if m in out:
            _PX_CACHE[m] = (now, out[m])
    if len(_PX_CACHE) > 5000:
        for k in sorted(_PX_CACHE, key=lambda k: _PX_CACHE[k][0])[:2000]:
            _PX_CACHE.pop(k, None)
    return {m: v for m, v in out.items() if v.get("mcap")}


_SOL = {"v": 150.0, "ts": 0.0}
_PX_CACHE: dict = {}  # mint -> (ts, price dict) from DexScreener


def _sol_usd() -> float:
    """SOL price, cached in memory for a minute (no DB read per coin)."""
    if time.time() - _SOL["ts"] > 60:
        _SOL.update(v=float(get_db().kv_get("sol_usd") or 150), ts=time.time())
    return _SOL["v"]


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
    from core import fastgate
    fastgate.ensure_fresh()
    pool, blocked = [], {}
    for x in rows:  # FAST GATE: deal-breakers from memory, before any price lookup
        if x["mint"] in held:
            continue
        ok, why = fastgate.check(x)
        if ok:
            pool.append(x)
        else:
            k = why.split(" (")[0]
            blocked[k] = blocked.get(k, 0) + 1
    _board["fastgate_blocked"] = blocked
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
        "KOLS": _kols_score(x, px),
        "DEV": 1.0 if x.get("_tracked_dev") else _dev_score(x),
        "HOLDERS": min(1.0, x["holders"] / max(30, age_min * 6)) if x.get("holders")
                   else (min(1.0, px["traders"] / max(15, age_min * 4)) if px.get("traders") else None),  # live unique traders
        "LIQUIDITY": min(1.0, (px.get("liq") or 0) / max(1, px["mcap"]) * 4) if px.get("liq") else None,
        "SOCIAL": min(1.0, ((1 if tw else 0) + (0.5 if x.get("website") else 0) + (x.get("x_mentions") or 0) / 20) / 1.5),
        "CHECKS": {True: 1.0, False: 0.0}.get(x.get("is_real")),
    }
    return {k: (round(v, 2) if v is not None else None) for k, v in ev.items()}


def _kols_score(x: dict, px: dict) -> float | None:
    """Tracked KOL buys + SMART wallets (learned: early in past runners) among the coin's early buyers."""
    n = len(x.get("kols") or [])
    try:
        from core import learning
        smart = learning.smart_set()
        n += sum(1 for w in (px.get("early") or []) if w in smart)
    except Exception:
        pass
    return min(1.0, n / 2) if n else None


def _dev_score(x: dict) -> float | None:
    """Precomputed dev record (core/fastgate, in memory): runners and REAL runners per launch."""
    from core import fastgate
    st = fastgate.devstats(x.get("creator") or x.get("dev_wallet"))
    if st and st["launches"] >= 2:
        return round(min(1.0, (st["real"] * 1.0 + st["runners"] * 0.5) / st["launches"] * 6), 2)
    if x.get("dev_launches"):
        return min(1.0, (x.get("dev_migrations") or 0) / x["dev_launches"] * 4)
    return None


_RULES = []


def _rules():
    if not _RULES:
        from catalysts.base import load_rules
        _RULES.extend(load_rules())
    return _RULES


def confidence(ev: dict) -> float:
    """Weighted mean of the measured evidence with the LIVE scorer version's weights (core/learning.py)."""
    try:
        from core import learning
        lw, _, _, _ = learning.active_weights()
        return learning.weighted_conf(ev, lw, MIN_MEASURED_WEIGHT)
    except Exception:
        pass
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
    if x.get("created_ts"):
        db.conn.execute("UPDATE sniper_positions SET entry_age_s=? WHERE id=?", (int(time.time() - x["created_ts"]), cur.lastrowid))
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


def _draft(side: str, coin: dict, extra: dict, key: str):
    try:
        from core import drafts
        drafts.make_async("sniper", {"side": side, "mint": coin.get("mint"), "symbol": coin.get("symbol"), "paper": True,
                                     "holding": side == "buy", **extra}, key=key)
    except Exception:
        pass


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
            last = (json.loads(p["exits"] or "[]") or [{}])[-1]
            _draft("sell", p, {"x": x_now, "pnl": p["realized_sol"], "reason": last.get("why", "")}, f"exit:{p['id']}")
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
    from core import perf
    open_pos = [dict(r) for r in db.q("SELECT * FROM sniper_positions WHERE status IN ('open','handoff')")]
    with perf.timer("sniper:watcher · candidates"):
        cands = candidates(c)
    emit("WATCHER", f"scanning {len(cands)} fresh launches · {len(open_pos)} open", "info", len(cands))
    try:  # stream live trades for fresh candidates + open positions (PumpPortal), instead of polling them
        from sources import pumpportal
        pumpportal.watch([x["mint"] for x in cands[:300]] + [p["mint"] for p in open_pos])
    except Exception:
        pass
    with perf.timer("sniper:watcher · prices"):
        px = prices(cands + open_pos)
    _record_ticks({m: v for m, v in px.items()})
    with db._lock:
        manage(c, open_pos, px)
    from core import axiom_filters, fastgate
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
        bl = sum(1 for w in (q.get("early") or []) if fastgate.is_blacklisted(w))
        if bl >= 3:  # FAST GATE (post-price): known scam wallets among the first buyers
            rejects["scam early buyers"] = rejects.get("scam early buyers", 0) + 1
            continue
        with perf.timer("sniper:analyst · evidence"):
            ev = evidence(x, q)
        perf.mark(x["mint"], "scored")
        scored.append({"mint": x["mint"], "symbol": x.get("symbol"), "name": x.get("name"), "image": x.get("image"),
                       "mcap": q["mcap"], "liq": q.get("liq"), "pair": q.get("pair"), "ev": ev, "conf": confidence(ev),
                       "age_min": round((time.time() - (x.get("created_ts") or time.time())) / 60), "_x": x, "_px": q})
    try:  # LEARNING: record what we saw (first decision per coin) + the shadow version's score
        from core import learning
        _, lvn, sw, svn = learning.active_weights()
        for s in scored:
            sc = learning.weighted_conf(s["ev"], sw, MIN_MEASURED_WEIGHT) if sw else None
            learning.record(s["mint"], s["symbol"], s["ev"], s["conf"], sc, s["mcap"], False, False, lvn, svn,
                            s["_x"].get("creator") or s["_x"].get("dev_wallet"), s["_px"].get("early"))
    except Exception as e:
        log("SCOUT", f"learning record: {e}", "warn")
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
        perf.mark(s["mint"], "gated")
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
        perf.mark(s["mint"], "filled")
        try:
            from core import learning
            learning.record(s["mint"], s["symbol"], s["ev"], s["conf"], None, s["mcap"], True, True, 0, None, None, None)
        except Exception:
            pass
        n_open += 1
        if c["mode"] == "dryflip":
            emit("HUMAN", f"HANDOFF #{pid} ${s['symbol']} · buy {c['size_sol']} SOL in DryFlip · TP {c['tp1_x']}x / SL -{c['stop_loss_pct']}%",
                 "handoff", s["conf"])
        else:
            emit("EXECUTOR", f"BUY {c['size_sol']} SOL ${s['symbol']} @ ${s['mcap'] / 1e3:,.1f}k · conf {s['conf']:.2f}", "fill", s["conf"])
        _draft("buy", s["_x"], {"mcap": s["mcap"], "why": _why(s["_x"], s["ev"]), "conf": s["conf"]}, f"snipe:{pid}")
        emit("REPORTER", f"position #{pid} ${s['symbol']} · evidence {_why(s['_x'], s['ev'])}", "info")
    ms = (time.monotonic() - t0) * 1000
    perf.record("sniper:tick · total", ms / 1000)
    with _lock:
        _board.update(candidates=[{k: v for k, v in s.items() if not k.startswith("_")} for s in scored[:8]],
                      focus={k: v for k, v in scored[0].items() if not k.startswith("_")} if scored else None)
        _board["tick_ms"].append(ms)
        _board["ticks"] += 1


_started = [False]
_wake = threading.Event()


def wake():
    """A new launch landed: tick now instead of waiting for the next interval."""
    _wake.set()


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
            _wake.wait(timeout=max(5, conf()["tick_s"]))
            if _wake.is_set():
                _wake.clear()
                time.sleep(1.0)  # debounce: a burst of launches = one tick
    try:
        from sources import pumpportal
        pumpportal.on_launch(wake)
    except Exception:
        pass
    t = threading.Thread(target=loop, daemon=True, name="sniper")
    t.start()
    return t


# ---------------------------------------------------------------- API helpers
def state(after: int = 0) -> dict:
    ensure_tables()
    c = conf()
    db = get_db()
    rows = [dict(r) for r in db.q("SELECT * FROM sniper_positions ORDER BY id DESC LIMIT 2000")]
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
        "readiness": readiness(closed, c["start_balance_sol"]),
        "fastgate": {**__import__("core.fastgate", fromlist=["stats"]).stats(), "blocked_last_tick": _board.get("fastgate_blocked", {})},
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
                "exposure_pct": round(exposure / equity * 100, 1) if equity else 0, "ticks": _board["ticks"], "decisions": _board["gate_checks"]},
    }


def readiness(closed: list[dict], start: float) -> dict:
    """LIVE READINESS 0.0-100: is this strategy's paper record strong enough to risk real money? Informational only:
    the bot has no wallet and never trades live. Score = weighted parts, then hard caps for thin or losing records."""
    from core.config import get_config
    n = len(closed)
    pnl = [r["realized_sol"] for r in sorted(closed, key=lambda r: r["closed_ts"] or 0)]
    size = statistics_mean([r["size_sol"] for r in closed]) or 1
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) else (9.9 if wins else 0)
    exp = sum(pnl) / n / size if n else 0                       # average return per trade, as a fraction of size
    eq, peak, mdd = start, start, 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak else 0)
    last = pnl[-20:]
    recent_exp = sum(last) / len(last) / size if last else 0
    span_d = (max(r["closed_ts"] or 0 for r in closed) - min(r["opened_ts"] for r in closed)) / 86400 if n else 0
    parts = {
        "sample": (min(1, n / 100), 25, f"{n} closed trades (100 = full credit)"),
        "edge": (max(0, min(1, exp / 0.25)), 25, f"{exp * 100:+.1f}% average return per trade"),
        "profit_factor": (max(0, min(1, (pf - 1) / 1)), 15, f"profit factor {pf:.2f} (2.0 = full credit)"),
        "drawdown": (max(0, min(1, (0.4 - mdd) / 0.25)), 15, f"max drawdown {mdd * 100:.1f}% (under 15% = full)"),
        "consistency": (max(0, min(1, recent_exp / max(exp, 0.01))) if exp > 0 else 0, 10, f"last 20 trades {recent_exp * 100:+.1f}% per trade"),
        "time": (min(1, span_d / 7), 10, f"{span_d:.1f} days of paper trading (7 = full)"),
    }
    score = sum(v * w for v, w, _ in parts.values())
    caps = []
    if get_config().demo:
        caps.append((15.0, "demo data: generated prices can't prove an edge"))
    if n < 30:
        caps.append((35.0, f"needs at least 30 closed trades ({30 - n} to go)"))
    if pf < 1.2:
        caps.append((30.0, f"profit factor {pf:.2f} is under 1.2: the strategy isn't clearly profitable"))
    if mdd > 0.35:
        caps.append((45.0, f"drawdown {mdd * 100:.0f}% is too deep"))
    for cap, _ in caps:
        score = min(score, cap)
    verdict = ("not ready" if score < 40 else "promising: keep paper trading" if score < 70
               else "ready for a small live test" if score < 85 else "ready")
    return {"score": round(score, 1), "verdict": verdict, "caps": [c[1] for c in caps],
            "parts": {k: {"score": round(v * 100), "weight": w, "note": t} for k, (v, w, t) in parts.items()}}


def statistics_mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


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


# ---------------------------------------------------------------- HISTORY: every trade, why, how it ended, what it did after
MISTAKES = {
    "sold_early": "Sold too early: after we exited it went at least 2x higher than our exit",
    "stopped_then_ran": "Stopped out, then it ran: hit the stop-loss and later reached 2x our entry",
    "gave_back": "Gave back gains: was up 1.5x+ during the trade but closed at a loss",
    "bought_rug": "Bought a rug / fake: the coin was later labelled rugged or fake",
    "thin_evidence": "Thin evidence: confidence barely cleared the gate and it lost",
    "late_entry": "Bought the top: it never traded above our entry",
    "stop_too_tight": "Stop too tight: stopped out, then it recovered above our entry",
}


def history(filt: str = "all", limit: int = 500) -> dict:
    """Every trade the sniper took (open + closed), with the evidence it bought on, the exits, what the coin did after
    (outcomes 1h / 24h / 7d), automatic mistake tags, and your own notes. Summary: win rate, avg win / loss, mistakes by type,
    and which evidence was higher in winners than in losers."""
    ensure_tables()
    c = conf()
    db = get_db()
    rows = [dict(r) for r in db.q("SELECT * FROM sniper_positions ORDER BY opened_ts DESC LIMIT ?", (limit,))]
    out_rows = {}
    try:
        from core import learning
        learning.ensure_tables()
        mints = [r["mint"] for r in rows]
        for i in range(0, len(mints), 400):
            ch = mints[i:i + 400]
            for o in db.q(f"SELECT mint, mcap0, h1, h24, d7, label FROM outcomes WHERE mint IN ({','.join('?' * len(ch))})", tuple(ch)):
                out_rows[o["mint"]] = dict(o)
    except Exception:
        pass
    items = []
    for r in rows:
        exits = json.loads(r["exits"] or "[]")
        ev = json.loads(r["evidence"] or "{}")
        o = out_rows.get(r["mint"]) or {}
        after = {}
        for cp in ("h1", "h24", "d7"):
            if o.get(cp):
                d = json.loads(o[cp])
                base = (o.get("mcap0") or r["entry_mcap"]) / r["entry_mcap"] if r["entry_mcap"] else 1  # outcomes are vs first-seen mcap
                after[cp] = {"x": round((d.get("x") or 0) * base, 3) if d.get("x") is not None else None,
                             "peak_x": round((d.get("peak_x") or 0) * base, 3) if d.get("peak_x") is not None else None}
        exit_x = exits[-1]["x"] if exits else None
        later_peak = max([a["peak_x"] or 0 for a in after.values()] + [0])
        closed = r["status"] == "closed"
        win = closed and (r["realized_sol"] or 0) > 0
        tags = []
        stopped = closed and any(e["why"].startswith(("stop-loss", "time stop")) for e in exits)
        if closed and exit_x and not stopped and later_peak >= exit_x * 2:
            tags.append("sold_early")
        if stopped and later_peak >= 2:
            tags.append("stopped_then_ran")
        elif stopped and later_peak > 1.0:
            tags.append("stop_too_tight")
        if closed and (r["peak_x"] or 1) >= 1.5 and (r["realized_sol"] or 0) < 0:
            tags.append("gave_back")
        if o.get("label") in ("rugged", "fake"):
            tags.append("bought_rug")
        if closed and not win and (r["conf"] or 0) < c["gate"] + 0.03:
            tags.append("thin_evidence")
        if closed and not win and (r["peak_x"] or 1) <= 1.02 and exits and exits[-1]["x"] < 0.8:
            tags.append("late_entry")
        if r.get("user_tag"):
            tags.append("yours:" + r["user_tag"])
        hold = ((r["closed_ts"] or time.time()) - r["opened_ts"])
        items.append({
            "id": r["id"], "mint": r["mint"], "symbol": r["symbol"], "name": r["name"], "image": r["image"], "mode": r["mode"],
            "status": r["status"], "opened_ts": r["opened_ts"], "closed_ts": r["closed_ts"], "hold_s": int(hold),
            "entry_mcap": r["entry_mcap"], "size_sol": r["size_sol"], "conf": r["conf"], "evidence": ev, "reason": r["reason"],
            "exits": exits, "realized_sol": round(r["realized_sol"] or 0, 4), "fees_sol": round(r["fees_sol"] or 0, 4),
            "peak_x": r["peak_x"], "last_x": r["last_x"], "exit_x": exit_x, "after": after, "label": o.get("label"),
            "roi_pct": round((r["realized_sol"] or 0) / r["size_sol"] * 100, 1) if r["size_sol"] else None,
            "tags": tags, "note": r.get("note"), "win": win, "entry_age_s": r.get("entry_age_s")})
    keep = {"all": lambda x: True, "open": lambda x: x["status"] != "closed", "wins": lambda x: x["win"],
            "losses": lambda x: x["status"] == "closed" and not x["win"], "mistakes": lambda x: bool(x["tags"]),
            "noted": lambda x: bool(x["note"])}.get(filt, lambda x: True)
    closed = [x for x in items if x["status"] == "closed"]
    wins = [x for x in closed if x["win"]]
    losses = [x for x in closed if not x["win"]]
    mist = {}
    for x in items:
        for t in x["tags"]:
            if not t.startswith("yours:"):
                mist[t] = mist.get(t, 0) + 1
    # which evidence separated winners from losers
    edge = []
    for k in EVIDENCE:
        w = [x["evidence"].get(k) for x in wins if x["evidence"].get(k) is not None]
        l_ = [x["evidence"].get(k) for x in losses if x["evidence"].get(k) is not None]
        if w and l_:
            edge.append({"feature": k, "winners": round(sum(w) / len(w), 2), "losers": round(sum(l_) / len(l_), 2),
                         "gap": round(sum(w) / len(w) - sum(l_) / len(l_), 2)})
    edge.sort(key=lambda e: -abs(e["gap"]))
    by_exit = {}
    for x in closed:
        why = (x["exits"][-1]["why"].split(" ")[0] if x["exits"] else "?")
        b = by_exit.setdefault(why, {"n": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] = round(b["pnl"] + x["realized_sol"], 4)
    return {"rows": [x for x in items if keep(x)], "mistake_labels": MISTAKES, "gate": c["gate"],
            "summary": {"trades": len(items), "open": len(items) - len(closed), "closed": len(closed), "wins": len(wins),
                        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
                        "avg_win_sol": round(sum(x["realized_sol"] for x in wins) / len(wins), 4) if wins else None,
                        "avg_loss_sol": round(sum(x["realized_sol"] for x in losses) / len(losses), 4) if losses else None,
                        "realized_sol": round(sum(x["realized_sol"] for x in closed), 4), "mistakes": mist, "evidence_edge": edge[:6],
                        "by_exit": by_exit}}


def set_note(pid: int, note: str | None = None, tag: str | None = None) -> bool:
    db = get_db()
    if not db.q("SELECT 1 FROM sniper_positions WHERE id=?", (pid,)):
        return False
    if note is not None:
        db.x("UPDATE sniper_positions SET note=? WHERE id=?", (note.strip()[:1000] or None, pid))
    if tag is not None:
        db.x("UPDATE sniper_positions SET user_tag=? WHERE id=?", (tag.strip()[:40] or None, pid))
    return True


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
