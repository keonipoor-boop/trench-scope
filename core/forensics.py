"""Fake-chart forensics, shared by the transaction sources and the chart-shape source.

Trade-level tells (from a sample of swaps):
  - low fees: bots keep priority fees + tips tiny compared with the volume they print
  - round-trip wallets: the same wallet buys AND sells (wash); per-wallet buy/sell symmetry
  - repeated identical trade sizes (fixed-size bot buys), many trades per trader
  - clockwork timing (low variance between trades)
Chart-level tells (from 1-minute candles):
  - "staircase": mostly green candles of near-equal size and volume, no real pullbacks,
    tiny wicks, then a cliff dump
Background: Bitquery "Solana volume numbers are a lie" (round-trip wallets, buy/sell symmetry,
trader count vs trade count); arXiv 2609.10246 "Meme Coin Factories" (many small trades to cut fees)."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict


def _cv(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = statistics.mean(xs)
    return statistics.pstdev(xs) / m if m else None


def _clamp(x):
    return max(0.0, min(1.0, x))


def trade_features(trades: list[dict]) -> dict:
    """trades: [{payer, side: 'buy'|'sell', sol, fee_sol, tip_sol, ts}] (any order)."""
    trades = [t for t in trades if t.get("payer") and t.get("side") in ("buy", "sell")]
    n = len(trades)
    if n < 10:
        return {"sample_txs": n}
    by = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "n": 0})
    for t in trades:
        w = by[t["payer"]]
        w[t["side"]] += t.get("sol") or 0
        w["n"] += 1
    wallets = len(by)
    rt = [w for w in by.values() if w["buy"] > 0 and w["sell"] > 0]
    sym = [w for w in rt if min(w["buy"], w["sell"]) / max(w["buy"], w["sell"]) >= 0.8]
    sizes = [round(t["sol"], 3) for t in trades if (t.get("sol") or 0) > 0]
    mode_share = 0.0
    if sizes:
        counts = defaultdict(int)
        for s in sizes:
            counts[s] += 1
        mode_share = max(counts.values()) / len(sizes)
    ts = sorted(t["ts"] for t in trades if t.get("ts"))
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b >= a]
    fees = sum((t.get("fee_sol") or 0) + (t.get("tip_sol") or 0) for t in trades)
    vol = sum(t.get("sol") or 0 for t in trades)
    return {
        "sample_txs": n, "sample_traders": wallets,
        "sample_fees_sol": round(fees, 5), "sample_volume_sol": round(vol, 3),
        "avg_fee_sol": fees / n,
        "fee_volume_pct": round(fees / vol * 100, 3) if vol else None,
        "round_trip_pct": round(len(rt) / wallets * 100, 1),
        "symmetric_wallet_pct": round(len(sym) / wallets * 100, 1),
        "size_mode_pct": round(mode_share * 100, 1),
        "size_cv": round(_cv(sizes), 2) if _cv(sizes) is not None else None,
        "txs_per_trader": round(n / wallets, 2),
        "interval_cv": round(_cv(gaps), 2) if _cv(gaps) is not None else None,
    }


def chart_features(rows: list[list[float]], supply: float = 1e9) -> dict:
    """rows: [[ts, open, high, low, close, volume_usd], ...] 1-5 minute candles."""
    rows = sorted([r for r in rows if r and r[4]], key=lambda r: r[0])
    if len(rows) < 8:
        return {}
    peak_i = max(range(len(rows)), key=lambda i: rows[i][4])
    rise = rows[: peak_i + 1]
    if len(rise) < 6:
        return {"chart_candles": len(rows)}
    green = [r for r in rise if r[4] > r[1]]
    rets = [math.log(r[4] / r[1]) for r in green if r[1] > 0]
    vols = [r[5] for r in rise if len(r) > 5]
    run_max, max_pb = 0.0, 0.0
    for r in rise:
        run_max = max(run_max, r[4])
        max_pb = max(max_pb, 1 - r[4] / run_max if run_max else 0)
    wick = [(r[2] - r[3]) / abs(r[4] - r[1]) for r in rise if abs(r[4] - r[1]) > 0]
    wick_ratio = statistics.median(wick) if wick else 5.0
    peak = rows[peak_i][4]
    after = rows[peak_i + 1: peak_i + 4]
    cliff = bool(after) and min(r[4] for r in after) / peak <= 0.35
    green_ratio = len(green) / len(rise)
    cv_ret, cv_vol = _cv(rets), _cv(vols)
    parts = [
        _clamp((green_ratio - 0.55) / 0.35),                        # mostly green steps
        _clamp((1.2 - cv_ret) / 0.8) if cv_ret is not None else 0.5,  # equal-size steps
        _clamp((1.0 - cv_vol) / 0.7) if cv_vol is not None else 0.5,  # equal volume per candle
        _clamp((0.25 - max_pb) / 0.2),                               # no real pullbacks
        _clamp((3.0 - wick_ratio) / 2.0),                            # clean candles, tiny wicks
    ]
    return {
        "chart_candles": len(rows), "rise_candles": len(rise),
        "green_ratio": round(green_ratio, 2), "step_cv": round(cv_ret, 2) if cv_ret is not None else None,
        "volume_cv": round(cv_vol, 2) if cv_vol is not None else None,
        "max_pullback_pct": round(max_pb * 100, 1), "wick_ratio": round(wick_ratio, 2),
        "cliff_dump": cliff, "staircase_score": round(sum(parts) / len(parts), 2),
    }


def to_update(coin: dict, feats: dict) -> dict:
    """Sample features -> coin fields. Lifetime fees are estimated as avg fee per trade x trade count."""
    upd = {k: v for k, v in feats.items() if k != "avg_fee_sol" and v is not None}
    n = feats.get("sample_txs") or 0
    if feats.get("avg_fee_sol"):
        total_tx = max(coin.get("txns_24h") or 0, n)
        upd["fees_sol"] = round(feats["avg_fee_sol"] * total_tx, 3)
    if not coin.get("unique_traders") and feats.get("sample_traders") and n:
        upd["unique_traders"] = int(feats["sample_traders"] / n * max(coin.get("txns_24h") or 0, n))
    return upd
