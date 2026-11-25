"""SPEED: where the time goes. In-memory, no I/O on the hot path.

Per-coin path (first time each stage happens for a mint):
  launch   the coin's on-chain launch (created_ts: earliest any source reports)
  ws       the live feed delivered it (PumpPortal websocket)
  db       it landed in our database
  scored   the sniper's ANALYST scored it
  gated    RISK made its first call (pass or hold)
  filled   EXECUTOR filled a paper buy
Each consecutive pair (and launch -> gated / launch -> filled) is a latency series with p50 / p95.
Timers: scan stages, sniper tick parts, API routes. `/api/perf` serves it; the SNIPER DESK shows it."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager

PATH = ["launch", "ws", "db", "scored", "gated", "filled"]
PAIRS = [("launch", "ws"), ("ws", "db"), ("db", "scored"), ("scored", "gated"), ("gated", "filled"),
         ("launch", "gated"), ("launch", "filled")]
_lock = threading.Lock()
_marks: OrderedDict = OrderedDict()
_lat: dict = {f"{a}→{b}": deque(maxlen=600) for a, b in PAIRS}
_timers: dict = {}
_counts: dict = {}


def mark(mint: str, stage: str, ts: float | None = None):
    """First time `stage` happens for this coin. Records latencies to the stages that came before."""
    if not mint:
        return
    ts = ts or time.time()
    with _lock:
        m = _marks.get(mint)
        if m is None:
            m = _marks[mint] = {}
            if len(_marks) > 30000:
                _marks.popitem(last=False)
        if stage in m:
            return
        m[stage] = ts
        for a, b in PAIRS:
            if b == stage and a in m and ts >= m[a]:
                _lat[f"{a}→{b}"].append(ts - m[a])
        _counts[stage] = _counts.get(stage, 0) + 1


def launch(mint: str, created_ts: float | None):
    if created_ts:
        with _lock:
            m = _marks.setdefault(mint, {})
            if "launch" not in m or created_ts < m["launch"]:
                m["launch"] = created_ts


def record(name: str, seconds: float):
    with _lock:
        d = _timers.get(name)
        if d is None:
            d = _timers[name] = deque(maxlen=300)
        d.append(seconds)


@contextmanager
def timer(name: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t0)


def _stats(xs) -> dict | None:
    xs = sorted(xs)
    if not xs:
        return None
    q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]  # noqa: E731
    return {"n": len(xs), "p50": round(q(0.5), 4), "p95": round(q(0.95), 4), "max": round(xs[-1], 4)}


def snapshot() -> dict:
    with _lock:
        path = [{"stage": k, **(_stats(v) or {"n": 0})} for k, v in _lat.items()]
        timers = {k: _stats(v) for k, v in _timers.items()}
        counts = dict(_counts)
    groups = {}
    for k, v in timers.items():
        if v:
            groups.setdefault(k.split(":")[0], []).append({"name": k, **v})
    for g in groups.values():
        g.sort(key=lambda x: -x["p50"])
    try:
        from sources import pumpportal
        feed = pumpportal.status()
    except Exception:
        feed = {}
    return {"path": path, "timers": groups, "counts": counts, "feed": feed, "ts": int(time.time())}


def reset():
    with _lock:
        _marks.clear()
        for d in _lat.values():
            d.clear()
        _timers.clear()
        _counts.clear()
