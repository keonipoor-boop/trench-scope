"""PumpPortal live feed (wss://pumpportal.fun/api/data): every pump.fun launch and migration
in real time. pump.fun's own API sits behind Cloudflare and blocks servers; this does not.

One background websocket (PumpPortal asks clients to reuse a single connection) buffers
events; discover() drains the buffer. It only sees what happens while the app is running,
so GeckoTerminal discovery backfills already-migrated coins on first start."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque

from core.events import log, source_result
from sources.base import Source

_buf: deque = deque(maxlen=200_000)
_state = {"thread": None, "connected": False, "events": 0, "since": None, "last_error": None, "trades": 0, "watching": 0,
          "flushed": 0}
_lock = threading.Lock()
_ws = {"ws": None, "loop": None}
_watch: dict[str, float] = {}           # mint -> when we subscribed to its trades
BOOK: dict[str, dict] = {}              # live per-coin trade book from the trade stream (no polling)
_wake: list = []                         # callbacks run when new launches land (the sniper's wake-up)


def on_launch(cb):
    _wake.append(cb)


def _trade(ev: dict):
    """A live buy/sell on a coin we watch: update its book (price, flow, traders, early buyers)."""
    m = ev["mint"]
    b = BOOK.get(m)
    if b is None:
        b = BOOK[m] = {"buys": 0, "sells": 0, "vol_sol": 0.0, "traders": set(), "early": [], "first_ts": time.time()}
    side = ev.get("txType")
    b["buys" if side == "buy" else "sells"] += 1
    b["vol_sol"] += float(ev.get("solAmount") or 0)
    w = ev.get("traderPublicKey")
    if w:
        if w not in b["traders"] and len(b["early"]) < 25 and side == "buy":
            b["early"].append(w)
        b["traders"].add(w)
    if ev.get("marketCapSol"):
        b["mcap_sol"] = float(ev["marketCapSol"])
    if ev.get("vSolInBondingCurve"):
        b["liq_sol"] = float(ev["vSolInBondingCurve"])
    b["last_ts"] = time.time()
    _state["trades"] += 1


def watch(mints: list[str], max_watch: int = 400, ttl_s: int = 1800):
    """Subscribe to live trades for these coins (and drop ones we've watched for longer than ttl)."""
    loop, ws = _ws["loop"], _ws["ws"]
    if not loop or not ws:
        return
    now = time.time()
    new = [m for m in mints if m not in _watch][: max(0, max_watch - len(_watch))]
    old = [m for m, t in _watch.items() if now - t > ttl_s and m not in mints]
    try:
        if new:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": new})), loop)
            for m in new:
                _watch[m] = now
        if old:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": old})), loop)
            for m in old:
                _watch.pop(m, None)
                BOOK.pop(m, None)
    except Exception:
        pass
    _state["watching"] = len(_watch)


def _event_to_coin(ev: dict) -> dict | None:
    mint = ev.get("mint")
    if not mint:
        return None
    now = int(time.time())
    if ev.get("txType") in ("buy", "sell"):  # trade stream, not a launch or a migration
        _trade(ev)
        return None
    if ev.get("txType") == "create":
        return {"mint": mint, "name": ev.get("name"), "symbol": ev.get("symbol"), "created_ts": now,
                "metadata_uri": ev.get("uri"), "bonding_curve": ev.get("bondingCurveKey"),
                "creator": ev.get("traderPublicKey"), "mcap_sol_launch": ev.get("marketCapSol"),
                "seen_by": "pumpportal"}
    # anything else carrying a mint on the migration channel = graduated to PumpSwap
    return {"mint": mint, "complete": True, "migrated_ts": now,
            "pool_address": ev.get("pool") if isinstance(ev.get("pool"), str) and len(ev.get("pool")) > 30 else None}


async def _run(url: str):
    import websockets
    backoff = 2
    while True:
        try:
            async with websockets.connect(url, open_timeout=20, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                _ws.update(ws=ws, loop=asyncio.get_running_loop())
                if _watch:  # reconnect: re-subscribe the coins we were watching
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": list(_watch)}))
                _state.update(connected=True, last_error=None)
                _state["since"] = _state["since"] or time.time()
                log("SCOUT", "PumpPortal live feed connected (launches + migrations)")
                backoff = 2
                async for raw in ws:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    c = _event_to_coin(ev)
                    if c:
                        from core import perf
                        perf.mark(c["mint"], "ws")
                        if c.get("created_ts"):
                            perf.launch(c["mint"], c["created_ts"])
                        _buf.append(c)
                        _state["events"] += 1
        except Exception as e:
            _ws.update(ws=None)
            _state.update(connected=False, last_error=str(e)[:200])
            log("SCOUT", f"PumpPortal feed dropped ({e}); reconnecting in {backoff}s", "warn")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


def _flusher():
    """Every second: write new launches/migrations straight to the database and wake the sniper
    (before: they waited for the next scan, up to `scan_every_min`)."""
    from core.db import get_db
    while True:
        time.sleep(1)
        if not _buf:
            continue
        out: dict[str, dict] = {}
        while _buf:
            c = _buf.popleft()
            out.setdefault(c["mint"], {}).update({k: v for k, v in c.items() if v is not None})
        try:
            get_db().upsert_many(list(out.values()))
            _state["flushed"] += len(out)
        except Exception as e:
            _state["last_error"] = f"flush: {str(e)[:150]}"
            continue
        if any(not c.get("complete") for c in out.values()):
            for cb in list(_wake):
                try:
                    cb()
                except Exception:
                    pass


def start(url: str = "wss://pumpportal.fun/api/data"):
    with _lock:
        t = _state["thread"]
        if t and t.is_alive():
            return
        t = threading.Thread(target=lambda: asyncio.run(_run(url)), daemon=True, name="pumpportal")
        _state["thread"] = t
        t.start()
        threading.Thread(target=_flusher, daemon=True, name="pumpportal-flush").start()


def status() -> dict:
    return {**{k: v for k, v in _state.items() if k != "thread"}, "book": len(BOOK)}


class PumpPortal(Source):
    name = "pumpportal"
    agent = "SCOUT"
    order = 5

    def discover(self, since_ts):
        start(self.conf.get("url", "wss://pumpportal.fun/api/data"))
        out: dict[str, dict] = {}
        while _buf:
            c = _buf.popleft()
            out.setdefault(c["mint"], {}).update({k: v for k, v in c.items() if v is not None})
        st = status()
        source_result(self.name, self.agent, st["connected"], error=st["last_error"],
                      note=f"{st['events']} events since start")
        if out:
            mig = sum(1 for c in out.values() if c.get("complete"))
            log("SCOUT", f"PumpPortal: {len(out) - mig} launches, {mig} migrations since last scan")
        return list(out.values())

    def doctor(self):
        import asyncio as aio

        async def probe():
            import websockets
            async with websockets.connect(self.conf.get("url", "wss://pumpportal.fun/api/data"), open_timeout=15) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                n, end = 0, time.time() + 12
                while time.time() < end:
                    try:
                        m = json.loads(await aio.wait_for(ws.recv(), 4))
                        n += 1 if m.get("mint") else 0
                    except aio.TimeoutError:
                        pass
                return n
        try:
            n = aio.run(probe())
            return [("websocket launches (12s sample)", True, f"{n} new tokens")]
        except Exception as e:
            return [("websocket", False, str(e)[:120])]
