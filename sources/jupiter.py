"""Jupiter token API (lite-api.jup.ag, free, no key, works from servers).

Discovery: top traded / trending / organic / recent token lists, filtered to pump.fun.
Enrichment: batch lookup of up to 100 mints per call. Gives holders, top-holder %, 24h traders,
organic volume score (anti-wash), launch + graduation times, X link, website, icon.
This lets the REAL checks run without a Helius key."""
from __future__ import annotations

from datetime import datetime

from sources.base import Source

LISTS = ["/tokens/v2/toptraded/24h?limit=100", "/tokens/v2/toptrending/24h?limit=100",
         "/tokens/v2/toporganicscore/24h?limit=100", "/tokens/v2/recent"]


def _iso(ts):
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def jup_to_coin(t: dict) -> dict | None:
    mint = t.get("id")
    if not mint:
        return None
    s24 = t.get("stats24h") or {}
    audit = t.get("audit") or {}
    vol = (s24.get("buyVolume") or 0) + (s24.get("sellVolume") or 0)
    org = (s24.get("buyOrganicVolume") or 0) + (s24.get("sellOrganicVolume") or 0)
    c = {
        "mint": mint, "name": t.get("name"), "symbol": t.get("symbol"), "image": t.get("icon"),
        "twitter": t.get("twitter"), "website": t.get("website"), "telegram": t.get("telegram"),
        "launchpad": t.get("launchpad"),
        "created_ts": _iso((t.get("firstPool") or {}).get("createdAt")) or _iso(t.get("createdAt")),
        "complete": True if t.get("graduatedPool") else None,
        "graduated_ts": _iso(t.get("graduatedAt")),
        "pool_address": t.get("graduatedPool"),
        "usd_market_cap": t.get("mcap") or t.get("fdv"),
        "holders": t.get("holderCount"),
        "top10_pct": round(audit["topHoldersPercentage"], 2) if audit.get("topHoldersPercentage") is not None else None,
        "unique_traders": s24.get("numTraders"),
        "organic_score": round(t["organicScore"], 1) if t.get("organicScore") is not None else None,
        "organic_volume_pct": round(org / vol * 100, 2) if vol else None,
        "dev_wallet": t.get("dev"), "dev_launches": audit.get("devMints"),
        "volume_24h_jup": vol or None,
    }
    return {k: v for k, v in c.items() if v is not None}


class JupiterDiscovery(Source):
    name = "jupiter"
    agent = "SCOUT"
    order = 12

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.base = self.conf.get("base_url", "https://lite-api.jup.ag")

    def discover(self, since_ts):
        out = {}
        for path in self.conf.get("lists", LISTS):
            try:
                rows = self.http_get(self.base + path)
            except Exception:
                continue
            for t in rows if isinstance(rows, list) else []:
                if t.get("launchpad") not in self.conf.get("launchpads", ["pump.fun"]):
                    continue
                c = jup_to_coin(t)
                if c and (c.get("created_ts") or since_ts) >= since_ts:
                    c["seen_by"] = "jupiter"
                    out.setdefault(c["mint"], {}).update(c)
        return list(out.values())

    def doctor(self):
        try:
            rows = self.http_get(self.base + LISTS[0])
            pf = [t for t in rows if t.get("launchpad") == "pump.fun"]
            return [("tokens/v2/toptraded/24h", True, f"{len(rows)} tokens, {len(pf)} pump.fun")]
        except Exception as e:
            return [("tokens/v2/toptraded/24h", False, str(e)[:120])]


class JupiterEnrich(Source):
    """Batch refresh of holders/top-holder %/traders/organic score for candidates, every scan."""
    name = "jupiter_tokens"
    agent = "HOLDERS"
    stage = "cheap"
    order = 25

    def __init__(self, cfg=None):
        super().__init__(cfg)
        jc = self.cfg.source("jupiter")
        self.base = jc.get("base_url", "https://lite-api.jup.ag")
        self.conf = {**jc, **self.conf}

    def enrich(self, coins):
        out, n = {}, self.conf.get("batch", 100)
        mints = ["So11111111111111111111111111111111111111112"] + [c["mint"] for c in coins]
        for i in range(0, len(mints), n):
            try:
                rows = self.http_get(f"{self.base}/tokens/v2/search?query={','.join(mints[i:i + n])}")
            except Exception:
                continue
            for t in rows if isinstance(rows, list) else []:
                if t.get("id") == "So11111111111111111111111111111111111111112":
                    if t.get("usdPrice"):
                        from core.db import get_db
                        get_db().kv_set("sol_usd", float(t["usdPrice"]))
                    continue
                c = jup_to_coin(t)
                if c:
                    out[c["mint"]] = c
        return out

    def doctor(self):
        try:
            rows = self.http_get(f"{self.base}/tokens/v2/search?query=So11111111111111111111111111111111111111112,"
                                 "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            return [("tokens/v2/search (batch)", len(rows) >= 2, f"{len(rows)} results for 2 mints")]
        except Exception as e:
            return [("tokens/v2/search", False, str(e)[:120])]
