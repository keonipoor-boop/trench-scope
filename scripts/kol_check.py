"""Live test of the KOL research agent on real wallets. Usage: python scripts/kol_check.py WALLET [SIDE...]"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import load_config  # noqa: E402

load_config()
from core import kol_research as K  # noqa: E402

# profile picture through the same server endpoint the dashboard uses
from api.images import kol_pfp  # noqa: E402
import asyncio  # noqa: E402
from api.images import source_icon  # noqa: E402


async def _pics():
    for handle in ("blknoiz06", "cented7"):
        try:
            r = await kol_pfp(handle)
            print("PFP", handle, r.media_type, len(r.body), "bytes")
        except Exception as e:
            print("PFP", handle, "FAILED", e)
    for src in ("pumpportal", "jupiter", "dexscreener", "geckoterminal", "helius_txs", "solana_rpc", "metadata", "pumpfun"):
        try:
            r = await source_icon(src)
            print("ICON", src, r.media_type, len(r.body), "bytes")
        except Exception as e:
            print("ICON", src, "FAILED", e)
asyncio.run(_pics())
K.save_kol({"name": "LiveTest", "twitter": None, "wallet": sys.argv[1], "side_wallets": sys.argv[2:]})
p = K.research("LiveTest")
if not p:
    print("research failed")
    sys.exit(1)
print("STYLE", p["style"], "RISK", p["risk"], "SOURCE", p["data_source"], "SOL$", p["sol_usd"])
print(p["description"])
print(json.dumps({k: p["stats"].get(k) for k in ("trades", "positions", "closed", "win_rate", "median_size_sol",
                                                  "median_entry_mcap", "median_entry_age_s", "median_hold_s",
                                                  "entry_mcap_mix", "themes", "total_pnl_sol")}, default=str))
print("HOLDINGS", [(h["symbol"], bool(h.get("image")), h["invested_sol"]) for h in p.get("holdings", [])])
from api.images import coin_img  # noqa: E402
import asyncio  # noqa: E402
MINTS = ["8fWsV1u1SYcN358tSA6dyTjfpmaQTziftcHwKBFSpump", "FJKt1LxbWBpTRt9BaDEB6Wr2qL5LZzjFF5sx8rXnpump"] + \
    [h["mint"] for h in p.get("holdings", [])[:3]] + sys.argv[2:3]


async def _imgs():
    for mint in MINTS:
        try:
            r = await coin_img(mint)
            print("IMG", mint[:8], r.media_type, len(r.body), "bytes")
        except Exception as e:
            print("IMG", mint[:8], "FAILED", e)
asyncio.run(_imgs())
for r in p["recent"][:8]:
    print("  ", r["symbol"], r["entry_mcap"], r["invested_sol"], r["multiple"], r["hold_s"])
