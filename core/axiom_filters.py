"""Axiom "New Pairs" filter presets for the SNIPER DESK.

Paste the JSON Axiom exports ({"filters": {...}, "tab": "newPairs"}); the WATCHER applies every filter it can measure
and reports the ones it can't (Axiom-only data such as snipers, insiders, bundles, fresh wallets, image reuse) instead
of pretending. Unset values (null / [] / false toggles) mean "no limit", exactly like Axiom."""
from __future__ import annotations

import time

from core.config import get_config, load_json_data
from core.db import get_db

UNIT_S = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

# Axiom protocol name -> how we recognise it (mint suffix, DexScreener dexId, Jupiter launchpad)
PROTOCOLS = {
    "Pump": {"suffix": "pump", "dex": {"pumpfun"}, "pad": {"pump.fun"}},
    "Pump AMM": {"dex": {"pumpswap"}},
    "Pump Raydium": {"suffix": "pump", "dex": {"raydium"}},
    "Bonk": {"suffix": "bonk", "pad": {"letsbonk.fun", "bonk"}},
    "LaunchLab": {"dex": {"launchlab", "raydium-launchlab"}, "pad": {"raydium launchlab", "launchlab"}},
    "Bags": {"suffix": "bags", "pad": {"bags.fun", "bags"}},
    "Believe": {"pad": {"believe", "believe.app"}},
    "Jupiter Studio": {"pad": {"jup-studio", "jupiter studio"}},
    "Moonshot": {"dex": {"moonshot"}, "pad": {"moonshot"}},
    "Boop": {"suffix": "boop", "pad": {"boop.fun", "boop"}},
    "Heaven": {"pad": {"heaven"}},
    "Dynamic BC": {"dex": {"meteoradbc"}, "pad": {"met-dbc", "meteora dbc"}},
    "Meteora DLMM": {"dex": {"meteora"}}, "Meteora AMM V2": {"dex": {"meteoradammv2"}}, "Meteora AMM V1": {"dex": {"meteoradamm"}},
    "Raydium CPMM": {"dex": {"raydium-cpmm"}}, "Raydium CLMM": {"dex": {"raydium-clmm"}}, "Raydium V4": {"dex": {"raydium"}},
    "Orca": {"dex": {"orca"}},
}

# filters we can't measure with the bot's data sources (reported, never silently "passed")
UNSUPPORTED = {"devSoldAll": "dev sold all", "excludeVamped": "vamped coins", "ogOnly": "OG only", "originalSocials": "original socials",
               "riseProDegen": "Rise Pro degen", "snipers": "snipers %", "insiders": "insiders %", "bundle": "bundle %",
               "freshWalletsHoldPercent": "fresh wallets %", "proTradersHoldPercent": "pro traders %", "devHolding": "dev holding %",
               "calloutCount": "callouts", "botUsers": "bot users", "netBuy": "net buy", "devFundingAgeMins": "dev funding age",
               "tweetAgeMins": "tweet age", "twitterRenames": "X renames", "twitterFollowers": "X followers",
               "twitterFollowing": "X following", "userCount": "Axiom users", "hiddenCount": "hidden count",
               "imgReuseCount": "image reuse", "originalAvatar": "original avatar", "xReuse": "X reuse", "hideReusedX": "hide reused X",
               "excludeMintable": "mintable", "excludeFreezable": "freezable", "excludeTransferHook": "transfer hook",
               "lpBurnt": "LP burnt", "tiktok": "TikTok", "bondingCurve": "bonding curve %", "twitter": "X mentions (Axiom)",
               "numMigrations": "dev migrations count"}


def load() -> dict:
    p = get_db().kv_get("sniper_filters")
    if p is None:
        p = load_json_data("sniper_filters.json", {}) or {}
    return p.get("filters", p) if isinstance(p, dict) else {}


def save(preset: dict) -> dict:
    f = preset.get("filters", preset) if isinstance(preset, dict) else None
    if not isinstance(f, dict):
        raise ValueError("expected Axiom's filter JSON: {\"filters\": {...}}")
    get_db().kv_set("sniper_filters", {"filters": f, "tab": preset.get("tab"), "saved": int(time.time())})
    return f


