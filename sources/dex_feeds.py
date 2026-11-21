"""DexScreener token feeds: latest profiles, latest + top boosts. These are coins whose teams
paid for a profile or boosts, which includes most runners, and they come with socials and
descriptions."""
from __future__ import annotations

from sources.base import Source

FEEDS = ["/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"]


def profile_to_coin(p: dict) -> dict | None:
    if p.get("chainId") != "solana" or not p.get("tokenAddress"):
        return None
    c = {"mint": p["tokenAddress"], "dex_url": p.get("url"), "seen_by": "dexscreener:feeds"}
    if p.get("description"):
        c["description"] = p["description"][:600]
    for l in p.get("links") or []:
        url, t, label = l.get("url"), (l.get("type") or "").lower(), (l.get("label") or "").lower()
        if not url:
            continue
        if t == "twitter":
            c.setdefault("twitter_dex", url)
        elif t == "telegram":
            c.setdefault("telegram", url)
        elif label == "website" or not t:
            c.setdefault("website", url)
    if p.get("cto"):
        c["dex_cto"] = True
    if p.get("totalAmount"):
        c["dex_boosts"] = p["totalAmount"]
    return c


class DexFeeds(Source):
    name = "dex_feeds"
    agent = "SCOUT"
    order = 18

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.base = self.cfg.source("dexscreener").get("base_url", "https://api.dexscreener.com")

    def discover(self, since_ts):
        out = {}
        for f in FEEDS:
            try:
                rows = self.http_get(self.base + f)
            except Exception:
                continue
            for p in rows if isinstance(rows, list) else []:
                c = profile_to_coin(p)
                if c:
                    out.setdefault(c["mint"], {}).update(c)
        return list(out.values())  # created_ts unknown until DexScreener pairs fill it in

    def doctor(self):
        try:
            rows = self.http_get(self.base + FEEDS[0])
            sol = [r for r in rows if r.get("chainId") == "solana"]
            return [("token-profiles/latest", True, f"{len(rows)} profiles, {len(sol)} on solana")]
        except Exception as e:
            return [("token-profiles/latest", False, str(e)[:120])]
