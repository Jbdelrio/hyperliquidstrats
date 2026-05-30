# Trading Frequency Diagnostic

*Generated 2026-05-23T16:26:41 — window 60.0 min (since 2026-05-23T15:26:34)*


## 1. Data feed health

| Coin | books/s | trades/s | last_book_age | last_trade_age | book_stale | mean_spread_bps |
|---|---|---|---|---|---|---|
| AAVE | 1.83 | 0.12 | 1s | 14s | no | 2.03 |
| ARB | 1.83 | 0.02 | 1s | 24s | no | 2.24 |
| AVAX | 1.83 | 0.47 | 1s | 9s | no | 1.33 |
| BCH | 1.83 | 0.03 | 1s | 1s | no | 1.66 |
| BTC | 1.83 | 11.00 | 1s | 2s | no | 0.14 |
| ETH | 1.83 | 1.00 | 1s | 1s | no | 0.49 |
| HYPE | 1.83 | 3.82 | 1s | 1s | no | 0.50 |
| LINK | 1.83 | 0.18 | 1s | 2s | no | 1.92 |
| LTC | 1.83 | 0.07 | 1s | 3s | no | 0.85 |
| OP | 1.83 | 0.03 | 1s | 1s | no | 5.80 |
| SOL | 1.83 | 0.68 | 1s | 1s | no | 0.14 |
| XRP | 1.83 | 0.15 | 1s | 6s | no | 0.76 |

Global: reconnections=3, queue_drops=0, book_updates=424525, trades=180494

## 2. Per-strategy summary

| Strategy | State | Raw signals | Accepted | Rejected | Main rejection | Trades/h | Warmup | Suggested action |
|---|---|---|---|---|---|---|---|---|
| AbsorptionReversal | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| AlphaPressureScalper | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| BTC_5MIN_BINARY_REPL | ACTIVE | 0 | 0 | 0 | — | 0.00 | 19711/1800s OK | — |
| BTC_BINARY_HIGHLEV | ACTIVE | 0 | 0 | 0 | — | 0.00 | 19711/1800s OK | — |
| BookFlowDivergenceReversal | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| BreakoutControlled | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| DonchianTrend | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| FundingArbEnhanced | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| FundingArbitrage | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| FundingCarryHedged | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| MeanReversionKalman | ACTIVE | 0 | 3 | 0 | — | 3.92 | ACTIVE | — |
| MetaAlpha | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| MomentumLS | SUSPENDED | 127 | 11 | 127 | cooldown (127) | 11.27 | SUSPENDED | Cooldown_*_s too long for the trading horizon. |
| OBImbalanceScalper | SUSPENDED | 2 | 7 | 2 | cooldown (2) | 9.20 | SUSPENDED | Cooldown_*_s too long for the trading horizon. |
| RSIBollingerReversion | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| RelativeValue | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| RotationMomentum | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| S8EMS | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| SecondsResearch | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| SpotPerpBasis | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |
| VolatilityRegimeBreakout | ACTIVE | 0 | 0 | 0 | — | 0.00 | ACTIVE | — |

## 3. Top rejection reasons (all strategies, current session)

| Reason | Count |
|---|---|
| `max_positions_reached` | 127343 |
| `market_quality` | 16124 |
| `spread_too_tight` | 4173 |
| `not_enough_data` | 300 |
| `low_volume` | 218 |
| `cooldown` | 129 |
| `funding_too_low` | 116 |
| `spread_too_wide` | 1 |
| `har_rv_too_high` | 1 |

## 4. Gate breakdown

### MarketQualityGate (since process start)
- total evaluated : 43430
- total blocked   : 12710
- top reasons :
   - `low_volume` : 4760
   - `ofi_against_long` : 3368
   - `ofi_against_short` : 2134
   - `depth_against_long` : 750
   - `depth_against_short` : 553
   - `latency_p95` : 519
   - `warmup` : 347
   - `trade_stale` : 198

### DecisionThrottle (since process start)
- total evaluated : 30720
- total blocked   : 1593
- top reasons :
   - `strategy_gap` : 1409
   - `symbol_gap` : 184

### StrategyCapitalLedger / Portfolio (rejected events in window)
- `market_quality` : 307
- `throttle` : 18
- `strategy_budget_blocked` : 9
- `sanity_pending_order_exists` : 5

### MQG blocks per (strategy, reason) — from engine_v9.log
- MomentumLS → `low_volume` : 27940
- MomentumLS → `ofi_against_long` : 11061
- MomentumLS → `ofi_against_short` : 7697
- OBImbalanceScalper → `low_volume` : 3342
- MomentumLS → `depth_against_short` : 2203
- MomentumLS → `trade_stale` : 2175
- MeanReversionKalman → `ofi_against_short` : 2124
- MomentumLS → `depth_against_long` : 2042
- MeanReversionKalman → `ofi_against_long` : 2005
- OBImbalanceScalper → `latency_p95` : 1884
- OBImbalanceScalper → `warmup` : 1773
- MeanReversionKalman → `depth_against_short` : 1754

## 5. Strategies that never trade — likely cause

| Strategy | Likely cause |
|---|---|
| AbsorptionReversal | no signal emitted (likely warmup or never met entry condition) |
| AlphaPressureScalper | no signal emitted (likely warmup or never met entry condition) |
| BTC_5MIN_BINARY_REPL | no signal emitted (likely warmup or never met entry condition) |
| BTC_BINARY_HIGHLEV | no signal emitted (likely warmup or never met entry condition) |
| BookFlowDivergenceReversal | no signal emitted (likely warmup or never met entry condition) |
| BreakoutControlled | no signal emitted (likely warmup or never met entry condition) |
| DonchianTrend | no signal emitted (likely warmup or never met entry condition) |
| FundingArbEnhanced | no signal emitted (likely warmup or never met entry condition) |
| FundingArbitrage | no signal emitted (likely warmup or never met entry condition) |
| FundingCarryHedged | no signal emitted (likely warmup or never met entry condition) |
| MetaAlpha | no signal emitted (likely warmup or never met entry condition) |
| RSIBollingerReversion | no signal emitted (likely warmup or never met entry condition) |
| RelativeValue | no signal emitted (likely warmup or never met entry condition) |
| RotationMomentum | no signal emitted (likely warmup or never met entry condition) |
| S8EMS | no signal emitted (likely warmup or never met entry condition) |
| SecondsResearch | no signal emitted (likely warmup or never met entry condition) |
| SpotPerpBasis | no signal emitted (likely warmup or never met entry condition) |
| VolatilityRegimeBreakout | no signal emitted (likely warmup or never met entry condition) |

## 6. Market regime

- market regime (BTC) : **NORMAL**
- BTC 5-min return : -0.03%

## 7. Headlines

- raw signals (window) : **172632**
- PLACE decisions      : **24227**
- executed fills       : **21**
- trades / 2min        : **0.70**
- net PnL (window)     : **$-4.0700**  (2W / 19L, 9.5% WR)

---
*Generated by `scripts/diagnose_trading_frequency.py`.*