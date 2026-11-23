"""X / Twitter API v2 (optional): mention count, top tweets, big-account flag, and
the author of a coin's linked tweet."""
from __future__ import annotations

from urllib.parse import quote

from core.config import env
from core.links import parse_twitter
from sources.base import Source


def _ts(s):
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None
    except ValueError:
        return None


class Twitter(Source):
    name = "twitter"
    agent = "CATALYST"
    stage = "expensive"
    order = 40
    requires_env = ["X_BEARER_TOKEN"]

    def _get(self, path):
        return self.http_get(self.conf.get("base_url", "https://api.x.com/2") + path,
                             headers={"Authorization": f"Bearer {env('X_BEARER_TOKEN')}"})

    def enrich(self, coins):
        big = self.cfg.get_path("catalysts.big_account_followers", 100_000)
        out = {}
        for c in coins:
            upd = {}
            q = f'"{c["mint"]}"' + (f' OR ${c["symbol"]}' if c.get("symbol") and len(c["symbol"]) <= 10 else "")
            try:
                r = self._get(f"/tweets/search/recent?query={quote(q + ' -is:retweet')}"
                              f"&max_results={self.conf.get('max_results', 50)}"
                              "&tweet.fields=public_metrics,created_at,author_id&expansions=author_id"
                              "&user.fields=public_metrics,username,name,verified")
                users = {u["id"]: u for u in (r.get("includes") or {}).get("users", [])}
                tweets = r.get("data") or []
                upd["x_mentions"] = (r.get("meta") or {}).get("result_count", len(tweets))
                def eng(t):
                    pm = t.get("public_metrics") or {}
                    fol = (users.get(t.get("author_id"), {}).get("public_metrics") or {}).get("followers_count", 0)
                    return pm.get("like_count", 0) + 2 * pm.get("retweet_count", 0) + fol / 1000
                ranked = sorted(tweets, key=eng, reverse=True)
                upd["x_top_tweets"] = [{
                    "text": t["text"][:400], "id": t["id"],
                    "author": (users.get(t.get("author_id"), {}).get("username") or "").lower() or None,
                    "name": users.get(t.get("author_id"), {}).get("name"),
                    "verified": users.get(t.get("author_id"), {}).get("verified"),
                    "followers": (users.get(t.get("author_id"), {}).get("public_metrics") or {}).get("followers_count"),
                    "likes": (t.get("public_metrics") or {}).get("like_count"),
                    "retweets": (t.get("public_metrics") or {}).get("retweet_count"),
                    "replies": (t.get("public_metrics") or {}).get("reply_count"),
                    "quotes": (t.get("public_metrics") or {}).get("quote_count"),
                    "views": (t.get("public_metrics") or {}).get("impression_count"),
                    "ts": _ts(t.get("created_at")),
                } for t in ranked[:8]]
                bigs = [u["username"] for u in users.values()
                        if (u.get("public_metrics") or {}).get("followers_count", 0) >= big]
                upd["x_big_account"] = bool(bigs)
                upd["x_big_accounts"] = bigs[:5]
            except Exception:
                pass
            link = parse_twitter(c.get("twitter"))
            if link["kind"] == "status":
                try:
                    r = self._get(f"/tweets/{link['id']}?expansions=author_id&user.fields=public_metrics,username")
                    u = ((r.get("includes") or {}).get("users") or [{}])[0]
                    upd["tweet_author"] = (u.get("username") or "").lower() or None
                    upd["tweet_author_followers"] = (u.get("public_metrics") or {}).get("followers_count")
                    upd["tweet_text"] = (r.get("data") or {}).get("text")
                except Exception:
                    pass
            if upd:
                out[c["mint"]] = upd
        return out

    def doctor(self):
        if self.missing_env():
            return [("X_BEARER_TOKEN", False, "missing in .env (optional)")]
        try:
            self._get("/tweets/search/recent?query=solana&max_results=10")
            return [("tweets/search/recent", True, "ok")]
        except Exception as e:
            return [("tweets/search/recent", False, str(e)[:120])]
