"""STRATEGY LIBRARY: the AI's model of each KOL's strategy, rebuilt on every (hourly) research run.

build_spec() turns positions into a strategy: entry rules, exit strategy (take-profit ladder, initials out,
stop-loss, time stop, runner bag), risk, plain-language rules and a confidence score. update() compares it
with the latest stored version and saves a new version, with a changelog, when something meaningful moved.
It also compares the last 7 days with the long-run pattern and flags a STRATEGY SHIFT in progress."""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter

from core import llm
from core.db import get_db
from core.events import log


_READY: set = set()


def ensure_table():
    if id(get_db()) in _READY:
        return
    _READY.add(id(get_db()))
    get_db().x("""CREATE TABLE IF NOT EXISTS kol_strategies (name TEXT, version INTEGER, ts INTEGER, checked_ts INTEGER,
                  spec TEXT, changes TEXT, PRIMARY KEY (name, version))""")


def _q(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _dur(s):
    if s is None:
        return "—"
    return f"{s / 86400:.1f}d" if s >= 86400 else f"{s / 3600:.1f}h" if s >= 3600 else f"{s / 60:.0f}m" if s >= 60 else f"{s:.0f}s"


def _usd(v):
    return "—" if not v else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def exit_model(pos: list[dict]) -> dict:
    """How they get out: take-profit ladder, initials, stop-loss, time stop, runner bag."""
    with_exits = [p for p in pos if p.get("exits")]
    winners = [p for p in with_exits if (p.get("multiple") or 0) > 1]
    losers = [p for p in pos if p["closed"] and (p.get("multiple") or 0) <= 1]
    profit_sells = [e for p in with_exits for e in p["exits"] if e["x"] > 1.05]
    first = [p["exits"][0] for p in with_exits]
    multi = [p for p in with_exits if len(p["exits"]) >= 2]
    # "initials out": a PARTIAL first sell above ~1.6x that gets back roughly what they put in
    initials = [p for p in winners if p["exits"][0]["frac"] < 0.9 and p["exits"][0]["x"] >= 1.6
                and p["exits"][0]["sol"] >= 0.8 * p["invested_sol"]]
    runner = [p for p in winners if len(p["exits"]) >= 2 and p["exits"][0]["frac"] < 0.9]
    ladder = []
    for lo, hi, lbl in ((1.05, 1.5, "1-1.5x"), (1.5, 2.5, "1.5-2.5x"), (2.5, 4, "2.5-4x"), (4, 8, "4-8x"), (8, 1e9, "8x+")):
        es = [e for e in profit_sells if lo <= e["x"] < hi]
        if es:
            ladder.append({"band": lbl, "sells": len(es), "avg_frac": round(statistics.mean(e["frac"] for e in es) * 100)})
    return {
        "tp_levels": sorted({round(v, 1) for v in (_q([e["x"] for e in profit_sells], .25), _q([e["x"] for e in profit_sells], .5),
                                                     _q([e["x"] for e in profit_sells], .75)) if v is not None}),
        "tp_ladder": ladder,
        "first_sell_x": round(_med([e["x"] for e in first]), 2) if first else None,
        "first_sell_pct": round(_med([p["exits"][0]["frac"] for p in multi]) * 100) if multi else None,
        "single_exit_pct": round((len(with_exits) - len(multi)) / len(with_exits) * 100) if with_exits else None,
        "first_sell_after_s": _med([e["t"] for e in first]),
        "initials_out_pct": round(len(initials) / len(winners) * 100) if winners else None,
        "runner_bag_pct": round(len(runner) / len(winners) * 100) if winners else None,
        "exits_per_position": round(statistics.mean(len(p["exits"]) for p in with_exits), 1) if with_exits else None,
        "stop_loss_x": round(_med([p["multiple"] for p in losers if p.get("multiple") is not None]), 2) if losers else None,
        "cut_after_s": _med([p.get("hold_s") for p in losers]),
        "win_hold_s": _med([p.get("hold_s") for p in winners if p["closed"]]),
        "max_hold_s": _q([p.get("hold_s") for p in pos if p["closed"]], .9),
        "rides_to_zero_pct": round(sum(1 for p in losers if (p.get("multiple") or 0) < 0.15) / len(losers) * 100) if losers else None,
    }


EX_FIELDS = ("mint", "symbol", "name", "image", "entry_mcap", "entry_age_s", "invested_sol", "multiple", "pnl_sol",
             "hold_s", "first_buy_ts", "closed", "themes")


def examples(pos: list[dict], entry: dict) -> dict:
    """Real coins behind the playbook: recent buys that fit their usual entry, their best and worst trades."""
    pick = lambda p: {k: p.get(k) for k in EX_FIELDS}  # noqa: E731
    lo, hi = entry.get("mcap_low"), entry.get("mcap_high")
    fits = [p for p in pos if p.get("entry_mcap") and lo and hi and lo <= p["entry_mcap"] <= hi]
    fits.sort(key=lambda p: -(p.get("first_buy_ts") or 0))
    closed = [p for p in pos if p["closed"] and p.get("pnl_sol") is not None]
    best = sorted([p for p in closed if p["pnl_sol"] > 0], key=lambda p: -p["pnl_sol"])[:4]
    worst = sorted([p for p in closed if p["pnl_sol"] < 0], key=lambda p: p["pnl_sol"])[:3]
    return {"typical": [pick(p) for p in fits[:6]], "best": [pick(p) for p in best], "worst": [pick(p) for p in worst]}


def build_spec(k: dict, pos: list[dict], s: dict, style: list[str], risk: list[str]) -> dict:
    mc = [p["entry_mcap"] for p in pos if p.get("entry_mcap")]
    age = [p["entry_age_s"] for p in pos if p.get("entry_age_s") is not None]
    sizes = [p["invested_sol"] for p in pos if p.get("invested_sol")]
    themes = Counter(t for p in pos for t in (p.get("themes") or []))
    pads = Counter(p.get("launchpad") or "unknown" for p in pos)
    ex = exit_model(pos)
    closed = sum(1 for p in pos if p["closed"])
    conf = min(100, round(closed * 2.5 + len(pos) * 0.5))
    entry = {
        "mcap_low": _q(mc, .25), "mcap_mid": _q(mc, .5), "mcap_high": _q(mc, .75),
        "age_low_s": _q(age, .25), "age_mid_s": _q(age, .5), "age_high_s": _q(age, .75),
        "size_low": _q(sizes, .25), "size_mid": _q(sizes, .5), "size_high": _q(sizes, .75),
        "themes": [t for t, _ in themes.most_common(3)], "launchpads": [p for p, _ in pads.most_common(2)],
    }
    rules = []
    if entry["mcap_mid"]:
        rules.append(f"Enter between {_usd(entry['mcap_low'])} and {_usd(entry['mcap_high'])} market cap "
                     f"(typically {_usd(entry['mcap_mid'])}), {_dur(entry['age_low_s'])}-{_dur(entry['age_high_s'])} after launch.")
    if entry["size_mid"]:
        rules.append(f"Size {entry['size_low']:.2f}-{entry['size_high']:.2f} SOL per coin (usually {entry['size_mid']:.2f}).")
    if entry["themes"]:
        rules.append("Favours " + ", ".join(entry["themes"]) + " coins" + (f" on {entry['launchpads'][0]}" if entry["launchpads"] else "") + ".")
    if ex["first_sell_x"] and (ex["single_exit_pct"] or 0) >= 60:
        rules.append(f"Usually exits the whole bag in one sell ({ex['single_exit_pct']}% of trades), typically around "
                     f"{ex['first_sell_x']}x, {_dur(ex['first_sell_after_s'])} after entry.")
    elif ex["first_sell_x"]:
        rules.append(f"First take-profit around {ex['first_sell_x']}x"
                     + (f", selling ~{ex['first_sell_pct']}% of the bag" if ex["first_sell_pct"] is not None else "")
                     + f" {_dur(ex['first_sell_after_s'])} after entry.")
    if ex["initials_out_pct"] is not None and ex["initials_out_pct"] >= 35:
        rules.append(f"Takes initials out on {ex['initials_out_pct']}% of winners, then lets the rest ride.")
    if ex["tp_levels"]:
        rules.append("Profit-taking ladder: " + " → ".join(f"{x}x" for x in ex["tp_levels"]) + ".")
    if ex["stop_loss_x"] is not None:
        rules.append(f"Cuts losers near {round((ex['stop_loss_x'] - 1) * 100)}% after about {_dur(ex['cut_after_s'])}"
                     + (f"; {ex['rides_to_zero_pct']}% of losers ride to ~zero." if ex["rides_to_zero_pct"] else "."))
    if ex["max_hold_s"]:
        rules.append(f"Rarely holds longer than {_dur(ex['max_hold_s'])}.")
    title = " · ".join(style[:2]) + (" · scales out" if (ex["exits_per_position"] or 0) >= 1.8 else " · one-shot exits")
    return {"title": title, "style": style, "risk_labels": risk, "entry": entry, "exit": ex,
            "risk": {"size_cv": s.get("size_cv"), "trades_per_day": s.get("trades_per_day"), "bag_hold_pct": s.get("bag_hold_pct"),
                     "win_rate": s.get("win_rate"), "avg_win_x": s.get("avg_win_x"), "median_loss_pct": s.get("median_loss_pct")},
            "rules": rules, "confidence": conf, "sample": {"positions": len(pos), "closed": closed},
            "examples": examples(pos, entry)}


# what counts as a meaningful change between versions
TRACKED = [
    ("entry.mcap_mid", "typical entry market cap", "rel", 0.4, _usd),
    ("entry.age_mid_s", "entry time after launch", "rel", 0.5, _dur),
    ("entry.size_mid", "typical size (SOL)", "rel", 0.4, lambda v: f"{v:.2f}"),
    ("exit.first_sell_x", "first take-profit", "abs", 0.4, lambda v: f"{v}x"),
    ("exit.first_sell_pct", "share sold at first take-profit", "abs", 20, lambda v: f"{v}%"),
    ("exit.initials_out_pct", "initials-out rate", "abs", 25, lambda v: f"{v}%"),
    ("exit.stop_loss_x", "stop-loss level", "abs", 0.2, lambda v: f"{round((v - 1) * 100)}%"),
    ("exit.cut_after_s", "time to cut losers", "rel", 0.6, _dur),
    ("exit.exits_per_position", "exits per position", "abs", 0.8, lambda v: f"{v}"),
    ("risk.win_rate", "win rate", "abs", 15, lambda v: f"{v}%"),
]


def _get(d, path):
    for p in path.split("."):
        d = (d or {}).get(p)
    return d


def diff(old: dict, new: dict) -> list[str]:
    out = []
    for path, label, mode, thr, fmt in TRACKED:
        a, b = _get(old, path), _get(new, path)
        if a is None or b is None:
            continue
        moved = abs(b - a) / max(abs(a), 1e-9) >= thr if mode == "rel" else abs(b - a) >= thr
        if moved:
            out.append(f"{label}: {fmt(a)} → {fmt(b)}")
    if set(old.get("style") or []) != set(new.get("style") or []):
        out.append(f"style: {' / '.join(old.get('style') or ['—'])} → {' / '.join(new.get('style') or ['—'])}")
    return out


def playbook(name: str, spec: dict) -> tuple[str, str]:
    if llm.available():
        out = llm.ask(f"KOL {name}. Strategy model (JSON): {json.dumps(spec, default=str)}\n\nWrite their trading strategy as a "
                      "compact playbook: 'ENTRY' (2-3 lines), 'EXIT' (take-profit ladder, initials, stop-loss, time stop; "
                      "2-4 lines), 'RISK' (1-2 lines), then 'HOW TO READ THEIR BUYS' (2 bullets). Use the numbers. No hype, "
                      "no financial advice.", system="You reverse-engineer memecoin traders' strategies from wallet data.",
                      max_tokens=700)
        if out:
            return out, "claude"
    return "\n".join("• " + r for r in spec["rules"]), "template"


def update(name: str, k: dict, pos: list[dict], s: dict, style: list[str], risk: list[str], sol_usd: float) -> dict:
    from core.kol_research import archetype, stats  # local import: kol_research imports this module lazily
    ensure_table()
    db = get_db()
    now = int(time.time())
    spec = build_spec(k, pos, s, style, risk)
    # fine-tuning: does the last week look different from the long-run pattern?
    recent = [p for p in pos if (p.get("first_buy_ts") or 0) >= now - 7 * 86400]
    shift = []
    if len(recent) >= 6 and len(recent) < len(pos):
        rs = stats(recent, [])
        r_style, r_risk = archetype(rs)
        shift = diff(spec, build_spec(k, recent, rs, r_style, r_risk))
    spec["shift"] = shift
    prev = db.q("SELECT * FROM kol_strategies WHERE name=? ORDER BY version DESC LIMIT 1", (name,))
    if prev:
        old = json.loads(prev[0]["spec"])
        changes = diff(old, spec)
        if not changes:  # same strategy: just mark it re-checked (keep the stored playbook)
            spec["playbook"], spec["playbook_by"] = old.get("playbook"), old.get("playbook_by")
            db.x("UPDATE kol_strategies SET checked_ts=?, spec=? WHERE name=? AND version=?",
                 (now, json.dumps(spec, default=str), name, prev[0]["version"]))
            return {"version": prev[0]["version"], "changed": False}
        version = prev[0]["version"] + 1
    else:
        changes, version = ["first version: strategy formed from wallet history"], 1
    spec["playbook"], spec["playbook_by"] = playbook(name, spec)
    db.x("INSERT INTO kol_strategies(name, version, ts, checked_ts, spec, changes) VALUES(?,?,?,?,?,?)",
         (name, version, now, now, json.dumps(spec, default=str), json.dumps(changes)))
    log("KOLS", f"strategy {name} v{version}: " + "; ".join(changes[:3]))
    return {"version": version, "changed": True}


def library() -> list[dict]:
    ensure_table()
    out = []
    for r in get_db().q("""SELECT s.* FROM kol_strategies s JOIN (SELECT name, MAX(version) v FROM kol_strategies GROUP BY name) m
                           ON s.name=m.name AND s.version=m.v ORDER BY s.ts DESC"""):
        spec = json.loads(r["spec"])
        out.append({"name": r["name"], "version": r["version"], "ts": r["ts"], "checked_ts": r["checked_ts"],
                    "title": spec.get("title"), "confidence": spec.get("confidence"), "shift": spec.get("shift") or [],
                    "changes": json.loads(r["changes"] or "[]"), "first_sell_x": (spec.get("exit") or {}).get("first_sell_x"),
                    "entry_mcap": (spec.get("entry") or {}).get("mcap_mid"), "win_rate": (spec.get("risk") or {}).get("win_rate")})
    return out


def versions(name: str) -> list[dict]:
    ensure_table()
    return [{"version": r["version"], "ts": r["ts"], "checked_ts": r["checked_ts"], "spec": json.loads(r["spec"]),
             "changes": json.loads(r["changes"] or "[]")}
            for r in get_db().q("SELECT * FROM kol_strategies WHERE name=? ORDER BY version DESC", (name,))]


def delete(name: str):
    ensure_table()
    get_db().x("DELETE FROM kol_strategies WHERE name=?", (name,))
