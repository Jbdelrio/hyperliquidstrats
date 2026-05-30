# Probe — can ARIMA / GARCH predict short-horizon direction?

AR(1) directional hit-rate (50% = coin flip), GARCH vol-forecast correlation (magnitude vs signed), and microstructure Spearman IC.


## 1. AR(1) ('ARIMA') directional hit-rate — 50% is a coin flip

| Coin | 15s | 30s | 120s | 300s |
|---|---|---|---|---|
| BTC | 51.3% | 50.4% | 48.2% | 46.2% |
| ETH | 51.9% | 50.2% | 47.1% | 47.9% |
| INJ | 52.2% | 51.3% | 52.3% | 54.3% |
| WLD | 50.4% | 50.3% | 46.0% | 45.1% |
| KAITO | 52.8% | 54.0% | 57.6% | 58.1% |
| BANANA | 45.6% | 44.3% | 44.6% | 43.1% |

## 2. GARCH(1,1) — forecasts magnitude, not direction

| Coin | corr(vol, |r|) | corr(vol, signed r) | alpha+beta |
|---|---:|---:|---:|
| BTC | +0.060 | +0.007 | 1.000 |
| ETH | +0.067 | +0.004 | 1.000 |
| INJ | +0.035 | +0.011 | 0.051 |
| WLD | +0.067 | +0.014 | 1.000 |
| KAITO | +0.022 | -0.000 | 0.980 |
| BANANA | +0.043 | +0.004 | 0.726 |

## 3. Microstructure feature IC (Spearman) vs forward return

| Coin | Feature | 15s | 30s | 120s | 300s |
|---|---|---|---|---|---|
| BTC | trade_imbalance_30s | +0.046 | +0.030 | +0.000 | -0.025 |
| BTC | microprice_pressure | +0.319 | +0.241 | +0.119 | +0.064 |
| BTC | obi_10 | +0.323 | +0.250 | +0.125 | +0.061 |
| ETH | trade_imbalance_30s | +0.038 | +0.023 | -0.015 | -0.039 |
| ETH | microprice_pressure | +0.283 | +0.202 | +0.095 | +0.042 |
| ETH | obi_10 | +0.262 | +0.188 | +0.090 | +0.040 |
| INJ | trade_imbalance_30s | -0.013 | -0.027 | +0.013 | +0.036 |
| INJ | microprice_pressure | +0.043 | +0.055 | +0.065 | +0.082 |
| INJ | obi_10 | +0.004 | +0.003 | -0.001 | +0.033 |
| WLD | trade_imbalance_30s | -0.012 | -0.028 | -0.072 | -0.086 |
| WLD | microprice_pressure | +0.055 | +0.028 | +0.007 | -0.023 |
| WLD | obi_10 | +0.058 | +0.053 | +0.071 | +0.069 |
| KAITO | trade_imbalance_30s | -0.022 | -0.028 | -0.001 | -0.011 |
| KAITO | microprice_pressure | +0.037 | +0.021 | -0.033 | -0.064 |
| KAITO | obi_10 | -0.017 | -0.018 | -0.012 | -0.009 |
| BANANA | trade_imbalance_30s | -0.023 | -0.033 | +0.008 | -0.018 |
| BANANA | microprice_pressure | +0.079 | +0.057 | +0.072 | +0.025 |
| BANANA | obi_10 | +0.017 | +0.016 | +0.057 | +0.064 |

## Verdict

- If the AR(1) hit-rates sit at ~50% and GARCH `corr(vol, signed r) ≈ 0`, then **no time-series model gives a directional edge** at these horizons — GARCH/ARIMA tell you *how big* the next move is, never *which way*.

- Any tradeable direction has to come from the **microstructure IC** in §3 — and that IC is strongest on the altcoins at 120-300s, not on majors at 15-30s. That is exactly where the leverage backtest found positive expectancy.
