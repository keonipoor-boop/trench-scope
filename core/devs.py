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
    _cache = (time.time(), out)
    return out[:limit]


def dev_detail(wallet: str) -> dict | None:
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
