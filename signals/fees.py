from signals.base import Signal


def total_fees_sol(coin: dict) -> float | None:
    """Total fees traders paid (priority + tips + terminal fees), like Axiom's "F": the sampled fee rate times
    the coin's whole traded volume. Falls back to avg fee per trade x trade count."""
    from core.db import get_db
    fvp, vol = coin.get("fee_volume_pct"), coin.get("volume_24h") or coin.get("volume_24h_jup")
    if fvp is not None and vol:
        sol_usd = get_db().kv_get("sol_usd") or 150
        return fvp / 100 * vol / sol_usd
    return coin.get("fees_sol")


class Fees(Signal):
    """Real buyers pay to trade: priority fees, Jito tips and terminal fees (Axiom/Photon/GMGN ~1%).
    Bots that paint staircase charts call the program directly and pay almost nothing, so lots of
    volume with very little fee behind it is the tell (the "F" number on Axiom)."""
    key, label = "fees", "FEES"

    def evaluate(self, coin):
        c = self.conf
        share, fvp = coin.get("fee_paying_volume_pct"), coin.get("fee_volume_pct")
        # fees vs CURRENT market cap (not ATH): a coin dumping from its peak has a smaller mcap and volume while the
        # fees already paid stay, so it looks better, not worse; a fake keeps a big mcap with tiny fees
        mcap = coin.get("usd_market_cap") or coin.get("ath_mcap") or 0
        total = total_fees_sol(coin)
        per = total / (mcap / 100_000) if total is not None and mcap else None
        need_per = c.get("min_sol_per_100k_mcap", 6)
        per100k = f", ≈{total:.1f} SOL total fees = {per:.1f} SOL per $100k of market cap (min {need_per})" if per is not None else ""
        if share is not None:
            need_share, need_fvp = c.get("min_fee_paying_volume_pct", 40), c.get("min_fee_volume_pct", 0.3)
            ok = share >= need_share and (fvp is None or fvp >= need_fvp) and (per is None or per >= need_per)
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
