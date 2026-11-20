from catalysts.base import CatalystRule, has_word, text_of

WORDS = ("breaking", "news", "announced", "headline", "today", "election", "verdict", "launch event",
         "lawsuit", "arrested", "dies", "wins", "tariff")


class NewsEvent(CatalystRule):
    category = "news"

    def check(self, coin, ctx):
        if has_word(text_of(coin), *WORDS):
            return self.category, 58, "Tied to a news cycle / real-world event"
