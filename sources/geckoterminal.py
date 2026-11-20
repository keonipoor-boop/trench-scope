"""GeckoTerminal OHLCV -> ATH market cap (max high x supply) and time-to-100k.
Rate-limited (~30/min) and only run on ATH candidates; result cached via coins.ohlcv_ts."""
from __future__ import annotations

from sources.base import Source


class GeckoTerminal(Source):
    name = "geckoterminal"
    agent = "SCOUT"
    stage = "ohlcv"
    order = 20

    def candles(self, pool: str) -> list[list[float]]:
        c = self.conf
        url = (f"{c.get('base_url')}/networks/solana/pools/{pool}/ohlcv/{c.get('timeframe', 'hour')}"
               f"?aggregate={c.get('aggregate', 1)}&limit={c.get('limit', 200)}&currency=usd&token=base")
        data = self.http_get(url, headers={"Accept": "application/json;version=20230302"})
        return (((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []

    def enrich(self, coins):
        supply = self.cfg.get_path("pipeline.total_supply", 1_000_000_000)
        thr = self.cfg.get_path("pipeline.runner_ath_usd", 100_000)
        out = {}
        for c in coins:
            pool = c.get("pair_address") or c.get("pool_address")
            if not pool:
                continue
            try:
                rows = sorted(self.candles(pool), key=lambda r: r[0])
            except Exception:
                continue
            if not rows:
                continue
            ath = max(r[2] for r in rows) * supply
            upd = {"ohlcv_ath": ath, "ath_mcap": ath}
            hit = next((r for r in rows if r[2] * supply >= thr), None)
            if hit:
                upd["ts_100k"] = int(hit[0])
            out[c["mint"]] = upd
        return out

    def doctor(self):
        try:
            data = self.http_get(f"{self.conf.get('base_url')}/networks/solana/trending_pools?page=1")
            pools = (data or {}).get("data") or []
            if not pools:
                return [("trending_pools", False, "empty")]
            pool = pools[0]["attributes"]["address"]
            rows = self.candles(pool)
            return [("trending_pools", True, f"{len(pools)} pools"), ("ohlcv/hour", bool(rows), f"{len(rows)} candles")]
        except Exception as e:
            return [("geckoterminal", False, str(e)[:120])]
