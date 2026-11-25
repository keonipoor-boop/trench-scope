"""Thesis engine: window-vs-baseline stats per theme -> daily / hourly narrative thesis."""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict

from catalysts.base import CATEGORY_LABELS
from core import llm, narratives
from core.config import get_config
from core.db import get_db
from core.events import AgentRun, log

UNTHEMED = "Unthemed"


def event_ts(c: dict) -> int:
    """When a coin 'ran': the moment it crossed the runner threshold (or launch time if unknown)."""
    return c.get("ts_100k") or c.get("created_ts") or 0


def compute(window_h: float, baseline_h: float, now: float | None = None, coins: list[dict] | None = None) -> dict:
    cfg = get_config()
    tc = cfg.get("thesis", {})
    nc = cfg.get("narratives", {})
    now = now or time.time()
    start, base_start = now - window_h * 3600, now - (window_h + baseline_h) * 3600
    if coins is None:
        coins = get_db().light_coins(int(base_start - 86400))

    launches_now = [c for c in coins if start <= (c.get("created_ts") or 0) <= now]
    launches_base = [c for c in coins if base_start <= (c.get("created_ts") or 0) < start]
    runners_now = [c for c in coins if c.get("is_runner") and start <= event_ts(c) <= now]
    runners_base = [c for c in coins if c.get("is_runner") and base_start <= event_ts(c) < start]

    def by_theme(cs):
        d = defaultdict(list)
        for c in cs:
            for t in (c.get("themes") or [UNTHEMED]):
                d[t].append(c)
        return d

    ln, lb, rn, rb = by_theme(launches_now), by_theme(launches_base), by_theme(runners_now), by_theme(runners_base)
    no_baseline = not launches_base  # nothing earlier to compare with: momentum is neutral (1x), flagged
    overall_hit = len(runners_now) / len(launches_now) if launches_now else 0
    all_themes = list(narratives.themes()) + [UNTHEMED]
    rows = []
    for t in all_themes:
        r_now, r_base = len(rn[t]), len(rb[t])
        l_now, l_base = len(ln[t]), len(lb[t])
        rate_now = r_now / window_h
        rate_base = (r_base + 0.5) / baseline_h
        real = [c for c in rn[t] if c.get("is_real")]
        rows.append({
            "theme": t, "runners": r_now, "runners_base": r_base, "launches": l_now, "launches_base": l_base,
            "hit_rate": round(r_now / l_now * 100, 2) if l_now else 0.0,
            "hit_rate_base": round(r_base / l_base * 100, 2) if l_base else 0.0,
            "momentum": (1.0 if no_baseline else round(min(rate_now / rate_base, 20.0), 2)) if r_now else 0.0,
            "share": round(r_now / len(runners_now) * 100, 1) if runners_now else 0.0,
            "real_pct": round(len(real) / r_now * 100) if r_now else None,
            "top": [{"mint": c["mint"], "symbol": c.get("symbol"), "ath": c.get("ath_mcap")}
                    for c in sorted(rn[t], key=lambda c: -(c.get("ath_mcap") or 0))[:3]],
        })
    rows.sort(key=lambda r: (r["runners"], r["momentum"]), reverse=True)
    themed = [r for r in rows if r["theme"] != UNTHEMED and r["runners"] > 0]
    leader = themed[0]["theme"] if themed else None

    cats = Counter(c.get("primary_catalyst") or "unknown" for c in runners_now)
    ttt = [c["time_to_100k_min"] for c in runners_now if c.get("time_to_100k_min") is not None]
    emerging = narratives.emerging_words(runners_now, runners_base + launches_base[:0],
                                         nc.get("emerging_min_count", 2), nc.get("emerging_top_n", 10),
                                         nc.get("min_word_len", 3))

    # LOOK FOR / AVOID
    look, avoid = [], []
    for r in sorted(themed, key=lambda r: r["share"] * max(r["momentum"], 0.3) *
                    ((r["hit_rate"] / (overall_hit * 100)) if overall_hit else 1), reverse=True)[:tc.get("look_for_n", 4)]:
        if r["runners"] < 2 and window_h > 2:
            continue
        rel = f", {r['hit_rate'] / (overall_hit * 100):.1f}x avg hit rate" if overall_hit and r["hit_rate"] else ""
        real = f", {r['real_pct']}% REAL" if r["real_pct"] is not None else ""
        look.append(f"{r['theme']}: {r['runners']} runners, {r['hit_rate']}% hit rate{rel}, momentum {r['momentum']}x{real}")
    if cats:
        top_cat, n = cats.most_common(1)[0]
        look.append(f"Catalyst that works: {CATEGORY_LABELS.get(top_cat, top_cat)} ({n} of {len(runners_now)} runners)")
    fresh = [e for e in emerging if e["new_meta"]][:3]
    if fresh:
        look.append("Emerging words (no theme yet): " + ", ".join(f"\"{e['word']}\" x{e['count']}" for e in fresh))
    for r in rows:
        if r["theme"] == UNTHEMED:
            continue
        if r["launches"] >= tc.get("avoid_min_launches", 15) and overall_hit and \
                r["hit_rate"] < overall_hit * 100 * tc.get("avoid_hit_rate_ratio", 0.5):
            avoid.append(f"{r['theme']}: {r['launches']} launches, only {r['runners']} ran ({r['hit_rate']}% vs {overall_hit * 100:.1f}% avg)")
        elif r["runners"] >= 3 and (r["real_pct"] or 0) < 40:
            avoid.append(f"{r['theme']}: {100 - (r['real_pct'] or 0)}% of runners fail the REAL checks (botted/concentrated)")
    real_all = [c for c in runners_now if c.get("is_real")]
    return {
        "window_h": window_h, "baseline_h": baseline_h, "ts": int(now), "no_baseline": no_baseline,
        "launches": len(launches_now), "runners": len(runners_now), "runners_base": len(runners_base),
        "launches_base": len(launches_base),
        "hit_rate": round(overall_hit * 100, 2),
        "real_pct": round(len(real_all) / len(runners_now) * 100) if runners_now else None,
        "avg_ttt_min": round(statistics.mean(ttt)) if ttt else None,
        "median_ttt_min": round(statistics.median(ttt)) if ttt else None,
        "leader": leader, "themes": rows,
        "catalysts": [{"category": k, "label": CATEGORY_LABELS.get(k, k), "count": v} for k, v in cats.most_common()],
        "emerging": emerging, "look_for": look, "avoid": avoid[:5],
        "top_runners": [{"mint": c["mint"], "name": c.get("name"), "symbol": c.get("symbol"), "ath": c.get("ath_mcap"),
                         "catalyst": c.get("primary_catalyst"), "real": c.get("is_real")}
                        for c in sorted(runners_now, key=lambda c: -(c.get("ath_mcap") or 0))[:6]],
    }


