"""DEV SYSTEM: studies the devs (creators) behind runners. Populates itself: every runner's creator wallet
(from the live feed, Jupiter or pump.fun) is grouped; Jupiter adds each dev's total launches and how many
graduated. Profiles: how often their coins run, how big, how many get flagged fake, what they launch."""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict

from core.db import get_db


def dev_of(c: dict) -> str | None:
    return c.get("dev_wallet") or c.get("creator")


def _short(w: str) -> str:
    return f"{w[:4]}…{w[-4:]}"


def labels(d: dict) -> list[str]:
    out = []
    if d["runners"] >= 3 or (d["runner_rate"] or 0) >= 5:
        out.append("Hit maker")
    if d["fake"] >= 2 and d["fake"] / max(1, d["runners"]) >= 0.5:
        out.append("Fake factory")
    elif d["real"] and d["real"] / max(1, d["runners"]) >= 0.7:
        out.append("Clean runners")
    if (d["launches"] or 0) >= 100:
        out.append("Serial launcher")
    elif (d["launches"] or 0) <= 3:
        out.append("New dev")
    if d["runners"] == 1 and (d["launches"] or 0) >= 10:
        out.append("One-hit")
    if (d["migration_rate"] or 0) >= 20:
        out.append("Migrator")
    return out or ["Dev"]


def _aggregate(dev: str, coins: list[dict]) -> dict:
    runners = [c for c in coins if c.get("is_runner")]
    real = sum(1 for c in runners if c.get("is_real") is True)
    fake = sum(1 for c in runners if c.get("is_real") is False)
    launches = max([c.get("dev_launches") or 0 for c in coins] + [len(coins)])
    migr = max([c.get("dev_migrations") or 0 for c in coins])
    best = max(runners, key=lambda c: c.get("ath_mcap") or 0) if runners else None
    d = {"wallet": dev, "name": _short(dev), "coins_seen": len(coins), "runners": len(runners), "real": real, "fake": fake,
         "launches": launches, "migrations": migr or None,
         "runner_rate": round(len(runners) / launches * 100, 1) if launches else None,
         "migration_rate": round(migr / launches * 100, 1) if launches and migr else None,
         "best": {"mint": best["mint"], "symbol": best.get("symbol"), "ath": best.get("ath_mcap"), "image": best.get("image")} if best else None,
         "avg_ath": round(statistics.mean([c.get("ath_mcap") or 0 for c in runners])) if runners else None,
         "themes": [t for t, _ in Counter(t for c in runners for t in (c.get("themes") or [])).most_common(3)],
         "catalysts": [k for k, _ in Counter(c.get("primary_catalyst") for c in runners if c.get("primary_catalyst")).most_common(3)],
         "last_launch_ts": max([c.get("created_ts") or 0 for c in coins]) or None}
    d["labels"] = labels(d)
    return d


DEV_SQL = "COALESCE(json_extract(data,'$.dev_wallet'), json_extract(data,'$.creator'))"


def _coins_by_dev(min_runners: int = 1, only: list[str] | None = None) -> dict[str, list[dict]]:
    """Only devs behind runners are loaded (fast even with 100k+ launches in the db)."""
    db = get_db()
    devs = only or [r["d"] for r in db.q(f"SELECT DISTINCT {DEV_SQL} d FROM coins WHERE is_runner=1 AND {DEV_SQL} IS NOT NULL")][:600]
    by = defaultdict(list)
    for i in range(0, len(devs), 200):
        chunk = devs[i:i + 200]
        for c in db.coins(f"{DEV_SQL} IN ({','.join('?' * len(chunk))})", tuple(chunk), order="created_ts DESC"):
            by[dev_of(c)].append(c)
    return {w: cs for w, cs in by.items() if sum(1 for c in cs if c.get("is_runner")) >= min_runners}


_cache: tuple[float, list] | None = None


def devs_list(limit: int = 24) -> list[dict]:
    """Devs behind runners, best first (most runners, then biggest peak). Cached for a minute."""
    global _cache
    if _cache and time.time() - _cache[0] < 60:
        return _cache[1][:limit]
    out = []
    for w, cs in _coins_by_dev().items():
        d = _aggregate(w, cs)
        runners = sorted([c for c in cs if c.get("is_runner")], key=lambda c: -(c.get("ath_mcap") or 0))
        d["moons"] = [{"mint": c["mint"], "symbol": c.get("symbol"), "name": c.get("name"), "image": c.get("image"),
                       "first_buy_ts": c.get("created_ts"), "ath": c.get("ath_mcap"), "is_real": c.get("is_real")}
                      for c in runners[:8]]
        out.append(d)
    out.sort(key=lambda d: (-d["runners"], -((d["best"] or {}).get("ath") or 0)))
    pinned = []
    for t in tracked():
        d, _ = tracked_profile(t)
        pinned.append(d)
    mine = {w for t in tracked() for w in t["wallets"]}
    out = pinned + [d for d in out if d["wallet"] not in mine]
    _cache = (time.time(), out)
    return out[:max(limit, len(pinned))]


