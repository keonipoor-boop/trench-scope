"""DEV LAB: finds the narratives runners create and proposes RELATED coin ideas ("Golden Inu runs -> a Golden Dog play"),
then checks whether each idea WOULD HAVE BEEN RIGHT. Nothing is ever launched: it's paper deving.

  parents    today's runners (REAL preferred), with their key words (name minus filler words)
  beta stats after a runner with word W launched, how many later coins with W launched and how many ran (vs the base rate)
  ideas      3 concepts per parent: keep one key word, swap the other (template), or Claude (when keyed). Each must be
             related but clearly distinct: `originality()` flags an exact ticker or a near-identical name (a clone that
             would trick buyers), and such ideas are marked "clone risk".
  verdict    24h after an idea: did a coin sharing its key word, launched AFTER the idea, reach the runner bar?
             right = yes, partial = reached $50k, wrong = neither.
  readiness  0.0-100 from the verdicts: sample size, hit rate vs the base rate (lift), recent form, time; hard caps when
             the record is thin, no better than chance, or demo data. Information only."""
from __future__ import annotations

import difflib
import json
import random
import re
import threading
import time

from core import llm
from core.config import get_config
from core.db import get_db
from core.events import log

FILLER = {"the", "coin", "token", "sol", "solana", "official", "of", "on", "and", "a", "an", "inu", "ai", "meme", "fun", "pump",
          "dot", "is", "my", "to", "in", "for", "by", "with", "this", "that"}
ANIMALS = ["dog", "cat", "frog", "pepe", "bull", "ape", "duck", "bear", "penguin", "hamster", "goat", "shiba", "wolf", "monkey", "fish"]
ADJ = ["golden", "baby", "based", "giga", "mini", "dark", "cyber", "retro", "sad", "happy", "tiny", "super", "lucky", "angry"]
RESOLVE_AFTER = 24 * 3600


def bars() -> tuple[float, float]:
    """(right, partial) market caps for a follow-on coin: dev plays don't need $100k to have been right."""
    c = get_config().get("devlab", {}) or {}
    return float(c.get("right_mcap", 40_000)), float(c.get("partial_mcap", 30_000))


def ensure_table():
    get_db().x("""CREATE TABLE IF NOT EXISTS dev_ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, parent_mint TEXT,
                  parent_symbol TEXT, parent_name TEXT, name TEXT, ticker TEXT, keyword TEXT, pitch TEXT, by TEXT,
                  originality TEXT, verdict TEXT, outcome TEXT)""")
    db = get_db()
    if "kit" not in {r["name"] for r in db.q("PRAGMA table_info(dev_ideas)")}:
        db.x("ALTER TABLE dev_ideas ADD COLUMN kit TEXT")
        db.x("ALTER TABLE dev_ideas ADD COLUMN source TEXT")


def words(name: str | None) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", (name or "").lower()) if w not in FILLER]


def _ticker(name: str) -> str:
    w = re.sub(r"[^A-Za-z ]", "", name).split()
    t = "".join(x[0] for x in w) if len(w) >= 3 else (w[0][:4] + (w[1][:3] if len(w) > 1 else "")) if w else "IDEA"
    return t.upper()[:7]


def originality(name: str, ticker: str, recent: list[dict]) -> dict:
    """A related idea is fine; a clone isn't. Exact ticker or a near-identical name = clone risk."""
    n = name.lower()
    for c in recent:
        if (c.get("symbol") or "").upper() == ticker.upper():
            return {"ok": False, "why": f"ticker ${ticker} is already taken by {c.get('name') or c['mint'][:6]}", "mint": c["mint"]}
        r = difflib.SequenceMatcher(None, n, (c.get("name") or "").lower()).ratio()
        if r >= 0.86:
            return {"ok": False, "why": f"name is {round(r * 100)}% the same as {c.get('name')} (${c.get('symbol')}): clone risk", "mint": c["mint"]}
    return {"ok": True, "why": "distinct name and ticker"}


