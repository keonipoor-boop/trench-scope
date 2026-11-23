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


def ensure_table():
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


def _helius_trades(wallet: str) -> list[dict]:
    from sources.helius import api_url
    out, before = [], None
    for _ in range(_conf().get("helius_pages", 5)):
        q = f"/addresses/{wallet}/transactions?type=SWAP&limit=100" + (f"&before={before}" if before else "")
        txs = http.get(api_url(q), rate_key="helius_kol", per_min=120, retries=2)
        if not isinstance(txs, list) or not txs:
            break
        for t in txs:
            tr = _parse_enhanced(t, wallet)
            if tr:
                out.append(tr)
        before = txs[-1].get("signature")
        if len(txs) < 100:
            break
    return out


def _parse_enhanced(t: dict, wallet: str) -> dict | None:
    if t.get("transactionError"):
        return None
    deltas = defaultdict(float)
    for tt in t.get("tokenTransfers") or []:
        amt = float(tt.get("tokenAmount") or 0)
        if tt.get("toUserAccount") == wallet:
            deltas[tt.get("mint")] += amt
        if tt.get("fromUserAccount") == wallet:
            deltas[tt.get("mint")] -= amt
    sol = next((a.get("nativeBalanceChange") or 0 for a in t.get("accountData") or [] if a.get("account") == wallet), 0) / LAMPORTS
    return _to_trade(deltas, sol, t.get("timestamp"), t.get("signature"))


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


def fetch_trades(wallet: str) -> tuple[list[dict], str]:
    cfg = get_config()
    if cfg.demo:
        return _demo_trades(wallet), "demo"
    try:
        if env("HELIUS_API_KEY"):
            tr, src = _helius_trades(wallet), "helius"
        else:
            tr, src = _rpc_trades(wallet), "public-rpc"
        source_result("kol_research", "KOLS", True, note=f"{src}: {len(tr)} swaps for {wallet[:6]}")
        return tr, src
    except Exception as e:
        source_result("kol_research", "KOLS", False, error=str(e))
        raise


# ---------------------------------------------------------------- token info
def token_info(mints: list[str]) -> tuple[dict, float]:
    """Jupiter batch lookup -> {mint: info}, SOL price in USD."""
    info, sol_usd = {}, 0.0
    mints = list(dict.fromkeys([WSOL] + mints))
    if get_config().demo:
        for c in get_db().coins("1=1", limit=20000):
            if c["mint"] in mints:
                info[c["mint"]] = {"symbol": c.get("symbol"), "name": c.get("name"), "launchpad": "pump.fun",
                                   "created_ts": c.get("created_ts"), "mcap": c.get("usd_market_cap") or 0,
                                   "usd_price": (c.get("usd_market_cap") or 0) / 1e9, "supply": 1e9,
                                   "holders": c.get("holders"), "ath": c.get("ath_mcap")}
        return info, 150.0
    from sources.jupiter import _iso
    base = get_config().get_path("sources.jupiter.base_url", "https://lite-api.jup.ag")
    for i in range(0, len(mints), 100):
        try:
            rows = http.get(f"{base}/tokens/v2/search?query={','.join(mints[i:i + 100])}", rate_key="jupiter", per_min=50)
        except Exception:
            continue
        for t in rows if isinstance(rows, list) else []:
            if t.get("id") == WSOL:
                sol_usd = float(t.get("usdPrice") or 0)
                continue
            info[t["id"]] = {"symbol": t.get("symbol"), "name": t.get("name"), "launchpad": t.get("launchpad"),
                             "created_ts": _iso((t.get("firstPool") or {}).get("createdAt")) or _iso(t.get("createdAt")),
                             "mcap": t.get("mcap") or 0, "usd_price": float(t.get("usdPrice") or 0),
                             "supply": float(t.get("circSupply") or t.get("totalSupply") or 1e9),
                             "holders": t.get("holderCount"), "twitter": t.get("twitter")}
    return info, sol_usd or 150.0


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


