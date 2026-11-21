"""Probe candidate discovery endpoints from a real network (run in GitHub Actions)."""
import asyncio
import json
import sys

import httpx

GT = "https://api.geckoterminal.com/api/v2"
URLS = [
    f"{GT}/networks/solana/dexes?page=1",
    f"{GT}/networks/solana/dexes?page=2",
    f"{GT}/networks/solana/dexes/pumpswap/pools?page=1&sort=h24_volume_usd_desc",
    f"{GT}/networks/solana/dexes/pumpswap/pools?page=1&sort=pool_created_at_desc",
    f"{GT}/networks/solana/dexes/pump-fun/pools?page=1",
    f"{GT}/networks/solana/new_pools?page=1",
    f"{GT}/networks/solana/tokens/So11111111111111111111111111111111111111112/info",
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
    "https://api.dexscreener.com/latest/dex/search?q=pumpswap",
    "https://frontend-api-v3.pump.fun/coins?offset=0&limit=5&sort=market_cap&order=DESC&includeNsfw=true&complete=true",
]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
     "Accept": "application/json", "Origin": "https://pump.fun", "Referer": "https://pump.fun/"}

for u in URLS:
    try:
        r = httpx.get(u, headers=H, timeout=20)
        body = r.text
        print(f"\n### {r.status_code} {u}\n{body[:1500]}")
    except Exception as e:
        print(f"\n### ERR {u} {e}")


async def ws():
    import websockets
    try:
        async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as w:
            await w.send(json.dumps({"method": "subscribeNewToken"}))
            await w.send(json.dumps({"method": "subscribeMigration"}))
            n = 0
            end = asyncio.get_event_loop().time() + 25
            while asyncio.get_event_loop().time() < end:
                try:
                    m = await asyncio.wait_for(w.recv(), 5)
                except asyncio.TimeoutError:
                    continue
                n += 1
                if n <= 4:
                    print("WS MSG", m[:700])
            print(f"### PUMPPORTAL WS: {n} messages in 25s")
    except Exception as e:
        print("### PUMPPORTAL WS ERR", e)

asyncio.run(ws())
