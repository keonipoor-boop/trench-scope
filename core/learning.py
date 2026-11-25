"""ALWAYS LEARNING. The sniper's scoring improves itself from what actually happened, under your rules.

OUTCOMES   every coin the sniper scores is recorded once, with the exact evidence it saw and its confidence. At 1h, 24h
           and 7d we check what became of it: x vs the price we saw (now + peak) and a label:
             ran (peak >= 2x or crossed $100k) · rugged (down 90% within the hour) · fake (failed the REAL checks)
             died (under 0.5x) · flat
SCORER     a logistic model learns which evidence predicts "ran". Its positive weights become the sniper's evidence
           weights (same 0-1 confidence and the same gate you set). Each retrain is a new VERSION that first runs in
           SHADOW next to the live one, scoring the same coins; it's promoted only if it ranks outcomes better
           (AUC +0.02 or more, enough samples, hit rate above the gate no worse). Old versions are kept: roll back any time.
GUARDS     if the live version's recent hit rate drops well below what it was promoted on -> roll back to the previous
           good version AND pause the sniper, and alert you. Meta shift (recent hit rate falls) -> retrain + alert.
LISTS      rugs / fakes blacklist their dev (and repeat early buyers); wallets that keep buying early into runners become
           SMART wallets (dropped when they stop performing); devs with repeat REAL runners become auto-tracked.
NEVER      changes your risk limits: trade size, stop-loss / loss cap, max positions, the gate you set, or the kill switch.
Every automatic change goes to the change log (`changes` table) and the weekly recap."""
from __future__ import annotations

import json
import math
import random
import threading
import time

from core.config import get_config
from core.db import get_db
from core.events import log

FEATURES = ["FLOW", "MOMENTUM", "CATALYST", "KOLS", "DEV", "HOLDERS", "LIQUIDITY", "SOCIAL", "CHECKS"]
CHECKPOINTS = [("h1", 3600), ("h24", 86400), ("d7", 7 * 86400)]
RISK_KEYS = {"size_sol", "max_open", "stop_loss_pct", "max_hold_min", "enabled", "gate", "start_balance_sol"}  # never touched
_lock = threading.RLock()


def conf() -> dict:
    c = {"min_samples": 60, "promote_auc_gain": 0.02, "retrain_every_h": 24, "retrain_min_new": 40, "drift_window": 40,
         "drift_drop": 0.5, "ran_x": 2.0, "smart_min_hits": 3, "smart_min_rate": 0.35, "smart_drop_rate": 0.12}
    c.update(get_config().get("learning", {}) or {})
    return c


def ensure_tables():
    db = get_db()
    db.x("""CREATE TABLE IF NOT EXISTS outcomes (mint TEXT PRIMARY KEY, symbol TEXT, decided_ts INTEGER, mcap0 REAL, features TEXT,
            conf REAL, shadow_conf REAL, passed INTEGER, acted INTEGER, version INTEGER, shadow_version INTEGER, dev TEXT, early TEXT,
            h1 TEXT, h24 TEXT, d7 TEXT, label TEXT, label_ts INTEGER)""")
    db.x("CREATE INDEX IF NOT EXISTS ix_outcomes_ts ON outcomes(decided_ts)")
    db.x("CREATE INDEX IF NOT EXISTS ix_outcomes_label ON outcomes(label)")
    db.x("""CREATE TABLE IF NOT EXISTS scorer_versions (version INTEGER PRIMARY KEY, ts INTEGER, status TEXT, weights TEXT, bias REAL,
            metrics TEXT, note TEXT, promoted_ts INTEGER, retired_ts INTEGER)""")
    db.x("CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, summary TEXT, detail TEXT)")
    db.x("""CREATE TABLE IF NOT EXISTS smart_wallets (wallet TEXT PRIMARY KEY, kind TEXT, status TEXT, hits INTEGER, seen INTEGER,
            added_ts INTEGER, dropped_ts INTEGER, note TEXT)""")
    if not db.q("SELECT 1 FROM scorer_versions"):
        from core import sniper
        db.x("INSERT INTO scorer_versions(version,ts,status,weights,bias,metrics,note,promoted_ts) VALUES(?,?,?,?,?,?,?,?)",
             (1, int(time.time()), "live", json.dumps(dict(sniper.WEIGHTS)), 0.0, json.dumps({}), "hand-set starting weights", int(time.time())))


