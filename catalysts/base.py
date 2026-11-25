"""Catalyst rules. Each rule's check(coin, ctx) returns (category, weight, bullet) or None.
The highest-weight hit becomes the coin's primary catalyst."""
from __future__ import annotations

import re

CATEGORY_LABELS = {
    "celebrity_tweet": "CELEBRITY TWEET", "tweet_play": "TWEET PLAY", "community": "COMMUNITY COIN",
    "cto": "CTO", "tech": "TECH / UTILITY", "kol_call": "KOL CALL", "livestream": "LIVESTREAM",
    "news": "NEWS / EVENT", "meme": "PURE MEME / VIBES", "rewards": "HOLDER REWARDS",
}


class CatalystRule:
    category: str = "base"
    abstract = False

    def check(self, coin: dict, ctx: dict):
        raise NotImplementedError


def text_of(coin) -> str:
    # what the coin says about itself: its own description, linked post, X bio / pinned post / community (sources/socials.py)
    return " ".join(str(coin.get(k) or "") for k in ("name", "symbol", "description", "tweet_text", "x_bio", "x_pinned_text",
                                                     "x_community_desc")).lower()


def has_word(text: str, *words) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) for w in words)


def load_rules() -> list[CatalystRule]:
    from core.registry import discover
    return [cls() for cls in discover("catalysts", CatalystRule)]


def classify(coin: dict, rules: list[CatalystRule], ctx: dict) -> tuple[str | None, list[dict]]:
    hits = []
    for r in rules:
        try:
            h = r.check(coin, ctx)
        except Exception:
            h = None
        if h:
            cat, w, bullet = h
            hits.append({"category": cat, "label": CATEGORY_LABELS.get(cat, cat.upper()), "weight": w, "bullet": bullet})
    hits.sort(key=lambda h: h["weight"], reverse=True)
    return (hits[0]["category"] if hits else None), hits
