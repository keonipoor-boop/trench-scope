from signals.base import Signal


class Fees(Signal):
    """Real buyers pay fees (network + priority + Jito tips). Wash/botted volume shows up as
    huge volume with tiny fees relative to ATH and relative to the volume itself."""
    key, label = "fees", "FEES"

    def evaluate(self, coin):
        fees, ath, fvp = coin.get("fees_sol"), coin.get("ath_mcap") or 0, coin.get("fee_volume_pct")
        if (fees is None or not ath) and coin.get("organic_score") is not None:
            # no Helius fee sample: fall back to Jupiter's organic-volume score (also anti-wash)
            org, need = coin["organic_score"], self.conf.get("min_organic_score", 25)
            ovp = coin.get("organic_volume_pct")
            note = f"organic score {org:.0f}/100 (min {need})" + (f", {ovp:.1f}% organic volume" if ovp is not None else "")
            if org < need:
                note += " -> likely botted volume"
            return self.result(org >= need, org / (need * 2), round(org, 1), note + " [Jupiter; add Helius for fee data]")
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
