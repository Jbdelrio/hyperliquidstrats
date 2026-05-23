# Funding Rate Tool

CLI + Dash GUI for live multi-exchange perpetual funding rates and cross-venue arbitrage spreads.

## Exchanges supported

| Exchange    | Cadence | Per-coin | Top-N | History | Detail |
| ----------- | :-----: | :------: | :---: | :-----: | :----: |
| Binance     | 8h      | y        | y     | y       | y      |
| Aster       | 8h      | y        | y     | y       | y      |
| Bitget      | 8h      | y        | y     | y       | y      |
| OKX         | 8h      | y        | -     | y       | y      |
| GateIO      | 8h      | y        | y     | y       | y      |
| Hyperliquid | 1h      | y        | y     | y       | y      |
| Kraken      | 1h      | y        | -     | y       | y      |
| Bitmex      | 8h      | y        | y     | y       | y      |
| Phemex      | 8h      | y*       | -     | -       | -      |

`*` = flaky / often 500. Top-N requires a single-call bulk endpoint, which OKX / Kraken / Phemex don't expose.

**Rate limits respected**: per-exchange min delay between calls is configurable in `config/endpoints.py::EXCHANGE_DELAYS`. Defaults: 0.2s globally, **2s for Bitmex** (30 req/min unauth), **1s for Kraken Futures**.

Unreachable exchanges are silently skipped; flaky ones (`*`) are still attempted on each refresh.

## Install

```powershell
python -m pip install -r requirements.txt
copy .env.example .env   # optional — defaults work without it
```

Python 3.10+. Tested on Windows 11 / Python 3.11.

## CLI

```powershell
# default: BTC across all working exchanges
python main.py cli

# multiple coins
python main.py cli --coins BTC,ETH,SOL

# arbitrage with alert at >= 1 bps spread (exits with code 10 if hit)
python main.py cli --coins BTC --arbitrage --alert 0.0001

# per-exchange comparison vs avg/min/max
python main.py cli --coins BTC,ETH --compare

# export raw rows
python main.py cli --coins BTC,ETH --export csv --output rates.csv

# live watch mode (refresh every 30s)
python main.py cli --coins BTC,ETH --arbitrage --watch 30

# top-20 most extreme funding rates on Binance, all perpetuals
python main.py cli --top 20 --exchange binance
python main.py cli --top 10 --exchange hyperliquid --top-mode high
python main.py cli --lowest 10 --exchange gateio

# every perp on an exchange (no cap)
python main.py cli --all --exchange bitmex

# rich detail panel for one symbol — rate, mark, index, OI, vol, change %, countdown
python main.py cli --symbol ETHUSDT --exchange binance --detail
python main.py cli --symbol BTC --exchange hyperliquid --detail
python main.py cli --symbol XBTUSDT --exchange bitmex --detail

# raw symbol lookup (auto-detail)
python main.py cli --symbol BTC-USDT-SWAP --exchange okx

# last N historical fundings for a specific symbol
python main.py cli --history 50 --symbol BTCUSDT --exchange binance
python main.py cli --history 20 --symbol BTC --exchange hyperliquid

# bypass + clear the 5-minute SQLite cache
python main.py cli --no-cache --clear-cache
```

Direct CLI invocation also works: `python -m cli.main --coins BTC`.

### CLI options

