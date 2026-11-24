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
| name, symbol, created_ts, metadata_uri, bonding_curve, complete, migrated_ts | pumpportal (live websocket) |
| symbol, pool_address, usd_market_cap, volume_24h, complete, created_ts (provisional) | gecko_discovery |
| description, twitter_dex, website, telegram, dex_boosts | dex_feeds |
| description, twitter, website, telegram, image | metadata (uri JSON, then GeckoTerminal info) |
| name, symbol, description, image, twitter, website, telegram, created_ts, complete, pf_ath_mcap, livestream, bonding_curve, pool_address | pumpfun (often Cloudflare-blocked on servers; auto-pauses) |
| usd_market_cap, volume_24h, txns_24h, pair_address, dex_url, liquidity_usd | dexscreener |
| ath_mcap, ts_100k (→ time_to_100k_min) | geckoterminal (OHLCV) |
| holders, top10_pct | helius_holders |
| unique_traders, fees_sol, fee_volume_pct, sample_*, round_trip_pct, symmetric_wallet_pct, size_mode_pct, txs_per_trader, interval_cv | helius_txs, or solana_rpc (free fallback) via core/forensics.trade_features |
| staircase_score, green_ratio, step_cv, volume_cv, max_pullback_pct, wick_ratio, cliff_dump | chart_shape (1m candles) via core/forensics.chart_features |
| holders, top10_pct, unique_traders, organic_score, organic_volume_pct | jupiter_tokens |
| kols: [{name, twitter, ts}] | kols |
| x_mentions, x_top_tweets, x_big_account, tweet_author, tweet_author_followers | twitter |
| themes, is_runner, signals, is_real, primary_catalyst, catalysts, why, why_llm | pipeline |

## Pipeline stages and costs

1. **discover** — every source with `discover(since_ts)`.
2. **cheap** (`stage="cheap"`) — with `pipeline.cheap_scope: "candidates"` (live default) only graduated coins,
   coins with ATH ≥ prefilter, and coins with unknown launch time; `"all"` (demo) = every coin. Due for refresh (`pipeline.cheap_refresh_min`, 60 min for coins >24h old), capped by `max_cheap_per_scan`.
3. **ohlcv** (`stage="ohlcv"`) — only candidates (ATH ≥ `ath_prefilter_usd` or graduated), re-checked every `ohlcv_refresh_min`. GeckoTerminal is rate limited to `rate_per_min` (28).
4. **runners** — `ath_mcap ≥ runner_ath_usd`.
5. **expensive** (`stage="expensive"`) — runners only, at most every `deep_refresh_min` (30), capped by `max_deep_per_scan`.
6. **signals** → `is_real` (all `required` signals must pass; missing data = not real yet, unless the
   signal sets `allow_missing: true`). Fake-chart checks: FEES (fees per $100k ATH, fee/volume, Jupiter organic
   score fallback), WASH (round-trip wallets, buy/sell symmetry, same-size trades, trades per wallet, clockwork
   timing; else volume per trader), CHART (staircase score + cliff dump). Logic lives in `core/forensics.py`.
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

### KOL LAB (research agent)
`core/kol_research.py`. KOLs live in `kols.json` (data dir) as `{name, twitter, wallet, side_wallets: [...]}`;
the dashboard's KOL LAB edits them via `/api/kols`. For each KOL it pulls swaps for every wallet (Helius enhanced
txs, else a small public-RPC sample; demo mode synthesizes), rebuilds positions, enriches tokens via Jupiter, then
computes stats -> `archetype()` labels (style + risk) -> `describe()` (Claude if keyed, else template) and
`side_wallet_insights()` (does a side wallet buy before main?). Profiles are stored in the `kol_profiles` table and
refreshed every `kol_research.refresh_h` (1h) by a background worker (`start_worker`, one KOL at a time,
independent of scans); open positions become `holdings` (the planet moons); `pnl_periods()` gives lifetime/month/
week/day PnL and `pnl_series` feeds the profile's PnL chart and the KOL LAB's PNL period bar. Add a label: edit `archetype()`. Add a stat: `stats()`
plus a cell in `openKol()` (web/app.js). Profile pictures are served by `/api/pfp/<handle>` (server fetches + caches in the data dir:
X API photo when `X_BEARER_TOKEN` is set, else unavatar.io; misses retried hourly). UI: a solar system
(web/app.js `loadKols`/`spaceFrame`/`zoomTo`): KOLs orbit the core on 3 rings, ring colour = style
(`STYLE_COLORS`), hover pauses the orbit, click zooms and opens the profile, Esc/✕ zooms out.

