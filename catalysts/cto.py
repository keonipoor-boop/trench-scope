from catalysts.base import CatalystRule, has_word, text_of


class CommunityTakeover(CatalystRule):
    category = "cto"

    def check(self, coin, ctx):
        t = text_of(coin) + " " + (coin.get("twitter") or "").lower()
        if has_word(t, "cto", "community takeover", "takeover") or "_cto" in t:
            return self.category, 70, "Community takeover: dev left, holders revived it"
