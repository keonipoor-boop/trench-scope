from signals.base import Signal


class Fees(Signal):
    """Real buyers pay fees (network + priority + Jito tips). Wash/botted volume shows up as
    huge volume with tiny fees relative to ATH and relative to the volume itself."""
    key, label = "fees", "FEES"

    def evaluate(self, coin):
        fees, ath, fvp = coin.get("fees_sol"), coin.get("ath_mcap") or 0, coin.get("fee_volume_pct")
        if fees is None or not ath:
            return self.missing()
        per100k = fees / (ath / 100_000)
        need_a, need_b = self.conf.get("min_sol_per_100k_ath", 2.0), self.conf.get("min_fee_volume_pct", 0.25)
        ok_a = per100k >= need_a
        ok_b = fvp is None or fvp >= need_b
        score = 0.5 * min(1, per100k / (need_a * 2)) + 0.5 * (min(1, (fvp or need_b) / (need_b * 3)))
        note = f"{per100k:.2f} SOL fees per $100k ATH (min {need_a})"
        if fvp is not None:
            note += f", fee/vol {fvp:.2f}% (min {need_b}%)"
        if not (ok_a and ok_b):
            note += " -> likely wash/botted volume"
        return self.result(ok_a and ok_b, score, round(per100k, 2), note)
