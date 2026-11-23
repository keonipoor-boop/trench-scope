"""Image proxy: coin pictures, KOL profile pictures and data-source logos.

Fetched once by the server, cached in the data dir, served to the dashboard. Async, so slow image hosts
never tie up the threads that answer clicks. Misses are remembered for an hour.
Only public https URLs are fetched: coin metadata (image links) is attacker-controlled."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from core.config import DATA_DIR

router = APIRouter()
_MINT = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,48}$")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_sem = asyncio.Semaphore(16)
_client: httpx.AsyncClient | None = None
HDR = {"Cache-Control": "max-age=86400"}

SOURCE_DOMAINS = {
    "pumpfun": "pump.fun", "pumpfun_details": "pump.fun", "pumpportal": "pumpportal.fun", "pump.fun": "pump.fun",
    "jupiter": "jup.ag", "jupiter_tokens": "jup.ag", "geckoterminal": "geckoterminal.com",
    "gecko_discovery": "geckoterminal.com", "chart_shape": "geckoterminal.com", "dexscreener": "dexscreener.com",
    "dex_feeds": "dexscreener.com", "helius_txs": "helius.dev", "helius_holders": "helius.dev", "helius": "helius.dev",
    "helius das": "helius.dev", "kols": "helius.dev", "kol wallets": "helius.dev", "kol_research": "helius.dev",
    "solana_rpc": "solana.com", "metadata": "ipfs.tech", "twitter": "x.com", "x api": "x.com",
}


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        verify = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE") or True
        _client = httpx.AsyncClient(timeout=6, follow_redirects=True, verify=verify,
                                    headers={"User-Agent": "Mozilla/5.0 TrenchScope image cache"})
    return _client


def safe_url(u) -> bool:
    try:
        x = urlparse(u or "")
    except ValueError:
        return False
    host = (x.hostname or "").lower()
    return (x.scheme == "https" and "." in host and not re.match(r"^[\d.]+$", host) and ":" not in host
            and host != "localhost" and not host.endswith((".local", ".internal")))


def _ipfs(u: str) -> str:
    return "https://ipfs.io/ipfs/" + u[7:] if u.startswith("ipfs://") else u


async def _fetch_image(u: str) -> tuple[bytes, str] | None:
    u = _ipfs(u)
    if not safe_url(u):
        return None
    try:
        async with _sem:
            r = await _http().get(u)
        ctype = r.headers.get("content-type", "").split(";")[0]
        if r.status_code == 200 and ctype.startswith("image/") and 100 < len(r.content) < 3_000_000:
            return r.content, ctype
    except Exception:
        pass
    return None


async def _json(u: str):
    if not safe_url(_ipfs(u)):
        return None
    try:
        async with _sem:
            r = await _http().get(_ipfs(u))
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def _cached(key: str, candidates) -> Response:
    """candidates: async generator of URLs, tried in order until one is an image."""
    d = DATA_DIR / "img"
    d.mkdir(exist_ok=True)
    f, meta, miss = d / f"{key}.img", d / f"{key}.type", d / f"{key}.miss"
    if f.exists() and f.stat().st_size > 100:
        return Response(f.read_bytes(), media_type=meta.read_text() if meta.exists() else "image/png", headers=HDR)
    if miss.exists() and time.time() - miss.stat().st_mtime < 3600:
        raise HTTPException(404, "no image (retrying hourly)")
    seen = set()
    async for u in candidates:
        if not u or u in seen:
            continue
        seen.add(u)
        got = await _fetch_image(u)
        if got:
            f.write_bytes(got[0])
            meta.write_text(got[1])
            return Response(got[0], media_type=got[1], headers=HDR)
    miss.touch()
    raise HTTPException(404, "no image found")


@router.get("/api/img/{mint}")
async def coin_img(mint: str):
    if not _MINT.match(mint):
        raise HTTPException(400, "bad mint")

    async def candidates():
        from core.db import get_db
        c = await asyncio.to_thread(get_db().get_coin, mint) or {}
        if c.get("image"):
            yield c["image"]
        try:
            from core import kol_research
            for k in kol_research.list_kols():
                for h in ((kol_research.get_profile(k["name"]) or {}).get("holdings") or []):
                    if h.get("mint") == mint and h.get("image"):
                        yield h["image"]
        except Exception:
            pass
        if c.get("metadata_uri"):  # the coin's own pump.fun metadata JSON has its image
            j = await _json(c["metadata_uri"])
            if isinstance(j, dict):
                yield j.get("image")
        j = await _json(f"https://lite-api.jup.ag/tokens/v2/search?query={mint}")
        for t in j if isinstance(j, list) else []:
            if t.get("id") == mint:
                yield t.get("icon")
        yield f"https://dd.dexscreener.com/ds-data/tokens/solana/{mint}.png?size=lg"
        yield f"https://images.pump.fun/coin-image/{mint}?variant=86x86"

    return await _cached(mint, candidates())


@router.get("/api/pfp/{handle}")
async def kol_pfp(handle: str):
    if not _HANDLE.match(handle):
        raise HTTPException(400, "bad handle")

    async def candidates():
        from core import kol_research
        for k in kol_research.list_kols():
            if (kol_research.handle_of(k) or "").lower() == handle.lower():
                px = ((kol_research.get_profile(k["name"]) or {}).get("x") or {}).get("pfp") or ""
                if px.startswith("https://pbs.twimg.com/"):
                    yield px
        yield f"https://unavatar.io/x/{handle}?fallback=false"
        yield f"https://unavatar.io/twitter/{handle}?fallback=false"

    return await _cached("x_" + handle.lower(), candidates())


@router.get("/api/srcicon/{name}")
async def source_icon(name: str):
    """Logo for a SENSORY INPUT card (the source's site favicon)."""
    key = re.sub(r"\s*\(demo\)$", "", name.lower()).strip()
    domain = SOURCE_DOMAINS.get(key)
    if not domain:
        raise HTTPException(404, "unknown source")

    async def candidates():
        yield f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
        yield f"https://icons.duckduckgo.com/ip3/{domain}.ico"
        yield f"https://{domain}/favicon.ico"
        if domain == "pumpportal.fun":  # no findable favicon: it's pump.fun's feed, use their logo
            yield "https://www.google.com/s2/favicons?domain=pump.fun&sz=64"

    return await _cached("src_" + domain.replace(".", "_"), candidates())
