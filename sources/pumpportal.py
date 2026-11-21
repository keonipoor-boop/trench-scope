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
_state = {"thread": None, "connected": False, "events": 0, "since": None, "last_error": None}
_lock = threading.Lock()


def _event_to_coin(ev: dict) -> dict | None:
    mint = ev.get("mint")
    if not mint:
        return None
    now = int(time.time())
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
                        _buf.append(c)
                        _state["events"] += 1
        except Exception as e:
            _state.update(connected=False, last_error=str(e)[:200])
            log("SCOUT", f"PumpPortal feed dropped ({e}); reconnecting in {backoff}s", "warn")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


def start(url: str = "wss://pumpportal.fun/api/data"):
    with _lock:
        t = _state["thread"]
        if t and t.is_alive():
            return
        t = threading.Thread(target=lambda: asyncio.run(_run(url)), daemon=True, name="pumpportal")
        _state["thread"] = t
        t.start()


def status() -> dict:
    return {k: v for k, v in _state.items() if k != "thread"}


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
