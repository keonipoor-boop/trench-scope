from catalysts.base import CatalystRule


class PureMeme(CatalystRule):
    """Fallback: always matches with the lowest weight."""
    category = "meme"

    def check(self, coin, ctx):
        bits = []
        if coin.get("x_big_account"):
            bits.append("big accounts posting")
        if (coin.get("x_mentions") or 0) > 20:
            bits.append(f"{coin['x_mentions']} X mentions")
        return self.category, 10, "Pure meme / vibes" + (f" ({', '.join(bits)})" if bits else ": no hard catalyst found")
