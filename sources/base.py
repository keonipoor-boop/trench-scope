"""Base class for data sources.

A source may implement either or both of:
  discover(since_ts) -> list[coin dict]      new coins (must include "mint")
  enrich(coins)      -> dict[mint, dict]     partial field updates per mint

`stage` decides when enrich runs:
  "cheap"     every scan, on all coins in the lookback window (batched APIs)
  "ohlcv"     only on ATH candidates (rate-limited history)
  "expensive" only on runners (ATH >= threshold), at most every deep_refresh_min
"""
from __future__ import annotations

import time

from core.config import env, get_config
from core.events import source_result
from core import http


class Source:
    name: str = "base"
    agent: str = "SCOUT"          # which agent in the AGENT STRIP owns this source
    stage: str = "cheap"          # cheap | ohlcv | expensive
    order: int = 50               # lower runs first within a stage
    requires_env: list[str] = []  # keys that must be present in .env
    optional_env: list[str] = []
    is_demo: bool = False
    abstract: bool = False

    def __init__(self, cfg=None):
        self.cfg = cfg or get_config()
        self.conf = self.cfg.source(self.name)

    # ---- capability checks -------------------------------------------------
    def missing_env(self) -> list[str]:
        return [k for k in self.requires_env if not env(k)]

    def enabled(self) -> bool:
        if self.cfg.demo:
            return self.is_demo
        if self.is_demo:
            return False
        return self.conf.get("enabled", True) and not self.missing_env()

    def can_discover(self) -> bool:
        return type(self).discover is not Source.discover

    def can_enrich(self) -> bool:
        return type(self).enrich is not Source.enrich

    # ---- plugin API --------------------------------------------------------
    def discover(self, since_ts: int) -> list[dict]:
        return []

    def enrich(self, coins: list[dict]) -> dict[str, dict]:
        return {}

    def doctor(self) -> list[tuple[str, bool, str]]:
        """Return [(check, ok, detail)]. Default: try discover/enrich on nothing."""
        return [("no doctor checks defined", True, "")]

    # ---- helpers -----------------------------------------------------------
    def http_get(self, url, **kw):
        return self._call("GET", url, **kw)

    def http_post(self, url, **kw):
        return self._call("POST", url, **kw)

    def _call(self, method, url, **kw):
        kw.setdefault("per_min", self.conf.get("rate_per_min", 0))
        kw.setdefault("rate_key", self.name)
        t0 = time.monotonic()
        try:
            out = http.request(method, url, **kw)
        except Exception as e:
            source_result(self.name, self.agent, False, error=str(e))
            raise
        source_result(self.name, self.agent, True, latency_ms=(time.monotonic() - t0) * 1000)
        return out


def load_sources(cfg=None, only_enabled=True) -> list[Source]:
    from core.registry import discover
    objs = [cls(cfg) for cls in discover("sources", Source)]
    if only_enabled:
        objs = [s for s in objs if s.enabled()]
    return sorted(objs, key=lambda s: (s.order, s.name))