def beta_stats(parent: dict, coins: list[dict], runner_ath: float) -> dict:
    """Coins launched after the parent (within 48h) that reuse one of its key words, and how they did."""
    ws = words(parent.get("name")) or words(parent.get("symbol"))
    t0 = parent.get("created_ts") or 0
    out = {}
    for w in ws[:3]:
        followers = [c for c in coins if c["mint"] != parent["mint"] and t0 < (c.get("created_ts") or 0) <= t0 + 48 * 3600
                     and w in (c.get("name") or "").lower()]
        ran = [c for c in followers if (c.get("ath_mcap") or 0) >= runner_ath]
        out[w] = {"launched": len(followers), "ran": len(ran), "rate": round(len(ran) / len(followers) * 100, 1) if followers else None,
                  "best": max(followers, key=lambda c: c.get("ath_mcap") or 0, default=None)}
        if out[w]["best"]:
            b = out[w]["best"]
            out[w]["best"] = {"mint": b["mint"], "symbol": b.get("symbol"), "name": b.get("name"), "ath": b.get("ath_mcap"), "image": b.get("image")}
    return out


def _template_ideas(parent: dict, rnd: random.Random) -> list[dict]:
    ws = words(parent.get("name")) or [w.lower() for w in words(parent.get("symbol"))] or ["meme"]
    out = []
    animal = next((w for w in ws if w in ANIMALS), None)
    adj = next((w for w in ws if w in ADJ), None)
    key = adj or ws[0]
    if animal:
        for a in rnd.sample([x for x in ANIMALS if x != animal], 2):
            out.append((f"{key.title()} {a.title()}", key, f"the {key} meta, new animal: rides {parent.get('symbol')}'s narrative without copying it"))
        out.append((f"{rnd.choice([x for x in ADJ if x != key]).title()} {animal.title()}", animal, f"the {animal} crowd from {parent.get('symbol')}, a fresh twist"))
    else:
        for a in rnd.sample(ANIMALS, 2):
            out.append((f"{key.title()} {a.title()}", key, f"puts a mascot on the '{key}' narrative {parent.get('symbol')} opened"))
        out.append((f"{rnd.choice(ADJ).title()} {key.title()}", key, f"a sequel-style play on '{key}', distinct name and ticker"))
    return [{"name": n, "ticker": _ticker(n), "keyword": k, "pitch": p, "by": "template"} for n, k, p in out]


def _llm_ideas(parent: dict, stats: dict) -> list[dict] | None:
    if not llm.available():
        return None
    out = llm.ask(
        f"A Solana memecoin is running: {parent.get('name')} (${parent.get('symbol')}), description: {(parent.get('description') or '')[:300]}. "
        f"Follow-on launches reusing its words so far: {json.dumps({w: {k: v for k, v in s.items() if k != 'best'} for w, s in stats.items()})}.\n"
        "Propose 3 RELATED but clearly DISTINCT coin concepts that play the same narrative. Never reuse its exact name, ticker or "
        "logo, never imply affiliation with it. JSON list only: [{\"name\":..., \"ticker\":..., \"keyword\": <the shared narrative "
        "word, lowercase, one word from its name>, \"pitch\": <one line>}]",
        system="You help a memecoin creator brainstorm original narrative plays. No clones, no impersonation.", max_tokens=500)
    try:
        ideas = json.loads(out[out.index("["):out.rindex("]") + 1]) if out else None
    except (ValueError, json.JSONDecodeError):
        return None
    clean = []
    for i in ideas or []:
        if isinstance(i, dict) and i.get("name") and i.get("keyword"):
            clean.append({"name": str(i["name"])[:40], "ticker": re.sub(r"[^A-Z0-9]", "", str(i.get("ticker") or _ticker(i["name"])).upper())[:8],
                          "keyword": str(i["keyword"]).lower()[:20], "pitch": str(i.get("pitch") or "")[:160], "by": "claude"})
    return clean[:3] or None


