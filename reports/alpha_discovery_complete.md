# Alpha discovery — exhaustive IC sweep

*Generated 2026-05-25T15:10:18 — corpus from seconds_features.parquet*

## 1. Headlines

- (symbol × feature × horizon) combos scanned : **2,772**
- Valid (n_test ≥ 4000) : **2,562**
- Sign stable (train + test same sign) : **1,435**
- Net positive after MAKER cost (3 bps RT) : **190**
- Net positive after TAKER cost (9 bps RT) : **58**

## 2. Top 20 signals by |Spearman IC| (out-of-sample)

| Symbol | Feature | Horizon | n_test | Pearson_test | Spearman_test | t_stat | Decile spread test bps | Net maker bps | Net taker bps | Sign stable? |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| BTC | obi_3 | 5s | 37,855 | 0.16555 | 0.45132 | 32.66 | 1.402 | -2.598 | -8.598 | OK |
| BTC | obi_1 | 5s | 37,855 | 0.16057 | 0.45005 | 31.65 | 1.343 | -2.657 | -8.657 | OK |
| BTC | obi_5 | 5s | 37,855 | 0.16857 | 0.447 | 33.27 | 1.426 | -2.574 | -8.574 | OK |
| BTC | microprice_pressure | 5s | 37,855 | 0.14245 | 0.44635 | 28.0 | 1.317 | -2.683 | -8.683 | OK |
| BTC | obi_10 | 5s | 37,855 | 0.17809 | 0.43177 | 35.21 | 1.44 | -2.56 | -8.56 | OK |
| ETH | obi_3 | 5s | 37,813 | 0.15595 | 0.40512 | 30.7 | 1.827 | -2.173 | -8.173 | OK |
| ETH | obi_1 | 5s | 37,813 | 0.14489 | 0.40501 | 28.47 | 1.672 | -2.328 | -8.328 | OK |
| ETH | microprice_pressure | 5s | 37,813 | 0.14081 | 0.40241 | 27.66 | 1.65 | -2.35 | -8.35 | OK |
| ETH | obi_5 | 5s | 37,813 | 0.16214 | 0.39033 | 31.95 | 1.784 | -2.216 | -8.216 | OK |
| ETH | obi_10 | 5s | 37,813 | 0.1701 | 0.36999 | 33.57 | 1.736 | -2.264 | -8.264 | OK |
| SOL | obi_1 | 5s | 37,573 | 0.13772 | 0.35575 | 26.95 | 1.604 | -2.396 | -8.396 | OK |
| SOL | obi_3 | 5s | 37,573 | 0.14275 | 0.35373 | 27.96 | 1.644 | -2.356 | -8.356 | OK |
| BTC | pressure_score_raw | 5s | 37,855 | 0.15065 | 0.35326 | 29.65 | 1.127 | -2.873 | -8.873 | OK |
| SOL | obi_5 | 5s | 37,573 | 0.14855 | 0.35218 | 29.12 | 1.769 | -2.231 | -8.231 | OK |
| SOL | microprice_pressure | 5s | 37,573 | 0.102 | 0.35099 | 19.87 | 1.606 | -2.394 | -8.394 | OK |
| BTC | obi_5 | 15s | 37,845 | 0.15414 | 0.34176 | 30.35 | 1.833 | -2.167 | -8.167 | OK |
| BTC | obi_3 | 15s | 37,845 | 0.15216 | 0.34156 | 29.95 | 1.926 | -2.074 | -8.074 | OK |
| BTC | obi_1 | 15s | 37,845 | 0.1467 | 0.33567 | 28.85 | 1.813 | -2.187 | -8.187 | OK |
| SOL | obi_10 | 5s | 37,573 | 0.14949 | 0.33512 | 29.3 | 1.798 | -2.202 | -8.202 | OK |
| BTC | microprice_pressure | 15s | 37,845 | 0.11856 | 0.33472 | 23.23 | 1.679 | -2.321 | -8.321 | OK |

## 3. Top 20 signals — net positive after MAKER cost (sign-stable)

This is what we would actually trade if we used maker mode.

