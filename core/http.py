"""Shared HTTP client: per-host rate limiting, retries with backoff, graceful failure."""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse

import httpx

UA = "TrenchScope/1.0 (+research bot)"


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._next: dict[str, float] = defaultdict(float)

    def wait(self, key: str, per_min: float):
        if not per_min:
            return
        gap = 60.0 / per_min
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next[key])
            self._next[key] = t + gap
        delay = t - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_limiter = RateLimiter()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        verify = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE") or True
        _client = httpx.Client(timeout=20, headers={"User-Agent": UA, "Accept": "application/json"},
                               follow_redirects=True, verify=verify)
    return _client


class HTTPError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def request(method: str, url: str, *, rate_key: str | None = None, per_min: float = 0,
            retries: int = 3, **kw):
    """Returns parsed JSON (or text). Raises HTTPError after retries."""
    key = rate_key or urlparse(url).netloc
    last = None
    for attempt in range(retries):
        _limiter.wait(key, per_min)
        try:
            r = client().request(method, url, **kw)
        except httpx.ProxyError as e:  # policy denial: retrying won't help
            raise HTTPError(f"ProxyError: {e}", 403)
        except httpx.HTTPError as e:
            last = HTTPError(f"{type(e).__name__}: {e}")
            time.sleep(min(2 ** attempt, 8))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = HTTPError(f"HTTP {r.status_code}", r.status_code)
            ra = r.headers.get("retry-after")
            time.sleep(min(float(ra), 30) if ra and ra.replace(".", "").isdigit() else min(2 ** (attempt + 1), 8))
            continue
        if r.status_code >= 400:
            raise HTTPError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or r.text[:1] in "[{":
            try:
                return r.json()
            except ValueError:
                pass
        return r.text
    raise last or HTTPError("request failed")


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
