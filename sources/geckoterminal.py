"""GeckoTerminal OHLCV -> ATH market cap (max high x supply) and time-to-100k.
Rate-limited (~30/min) and only run on ATH candidates; result cached via coins.ohlcv_ts."""
from __future__ import annotations

from sources.base import Source


def sane_ath(rows, supply: float = 1e9) -> float:
    """Highest believable price. Ignores dust trades (tiny-volume candles), broken wicks
    (high counts up to 10x the candle's open/close), closes >50x the median, and any peak
    implying a market cap above 100x everything ever traded in the window."""
    if not rows:
        return 0.0
    total_vol = sum((r[5] if len(r) > 5 else 0) or 0 for r in rows)
    closes = sorted(r[4] for r in rows if r[4])
    med = closes[len(closes) // 2] if closes else 0
    min_vol = max(200.0, 0.002 * total_vol)
    best = 0.0
    for r in rows:
        o, h, cl = r[1], r[2], r[4]
        v = (r[5] if len(r) > 5 else 0) or 0
        if total_vol and v < min_vol:
            continue
        if med and cl > med * 50:
            continue
        best = max(best, min(h, 10 * max(o, cl)))
    if total_vol and best * supply > 100 * total_vol:
        best = min(best, 100 * total_vol / supply)
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
            ath = sane_ath(rows, supply) * supply
            upd = {"ohlcv_ath": ath, "ath_mcap": ath}
            peak = ath / supply
            hit = next((r for r in rows if min(r[2], 10 * max(r[1], r[4]), peak) * supply >= thr), None) if ath >= thr else None
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
