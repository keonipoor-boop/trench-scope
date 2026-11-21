"""Demo source: deterministic, realistic fake trenches so the whole app runs with no keys.

Coins are generated per 10-minute bucket from a seeded RNG, so re-running a scan returns
the same coins and new buckets produce new launches. Themes rotate: one theme is "hot" for
the day, another spikes for a few hours (to trigger META SHIFT), and a non-keyword word
trends so emerging-word detection has something to find.
Only active when the app runs in demo mode (`python run.py demo` / TRENCH_MODE=demo)."""
from __future__ import annotations

import hashlib
import math
import random
import time

from core.events import source_result
from sources.base import Source

BUCKET = 600
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

VOCAB = {
    "AI / agents": (["Agent", "Neural", "GPT", "Sentient", "Swarm", "Terminal", "AGI", "Grok", "Cortex", "Synth"], ["AI", "Agent", "Bot", "Mind", "OS"]),
    "Political": (["Trump", "MAGA", "Vance", "Tariff", "Senate", "America", "Election", "President"], ["Coin", "2028", "Army", "Wins", "Rally"]),
    "Elon / celebrity": (["Elon", "Musk", "Kanye", "Drake", "MrBeast", "Saylor", "Rogan", "Taylor"], ["Dog", "Says", "Fren", "Meme", "Rocket"]),
    "Animals": (["Pepe", "Shiba", "Hippo", "Capybara", "Penguin", "Frog", "Duck", "Monkey", "Goat", "Squirrel"], ["Inu", "King", "Wif Hat", "Baby", "Lord"]),
    "Tech / utility": (["Zk", "Depin", "Launchpad", "Bridge", "Compute", "Privacy", "Protocol"], ["App", "Labs", "SDK", "Infra"]),
    "CTO": (["Revival", "Reborn", "Takeover", "CTO"], ["Pepe", "Doge", "Cat", "Frog"]),
    "Brainrot": (["Skibidi", "Rizz", "Sigma", "Gyatt", "Ohio", "Mewing", "Fanum", "Aura", "NPC"], ["Toilet", "Max", "Lord", "Mode", "Coin"]),
    "Asia meta": (["Lucky", "Dragon", "Panda", "CZ", "Binance", "Hanzi", "Wen"], ["Cat", "Coin", "888", "King", "Moon"]),
    "Culture / memes": (["Wojak", "Chad", "Based", "GM", "Moon", "Cult", "Fart"], ["Coin", "Club", "Season", "Lord"]),
    "Sports / events": (["NBA", "Superbowl", "F1", "UFC", "Olympics"], ["Coin", "Champ", "Goat"]),
}
THEMES = list(VOCAB)
RANDOM_WORDS = ["Bonk", "Zoomer", "Blorp", "Meep", "Glorb", "Wobble", "Snek", "Gloop", "Plonk", "Zorp"]
EMERGING = ["Lobster", "Cabal", "Tung", "Yapper", "Hamster", "Quantum", "Sloth", "Gooner", "Pengu", "Moodeng", "Nugget",
            "Wizard", "Toaster", "Gremlin", "Pickle", "Possum", "Mochi", "Kraken", "Burrito", "Yeti", "Noodle", "Otter", "Waffle", "Comet"]
