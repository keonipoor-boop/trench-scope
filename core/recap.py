"""WEEKLY RECAP: what the bot learned, what it changed, wins and misses, wallets added / dropped, speed, and what to
improve next. Generated every 7 days by the learning worker (or on demand), stored in `recaps`, shown on the SNIPER
DESK, and sent to your Discord webhook when one is set. Claude writes the prose when keyed; otherwise a template."""
from __future__ import annotations

import json
import time

from core import llm
from core.db import get_db
from core.events import log

WEEK = 7 * 86400


def ensure_table():
    get_db().x("CREATE TABLE IF NOT EXISTS recaps (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, data TEXT, text TEXT)")


def latest() -> dict | None:
    ensure_table()
    r = get_db().q("SELECT * FROM recaps ORDER BY id DESC LIMIT 1")
    return {**dict(r[0]), "data": json.loads(r[0]["data"])} if r else None


def maybe():
    lt = latest()
    if not lt or time.time() - lt["ts"] >= WEEK:
        return generate()
    return None


def _data(since: int) -> dict:
    from core import fastgate, learning, perf, sniper
    db = get_db()
    learning.ensure_tables()
    sniper.ensure_tables()
    fastgate.ensure_table()
    changes = [dict(r) for r in db.q("SELECT ts, kind, summary FROM changes WHERE ts >= ? ORDER BY id", (since,))]
    kinds = {}
    for c in changes:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    lv = learning.live()
    week_start_v = db.q("SELECT weights, version FROM scorer_versions WHERE promoted_ts <= ? ORDER BY promoted_ts DESC LIMIT 1", (since,))
    w0 = json.loads(week_start_v[0]["weights"]) if week_start_v else lv["weights"]
    weight_moves = sorted([{"feature": k, "from": round(w0.get(k, 0), 2), "to": round(v, 2)} for k, v in lv["weights"].items()],
                          key=lambda x: -abs(x["to"] - x["from"]))[:5]
    out = [dict(r) for r in db.q("SELECT mint, symbol, label, passed, acted, h24, d7 FROM outcomes WHERE decided_ts >= ?", (since,))]
    lab = {}
    for o in out:
        lab[o["label"] or "pending"] = lab.get(o["label"] or "pending", 0) + 1
    peak = lambda o: (json.loads(o["d7"] or o["h24"] or "{}") or {}).get("peak_x") or 0  # noqa: E731
    caught = sorted([o for o in out if o["label"] == "ran" and o["passed"]], key=lambda o: -peak(o))[:5]
    missed = sorted([o for o in out if o["label"] == "ran" and not o["passed"]], key=lambda o: -peak(o))[:5]
    trades = [dict(r) for r in db.q("SELECT symbol, realized_sol, exits FROM sniper_positions WHERE closed_ts >= ?", (since,))]
    wins = sorted([t for t in trades if t["realized_sol"] > 0], key=lambda t: -t["realized_sol"])[:3]
    losses = sorted([t for t in trades if t["realized_sol"] <= 0], key=lambda t: t["realized_sol"])[:3]
    smart_add = [dict(r) for r in db.q("SELECT wallet, kind, note FROM smart_wallets WHERE added_ts >= ?", (since,))]
    smart_drop = [dict(r) for r in db.q("SELECT wallet, kind, note FROM smart_wallets WHERE dropped_ts >= ?", (since,))]
    bl = [dict(r) for r in db.q("SELECT wallet, kind, reason FROM blacklist WHERE ts >= ?", (since,))] if db.q(
        "SELECT name FROM sqlite_master WHERE name='blacklist'") else []
    kol_flags = []
    try:  # your KOLs are never removed: underperformers are flagged for you to decide
        from core import kol_research
        for k in kol_research.list_kols():
            p = kol_research.get_profile(k["name"]) or {}
            m = (p.get("periods") or {}).get("month") or {}
            if m.get("realized_sol") is not None and m["realized_sol"] < 0 and (m.get("win_rate") or 0) < 40:
                kol_flags.append({"name": k["name"], "month_realized_sol": m["realized_sol"], "win_rate": m.get("win_rate")})
    except Exception:
        pass
    snap = perf.snapshot()
    path = {r["stage"]: r.get("p50") for r in snap["path"] if r.get("n")}
    prev = db.kv_get("recap_perf") or {}
    db.kv_set("recap_perf", path)
    ready = sniper.readiness([dict(r) for r in db.q("SELECT * FROM sniper_positions WHERE status='closed'")], sniper.conf()["start_balance_sol"])
    nxt = []
    for m in learning.missed()[:2]:
        nxt.append(f"Test '{m['rule']}': it would have caught {m['caught']} of {m['of_missed']} missed runners at {round(m['precision'] * 100)}% precision.")
    slow = sorted(((k, v) for k, v in path.items() if not k.startswith("launch")), key=lambda kv: -(kv[1] or 0))[:1]
    if slow:
        nxt.append(f"Slowest step: {slow[0][0]} ({slow[0][1]:.2f}s p50).")
    if ready["caps"]:
        nxt.append("Readiness is held back by: " + "; ".join(ready["caps"][:2]) + ".")
    if kol_flags:
        nxt.append("Review KOLs losing money this month: " + ", ".join(k["name"] for k in kol_flags) + ".")
    return {"since": since, "until": int(time.time()), "changes": changes[-40:], "change_counts": kinds, "live_version": lv["version"],
            "weight_moves": weight_moves, "outcomes": lab, "caught": caught, "missed": missed, "trades": len(trades),
            "wins": wins, "losses": losses, "realized_sol": round(sum(t["realized_sol"] for t in trades), 3),
            "smart_added": smart_add, "smart_dropped": smart_drop, "blacklisted": bl, "kol_flags": kol_flags,
            "speed_now": path, "speed_prev": prev, "readiness": ready["score"], "next": nxt}


