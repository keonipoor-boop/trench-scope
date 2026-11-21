"""Free fallback for the fee + wash forensics when there's no Helius key: samples recent swaps
from a public Solana RPC (getSignaturesForAddress + batched getTransaction).
Public RPCs are rate limited, so this only samples the top few runners per scan."""
from __future__ import annotations

from core.config import env
from core.forensics import to_update, trade_features
from sources.base import Source
from sources.helius import LAMPORTS, PUMP_FEE_ACCOUNTS, TOKEN_ACCOUNT_RENT


def parse_tx(tx: dict, mint: str, pool: str | None = None) -> dict | None:
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return None
    meta, msg = tx["meta"], (tx.get("transaction") or {}).get("message") or {}
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in msg.get("accountKeys") or []]
    loaded = meta.get("loadedAddresses") or {}
    keys += (loaded.get("writable") or []) + (loaded.get("readonly") or [])
    if not keys:
        return None
    payer = keys[0]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    fee = (meta.get("fee") or 0) / LAMPORTS
    # tips + trading-terminal fees: SOL landing in existing plain accounts other than the pool,
    # token accounts and pump's protocol fee wallets (Axiom-style "fees paid")
    token_idx = {b.get("accountIndex") for b in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or [])}
    tip = 0
    for i, k in enumerate(keys[:min(len(pre), len(post))]):
        d = post[i] - pre[i]
        if i == 0 or d <= 0 or pre[i] == 0 or i in token_idx or k == pool or k in PUMP_FEE_ACCOUNTS \
                or d == TOKEN_ACCOUNT_RENT:
            continue
        tip += d
    tip /= LAMPORTS
    change = ((post[0] - pre[0]) / LAMPORTS) if pre and post else 0

    def bal(lst):
        return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in lst or []
                   if b.get("mint") == mint and b.get("owner") == payer)
    delta = bal(meta.get("postTokenBalances")) - bal(meta.get("preTokenBalances"))
    if not delta:
        return None
    side = "buy" if delta > 0 else "sell"
    sol = (-change - fee - tip) if side == "buy" else (change + fee + tip)
    return {"payer": payer, "side": side, "sol": max(0.0, sol), "fee_sol": fee, "tip_sol": tip,
            "ts": tx.get("blockTime")}


class SolanaRpcTxs(Source):
    name = "solana_rpc"
    agent = "FEES"
    stage = "expensive"
    order = 21

    def enabled(self) -> bool:
        return super().enabled() and not env("HELIUS_API_KEY")  # Helius does this better when present

    def rpc(self, body):
        return self.http_post(self.conf.get("rpc_url", "https://api.mainnet-beta.solana.com"), json=body,
                              headers={"Content-Type": "application/json"})

    def enrich(self, coins):
        out, budget = {}, self.conf.get("coins_per_scan", 15)
        n = self.conf.get("sample_size", 40)
        for c in sorted(coins, key=lambda c: (bool(c.get("sample_txs")), -(c.get("ath_mcap") or 0))):
            if budget <= 0:
                break
            pool = c.get("pair_address") or c.get("pool_address") or c.get("bonding_curve")
            if not pool:
                continue
            budget -= 1
            try:
                sigs = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                 "params": [pool, {"limit": n}]}).get("result") or []
                batch = [{"jsonrpc": "2.0", "id": i, "method": "getTransaction",
                          "params": [s["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "json"}]}
                         for i, s in enumerate(sigs) if not s.get("err")]
                trades = []
                for i in range(0, len(batch), self.conf.get("batch", 20)):
                    res = self.rpc(batch[i:i + self.conf.get("batch", 20)])
                    for r in res if isinstance(res, list) else []:
                        t = parse_tx(r.get("result"), c["mint"], pool)
                        if t:
                            trades.append(t)
            except Exception:
                continue
            fc = self.cfg.get("signals", {}).get("fees", {})
            feats = trade_features(trades, fc.get("min_trade_sol", 0.1), fc.get("min_fee_volume_pct", 0.3))
            if feats.get("sample_txs", 0) >= 10:
                out[c["mint"]] = to_update(c, feats)
        return out

    def doctor(self):
        try:
            r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
            return [("public RPC getHealth", r.get("result") == "ok", str(r.get("result") or r.get("error"))[:80])]
        except Exception as e:
            return [("public RPC", False, str(e)[:120])]
