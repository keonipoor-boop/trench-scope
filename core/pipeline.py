"""The scan pipeline.

  1. DISCOVER   every source with discover() -> new coins in the lookback window
  2. CHEAP      batched enrichment (DexScreener...) on all coins that are due
  3. OHLCV      ATH + time-to-100k on candidates only (rate-limited, cached)
  4. RUNNERS    ATH >= pipeline.runner_ath_usd
  5. DEEP       expensive enrichment (Helius, KOLs, X) on runners, max every deep_refresh_min
  6. SIGNALS    -> is_real
  7. CATALYSTS  -> primary catalyst + why-it-ran bullets (Claude if key set)
  8. SNAPSHOT   runner snapshots for history
"""
from __future__ import annotations

import time
from collections import defaultdict

from catalysts.base import load_rules, classify
from core import llm, narratives
from core.config import get_config, load_json_data
from core.db import get_db
from core.events import AgentRun, agent_status, log
from signals.base import evaluate_all, load_signals
from sources.base import load_sources


def _apply(coins_by_mint: dict, updates: dict, stamp_field: str | None = None, now: int = 0):
    for mint, upd in (updates or {}).items():
        c = coins_by_mint.get(mint)
        if c is None:
            continue
        for k, v in upd.items():
            if v is None:
                continue
            if k == "ath_mcap":
                c[k] = max(float(c.get(k) or 0), float(v))
            else:
                c[k] = v
    if stamp_field:
        for c in coins_by_mint.values():
            c[stamp_field] = now


def derive(c: dict, thr: float):
    c["ath_mcap"] = max(float(c.get("ath_mcap") or 0), float(c.get("pf_ath_mcap") or 0),
                        float(c.get("usd_market_cap") or 0))
    if c.get("ts_100k") and c.get("created_ts"):
        c["time_to_100k_min"] = max(0, round((c["ts_100k"] - c["created_ts"]) / 60, 1))
    c["is_runner"] = c["ath_mcap"] >= thr


def template_why(c: dict) -> list[str]:
    out = [h["bullet"] for h in (c.get("catalysts") or [])[:2]]
    sig = c.get("signals") or {}
    if c.get("time_to_100k_min") is not None:
        out.append(f"Hit $100k in {c['time_to_100k_min']:.0f} min, peaked at ${c['ath_mcap'] / 1e3:,.0f}k")
    elif sig.get("holders", {}).get("value"):
        out.append(sig["holders"]["note"])
    if c.get("themes"):
        out.append("Rode the " + ", ".join(c["themes"][:2]) + " narrative")
    if c.get("is_real") is False:
        fails = [s["label"] for s in sig.values() if s.get("required") and s.get("passed") is False]
        if fails:
            out.append("Caution: fails " + ", ".join(fails) + " checks")
    return out[:3]


