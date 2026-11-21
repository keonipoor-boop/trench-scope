"""Print raw per-trade fee accounting for a few coins (diagnostic). Usage: python scripts/fee_debug.py SYMBOL..."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx  # noqa: E402

from sources.helius import LAMPORTS  # noqa: E402
from sources.solana_rpc import parse_tx  # noqa: E402

RPC = "https://api.mainnet-beta.solana.com"
for sym in sys.argv[1:]:
    toks = httpx.get(f"https://lite-api.jup.ag/tokens/v2/search?query={sym}", timeout=20).json()
    toks = [t for t in toks if t.get("launchpad") == "pump.fun" and t.get("graduatedPool")]
    if not toks:
        print("no graduated pump token for", sym)
        continue
    t = max(toks, key=lambda t: t.get("holderCount") or 0)
    mint, pool = t["id"], t["graduatedPool"]
    print(f"\n##### {sym} {mint} pool={pool} holders={t.get('holderCount')} mcap={t.get('mcap')}")
    sigs = httpx.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                 "params": [pool, {"limit": 25}]}, timeout=30).json().get("result") or []
    batch = [{"jsonrpc": "2.0", "id": i, "method": "getTransaction",
              "params": [s["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "json"}]} for i, s in enumerate(sigs)]
    res = httpx.post(RPC, json=batch, timeout=60).json()
    for r in res if isinstance(res, list) else []:
        tx = r.get("result")
        if not tx:
            print("  (no result)", str(r.get("error"))[:100])
            continue
        meta, msg = tx["meta"], tx["transaction"]["message"]
        keys = msg["accountKeys"] + (meta.get("loadedAddresses") or {}).get("writable", []) + (meta.get("loadedAddresses") or {}).get("readonly", [])
        deltas = sorted(((post - pre) / LAMPORTS, keys[i][:6], pre == 0) for i, (pre, post) in enumerate(zip(meta["preBalances"], meta["postBalances"])) if post != pre)
        p = parse_tx(tx, mint, pool)
        print(f"  parsed={ {k: (round(v, 5) if isinstance(v, float) else v) for k, v in (p or {}).items() if k != 'ts'} }")
        print(f"     prio+base={meta['fee'] / LAMPORTS:.6f} lamport deltas={[(round(d, 5), k, 'NEW' if n else '') for d, k, n in deltas][:8]}")