def change(kind: str, summary: str, detail: dict | None = None):
    get_db().x("INSERT INTO changes(ts,kind,summary,detail) VALUES(?,?,?,?)", (int(time.time()), kind, summary, json.dumps(detail or {}, default=str)))
    log("THESIS", f"LEARNING · {summary}")


def alert(msg: str):
    db = get_db()
    a = (db.kv_get("learning_alerts", []) or [])[-19:] + [{"ts": int(time.time()), "msg": msg}]
    db.kv_set("learning_alerts", a)
    log("THESIS", f"⚠ LEARNING ALERT · {msg}", "warn")
    try:  # same notification path as X drafts (toast + browser + your Discord webhook)
        from core import drafts
        s = drafts.settings()
        if s.get("discord_webhook"):
            from core import http
            http.post(s["discord_webhook"], json={"content": f"**Trench Scope alert:** {msg}"[:1900]}, rate_key="discord_hook", per_min=20, retries=1)
    except Exception:
        pass


# ---------------------------------------------------------------- versions / scoring
def versions() -> list[dict]:
    ensure_tables()
    return [{**dict(r), "weights": json.loads(r["weights"]), "metrics": json.loads(r["metrics"] or "{}")}
            for r in get_db().q("SELECT * FROM scorer_versions ORDER BY version DESC")]


def live() -> dict:
    v = [x for x in versions() if x["status"] == "live"]
    return v[0] if v else versions()[-1]


def shadow() -> dict | None:
    v = [x for x in versions() if x["status"] == "shadow"]
    return v[0] if v else None


_cache = {"ts": 0.0, "live": None, "shadow": None}


def active_weights() -> tuple[dict, int, dict | None, int | None]:
    """(live weights, live version, shadow weights, shadow version), cached 30s (hot path: no DB per coin)."""
    if time.time() - _cache["ts"] > 30:
        ensure_tables()
        lv, sh = live(), shadow()
        _cache.update(ts=time.time(), live=(lv["weights"], lv["version"]), shadow=(sh["weights"], sh["version"]) if sh else None)
    lw, lvn = _cache["live"]
    sw, svn = _cache["shadow"] or (None, None)
    return lw, lvn, sw, svn


def weighted_conf(ev: dict, w: dict, min_weight: float) -> float:
    measured = {k: v for k, v in ev.items() if v is not None and k in w}
    tot = sum(w[k] for k in measured)
    if not tot:
        return 0.0
    c = sum(v * w[k] for k, v in measured.items()) / tot
    return round(c * min(1.0, tot / (min_weight * sum(w.values()) / 9.7)), 3)


