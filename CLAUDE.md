# Trench Scope — architecture & extension guide

A bot that scans pump.fun, finds coins that ran (ATH ≥ $100k by default), explains why, and writes
daily + hourly narrative theses. Python 3.11+, FastAPI, SQLite, vanilla-JS single-page dashboard.

## Layout

```
run.py                 CLI: scan | backfill | thesis | serve | daemon | demo | doctor
config.json            ALL tunables. config.local.json is deep-merged on top (gitignored)
.env                   secrets (HELIUS_API_KEY, X_BEARER_TOKEN, ANTHROPIC_API_KEY)
core/
  config.py            load_config() / get_config(); cfg.get_path("a.b.c"); cfg.demo
  db.py                SQLite. coins = indexed columns + JSON `data` blob (new fields need no migration)
  http.py              shared httpx client: per-source rate limit, retry/backoff on 429/5xx
  registry.py          plugin auto-discovery (imports every module in a package)
  events.py            log() -> THE TRACE, agent_status() -> AGENT STRIP, source_result() -> SENSORY INPUT
  pipeline.py          the scan (discover -> cheap -> ohlcv -> runners -> deep -> signals -> catalysts)
  narratives.py        theme tagging + emerging-word detection
  thesis.py            window-vs-baseline stats, LOOK FOR / AVOID, META SHIFT, prose
  llm.py               optional Claude calls (why-it-ran bullets, thesis prose); None if no key
  scheduler.py         scan every N min, hourly thesis at :MM, daily at HH:00 local
  doctor.py            endpoint/key health check
  links.py             X/Twitter URL parsing (status / community / profile)
sources/   *.py        data source plugins (Source subclasses)
signals/   *.py        REAL-check plugins (Signal subclasses)
catalysts/ *.py        catalyst rule plugins (CatalystRule subclasses)
data/                  narratives.json, celebrities.json, kols.json, *.db
api/server.py          REST API + serves web/
web/                   index.html, style.css, app.js (panels), synapse.js (canvas)
```

## The coin dict

Everything flows through a plain dict keyed by `mint`. Sources return partial dicts; the pipeline
merges them (None never overwrites; `ath_mcap` only ever goes up). Common fields:

| field | set by |
|---|---|
| name, symbol, description, image, twitter, website, telegram, created_ts, complete, pf_ath_mcap, livestream, bonding_curve, pool_address | pumpfun |
| usd_market_cap, volume_24h, txns_24h, pair_address, dex_url, liquidity_usd | dexscreener |
| ath_mcap, ts_100k (→ time_to_100k_min) | geckoterminal (OHLCV) |
| holders, top10_pct | helius_holders |
| unique_traders, fees_sol, fee_volume_pct, sample_* | helius_txs |
| kols: [{name, twitter, ts}] | kols |
| x_mentions, x_top_tweets, x_big_account, tweet_author, tweet_author_followers | twitter |
| themes, is_runner, signals, is_real, primary_catalyst, catalysts, why, why_llm | pipeline |

## Pipeline stages and costs

1. **discover** — every source with `discover(since_ts)`.
2. **cheap** (`stage="cheap"`) — all window coins due for refresh (`pipeline.cheap_refresh_min`, 60 min for coins >24h old), capped by `max_cheap_per_scan`.
3. **ohlcv** (`stage="ohlcv"`) — only candidates (ATH ≥ `ath_prefilter_usd` or graduated), re-checked every `ohlcv_refresh_min`. GeckoTerminal is rate limited to `rate_per_min` (28).
4. **runners** — `ath_mcap ≥ runner_ath_usd`.
5. **expensive** (`stage="expensive"`) — runners only, at most every `deep_refresh_min` (30), capped by `max_deep_per_scan`.
6. **signals** → `is_real` (all `required` signals must pass; missing data = not real yet).
7. **catalysts** → highest-weight hit is primary; Claude writes `why_llm` (≤10 coins/scan) if a key is set, otherwise `why_template`.

Each stage is wrapped in `AgentRun(...)`, so a failure is logged to THE TRACE, marks the agent red, and
the scan continues.

## How to add things

