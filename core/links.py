"""Social link parsing shared by sources and catalyst rules."""
from __future__ import annotations

import re

_X = r"(?:https?://)?(?:www\.|mobile\.)?(?:twitter|x)\.com/"


def parse_twitter(url: str | None) -> dict:
    """-> {kind: status|community|profile|other|none, handle, id}"""
    if not url:
        return {"kind": "none"}
    u = url.strip()
    m = re.match(_X + r"i/communities/(\d+)", u)
    if m:
        return {"kind": "community", "id": m.group(1)}
    m = re.match(_X + r"([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)", u)
    if m:
        return {"kind": "status", "handle": m.group(1).lower(), "id": m.group(2)}
    m = re.match(_X + r"i/(?:web/)?status/(\d+)", u)
    if m:
        return {"kind": "status", "handle": None, "id": m.group(1)}
    m = re.match(_X + r"([A-Za-z0-9_]{1,15})/?(?:\?.*)?$", u)
    if m and m.group(1).lower() not in ("i", "home", "search", "intent"):
        return {"kind": "profile", "handle": m.group(1).lower()}
    return {"kind": "other"}


def all_links(coin: dict) -> list[str]:
    links = [coin.get("twitter"), coin.get("twitter_dex"), coin.get("website"), coin.get("telegram")]
    links += coin.get("websites") or []
    return [l for l in links if l]