# ---------------------------------------------------------------- outcomes
def record(mint: str, symbol: str | None, ev: dict, conf_: float, shadow_conf: float | None, mcap: float, passed: bool, acted: bool,
           version: int, shadow_version: int | None, dev: str | None, early: list | None):
    """First decision on a coin: what we saw and what we did. Later decisions update passed/acted only."""
    ensure_tables()
    db = get_db()
    with db._lock:
        cur = db.conn.execute("""INSERT OR IGNORE INTO outcomes(mint,symbol,decided_ts,mcap0,features,conf,shadow_conf,passed,acted,version,
                                 shadow_version,dev,early) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (mint, symbol, int(time.time()), mcap, json.dumps(ev), conf_, shadow_conf, int(passed), int(acted), version,
                               shadow_version, dev, json.dumps((early or [])[:25])))
        if not cur.rowcount and (passed or acted):
            db.conn.execute("UPDATE outcomes SET passed=MAX(passed,?), acted=MAX(acted,?) WHERE mint=?", (int(passed), int(acted), mint))
        db.conn.commit()


def _price_at(rows: list[dict], at_offset: dict) -> dict:
    """Current market cap (live) or the simulated price at decided_ts + offset (demo, so the loop is testable)."""
    from core import sniper
    out = {}
    if get_config().demo:
        for r in rows:
            base = min((get_db().get_coin(r["mint"]) or {}).get("usd_market_cap") or r["mcap0"] or 30_000, 60_000)
            t = r["decided_ts"] + at_offset[r["mint"]]
            pts = [sniper._demo_mcap(r["mint"], base, r["decided_ts"] + k * (at_offset[r["mint"]] / 12)) for k in range(13)]
            out[r["mint"]] = {"mcap": sniper._demo_mcap(r["mint"], base, t), "peak": max(pts)}
        return out
    coins = [{"mint": r["mint"]} for r in rows]
    for m, p in sniper.prices(coins).items():
        out[m] = {"mcap": p["mcap"], "peak": p["mcap"]}
    db = get_db()
    for r in rows:  # peak: highest we saw while watching it + the coin's recorded ATH
        ticks = db.q("SELECT MAX(mcap) m FROM sniper_ticks WHERE mint=?", (r["mint"],))
        ath = (db.get_coin(r["mint"]) or {}).get("ath_mcap") or 0
        o = out.setdefault(r["mint"], {"mcap": None, "peak": 0})
        o["peak"] = max(o.get("peak") or 0, (ticks[0]["m"] if ticks else 0) or 0, ath)
    return out


def label_of(r: dict, chk: dict, coin: dict | None) -> str:
    c = conf()
    x, peak = chk.get("x") or 0, chk.get("peak_x") or 0
    if coin and coin.get("is_real") is False:
        return "fake"
    if peak >= c["ran_x"] or (coin or {}).get("ath_mcap", 0) >= get_config().get_path("pipeline.runner_ath_usd", 100_000):
        return "ran"
    if chk.get("cp") == "h1" and x <= 0.1:
        return "rugged"
    if x < 0.5:
        return "died"
    return "flat"


def resolve(limit: int = 300) -> int:
    """Fill the 1h / 24h / 7d checkpoints that are due, and (re)label."""
    ensure_tables()
    db, now, n = get_db(), time.time(), 0
    demo = get_config().demo
    for cp, off in CHECKPOINTS:
        due_ts = now if demo else now - off  # demo: the simulated future is available right away
        rows = [dict(r) for r in db.q(f"SELECT * FROM outcomes WHERE {cp} IS NULL AND decided_ts <= ? ORDER BY decided_ts LIMIT ?",
                                      (int(due_ts), limit))]
        if not rows:
            continue
        px = _price_at(rows, {r["mint"]: off for r in rows})
        for r in rows:
            p = px.get(r["mint"]) or {}
            if not p.get("mcap") and not demo:
                p = {"mcap": 0, "peak": p.get("peak") or 0}  # gone from every price feed: treated as dead
            chk = {"cp": cp, "mcap": p.get("mcap"), "x": round((p.get("mcap") or 0) / r["mcap0"], 3) if r["mcap0"] else None,
                   "peak_x": round((p.get("peak") or 0) / r["mcap0"], 3) if r["mcap0"] else None, "ts": int(now)}
            coin = db.get_coin(r["mint"])
            lab = label_of(r, chk, coin)
            if r["label"] in ("ran", "rugged", "fake") and lab not in ("ran", "fake"):
                lab = r["label"]  # an early verdict like "ran" stays (it did run); fake overrides
            db.x(f"UPDATE outcomes SET {cp}=?, label=?, label_ts=? WHERE mint=?", (json.dumps(chk), lab, int(now), r["mint"]))
            n += 1
            if lab in ("rugged", "fake"):
                _on_scam(r, lab, coin)
    if n:
        _update_smart_wallets()
    return n


# ---------------------------------------------------------------- lists: blacklist + smart wallets
def _on_scam(r: dict, lab: str, coin: dict | None):
    from core import fastgate
    dev = r.get("dev") or (coin or {}).get("creator") or (coin or {}).get("dev_wallet")
    if dev and fastgate.add(dev, "dev", f"{lab}: ${r.get('symbol') or r['mint'][:6]}", r["mint"]):
        change("blacklist", f"blacklisted dev {dev[:6]}… behind {lab} ${r.get('symbol') or '?'}", {"wallet": dev, "mint": r["mint"]})
    # repeat early buyers of scams (bundlers / insiders): blacklisted after 2 scams
    db = get_db()
    for w in json.loads(r.get("early") or "[]")[:10]:
        k = f"scam_early:{w}"
        cnt = (db.kv_get(k) or 0) + 1
        db.kv_set(k, cnt)
        if cnt >= 2 and fastgate.add(w, "wallet", f"early buyer in {cnt} rugs/fakes", r["mint"]):
            change("blacklist", f"blacklisted wallet {w[:6]}…: early buyer in {cnt} rugs/fakes", {"wallet": w})


def _update_smart_wallets():
    """Wallets that keep buying early into runners -> SMART (added); ones that stop performing -> dropped."""
    c = conf()
    db = get_db()
    stats: dict[str, list] = {}
    for r in db.q("SELECT early, label FROM outcomes WHERE label IS NOT NULL AND early != '[]' ORDER BY decided_ts"):
        for w in json.loads(r["early"] or "[]")[:15]:
            stats.setdefault(w, []).append(r["label"] == "ran")
    known = {r["wallet"]: dict(r) for r in db.q("SELECT * FROM smart_wallets")}
    for w, hist in stats.items():
        hits, seen = sum(hist), len(hist)
        k = known.get(w)
        if (not k or k["status"] == "dropped") and hits >= c["smart_min_hits"] and hits / seen >= c["smart_min_rate"]:
            if k and time.time() - (k.get("dropped_ts") or 0) < 7 * 86400:
                continue
            db.x("INSERT OR REPLACE INTO smart_wallets(wallet,kind,status,hits,seen,added_ts,note) VALUES(?,?,?,?,?,?,?)",
                 (w, "early buyer", "active", hits, seen, int(time.time()), f"early in {hits} runners ({seen} coins)"))
            change("smart_wallet", f"added smart wallet {w[:6]}…: early in {hits} runners ({round(hits / seen * 100)}% hit)", {"wallet": w})
        elif k and k["status"] == "active":
            recent = hist[-10:]
            if len(recent) >= 8 and sum(recent) / len(recent) < c["smart_drop_rate"]:
                db.x("UPDATE smart_wallets SET status='dropped', dropped_ts=?, hits=?, seen=? WHERE wallet=?", (int(time.time()), hits, seen, w))
                change("smart_wallet", f"dropped smart wallet {w[:6]}…: {sum(recent)} hits in its last {len(recent)}", {"wallet": w})
            else:
                db.x("UPDATE smart_wallets SET hits=?, seen=? WHERE wallet=?", (hits, seen, w))
    # devs with repeat REAL runners: auto-tracked (your devs.json is never edited)
    from core import fastgate
    fastgate.ensure_fresh()
    for dev, st in list(fastgate._mem["devstats"].items()):
        if st["real"] >= 2 and st["fakes"] == 0 and dev not in known:
            if not db.q("SELECT 1 FROM smart_wallets WHERE wallet=?", (dev,)):
                db.x("INSERT INTO smart_wallets(wallet,kind,status,hits,seen,added_ts,note) VALUES(?,?,?,?,?,?,?)",
                     (dev, "dev", "active", st["real"], st["launches"], int(time.time()), f"{st['real']} REAL runners from {st['launches']} launches"))
                change("smart_wallet", f"auto-tracking dev {dev[:6]}…: {st['real']} REAL runners", {"wallet": dev})


_smart = {"ts": 0.0, "set": set()}


def smart_set() -> set:
    if time.time() - _smart["ts"] > 60:
        try:
            ensure_tables()
            _smart.update(ts=time.time(), set={r["wallet"] for r in get_db().q("SELECT wallet FROM smart_wallets WHERE status='active'")})
        except Exception:
            pass
    return _smart["set"]


# ---------------------------------------------------------------- training
def _dataset(since_ts: int = 0) -> list[tuple[list[float], int, dict]]:
    rows = get_db().q("SELECT features, label, conf, shadow_conf, passed, decided_ts FROM outcomes WHERE label IS NOT NULL AND decided_ts >= ?",
                      (since_ts,))
    out = []
    for r in rows:
        ev = json.loads(r["features"] or "{}")
        x = [ev.get(k) if ev.get(k) is not None else 0.5 for k in FEATURES] + [1.0 if ev.get(k) is None else 0.0 for k in FEATURES]
        out.append((x, 1 if r["label"] == "ran" else 0, dict(r)))
    return out


def _fit(data, epochs: int = 300, lr: float = 0.3, l2: float = 0.01, seed: int = 1) -> tuple[list[float], float]:
    """Plain logistic regression (no dependencies)."""
    rnd = random.Random(seed)
    n = len(data[0][0])
    w, b = [0.0] * n, 0.0
    pos = sum(y for _, y, _ in data)
    wpos = (len(data) - pos) / max(1, pos)  # class balance: runners are rare
    for _ in range(epochs):
        rnd.shuffle(data)
        for x, y, _ in data:
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            g = (p - y) * (wpos if y else 1.0)
            w = [wi - lr * (g * xi + l2 * wi) / len(data) * 50 for wi, xi in zip(w, x)]
            b -= lr * g / len(data) * 50
    return w, b


def auc(scores: list[tuple[float, int]]) -> float | None:
    pos = [s for s, y in scores if y]
    neg = [s for s, y in scores if not y]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def _eval(weights: dict, data, gate: float, min_w: float) -> dict:
    sc = [(weighted_conf({k: (x[i] if not x[i + len(FEATURES)] else None) for i, k in enumerate(FEATURES)}, weights, min_w), y) for x, y, _ in data]
    above = [y for s, y in sc if s >= gate]
    return {"auc": auc(sc), "n": len(sc), "hit_rate_above_gate": round(sum(above) / len(above), 3) if above else None,
            "n_above_gate": len(above), "base_rate": round(sum(y for _, y in sc) / len(sc), 3) if sc else None}


def importance(data) -> list[dict]:
    """Why: for each feature, the runner rate when it's high (>=0.6) vs low (<0.4)."""
    out = []
    for i, k in enumerate(FEATURES):
        hi = [y for x, y, _ in data if not x[i + len(FEATURES)] and x[i] >= 0.6]
        lo = [y for x, y, _ in data if not x[i + len(FEATURES)] and x[i] < 0.4]
        out.append({"feature": k, "high_rate": round(sum(hi) / len(hi), 3) if hi else None, "low_rate": round(sum(lo) / len(lo), 3) if lo else None,
                    "n_high": len(hi), "n_low": len(lo)})
    return out


def retrain(reason: str = "scheduled") -> dict | None:
    """Train a new version from all labeled outcomes; it starts in SHADOW. Never touches risk limits or the gate."""
    from core import sniper
    ensure_tables()
    c = conf()
    data = _dataset()
    if len(data) < c["min_samples"] or sum(y for _, y, _ in data) < 5:
        return None
    w, b = _fit(list(data))
    coef = dict(zip(FEATURES, w[:len(FEATURES)]))
    lw = live()["weights"]
    # positive coefficients become evidence weights, scaled to the live weights' total (same confidence scale)
    raw = {k: max(0.05, v) for k, v in coef.items()}
    scale = sum(lw.values()) / sum(raw.values())
    new_w = {k: round(v * scale, 3) for k, v in raw.items()}
    gate = sniper.conf()["gate"]
    m_new = _eval(new_w, data, gate, sniper.MIN_MEASURED_WEIGHT)
    m_live = _eval(lw, data, gate, sniper.MIN_MEASURED_WEIGHT)
    db = get_db()
    db.x("UPDATE scorer_versions SET status='retired', retired_ts=? WHERE status='shadow'", (int(time.time()),))
    ver = (db.q("SELECT MAX(version) v FROM scorer_versions")[0]["v"] or 0) + 1
    metrics = {"train": m_new, "live_on_same_data": m_live, "coef": {k: round(v, 3) for k, v in coef.items()}, "importance": importance(data),
               "trained_on": len(data), "reason": reason}
    db.x("INSERT INTO scorer_versions(version,ts,status,weights,bias,metrics,note) VALUES(?,?,?,?,?,?,?)",
         (ver, int(time.time()), "shadow", json.dumps(new_w), b, json.dumps(metrics, default=str), f"retrained ({reason}) on {len(data)} outcomes"))
    db.kv_set("learning_last_train", {"ts": int(time.time()), "n": len(data)})
    _cache["ts"] = 0
    top = sorted(new_w.items(), key=lambda kv: -kv[1])[:3]
    change("retrain", f"v{ver} trained in shadow ({reason}): AUC {m_new['auc']} vs live {m_live['auc']} on {len(data)} outcomes; "
                      f"top weights {', '.join(f'{k} {v}' for k, v in top)}", {"version": ver, **metrics})
    return {"version": ver, **metrics}


def judge() -> dict | None:
    """Shadow vs live on coins BOTH scored since the shadow started (out of sample). Promote only if clearly better."""
    from core import sniper
    c = conf()
    sh = shadow()
    if not sh:
        return None
    rows = get_db().q("SELECT conf, shadow_conf, label FROM outcomes WHERE shadow_version=? AND label IS NOT NULL AND shadow_conf IS NOT NULL",
                      (sh["version"],))
    if len(rows) < max(30, c["min_samples"] // 2):
        return {"waiting": len(rows)}
    gate = sniper.conf()["gate"]
    a_live, a_sh = auc([(r["conf"], r["label"] == "ran") for r in rows]), auc([(r["shadow_conf"], r["label"] == "ran") for r in rows])
    hr = lambda key: (lambda xs: sum(xs) / len(xs) if xs else 0)([r["label"] == "ran" for r in rows if (r[key] or 0) >= gate])  # noqa: E731
    res = {"n": len(rows), "auc_live": a_live, "auc_shadow": a_sh, "hit_live": round(hr("conf"), 3), "hit_shadow": round(hr("shadow_conf"), 3)}
    if a_live is None or a_sh is None:
        return res
    db = get_db()
    if a_sh >= a_live + c["promote_auc_gain"] and res["hit_shadow"] >= res["hit_live"]:
        lv = live()
        db.x("UPDATE scorer_versions SET status='previous', retired_ts=? WHERE version=?", (int(time.time()), lv["version"]))
        db.x("UPDATE scorer_versions SET status='live', promoted_ts=?, metrics=? WHERE version=?",
             (int(time.time()), json.dumps({**sh["metrics"], "shadow_test": res}), sh["version"]))
        _cache["ts"] = 0
        change("promote", f"v{sh['version']} promoted: shadow AUC {a_sh} vs live {a_live} on {len(rows)} coins; hit rate above gate "
                          f"{res['hit_shadow']} vs {res['hit_live']}", res)
        res["promoted"] = True
    elif len(rows) >= c["min_samples"] * 2:
        db.x("UPDATE scorer_versions SET status='retired', retired_ts=?, metrics=? WHERE version=?",
             (int(time.time()), json.dumps({**sh["metrics"], "shadow_test": res}), sh["version"]))
        _cache["ts"] = 0
        change("reject", f"v{sh['version']} not promoted: shadow AUC {a_sh} vs live {a_live}", res)
        res["rejected"] = True
    return res


def rollback(to_version: int | None = None, reason: str = "manual") -> dict | None:
    db = get_db()
    lv = live()
    target = to_version or next((v["version"] for v in versions() if v["status"] == "previous"), None)
    if not target or target == lv["version"]:
        return None
    db.x("UPDATE scorer_versions SET status='retired', retired_ts=? WHERE version=?", (int(time.time()), lv["version"]))
    db.x("UPDATE scorer_versions SET status='live', promoted_ts=? WHERE version=?", (int(time.time()), target))
    _cache["ts"] = 0
    change("rollback", f"rolled back v{lv['version']} -> v{target} ({reason})", {"from": lv["version"], "to": target})
    return {"from": lv["version"], "to": target}


def guard() -> dict:
    """Performance drop -> roll back + pause the sniper + alert. Meta shift (recent hit rate falling) -> retrain + alert."""
    from core import sniper
    c = conf()
    db = get_db()
    lv = live()
    rows = [dict(r) for r in db.q("SELECT label FROM outcomes WHERE version=? AND passed=1 AND label IS NOT NULL ORDER BY decided_ts DESC LIMIT ?",
                                  (lv["version"], c["drift_window"]))]
    out = {"window": len(rows)}
    if len(rows) < c["drift_window"]:
        return out
    recent = sum(r["label"] == "ran" for r in rows) / len(rows)
    ref = ((lv["metrics"].get("shadow_test") or {}).get("hit_shadow") or (lv["metrics"].get("train") or {}).get("hit_rate_above_gate"))
    older = [dict(r) for r in db.q("SELECT label FROM outcomes WHERE passed=1 AND label IS NOT NULL ORDER BY decided_ts DESC LIMIT 200 OFFSET ?",
                                   (c["drift_window"],))]
    base = (sum(r["label"] == "ran" for r in older) / len(older)) if len(older) >= 40 else ref
    out.update(recent=round(recent, 3), reference=round(base, 3) if base else None)
    if base and recent < base * (1 - c["drift_drop"]):
        last = db.kv_get("learning_guard_ts") or 0
        if time.time() - last > 6 * 3600:
            db.kv_set("learning_guard_ts", int(time.time()))
            rb = rollback(reason=f"hit rate {recent:.0%} vs {base:.0%}") if ref and lv["version"] > 1 else None
            if rb:  # your rule: performance drops -> last good version AND pause trading
                sniper.save_settings({"enabled": False})
                change("pause", f"sniper paused after rollback v{rb['from']} -> v{rb['to']}: hit rate {recent:.0%} vs {base:.0%}", out)
                alert(f"Performance dropped (hit rate {recent:.0%} vs {base:.0%}). Rolled back to v{rb['to']} and paused the sniper. Press RUN to resume.")
            r = retrain("meta shift")
            if not rb:
                alert(f"Meta shift: the sniper's hit rate fell to {recent:.0%} (was {base:.0%}). "
                      + (f"Retrained: v{r['version']} is testing in shadow." if r else "Not enough outcomes to retrain yet."))
            out["acted"] = True
    return out


def missed() -> list[dict]:
    """Runners we scored but didn't pass: which single evidence threshold would have caught them, and what it costs
    (its hit rate on everything we scored). The top tests feed the weekly recap."""
    data = _dataset()
    miss = [(x, y, r) for x, y, r in data if y and not r["passed"]]
    if len(miss) < 3:
        return []
    out = []
    for i, k in enumerate(FEATURES):
        for t in (0.6, 0.7, 0.8):
            caught = sum(1 for x, _, _ in miss if not x[i + len(FEATURES)] and x[i] >= t)
            fired = [y for x, y, _ in data if not x[i + len(FEATURES)] and x[i] >= t]
            if caught and fired:
                out.append({"rule": f"{k} >= {t}", "caught": caught, "of_missed": len(miss), "precision": round(sum(fired) / len(fired), 3),
                            "fires": len(fired)})
    base = sum(y for _, y, _ in data) / len(data)
    out = [o for o in out if o["precision"] > base * 1.5]
    out.sort(key=lambda o: (-o["caught"] * o["precision"]))
    return out[:5]


def tick():
    """Background loop body: resolve checkpoints -> lists -> judge shadow -> guard -> scheduled retrain."""
    with _lock:
        ensure_tables()
        n = resolve()
        judge()
        guard()
        c = conf()
        last = get_db().kv_get("learning_last_train") or {"ts": 0, "n": 0}
        labeled = get_db().q("SELECT COUNT(*) n FROM outcomes WHERE label IS NOT NULL")[0]["n"]
        if (time.time() - last["ts"] > c["retrain_every_h"] * 3600 and labeled - last["n"] >= c["retrain_min_new"]) and not shadow():
            retrain("scheduled")
        try:
            from core import recap
            recap.maybe()  # weekly
        except Exception as e:
            log("THESIS", f"recap: {e}", "warn")
        return n


_started = [False]


def start_worker():
    if _started[0]:
        return
    _started[0] = True

    def loop():
        time.sleep(45)
        while True:
            try:
                tick()
            except Exception as e:
                log("THESIS", f"learning: {e}", "warn")
            time.sleep(300)
    threading.Thread(target=loop, daemon=True, name="learning").start()


def summary() -> dict:
    ensure_tables()
    db = get_db()
    lab = {r["label"]: r["n"] for r in db.q("SELECT label, COUNT(*) n FROM outcomes WHERE label IS NOT NULL GROUP BY label")}
    lv, sh = live(), shadow()
    return {"live": {"version": lv["version"], "weights": lv["weights"], "metrics": lv["metrics"], "note": lv["note"]},
            "shadow": {"version": sh["version"], "weights": sh["weights"], "metrics": sh["metrics"]} if sh else None,
            "versions": [{k: v for k, v in x.items() if k in ("version", "status", "ts", "note", "promoted_ts")} for x in versions()[:10]],
            "outcomes": {"total": db.q("SELECT COUNT(*) n FROM outcomes")[0]["n"], "labels": lab},
            "smart": [dict(r) for r in db.q("SELECT * FROM smart_wallets ORDER BY status, hits DESC LIMIT 30")],
            "blacklist": [dict(r) for r in db.q("SELECT * FROM blacklist ORDER BY ts DESC LIMIT 30")] if db.q(
                "SELECT name FROM sqlite_master WHERE name='blacklist'") else [],
            "changes": [dict(r) for r in db.q("SELECT id, ts, kind, summary FROM changes ORDER BY id DESC LIMIT 40")],
            "alerts": (db.kv_get("learning_alerts", []) or [])[-5:], "missed": missed(),
            "risk_limits_locked": ["trade size", "max open positions", "stop-loss / loss cap", "time stop", "your gate", "starting balance"],
            "kill_switch": "it can PAUSE the sniper when performance drops (your rule); only you can resume it"}