def _set(v) -> bool:
    if isinstance(v, dict):
        return any(v.get(k) is not None for k in ("min", "max"))
    return bool(v)


def summary(f: dict) -> dict:
    active, ignored = [], []
    for k, v in f.items():
        if k in UNSUPPORTED and _set(v):
            ignored.append(UNSUPPORTED[k])
    pro = [p for p, on in (f.get("protocols") or {}).items() if on]
    if pro:
        active.append("protocols: " + ", ".join(pro))
    age = f.get("age") or {}
    if age.get("max") is not None:
        active.append(f"age ≤ {age['max']} {age.get('maxUnit', 'minutes')}")
    if age.get("min") is not None:
        active.append(f"age ≥ {age['min']} {age.get('minUnit', 'minutes')}")
    for k, lbl in (("volume", "volume $"), ("marketCap", "mcap $"), ("liquidity", "liquidity $"), ("holders", "holders"),
                   ("top10Holders", "top-10 %"), ("txns", "txns"), ("numBuys", "buys"), ("numSells", "sells"), ("kolCount", "KOLs"),
                   ("fees", "fees SOL"), ("numDevPairs", "dev launches"), ("devMigrationsPercent", "dev migration %")):
        r = f.get(k) or {}
        if r.get("min") is not None:
            active.append(f"{lbl} ≥ {r['min']:,}" if isinstance(r["min"], (int, float)) else f"{lbl} ≥ {r['min']}")
        if r.get("max") is not None:
            active.append(f"{lbl} ≤ {r['max']:,}" if isinstance(r["max"], (int, float)) else f"{lbl} ≤ {r['max']}")
    for k, lbl in (("searchKeywords", "keywords"), ("excludeKeywords", "exclude keywords"), ("twitterHandles", "X handles"),
                   ("excludeTwitterHandles", "exclude X handles"), ("devWallets", "dev wallets"), ("excludeDevWallets", "exclude devs")):
        if f.get(k):
            active.append(f"{lbl}: {len(f[k])}")
    for k, lbl in (("twitterExists", "has X"), ("website", "has website"), ("telegram", "has Telegram"), ("atLeastOneSocial", "≥1 social"),
                   ("twitterAbsent", "no X"), ("twitterProfileOnly", "X profile link only"), ("onlyTwitterLinked", "only X linked"),
                   ("dexPaid", "DEX paid"), ("isStreaming", "livestreaming")):
        if f.get(k):
            active.append(lbl)
    for k, lbl in (("showHolderRewards", "holder-reward coins"), ("showBoosted", "boosted coins")):
        if f.get(k) is False:
            active.append(f"hide {lbl}")
    return {"active": active, "ignored": ignored}


def protocol_of(c: dict, dex_id: str | None) -> str | None:
    mint, dex, pad = c["mint"].lower(), (dex_id or c.get("dex_id") or "").lower(), (c.get("launchpad") or "").lower()
    for name, m in PROTOCOLS.items():  # most specific first: dex id (graduated pools), then launchpad, then mint suffix
        if dex and dex in m.get("dex", set()) and (not m.get("suffix") or mint.endswith(m["suffix"])):
            return name
    for name, m in PROTOCOLS.items():
        if pad and pad in m.get("pad", set()):
            return name
    for name, m in PROTOCOLS.items():
        if m.get("suffix") and mint.endswith(m["suffix"]) and not m.get("dex", set()) - {"pumpfun"}:
            return name
    return None


def _rng(v, r) -> bool:
    if not r:
        return True
    if r.get("min") is not None and (v is None or v < r["min"]):
        return False
    if r.get("max") is not None and v is not None and v > r["max"]:
        return False
    return True


def _handle(u: str | None) -> str | None:
    from core.links import parse_twitter
    l = parse_twitter(u)
    return (l.get("handle") or "").lower() or None


