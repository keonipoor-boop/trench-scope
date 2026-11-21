"""GeckoTerminal OHLCV -> ATH market cap (max high x supply) and time-to-100k.
Rate-limited (~30/min) and only run on ATH candidates; result cached via coins.ohlcv_ts."""
from __future__ import annotations

from sources.base import Source


def sane_ath(rows) -> float:
    """Highest price, ignoring broken wicks: a candle's high counts up to 10x its open/close,
    and candles whose close is >50x the median close are dropped."""
    closes = sorted(r[4] for r in rows if r[4])
    med = closes[len(closes) // 2] if closes else 0
    best = 0.0
    for _, o, h, _l, cl, *_ in rows:
        if med and cl > med * 50:
            continue
        best = max(best, min(h, 10 * max(o, cl)))
    return best


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
        out, fails = {}, 0
        for c in coins:
            pool = c.get("pair_address") or c.get("pool_address")
            if not pool:
                continue
            try:
                rows = sorted(self.candles(pool), key=lambda r: r[0])
                fails = 0
            except Exception as e:
                fails += getattr(e, "status", None) in (429, None)
                if fails >= 3:  # throttled: leave the rest for the next scan
                    break
                continue
            if not rows:
                continue
            ath = sane_ath(rows) * supply
            upd = {"ohlcv_ath": ath, "ath_mcap": ath}
            hit = next((r for r in rows if min(r[2], 10 * max(r[1], r[4])) * supply >= thr), None)
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
