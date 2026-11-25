"""FAST GATE: the deal-breakers, checked in microseconds from memory BEFORE the sniper even prices a coin.
Deep checks (fees, wash trading, chart shape, holders) keep running in the background pipeline afterwards.

In memory, refreshed in the background every `REFRESH_S` (never on the trading path):
  blacklist   wallets / devs behind rugs and fakes (core/learning.py adds them; you can add your own)
  devstats    every dev's launches / runners / fakes, precomputed from the DB
  kols        your KOLs' wallets (main + side)
Rejects: blacklisted dev, a "fake factory" dev (most of their runners were flagged fake), a coin already flagged fake."""
from __future__ import annotations

import threading
import time

from core.db import get_db
from core.events import log

REFRESH_S = 300
_mem = {"blacklist": {}, "devstats": {}, "kols": set(), "ts": 0.0}
_lock = threading.Lock()


def ensure_table():
    get_db().x("""CREATE TABLE IF NOT EXISTS blacklist (wallet TEXT PRIMARY KEY, kind TEXT, reason TEXT, mint TEXT, ts INTEGER,
                  source TEXT)""")


def refresh():
    """Precompute everything the gate needs (background)."""
    ensure_table()
    db = get_db()
    bl = {r["wallet"]: {"kind": r["kind"], "reason": r["reason"]} for r in db.q("SELECT * FROM blacklist")}
    ds = {}
    dev_sql = "COALESCE(json_extract(data,'$.dev_wallet'), json_extract(data,'$.creator'))"
    for r in db.q(f"""SELECT {dev_sql} d, COUNT(*) n, SUM(is_runner) runners, SUM(CASE WHEN is_runner=1 AND is_real=0 THEN 1 ELSE 0 END) fakes,
                      SUM(CASE WHEN is_runner=1 AND is_real=1 THEN 1 ELSE 0 END) real FROM coins WHERE {dev_sql} IS NOT NULL GROUP BY d"""):
        ds[r["d"]] = {"launches": r["n"], "runners": r["runners"] or 0, "fakes": r["fakes"] or 0, "real": r["real"] or 0}
    kols = set()
    try:
        from core import kol_research
        for k in kol_research.list_kols():
            kols.update([k.get("wallet")] + (k.get("side_wallets") or []))
    except Exception:
        pass
    with _lock:
        _mem.update(blacklist=bl, devstats=ds, kols={w for w in kols if w}, ts=time.time())


def ensure_fresh():
    if time.time() - _mem["ts"] > REFRESH_S:
        threading.Thread(target=refresh, daemon=True).start() if _mem["ts"] else refresh()


def devstats(dev: str | None) -> dict | None:
    return _mem["devstats"].get(dev) if dev else None


def is_blacklisted(wallet: str | None) -> dict | None:
    return _mem["blacklist"].get(wallet) if wallet else None


def check(c: dict) -> tuple[bool, str | None]:
    """Deal-breakers only. -> (ok, reason)."""
    if c.get("is_real") is False:
        return False, "flagged fake"
    dev = c.get("creator") or c.get("dev_wallet")
    b = is_blacklisted(dev)
    if b:
        return False, f"dev blacklisted ({b['reason']})"
    st = devstats(dev)
    if st and st["fakes"] >= 2 and st["fakes"] >= 0.5 * max(1, st["runners"]):
        return False, f"fake-factory dev ({st['fakes']} of {st['runners']} runners fake)"
    return True, None


def add(wallet: str, kind: str, reason: str, mint: str | None = None, source: str = "auto") -> bool:
    ensure_table()
    db = get_db()
    if db.q("SELECT 1 FROM blacklist WHERE wallet=?", (wallet,)):
        return False
    db.x("INSERT INTO blacklist(wallet,kind,reason,mint,ts,source) VALUES(?,?,?,?,?,?)", (wallet, kind, reason, mint, int(time.time()), source))
    with _lock:
        _mem["blacklist"][wallet] = {"kind": kind, "reason": reason}
    log("FEES", f"blacklisted {kind} {wallet[:6]}…: {reason}")
    return True


def remove(wallet: str):
    ensure_table()
    get_db().x("DELETE FROM blacklist WHERE wallet=?", (wallet,))
    with _lock:
        _mem["blacklist"].pop(wallet, None)


def stats() -> dict:
    return {"blacklisted": len(_mem["blacklist"]), "devs": len(_mem["devstats"]), "kol_wallets": len(_mem["kols"]),
            "refreshed": int(_mem["ts"])}