| Symbol | Feature | Horizon | n_test | Spearman | Decile spread bps | Net maker bps | Net taker bps |
|---|---|---:|---:|---:|---:|---:|---:|
| WLD | liquidity_vacuum | 300s | 8,287 | 0.14814 | 26.153 | 22.153 | 16.153 |
| INJ | liquidity_vacuum | 300s | 8,412 | 0.11114 | 22.243 | 18.243 | 12.243 |
| COMP | obi_10 | 300s | 8,457 | 0.11007 | 21.532 | 17.532 | 11.532 |
| BLAST | obi_1 | 300s | 7,930 | 0.26084 | 19.819 | 15.819 | 9.819 |
| WLD | sell_volume_usd_10s | 120s | 8,469 | 0.09746 | 19.783 | 15.783 | 9.783 |
| BANANA | rv_30s | 300s | 8,467 | 0.12817 | 19.486 | 15.486 | 9.486 |
| AAVE | obi_10 | 300s | 29,656 | -0.06602 | 19.239 | 15.239 | 9.239 |
| INJ | trade_imbalance_30s | 120s | 5,812 | 0.0146 | 19.157 | 15.157 | 9.157 |
| APE | microprice_pressure | 300s | 8,472 | 0.18171 | 18.037 | 14.037 | 8.037 |
| APE | rv_30s | 300s | 8,472 | 0.04338 | 17.675 | 13.675 | 7.675 |
| WLD | obi_10 | 120s | 8,469 | 0.09484 | 16.812 | 12.812 | 6.812 |
| APE | rv_60s | 300s | 8,472 | 0.05328 | 16.456 | 12.456 | 6.456 |
| BANANA | rv_60s | 300s | 8,467 | 0.153 | 16.342 | 12.342 | 6.342 |
| BLAST | obi_1 | 120s | 8,110 | 0.22638 | 13.228 | 9.228 | 3.228 |
| APE | obi_3 | 300s | 8,472 | 0.101 | 11.054 | 7.054 | 1.054 |
| WLD | rv_30s | 120s | 8,467 | 0.0902 | 10.593 | 6.593 | 0.593 |
| AAVE | obi_10 | 120s | 29,836 | -0.03555 | 10.45 | 6.45 | 0.45 |
| WLD | rv_60s | 60s | 8,527 | 0.0905 | 10.398 | 6.398 | 0.398 |
| AVAX | vwap_slope_5_30 | 300s | 10,181 | 0.02036 | 10.34 | 6.34 | 0.34 |
| WLD | rv_60s | 30s | 8,557 | 0.10454 | 9.683 | 5.683 | -0.317 |

## 4. Top 20 signals — net positive after TAKER cost (sign-stable)

These are tradeable in taker mode without any maker queue games.

| Symbol | Feature | Horizon | n_test | Spearman | Decile spread bps | Net taker bps |
|---|---|---:|---:|---:|---:|---:|
| WLD | liquidity_vacuum | 300s | 8,287 | 0.14814 | 26.153 | 16.153 |
| INJ | liquidity_vacuum | 300s | 8,412 | 0.11114 | 22.243 | 12.243 |
| COMP | obi_10 | 300s | 8,457 | 0.11007 | 21.532 | 11.532 |
| BLAST | obi_1 | 300s | 7,930 | 0.26084 | 19.819 | 9.819 |
| WLD | sell_volume_usd_10s | 120s | 8,469 | 0.09746 | 19.783 | 9.783 |
| BANANA | rv_30s | 300s | 8,467 | 0.12817 | 19.486 | 9.486 |
| AAVE | obi_10 | 300s | 29,656 | -0.06602 | 19.239 | 9.239 |
| INJ | trade_imbalance_30s | 120s | 5,812 | 0.0146 | 19.157 | 9.157 |
| APE | microprice_pressure | 300s | 8,472 | 0.18171 | 18.037 | 8.037 |
| APE | rv_30s | 300s | 8,472 | 0.04338 | 17.675 | 7.675 |
| WLD | obi_10 | 120s | 8,469 | 0.09484 | 16.812 | 6.812 |
| APE | rv_60s | 300s | 8,472 | 0.05328 | 16.456 | 6.456 |
| BANANA | rv_60s | 300s | 8,467 | 0.153 | 16.342 | 6.342 |
| BLAST | obi_1 | 120s | 8,110 | 0.22638 | 13.228 | 3.228 |
| APE | obi_3 | 300s | 8,472 | 0.101 | 11.054 | 1.054 |
| WLD | rv_30s | 120s | 8,467 | 0.0902 | 10.593 | 0.593 |
| AAVE | obi_10 | 120s | 29,836 | -0.03555 | 10.45 | 0.45 |
| WLD | rv_60s | 60s | 8,527 | 0.0905 | 10.398 | 0.398 |
| AVAX | vwap_slope_5_30 | 300s | 10,181 | 0.02036 | 10.34 | 0.34 |

## 5. Cross-asset lead-lag (other_symbol[feature] predicts target)

### 5a. Top 20 by |Spearman IC|

