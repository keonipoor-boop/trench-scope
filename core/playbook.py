"""STRATEGY: a living, data-backed thesis on how to trade new pairs, computed from what Trench Scope has seen.

Four chapters, each a set of rules with the numbers behind them:
  SPOT      which launches become runners: hit rate (and lift vs the base rate) by narrative, socials, dev record, KOL buys
  BUY       the odds ladder: if a coin reaches $X, how often it reaches the next level; fast vs slow runners
  EXIT/HOLD what holds a run: how much of the peak runners keep (by REAL/FAKE, catalyst, top-10 concentration)
  CUT       when to take the loss: after a drawdown of 30/50/70% from the high, how often a runner made a new high
            (from the per-scan market cap snapshots), plus what the tracked KOLs' strategies do
Claude writes the prose when a key is set; otherwise a template does. Cached (`playbook.cache_min`)."""
from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict

from core import llm, narratives
from core.config import get_config
from core.db import get_db

_cache: dict = {}
LADDER = [40_000, 69_000, 100_000, 250_000, 500_000, 1_000_000, 3_000_000]


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def _usd(v):
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _ex(cs, n=4):
    return [{"mint": c["mint"], "symbol": c.get("symbol"), "image": c.get("image"), "ath": c.get("ath_mcap"),
             "now": c.get("usd_market_cap"), "is_real": c.get("is_real")} for c in cs[:n]]


def spot(coins, runners_ath):
    """Hit rate by feature among launches, vs the base rate."""
    base_n = len(coins)
    hits = sum(1 for c in coins if (c.get("ath_mcap") or 0) >= runners_ath)
    base = hits / base_n if base_n else 0
    feats = defaultdict(lambda: [0, 0, []])

    def add(key, c):
        f = feats[key]
        f[0] += 1
        if (c.get("ath_mcap") or 0) >= runners_ath:
            f[1] += 1
            f[2].append(c)
    from core import devs
    tracked = {w for t in devs.tracked() for w in t["wallets"]}
    dev_hits = defaultdict(int)
    for c in sorted(coins, key=lambda c: c.get("created_ts") or 0):
        themes = c.get("themes") or narratives.tag(c)
        for t in themes or ["Unthemed"]:
            add(f"Narrative: {t}", c)
        add("Has an X link" if (c.get("twitter") or c.get("twitter_dex")) else "No X link", c)
        if c.get("website"):
            add("Has a website", c)
        if c.get("kols"):
            add("A tracked KOL bought it", c)
        dev = c.get("dev_wallet") or c.get("creator")
        if dev and dev in tracked:
            add("Launched by a dev you track", c)
        if dev and dev_hits[dev] > 0:
            add("Dev already had a runner", c)
        if dev and (c.get("ath_mcap") or 0) >= runners_ath:
            dev_hits[dev] += 1
        if c.get("x_bio") or c.get("x_pinned_text") or c.get("site_text"):
            pass
        if c.get("primary_catalyst") == "rewards":
            add("Pays holders (rewards / rev share)", c)
    rows = []
    for k, (n, h, ex) in feats.items():
        if n < 15:
            continue
        rate = h / n
        rows.append({"feature": k, "n": n, "hits": h, "hit_rate": round(rate * 100, 2),
                     "lift": round(rate / base, 2) if base else None,
                     "examples": _ex(sorted(ex, key=lambda c: -(c.get("ath_mcap") or 0)), 3)})
    rows.sort(key=lambda r: -(r["lift"] or 0))
    return {"base_rate": round(base * 100, 2), "launches": base_n, "runners": hits, "best": rows[:8],
            "worst": [r for r in sorted(rows, key=lambda r: r["lift"] or 0)[:4] if (r["lift"] or 1) < 0.8]}


def buy(coins, runners):
    """Odds ladder + speed: P(next level | reached this level)."""
    ladder = []
    for i in range(len(LADDER) - 1):
        a, b = LADDER[i], LADDER[i + 1]
        reached = [c for c in coins if (c.get("ath_mcap") or 0) >= a]
        nxt = [c for c in reached if (c.get("ath_mcap") or 0) >= b]
        if len(reached) >= 5:
            ladder.append({"from": a, "to": b, "n": len(reached), "pct": _pct(len(nxt), len(reached))})
    speed = []
    for label, lo, hi in (("under 15 min", 0, 15), ("15-60 min", 15, 60), ("1-6 hours", 60, 360), ("over 6 hours", 360, 10**9)):
        grp = [c for c in runners if c.get("time_to_100k_min") is not None and lo <= c["time_to_100k_min"] < hi]
        if len(grp) >= 4:
            speed.append({"label": label, "n": len(grp), "to_500k": _pct(sum(1 for c in grp if c["ath_mcap"] >= 500_000), len(grp)),
                          "real": _pct(sum(1 for c in grp if c.get("is_real") is True), len(grp)), "median_ath": _med([c["ath_mcap"] for c in grp])})
    real = [c for c in runners if c.get("is_real") is True]
    fake = [c for c in runners if c.get("is_real") is False]
    rf = {"real_to_500k": _pct(sum(1 for c in real if c["ath_mcap"] >= 500_000), len(real)),
          "fake_to_500k": _pct(sum(1 for c in fake if c["ath_mcap"] >= 500_000), len(fake)), "real_n": len(real), "fake_n": len(fake)}
    return {"ladder": ladder, "speed": speed, "real_vs_fake": rf}


