from catalysts.base import CatalystRule
from core.links import parse_twitter


class CelebrityTweet(CatalystRule):
    category = "celebrity_tweet"

    def check(self, coin, ctx):
        l = parse_twitter(coin.get("twitter"))
        author = (coin.get("tweet_author") or l.get("handle") or "").lower()
        if l["kind"] == "status" and author in ctx["celebrities"]:
            return self.category, 95, f"Linked tweet is from @{author}, a tracked celebrity"
        big = ctx["cfg"].get_path("catalysts.big_account_followers", 100_000)
        if l["kind"] == "status" and (coin.get("tweet_author_followers") or 0) >= big:
            return self.category, 85, f"Linked tweet author @{author} has {coin['tweet_author_followers']:,} followers"