| Predictor | Feature | Target | Horizon | n | Pearson | Spearman | t_stat | Decile spread bps | Net maker bps | Net taker bps |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ETH | obi_5 | BTC | 5s | 83,489 | 0.1077 | 0.29227 | 31.3 | 1.102 | -2.898 | -8.898 |
| ETH | obi_10 | BTC | 5s | 83,489 | 0.11253 | 0.27675 | 32.72 | 1.108 | -2.892 | -8.892 |
| SOL | obi_5 | BTC | 5s | 82,403 | 0.0985 | 0.27395 | 28.41 | 0.99 | -3.01 | -9.01 |
| SOL | obi_10 | BTC | 5s | 82,403 | 0.09907 | 0.26657 | 28.58 | 1.031 | -2.969 | -8.969 |
| SOL | obi_5 | ETH | 5s | 82,718 | 0.09237 | 0.26274 | 26.68 | 1.198 | -2.802 | -8.802 |
| SOL | obi_10 | ETH | 5s | 82,718 | 0.09091 | 0.24748 | 26.26 | 1.217 | -2.783 | -8.783 |
| BTC | obi_5 | ETH | 5s | 83,489 | 0.08532 | 0.24655 | 24.74 | 1.089 | -2.911 | -8.911 |
| BTC | obi_10 | ETH | 5s | 83,489 | 0.08859 | 0.24262 | 25.7 | 1.102 | -2.898 | -8.898 |
| ETH | pressure_score_raw | BTC | 5s | 83,489 | 0.08507 | 0.2385 | 24.67 | 0.961 | -3.039 | -9.039 |
| SOL | pressure_score_raw | BTC | 5s | 82,403 | 0.08212 | 0.21511 | 23.65 | 0.839 | -3.161 | -9.161 |
| BTC | pressure_score_raw | ETH | 5s | 83,489 | 0.07501 | 0.21321 | 21.73 | 1.0 | -3.0 | -9.0 |
| ETH | obi_5 | SOL | 5s | 82,718 | 0.08919 | 0.21318 | 25.75 | 1.416 | -2.584 | -8.584 |
| ETH | obi_5 | BTC | 15s | 83,479 | 0.08064 | 0.20917 | 23.38 | 1.347 | -2.653 | -8.653 |
| ETH | obi_10 | SOL | 5s | 82,718 | 0.09637 | 0.20508 | 27.85 | 1.42 | -2.58 | -8.58 |
| SOL | pressure_score_raw | ETH | 5s | 82,718 | 0.07297 | 0.20007 | 21.04 | 0.984 | -3.016 | -9.016 |
| ETH | obi_10 | BTC | 15s | 83,479 | 0.08054 | 0.19391 | 23.35 | 1.356 | -2.644 | -8.644 |
| SOL | obi_5 | BTC | 15s | 82,393 | 0.08117 | 0.19246 | 23.38 | 1.248 | -2.752 | -8.752 |
| SOL | obi_10 | BTC | 15s | 82,393 | 0.08037 | 0.18656 | 23.14 | 1.307 | -2.693 | -8.693 |
| ETH | pressure_score_raw | BTC | 15s | 83,479 | 0.07482 | 0.18619 | 21.68 | 1.344 | -2.656 | -8.656 |
| BTC | obi_5 | SOL | 5s | 82,403 | 0.06667 | 0.17685 | 19.18 | 1.08 | -2.92 | -8.92 |

### 5b. Cross-asset signals net-positive after MAKER cost

| Predictor | Feature | Target | Horizon | Spearman | Decile bps | Net maker bps |
|---|---|---|---:|---:|---:|---:|
| AAVE | obi_10 | SOL | 300s | -0.00242 | 11.389 | 7.389 |
| BLAST | obi_10 | SOL | 300s | 0.06111 | 10.496 | 6.496 |
| BLAST | pressure_score_raw | SOL | 300s | -0.00611 | 10.494 | 6.494 |
| AAVE | obi_10 | ETH | 300s | 0.01221 | 9.46 | 5.46 |
| BLAST | obi_10 | ETH | 300s | 0.04438 | 9.381 | 5.381 |
| AAVE | obi_10 | BTC | 300s | 0.0337 | 7.903 | 3.903 |
| XRP | obi_10 | ETH | 300s | 0.04663 | 7.558 | 3.558 |
| AAVE | obi_5 | SOL | 300s | 0.00758 | 7.072 | 3.072 |
| BLAST | pressure_score_raw | ETH | 300s | -0.00678 | 7.067 | 3.067 |
| XRP | obi_10 | SOL | 300s | 0.024 | 6.163 | 2.163 |
| BLAST | obi_10 | BTC | 300s | 0.05978 | 6.086 | 2.086 |
| STABLE | obi_10 | BTC | 300s | 0.06429 | 5.998 | 1.998 |
| APE | pressure_score_raw | ETH | 300s | 0.08181 | 5.859 | 1.859 |
| APE | obi_10 | ETH | 300s | 0.05882 | 5.769 | 1.769 |
| AAVE | obi_5 | ETH | 300s | 0.0086 | 5.372 | 1.372 |
| APE | obi_5 | ETH | 300s | 0.06851 | 5.344 | 1.344 |
| HYPE | obi_5 | SOL | 300s | 0.02879 | 5.305 | 1.305 |
| BLAST | obi_10 | ETH | 120s | 0.04275 | 5.181 | 1.181 |
| AAVE | obi_10 | SOL | 120s | -0.00703 | 5.161 | 1.161 |
| HYPE | obi_10 | SOL | 300s | 0.03468 | 5.124 | 1.124 |

## 6. Verdict + next steps

- **Alpha exists at the seconds scale** — 190 signals are net-positive after maker cost on out-of-sample data.
- 58 signals beat the harder TAKER threshold — these are the priority for a paper run with the existing execution.
- Use `reports/alpha_discovery.json` as input to the Phase-3 backtest.
- Cross-asset signals (§5) are the most promising direction — exploiting lead-lag is harder to crowd out and matches the BTC/ETH lead found in §1.