def _template(d: dict) -> str:
    fmt = lambda s: f"{s:.2f}s" if s is not None else "—"  # noqa: E731
    L = [f"WEEKLY RECAP · {time.strftime('%b %d', time.localtime(d['since']))} – {time.strftime('%b %d', time.localtime(d['until']))}", ""]
    L.append(f"LEARNED · live scorer v{d['live_version']} · " + (", ".join(f"{k} {v}" for k, v in d["change_counts"].items()) or "no automatic changes"))
    for w in d["weight_moves"][:3]:
        if w["from"] != w["to"]:
            L.append(f"  {w['feature']} weight {w['from']} → {w['to']}")
    L.append(f"OUTCOMES · " + ", ".join(f"{v} {k}" for k, v in d["outcomes"].items()) if d["outcomes"] else "OUTCOMES · none resolved yet")
    if d["caught"]:
        L.append("  caught: " + ", ".join(f"${o['symbol']}" for o in d["caught"]))
    if d["missed"]:
        L.append("  missed: " + ", ".join(f"${o['symbol']}" for o in d["missed"]))
    L.append(f"PAPER TRADES · {d['trades']} closed · realized {d['realized_sol']:+.3f} SOL · readiness {d['readiness']}/100")
    L.append(f"WALLETS · +{len(d['smart_added'])} smart · -{len(d['smart_dropped'])} dropped · {len(d['blacklisted'])} blacklisted")
    sn, sp = d["speed_now"], d["speed_prev"]
    if sn.get("launch→gated") is not None:
        L.append(f"SPEED · launch → decision {fmt(sn.get('launch→gated'))}" + (f" (last week {fmt(sp.get('launch→gated'))})" if sp.get("launch→gated") else ""))
    if d["next"]:
        L.append("NEXT")
        L += [f"  • {n}" for n in d["next"]]
    return "\n".join(L)


def generate(force: bool = False) -> dict:
    ensure_table()
    since = int(time.time() - WEEK)
    d = _data(since)
    text = None
    if llm.available():
        text = llm.ask("Weekly data from a memecoin scanner + paper-trading sniper (JSON):\n" + json.dumps(d, default=str)[:12000] +
                       "\n\nWrite the weekly recap for its owner: sections LEARNED, CHANGED, WINS & MISSES, WALLETS, SPEED, NEXT. Short lines, "
                       "real numbers, plain words, no hype.", system="You write concise weekly engineering + trading recaps.", max_tokens=900)
    text = text or _template(d)
    rid = get_db().x("INSERT INTO recaps(ts,data,text) VALUES(?,?,?)", (int(time.time()), json.dumps(d, default=str), text))
    log("THESIS", f"weekly recap #{rid} written")
    try:
        from core import drafts, http
        hook = drafts.settings().get("discord_webhook")
        if hook:
            http.post(hook, json={"content": ("**Trench Scope weekly recap**\n" + text)[:1900]}, rate_key="discord_hook", per_min=20, retries=1)
    except Exception:
        pass
    return {"id": rid, "text": text, "data": d}