| Flag                | Description                                                 |
| ------------------- | ----------------------------------------------------------- |
| `--coins`           | Comma-separated, e.g. `BTC,ETH,SOL` (default `BTC,ETH,SOL`) |
| `--exchanges`       | Comma-separated or `all` (default `all`)                    |
| `--compare`         | Show per-coin vs avg / min / max table                      |
| `--arbitrage`       | Show low-vs-high exchange spread table                      |
| `--alert THRESHOLD` | Decimal threshold; exit 10 if any spread >= threshold       |
| `--export csv|json` | Dump raw results to file                                    |
| `--output PATH`     | Where to write the export (default `funding_rates.<ext>`)   |
| `--no-cache`        | Skip the SQLite cache                                       |
| `--clear-cache`     | Wipe the cache before running                               |
| `--watch SECONDS`   | Re-run continuously every N seconds                         |
| `--top N`           | Scan ALL perps of one exchange, show top N (replaces normal output) |
| `--lowest N`        | Sugar for `--top N --top-mode low` (most negative rates)    |
| `--all`             | Dump every perp on `--exchange` (no cap)                    |
| `--top-mode MODE`   | `abs` (default) / `high` / `low` — sort by magnitude or sign |
| `--exchange EX`     | Target exchange for `--symbol` / `--detail` / `--history` / `--top` / `--all` (default: binance) |
| `--symbol SYM`      | Raw exchange symbol — bypasses coin→symbol mapping          |
| `--detail`          | Rich panel for `--symbol`: rate, mark, index, OI, vol, change %, countdown |
| `--history N`       | Last N historical fundings for `--symbol` on `--exchange`   |

## GUI

```powershell
python main.py gui
# -> http://127.0.0.1:9000
```

Four tabs — auto-refresh every 30 s, also refreshable manually. Theme: **Bootswatch CYBORG** (dark with cyan accent).

- **Funding Rates** — sortable / filterable table with colour-coded BPS and annualised %.
- **Arbitrage** — bar chart of best long/short pair per coin + alert line.
- **Heatmap** — exchange x coin matrix in basis points (diverging red/green).
- **Top-N** — pick an exchange (binance / aster / bitget / gateio / hyperliquid), an N (1–200) and a sort mode (most extreme / highest / lowest); shows ALL perps of that exchange.

The alert threshold input is in **bps** (basis points).

## Configuration

Override via environment variables (see `.env.example`):

| Variable                 | Default | What                                       |
| ------------------------ | ------- | ------------------------------------------ |
| `FRT_CACHE_TTL_SECONDS`  | 300     | Cache lifetime for fetched rows            |
| `FRT_REQUEST_TIMEOUT`    | 10      | HTTP timeout per request (seconds)         |
| `FRT_RATE_LIMIT_DELAY`   | 0.2     | Min seconds between calls to same exchange |
| `FRT_GUI_PORT`           | 9000    | Dash server port                           |
| `FRT_GUI_HOST`           | 127.0.0.1 | Dash bind host                           |
| `FRT_GUI_REFRESH_MS`     | 30000   | GUI auto-refresh interval (ms)             |
| `FRT_LOG_LEVEL`          | INFO    | Logger verbosity                           |

## Architecture

```
funding_rate_tool/
├── main.py                 # unified `cli` / `gui` dispatcher
├── cli/
│   ├── fetcher.py          # async aiohttp fanout + per-exchange rate limit
│   ├── cache.py            # SQLite 5-min TTL cache
│   ├── display.py          # colored terminal tables + CSV/JSON export
│   └── main.py             # argparse + commands
├── gui/
│   ├── app.py              # Dash app + callbacks
│   └── components/         # controls / rates table / charts
├── config/
│   ├── endpoints.py        # per-exchange adapter functions
│   └── settings.py         # env-driven globals + dark theme palette
├── utils/
│   ├── helpers.py          # arbitrage math + formatting
│   └── logger.py
├── requirements.txt
└── README.md
```

- Each exchange adapter is an `async def fetch_xxx(session, coin) -> Optional[dict]`. Add a new venue by writing one function and registering it in `config/endpoints.py::EXCHANGES`.
- All HTTP goes through one shared `aiohttp.ClientSession` with a per-exchange `asyncio.Lock` enforcing `RATE_LIMIT_DELAY`.
- Cache lives in `./.cache/funding_rates.db`; safe to delete at any time.

## Notes

- Annualised % uses 3 fundings/day for 8h venues and 24 fundings/day for Hyperliquid + Kraken.
- Hyperliquid and Kraken don't publish a hard "next funding time" — we round up to the next UTC hour.
- Phemex is included for completeness but typically returns 500; failures are silent (just marked `*`).
- No API keys required — all endpoints used are public.