def dev_detail(wallet: str) -> dict | None:
    """wallet = a dev wallet, or the name of a tracked dev."""
    t = next((x for x in tracked() if x["name"] == wallet or wallet in x["wallets"]), None)
    if t:
        d, cs = tracked_profile(t)
    else:
        cs = _coins_by_dev(0, [wallet]).get(wallet, [])
        if not cs:
            return None
        d = _aggregate(wallet, cs)
    runners = sorted([c for c in cs if c.get("is_runner")], key=lambda c: -(c.get("ath_mcap") or 0))
    slim = lambda c: {k: c.get(k) for k in ("mint", "symbol", "name", "image", "ath_mcap", "usd_market_cap", "created_ts",  # noqa: E731
                                            "is_real", "primary_catalyst", "holders", "time_to_100k_min")} | \
        {"fake_headline": (c.get("fake") or {}).get("headline")}
    d["runner_coins"] = [slim(c) for c in runners[:30]]
    d["other_coins"] = [slim(c) for c in cs if not c.get("is_runner")][:20]
    rr = d["runner_rate"]
    if d.get("needs_wallet"):
        d["description"] = (f"{d['name']} is on your watch list but has no dev wallet yet. Click EDIT and paste their dev "
                            "wallet(s): every coin they launch is then tracked, priced and checked like any runner.")
        d["runner_coins"], d["other_coins"] = [], []
        return d
    bits = [f"{d['name']} has launched {d['launches']} coin{'s' if d['launches'] != 1 else ''}"
            + (f", {d['migrations']} graduated ({d['migration_rate']}%)" if d.get("migrations") else "")
            + f"; {d['runners']} of the ones we've seen crossed $100k" + (f" ({rr}% of all their launches)." if rr is not None else ".")]
    if d["best"]:
        bits.append(f"Best: ${d['best']['symbol']} peaking at ${(d['best']['ath'] or 0) / 1e3:,.0f}k.")
    if d["runners"]:
        bits.append(f"{d['real']} passed the REAL checks, {d['fake']} were flagged fake.")
    if d["themes"]:
        bits.append("Usually launches " + ", ".join(d["themes"]) + " coins.")
    d["description"] = " ".join(bits)
    return d


# ---------------------------------------------------------------- tracked devs (you add them: devs.json)
# {name, twitter, wallets: [...]}. Tracked devs are pinned on the DEV SYSTEM even before they have a runner; their
# launches come from the live feed (PumpPortal reports every launch's creator) plus an hourly Helius scan of
# each wallet's history for pump.fun creates.
import re as _re  # noqa: E402

from core.config import load_json_data, save_json_data  # noqa: E402
from core.events import log  # noqa: E402

WALLET_RE = _re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def tracked() -> list[dict]:
    out = []
    for d in load_json_data("devs.json", []) or []:
        if d.get("name"):
            out.append({**d, "wallets": [w for w in (d.get("wallets") or []) if w and WALLET_RE.match(w)]})
    return out


def save_tracked(d: dict):
    devs = [x for x in (load_json_data("devs.json", []) or []) if x.get("name") and x.get("name") != d["name"]
            and x.get("name") != d.get("old_name")]
    devs.append({k: d.get(k) for k in ("name", "twitter", "wallets", "note")})
    save_json_data("devs.json", devs)
    global _cache
    _cache = None


def delete_tracked(name: str):
    save_json_data("devs.json", [x for x in (load_json_data("devs.json", []) or []) if x.get("name") != name])
    global _cache
    _cache = None


def handle_of(d: dict) -> str | None:
    tw = (d.get("twitter") or "").strip().rstrip("/")
    return (tw.split("/")[-1].lstrip("@") or None) if tw else None


def _launches(wallets: list[str]) -> list[dict]:
    out = []
    for w in wallets:
        out += get_db().kv_get(f"devlaunches:{w}", []) or []
    return out


