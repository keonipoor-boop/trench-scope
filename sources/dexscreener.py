"""DexScreener: market cap, volume, txns, pair address, socials. 30 mints per call."""
from __future__ import annotations

from sources.base import Source


def _best_pairs(pairs: list[dict]) -> dict[str, dict]:
    """Most liquid pair per mint, annotated with the earliest pair time (= launch for pump coins)
    and whether any pair is a graduated pool."""
    best: dict[str, dict] = {}
    first: dict[str, int] = {}
    grad: dict[str, bool] = {}
    for p in pairs or []:
        mint = (p.get("baseToken") or {}).get("address")
        if not mint:
            continue
        if p.get("pairCreatedAt"):
            first[mint] = min(first.get(mint, 10**15), int(p["pairCreatedAt"]))
        if p.get("dexId") and p["dexId"] != "pumpfun":
            grad[mint] = True
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if mint not in best or liq > ((best[mint].get("liquidity") or {}).get("usd") or 0):
            best[mint] = p
    for m, p in best.items():
        p["_first_ts"] = first.get(m)
        p["_graduated"] = grad.get(m, False)
    return best


def pair_to_fields(p: dict) -> dict:
    info = p.get("info") or {}
    socials = {s.get("type"): s.get("url") for s in info.get("socials") or [] if s.get("type")}
    websites = [w.get("url") for w in info.get("websites") or [] if w.get("url")]
    tx = (p.get("txns") or {}).get("h24") or {}
    out = {
        "pair_address": p.get("pairAddress"),
        "dex_id": p.get("dexId"),
        "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
        "usd_market_cap": p.get("marketCap") or p.get("fdv"),
        "volume_24h": (p.get("volume") or {}).get("h24"),
        "txns_24h": (tx.get("buys") or 0) + (tx.get("sells") or 0),
        "buys_24h": tx.get("buys"), "sells_24h": tx.get("sells"),
        "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
        "price_change_24h": (p.get("priceChange") or {}).get("h24"),
        "dex_url": p.get("url"),
        "image": info.get("imageUrl"),
        "symbol": (p.get("baseToken") or {}).get("symbol"),
        "name": (p.get("baseToken") or {}).get("name"),
        "created_ts": int(p["_first_ts"] / 1000) if p.get("_first_ts") else None,
        "complete": True if p.get("_graduated") else None,
    }
    if socials.get("twitter"):
        out["twitter_dex"] = socials["twitter"]
    if socials.get("telegram"):
        out["telegram"] = socials["telegram"]
    if websites:
        out["websites"] = websites
    return {k: v for k, v in out.items() if v is not None}


class DexScreener(Source):
    name = "dexscreener"
    agent = "SCOUT"
    stage = "cheap"
    order = 20

    def fetch(self, mints: list[str]) -> dict[str, dict]:
        url = f"{self.conf.get('base_url', 'https://api.dexscreener.com')}/tokens/v1/solana/{','.join(mints)}"
        data = self.http_get(url)
        pairs = data if isinstance(data, list) else (data or {}).get("pairs") or []
        return {m: pair_to_fields(p) for m, p in _best_pairs(pairs).items()}

    def enrich(self, coins):
        out, n = {}, self.conf.get("batch", 30)
        mints = [c["mint"] for c in coins]
        for i in range(0, len(mints), n):
            try:
                out.update(self.fetch(mints[i:i + n]))
            except Exception:
                continue
        return out

    def doctor(self):
        try:
            r = self.fetch(["So11111111111111111111111111111111111111112"])
            return [("tokens/v1/solana", True, f"{len(r)} pair(s) for wSOL")]
        except Exception as e:
            return [("tokens/v1/solana", False, str(e)[:120])]