def fmt_usd(v):
    if not v:
        return "$0"
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def template_text(kind: str, s: dict, shift: dict | None = None) -> str:
    lead = next((r for r in s["themes"] if r["theme"] == s["leader"]), None)
    span = "24h" if kind == "daily" else "hour"
    lines = []
    if lead:
        lines.append(f"{s['leader'].upper()} LEADS THE {span.upper()}: {lead['runners']} of {s['runners']} runners "
                     f"({lead['share']}%), {lead['hit_rate']}% hit rate, momentum {lead['momentum']}x vs baseline.")
    else:
        lines.append(f"No dominant theme this {span}: {s['runners']} runners from {s['launches']} launches.")
    if shift and shift.get("shift"):
        lines.append("META SHIFT: " + shift["reason"] + ".")
    lines.append(f"{s['runners']} runners / {s['launches']} launches ({s['hit_rate']}% hit rate)"
                 + (f", {s['real_pct']}% pass REAL" if s["real_pct"] is not None else "")
                 + (f", avg {s['avg_ttt_min']}m to 100k" if s["avg_ttt_min"] else "") + ".")
    if s["catalysts"]:
        mix = ", ".join(f"{c['label'].lower()} {c['count']}" for c in s["catalysts"][:4])
        lines.append(f"Catalyst mix: {mix}.")
    hot = [r for r in s["themes"] if r["momentum"] >= 1.5 and r["runners"] >= 2 and r["theme"] != s["leader"]]
    if hot:
        lines.append("Accelerating: " + ", ".join(f"{r['theme']} ({r['momentum']}x)" for r in hot[:3]) + ".")
    if s["top_runners"]:
        t = s["top_runners"][0]
        lines.append(f"Biggest runner: ${t['symbol']} at {fmt_usd(t['ath'])} ATH.")
    return " ".join(lines)


