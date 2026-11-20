from catalysts.base import CatalystRule, has_word, text_of


class Livestream(CatalystRule):
    category = "livestream"

    def check(self, coin, ctx):
        if coin.get("livestream") or has_word(text_of(coin), "livestream", "live stream", "streaming now"):
            return self.category, 55, "Dev livestream on pump.fun pulled attention"
