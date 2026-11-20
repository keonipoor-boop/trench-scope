from catalysts.base import CatalystRule
from core.links import parse_twitter


class TweetPlay(CatalystRule):
    category = "tweet_play"

    def check(self, coin, ctx):
        l = parse_twitter(coin.get("twitter"))
        if l["kind"] == "status":
            who = coin.get("tweet_author") or l.get("handle") or "someone"
            return self.category, 60, f"Coin is a play on a specific tweet by @{who}"
