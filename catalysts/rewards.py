"""HOLDER REWARDS: the coin pays you to hold it (rewards / revenue share / dividends / staking), or the team buys back
and burns. Read from what the coin says about itself: description, X bio, pinned post, X community, website
(sources/socials.py). The coin's NAME alone never counts ($BASKET isn't a basket fund just by being called that)."""
from __future__ import annotations

import re

from catalysts.base import CatalystRule

WHERE = [("description", "description"), ("x_bio", "X bio"), ("x_pinned_text", "pinned X post"),
         ("x_community_desc", "X community"), ("tweet_text", "linked post"), ("site_text", "website")]

# (label, weight, patterns)
MECHANICS = [
    ("holder rewards", 74, [r"rewards? (?:to |for )?holders", r"holders? (?:earn|get|receive)", r"hold(?:ing)? (?:to|and) earn",
                            r"earn (?:sol|usdc|rewards|passive)", r"rewards? (?:in|paid in) (?:sol|usdc|\$?\w+)",
                            r"auto[- ]?rewards?", r"reflections?", r"cash ?back", r"rewards? every \d+", r"\brewards?\b"]),
    ("revenue share", 74, [r"rev(?:enue)?[- ]share", r"profit[- ]shar", r"dividends?", r"(?:creator |trading )?fees? (?:go|are sent|sent|distributed|split|paid) (?:back )?to holders",
                           r"\d{1,3}% of (?:the )?(?:creator |trading )?fees", r"passive income", r"\byield\b", r"\bapy\b"]),
    ("staking", 70, [r"\bstak(?:e|ing)\b"]),
    ("airdrops to holders", 70, [r"airdrops? (?:to|for) (?:all )?holders", r"holders? (?:get|receive) (?:an )?airdrops?"]),
    ("index / basket of tokens", 70, [r"basket of (?:[\w$]+ ){0,4}(?:tokens|coins|memes|memecoins|assets)", r"index (?:fund|token|of)", r"one token (?:to )?(?:hold|own) (?:them )?all"]),
    # "LP burned" is on every migrated pump.fun coin: only supply burns / buybacks count
    ("buyback + burn", 62, [r"buy ?backs?", r"burn(?:s|ed|ing)? (?:\d+% of )?(?:the )?(?:supply|tokens|\$\w+)",
                            r"(?:supply|tokens) (?:get |are )?burn(?:ed|t)"]),
]


def _snippet(text: str, m: re.Match, width: int = 70) -> str:
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")


class HolderRewards(CatalystRule):
    category = "rewards"

    def check(self, coin, ctx):
        found = []  # (weight, label, where, snippet)
        for field, where in WHERE:
            text = str(coin.get(field) or "")
            if not text:
                continue
            low = text.lower()
            for label, w, pats in MECHANICS:
                if any(f[1] == label for f in found):
                    continue
                for p in pats:
                    m = re.search(p, low)
                    if m:
                        found.append((w, label, where, _snippet(text, m)))
                        break
        if not found:
            return None
        found.sort(key=lambda f: -f[0])
        w, label, where, snip = found[0]
        others = [f[1] for f in found[1:3]]
        weight = min(85, w + 4 * len(found[1:3]))  # several mechanics = a stronger pitch
        bullet = f"Pays holders: {label}" + (f" + {', '.join(others)}" if others else "") + f" (from its {where}: “{snip}”)"
        return self.category, weight, bullet