def new_pair_narratives(window_min: int = 45, baseline_h: int = 6) -> list[dict]:
    """Words spiking in FRESH launches (New Pairs): launches in the last `window_min` vs the `baseline_h` before.
    A narrative forming before any coin has run. Lead = the strongest coin carrying the word."""
    from collections import defaultdict
    db, now = get_db(), time.time()
    if get_config().demo:  # demo launches are spread over days: compare the last day with the week
        window_min, baseline_h = 24 * 60, 6 * 24
    w0, b0 = now - window_min * 60, now - window_min * 60 - baseline_h * 3600
    rows = db.coins("created_ts >= ?", (int(b0),), order="created_ts DESC", limit=None)
    cur, base, lead = defaultdict(int), defaultdict(int), {}
    for c in rows:
        ws = set(words(c.get("name")))
        for w in ws:
            if (c.get("created_ts") or 0) >= w0:
                cur[w] += 1
                if w not in lead or (c.get("usd_market_cap") or c.get("ath_mcap") or 0) > (lead[w].get("usd_market_cap") or lead[w].get("ath_mcap") or 0):
                    lead[w] = c
            else:
                base[w] += 1
    out = []
    for w, n in cur.items():
        if n < 3:
            continue
        rate_now = n / window_min
        rate_base = base[w] / (baseline_h * 60) if base[w] else 0
        spike = rate_now / rate_base if rate_base else float(n)
        if spike >= 2:
            c = lead[w]
            out.append({"word": w, "count": n, "spike": round(spike, 1), "mint": c["mint"], "symbol": c.get("symbol"), "name": c.get("name"),
                        "mcap": c.get("usd_market_cap") or c.get("ath_mcap"), "image": c.get("image")})
    out.sort(key=lambda x: (-x["spike"] * min(x["count"], 10)))
    return out[:12]


def kit(i: dict, parent: dict) -> dict:
    """Everything Axiom's Token Deploy form asks for, ready to copy. Nothing is deployed from here."""
    from core.links import parse_twitter
    post = None
    for f in ("twitter", "twitter_dex"):  # a coin built on a post: the narrative's post is the natural POST link
        if parse_twitter(parent.get(f))["kind"] == "status":
            post = parent[f]
            break
    name = i["name"][:32]
    sym = re.sub(r"[^A-Z0-9]", "", i["ticker"].upper())[:13]
    desc = f"{name} (${sym}). The {i['keyword']} meta's newest face. Community first, no promises."
    return {"name": name, "symbol": sym, "description": desc[:300], "website": "", "post": post,
            "image_prompt": f"{name}: a bold, simple meme mascot, {i['keyword']} theme, flat colors, clean background, centered, no text",
            "platform": "Pump", "dev_buy_presets": [1, 3, 5, 10], "dev_buy_sol": 1}


