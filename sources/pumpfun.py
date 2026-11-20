"""pump.fun frontend API (unofficial). Tries every base URL x path in config until one
works, remembers the working combo, and fails gracefully."""
from __future__ import annotations

import time

from core.events import log
from sources.base import Source

HEADERS = {"Origin": "https://pump.fun", "Referer": "https://pump.fun/"}


def normalize(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    mint = raw.get("mint") or raw.get("address")
    if not mint:
        return None
    created = raw.get("created_timestamp") or raw.get("createdAt") or 0
    if isinstance(created, (int, float)) and created > 1e12:
        created = int(created / 1000)
    ath = raw.get("ath_market_cap") or raw.get("athMarketCap")
    ath_ts = raw.get("ath_market_cap_timestamp")
    return {
        "mint": mint,
        "name": raw.get("name"),
        "symbol": raw.get("symbol") or raw.get("ticker"),
        "description": raw.get("description"),
        "image": raw.get("image_uri") or raw.get("imageUri"),
        "twitter": raw.get("twitter"),
        "telegram": raw.get("telegram"),
        "website": raw.get("website"),
        "creator": raw.get("creator"),
        "created_ts": int(created) if created else None,
        "complete": bool(raw.get("complete")),
        "usd_market_cap": raw.get("usd_market_cap"),
        "pf_ath_mcap": float(ath) if ath else None,
        "pf_ath_ts": int(ath_ts / 1000) if ath_ts and ath_ts > 1e12 else ath_ts,
        "reply_count": raw.get("reply_count"),
        "livestream": bool(raw.get("is_currently_live")) or None,
        "koth_ts": raw.get("king_of_the_hill_timestamp"),
        "bonding_curve": raw.get("bonding_curve"),
        "assoc_bonding_curve": raw.get("associated_bonding_curve"),
        "pool_address": raw.get("pump_swap_pool") or raw.get("raydium_pool"),
        "nsfw": raw.get("nsfw"),
    }


class PumpFun(Source):
    name = "pumpfun"
    agent = "SCOUT"
    order = 10

    _working: tuple[str, str] | None = None

    def _list(self, base, path, offset, sort, complete):
        url = base + path.format(offset=offset, limit=self.conf.get("page_limit", 50), sort=sort,
                                 complete="true" if complete else "false")
        data = self.http_get(url, headers=HEADERS, retries=2)
        if isinstance(data, dict):
            data = data.get("coins") or data.get("data") or []
        return data if isinstance(data, list) else []

    def _combos(self):
        if PumpFun._working:
            yield PumpFun._working
        for b in self.conf.get("base_urls", []):
            for p in self.conf.get("list_paths", []):
                if (b, p) != PumpFun._working:
                    yield (b, p)

    def discover(self, since_ts: int) -> list[dict]:
        out: dict[str, dict] = {}
        for base, path in self._combos():
            try:
                for complete in (True, False):
                    for sort in self.conf.get("sorts", ["created_timestamp"]):
                        if "{sort}" not in path and sort != self.conf["sorts"][0]:
                            continue
                        for page in range(self.conf.get("max_pages", 10)):
                            rows = self._list(base, path, page * self.conf.get("page_limit", 50), sort, complete)
                            if not rows:
                                break
                            oldest = None
                            for raw in rows:
                                c = normalize(raw)
                                if c and (c["created_ts"] or 0) >= since_ts:
                                    out[c["mint"]] = c
                                if c and c["created_ts"]:
                                    oldest = min(oldest or c["created_ts"], c["created_ts"])
                            if sort == "created_timestamp" and oldest and oldest < since_ts:
                                break
                PumpFun._working = (base, path)
                log("SCOUT", f"pump.fun {base.split('//')[1]} -> {len(out)} coins")
                return list(out.values())
            except Exception as e:
                log("SCOUT", f"pump.fun endpoint failed {base}{path.split('?')[0]}: {e}", "warn")
                continue
        log("SCOUT", "pump.fun: every configured endpoint failed", "error")
        return list(out.values())

    def doctor(self):
        res = []
        for b in self.conf.get("base_urls", []):
            for p in self.conf.get("list_paths", []):
                try:
                    rows = self._list(b, p, 0, self.conf["sorts"][0], True)
                    res.append((f"list {b}{p.split('?')[0]}", bool(rows), f"{len(rows)} coins"))
                except Exception as e:
                    res.append((f"list {b}{p.split('?')[0]}", False, str(e)[:120]))
        return res


class PumpFunDetails(Source):
    """Per-coin detail call for ATH candidates: ath_market_cap, livestream, socials."""
    name = "pumpfun_details"
    agent = "SCOUT"
    stage = "ohlcv"
    order = 5

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.conf = {**self.cfg.source("pumpfun"), **self.conf}

    def fetch(self, mint):
        for b in self.conf.get("base_urls", []):
            for p in self.conf.get("detail_paths", ["/coins/{mint}"]):
                try:
                    d = self.http_get(b + p.format(mint=mint), headers=HEADERS, retries=1)
                    c = normalize(d)
                    if c:
                        return c
                except Exception:
                    continue
        return None

    def enrich(self, coins):
        out = {}
        for c in coins:
            d = self.fetch(c["mint"])
            if d:
                out[c["mint"]] = {k: v for k, v in d.items() if v is not None}
        return out

    def doctor(self):
        mint = "So11111111111111111111111111111111111111112"
        res = []
        for b in self.conf.get("base_urls", []):
            for p in self.conf.get("detail_paths", []):
                try:
                    self.http_get(b + p.format(mint=mint), headers=HEADERS, retries=1)
                    res.append((f"detail {b}{p}", True, "reachable"))
                except Exception as e:
                    ok = getattr(e, "status", None) in (404, 400)  # reachable, just not a pump coin
                    res.append((f"detail {b}{p}", ok, "reachable (404 for non-pump mint)" if ok else str(e)[:120]))
        return res
