"""𝕏 POSTS: the posts that pushed a coin, for the coin's 𝕏 tab.

Where they come from:
  - the tweet the coin links to (its "twitter" field is a status): X API when X_BEARER_TOKEN is set,
    else X's free public embed endpoint (publish.twitter.com/oembed, no key; text + author + date)
  - tweet links inside the coin's description / DexScreener socials
  - X search results (`x_top_tweets`, needs X_BEARER_TOKEN): the most-liked posts mentioning the CA or $TICKER

Each post gets a timing against the run (before / during / after it crossed $100k) and a BOOST flag when it
plausibly moved the price: posted before or during the run AND (it's the coin's own linked tweet, a celebrity /
KOL / big account wrote it, or it got real engagement).
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from core import http
from core.config import env, get_config
from core.db import get_db
from core.links import parse_twitter

_STATUS = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15}|i(?:/web)?)/status(?:es)?/(\d{5,25})")
OEMBED_TTL, OEMBED_MISS_TTL = 7 * 86400, 6 * 3600


def _celebs() -> set:
    from core.config import load_json_data
    return {h.lower() for h in load_json_data("celebrities.json", [])}


def _kol_handles() -> dict:
    try:
        from core import kol_research
        out = {}
        for k in kol_research.list_kols():
            h = kol_research.handle_of(k)
            if h:
                out[h.lower()] = k["name"]
        return out
    except Exception:
        return {}


def _oembed(handle: str | None, tid: str) -> dict | None:
    """Free tweet lookup (no key). Cached in kv; misses retried after 6h."""
    db = get_db()
    key = f"oembed:{tid}"
    hit = db.kv_get(key)
    if hit and time.time() - hit.get("ts", 0) < (OEMBED_TTL if hit.get("post") else OEMBED_MISS_TTL):
        return hit.get("post")
    post = None
    try:
        url = f"https://x.com/{handle or 'i'}/status/{tid}"
        r = http.get(f"https://publish.twitter.com/oembed?omit_script=1&dnt=true&url={quote(url, safe='')}",
                     rate_key="x_oembed", per_min=30, retries=1)
        if isinstance(r, dict) and r.get("html"):
            m = re.search(r"<p[^>]*>(.*?)</p>", r["html"], re.S)
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", re.sub(r"<a [^>]*>(.*?)</a>", r"\1", m.group(1)))) if m else ""
            text = re.sub(r"<[^>]+>", "", text).strip()
            dm = re.findall(r">([A-Z][a-z]+ \d{1,2}, \d{4})</a>", r["html"])
            ts = None
            if dm:
                try:
                    ts = int(datetime.strptime(dm[-1], "%B %d, %Y").replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    pass
            author_url = r.get("author_url") or ""
            post = {"id": tid, "text": text[:600], "name": r.get("author_name"),
                    "author": (author_url.rstrip("/").split("/")[-1] or handle or "").lower() or None,
                    "ts": ts, "ts_day_only": True}
    except Exception:
        post = None
    db.kv_set(key, {"ts": int(time.time()), "post": post})
    return post


def _x_api_tweet(tid: str) -> dict | None:
    if not env("X_BEARER_TOKEN"):
        return None
    base = get_config().get_path("sources.twitter.base_url", "https://api.x.com/2")
    try:
        r = http.get(f"{base}/tweets/{tid}?tweet.fields=public_metrics,created_at&expansions=author_id"
                     "&user.fields=public_metrics,username,name,verified",
                     headers={"Authorization": f"Bearer {env('X_BEARER_TOKEN')}"}, rate_key="twitter", per_min=30, retries=1)
        t, u = r.get("data") or {}, ((r.get("includes") or {}).get("users") or [{}])[0]
        if not t:
            return None
        pm, um = t.get("public_metrics") or {}, u.get("public_metrics") or {}
        return {"id": tid, "text": t.get("text"), "author": (u.get("username") or "").lower() or None, "name": u.get("name"),
                "followers": um.get("followers_count"), "verified": u.get("verified"),
                "likes": pm.get("like_count"), "retweets": pm.get("retweet_count"), "replies": pm.get("reply_count"),
                "quotes": pm.get("quote_count"), "views": pm.get("impression_count"), "ts": _iso(t.get("created_at"))}
    except Exception:
        return None


def _iso(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _linked_ids(coin: dict) -> list[tuple[str | None, str, str]]:
    """(handle, tweet id, why) for every tweet the coin itself links to."""
    out, seen = [], set()
    for field, why in (("twitter", "linked"), ("twitter_dex", "linked")):
        l = parse_twitter(coin.get(field))
        if l["kind"] == "status" and l["id"] not in seen:
            seen.add(l["id"]); out.append((l.get("handle"), l["id"], why))
    for m in _STATUS.finditer(coin.get("description") or ""):
        h, tid = m.group(1), m.group(2)
        if tid not in seen:
            seen.add(tid); out.append((None if h == "i" or h.startswith("i/") else h.lower(), tid, "description"))
    return out


def _timing(ts: int | None, coin: dict, day_only: bool) -> tuple[str, str]:
    """-> (phase, label) relative to the run. phase: before | during | after | unknown."""
    if not ts:
        return "unknown", ""
    launch, hit = coin.get("created_ts"), coin.get("ts_100k")
    if day_only:  # oEmbed gives the day only
        if launch and ts + 86400 < launch:
            return "before", "posted before launch"
        return "unknown", "posted " + datetime.fromtimestamp(ts, timezone.utc).strftime("%b %d")
    if launch and ts < launch:
        return "before", f"{_dur(launch - ts)} before launch"
    if hit and ts <= hit:
        return "during", f"{_dur(hit - ts)} before it crossed $100k"
    if hit:
        return "after", f"{_dur(ts - hit)} after it crossed $100k"
    if launch:
        return "during", f"{_dur(ts - launch)} after launch"
    return "unknown", ""


def _dur(s):
    s = max(0, int(s))
    return f"{s // 60}m" if s < 3600 else f"{s // 3600}h" if s < 86400 else f"{s // 86400}d"


def posts_for(coin: dict) -> dict:
    celebs, kols = _celebs(), _kol_handles()
    big = get_config().get_path("catalysts.big_account_followers", 100_000)
    keyed = bool(env("X_BEARER_TOKEN"))
    posts: dict[str, dict] = {}

    for handle, tid, why in _linked_ids(coin):
        p = None
        if coin.get("demo"):
            p = {"id": tid, "author": handle, "text": coin.get("tweet_text") or f"${coin.get('symbol')}", "ts": coin.get("tweet_ts"),
                 "followers": coin.get("tweet_author_followers"), "likes": coin.get("tweet_likes"), "retweets": coin.get("tweet_retweets")}
        else:
            p = _x_api_tweet(tid) or _oembed(handle, tid)
        if not p:  # still show the link, even if X didn't answer
            p = {"id": tid, "author": handle, "text": None}
        p["source"] = why
        posts[tid] = p

    for t in coin.get("x_top_tweets") or []:
        tid = str(t.get("id") or "")
        key = tid or f"q{len(posts)}"
        if tid in posts:
            posts[tid] = {**t, **{k: v for k, v in posts[tid].items() if v is not None}}
            continue
        posts[key] = {**t, "source": "search"}

    out = []
    for p in posts.values():
        a = (p.get("author") or "").lower() or None
        p["author"] = a
        phase, when = _timing(p.get("ts"), coin, bool(p.get("ts_day_only")))
        who = ("celebrity" if a in celebs else "kol" if a in kols else
               "big account" if (p.get("followers") or 0) >= big else None)
        likes, rts, fol = p.get("likes") or 0, p.get("retweets") or 0, p.get("followers") or 0
        impact = likes + 2 * rts + 3 * (p.get("quotes") or 0) + fol / 1000 + (500 if who == "celebrity" else 150 if who else 0) \
            + (300 if p["source"] == "linked" else 0)
        engaged = likes >= 100 or rts >= 25 or fol >= 10_000
        boost = phase in ("before", "during", "unknown") and (p["source"] == "linked" or who is not None or engaged)
        why = []
        if p["source"] == "linked":
            why.append("the coin is built on this post")
        if who == "celebrity":
            why.append(f"@{a} is a celebrity")
        elif who == "kol":
            why.append(f"posted by KOL {kols[a]}")
        elif who == "big account":
            why.append(f"{fol:,} followers")
        if likes >= 100 or rts >= 25:
            why.append(f"{likes:,} likes · {rts:,} reposts")
        if phase == "after":
            why.append("came after the run (followed it, didn't cause it)")
        out.append({**p, "phase": phase, "when": when, "who": who, "kol": kols.get(a) if a else None,
                    "boost": boost, "impact": round(impact, 1), "why": why,
                    "url": f"https://x.com/{a or 'i'}/status/{p['id']}" if p.get("id") else None})
    out.sort(key=lambda p: (not p["boost"], -p["impact"]))
    link = parse_twitter(coin.get("twitter"))
    account = None
    if link["kind"] in ("profile", "community"):
        account = {"kind": link["kind"], "url": coin.get("twitter"), "handle": link.get("handle")}
    return {"posts": out, "boosts": sum(1 for p in out if p["boost"]), "mentions": coin.get("x_mentions"),
            "searched": keyed or bool(coin.get("demo")), "account": account,
            "big_accounts": coin.get("x_big_accounts") or []}