def generate(max_parents: int = 12) -> int:
    """New ideas for today's runners that don't have ideas yet."""
    ensure_table()
    db, now = get_db(), time.time()
    runner_ath = get_config().get_path("pipeline.runner_ath_usd", 100_000)
    window = 30 * 86400 if get_config().demo else 24 * 3600
    parents = db.coins("is_runner=1 AND created_ts >= ?", (int(now - window),), order="ath_mcap DESC", limit=60)
    parents = [p for p in parents if p.get("is_real") is not False]
    mine = _tracked_wallets()
    parents.sort(key=lambda p: (0 if (p.get("dev_wallet") or p.get("creator")) in mine else 1))  # your tracked devs first
    have = {r["parent_mint"] for r in db.q("SELECT DISTINCT parent_mint FROM dev_ideas")}
    recent = db.coins("created_ts >= ?", (int(now - 7 * 86400),), order="created_ts DESC", limit=None)
    made = 0
    todo = [(p, "runner") for p in parents if p["mint"] not in have]
    # New Pairs: a spiking word's lead coin is a parent too (narratives forming before anything has run)
    for nw in new_pair_narratives():
        if nw["mint"] not in have and all(nw["mint"] != p["mint"] for p, _ in todo):
            lead = db.get_coin(nw["mint"]) or {"mint": nw["mint"], "name": nw["name"], "symbol": nw["symbol"]}
            lead = {**lead, "_word": nw["word"]}
            todo.append((lead, "new pairs"))
    runners_q = [t for t in todo if t[1] == "runner"]
    pairs_q = [t for t in todo if t[1] == "new pairs"]
    mixed = []  # alternate so New Pairs narratives are never crowded out by runners
    while runners_q or pairs_q:
        if pairs_q:
            mixed.append(pairs_q.pop(0))
        if runners_q:
            mixed.append(runners_q.pop(0))
    for p, source in mixed[:max_parents]:
        stats = beta_stats(p, recent, runner_ath)
        if p.get("_word"):  # keep the spiking word as the narrative
            p = {**p, "name": f"{p['_word']} {p.get('name') or ''}"}
        ideas = _llm_ideas(p, stats) or _template_ideas(p, random.Random(p["mint"]))
        # in demo, the "idea time" is the parent's launch so verdicts can be checked against the data that followed
        ts = int(p.get("created_ts") or now) if get_config().demo else int(now)
        for i in ideas:
            o = originality(i["name"], i["ticker"], recent)
            db.x("INSERT INTO dev_ideas(ts,parent_mint,parent_symbol,parent_name,name,ticker,keyword,pitch,by,originality,verdict,outcome,kit,source) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ts, p["mint"], p.get("symbol"), p.get("name"), i["name"], i["ticker"], i["keyword"],
                                                          i["pitch"], i["by"], json.dumps(o), None, None, json.dumps(kit(i, p)), source))
            made += 1
    invalidate()
    if made:
        log("CATALYST", f"DEV LAB: {made} narrative ideas from {min(len(todo), max_parents)} narratives (runners + new pairs)")
    return made


def resolve() -> int:
    """Would it have been right? Check ideas older than 24h against what launched after them."""
    ensure_table()
    db, now = get_db(), time.time()
    runner_ath = get_config().get_path("pipeline.runner_ath_usd", 100_000)
    due = [dict(r) for r in db.q("SELECT * FROM dev_ideas WHERE verdict IS NULL AND ts <= ?", (int(now - RESOLVE_AFTER),))]
    n = 0
    for d in due:
        later = db.coins("created_ts > ? AND created_ts <= ? AND mint != ?", (d["ts"], d["ts"] + RESOLVE_AFTER, d["parent_mint"]),
                         order="ath_mcap DESC", limit=None)
        match = [c for c in later if d["keyword"] in (c.get("name") or "").lower()]
        best = match[0] if match else None
        ath = (best or {}).get("ath_mcap") or 0
        right_at, partial_at = bars()
        verdict = "right" if ath >= right_at else "partial" if ath >= partial_at else "wrong"
        outcome = {"matches": len(match), "best": {"mint": best["mint"], "symbol": best.get("symbol"), "name": best.get("name"),
                                                    "ath": ath, "image": best.get("image")} if best else None}
        db.x("UPDATE dev_ideas SET verdict=?, outcome=? WHERE id=?", (verdict, json.dumps(outcome), d["id"]))
        n += 1
    return n


def base_rate() -> float:
    """Share of all launches that reach the RIGHT bar (same bar as the verdicts, so the chance level is fair)."""
    db, now = get_db(), time.time()
    r = db.q("SELECT COUNT(*) n, SUM(ath_mcap >= ?) h FROM coins WHERE created_ts >= ?", (bars()[0], int(now - 7 * 86400)))[0]
    return (r["h"] or 0) / r["n"] if r["n"] else 0