### KOL trade history + PnL
Every wallet's swaps are stored in `kol_trades` (sync state in `kol_sync`). With Helius, each research run fetches
new swaps (`forward_pages`) and backfills older ones (`backfill_pages_per_run`, ~3,000 swaps/wallet/hour, following
Helius' "continue with before=<sig>" replies) until the first swap; then `history.complete` is true and lifetime
is real lifetime. `pnl_periods()`: realized = sells inside the period vs average cost; unrealized = held bags;
`pnl_series` = realized PnL bucketed hourly (last 30d) / daily. Without Helius the public RPC adds a small sample per run (marked incomplete).
`positions()` is a running average-cost ledger (like Axiom/kolscan): each sell realizes proceeds - avg cost x tokens sold;
token transfers are stored as `xfer_in`/`xfer_out` with a `peer` (own-wallet moves ignored, others leave at cost / arrive free);
sells whose buys predate the loaded history are counted in `unpriced`, never guessed. The UI headline is REALIZED PnL
("(SO FAR)" until `history.complete`). Token facts are cached in `tok_info`; prices refresh only for recently traded / held mints.
Bump `PARSER_VERSION` when parsing changes: every wallet re-syncs once.

### STRATEGY LIBRARY
`core/strategy.py`, run at the end of every KOL research. `build_spec()` = entry, exit (`exit_model()`: take-profit
ladder, first sell, initials out, runner bag, stop-loss, time stop), risk, rules, confidence. `update()` diffs against
the latest version (`TRACKED` thresholds) and writes a new version + changelog only on a meaningful change; it also
flags a 7-day "shift". `examples()` adds real coins from the wallet (typical entries, best, worst) shown as EXAMPLE
TRADES cards (`exampleShelf` in app.js; refreshed every hourly check). Stored in `kol_strategies`; served by `/api/strategies[/<name>]`; UI is the KOL LAB's
STRATEGY LIBRARY tab (`loadLibrary`/`openCodex` in app.js).

### Hall of Runners + tape tabs
- `/api/leaders?period=hour|day|week|month&real_only=` -> runner of the period (by peak mcap) overall + per narrative;
  panel `#leaders` (`loadLeaders` in app.js). Runners are never pruned, so longer periods fill in over time.
- THE TAPE has REAL (default) / FAKE / ANALYZING / ALL tabs: `/api/state?tape_filter=`; counts in `tape_counts`.
- REAL requires every required check to be measured AND passed; missing data = Analyzing (is_real NULL), never Real.
  FEES estimates total fees like Axiom's F (`signals/fees.total_fees_sol`: the LOWER of sampled fee rate x volume and
  avg fee per trade x trade count; SOL price from Jupiter -> kv `sol_usd`); it must be >= `min_fee_volume_pct` of volume,
  >= `min_sol_per_100k_mcap` (6) SOL per $100k of CURRENT market cap (not ATH: a coin dumping from its peak keeps the
  fees it earned) and >= `min_sol_per_trader` (0.002). The sample is spread evenly over the coin's whole life
  (`HeliusTxs.spread_sample`: every signature back to launch, then an even pick), so a dev who washed the run can't hide
  behind later real traders. `forensics.strip_pool_fees`: SOL sent to a wallet that >=90% of sampled trades pay (creator
  vault, token tax, protocol fee) is NOT a trader fee (a washing dev gets it back). Bump `FORENSICS_VERSION` when
  measurement changes: all runners drop their fee verdict (-> Analyzing) and are re-sampled.
- NARRATIVE ROTATION is a board (`renderRotation`): share bar, Leading/Improving/Weakening/Lagging, heating/cooling.

### DEV SYSTEM
`core/devs.py` (`/api/devs`, `/api/devs/<wallet>`): groups coins by creator (`dev_wallet` from Jupiter, `creator`
from PumpPortal / pump.fun) for every dev behind a runner; Jupiter adds total launches + graduations. Labels in
`labels()` (Hit maker, Fake factory, Clean runners, Serial launcher, One-hit, Migrator, New dev). The KOL LAB's
DEV SYSTEM tab reuses the solar engine with `SYS_MODE = "dev"` (planets = devs, moons = their runners, `openDev`).

