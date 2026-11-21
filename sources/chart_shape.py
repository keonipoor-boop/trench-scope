"""1-minute candles for runners -> staircase / cliff-dump chart features (core/forensics.py).
Uses GeckoTerminal's shared rate budget, so it only does a few coins per scan."""
from __future__ import annotations

import time

from core.forensics import chart_features
from sources.base import Source


class ChartShape(Source):
    name = "chart_shape"
    agent = "FEES"
    stage = "expensive"
    order = 30

    def __init__(self, cfg=None):
        super().__init__(cfg)
        gt = self.cfg.source("geckoterminal")
        self.base = gt.get("base_url", "https://api.geckoterminal.com/api/v2")
        self.conf = {**self.conf, "rate_per_min": gt.get("rate_per_min", 24)}

    def _call(self, method, url, **kw):
        kw.setdefault("rate_key", "geckoterminal")
        return super()._call(method, url, **kw)

    def candles(self, pool: str, created_ts: int | None):
        limit = self.conf.get("limit", 1000)
        q = f"aggregate={self.conf.get('aggregate', 1)}&limit={limit}&currency=usd&token=base"
        if created_ts:  # the launch + run-up, not just the latest hours
            q += f"&before_timestamp={int(min(time.time(), created_ts + limit * 60 * self.conf.get('aggregate', 1)))}"
        data = self.http_get(f"{self.base}/networks/solana/pools/{pool}/ohlcv/minute?{q}",
                             headers={"Accept": "application/json;version=20230302"})
        return (((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []

    def enrich(self, coins):
        out, budget, fails = {}, self.conf.get("coins_per_scan", 12), 0
        for c in sorted(coins, key=lambda c: (c.get("staircase_score") is not None, -(c.get("ath_mcap") or 0))):
            pool = c.get("pair_address") or c.get("pool_address")
            if budget <= 0 or not pool:
                continue
            budget -= 1
            try:
                feats = chart_features(self.candles(pool, c.get("created_ts")))
                fails = 0
            except Exception:
                fails += 1
                if fails >= 3:
                    break
                continue
            if feats:
                out[c["mint"]] = feats
        return out

    def doctor(self):
        return [("uses geckoterminal ohlcv/minute", True, "see GECKOTERMINAL checks")]
