"""Solana RPC with automatic failover: Helius first (when keyed), then backups (`rpc.backups` in config, default the
public mainnet RPC). Every call measures latency; an endpoint that errors or answers slower than `rpc.slow_s` is
benched for `rpc.bench_s` and the next healthy one is used. Status shows on the SNIPER DESK's SPEED card."""
from __future__ import annotations

import threading
import time

from core import http
from core.config import env, get_config

_state: dict[str, dict] = {}
_lock = threading.Lock()


def endpoints() -> list[tuple[str, str]]:
    out = []
    if env("HELIUS_API_KEY"):
        out.append(("helius", f"https://mainnet.helius-rpc.com/?api-key={env('HELIUS_API_KEY')}"))
    for i, u in enumerate(get_config().get_path("rpc.backups", ["https://api.mainnet-beta.solana.com"]) or []):
        out.append((f"backup{i + 1}" if "mainnet-beta" not in u else "public", u))
    return out


def _ok(name: str) -> bool:
    s = _state.get(name)
    return not s or time.time() >= s.get("bench_until", 0)


def call(method: str, params: list, timeout_s: float | None = None) -> dict:
    """JSON-RPC call on the fastest healthy endpoint; fails over on error / slowness. Raises if all fail."""
    cfg = get_config()
    slow = cfg.get_path("rpc.slow_s", 3.0)
    bench = cfg.get_path("rpc.bench_s", 60)
    eps = endpoints()
    # healthy first, then by measured latency
    eps.sort(key=lambda e: (not _ok(e[0]), (_state.get(e[0]) or {}).get("ewma", 0.5)))
    last = None
    for name, url in eps:
        t0 = time.perf_counter()
        try:
            r = http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, rate_key="rpc_" + name,
                          per_min=600 if name == "helius" else 90, retries=1, timeout=timeout_s or slow * 2)
            dt = time.perf_counter() - t0
            if isinstance(r, dict) and r.get("error"):
                raise RuntimeError(str(r["error"])[:120])
            with _lock:
                s = _state.setdefault(name, {"ewma": dt, "calls": 0, "errors": 0})
                s["ewma"] = 0.8 * s["ewma"] + 0.2 * dt
                s["calls"] += 1
                if dt > slow:
                    s["bench_until"] = time.time() + bench
                    s["last_error"] = f"slow {dt:.1f}s"
            return r
        except Exception as e:
            last = e
            with _lock:
                s = _state.setdefault(name, {"ewma": 1.0, "calls": 0, "errors": 0})
                s["errors"] += 1
                s["bench_until"] = time.time() + bench
                s["last_error"] = str(e)[:120]
    raise RuntimeError(f"all RPC endpoints failed: {last}")


def status() -> list[dict]:
    now = time.time()
    return [{"name": n, "ms": round((_state.get(n) or {}).get("ewma", 0) * 1000), "calls": (_state.get(n) or {}).get("calls", 0),
             "errors": (_state.get(n) or {}).get("errors", 0), "benched": not _ok(n),
             "bench_s": max(0, int((_state.get(n) or {}).get("bench_until", 0) - now)), "last_error": (_state.get(n) or {}).get("last_error")}
            for n, _ in endpoints()]
