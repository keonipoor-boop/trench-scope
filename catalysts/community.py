from catalysts.base import CatalystRule
from core.links import parse_twitter


class Community(CatalystRule):
    category = "community"

    def check(self, coin, ctx):
        if parse_twitter(coin.get("twitter")).get("kind") == "community" or \
                parse_twitter(coin.get("twitter_dex")).get("kind") == "community":
            return self.category, 45, "Launched with an X Community: organized holder base from day one"