def scan(backfill_days: float | None = None) -> dict:
    cfg = get_config()
    db = get_db()
    pc = cfg.get("pipeline", {})
    now = int(time.time())
    lookback = backfill_days if backfill_days is not None else pc.get("lookback_days", 7)
    since = now - int(lookback * 86400)
    thr = pc.get("runner_ath_usd", 100_000)
    sources = load_sources(cfg)
    stats = {"discovered": 0, "cheap": 0, "ohlcv": 0, "deep": 0, "runners": 0, "real": 0}
    t0 = time.time()
    log("SCOUT", f"scan start: {len(sources)} sources, lookback {lookback}d"
        + (" [DEMO MODE]" if cfg.demo else ""))

    # 1. DISCOVER ------------------------------------------------------------
    with AgentRun("SCOUT", "discovering launches") as run:
        found: dict[str, dict] = {}
        for s in sources:
            if not s.can_discover():
                continue
            try:
                for c in s.discover(since):
                    c.setdefault("sources", [])
                    found.setdefault(c["mint"], {}).update({k: v for k, v in c.items() if v is not None})
            except Exception as e:
                log("SCOUT", f"{s.name}.discover failed: {e}", "error")
        existing = {r["mint"] for r in db.q("SELECT mint FROM coins WHERE created_ts >= ? OR created_ts IS NULL", (since,))}
        new = [c for m, c in found.items() if m not in existing]
        for c in found.values():
            if c.get("usd_market_cap"):
                derive(c, thr)
            c["themes"] = narratives.tag(c)
        db.upsert_many(list(found.values()))
        stats["discovered"] = len(found)
        run.result = f"{len(found)} coins seen, {len(new)} new"
        log("SCOUT", run.result)

    # Which coins get enriched. "candidates" (live default): graduated coins, coins already near the
    # runner bar, and coins whose launch time we don't know yet. Every coin over $100k has graduated,
    # so this covers all runners without enriching 20k+ dead launches a day.
    if pc.get("cheap_scope", "candidates") == "all":
        window = {c["mint"]: c for c in db.coins("created_ts >= ? OR created_ts IS NULL", (since,))}
    else:
        window = {c["mint"]: c for c in db.coins(
            "(created_ts >= ? AND (json_extract(data,'$.complete') IN (1, 'true') OR ath_mcap >= ?)) OR created_ts IS NULL",
            (since, pc.get("ath_prefilter_usd", 40_000)))}

    # 2. CHEAP enrichment -----------------------------------------------------
    with AgentRun("SCOUT", "cheap enrichment") as run:
        young_cut = now - 86400
        due = [c for c in window.values() if (c.get("cheap_ts") or 0) <
               now - 60 * (pc.get("cheap_refresh_min", 10) if (c.get("created_ts") or 0) > young_cut else 60)]
        due.sort(key=lambda c: -(c.get("created_ts") or 0))
        due = due[: pc.get("max_cheap_per_scan", 1500)]
        batch = {c["mint"]: c for c in due}
        for s in sources:
            if s.stage == "cheap" and s.can_enrich() and batch:
                try:
                    _apply(batch, s.enrich(list(batch.values())))
                except Exception as e:
                    log("SCOUT", f"{s.name}.enrich failed: {e}", "error")
        for c in batch.values():
            c["cheap_ts"] = now
            derive(c, thr)
            c["themes"] = narratives.tag(c)
        db.upsert_many(list(batch.values()))
        for m in [m for m, c in window.items() if not c.get("created_ts") or c["created_ts"] < since]:
            window.pop(m)  # launch time still unknown, or older than the lookback
        stats["cheap"] = len(batch)
        run.result = f"cheap-enriched {len(batch)} coins"
        log("SCOUT", run.result)

    # 3. OHLCV on ATH candidates ---------------------------------------------
    ohlcv_sources = [s for s in sources if s.stage == "ohlcv" and s.can_enrich()]
    if ohlcv_sources:
        with AgentRun("SCOUT", "ATH via OHLCV") as run:
            pre = pc.get("ath_prefilter_usd", 40_000)
            cand = [c for c in window.values()
                    if ((c.get("ath_mcap") or 0) >= pre or c.get("complete"))
                    and (c.get("ohlcv_ts") or 0) < now - 60 * pc.get("ohlcv_refresh_min", 60)]
            # never-checked coins first (newest first): that's where a dumped coin's hidden
            # $100k+ ATH lives. Then refresh the rest, biggest first.
            cand.sort(key=lambda c: (bool(c.get("ohlcv_ts")), -(c.get("created_ts") or 0) if not c.get("ohlcv_ts")
                                     else -(c.get("ath_mcap") or 0)))
            batch = {c["mint"]: c for c in cand[: pc.get("max_ohlcv_per_scan", 60)]}
            for s in ohlcv_sources:
                if batch:
                    try:
                        _apply(batch, s.enrich(list(batch.values())))
                    except Exception as e:
                        log("SCOUT", f"{s.name}.enrich failed: {e}", "error")
            for c in batch.values():
                if c.get("ohlcv_ath") is not None or c.get("ohlcv_ts"):
                    c["ohlcv_ts"] = now  # throttled coins keep ohlcv_ts=0 and go first next scan
                derive(c, thr)
            db.upsert_many(list(batch.values()))
            stats["ohlcv"] = len(batch)
            run.result = f"ATH checked on {len(batch)} candidates"
            log("SCOUT", run.result)

    # 4. RUNNERS + 5. DEEP enrichment ------------------------------------------
    for c in window.values():
        derive(c, thr)
    runners = {m: c for m, c in window.items() if c["is_runner"]}
    stats["runners"] = len(runners)
    due = sorted([c for c in runners.values() if (c.get("deep_ts") or 0) < now - 60 * pc.get("deep_refresh_min", 30)],
                 key=lambda c: -(c.get("ath_mcap") or 0))[: pc.get("max_deep_per_scan", 40)]
    deep = {c["mint"]: c for c in due}
    log("SCOUT", f"{len(runners)} runners >= ${thr / 1e3:.0f}k ATH, {len(deep)} due for deep enrichment")
    by_agent = defaultdict(list)
    for s in sources:
        if s.stage == "expensive" and s.can_enrich():
            by_agent[s.agent].append(s)
    for agent, srcs in by_agent.items():
        if not deep:
            break
        with AgentRun(agent, f"deep enrichment on {len(deep)} runners") as run:
            for s in srcs:
                try:
                    _apply(deep, s.enrich(list(deep.values())))
                except Exception as e:
                    log(agent, f"{s.name}.enrich failed: {e}", "error")
            run.result = f"{', '.join(s.name for s in srcs)} -> {len(deep)} runners"
            log(agent, run.result)
    for c in deep.values():
        c["deep_ts"] = now
    stats["deep"] = len(deep)

    # 6. SIGNALS + 7. CATALYSTS ----------------------------------------------
    signals = load_signals(cfg)
    rules = load_rules()
    ctx = {"cfg": cfg, "celebrities": {x.lower() for x in load_json_data("celebrities.json", [])}}
    llm_budget = 10 if llm.available() and cfg.get_path("llm.why_bullets", True) else 0
    with AgentRun("CATALYST", "classifying catalysts") as run:
        for c in runners.values():
            c["signals"], c["is_real"] = evaluate_all(c, signals)
            c["primary_catalyst"], c["catalysts"] = classify(c, rules, ctx)
            c["why_template"] = template_why(c)
            if llm_budget and not c.get("why_llm") and c.get("deep_ts"):
                b = llm.why_bullets(c)
                llm_budget -= 1
                if b:
                    c["why_llm"] = b
            c["why"] = c.get("why_llm") or c["why_template"]
        real = sum(1 for c in runners.values() if c["is_real"])
        stats["real"] = real
        mix = defaultdict(int)
        for c in runners.values():
            mix[c["primary_catalyst"]] += 1
        run.result = f"{len(runners)} runners classified, top: " + ", ".join(
            f"{k} {v}" for k, v in sorted(mix.items(), key=lambda x: -x[1])[:3])
        log("CATALYST", run.result)
    # per-signal agent summaries (HOLDERS / FEES / KOLS rings)
    for agent, keys in (("HOLDERS", ("holders", "top10")), ("FEES", ("fees", "traders")), ("KOLS", ("kols",))):
        checked = [c for c in runners.values() if any((c.get("signals") or {}).get(k, {}).get("passed") is not None for k in keys)]
        passed = [c for c in checked if all((c["signals"].get(k) or {}).get("passed") is not False for k in keys)]
        if checked:
            agent_status(agent, "ok", f"{len(passed)}/{len(checked)} runners pass {'+'.join(keys)}")
            log(agent, f"{len(passed)}/{len(checked)} runners pass {'+'.join(keys)}")
        elif agent not in by_agent:
            agent_status(agent, "idle", "no source configured (see doctor)")
    db.upsert_many(list(runners.values()))

    # 8. SNAPSHOTS -------------------------------------------------------------
    with db._lock:
        for c in deep.values():
            db.conn.execute("INSERT INTO snapshots(mint,ts,mcap,holders,volume_24h,data) VALUES(?,?,?,?,?,?)",
                            (c["mint"], now, c.get("usd_market_cap"), c.get("holders"), c.get("volume_24h"), None))
        db.conn.commit()

    # keep the db small: drop non-runners well past the lookback (launch counts need ~4 extra days)
    keep = now - int((pc.get("lookback_days", 7) + 4) * 86400)
    db.x("DELETE FROM coins WHERE is_runner=0 AND (created_ts < ? OR (created_ts IS NULL AND updated_ts < ?))",
         (keep, now - 3 * 86400))

    stats["seconds"] = round(time.time() - t0, 1)
    db.kv_set("last_scan", {"ts": now, **stats})
    log("SCOUT", f"scan done in {stats['seconds']}s: {stats['runners']} runners, {stats['real']} REAL")
    return stats
