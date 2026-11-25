"""KOL research agent.

For each KOL in kols.json (main wallet + side wallets) it pulls swap history, rebuilds positions,
enriches every token (Jupiter), and studies what they like to buy, how they trade and how they
manage risk. Output is a profile (stats + archetype + written description) stored in the
kol_profiles table and shown in the dashboard's KOL LAB.

Data: Helius enhanced transactions when HELIUS_API_KEY is set (deep history), otherwise a slow,
small sample from a public Solana RPC. Demo mode synthesizes trades from demo coins."""
from __future__ import annotations

import json
import random
import re
import statistics
import time
from collections import Counter, defaultdict

from core import http, llm, narratives
from core.config import env, get_config, load_json_data, save_json_data
from core.db import get_db
from core.events import AgentRun, log, source_result

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTES = {WSOL, USDC, USDT}
LAMPORTS = 1_000_000_000


# ---------------------------------------------------------------- KOL list (kols.json)
def list_kols() -> list[dict]:
    out = []
    for k in load_json_data("kols.json", []) or []:
        if not k.get("name"):
            continue
        k = dict(k)
        k["side_wallets"] = [w for w in (k.get("side_wallets") or []) if w]
        if k.get("wallet") or k["side_wallets"]:
            out.append(k)
    return out


def handle_of(k: dict) -> str | None:
    tw = (k.get("twitter") or "").strip().rstrip("/")
    if not tw:
        return None
    return tw.split("/")[-1].lstrip("@") or None


def save_kol(k: dict):
    kols = [x for x in (load_json_data("kols.json", []) or []) if x.get("name") and x.get("name") != k["name"]]
    kols.append(k)
    save_json_data("kols.json", kols)


def delete_kol(name: str):
    save_json_data("kols.json", [x for x in (load_json_data("kols.json", []) or []) if x.get("name") != name])
    get_db().x("DELETE FROM kol_profiles WHERE name=?", (name,))


_READY: set = set()  # (db, what) already set up in this process: reads never take the write lock


def _once(what: str) -> bool:
    key = (id(get_db()), what)
    if key in _READY:
        return False
    _READY.add(key)
    return True


def ensure_table():
    if not _once("profiles"):
        return
    get_db().x("CREATE TABLE IF NOT EXISTS kol_profiles (name TEXT PRIMARY KEY, ts INTEGER, status TEXT, data TEXT)")


def get_profile(name: str) -> dict | None:
    ensure_table()
    r = get_db().q("SELECT * FROM kol_profiles WHERE name=?", (name,))
    if not r:
        return None
    d = json.loads(r[0]["data"] or "{}")
    d.update(ts=r[0]["ts"], status=r[0]["status"])
    return d


def set_status(name: str, status: str, data: dict | None = None):
    ensure_table()
    prev = get_profile(name) or {}
    get_db().x("INSERT INTO kol_profiles(name,ts,status,data) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
               "ts=excluded.ts, status=excluded.status, data=excluded.data",
               (name, int(time.time()), status, json.dumps(data if data is not None else
                                                             {k: v for k, v in prev.items() if k not in ("ts", "status")},
                                                             default=str)))


# ---------------------------------------------------------------- fetching trades
def _conf():
    return get_config().get("kol_research", {})


PARSER_VERSION = 3  # bump when trade parsing changes: stored histories are rebuilt once


def ensure_trade_tables():
    if not _once("trades"):
        return
    db = get_db()
    db.x("""CREATE TABLE IF NOT EXISTS kol_trades (wallet TEXT, sig TEXT, ts INTEGER, mint TEXT, side TEXT, tokens REAL,
            sol REAL, usd REAL, PRIMARY KEY (wallet, sig))""")
    db.x("CREATE INDEX IF NOT EXISTS ix_kol_trades_w ON kol_trades(wallet, ts)")
    db.x("CREATE INDEX IF NOT EXISTS ix_kol_trades_wm ON kol_trades(wallet, mint)")
    db.x("""CREATE TABLE IF NOT EXISTS kol_sync (wallet TEXT PRIMARY KEY, oldest_sig TEXT, done INTEGER DEFAULT 0,
            source TEXT, updated_ts INTEGER)""")
    cols = {r["name"] for r in db.q("PRAGMA table_info(kol_trades)")}
    if "peer" not in cols:
        db.x("ALTER TABLE kol_trades ADD COLUMN peer TEXT")
    if "tacct" not in cols:  # the wallet's token account for the coin: its own history = the coin's full trade history
        db.x("ALTER TABLE kol_trades ADD COLUMN tacct TEXT")
    db.x("CREATE TABLE IF NOT EXISTS tok_info (mint TEXT PRIMARY KEY, data TEXT, ts INTEGER, price_ts INTEGER)")
    if (db.kv_get("kol_parser_version") or 1) < PARSER_VERSION:  # histories parsed the old way: re-sync from scratch
        db.x("DELETE FROM kol_trades")
        db.x("DELETE FROM kol_sync")
        db.kv_set("kol_parser_version", PARSER_VERSION)
        log("KOLS", "trade parser upgraded: re-syncing every wallet's full history")