def tracked_profile(t: dict) -> dict:
    """A tracked dev: every wallet pooled, pinned even with no runners yet."""
    ws = t["wallets"]
    cs = []
    if ws:
        for w, coins in _coins_by_dev(0, ws).items():
            cs += coins
    known = {c["mint"] for c in cs}
    extra = [l for l in _launches(ws) if l["mint"] not in known]
    d = _aggregate(ws[0] if ws else t["name"], cs) if cs else {
        "wallet": ws[0] if ws else None, "coins_seen": 0, "runners": 0, "real": 0, "fake": 0, "launches": 0,
        "migrations": None, "runner_rate": None, "migration_rate": None, "best": None, "avg_ath": None, "themes": [],
        "catalysts": [], "last_launch_ts": None, "labels": []}
    d["launches"] = max(d["launches"] or 0, len(cs) + len(extra))
    if extra:
        d["last_launch_ts"] = max([d.get("last_launch_ts") or 0] + [l.get("ts") or 0 for l in extra]) or None
    d["runner_rate"] = round(d["runners"] / d["launches"] * 100, 1) if d["launches"] else None
    d["labels"] = labels(d) if cs else (["Needs wallet"] if not ws else ["Tracking"])
    h = handle_of(t)
    d.update(name=t["name"], key=t["name"], tracked=True, twitter=t.get("twitter"), handle=h, wallets=ws,
             pfp=f"/api/pfp/{h}" if h else None, needs_wallet=not ws, note=t.get("note"),
             scanned_ts=max([(get_db().kv_get(f"devscan:{w}") or {}).get("ts") or 0 for w in ws] or [0]) or None)
    runners = sorted([c for c in cs if c.get("is_runner")], key=lambda c: -(c.get("ath_mcap") or 0))
    recent = sorted(cs, key=lambda c: -(c.get("created_ts") or 0))
    d["moons"] = [{"mint": c["mint"], "symbol": c.get("symbol"), "name": c.get("name"), "image": c.get("image"),
                   "first_buy_ts": c.get("created_ts"), "ath": c.get("ath_mcap"), "is_real": c.get("is_real")}
                  for c in (runners or recent)[:8]]
    return d, cs


def scan_dev(wallet: str, first_pages: int = 10) -> int:
    """Find pump.fun coins this wallet created (Helius enhanced history) and add them to the coin db so the scan
    prices them. Hourly; the first run reads further back. Returns new launches found."""
    from core.config import env
    if not env("HELIUS_API_KEY"):
        return 0
    from core.kol_research import _helius_page
    db = get_db()
    st = db.kv_get(f"devscan:{wallet}") or {}
    seen = {l["mint"] for l in db.kv_get(f"devlaunches:{wallet}", []) or []}
    found, before, newest = [], None, None
    for i in range(first_pages if not st.get("newest") else 5):
        try:
            txs, nxt, done = _helius_page(wallet, before)
        except Exception as e:
            log("SCOUT", f"dev scan {wallet[:4]}…: {e}", "warn")
            break
        if i == 0 and txs:
            newest = txs[0].get("signature")
        stop = False
        for t in txs:
            if t.get("signature") == st.get("newest"):
                stop = True
                break
            if t.get("feePayer") != wallet or (t.get("type") or "") not in ("CREATE", "TOKEN_MINT", "CREATE_POOL"):
                continue
            for tt in t.get("tokenTransfers") or []:
                m = tt.get("mint")
                if m and m not in seen and not tt.get("fromUserAccount") and m not in {x["mint"] for x in found}:
                    found.append({"mint": m, "ts": t.get("timestamp")})
        if stop or done or not nxt:
            break
        before = nxt
    if found:
        db.kv_set(f"devlaunches:{wallet}", (db.kv_get(f"devlaunches:{wallet}", []) or []) + found)
        db.upsert_many([{"mint": l["mint"], "creator": wallet, "created_ts": l.get("ts")} for l in found])
    db.kv_set(f"devscan:{wallet}", {"ts": int(time.time()), "newest": newest or st.get("newest")})
    return len(found)


def scan_due(max_wallets: int = 1) -> int:
    """Called by the background worker: scan the tracked dev wallet that's been waiting longest (hourly)."""
    due = []
    for t in tracked():
        for w in t["wallets"]:
            ts = (get_db().kv_get(f"devscan:{w}") or {}).get("ts") or 0
            if time.time() - ts > 3600:
                due.append((ts, w, t["name"]))
    n = 0
    for _, w, name in sorted(due)[:max_wallets]:
        k = scan_dev(w)
        n += k
        if k:
            log("SCOUT", f"tracked dev {name}: found {k} launch{'es' if k != 1 else ''} from {w[:4]}…{w[-4:]}")
    return n
