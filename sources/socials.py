"""What the coin says about itself on its socials: the X account's bio + pinned post, the X community's description,
and the website's text. That's where rewards / revenue share / buybacks / utility are usually pitched, and the
catalyst rules (catalysts/rewards.py) need it to see why someone would buy.

X: the X API when X_BEARER_TOKEN is set, else the free public fxtwitter API (profile bio + pinned post, tweet text).
Communities need the X API. Runners only, refreshed every `refresh_h`."""
from __future__ import annotations

import html
import re
import time
from urllib.parse import quote

from core.config import env
from core.links import parse_twitter
from sources.base import Source


def _safe(u: str | None) -> bool:
    from api.images import safe_url
    return bool(u) and safe_url(u)


def page_text(raw: str, limit: int = 4000) -> str:
    """Title + meta descriptions + visible text of an HTML page."""
    if not isinstance(raw, str):
        return ""
    bits = []
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    if m:
        bits.append(m.group(1))
    for m in re.finditer(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|twitter:description)["\'][^>]*>', raw, re.I):
        c = re.search(r'content=["\']([^"\']*)', m.group(0), re.I)
        if c:
            bits.append(c.group(1))
    body = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    bits.append(body)
    text = html.unescape(" ".join(bits))
    return re.sub(r"\s+", " ", text).strip()[:limit]


class Socials(Source):
    name = "socials"
    agent = "CATALYST"
    stage = "expensive"
    order = 35

    def _x(self, path):
        return self.http_get("https://api.x.com/2" + path, headers={"Authorization": f"Bearer {env('X_BEARER_TOKEN')}"})

    def x_profile(self, handle: str) -> dict:
        if env("X_BEARER_TOKEN"):
            try:
                r = self._x(f"/users/by/username/{quote(handle)}?user.fields=description,pinned_tweet_id,url"
                            "&expansions=pinned_tweet_id&tweet.fields=text")
                u, inc = r.get("data") or {}, (r.get("includes") or {}).get("tweets") or []
                return {"x_bio": u.get("description"), "x_pinned_text": inc[0].get("text") if inc else None}
            except Exception:
                pass
        try:  # free public mirror, no key
            r = self.http_get(f"https://api.fxtwitter.com/{quote(handle)}")
            u = (r or {}).get("user") or {}
            out = {"x_bio": u.get("description")}
            pin = u.get("pinned_tweet") or {}
            if isinstance(pin, dict) and pin.get("text"):
                out["x_pinned_text"] = pin["text"]
            return out
        except Exception:
            return {}

    def x_community(self, cid: str) -> dict:
        if not env("X_BEARER_TOKEN"):
            return {}
        try:
            r = self._x(f"/communities/{cid}?community.fields=description,name")
            d = r.get("data") or {}
            return {"x_community_desc": " ".join(x for x in (d.get("name"), d.get("description")) if x) or None}
        except Exception:
            return {}

    def x_status(self, tid: str) -> dict:
        try:
            r = self.http_get(f"https://api.fxtwitter.com/status/{tid}")
            t = (r or {}).get("tweet") or {}
            return {"tweet_text": t.get("text")} if t.get("text") else {}
        except Exception:
            return {}

    def enrich(self, coins):
        out, now = {}, time.time()
        every = self.conf.get("refresh_h", 6) * 3600
        budget = self.conf.get("coins_per_scan", 25)
        for c in sorted(coins, key=lambda c: (bool(c.get("socials_ts")), -(c.get("ath_mcap") or 0))):
            if budget <= 0:
                break
            if now - (c.get("socials_ts") or 0) < every:
                continue
            budget -= 1
            upd = {"socials_ts": int(now)}
            for field in ("twitter", "twitter_dex"):
                l = parse_twitter(c.get(field))
                if l["kind"] == "profile" and not upd.get("x_bio"):
                    upd.update({k: v for k, v in self.x_profile(l["handle"]).items() if v})
                elif l["kind"] == "community" and not upd.get("x_community_desc"):
                    upd.update({k: v for k, v in self.x_community(l["id"]).items() if v})
                elif l["kind"] == "status" and not c.get("tweet_text") and not upd.get("tweet_text"):
                    upd.update(self.x_status(l["id"]))
            site = c.get("website")
            if _safe(site):
                try:
                    raw = self.http_get(site, retries=1, headers={"User-Agent": "Mozilla/5.0 (compatible; TrenchScope)"})
                    t = page_text(raw if isinstance(raw, str) else "")
                    if t:
                        upd["site_text"] = t
                except Exception:
                    pass
            out[c["mint"]] = upd
        return out

    def doctor(self):
        try:
            r = self.http_get("https://api.fxtwitter.com/x")
            return [("fxtwitter profile lookup (free)", bool((r or {}).get("user")), "ok" if (r or {}).get("user") else str(r)[:80])]
        except Exception as e:
            return [("fxtwitter profile lookup (free)", False, str(e)[:120])]
