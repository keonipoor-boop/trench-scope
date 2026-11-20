# Trench Scope

Scans the pump.fun trenches, finds coins that actually ran, checks whether each run was real, explains
why it ran, and writes a daily and an hourly narrative thesis. Results show up on a neon "agent
terminal" dashboard.

## Get it running without commands

**Option A: your own web link (always live, auto-updates)**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/OWNER/REPO)

1. Click the button and sign in to Render with GitHub.
2. Choose an `ADMIN_PASSWORD`. Paste any keys you have now, or add them later in the app's ⚙ Settings.
3. Click **Apply**. A few minutes later you get a permanent link like `https://trench-scope-xxxx.onrender.com`.

Every push to `main` redeploys the site automatically. On the free plan the app sleeps after 15 minutes
with nobody viewing it, and scanning resumes when you open it. The Starter plan ($7/mo) stays on 24/7,
and with a disk it also keeps history across restarts (see `render.yaml`). Keys you type into ⚙ Settings
on the free plan are lost when the app restarts, so put permanent keys in Render's Environment tab.

**Option B: a desktop app (runs on your computer, live data)**

Open the repo's **Releases** page and download the latest build for your system:
`TrenchScope-Windows.zip`, `TrenchScope-macOS.zip` or `TrenchScope-Linux.tar.gz`.
Unzip it and double-click it. Your browser opens the dashboard.
- Windows: if SmartScreen warns you, click More info, then Run anyway.
- macOS: the first time, right-click the app and choose Open.

A new release is built automatically on every push to `main`. When one is out, the dashboard shows
**Update available**. Your keys and data live in your user folder, so new versions keep them.

In both options, ⚙ **Settings** switches between LIVE and DEMO and stores API keys. In LIVE mode,
pump.fun, DexScreener and GeckoTerminal need no key. A free Helius key unlocks the REAL checks
(holders, fees, KOL buys).

## Quick start for developers (no keys needed)

```bash
pip install -r requirements.txt
python run.py demo          # seeds 7 days of fake trenches, then runs the scheduler + dashboard
# open http://127.0.0.1:8000
```

## Live mode

```bash
cp .env.example .env        # add HELIUS_API_KEY (and optionally X_BEARER_TOKEN, ANTHROPIC_API_KEY)
python run.py doctor        # tests every source/endpoint, shows missing keys
python run.py backfill --days 7
python run.py thesis daily
python run.py daemon        # scan every 10 min, hourly meta at :00, daily thesis at 08:00 local
```

| command | what it does |
|---|---|
| `scan` | one pass: discover → enrich → signals → catalysts |
| `backfill --days N` | scan with a custom lookback |
| `thesis daily\|hourly` | write a thesis now and print it |
| `serve` | dashboard + API only |
| `daemon` | scheduler + dashboard (`--no-api` for headless) |
| `demo` | demo database + fake sources + daemon |
| `doctor` | health-check sources and keys |

Add `--demo` to any command to run it against the demo database. Use `--host 0.0.0.0 --port 8000`
to expose the dashboard.

## What it checks

- **Runner:** ATH market cap ≥ $100k (`pipeline.runner_ath_usd`). ATH comes from GeckoTerminal
  OHLCV (max high × 1B supply), pump.fun's own ATH field, and the current market cap.
- **REAL** means every required signal passes. The defaults are: holders ≥ 300, top-10 holders ≤ 35%,
  fees ≥ 2 SOL per $100k of ATH with fee/volume ≥ 0.25% (to catch wash or botted volume), and
  unique traders ≥ 150. "KOL buyers ≥ 1" is tracked but optional.
- **Catalyst:** tweet play, celebrity tweet, community coin, CTO, tech/utility, KOL call, livestream,
  news/event, or pure meme. The rule with the highest weight wins.
- **Narratives:** coins are tagged with themes from `data/narratives.json`. On top of that, emerging
  words catch new metas before you have added a keyword for them.
- **Thesis:** the daily thesis compares the last 24h with the prior 72h. The hourly thesis compares
  the last 1h with the prior 6h and flags a META SHIFT.

Every threshold is in `config.json`. Put your own overrides in `config.local.json`.

## API

`GET /api/state` (everything the dashboard needs) · `GET /api/events?after=ID` · `GET /api/coin/{mint}` ·
`GET /api/runners` · `GET /api/theses?kind=daily` · `POST /api/scan` · `POST /api/thesis/{daily|hourly}`

## Notes

- The pump.fun frontend API is unofficial. Base URLs and paths are lists in config; the app tries each
  one and remembers the first that works.
- Helius fee and trader figures are estimated from a sample of up to 100 recent transactions,
  scaled to the 24h transaction count.
- Claude is optional. Without `ANTHROPIC_API_KEY`, the why-it-ran bullets and the thesis prose come
  from templates.
- See `CLAUDE.md` for the architecture and for how to add a source, signal, catalyst rule, theme, or KOL.

Not financial advice.
