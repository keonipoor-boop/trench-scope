"""Helius: holders + top-10 concentration (DAS getTokenAccounts) and an enhanced-tx
sample for unique traders, fees paid (network + priority + Jito tips) and fee/volume."""
from __future__ import annotations

from collections import defaultdict

from core.config import env
from sources.base import Source

WSOL = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
DECIMALS = 6  # pump.fun tokens
# Program-owned accounts that are never "holders"
KNOWN_POOL_OWNERS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4 authority
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM authority
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump fee/migration account
}
JITO_TIP_ACCOUNTS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}


def rpc_url():
    return f"https://mainnet.helius-rpc.com/?api-key={env('HELIUS_API_KEY')}"


def api_url(path):
    return f"https://api.helius.xyz/v0{path}{'&' if '?' in path else '?'}api-key={env('HELIUS_API_KEY')}"


class HeliusHolders(Source):
    name = "helius_holders"
    agent = "HOLDERS"
    stage = "expensive"
    order = 10
    requires_env = ["HELIUS_API_KEY"]

    def token_accounts(self, mint, page):
        body = {"jsonrpc": "2.0", "id": "ts", "method": "getTokenAccounts",
                "params": {"mint": mint, "page": page, "limit": 1000}}
        r = self.http_post(rpc_url(), json=body)
        if isinstance(r, dict) and r.get("error"):
            raise RuntimeError(r["error"])
        return ((r or {}).get("result") or {}).get("token_accounts") or []

    def enrich(self, coins):
        out = {}
        supply_raw = self.cfg.get_path("pipeline.total_supply", 1e9) * 10 ** DECIMALS
        for c in coins:
            exclude = KNOWN_POOL_OWNERS | set(self.conf.get("exclude_owners", []))
            exclude |= {x for x in (c.get("bonding_curve"), c.get("pool_address"), c.get("pair_address")) if x}
            exclude_accounts = {x for x in (c.get("assoc_bonding_curve"),) if x}
            owners: dict[str, float] = defaultdict(float)
            capped = False
            try:
                for page in range(1, self.conf.get("max_pages", 10) + 1):
                    accts = self.token_accounts(c["mint"], page)
                    for a in accts:
                        if a.get("owner") in exclude or a.get("address") in exclude_accounts:
                            continue
                        owners[a["owner"]] += float(a.get("amount") or 0)
                    if len(accts) < 1000:
                        break
                else:
                    capped = True
            except Exception:
                continue
            bal = sorted((v for v in owners.values() if v > 0), reverse=True)
            out[c["mint"]] = {
                "holders": len(bal), "holders_capped": capped,
                "top10_pct": round(sum(bal[:10]) / supply_raw * 100, 2) if bal else None,
            }
        return out

    def doctor(self):
        if self.missing_env():
            return [("HELIUS_API_KEY", False, "missing in .env")]
        try:
            r = self.http_post(rpc_url(), json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
            ok = (r or {}).get("result") == "ok"
            accts = self.token_accounts("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1)
            return [("rpc getHealth", ok, str((r or {}).get("result"))),
                    ("DAS getTokenAccounts", bool(accts), f"{len(accts)} accounts (USDC sample)")]
        except Exception as e:
            return [("helius rpc", False, str(e)[:120])]


class HeliusTxs(Source):
    name = "helius_txs"
    agent = "FEES"
    stage = "expensive"
    order = 20
    requires_env = ["HELIUS_API_KEY"]

    def txs(self, address, limit):
        r = self.http_get(api_url(f"/addresses/{address}/transactions?limit={min(limit, 100)}"))
        return r if isinstance(r, list) else []

    @staticmethod
    def analyze(txs: list[dict], pool: str, mint: str) -> dict:
        traders, net_fee, tips, vol_sol, n = set(), 0, 0, 0.0, 0
        for t in txs:
            if t.get("transactionError"):
                continue
            touches = any(tt.get("mint") == mint for tt in t.get("tokenTransfers") or [])
            if not touches:
                continue
            n += 1
            if t.get("feePayer"):
                traders.add(t["feePayer"])
            net_fee += t.get("fee") or 0  # base + priority, lamports
            for nt in t.get("nativeTransfers") or []:
                if nt.get("toUserAccount") in JITO_TIP_ACCOUNTS:
                    tips += nt.get("amount") or 0
                if pool and pool in (nt.get("toUserAccount"), nt.get("fromUserAccount")):
                    vol_sol += (nt.get("amount") or 0) / LAMPORTS
            for tt in t.get("tokenTransfers") or []:
                if tt.get("mint") == WSOL and pool and pool in (tt.get("toUserAccount"), tt.get("fromUserAccount")):
                    vol_sol += float(tt.get("tokenAmount") or 0)
        fees = (net_fee + tips) / LAMPORTS
        return {"sample_txs": n, "sample_traders": len(traders), "sample_fees_sol": round(fees, 5),
                "sample_tips_sol": round(tips / LAMPORTS, 5), "sample_volume_sol": round(vol_sol, 3),
                "fee_volume_pct": round(fees / vol_sol * 100, 3) if vol_sol else None,
                "avg_fee_sol": fees / n if n else None}

    def enrich(self, coins):
        out = {}
        size = self.conf.get("sample_size", 100)
        for c in coins:
            pool = c.get("pair_address") or c.get("bonding_curve") or c.get("pool_address")
            try:
                txs = self.txs(pool or c["mint"], size)
            except Exception:
                continue
            a = self.analyze(txs, pool, c["mint"])
            total_tx = max(c.get("txns_24h") or 0, a["sample_txs"])
            upd = dict(a)
            if a["avg_fee_sol"]:
                upd["fees_sol"] = round(a["avg_fee_sol"] * total_tx, 3)  # lifetime estimate
            # unique traders: extrapolate sample ratio onto total txns
            if a["sample_txs"]:
                upd["unique_traders"] = int(a["sample_traders"] / a["sample_txs"] * total_tx)
            out[c["mint"]] = upd
        return out

    def doctor(self):
        if self.missing_env():
            return [("HELIUS_API_KEY", False, "missing in .env")]
        try:
            t = self.txs("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", 5)
            return [("enhanced transactions", bool(t), f"{len(t)} txs")]
        except Exception as e:
            return [("enhanced transactions", False, str(e)[:120])]