def positions(trades: list[dict], info: dict, sol_usd: float) -> list[dict]:
    by = defaultdict(list)
    for t in trades:
        by[t["mint"]].append(t)
    now = time.time()
    out = []
    for mint, ts in by.items():
        ts.sort(key=lambda t: t.get("ts") or 0)
        buys = [t for t in ts if t["side"] == "buy"]
        sells = [t for t in ts if t["side"] == "sell"]
        if not buys:
            continue  # sold something bought before our window
        sol_of = lambda t: t["sol"] if t["sol"] > 1e-6 else (t["usd"] / sol_usd if sol_usd else 0)  # noqa: E731
        inv = sum(sol_of(t) for t in buys)
        ret = sum(sol_of(t) for t in sells)
        bought = sum(t["tokens"] for t in buys)
        sold = min(bought, sum(t["tokens"] for t in sells))
        left = max(0.0, bought - sold)
        ti = info.get(mint, {})
        unreal = left * ti.get("usd_price", 0) / sol_usd if sol_usd else 0
        closed = left <= bought * 0.05
        first = buys[0]
        entry_price_usd = (sol_of(first) * sol_usd / first["tokens"]) if first["tokens"] else 0
        entry_mcap = entry_price_usd * ti.get("supply", 1e9)
        age = (first["ts"] - ti["created_ts"]) if ti.get("created_ts") and first.get("ts") else None
        end = sells[-1]["ts"] if closed and sells else now
        pnl = ret + (0 if closed else unreal) - inv
        sym = ti.get("symbol") or mint[:6]
        out.append({
            "mint": mint, "symbol": sym, "name": ti.get("name"), "launchpad": ti.get("launchpad"),
            "themes": narratives.tag({"name": ti.get("name"), "symbol": sym}),
            "first_buy_ts": first.get("ts"), "invested_sol": round(inv, 3), "returned_sol": round(ret, 3),
            "unrealized_sol": round(unreal, 3), "pnl_sol": round(pnl, 3),
            "multiple": round((ret + (0 if closed else unreal)) / inv, 2) if inv else None,
            "closed": closed, "buys": len(buys), "sells": len(sells),
            "hold_s": int(end - first["ts"]) if first.get("ts") else None,
            "entry_mcap": round(entry_mcap) if entry_mcap else None, "entry_age_s": int(age) if age is not None and age >= 0 else None,
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
def research(name: str) -> dict | None:
    k = next((x for x in list_kols() if x["name"] == name), None)
    if not k:
        return None
    set_status(name, "running")
    with AgentRun("KOLS", f"researching {name}") as run:
        wallets = [w for w in [k.get("wallet")] + k["side_wallets"] if w]
        per_wallet, all_trades, src = {}, [], None
        for w in wallets:
            try:
                tr, src = fetch_trades(w)
            except Exception as e:
                log("KOLS", f"{name}: {w[:6]}… fetch failed: {e}", "warn")
                tr = []
            for t in tr:
                t["wallet"] = w
            per_wallet[w] = {"trades": tr}
            all_trades += tr
        info, sol_usd = token_info(list({t["mint"] for t in all_trades}))
        for w, d in per_wallet.items():
            d["positions"] = positions(d["trades"], info, sol_usd)
            d["stats"] = stats(d["positions"], d["trades"])
        all_pos = positions(all_trades, info, sol_usd)
        s = stats(all_pos, all_trades)
        style, risk = archetype(s)
        sides = side_wallet_insights(per_wallet, k.get("wallet"))
        text, by = describe(k, s, style, risk, sides)
        recent = sorted(all_pos, key=lambda p: -(p.get("first_buy_ts") or 0))[:15]
        profile = {
            "name": name, "twitter": k.get("twitter"), "x": x_profile(handle_of(k)),
            "wallets": [{"address": w, "role": "main" if w == k.get("wallet") else "side",
                         "stats": {x: d["stats"].get(x) for x in ("trades", "positions", "win_rate", "median_size_sol",
                                                                   "median_hold_s", "total_pnl_sol")}}
                        for w, d in per_wallet.items()],
            "stats": s, "style": style, "risk": risk, "side_insights": sides,
            "description": text, "description_by": by, "data_source": src, "sol_usd": sol_usd,
            "recent": recent,
        }
        set_status(name, "done", profile)
        run.result = f"{name}: {s.get('positions', 0)} positions, {', '.join(style)}"
        log("KOLS", f"profiled {name}: {', '.join(style)} | {', '.join(risk)} ({len(all_trades)} swaps via {src})")
        return profile
    set_status(name, "error")
    return None


def research_due():
    """Called by the scheduler: refresh stale profiles."""
    ensure_table()
    hrs = _conf().get("refresh_h", 6)
    for k in list_kols():
        p = get_profile(k["name"])
        if not p or p.get("status") != "done" or time.time() - (p.get("ts") or 0) > hrs * 3600:
            research(k["name"])