def check(c: dict, px: dict, f: dict) -> tuple[bool, str | None]:
    """-> (passes, first failed filter). px = live {mcap, liq, vol, txns, buys, sells, dex}."""
    demo = get_config().demo
    pro = f.get("protocols") or {}
    if pro and any(pro.values()):
        p = protocol_of(c, px.get("dex"))
        if p is not None and not pro.get(p, False):
            return False, f"protocol {p} is off"
    age = f.get("age") or {}
    if not demo and (age.get("max") is not None or age.get("min") is not None):  # demo coins have synthetic ages
        a = time.time() - (c.get("created_ts") or time.time())
        if age.get("max") is not None and a > age["max"] * UNIT_S.get(age.get("maxUnit", "minutes"), 60):
            return False, f"older than {age['max']} {age.get('maxUnit', 'minutes')}"
        if age.get("min") is not None and a < age["min"] * UNIT_S.get(age.get("minUnit", "minutes"), 60):
            return False, f"younger than {age['min']} {age.get('minUnit', 'minutes')}"
    checks = (("volume", px.get("vol") if px.get("vol") is not None else c.get("volume_24h")), ("marketCap", px.get("mcap")),
              ("liquidity", px.get("liq")), ("holders", c.get("holders")), ("top10Holders", c.get("top10_pct")),
              ("txns", px.get("txns") if px.get("txns") is not None else c.get("txns_24h")), ("numBuys", px.get("buys")),
              ("numSells", px.get("sells")), ("kolCount", len(c.get("kols") or [])), ("fees", c.get("fees_sol")),
              ("numDevPairs", c.get("dev_launches")),
              ("devMigrationsPercent", (c.get("dev_migrations") or 0) / c["dev_launches"] * 100 if c.get("dev_launches") else None))
    for k, v in checks:
        if not _rng(v, f.get(k)):
            return False, f"{k} {v if v is not None else 'unknown'} outside {f.get(k)}"
    text = f"{c.get('name') or ''} {c.get('symbol') or ''}".lower()
    if f.get("searchKeywords") and not any(w.lower() in text for w in f["searchKeywords"]):
        return False, "no search keyword"
    if f.get("excludeKeywords") and any(w.lower() in text for w in f["excludeKeywords"]):
        return False, "excluded keyword"
    h = _handle(c.get("twitter")) or _handle(c.get("twitter_dex"))
    if f.get("twitterHandles") and h not in {x.lower().lstrip("@") for x in f["twitterHandles"]}:
        return False, "X handle not in list"
    if f.get("excludeTwitterHandles") and h in {x.lower().lstrip("@") for x in f["excludeTwitterHandles"]}:
        return False, "excluded X handle"
    dev = c.get("creator") or c.get("dev_wallet")
    if f.get("devWallets") and dev not in set(f["devWallets"]):
        return False, "dev not in list"
    if f.get("excludeDevWallets") and dev in set(f["excludeDevWallets"]):
        return False, "excluded dev"
    tw, site, tg = c.get("twitter") or c.get("twitter_dex"), c.get("website"), c.get("telegram")
    if f.get("twitterExists") and not tw:
        return False, "no X link"
    if f.get("twitterAbsent") and tw:
        return False, "has an X link"
    if f.get("website") and not site:
        return False, "no website"
    if f.get("telegram") and not tg:
        return False, "no Telegram"
    if f.get("atLeastOneSocial") and not (tw or site or tg):
        return False, "no socials"
    from core.links import parse_twitter
    if f.get("twitterProfileOnly") and tw and parse_twitter(tw)["kind"] != "profile":
        return False, "X link isn't a profile"
    if f.get("onlyTwitterLinked") and (site or tg or not tw):
        return False, "not X-only"
    if f.get("dexPaid") and not c.get("dex_boosts"):
        return False, "DEX not paid"
    if f.get("isStreaming") and not c.get("livestream"):
        return False, "not streaming"
    if f.get("showHolderRewards") is False and c.get("primary_catalyst") == "rewards":
        return False, "holder-reward coin hidden"
    if f.get("showBoosted") is False and c.get("dex_boosts"):
        return False, "boosted coin hidden"
    return True, None


def window_s(f: dict) -> int | None:
    """The preset's max age, in seconds (the WATCHER only looks this far back)."""
    a = (f.get("age") or {})
    if a.get("max") is None:
        return None
    return int(a["max"] * UNIT_S.get(a.get("maxUnit", "minutes"), 60))
