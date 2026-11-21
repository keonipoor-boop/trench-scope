"""GeckoTerminal discovery: pages through Solana PumpSwap pools (graduated pump.fun coins) and
pump.fun bonding-curve pools. Works from servers, needs no key, and backfills migrated coins
the live feed hasn't seen. Shares GeckoTerminal's ~30 req/min budget."""
from __future__ import annotations

from datetime import datetime

from sources.base import Source


def _iso(ts: str | None):
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def pool_to_coin(p: dict, dex: str) -> dict | None:
    a = p.get("attributes") or {}
    base = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
    mint = base.split("_", 1)[-1] if base else None
    if not mint:
        return None
    sym = (a.get("name") or "").split(" / ")[0].strip() or None
    tx = (a.get("transactions") or {}).get("h24") or {}
    created = _iso(a.get("pool_created_at"))
    c = {
        "mint": mint, "symbol": sym, "pool_address": a.get("address"),
        "usd_market_cap": _f(a.get("market_cap_usd")) or _f(a.get("fdv_usd")),
        "volume_24h": _f((a.get("volume_usd") or {}).get("h24")),
        "txns_24h": (tx.get("buys") or 0) + (tx.get("sells") or 0),
        "gt_unique_traders_24h": tx.get("unique_traders"),
        "price_change_24h": _f((a.get("price_change_percentage") or {}).get("h24")),
        "seen_by": f"geckoterminal:{dex}",
    }
    if dex == "pumpswap":
        c.update(complete=True, migrated_ts=created, created_ts=created)  # launch time refined by DexScreener
    else:
        c.update(created_ts=created, bonding_curve=a.get("address"))
    return {k: v for k, v in c.items() if v is not None}


class GeckoDiscovery(Source):
    name = "gecko_discovery"
    agent = "SCOUT"
    order = 15

    def __init__(self, cfg=None):
        super().__init__(cfg)
        gt = self.cfg.source("geckoterminal")
        self.base = gt.get("base_url", "https://api.geckoterminal.com/api/v2")
        self.conf = {**self.conf, "rate_per_min": gt.get("rate_per_min", 28)}

    def _call(self, method, url, **kw):
        kw.setdefault("rate_key", "geckoterminal")  # same budget as OHLCV
        return super()._call(method, url, **kw)

    def pools(self, dex: str, page: int, sort: str | None):
        q = f"page={page}" + (f"&sort={sort}" if sort else "")
        data = self.http_get(f"{self.base}/networks/solana/dexes/{dex}/pools?{q}",
                             headers={"Accept": "application/json;version=20230302"})
        return (data or {}).get("data") or []

    def discover(self, since_ts):
        out = {}
        for dex, pages, sort in (("pumpswap", self.conf.get("pumpswap_pages", 10), "h24_volume_usd_desc"),
                                 ("pumpswap", self.conf.get("pumpswap_tx_pages", 5), "h24_tx_count_desc"),
                                 ("pump-fun", self.conf.get("pumpfun_pages", 3), None)):
            for page in range(1, pages + 1):
                try:
                    rows = self.pools(dex, page, sort)
                except Exception:
                    break
                if not rows:
                    break
                for p in rows:
                    c = pool_to_coin(p, dex)
                    if c and (c.get("created_ts") or since_ts) >= since_ts:
                        out.setdefault(c["mint"], {}).update(c)
        return list(out.values())

    def doctor(self):
        try:
            rows = self.pools("pumpswap", 1, "h24_volume_usd_desc")
            return [("dexes/pumpswap/pools", bool(rows), f"{len(rows)} pools")]
        except Exception as e:
            return [("dexes/pumpswap/pools", False, str(e)[:120])]