def llm_text(kind: str, s: dict, shift: dict | None) -> str | None:
    if not get_config().get_path("llm.thesis_prose", True):
        return None
    slim = {k: s[k] for k in ("runners", "launches", "hit_rate", "real_pct", "avg_ttt_min", "leader", "catalysts",
                              "emerging", "look_for", "avoid", "top_runners")}
    slim["themes"] = [r for r in s["themes"] if r["runners"] or r["launches"] > 10][:8]
    prompt = (f"Write the {kind} narrative thesis for pump.fun memecoin runners ({s['window_h']}h window vs "
              f"prior {s['baseline_h']}h). Data:\n{json.dumps(slim, default=str)}\n"
              + (f"A META SHIFT fired: {shift['reason']}.\n" if shift and shift.get("shift") else "")
              + "Start with a one-line uppercase headline, then 90-150 words of sharp, specific prose: what is "
                "working, why, what is fading. Cite numbers. No bullet lists, no financial advice.")
    return llm.ask(prompt, system="You are the lead analyst on a memecoin research desk. Terse, data-driven, no hype.")


def detect_shift(hourly: dict) -> dict:
    tc = get_config().get("thesis", {})
    daily = latest("daily")
    daily_leader = daily["leader"] if daily else None
    reasons = []
    if hourly["leader"] and daily_leader and hourly["leader"] != daily_leader:
        reasons.append(f"hourly leader {hourly['leader']} != daily leader {daily_leader}")
    hot = [r for r in hourly["themes"] if r["momentum"] > tc.get("meta_shift_momentum", 2.0)
           and r["runners"] >= tc.get("meta_shift_min_runners", 2) and r["theme"] != UNTHEMED]
    if hot:
        reasons.append(", ".join(f"{r['theme']} momentum {r['momentum']}x" for r in hot[:2]))
    return {"shift": bool(reasons), "reason": "; ".join(reasons), "daily_leader": daily_leader}


def generate(kind: str) -> dict:
    tc = get_config().get("thesis", {})
    with AgentRun("THESIS", f"writing {kind} thesis") as run:
        if kind == "daily":
            s = compute(tc.get("daily_window_h", 24), tc.get("daily_baseline_h", 72))
            shift = None
        else:
            s = compute(tc.get("hourly_window_h", 1), tc.get("hourly_baseline_h", 6))
            shift = detect_shift(s)
            s["meta_shift"] = shift
        text = llm_text(kind, s, shift)
        s["prose_by"] = "claude" if text else "template"
        text = text or template_text(kind, s, shift)
        tid = get_db().x("INSERT INTO theses(kind,ts,leader,meta_shift,text,data) VALUES(?,?,?,?,?,?)",
                         (kind, int(time.time()), s["leader"], int(bool(shift and shift["shift"])), text,
                          json.dumps(s, default=str)))
        msg = f"{kind} thesis #{tid}: leader {s['leader'] or '-'}, {s['runners']} runners"
        if shift and shift["shift"]:
            msg += f"  ** META SHIFT ** ({shift['reason']})"
        log("THESIS", msg, "warn" if shift and shift["shift"] else "info")
        run.result = msg
        out = {"id": tid, "kind": kind, "text": text, "data": s}
        try:
            from core import drafts
            drafts.on_thesis(kind, out)
        except Exception as e:
            log("THESIS", f"drafts: {e}", "warn")
        return out


def latest(kind: str) -> dict | None:
    r = get_db().q("SELECT * FROM theses WHERE kind=? ORDER BY ts DESC, id DESC LIMIT 1", (kind,))
    if not r:
        return None
    r = r[0]
    d = json.loads(r["data"])
    return {"id": r["id"], "kind": r["kind"], "ts": r["ts"], "leader": r["leader"],
            "meta_shift": bool(r["meta_shift"]), "text": r["text"], "data": d}
