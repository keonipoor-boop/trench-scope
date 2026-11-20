from catalysts.base import CatalystRule, has_word, text_of
from core.links import all_links


class TechUtility(CatalystRule):
    category = "tech"

    def check(self, coin, ctx):
        links = " ".join(all_links(coin)).lower()
        if "github.com" in links:
            return self.category, 75, "Ships code: GitHub repo linked"
        t = text_of(coin)
        if has_word(t, "ai agent", "agent", "autonomous", "sdk", "api", "app live", "beta", "protocol"):
            return self.category, 50, "Pitches a product (AI agent / app / tooling)"