### Timeframes
The dashboard's TIMEFRAME bar (7D … 1H; 1H is the shortest) passes `?tf=` to `/api/state`. `api/server.py:TIMEFRAMES` lists them;
runners count by when they crossed the bar (`ts_100k`, else launch), launches by launch time. Add a timeframe
there and in `TFS` in web/app.js.

### Coin pictures + fake explanations
- `api/images.py` (async, cached in the data dir, misses retried hourly): `/api/img/<mint>` (coin's own link, KOL
  holdings link, pump.fun metadata JSON image, Jupiter icon, DexScreener CDN, pump.fun image service),
  `/api/pfp/<handle>` (X photo / unavatar), `/api/srcicon/<source>` (site favicons for SENSORY INPUT; add the
  domain to `SOURCE_DOMAINS` for a new source). Only public https URLs are fetched (`safe_url`).
  Frontend: `coinImg(url, sym, big, mint)`; pass the mint so the proxy is used.
- `core/forensics.explain_fake(coin)` -> `coin["fake"] = {headline, failed, reasons, metrics}` for coins with
  is_real False; shown by `fakeBlock()` in the coin popover, COIN FILE and the FEES agent list.

### 𝕏 POSTS tab
`core/xposts.py` -> `/api/coin/<mint>/x`: the posts that pushed a coin. Sources: the tweet the coin links to (X API with
`X_BEARER_TOKEN`, else the free `publish.twitter.com/oembed`, cached in kv `oembed:<id>`), tweet links in the description,
and X search results (`x_top_tweets`, top 8 by engagement, from `sources/twitter.py`). Each post gets a phase vs the run
(before launch / before it crossed $100k / after) and `boost` = before or during the run AND (linked tweet, celebrity,
KOL, big account, or real engagement). UI: COIN FILE tabs (`CF_TAB`, `xPostsHtml`) and the coin popover's "Posts that boosted it" (key B).

### Clickable Synapse + popovers
Everything in the Synapse opens a Claude-style popover (`#coinpop`, web/app.js): narrative neurons -> `themePop`,
agent blobs -> `agentPop` (SCOUT/HOLDERS/KOLS/FEES/CATALYST/THESIS), SENSORY INPUT cards -> `sourcePop`, tape rows
and coin lists -> `openPop` (coin card; Backspace/← returns to the list it came from). Hit-testing lives in
web/synapse.js `pick()`. Lists come from `/api/coins?tf=&theme=&catalyst=&real=&fail=&kols=&sort=`.

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
- **Friends Edition** (`core/edition.py`): the same code built a second time (`EDITION=friends` in the workflow matrix) as
  `TrenchScope-Friends-v1.N-*`: its own data folder (`TrenchScopeFriends`), port 8100 and a FRIENDS EDITION badge, so it never
  shares keys, KOLs or history with the main copy, even on one computer. Same release, same update banner. Run from source
  with `TRENCH_EDITION=friends`. Add an edition: a row in `EDITIONS` + the workflow matrix.
- **Writable state** (db, `settings.json`, your copies of data/*.json, `config.local.json`) lives in `TRENCH_DATA_DIR`
  (desktop: the OS user-data folder; default: `./data`). Bundled defaults in `ROOT/data` are the fallback.
- **Settings API** (`/api/settings`): saving is allowed from localhost, or with header `x-admin-password` when `ADMIN_PASSWORD`
  is set (hosted). Changing the mode re-execs the process.
- New plugin modules are picked up by the desktop build automatically (`--collect-submodules`).

- `created_ts` always keeps the earliest value any source reports (DexScreener's first pair = launch).
- Non-runners older than lookback + 4 days are pruned each scan.

- DB reads use per-thread read connections (WAL), so the dashboard never waits on a scan's writes; writes
  commit in chunks of 200.
- Popovers render instantly from cached/known data (`cj`/`cjTrack`/`peek` in app.js); hovering a coin prefetches it.

## Conventions
- All thresholds in config.json; read via `self.conf` (plugins) or `get_config().get_path(...)`.
- Demo mode (`--demo` / `TRENCH_MODE=demo`) uses only `is_demo=True` sources and `data/trench_demo.db`.
- Log with `core.events.log(AGENT, msg, level)`; it feeds THE TRACE and stdout.
- Escape every external string in the frontend with `esc()` (coin names are attacker-controlled).
- Quick check after changes: `python run.py scan --demo && python run.py thesis daily --demo`.
