from signals.base import Signal


class Wash(Signal):
    """Wash / bot volume from a swap sample (Helius or public RPC): wallets that round-trip,
    fixed-size repeated trades, many trades per trader. Falls back to Jupiter's 24h volume per trader."""
    key, label = "wash", "WASH"

    def evaluate(self, coin):
        c = self.conf
        rt, mode, tpt = coin.get("round_trip_pct"), coin.get("size_mode_pct"), coin.get("txs_per_trader")
        if rt is not None:
            flags = []
            if rt > c.get("max_round_trip_pct", 60):
                flags.append(f"{rt:.0f}% of wallets buy AND sell")
            if (coin.get("symmetric_wallet_pct") or 0) > c.get("max_symmetric_pct", 35):
                flags.append(f"{coin['symmetric_wallet_pct']:.0f}% have matching buy/sell totals")
            if mode is not None and mode > c.get("max_same_size_pct", 35):
                flags.append(f"{mode:.0f}% of trades are the exact same size")
            if tpt is not None and tpt > c.get("max_trades_per_trader", 8):
                flags.append(f"{tpt:.1f} trades per wallet")
            if coin.get("interval_cv") is not None and coin["interval_cv"] < c.get("min_interval_cv", 0.35):
                flags.append("clockwork trade timing")
            score = 1 - min(1, len(flags) / 3)
            note = (", ".join(flags) + " -> bot/wash volume") if flags else \
                f"{rt:.0f}% round-trip wallets, {mode or 0:.0f}% same-size trades, {tpt or 0:.1f} trades/wallet"
            return self.result(not flags, score, round(rt, 1), note + f" [{coin.get('sample_txs')} trade sample]")
        vol, traders = coin.get("volume_24h") or coin.get("volume_24h_jup"), coin.get("unique_traders")
        if vol and traders:
            vpt, mx = vol / traders, c.get("max_volume_per_trader", 3000)
            return self.result(vpt <= mx, 1 - vpt / (mx * 2), round(vpt),
                               f"${vpt:,.0f} volume per trader (max ${mx:,}){' -> wash-like' if vpt > mx else ''}"
                               " [no trade sample yet]")
        return self.missing()
