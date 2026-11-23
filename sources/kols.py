"""KOL tracker: pull each wallet in data/kols.json once per scan, map mint -> KOL buys."""
from __future__ import annotations

import time
from collections import defaultdict

from core.config import load_json_data
from core.events import log
from sources.base import Source
from sources.helius import WSOL, api_url

STABLES = {WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}


class KolTracker(Source):
    name = "kols"
    agent = "KOLS"
    stage = "expensive"
    order = 30
    requires_env = ["HELIUS_API_KEY"]

    _cache: tuple[float, dict] | None = None

    def kols(self) -> list[dict]:
        """One entry per wallet: main wallets plus every side wallet (tagged '(side)')."""
        out = []
        for k in load_json_data("kols.json", []) or []:
            if k.get("wallet"):
                out.append(k)
            for w in k.get("side_wallets") or []:
                if w:
                    out.append({**k, "wallet": w, "name": f"{k['name']} (side)"})
        return out

    def build_map(self) -> dict[str, list[dict]]:
        ttl = self.cfg.get_path("scheduler.scan_every_min", 10) * 60 * 0.8
        if KolTracker._cache and time.time() - KolTracker._cache[0] < ttl:
            return KolTracker._cache[1]
        mp: dict[str, list[dict]] = defaultdict(list)
        for k in self.kols():
            try:
                txs = self.http_get(api_url(f"/addresses/{k['wallet']}/transactions?type=SWAP&limit={self.conf.get('tx_limit', 100)}"))
            except Exception as e:
                log("KOLS", f"{k['name']}: {e}", "warn")
                continue
            for t in txs if isinstance(txs, list) else []:
                for tt in t.get("tokenTransfers") or []:
                    m = tt.get("mint")
                    if tt.get("toUserAccount") == k["wallet"] and m and m not in STABLES:
                        if not any(x["name"] == k["name"] for x in mp[m]):
                            mp[m].append({"name": k["name"], "twitter": k.get("twitter"), "ts": t.get("timestamp")})
        KolTracker._cache = (time.time(), dict(mp))
        log("KOLS", f"pulled {len(self.kols())} KOL wallets -> {len(mp)} mints bought")
        return KolTracker._cache[1]

    def enrich(self, coins):
        mp = self.build_map()
        return {c["mint"]: {"kols": sorted(mp.get(c["mint"], []), key=lambda x: x.get("ts") or 0)} for c in coins}

    def doctor(self):
        n = len(self.kols())
        if self.missing_env():
            return [("kols.json", n > 0, f"{n} wallets"), ("HELIUS_API_KEY", False, "missing in .env")]
        return [("kols.json", n > 0, f"{n} wallets with addresses")]
