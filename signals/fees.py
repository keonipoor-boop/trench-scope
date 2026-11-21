from signals.base import Signal


class Fees(Signal):
    """Real buyers pay to trade: priority fees, Jito tips and terminal fees (Axiom/Photon/GMGN ~1%).
    Bots that paint staircase charts call the program directly and pay almost nothing, so lots of
    volume with very little fee behind it is the tell (the "F" number on Axiom)."""
    key, label = "fees", "FEES"

    def evaluate(self, coin):
        c = self.conf
        share, fvp = coin.get("fee_paying_volume_pct"), coin.get("fee_volume_pct")
        fees, ath = coin.get("fees_sol"), coin.get("ath_mcap") or 0
        per100k = f", ~{fees / (ath / 100_000):.1f} SOL per $100k ATH" if fees and ath else ""
        if share is not None:
            need_share, need_fvp = c.get("min_fee_paying_volume_pct", 40), c.get("min_fee_volume_pct", 0.3)
            ok = share >= need_share and (fvp is None or fvp >= need_fvp)
            note = (f"{share:.0f}% of volume paid real fees (min {need_share}%), fees = {fvp:.2f}% of volume"
                    f" (min {need_fvp}%){per100k} [{coin.get('sample_big_trades')} trades >= 0.1 SOL]")
            if not ok:
                note += " -> volume without fees: bot-painted"
            return self.result(ok, 0.5 * min(1, share / (need_share * 1.5)) + 0.5 * min(1, (fvp or 0) / (need_fvp * 3)),
                               round(share, 1), note)
        if (coin.get("organic_score") or 0) > 0:  # 0 = Jupiter hasn't scored it yet
            org, need = coin["organic_score"], c.get("min_organic_score", 25)
            note = f"Jupiter organic score {org:.0f}/100 (min {need})" + (" -> likely botted volume" if org < need else "")
            return self.result(org >= need, org / (need * 2), round(org, 1),
                               note + " [no fee sample yet; a Helius key samples more trades]")
        return self.missing("not enough trades >= 0.1 SOL sampled yet (a Helius key samples deeper)")