CELEBS = ["elonmusk", "realdonaldtrump", "kanyewest", "cz_binance", "saylor", "mrbeast"]
RANDOM_HANDLES = ["degenwhale", "solanalegend", "memesniper", "pumpwatcher", "chainsleuth", "cryptofren"]
DEMO_KOLS = ["Ansem", "Cented", "Orangie", "Gake", "Cupsey", "Jijo", "Euris", "Frank", "Kreo", "Waddles"]


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def daily_hot(t):  # rotates every 36h
    return THEMES[int(t // (36 * 3600)) % len(THEMES)]


def hourly_spike(t):  # rotates every 4h, different stride
    return THEMES[(int(t // (4 * 3600)) * 3 + 2) % len(THEMES)]


def emerging_word(t):  # rotates every 6h
    return EMERGING[int(t // (6 * 3600)) % len(EMERGING)]


def heat(theme, t):
    h = 1.0 + 0.35 * math.sin(t / 20000 + THEMES.index(theme))
    if theme == daily_hot(t):
        h *= 3.2
    if theme == hourly_spike(t):
        h *= 4.0
    return h


class _DemoBase(Source):
    is_demo = True
    abstract = True

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.conf = self.cfg.source("demo")
        self.seed = self.conf.get("seed", 1337)


def gen_coin(seed, bucket, i) -> dict:
    """Full hidden 'truth' for a coin. Everything downstream derives from this."""
    r = _rng(seed, bucket, i)
    created = bucket * BUCKET + r.randint(0, BUCKET - 1)
    weights = [heat(th, created) for th in THEMES] + [2.0]
    theme = r.choices(THEMES + [None], weights=weights)[0]
    emerg = emerging_word(created)
    use_emerg = r.random() < 0.10
    if theme:
        pre, suf = VOCAB[theme]
        a, b = r.choice(pre), r.choice(suf)
        name = f"{b} {a}" if theme == "CTO" else f"{a} {b}"
    else:
        name = f"{r.choice(RANDOM_WORDS)} {r.choice(RANDOM_WORDS)}"
    if use_emerg:
        name = f"{emerg} {r.choice(['Coin', 'Gang', 'Club', 'Szn', 'Mania'])}"
    symbol = "".join(w[0] for w in name.split()).upper() + r.choice(["", "", str(r.randint(1, 9))])
    if len(symbol) < 3:
        symbol = name.replace(" ", "")[:5].upper()
    mint = "".join(r.choice(B58) for _ in range(40)) + "pump"

    mult = heat(theme, created) if theme else 0.8
    if use_emerg:
        mult *= 4.5
    p_run = min(0.35, 0.009 * mult ** 1.5)
    runner = r.random() < p_run
    if runner:
        ath = 100_000 * math.exp(abs(r.gauss(0.6, 1.1)))
        ttt = int(60 * math.exp(r.gauss(3.6, 0.9)))  # median ~37 min to 100k
        ath_after = ttt + int(60 * math.exp(r.gauss(4.5, 1.0)))
    else:
        ath = r.choice([r.uniform(3_000, 12_000)] * 3 + [r.uniform(12_000, 95_000)])
        ttt, ath_after = None, int(60 * r.uniform(5, 120))

    # catalyst flavour
    cat_w = {"tweet": 2, "celeb": 0.6, "community": 1.5, "cto": 0.3, "tech": 0.6, "livestream": 0.5, "news": 0.6, "meme": 3}
    if theme == "Elon / celebrity":
        cat_w.update(celeb=4, tweet=3)
    if theme == "Political":
        cat_w.update(news=3, tweet=3)
    if theme in ("AI / agents", "Tech / utility"):
        cat_w.update(tech=4)
    if theme == "CTO":
        cat_w.update(cto=8)
    if theme in ("Brainrot", "Culture / memes"):
        cat_w.update(livestream=2, meme=4)
    cat = r.choices(list(cat_w), weights=list(cat_w.values()))[0]
    twitter = website = None
    desc = r.choice(["the most memeable coin on sol", "just vibes", "community driven, no roadmap", "we are so early",
                     f"{name} to the moon", "send it"])
    tid = r.randint(10**18, 10**19)
    if cat == "tweet":
        twitter = f"https://x.com/{r.choice(RANDOM_HANDLES)}/status/{tid}"
    elif cat == "celeb":
        twitter = f"https://x.com/{r.choice(CELEBS)}/status/{tid}"
    elif cat == "community":
        twitter = f"https://x.com/i/communities/{tid}"
    elif cat == "cto":
        desc = "CTO. dev abandoned, community took over. new socials, new team, same coin."
        twitter = f"https://x.com/{symbol.lower()}_cto"
    elif cat == "tech":
        website = f"https://github.com/{symbol.lower()}-labs/{symbol.lower()}"
        desc = r.choice(["open-source AI agent that trades memecoins autonomously", "app live, try the beta",
                         "SDK + API for onchain agents"])
    elif cat == "news":
        desc = r.choice(["breaking: announced today", "as seen on the news tonight", "commemorating today's headline event"])
    else:
        twitter = f"https://x.com/{symbol.lower()}onsol"
    if use_emerg:
        desc = f"{emerg.lower()} season. {desc}"

    real = runner and r.random() < 0.68
    fake_mode = None if real or not runner else r.choice(["wash", "concentrated", "thin"])
    return dict(mint=mint, name=name, symbol=symbol[:10], description=desc, created_ts=created, theme=theme,
                runner=runner, ath=ath, ttt=ttt, ath_after=ath_after, cat=cat, twitter=twitter, website=website,
                livestream=cat == "livestream", real=real, fake=fake_mode, seed=r.random())


def coin_at(seed, bucket, i, now) -> dict:
    t = gen_coin(seed, bucket, i)
    age = max(0, now - t["created_ts"])
    r = _rng(seed, t["mint"], "live")
    # ATH grows toward its peak over time (coins can become runners while you watch)
    if t["runner"]:
        frac = min(1.0, age / max(1, t["ath_after"]))
        ath = 5_000 + (t["ath"] - 5_000) * (frac ** 0.7)
    else:
        ath = 3_000 + (t["ath"] - 3_000) * min(1.0, age / max(1, t["ath_after"]))
    ts_100k = t["created_ts"] + t["ttt"] if t["ttt"] and age >= t["ttt"] else None
    if ts_100k:
        ath = max(ath, 100_000)
    else:
        ath = min(ath, 99_000)
    mcap = ath * (r.uniform(0.15, 0.9) if age > t["ath_after"] else r.uniform(0.75, 1.0))
    return dict(t, ath_now=ath, ts_100k=ts_100k, mcap=mcap)


class DemoScout(_DemoBase):
    name = "demo"
    agent = "SCOUT"
    stage = "cheap"
    order = 1

    def _buckets(self, since, now):
        return range(int(since // BUCKET), int(now // BUCKET) + 1)

    def discover(self, since_ts):
        now = time.time()
        n = self.conf.get("launches_per_10min", 9)
        out = []
        for b in self._buckets(since_ts, now):
            k = max(1, int(_rng(self.seed, b, "n").gauss(n, n * 0.3)))
            for i in range(k):
                t = gen_coin(self.seed, b, i)
                if since_ts <= t["created_ts"] <= now:
                    out.append({"mint": t["mint"], "name": t["name"], "symbol": t["symbol"],
                                "description": t["description"], "created_ts": t["created_ts"],
                                "twitter": t["twitter"], "website": t["website"],
                                "demo_ref": [b, i], "demo": True})
        source_result("pump.fun (demo)", "SCOUT", True, latency_ms=_rng(now).uniform(80, 260))
        return out

    def enrich(self, coins):
        now = time.time()
        out = {}
        for c in coins:
            if not c.get("demo_ref"):
                continue
            s = coin_at(self.seed, *c["demo_ref"], now)
            r = _rng(self.seed, c["mint"], int(now // 600))
            out[c["mint"]] = {
                "usd_market_cap": round(s["mcap"]), "volume_24h": round(s["ath_now"] * r.uniform(0.8, 4)),
                "txns_24h": int(s["ath_now"] / 100_000 * r.uniform(1500, 6000)) + r.randint(20, 300),
                "pair_address": "".join(_rng(c["mint"]).choice(B58) for _ in range(44)),
                "dex_id": "pumpswap" if s["ath_now"] > 69_000 else "pumpfun",
                "complete": s["ath_now"] > 69_000,
                "ath_mcap": round(s["ath_now"]), "ts_100k": s["ts_100k"],
                "livestream": s["livestream"] or None,
                "price_change_24h": round(r.uniform(-60, 140), 1),
            }
        source_result("dexscreener (demo)", "SCOUT", True, latency_ms=_rng(now, 1).uniform(60, 200))
        source_result("geckoterminal (demo)", "SCOUT", True, latency_ms=_rng(now, 2).uniform(150, 480))
        return out

    def doctor(self):
        return [("demo generator", True, "deterministic fake trenches")]


class DemoDeep(_DemoBase):
    name = "demo_deep"
    agent = "HOLDERS"
    stage = "expensive"
    order = 1

    def enrich(self, coins):
        now = time.time()
        out = {}
        for c in coins:
            if not c.get("demo_ref"):
                continue
            s = coin_at(self.seed, *c["demo_ref"], now)
            r = _rng(self.seed, c["mint"], "deep")
            scale = s["ath_now"] / 100_000
            holders = int(scale * r.uniform(250, 700) + r.uniform(250, 500))
            traders = int(holders * r.uniform(0.9, 1.6))
            top10 = r.uniform(14, 30)
            fees_per = r.uniform(2.6, 9)
            fvp = r.uniform(0.4, 1.7)
            if s["fake"] == "wash":
                fees_per, fvp, traders = r.uniform(0.3, 1.6), r.uniform(0.03, 0.2), int(traders * 0.4)
            elif s["fake"] == "concentrated":
                top10 = r.uniform(42, 70)
            elif s["fake"] == "thin":
                holders, traders = r.randint(60, 260), r.randint(40, 140)
            kols = []
            if s["real"] and r.random() < 0.4 or (s["runner"] and r.random() < 0.15):
                launch = s["created_ts"]
                for name in r.sample(DEMO_KOLS, r.randint(1, 4)):
                    kols.append({"name": name, "twitter": f"https://x.com/{name.lower()}",
                                 "ts": launch + int(r.expovariate(1 / 2400))})
                kols.sort(key=lambda k: k["ts"])
            # fake-chart forensics (see core/forensics.py)
            botted = s["fake"] == "wash"
            forensic = {
                "round_trip_pct": round(r.uniform(65, 90) if botted else r.uniform(10, 45), 1),
                "symmetric_wallet_pct": round(r.uniform(40, 70) if botted else r.uniform(2, 20), 1),
                "size_mode_pct": round(r.uniform(40, 85) if botted else r.uniform(2, 18), 1),
                "txs_per_trader": round(r.uniform(9, 25) if botted else r.uniform(1.3, 4), 2),
                "interval_cv": round(r.uniform(0.05, 0.3) if botted else r.uniform(0.8, 3), 2),
                "staircase_score": round(r.uniform(0.68, 0.95) if botted else r.uniform(0.08, 0.45), 2),
                "green_ratio": round(r.uniform(0.88, 0.99) if botted else r.uniform(0.5, 0.7), 2),
                "max_pullback_pct": round(r.uniform(0, 6) if botted else r.uniform(18, 45), 1),
                "cliff_dump": botted or r.random() < 0.15,
                "fee_paying_volume_pct": round(r.uniform(2, 25) if botted else r.uniform(50, 92), 1),
                "sample_big_trades": r.randint(20, 120),
            }
            mentions = int(scale * r.uniform(5, 40))
            big = s["cat"] == "celeb" or r.random() < 0.15 * min(scale, 3)
            twl = s["twitter"] or ""
            author = twl.split("/")[3].lower() if "/status/" in twl else None
            out[c["mint"]] = {
                **forensic,
                "holders": holders, "top10_pct": round(top10, 1), "unique_traders": traders,
                "fees_sol": round(fees_per * scale, 2), "fee_volume_pct": round(fvp, 3),
                "sample_txs": 100, "sample_fees_sol": round(fvp / 100 * 40, 4), "sample_volume_sol": 40.0,
                "kols": [k for k in kols if k["ts"] <= now],
                "x_mentions": mentions, "x_big_account": big,
                "x_big_accounts": [author] if big and author else (["cryptowhale"] if big else []),
                "tweet_author": author,
                "x_top_tweets": [{"author": r.choice(RANDOM_HANDLES), "likes": int(scale * r.uniform(20, 900)),
                                  "text": f"${c.get('symbol')} is the play. {r.choice(['dont fade', 'chart is clean', 'cto is cooking', 'devs based'])}"}],
            }
        for nm, ag in (("helius das (demo)", "HOLDERS"), ("helius txs (demo)", "FEES"), ("kol wallets (demo)", "KOLS"), ("x api (demo)", "CATALYST")):
            source_result(nm, ag, True, latency_ms=_rng(now, nm).uniform(90, 600))
        return out

    def doctor(self):
        return [("demo deep enrichment", True, "holders/fees/kols/x")]
