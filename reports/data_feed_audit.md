# Hyperliquid Data Feed Audit — DATA FEED NOT OK

- Snapshot ts : 2026-05-16T22:14:31
- Symbols     : BTC, ETH, SOL, HYPE
- Reconnections : 0
- JSON parse errors : 0
- Invalid books : 0
- Crossed books : 0
- Queue drops (book) : 0
- Queue drops (trade) : 0

## Per-symbol

| Symbol | bu/s | tr/s | spread_mean | spread_p95 | spread_max | lat_mean | lat_p95 | book_age_s | trade_age_s | invalid | crossed |
|--------|------|------|-------------|-----------|-----------|----------|--------|------------|------------|---------|---------|
| BTC | 1.85 | 2.75 | 0.13 | 0.13 | 0.26 | 602.16 | 0.00 | -0.89 | 2.76 | 0 | 0 |
| ETH | 1.85 | 0.55 | 0.46 | 0.46 | 0.92 | 1666.74 | 2563.72 | -0.89 | 0.60 | 0 | 0 |
| SOL | 1.85 | 0.17 | 0.12 | 0.12 | 0.46 | 2888.77 | 28060.76 | -0.89 | 1.34 | 0 | 0 |
| HYPE | 1.85 | 2.05 | 0.37 | 1.20 | 5.50 | 425.18 | 0.00 | -0.89 | 1.20 | 0 | 0 |

## Critical issues
- ETH: latency p95 2564ms > 1000
- SOL: latency p95 28061ms > 1000

## Recommendation
Do **NOT** start paper trading until the critical issues above are resolved.