"""Live diagnostic: run one real scan and report whether given tickers were found and why/why not.
Usage: python scripts/live_check.py PENNY SWAN ..."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import load_config  # noqa: E402

cfg = load_config()
from core import pipeline  # noqa: E402
from core.db import get_db  # noqa: E402

stats = pipeline.scan(backfill_days=float(os.getenv("DAYS", "2")))
db = get_db()
print("\n=== SCAN", stats)
print("\n=== SOURCES")
for r in db.q("SELECT name, ok, calls, errors, last_error FROM source_status"):
    print(f"  {r['name']:<18} ok={r['ok']} calls={r['calls']} errors={r['errors']} {r['last_error'] or ''}")
n = db.q("SELECT COUNT(*) n, MIN(created_ts) a, MAX(created_ts) b FROM coins")[0]
if n["n"]:
    print(f"\n=== {n['n']} coins discovered, created {(time.time() - n['a']) / 3600:.1f}h .. {(time.time() - n['b']) / 60:.0f}m ago")
print("\n=== TOP RUNNERS")
for c in db.coins("is_runner=1", order="ath_mcap DESC", limit=25):
    print(f"  ${c.get('symbol'):<10} ATH {c['ath_mcap']:>12,.0f}  MC {c.get('usd_market_cap') or 0:>12,.0f}  "
          f"age {(time.time() - c['created_ts']) / 3600:5.1f}h  real={c.get('is_real')}  {c.get('primary_catalyst')}")
for sym in sys.argv[1:]:
    rows = db.coins("UPPER(symbol)=?", (sym.upper(),))
    print(f"\n=== LOOKUP ${sym}: {len(rows)} match(es)")
    for c in rows:
        keys = ["mint", "name", "created_ts", "complete", "usd_market_cap", "pf_ath_mcap", "ohlcv_ath", "ath_mcap",
                "is_runner", "pair_address", "dex_id", "ts_100k"]
        print("  " + ", ".join(f"{k}={c.get(k)}" for k in keys))
