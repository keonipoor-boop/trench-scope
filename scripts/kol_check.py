"""Live test of the KOL research agent on real wallets. Usage: python scripts/kol_check.py WALLET [SIDE...]"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import load_config  # noqa: E402

load_config()
from core import kol_research as K  # noqa: E402

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
for r in p["recent"][:8]:
    print("  ", r["symbol"], r["entry_mcap"], r["invested_sol"], r["multiple"], r["hold_s"])
