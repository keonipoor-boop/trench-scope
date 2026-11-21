"""Coin metadata (description, X link, website, telegram, image) for ATH candidates that are
missing it. Order: the token's metadata JSON (URI from the live feed) -> GeckoTerminal token info."""
from __future__ import annotations

from sources.base import Source


class Metadata(Source):
    name = "metadata"
    agent = "CATALYST"
    stage = "ohlcv"
    order = 1

    def __init__(self, cfg=None):
        super().__init__(cfg)
        gt = self.cfg.source("geckoterminal")
        self.gt = gt.get("base_url", "https://api.geckoterminal.com/api/v2")
        self.gt_rate = gt.get("rate_per_min", 28)

    def from_uri(self, uri: str) -> dict:
        d = self.http_get(uri, per_min=120, rate_key="ipfs", retries=1)
        if not isinstance(d, dict):
            return {}
        return {"name": d.get("name"), "description": (d.get("description") or "")[:600] or None,
                "twitter": d.get("twitter"), "telegram": d.get("telegram"), "website": d.get("website"),
                "image": d.get("image")}

    def from_gt(self, mint: str) -> dict:
        d = self.http_get(f"{self.gt}/networks/solana/tokens/{mint}/info", per_min=self.gt_rate,
                          rate_key="geckoterminal", retries=1)
        a = ((d or {}).get("data") or {}).get("attributes") or {}
        tw = a.get("twitter_handle")
        tg = a.get("telegram_handle")
        return {"name": a.get("name"), "symbol": a.get("symbol"),
                "description": (a.get("description") or "")[:600] or None,
                "twitter": f"https://x.com/{tw}" if tw else None,
                "telegram": f"https://t.me/{tg}" if tg else None,
                "website": (a.get("websites") or [None])[0], "image": a.get("image_url")}

    def enrich(self, coins):
        out, gt_left = {}, self.conf.get("gt_info_per_scan", 8)
        thr = self.cfg.get_path("pipeline.runner_ath_usd", 100_000)
        for c in coins:
            if c.get("meta_done") or (c.get("description") and (c.get("twitter") or c.get("twitter_dex"))):
                continue
            meta = {}
            try:
                if c.get("metadata_uri"):
                    meta = self.from_uri(c["metadata_uri"])
                if not (meta.get("description") or meta.get("twitter")):
                    if gt_left <= 0 or (c.get("ath_mcap") or 0) < thr:
                        continue  # GeckoTerminal budget goes to runners; try again next scan
                    gt_left -= 1
                    meta = {**self.from_gt(c["mint"]), **{k: v for k, v in meta.items() if v}}
            except Exception:
                continue
            upd = {k: v for k, v in meta.items() if v and not c.get(k)}
            upd["meta_done"] = True
            out[c["mint"]] = upd
        return out

    def doctor(self):
        try:
            d = self.from_gt("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            return [("geckoterminal token info", bool(d.get("name")), d.get("name") or "empty")]
        except Exception as e:
            return [("geckoterminal token info", False, str(e)[:120])]
