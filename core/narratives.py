"""Theme tagging (data/narratives.json) + emerging-word detection."""
from __future__ import annotations

import re
from collections import Counter

from core.config import load_json_data

STOP = set("""the and for you our are was with this that just from have has not but all its it's out get got
new now who what when how why your they them their his her she him will can been more most very only into over
than then there here also coin coins token tokens sol solana pump fun memecoin meme memes official real first
one two lets let's to of in on is a an be by or at as it we us i me my no so up go do""".split())


def themes() -> dict[str, list[str]]:
    return load_json_data("narratives.json", {})


_compiled: list[tuple[str, re.Pattern]] | None = None
_mtime = None


def _patterns():
    global _compiled
    if _compiled is None:
        _compiled = []
        for theme, kws in themes().items():
            alts = "|".join(re.escape(k.lower()) for k in sorted(kws, key=len, reverse=True))
            _compiled.append((theme, re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")))
    return _compiled


def reload():
    global _compiled
    _compiled = None


def coin_text(c: dict) -> str:
    return " ".join(str(c.get(k) or "") for k in ("name", "symbol", "description")).lower()


def tag(coin: dict) -> list[str]:
    t = coin_text(coin)
    return [theme for theme, pat in _patterns() if pat.search(t)]


def tokens(c: dict, min_len=3) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]+", coin_text(c))
    return {w for w in words if len(w) >= min_len and w not in STOP}


def all_keywords() -> set[str]:
    return {k.lower() for kws in themes().values() for k in kws}


def emerging_words(now_coins: list[dict], base_coins: list[dict], min_count=2, top_n=10, min_len=3) -> list[dict]:
    """Words whose share of runner names/descriptions spiked vs the baseline window."""
    if not now_coins:
        return []
    now_df = Counter(w for c in now_coins for w in tokens(c, min_len))
    base_df = Counter(w for c in base_coins for w in tokens(c, min_len))
    kws = all_keywords()
    n_now, n_base = len(now_coins), len(base_coins)
    out = []
    for w, n in now_df.items():
        if n < min_count:
            continue
        now_frac = n / n_now
        base_frac = (base_df.get(w, 0) + 1) / (n_base + 2)
        spike = now_frac / base_frac
        if spike < 1.5:
            continue
        # attribute the word to a theme if most coins using it share one
        holders = [c for c in now_coins if w in tokens(c, min_len)]
        tc = Counter(t for c in holders for t in (c.get("themes") or []))
        theme, hits = tc.most_common(1)[0] if tc else (None, 0)
        owned = theme if hits / len(holders) >= 0.6 else None
        out.append({"word": w, "count": n, "base_count": base_df.get(w, 0), "spike": round(spike, 2),
                    "theme": owned, "new_meta": w not in kws and owned is None})
    out.sort(key=lambda x: (x["spike"] * min(x["count"], 6)), reverse=True)
    return out[:top_n]