def _store(wallet: str, trades: list[dict]) -> int:
    db = get_db()
    n = 0
    with db._lock:
        for t in trades:
            if not t.get("sig"):
                continue
            cur = db.conn.execute("INSERT OR IGNORE INTO kol_trades(wallet,sig,ts,mint,side,tokens,sol,usd,peer,tacct) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (wallet, t["sig"], t.get("ts"), t["mint"], t["side"], t["tokens"], t["sol"], t["usd"], t.get("peer"), t.get("tacct")))
            n += cur.rowcount
        db.conn.commit()
    return n


def _stored(wallet: str) -> list[dict]:
    return [dict(r) for r in get_db().q("SELECT * FROM kol_trades WHERE wallet=? ORDER BY ts", (wallet,))]


def _sync_state(wallet: str) -> dict:
    r = get_db().q("SELECT * FROM kol_sync WHERE wallet=?", (wallet,))
    return dict(r[0]) if r else {"wallet": wallet, "oldest_sig": None, "done": 0}


def _save_sync(wallet: str, **kw):
    st = {**_sync_state(wallet), **kw, "updated_ts": int(time.time())}
    get_db().x("""INSERT INTO kol_sync(wallet,oldest_sig,done,source,updated_ts) VALUES(?,?,?,?,?) ON CONFLICT(wallet) DO UPDATE SET
                  oldest_sig=excluded.oldest_sig, done=excluded.done, source=excluded.source, updated_ts=excluded.updated_ts""",
               (wallet, st.get("oldest_sig"), int(st.get("done") or 0), st.get("source"), st["updated_ts"]))


_CONTINUE = re.compile(r"`before` parameter set to ([1-9A-HJ-NP-Za-km-z]{60,90})")


def _helius_page(wallet: str, before: str | None) -> tuple[list[dict], str | None, bool]:
    """One page of swaps. Returns (txs, next_before, exhausted). Helius answers 'continue from <sig>' when
    a stretch of history has no swaps; we follow it."""
    from sources.helius import api_url
    # no type filter: Helius doesn't label many bot-routed trades (Axiom, Photon, BullX...) as SWAP
    q = f"/addresses/{wallet}/transactions?limit=100" + (f"&before={before}" if before else "")
    try:
        txs = http.get(api_url(q), rate_key="helius_kol", per_min=120, retries=2)
    except http.HTTPError as e:
        m = _CONTINUE.search(str(e))
        if m:
            return [], m.group(1), False
        raise
    if not isinstance(txs, list) or not txs:
        return [], None, True
    return txs, txs[-1].get("signature"), len(txs) < 100


def _helius_sync(wallet: str) -> None:
    """New swaps first (until we reach ones we already have), then keep backfilling older history each
    run until the wallet's very first swap: lifetime numbers are real lifetime."""
    c = _conf()
    known = {r["sig"] for r in get_db().q("SELECT sig FROM kol_trades WHERE wallet=? ORDER BY ts DESC LIMIT 300", (wallet,))}
    before = None
    for _ in range(c.get("forward_pages", 20)):
        txs, nxt, done = _helius_page(wallet, before)
        new = [tr for tr in (_parse_enhanced(t, wallet) for t in txs) if tr]
        _store(wallet, new)
        if done or any(t.get("signature") in known for t in txs) or not nxt:
            break
        before = nxt
    st = _sync_state(wallet)
    if st.get("done"):
        return
    first_row = get_db().q("SELECT sig FROM kol_trades WHERE wallet=? ORDER BY ts ASC LIMIT 1", (wallet,))
    oldest = st.get("oldest_sig") or (first_row[0]["sig"] if first_row else None)
    before, pages = oldest, 0
    while pages < c.get("backfill_pages_per_run", 30):
        txs, nxt, done = _helius_page(wallet, before)
        pages += 1
        _store(wallet, [tr for tr in (_parse_enhanced(t, wallet) for t in txs) if tr])
        if done or not nxt:
            _save_sync(wallet, oldest_sig=before, done=1, source="helius")
            return
        before = nxt
    _save_sync(wallet, oldest_sig=before, done=0, source="helius")


def _parse_enhanced(t: dict, wallet: str) -> dict | None:
    """A trade = the wallet's NET balance changes in one transaction, whatever program or bot routed it
    (Axiom, Photon, BullX, Jupiter, pump.fun, PumpSwap...). Net token changes come from tokenBalanceChanges of
    the wallet's own token accounts, so wrapping/unwrapping SOL nets out instead of being counted twice."""
    if t.get("transactionError"):
        return None
    deltas = defaultdict(float)
    sol = 0.0
    for a in t.get("accountData") or []:
        if a.get("account") == wallet:
            sol += (a.get("nativeBalanceChange") or 0) / LAMPORTS
        for tb in a.get("tokenBalanceChanges") or []:
            if tb.get("userAccount") != wallet:
                continue
            raw = tb.get("rawTokenAmount") or {}
            try:
                deltas[tb.get("mint")] += int(raw.get("tokenAmount") or 0) / 10 ** int(raw.get("decimals") or 0)
            except (TypeError, ValueError):
                continue
    tr = _to_trade(deltas, sol, t.get("timestamp"), t.get("signature"))
    if tr:
        for a in t.get("accountData") or []:
            for tb in a.get("tokenBalanceChanges") or []:
                if tb.get("userAccount") == wallet and tb.get("mint") == tr["mint"] and tb.get("tokenAccount"):
                    tr["tacct"] = tb["tokenAccount"]
    if tr and tr["side"].startswith("xfer"):  # who the tokens went to / came from (own wallet or not)
        for x in t.get("tokenTransfers") or []:
            if x.get("mint") != tr["mint"]:
                continue
            if tr["side"] == "xfer_out" and x.get("fromUserAccount") == wallet:
                tr["peer"] = x.get("toUserAccount")
            elif tr["side"] == "xfer_in" and x.get("toUserAccount") == wallet:
                tr["peer"] = x.get("fromUserAccount")
    return tr


def _rpc_trades(wallet: str) -> list[dict]:
    rpc = _conf().get("rpc_url", "https://api.mainnet-beta.solana.com")
    n = _conf().get("rpc_sample", 60)
    sigs = http.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                "params": [wallet, {"limit": n}]}, rate_key="kol_rpc", per_min=12).get("result") or []
    reqs = [{"jsonrpc": "2.0", "id": i, "method": "getTransaction",
             "params": [s["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "json"}]}
            for i, s in enumerate(sigs) if not s.get("err")]
    out = []
    for i in range(0, len(reqs), 5):
        res = http.post(rpc, json=reqs[i:i + 5], rate_key="kol_rpc", per_min=12)
        for r in res if isinstance(res, list) else []:
            tr = _parse_raw(r.get("result"), wallet)
            if tr:
                sigs_ = (r.get("result") or {}).get("transaction", {}).get("signatures") or []
                tr["sig"] = sigs_[0] if sigs_ else None
                out.append(tr)
    return out


def _parse_raw(tx: dict | None, wallet: str) -> dict | None:
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return None
    meta, msg = tx["meta"], (tx.get("transaction") or {}).get("message") or {}
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in msg.get("accountKeys") or []]
    la = meta.get("loadedAddresses") or {}
    keys += (la.get("writable") or []) + (la.get("readonly") or [])
    if wallet not in keys:
        return None
    i = keys.index(wallet)
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    sol = (post[i] - pre[i]) / LAMPORTS if i < len(pre) and i < len(post) else 0
    deltas = defaultdict(float)
    for sign, lst in ((-1, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in lst or []:
            if b.get("owner") == wallet:
                deltas[b.get("mint")] += sign * float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
    return _to_trade(deltas, sol, tx.get("blockTime"), None)


def _to_trade(deltas: dict, sol_change: float, ts, sig) -> dict | None:
    """One swap: exactly one non-quote token moved. SOL side = native change + wSOL; stables in USD."""
    tokens = {m: d for m, d in deltas.items() if m and m not in QUOTES and abs(d) > 1e-9}
    if len(tokens) != 1:
        return None
    mint, amount = next(iter(tokens.items()))
    sol = sol_change + deltas.get(WSOL, 0)
    usd = deltas.get(USDC, 0) + deltas.get(USDT, 0)
    # no SOL / stable moved: a token transfer (to a side wallet, a CEX, an airdrop...). Kept so the ledger knows
    # the tokens left (or arrived) without a price, instead of thinking they're still held
    if abs(sol) < 0.0005 and abs(usd) < 0.05:
        return {"mint": mint, "side": "xfer_in" if amount > 0 else "xfer_out", "tokens": abs(amount), "sol": 0.0, "usd": 0.0,
                "ts": ts, "sig": sig}
    if (amount > 0 and sol > 0.0005) or (amount < 0 and sol < -0.0005 and not usd):
        return None
    side = "buy" if amount > 0 else "sell"
    return {"mint": mint, "side": side, "tokens": abs(amount), "sol": abs(sol), "usd": abs(usd), "ts": ts, "sig": sig}


def _demo_trades(wallet: str) -> list[dict]:
    """Deterministic fake trades on demo coins (demo mode only)."""
    r = random.Random(wallet)
    coins = get_db().coins("created_ts >= ?", (int(time.time() - 7 * 86400),), order="ath_mcap DESC", limit=400)
    if not coins:
        return []
    style = r.choice(["sniper", "scalper", "swing", "momentum"])
    picks = r.sample(coins[:200] if style != "sniper" else coins, min(len(coins), r.randint(25, 60)))
    out = []
    size = {"sniper": 1.5, "scalper": 4, "swing": 8, "momentum": 15}[style]
    for c in picks:
        t0 = (c.get("created_ts") or time.time()) + {"sniper": r.uniform(5, 240), "scalper": r.uniform(300, 3600),
                                                       "swing": r.uniform(600, 7200), "momentum": r.uniform(3600, 20000)}[style]
        if t0 > time.time():
            continue
        buy_sol = max(0.1, r.gauss(size, size * 0.35))
        entry_mc = {"sniper": r.uniform(5e3, 2e4), "scalper": r.uniform(2e4, 1e5), "swing": r.uniform(3e4, 2e5),
                    "momentum": r.uniform(2e5, 2e6)}[style]
        tokens = buy_sol * 150 / entry_mc * 1e9
        out.append({"mint": c["mint"], "side": "buy", "tokens": tokens, "sol": buy_sol, "usd": 0, "ts": int(t0), "sig": None})
        mult = r.choice([0.3, 0.5, 0.7, 1.2, 1.6, 2.5, 4, 8]) if (c.get("ath_mcap") or 0) > 1e5 else r.choice([0.2, 0.4, 0.6, 1.1])
        hold = {"sniper": r.uniform(60, 1800), "scalper": r.uniform(60, 900), "swing": r.uniform(3600, 86400),
                "momentum": r.uniform(1800, 20000)}[style]
        exits = r.choice([1, 1, 2, 3]) if style != "scalper" else 1
        if r.random() < 0.85 and t0 + hold < time.time():
            for e in range(exits):
                out.append({"mint": c["mint"], "side": "sell", "tokens": tokens / exits, "sol": buy_sol * mult / exits,
                            "usd": 0, "ts": int(t0 + hold * (e + 1) / exits), "sig": None})
    return out


def coverage(wallet: str, trades: list[dict], src: str) -> dict:
    st = _sync_state(wallet)
    ts = [t["ts"] for t in trades if t.get("ts")]
    return {"swaps": len(trades), "since_ts": min(ts) if ts else None, "source": src,
            "complete": src == "demo" or bool(st.get("done"))}


# ---------------------------------------------------------------- per-coin full history
def _helius_parse(sigs: list[str]) -> list[dict]:
    from sources.helius import api_url
    out = []
    for i in range(0, len(sigs), 100):
        try:
            r = http.post(api_url("/transactions"), json={"transactions": sigs[i:i + 100]}, rate_key="helius_kol", per_min=120, retries=2)
            out += r if isinstance(r, list) else []
        except Exception:
            continue
    return out


def _token_accounts(wallet: str, mint: str) -> set[str]:
    """The wallet's token account(s) for a coin. Known from stored trades; older trades (stored before this field
    existed) are re-read once from Helius to learn it."""
    db = get_db()
    rows = db.q("SELECT sig, tacct FROM kol_trades WHERE wallet=? AND mint=? ORDER BY ts DESC LIMIT 20", (wallet, mint))
    accts = {r["tacct"] for r in rows if r["tacct"]}
    if not accts and rows:
        for t in _helius_parse([rows[0]["sig"]]):
            tr = _parse_enhanced(t, wallet)
            if tr and tr.get("tacct"):
                accts.add(tr["tacct"])
                db.x("UPDATE kol_trades SET tacct=? WHERE wallet=? AND mint=?", (tr["tacct"], wallet, mint))
    return accts


def coin_history(wallet: str, mint: str, max_pages: int = 10) -> int:
    """Pull EVERY transaction on the wallet's token account for this coin (back to the first buy), so the coin's PnL
    is complete even when the wallet's overall history isn't loaded that far back. Returns new trades stored."""
    from sources.helius import rpc_url
    db = get_db()
    have = {r["sig"] for r in db.q("SELECT sig FROM kol_trades WHERE wallet=? AND mint=?", (wallet, mint))}
    new_sigs = []
    for acct in _token_accounts(wallet, mint):
        before = None
        for _ in range(max_pages):
            params = {"limit": 1000, **({"before": before} if before else {})}
            try:
                from core import rpc
                r = rpc.call("getSignaturesForAddress", [acct, params])  # fastest healthy RPC, fails over
            except Exception:
                break
            page = (r or {}).get("result") or []
            new_sigs += [x["signature"] for x in page if not x.get("err") and x["signature"] not in have]
            if len(page) < 1000:
                break
            before = page[-1]["signature"]
    new_sigs = list(dict.fromkeys(new_sigs))
    trades = [tr for tr in (_parse_enhanced(t, wallet) for t in _helius_parse(new_sigs)) if tr]
    n = _store(wallet, trades) if trades else 0
    db.kv_set(f"coinhist:{wallet}:{mint}", {"ts": int(time.time()), "added": n})
    return n


def complete_coins(wallet: str, budget: int | None = None) -> dict:
    """Which coins need their full history: sells with no buy before them (bought before the loaded history),
    then coins still held, then the biggest trades. Each coin is done once (re-checked weekly)."""
    db = get_db()
    budget = budget or _conf().get("coin_history_per_run", 40)
    trades = _stored(wallet)
    by = defaultdict(list)
    for t in trades:
        by[t["mint"]].append(t)
    pri = []
    for m, ts in by.items():
        ts.sort(key=lambda t: t.get("ts") or 0)
        qty, unpriced, vol = 0.0, False, 0.0
        for t in ts:
            if t["side"] in ("buy", "xfer_in"):
                qty += t["tokens"] or 0
            elif t["side"] == "sell":
                if qty <= 1e-9:
                    unpriced = True
                qty -= t["tokens"] or 0
            vol += abs(t.get("sol") or 0)
        held = qty > 1e-6
        done = db.kv_get(f"coinhist:{wallet}:{m}")
        if done and time.time() - done["ts"] < 7 * 86400 and not (unpriced and done.get("added", 0) == 0 and time.time() - done["ts"] > 86400):
            continue
        if unpriced or held or vol > 0:
            pri.append((0 if unpriced else 1 if held else 2, -vol, m))
    pri.sort()
    added = 0
    for _, _, m in pri[:budget]:
        try:
            added += coin_history(wallet, m)
        except Exception as e:
            log("KOLS", f"coin history {m[:6]}: {e}", "warn")
    if added:
        log("KOLS", f"{wallet[:6]}: completed {min(budget, len(pri))} coins' full history (+{added} trades)")
    return {"checked": min(budget, len(pri)), "added": added, "remaining": max(0, len(pri) - budget)}


def fetch_trades(wallet: str) -> tuple[list[dict], str, dict]:
    """All known swaps for a wallet: synced into the local trade store, then read back in full."""
    cfg = get_config()
    ensure_trade_tables()
    if cfg.demo:
        tr = _demo_trades(wallet)
        return tr, "demo", coverage(wallet, tr, "demo")
    try:
        if env("HELIUS_API_KEY"):
            _helius_sync(wallet)
            try:  # per-coin full history: fixes PnL for coins bought before the loaded wallet history
                complete_coins(wallet)
            except Exception as e:
                log("KOLS", f"coin history: {e}", "warn")
            src = "helius"
        else:
            _store(wallet, _rpc_trades(wallet))
            src = "public-rpc"
        tr = _stored(wallet)
        cov = coverage(wallet, tr, src)
        source_result("kol_research", "KOLS", True,
                      note=f"{src}: {len(tr)} swaps for {wallet[:6]}" + ("" if cov["complete"] else " (backfilling)"))
        return tr, src, cov
    except Exception as e:
        source_result("kol_research", "KOLS", False, error=str(e))
        tr = _stored(wallet)  # whatever we have is still useful
        if tr:
            return tr, "stored", coverage(wallet, tr, "stored")
        raise


# ---------------------------------------------------------------- token info
def token_info(mints: list[str], fresh: set | None = None) -> tuple[dict, float]:
    """Jupiter lookup -> {mint: info}, SOL price. Coin facts (name, symbol, launch, supply) are cached forever;
    prices are refreshed only for `fresh` mints (recently traded / still held) - a 20k-coin wallet costs a
    handful of calls per run instead of hundreds."""
    info, sol_usd = {}, 0.0
    mints = list(dict.fromkeys([WSOL] + mints))
    if get_config().demo:
        for c in get_db().coins("1=1", limit=20000):
            if c["mint"] in mints:
                info[c["mint"]] = {"symbol": c.get("symbol"), "name": c.get("name"), "launchpad": "pump.fun",
                                   "created_ts": c.get("created_ts"), "mcap": c.get("usd_market_cap") or 0,
                                   "usd_price": (c.get("usd_market_cap") or 0) / 1e9, "supply": 1e9,
                                   "holders": c.get("holders"), "ath": c.get("ath_mcap"), "image": c.get("image")}
        return info, 150.0
    from sources.jupiter import _iso
    ensure_trade_tables()
    db, now = get_db(), int(time.time())
    fresh = set(fresh or ()) | {WSOL}
    cached = {}
    for i in range(0, len(mints), 500):
        chunk = mints[i:i + 500]
        for r in db.q(f"SELECT * FROM tok_info WHERE mint IN ({','.join('?' * len(chunk))})", tuple(chunk)):
            cached[r["mint"]] = (json.loads(r["data"]), r["price_ts"] or 0)
    need = [m for m in mints if m not in cached or (m in fresh and now - cached[m][1] > 600)]
    base = get_config().get_path("sources.jupiter.base_url", "https://lite-api.jup.ag")
    got = {}
    for i in range(0, len(need), 100):
        try:
            rows = http.get(f"{base}/tokens/v2/search?query={','.join(need[i:i + 100])}", rate_key="jupiter", per_min=50)
        except Exception:
            continue
        for t in rows if isinstance(rows, list) else []:
            got[t["id"]] = {"symbol": t.get("symbol"), "name": t.get("name"), "launchpad": t.get("launchpad"),
                            "created_ts": _iso((t.get("firstPool") or {}).get("createdAt")) or _iso(t.get("createdAt")),
                            "mcap": t.get("mcap") or 0, "usd_price": float(t.get("usdPrice") or 0),
                            "supply": float(t.get("circSupply") or t.get("totalSupply") or 1e9),
                            "holders": t.get("holderCount"), "twitter": t.get("twitter"), "image": t.get("icon")}
    with db._lock:
        for m, d in got.items():
            db.conn.execute("INSERT INTO tok_info(mint,data,ts,price_ts) VALUES(?,?,?,?) ON CONFLICT(mint) DO UPDATE SET "
                            "data=excluded.data, ts=excluded.ts, price_ts=excluded.price_ts", (m, json.dumps(d), now, now))
        db.conn.commit()
    for m in mints:
        d = got.get(m) or (cached.get(m) or ({}, 0))[0]
        if m == WSOL:
            sol_usd = float(d.get("usd_price") or 0)
        elif d:
            info[m] = d
    return info, sol_usd or float(db.kv_get("sol_usd") or 150)


# ---------------------------------------------------------------- analysis
def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _bucket(v, edges):
    for lo, hi, lbl in edges:
        if lo <= v < hi:
            return lbl
    return edges[-1][2]


MCAP_BUCKETS = [(0, 1e4, "<$10k"), (1e4, 5e4, "$10–50k"), (5e4, 2.5e5, "$50–250k"), (2.5e5, 1e6, "$250k–1M"), (1e6, 1e18, ">$1M")]
AGE_BUCKETS = [(0, 300, "<5m"), (300, 3600, "5–60m"), (3600, 86400, "1–24h"), (86400, 1e12, ">1d")]


def positions(trades: list[dict], info: dict, sol_usd: float, own: set | None = None, complete: bool = True) -> list[dict]:
    """Rebuild positions with a running average-cost ledger (how wallet trackers compute realized PnL):
    buys add cost; each sell realizes proceeds - (average cost so far x tokens sold); transfers to/from the KOL's
    own wallets are ignored (the pooled ledger already holds both sides); transfers elsewhere leave at cost
    (no PnL) or arrive at zero cost. Sells with no known cost (bought before the loaded history) are skipped and
    counted, not guessed."""
    own = own or set()
    by = defaultdict(list)
    for t in trades:
        by[t["mint"]].append(t)
    now = time.time()
    out = []
    sol_of = lambda t: t["sol"] if (t.get("sol") or 0) > 1e-6 else ((t.get("usd") or 0) / sol_usd if sol_usd else 0)  # noqa: E731
    for mint, ts in by.items():
        ts.sort(key=lambda t: t.get("ts") or 0)
        qty = cost = 0.0
        bought_tok = inv = ret = 0.0
        exits, buys_n, sells_n, first, last_sell = [], 0, 0, None, None
        unpriced_n, unpriced_sol = 0, 0.0
        for t in ts:
            side, tok = t["side"], t.get("tokens") or 0
            if side.startswith("xfer") and t.get("peer") in own:
                continue  # between the KOL's own wallets
            if side == "buy":
                sol = sol_of(t)
                qty += tok; cost += sol; bought_tok += tok; inv += sol; buys_n += 1
                first = first or t
            elif side == "xfer_in":
                qty += tok  # received free (airdrop / from a dev / unknown wallet): zero cost
                first = first or t
            elif side == "sell":
                sol = sol_of(t)
                if qty <= 1e-9:
                    unpriced_n += 1; unpriced_sol += sol  # bought before the loaded history
                    continue
                sold = min(tok, qty)
                avg = cost / qty
                basis = avg * sold
                pnl = sol * (sold / tok if tok else 1) - basis
                exits.append({"x": round(sol / tok / avg, 3) if tok and avg else None, "frac": round(sold / max(bought_tok, sold), 3),
                              "t": int(t["ts"] - first["ts"]) if t.get("ts") and first and first.get("ts") else None,
                              "ts": t.get("ts"), "sol": round(sol, 4), "pnl": round(pnl, 4)})
                ret += sol; qty -= sold; cost -= basis; sells_n += 1; last_sell = t
            elif side == "xfer_out":
                if qty > 1e-9:
                    sold = min(tok, qty)
                    cost -= cost / qty * sold  # left the wallet at cost: no PnL
                    qty -= sold
        if not first:
            if unpriced_n:
                out.append({"mint": mint, "symbol": (info.get(mint) or {}).get("symbol") or mint[:6], "unpriced_only": True,
                            "unpriced_sells": unpriced_n, "unpriced_sol": round(unpriced_sol, 3), "closed": True,
                            "exits": [], "invested_sol": 0, "returned_sol": 0, "unrealized_sol": 0, "pnl_sol": 0,
                            "cost_left_sol": 0, "first_buy_ts": None, "end_ts": None, "themes": [], "buys": 0, "sells": 0})
            continue
        ti = info.get(mint, {})
        peak = max(bought_tok, 1e-12)
        closed = qty <= peak * 0.05
        unreal = 0.0 if closed else (qty * ti.get("usd_price", 0) / sol_usd if sol_usd else 0)
        realized = sum(e["pnl"] for e in exits)
        end = (last_sell or {}).get("ts") if closed else now
        entry_price_usd = (sol_of(first) * sol_usd / first["tokens"]) if first.get("tokens") and first["side"] == "buy" else 0
        entry_mcap = entry_price_usd * ti.get("supply", 1e9)
        age = (first["ts"] - ti["created_ts"]) if ti.get("created_ts") and first.get("ts") else None
        sym = ti.get("symbol") or mint[:6]
        cost_left = 0.0 if closed else cost
        out.append({
            "end_ts": int(end) if end else None, "exits": exits, "cost_left_sol": round(cost_left, 4),
            "mint": mint, "symbol": sym, "name": ti.get("name"), "launchpad": ti.get("launchpad"),
            "themes": narratives.tag({"name": ti.get("name"), "symbol": sym}),
            "first_buy_ts": first.get("ts"), "invested_sol": round(inv, 3), "returned_sol": round(ret, 3),
            "unrealized_sol": round(unreal, 3), "realized_sol": round(realized, 3),
            "pnl_sol": round(realized + (unreal - cost_left), 3),
            "multiple": round((ret + unreal) / inv, 2) if inv else None,
            "closed": closed, "buys": buys_n, "sells": sells_n,
            "hold_s": int(end - first["ts"]) if first.get("ts") and end else None,
            "image": ti.get("image"), "mcap_now": ti.get("mcap") or None,
            "entry_mcap": round(entry_mcap) if entry_mcap else None, "entry_age_s": int(age) if age is not None and age >= 0 else None,
            "unpriced_sells": unpriced_n, "unpriced_sol": round(unpriced_sol, 3),
        })
    return out


def stats(pos: list[dict], trades: list[dict]) -> dict:
    if not pos:
        return {"positions": 0, "trades": len(trades)}
    closed = [p for p in pos if p["closed"]]
    wins = [p for p in closed if p["pnl_sol"] > 0]
    losses = [p for p in closed if p["pnl_sol"] <= 0]
    sizes = [p["invested_sol"] for p in pos if p["invested_sol"]]
    ts = [t["ts"] for t in trades if t.get("ts")]
    span_d = max(1 / 24, (max(ts) - min(ts)) / 86400) if ts else 1
    mc = Counter(_bucket(p["entry_mcap"], MCAP_BUCKETS) for p in pos if p.get("entry_mcap"))
    ages = Counter(_bucket(p["entry_age_s"], AGE_BUCKETS) for p in pos if p.get("entry_age_s") is not None)
    themes = Counter(t for p in pos for t in (p["themes"] or ["Unthemed"]))
    pads = Counter(p.get("launchpad") or "unknown" for p in pos)
    hours = Counter(time.gmtime(t).tm_hour for t in ts)
    open_bags = [p for p in pos if not p["closed"] and p["multiple"] is not None and p["multiple"] < 0.5]
    size_cv = statistics.pstdev(sizes) / statistics.mean(sizes) if len(sizes) > 2 and statistics.mean(sizes) else None
    loss_pct = [(p["multiple"] - 1) * 100 for p in losses if p["multiple"] is not None]
    ranked = sorted(pos, key=lambda p: p["pnl_sol"])
    return {
        "trades": len(trades), "positions": len(pos), "closed": len(closed), "days": round(span_d, 1),
        "trades_per_day": round(len(trades) / span_d, 1),
        "median_size_sol": round(_med(sizes), 2) if sizes else None, "size_cv": round(size_cv, 2) if size_cv is not None else None,
        "median_entry_mcap": _med([p["entry_mcap"] for p in pos if p.get("entry_mcap")]),
        "median_entry_age_s": _med([p["entry_age_s"] for p in pos if p.get("entry_age_s") is not None]),
        "entry_mcap_mix": dict(mc.most_common()), "entry_age_mix": dict(ages.most_common()),
        "themes": dict(themes.most_common(8)), "launchpads": dict(pads.most_common(5)),
        "win_rate": round(len(wins) / len(closed) * 100) if closed else None,
        "avg_win_x": round(statistics.mean([p["multiple"] for p in wins if p["multiple"]]), 2) if wins else None,
        "median_loss_pct": round(_med(loss_pct)) if loss_pct else None,
        "median_hold_s": _med([p["hold_s"] for p in closed if p.get("hold_s") is not None]),
        "median_hold_win_s": _med([p["hold_s"] for p in wins if p.get("hold_s") is not None]),
        "median_hold_loss_s": _med([p["hold_s"] for p in losses if p.get("hold_s") is not None]),
        "scale_out_pct": round(sum(1 for p in closed if p["sells"] >= 2) / len(closed) * 100) if closed else None,
        "bag_hold_pct": round(len(open_bags) / len(pos) * 100),
        "realized_pnl_sol": round(sum(p["returned_sol"] - p["invested_sol"] for p in closed), 2),
        "total_pnl_sol": round(sum(p["pnl_sol"] for p in pos), 2),
        "active_hours_utc": [h for h, _ in hours.most_common(3)],
        "best": [{k: p[k] for k in ("symbol", "mint", "pnl_sol", "multiple")} for p in ranked[::-1][:3] if p["pnl_sol"] > 0],
        "worst": [{k: p[k] for k in ("symbol", "mint", "pnl_sol", "multiple")} for p in ranked[:3] if p["pnl_sol"] < 0],
    }


PERIODS = [("lifetime", None), ("month", 30 * 86400), ("week", 7 * 86400), ("day", 86400)]


def pnl_periods(pos: list[dict], sol_usd: float, trades: list[dict] | None = None) -> dict:
    """PnL per period, accurate to the period:
    realized   = profit on sells made INSIDE the period (sale proceeds - average cost of the tokens sold)
    unrealized = current value - cost of what's still held (lifetime: all bags; periods: bags opened in the period)
    win rate   = positions whose final sell happened inside the period."""
    now, out = time.time(), {}
    for key, span in PERIODS:
        start = 0 if span is None else now - span
        realized = sum(e["pnl"] for p in pos for e in p.get("exits") or [] if (e.get("ts") or 0) >= start and e.get("pnl") is not None)
        held = [p for p in pos if not p["closed"] and (span is None or (p.get("first_buy_ts") or 0) >= start)]
        unreal = sum(p["unrealized_sol"] - p.get("cost_left_sol", 0) for p in held)
        closed_in = [p for p in pos if p["closed"] and (p.get("end_ts") or 0) >= start]
        tr = [t for t in (trades or []) if (t.get("ts") or 0) >= start]
        per_pos = sorted(((sum(e["pnl"] for e in p.get("exits") or [] if (e.get("ts") or 0) >= start), p["symbol"]) for p in pos),
                         reverse=True)
        bought = sum(t["sol"] for t in tr if t["side"] == "buy")
        total = realized + unreal
        out[key] = {"pnl_sol": round(total, 2), "realized_sol": round(realized, 2), "unrealized_sol": round(unreal, 2),
                    "pnl_usd": round(total * sol_usd) if sol_usd else None,
                    "trades": len(tr), "volume_sol": round(sum(t["sol"] for t in tr), 2), "bought_sol": round(bought, 2),
                    "positions": len(closed_in) + len(held),
                    "win_rate": round(sum(1 for p in closed_in if p["pnl_sol"] > 0) / len(closed_in) * 100) if closed_in else None,
                    "roi_pct": round(realized / bought * 100) if bought else None,
                    "best": {"symbol": per_pos[0][1], "pnl_sol": round(per_pos[0][0], 2)} if per_pos and per_pos[0][0] > 0 else None}
    return out


def archetype(s: dict) -> tuple[list[str], list[str]]:
    """Rule-based style + risk labels from stats."""
    style, risk = [], []
    age, mc, hold = s.get("median_entry_age_s"), s.get("median_entry_mcap"), s.get("median_hold_s")
    if age is not None and age < 600 and (mc or 0) < 30_000:
        style.append("Sniper")
    if hold is not None and hold < 1200:
        style.append("Scalper")
    elif hold is not None and hold > 12 * 3600:
        style.append("Swing / conviction")
    if (mc or 0) > 500_000:
        style.append("Late momentum")
    if (s.get("median_size_sol") or 0) >= 10:
        style.append("Whale sizing")
    th = [(t, n) for t, n in (s.get("themes") or {}).items() if t != "Unthemed"]
    if th and s.get("positions") and th[0][1] / s["positions"] >= 0.35:
        style.append(f"{th[0][0]} hunter")
    if (s.get("trades_per_day") or 0) > 40:
        style.append("High-frequency")
    if not style:
        style.append("Generalist")
    hw, hl = s.get("median_hold_win_s"), s.get("median_hold_loss_s")
    if hw and hl and hl < hw / 2 and (s.get("median_loss_pct") or -100) > -45:
        risk.append("Cuts losers fast")
    elif (s.get("median_loss_pct") or 0) < -70:
        risk.append("Rides losers to zero")
    if (s.get("scale_out_pct") or 0) >= 50:
        risk.append("Scales out")
    if (s.get("bag_hold_pct") or 0) >= 30:
        risk.append("Bag holder")
    if s.get("size_cv") is not None:
        risk.append("Consistent sizing" if s["size_cv"] < 0.5 else "Erratic sizing")
    if (s.get("win_rate") or 0) >= 55:
        risk.append("High win rate")
    return style, risk


def side_wallet_insights(per_wallet: dict, main: str | None) -> list[str]:
    out = []
    if not main or main not in per_wallet:
        return out
    main_first = {p["mint"]: p["first_buy_ts"] for p in per_wallet[main]["positions"] if p.get("first_buy_ts")}
    for w, d in per_wallet.items():
        if w == main:
            continue
        shared = [(p, main_first[p["mint"]]) for p in d["positions"] if p["mint"] in main_first and p.get("first_buy_ts")]
        if not shared:
            out.append(f"{w[:4]}…{w[-4:]}: no overlap with the main wallet ({len(d['positions'])} positions)")
            continue
        early = [p for p, m in shared if p["first_buy_ts"] < m]
        lead = _med([m - p["first_buy_ts"] for p, m in shared if p["first_buy_ts"] < m])
        msg = f"{w[:4]}…{w[-4:]}: shares {len(shared)} coins with main"
        if early:
            msg += f", bought FIRST on {len(early)} of them (median {_fmt_dur(lead)} earlier)"
        out.append(msg)
    return out


def _fmt_dur(s):
    if s is None:
        return "—"
    return f"{s / 86400:.1f}d" if s >= 86400 else f"{s / 3600:.1f}h" if s >= 3600 else f"{s / 60:.0f}m" if s >= 60 else f"{s:.0f}s"


def _fmt_usd(v):
    return "—" if not v else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def describe(k: dict, s: dict, style: list[str], risk: list[str], sides: list[str]) -> tuple[str, str]:
    if llm.available():
        slim = {x: s.get(x) for x in ("trades", "positions", "days", "trades_per_day", "median_size_sol", "size_cv",
                                     "median_entry_mcap", "median_entry_age_s", "entry_mcap_mix", "entry_age_mix",
                                     "themes", "launchpads", "win_rate", "avg_win_x", "median_loss_pct", "median_hold_s",
                                     "median_hold_win_s", "median_hold_loss_s", "scale_out_pct", "bag_hold_pct",
                                     "realized_pnl_sol", "best", "worst")}
        out = llm.ask(
            f"KOL: {k['name']} (X: {k.get('twitter') or 'n/a'}). Labels: style={style}, risk={risk}.\n"
            f"Side-wallet notes: {sides}\nWallet stats (times in seconds, SOL amounts): {json.dumps(slim, default=str)}\n\n"
            "Write a trader profile in two short paragraphs: (1) STYLE: what they buy and when (entry market cap, "
            "token age, themes, sizing, hold time); (2) RISK MANAGEMENT: how they handle losers and winners, scaling "
            "out, bag holding, consistency. Then 3 bullets starting with '- ' on how to read their buys. Cite numbers. "
            "No hype, no financial advice.",
            system="You are an on-chain analyst profiling memecoin traders from their wallet history. Terse and specific.",
            max_tokens=900)
        if out:
            return out, "claude"
    p1 = (f"{k['name']} is a {' / '.join(style).lower()} trader: median buy {s.get('median_size_sol') or '—'} SOL, "
          f"usually entering around {_fmt_usd(s.get('median_entry_mcap'))} market cap, {_fmt_duration_words(s.get('median_entry_age_s'))} after launch, "
          f"and holding about {_fmt_dur(s.get('median_hold_s'))}. Favourite themes: "
          f"{', '.join([t for t in (s.get('themes') or {}) if t != 'Unthemed'][:3]) or 'none clear'}. "
          f"{s.get('trades_per_day') or 0} trades a day over {s.get('days')} days.")
    p2 = (f"Risk: {', '.join(risk).lower() or 'no clear pattern yet'}. Win rate {s.get('win_rate') if s.get('win_rate') is not None else '—'}%, "
          f"winners average {s.get('avg_win_x') or '—'}x, median loser {s.get('median_loss_pct') if s.get('median_loss_pct') is not None else '—'}%. "
          f"Winners held {_fmt_dur(s.get('median_hold_win_s'))} vs losers {_fmt_dur(s.get('median_hold_loss_s'))}; "
          f"{s.get('scale_out_pct') if s.get('scale_out_pct') is not None else '—'}% of exits are scaled, "
          f"{s.get('bag_hold_pct')}% of positions are open bags down 50%+.")
    return p1 + "\n\n" + p2, "template"


def _fmt_duration_words(s):
    return "—" if s is None else _fmt_dur(s)


def x_profile(handle: str | None) -> dict:
    if not handle:
        return {}
    prof = {"handle": handle, "pfp": f"https://unavatar.io/x/{handle}"}
    if env("X_BEARER_TOKEN") and not get_config().demo:
        try:
            r = http.get(f"https://api.x.com/2/users/by/username/{handle}?user.fields=profile_image_url,description,public_metrics,name",
                         headers={"Authorization": f"Bearer {env('X_BEARER_TOKEN')}"}, rate_key="twitter", per_min=30, retries=1)
            u = (r or {}).get("data") or {}
            if u.get("profile_image_url"):
                prof["pfp"] = u["profile_image_url"].replace("_normal", "_400x400")
            prof.update(display_name=u.get("name"), bio=u.get("description"),
                        followers=(u.get("public_metrics") or {}).get("followers_count"))
        except Exception:
            pass
    return prof


# ---------------------------------------------------------------- entry points
import threading as _threading

_research_lock = _threading.Lock()


def research(name: str) -> dict | None:
    with _research_lock:
        return _research(name)


def _research(name: str) -> dict | None:
    k = next((x for x in list_kols() if x["name"] == name), None)
    if not k:
        return None
    set_status(name, "running")
    with AgentRun("KOLS", f"researching {name}") as run:
        wallets = [w for w in [k.get("wallet")] + k["side_wallets"] if w]
        per_wallet, all_trades, src, covs = {}, [], None, {}
        for w in wallets:
            try:
                tr, src, covs[w] = fetch_trades(w)
            except Exception as e:
                log("KOLS", f"{name}: {w[:6]}… fetch failed: {e}", "warn")
                tr = []
            for t in tr:
                t["wallet"] = w
            per_wallet[w] = {"trades": tr}
            all_trades += tr
        own = set(wallets)
        complete = bool(covs) and all(c["complete"] for c in covs.values())
        recent_cut = time.time() - 3 * 86400
        fresh = {t["mint"] for t in all_trades if (t.get("ts") or 0) >= recent_cut}
        prev = get_profile(name) or {}
        fresh |= {h["mint"] for h in prev.get("holdings") or []}
        info, sol_usd = token_info(list({t["mint"] for t in all_trades}), fresh)
        for w, d in per_wallet.items():
            d["positions"] = [p for p in positions(d["trades"], info, sol_usd, {w}, complete) if not p.get("unpriced_only")]
            d["stats"] = stats(d["positions"], d["trades"])
        pos_all = positions(all_trades, info, sol_usd, own, complete)
        unpriced = {"sells": sum(p.get("unpriced_sells") or 0 for p in pos_all),
                    "sol": round(sum(p.get("unpriced_sol") or 0 for p in pos_all), 2)}
        all_pos = [p for p in pos_all if not p.get("unpriced_only")]
        s = stats(all_pos, all_trades)
        style, risk = archetype(s)
        sides = side_wallet_insights(per_wallet, k.get("wallet"))
        text, by = describe(k, s, style, risk, sides)
        recent = sorted(all_pos, key=lambda p: -(p.get("first_buy_ts") or 0))[:15]
        periods = pnl_periods(all_pos, sol_usd, all_trades)
        # realized PnL by sell time, bucketed (hourly for the last 30 days, daily before) so a 20k-trade wallet
        # stays small; the chart for any period is the running sum of buckets inside it
        buckets = defaultdict(float)
        for p in all_pos:
            for e in p.get("exits") or []:
                if e.get("ts") and e.get("pnl") is not None:
                    size = 3600 if e["ts"] >= time.time() - 30 * 86400 else 86400
                    buckets[e["ts"] // size * size] += e["pnl"]
        series = [[t, round(v, 4), ""] for t, v in sorted(buckets.items())]
        # what they're holding right now (open positions that still have some value): the KOL's moons
        holdings = sorted([p for p in all_pos if not p["closed"] and (p["unrealized_sol"] >= _conf().get("min_holding_sol", 0.01)
                                                                       or not info.get(p["mint"], {}).get("usd_price"))],
                          key=lambda p: -(p["unrealized_sol"] or p["invested_sol"]))[: _conf().get("max_holdings", 12)]
        profile = {
            "name": name, "twitter": k.get("twitter"), "x": x_profile(handle_of(k)),
            "wallets": [{"address": w, "role": "main" if w == k.get("wallet") else "side",
                         "stats": {x: d["stats"].get(x) for x in ("trades", "positions", "win_rate", "median_size_sol",
                                                                   "median_hold_s", "total_pnl_sol")}}
                        for w, d in per_wallet.items()],
            "stats": s, "style": style, "risk": risk, "side_insights": sides,
            "description": text, "description_by": by, "data_source": src, "sol_usd": sol_usd,
            "recent": recent, "holdings": holdings, "periods": periods, "pnl_series": series,
            "unpriced": unpriced,
            "history": {"complete": bool(covs) and all(c["complete"] for c in covs.values()),
                        "swaps": sum(c["swaps"] for c in covs.values()),
                        "since_ts": min([c["since_ts"] for c in covs.values() if c["since_ts"]], default=None),
                        "wallets": covs},
        }
        try:  # STRATEGY LIBRARY: rebuild this KOL's strategy, version it if it changed
            from core import strategy
            profile["strategy"] = strategy.update(name, k, all_pos, s, style, risk, sol_usd)
        except Exception as e:
            log("KOLS", f"strategy for {name} failed: {e}", "warn")
        set_status(name, "done", profile)
        run.result = f"{name}: {s.get('positions', 0)} positions, {', '.join(style)}"
        log("KOLS", f"profiled {name}: {', '.join(style)} | {', '.join(risk)} ({len(all_trades)} swaps via {src})")
        return profile
    set_status(name, "error")
    return None


def research_due(max_kols: int | None = None) -> int:
    """Refresh stale profiles (oldest first). Returns how many were refreshed."""
    ensure_table()
    hrs = _conf().get("refresh_h", 1)
    stale = []
    for k in list_kols():
        p = get_profile(k["name"]) or {}
        if p.get("status") == "running" and time.time() - (p.get("ts") or 0) < 1800:
            continue
        if p.get("status") != "done" or time.time() - (p.get("ts") or 0) > hrs * 3600:
            stale.append((p.get("ts") or 0, k["name"]))
    n = 0
    for _, name in sorted(stale)[: max_kols or len(stale)]:
        research(name)
        n += 1
    return n


def start_worker():
    """Background worker: keeps every KOL profile fresh (refresh_h, default hourly), one KOL at a time,
    independent of the coin scans."""
    def loop():
        time.sleep(20)  # let the first scan get going
        while True:
            try:
                research_due(max_kols=1)
            except Exception as e:
                log("KOLS", f"KOL worker: {e}", "warn")
            try:  # tracked devs: look for new launches from their wallets (hourly each)
                from core import devs
                devs.scan_due(1)
            except Exception as e:
                log("SCOUT", f"dev worker: {e}", "warn")
            time.sleep(30)
    t = _threading.Thread(target=loop, daemon=True, name="kol-worker")
    t.start()
    return t