def hold(runners):
    """How much of the peak runners keep (retention = now / ATH), by group."""
    now = time.time()
    aged = [c for c in runners if c.get("usd_market_cap") and c.get("ath_mcap") and now - (c.get("created_ts") or now) > 6 * 3600]

    def ret(grp):
        r = [c["usd_market_cap"] / c["ath_mcap"] for c in grp]
        return {"n": len(grp), "median_keep": round(_med(r) * 100) if r else None,
                "faded_pct": _pct(sum(1 for x in r if x < 0.3), len(r))}
    groups = []
    for label, f in (("REAL (passed every check)", lambda c: c.get("is_real") is True),
                     ("FAKE (failed a check)", lambda c: c.get("is_real") is False),
                     ("Top-10 hold under 20%", lambda c: (c.get("top10_pct") or 100) < 20),
                     ("Top-10 hold over 35%", lambda c: (c.get("top10_pct") or 0) > 35),
                     ("Ran in under 15 min", lambda c: (c.get("time_to_100k_min") or 999) < 15),
                     ("Took over an hour", lambda c: (c.get("time_to_100k_min") or 0) > 60)):
        g = [c for c in aged if f(c)]
        if len(g) >= 5:
            groups.append({"label": label, **ret(g), "examples": _ex(sorted(g, key=lambda c: -(c["usd_market_cap"] / c["ath_mcap"])), 3)})
    by_cat = defaultdict(list)
    for c in aged:
        by_cat[c.get("primary_catalyst") or "meme"].append(c)
    cats = sorted([{"catalyst": k, **ret(v)} for k, v in by_cat.items() if len(v) >= 5], key=lambda r: -(r["median_keep"] or 0))
    return {"overall": ret(aged), "groups": groups, "catalysts": cats}


def cut(runners):
    """After a drawdown of X% from the running high, how often did the coin go on to make a new high?
    Uses the market-cap snapshots taken every scan."""
    db = get_db()
    mints = [c["mint"] for c in runners][:600]
    series = defaultdict(list)
    for i in range(0, len(mints), 300):
        ch = mints[i:i + 300]
        for r in db.q(f"SELECT mint, ts, mcap FROM snapshots WHERE mint IN ({','.join('?' * len(ch))}) ORDER BY mint, ts", tuple(ch)):
            if r["mcap"]:
                series[r["mint"]].append(r["mcap"])
    out = []
    for dd in (30, 50, 70):
        hit = recovered = 0
        for s in series.values():
            if len(s) < 4:
                continue
            peak, armed = s[0], False
            for v in s[1:]:
                if not armed and v <= peak * (1 - dd / 100):
                    armed, level = True, peak
                    hit += 1
                elif armed and v > level:
                    recovered += 1
                    break
                peak = max(peak, v) if not armed else peak
        if hit >= 5:
            out.append({"drawdown": dd, "n": hit, "new_high_pct": _pct(recovered, hit)})
    from core import strategy
    lib = strategy.library()
    kol = []
    for x in lib:
        v = strategy.versions(x["name"])
        if v:
            ex = v[0]["spec"].get("exit") or {}
            kol.append({"name": x["name"], "stop_loss_x": ex.get("stop_loss_x"), "cut_after_s": ex.get("cut_after_s"),
                        "win_rate": (v[0]["spec"].get("risk") or {}).get("win_rate")})
    return {"recovery": out, "coins_with_history": sum(1 for s in series.values() if len(s) >= 4), "kols": kol}