def readiness() -> dict:
    """DEV readiness 0.0-100: would the narrative plays have been right often enough, versus launching anything at random?
    Compared like for like: P(a word-matched follow-on coin within 24h reaches the bar)."""
    ensure_table()
    rows = [dict(r) for r in get_db().q("SELECT * FROM dev_ideas WHERE verdict IS NOT NULL ORDER BY ts")]
    n = len(rows)
    right = sum(1 for r in rows if r["verdict"] == "right")
    partial = sum(1 for r in rows if r["verdict"] == "partial")
    rate = right / n if n else 0
    base = base_rate()
    # baseline for "some coin with a random word ran within 24h" is far higher than one coin's base rate:
    # use each idea's own follow-on count to build the chance level
    chance = 0.0
    for r in rows:
        m = (json.loads(r["outcome"] or "{}") or {}).get("matches") or 0
        chance += 1 - (1 - base) ** m
    chance = chance / n if n else 0
    lift = rate / chance if chance else (2.0 if rate else 0)
    last = rows[-20:]
    recent = sum(1 for r in last if r["verdict"] == "right") / len(last) if last else 0
    span_d = (rows[-1]["ts"] - rows[0]["ts"]) / 86400 if n > 1 else 0
    clones = sum(1 for r in rows if not json.loads(r["originality"] or "{}").get("ok", True))
    parts = {
        "sample": (min(1, n / 60), 30, f"{n} ideas checked (60 = full credit)"),
        "edge": (max(0, min(1, (lift - 1) / 2)), 40, f"right {rate * 100:.0f}% vs {chance * 100:.0f}% by chance (lift {lift:.2f}x; 3x = full)"),
        "recent": (min(1, recent / max(rate, 0.01)) if rate else 0, 20, f"last {len(last)} ideas right {recent * 100:.0f}%"),
        "time": (min(1, span_d / 7), 10, f"{span_d:.1f} days of ideas (7 = full)"),
    }
    score = sum(v * w for v, w, _ in parts.values())
    caps = []
    if get_config().demo:
        caps.append((15.0, "demo data: generated launches can't prove a narrative edge"))
    if n < 20:
        caps.append((35.0, f"needs at least 20 checked ideas ({20 - n} to go)"))
    if lift < 1.5:
        caps.append((30.0, f"lift {lift:.2f}x: not clearly better than chance"))
    for cap, _ in caps:
        score = min(score, cap)
    verdict = "not ready" if score < 40 else "promising: keep paper deving" if score < 70 else "ready for a careful live test" if score < 85 else "ready"
    return {"score": round(score, 1), "verdict": verdict, "caps": [c[1] for c in caps], "right": right, "partial": partial, "n": n,
            "clone_risk": clones, "parts": {k: {"score": round(v * 100), "weight": w, "note": t} for k, (v, w, t) in parts.items()}}


def dev_x(t: dict) -> dict:
    """A tracked dev's own X bio + pinned post (what they say they're building), refreshed every 6h."""
    from core import devs
    h = devs.handle_of(t)
    if not h:
        return {}
    db, key = get_db(), f"devx:{h.lower()}"
    hit = db.kv_get(key)
    if hit and time.time() - hit.get("ts", 0) < 6 * 3600:
        return hit
    out = {"ts": int(time.time())}
    if not get_config().demo:
        try:
            from sources.socials import Socials
            out.update({k: v for k, v in Socials(get_config()).x_profile(h).items() if v})
        except Exception:
            pass
    db.kv_set(key, out)
    return out