### A data source
Create `sources/my_source.py`:
```python
from sources.base import Source

class MySource(Source):
    name = "my_source"        # also the config key: config.json -> sources.my_source
    agent = "SCOUT"           # SCOUT | HOLDERS | KOLS | FEES | CATALYST | THESIS (whose ring lights up)
    stage = "expensive"       # cheap | ohlcv | expensive
    order = 50                # run order within the stage
    requires_env = ["MY_KEY"] # source auto-disables if missing; doctor reports it

    def discover(self, since_ts):            # optional
        return [{"mint": "...", "name": "...", "created_ts": 1700000000}]

    def enrich(self, coins):                  # optional
        out = {}
        for c in coins:
            data = self.http_get(f"https://api.example.com/x/{c['mint']}")  # rate-limited + tracked
            out[c["mint"]] = {"my_field": data["value"]}
        return out

    def doctor(self):
        return [("example endpoint", True, "ok")]
```
Add `"my_source": {"enabled": true, "rate_per_min": 60}` under `sources` in config.json. Always use
`self.http_get/http_post` so calls show up in SENSORY INPUT and respect rate limits. Never raise out of
`enrich` for a single bad coin; skip it.

### A signal
Create `signals/my_signal.py`:
```python
from signals.base import Signal

class MySignal(Signal):
    key, label = "my_signal", "MINE"   # config: signals.my_signal

    def evaluate(self, coin):
        v = coin.get("my_field")
        if v is None:
            return self.missing()
        mn = self.conf.get("min", 10)
        return self.result(v >= mn, v / (mn * 2), v, f"{v} vs min {mn}")
```
Add `"my_signal": {"enabled": true, "required": false, "min": 10}` to `signals` in config.json.
`required: true` makes it part of the REAL gate. `score` (0-1) is the bar length in the COIN FILE.

### A catalyst rule
Create `catalysts/my_rule.py`:
```python
from catalysts.base import CatalystRule, has_word, text_of

class Airdrop(CatalystRule):
    category = "airdrop"
    def check(self, coin, ctx):        # ctx = {"cfg": cfg, "celebrities": set(...)}
        if has_word(text_of(coin), "airdrop"):
            return self.category, 62, "Airdrop hype pulled in farmers"
```
Weights in use: celebrity 85-95, tech/github 75, CTO 70, KOL call 65-85 (early) / 30 (late), tweet 60,
news 58, livestream 55, tech pitch 50, community 45, meme 10 (fallback). Add a display label in
`CATEGORY_LABELS` (catalysts/base.py) and `CAT` (web/app.js).

### A theme
Edit `data/narratives.json`: `"Theme name": ["keyword", "multi word keyword", ...]`. Matching is
case-insensitive with word boundaries on name + symbol + description. Coins are re-tagged on every
cheap refresh. Words that spike in runners but belong to no theme surface as "emerging" (magenta chips)
— that's your cue to add a new theme.

### A KOL / celebrity
- `data/kols.json`: append `{"name": "...", "wallet": "<solana address>", "twitter": "https://x.com/..."}`.
  Needs `HELIUS_API_KEY`. Each wallet's last `sources.kols.tx_limit` swaps are pulled once per scan.
- `data/celebrities.json`: lowercase X handles. A coin whose twitter link is a status by one of these → CELEBRITY TWEET.

### A dashboard panel
Add a `<section class="panel p-cyan" id="x">` in web/index.html, a grid-area in style.css, and a
`renderX()` in app.js called from `refresh()`. Data comes from `/api/state`; add fields in
`api/server.py:state()`.

## Shipping (cloud + desktop)
- `python run.py` with no args = `start` (scheduler + dashboard). It binds `0.0.0.0:$PORT` when `PORT` is set.
- **Cloud:** `render.yaml` (Render button, auto-deploys on push) and a `Dockerfile` (Railway, Fly, any VPS; data in `/data`).
- **Desktop:** `launcher.py` is the app entry point. `build_desktop.py` runs PyInstaller, and
  `.github/workflows/desktop.yml` builds Windows/macOS/Linux and publishes a GitHub Release on every push to main.
  `version.json` is stamped at build time. `core/updates.py` compares it with the latest release and drives the update banner.
- **Writable state** (db, `settings.json`, your copies of data/*.json, `config.local.json`) lives in `TRENCH_DATA_DIR`
  (desktop: the OS user-data folder; default: `./data`). Bundled defaults in `ROOT/data` are the fallback.
- **Settings API** (`/api/settings`): saving is allowed from localhost, or with header `x-admin-password` when `ADMIN_PASSWORD`
  is set (hosted). Changing the mode re-execs the process.
- New plugin modules are picked up by the desktop build automatically (`--collect-submodules`).

## Conventions
- All thresholds in config.json; read via `self.conf` (plugins) or `get_config().get_path(...)`.
- Demo mode (`--demo` / `TRENCH_MODE=demo`) uses only `is_demo=True` sources and `data/trench_demo.db`.
- Log with `core.events.log(AGENT, msg, level)`; it feeds THE TRACE and stdout.
- Escape every external string in the frontend with `esc()` (coin names are attacker-controlled).
- Quick check after changes: `python run.py scan --demo && python run.py thesis daily --demo`.