def rules(d: dict) -> dict:
    """The thesis in plain rules (template). Claude rewrites them when keyed."""
    sp, by, ho, cu = d["spot"], d["buy"], d["hold"], d["cut"]
    R = {"spot": [], "buy": [], "hold": [], "cut": []}
    R["spot"].append(f"Base rate: {sp['base_rate']}% of {sp['launches']:,} launches reached {_usd(d['runner_ath'])}. Anything below that is noise.")
    for r in sp["best"][:4]:
        if (r["lift"] or 0) >= 1.3:
            R["spot"].append(f"{r['feature']}: {r['hit_rate']}% hit rate, {r['lift']}x the base rate ({r['n']:,} launches).")
    for r in sp["worst"][:2]:
        R["spot"].append(f"Avoid / size down: {r['feature']} hits only {r['hit_rate']}% ({r['lift']}x).")
    for l in by["ladder"][:4]:
        R["buy"].append(f"Reached {_usd(l['from'])} → went on to {_usd(l['to'])} {l['pct']}% of the time ({l['n']:,} coins).")
    rf = by["real_vs_fake"]
    if rf["real_n"] and rf["fake_n"]:
        if (rf["real_to_500k"] or 0) >= (rf["fake_to_500k"] or 0):
            R["buy"].append(f"Buy what passes the checks: REAL runners reached $500k {rf['real_to_500k']}% of the time vs {rf['fake_to_500k']}% for FAKE.")
        else:
            R["buy"].append(f"Careful: FAKE runners printed bigger peaks here ({rf['fake_to_500k']}% reached $500k vs {rf['real_to_500k']}% REAL), "
                            "but painted charts dump on whoever is last. Trade them only as fast flips with a hard stop.")
    if by["speed"]:
        best = max(by["speed"], key=lambda s: s["to_500k"] or 0)
        R["buy"].append(f"Speed: runners that hit $100k {best['label']} went on to $500k most often ({best['to_500k']}%).")
    if ho["overall"].get("median_keep") is not None:
        R["hold"].append(f"Runners older than 6h keep a median {ho['overall']['median_keep']}% of their peak; {ho['overall']['faded_pct']}% fade below 30%. Take profit into the run.")
    for g in ho["groups"][:4]:
        R["hold"].append(f"{g['label']}: keeps {g['median_keep']}% of peak, {g['faded_pct']}% faded (n={g['n']}).")
    if ho["catalysts"]:
        top, bot = ho["catalysts"][0], ho["catalysts"][-1]
        R["hold"].append(f"Hold longer on {top['catalyst']} coins (keep {top['median_keep']}%); exit early on {bot['catalyst']} ({bot['median_keep']}%).")
    for r in cu["recovery"]:
        R["cut"].append(f"After a -{r['drawdown']}% drop from the high, only {r['new_high_pct']}% made a new high ({r['n']} cases): "
                        + ("cut it." if (r["new_high_pct"] or 0) < 25 else "a reload can work, but size down."))
    sl = [k["stop_loss_x"] for k in cu["kols"] if k.get("stop_loss_x")]
    if sl:
        R["cut"].append(f"Your tracked KOLs cut losers near {round((_med(sl) - 1) * 100)}% (median across {len(sl)} strategies).")
    if not cu["recovery"]:
        R["cut"].append("Not enough price history yet: the bot learns this as it snapshots runners every scan.")
    return R


def compute(tf_days: float = 7) -> dict:
    key = round(tf_days, 2)
    ttl = get_config().get_path("playbook.cache_min", 15) * 60
    hit = _cache.get(key)
    if hit and time.time() - hit["ts"] < ttl:
        return hit["data"]
    db = get_db()
    since = int(time.time() - tf_days * 86400)
    runner_ath = get_config().get_path("pipeline.runner_ath_usd", 100_000)
    coins = db.coins("created_ts >= ?", (since,), order="created_ts DESC", limit=None)
    runners = [c for c in coins if c.get("is_runner")]
    d = {"tf_days": tf_days, "runner_ath": runner_ath, "spot": spot(coins, runner_ath), "buy": buy(coins, runners),
         "hold": hold(runners), "cut": cut(runners), "ts": int(time.time())}
    d["rules"] = rules(d)
    d["by"] = "template"
    if llm.available():
        out = llm.ask("Data from a pump.fun scanner (JSON):\n" + json.dumps({k: d[k] for k in ("spot", "buy", "hold", "cut")}, default=str)[:12000]
                      + "\n\nRewrite as a trading thesis with four sections exactly titled SPOT, BUY, HOLD, CUT. Under each, 3-5 short "
                        "rules (one line each, start with '- ') using the numbers. No hype, no financial advice.",
                      system="You are a disciplined memecoin trader writing rules from data.", max_tokens=900)
        if out:
            sec, parsed = None, {"spot": [], "buy": [], "hold": [], "cut": []}
            for line in out.splitlines():
                u = line.strip().upper().strip("#*: ")
                if u in ("SPOT", "BUY", "HOLD", "CUT"):
                    sec = u.lower()
                elif sec and line.strip().startswith(("-", "•", "*")):
                    parsed[sec].append(line.strip().lstrip("-•* ").strip())
            if all(parsed.values()):
                d["rules"], d["by"] = parsed, "claude"
    _cache[key] = {"ts": time.time(), "data": d}
    return d