def dev_metas(days: float = 3) -> list[dict]:
    """What your tracked devs (DEV SYSTEM) are launching lately: their words, themes, best coin, and their own X posts."""
    from collections import Counter

    from core import devs, narratives
    db, since = get_db(), int(time.time() - days * 86400)
    out = []
    for t in devs.tracked():
        coins = []
        if t["wallets"]:
            q = ",".join("?" * len(t["wallets"]))
            coins = db.coins(f"(json_extract(data,'$.dev_wallet') IN ({q}) OR json_extract(data,'$.creator') IN ({q})) AND created_ts >= ?",
                             (*t["wallets"], *t["wallets"], since), order="created_ts DESC", limit=200)
        wc, th = Counter(), Counter()
        for c in coins:
            wc.update(set(words(c.get("name"))))
            th.update(c.get("themes") or narratives.tag(c))
        best = max(coins, key=lambda c: c.get("ath_mcap") or 0, default=None)
        x = dev_x(t)
        out.append({"name": t["name"], "handle": devs.handle_of(t), "launches": len(coins), "words": [w for w, _ in wc.most_common(6)],
                    "themes": [w for w, _ in th.most_common(3)], "needs_wallet": not t["wallets"],
                    "best": {"mint": best["mint"], "symbol": best.get("symbol"), "ath": best.get("ath_mcap")} if best else None,
                    "x_bio": x.get("x_bio"), "x_pinned": x.get("x_pinned_text"),
                    "latest": [{"mint": c["mint"], "symbol": c.get("symbol"), "name": c.get("name")} for c in coins[:4]]})
    return out


def _tracked_wallets() -> set:
    from core import devs
    return {w for t in devs.tracked() for w in t["wallets"]}


_STATE_CACHE = {"ts": 0.0, "v": None}


def state() -> dict:
    """Cached 10s: the lab's data changes every 10 minutes (and on 'new ideas')."""
    if _STATE_CACHE["v"] is not None and time.time() - _STATE_CACHE["ts"] < 10:
        return _STATE_CACHE["v"]
    v = _state()
    _STATE_CACHE.update(ts=time.time(), v=v)
    return v


def invalidate():
    _STATE_CACHE["ts"] = 0


def _state() -> dict:
    ensure_table()
    db, now = get_db(), time.time()
    runner_ath = get_config().get_path("pipeline.runner_ath_usd", 100_000)
    recent = db.coins("created_ts >= ?", (int(now - 7 * 86400),), order="created_ts DESC", limit=None)
    window = 30 * 86400 if get_config().demo else 24 * 3600
    parents = [p for p in db.coins("is_runner=1 AND created_ts >= ?", (int(now - window),), order="ath_mcap DESC", limit=12)
               if p.get("is_real") is not False][:8]
    ideas = [dict(r) for r in db.q("SELECT * FROM dev_ideas ORDER BY id DESC LIMIT 120")]
    for i in ideas:
        i["originality"] = json.loads(i["originality"] or "{}")
        i["kit"] = json.loads(i["kit"]) if i.get("kit") else None
        i["outcome"] = json.loads(i["outcome"]) if i["outcome"] else None
        i["resolves_in"] = max(0, int(i["ts"] + RESOLVE_AFTER - now)) if not i["verdict"] else 0
    return {"narratives": [{"mint": p["mint"], "symbol": p.get("symbol"), "name": p.get("name"), "image": p.get("image"),
                            "ath": p.get("ath_mcap"), "age_h": round((now - (p.get("created_ts") or now)) / 3600, 1),
                            "is_real": p.get("is_real"), "words": words(p.get("name")),
                            "beta": beta_stats(p, recent, runner_ath)} for p in parents],
            "ideas": ideas, "readiness": readiness(), "base_rate": round(base_rate() * 100, 2), "dev_metas": dev_metas(), "new_pairs": new_pair_narratives(),
            "bars": {"right": bars()[0], "partial": bars()[1]}}


_started = [False]


def start_worker():
    if _started[0]:
        return
    _started[0] = True

    def loop():
        time.sleep(30)
        while True:
            try:
                generate()
                resolve()
            except Exception as e:
                log("CATALYST", f"dev lab: {e}", "warn")
            time.sleep(600)
    threading.Thread(target=loop, daemon=True, name="devlab").start()
