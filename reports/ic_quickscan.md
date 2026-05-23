# IC Quick-Scan — signaux microstructure collectés

Source : `logs/seconds_features.csv` — 496800 lignes, 12 symboles.

Coût round-trip de référence : **10 bps**. Un signal n'est exploitable que si l'écart de rendement décile-haut − décile-bas dépasse ce coût.


## Couverture par symbole

| Symbole | Lignes |
|---|---|
| ARB | 41482 |
| AVAX | 41481 |
| AAVE | 41475 |
| BTC | 41473 |
| BCH | 41472 |
| ETH | 41413 |
| HYPE | 41403 |
| LINK | 41390 |
| LTC | 41366 |
| OP | 41345 |
| SOL | 41309 |
| XRP | 41191 |

## Spearman IC — signal vs forward return

| Signal | IC 5s | IC 15s | IC 30s | IC 60s | IC 120s | n |
|---|---|---|---|---|---|---|
| obi_1 | +0.1911 * | +0.1301 * | +0.0967 * | +0.0731 * | +0.0509 * | 496696 |
| obi_3 | +0.1811 * | +0.1260 * | +0.0941 * | +0.0721 * | +0.0508 * | 496696 |
| obi_5 | +0.1614 * | +0.1119 * | +0.0838 * | +0.0643 * | +0.0454 * | 496696 |
| obi_10 | +0.1334 * | +0.0947 * | +0.0716 * | +0.0549 * | +0.0364 * | 496696 |
| trade_imbalance_5s | +0.0667 * | +0.0345 * | +0.0295 * | +0.0275 * | +0.0172 | 259864 |
| trade_imbalance_10s | +0.0445 * | +0.0232 * | +0.0229 * | +0.0237 * | +0.0146 | 331164 |
| trade_imbalance_30s | +0.0263 * | +0.0148 | +0.0173 | +0.0206 * | +0.0119 | 436782 |
| microprice_pressure | +0.1251 * | +0.0778 * | +0.0569 * | +0.0447 * | +0.0291 * | 496696 |
| vwap_slope_5_30 | +0.0609 * | +0.0354 * | +0.0279 * | +0.0342 * | +0.0277 * | 259864 |
| r_5s | +0.1119 * | +0.0641 * | +0.0518 * | +0.0403 * | +0.0297 * | 496641 |
| r_15s | +0.0727 * | +0.0443 * | +0.0367 * | +0.0354 * | +0.0305 * | 496443 |
| r_30s | +0.0595 * | +0.0373 * | +0.0313 * | +0.0353 * | +0.0342 * | 496153 |
| book_flow_alignment | +0.0016 | -0.0014 | -0.0003 | +0.0039 | +0.0064 | 496696 |
| book_flow_divergence | -0.0701 * | -0.0542 * | -0.0348 * | -0.0194 | -0.0151 | 331164 |
| absorption_buy_proxy | +0.0434 * | +0.0317 * | +0.0261 * | +0.0199 | +0.0180 | 496696 |
| absorption_sell_proxy | -0.0355 * | -0.0210 * | -0.0127 | -0.0026 | +0.0025 | 496696 |
| liquidity_vacuum | -0.0068 | -0.0159 | -0.0202 * | -0.0147 | -0.0141 | 496336 |
| pressure_score_raw | +0.1250 * | +0.0789 * | +0.0634 * | +0.0541 * | +0.0380 * | 496696 |

`*` = |IC| ≥ 0.02 (seuil indicatif du framework §8). Un IC qui change de signe selon l'horizon = bruit.


## Écart de rendement décile-haut − décile-bas (bps)

Pour l'horizon 30 s. Si l'écart < coût round-trip, le signal ne paie pas même avec un timing parfait.

| Signal | Décile bas | Décile haut | Écart (bps) | > coût ? |
|---|---|---|---|---|
| obi_1 | -0.80 | +1.03 | +1.83 | non |
| obi_3 | -0.80 | +0.92 | +1.72 | non |
| obi_5 | -0.68 | +0.93 | +1.61 | non |
| obi_10 | -0.65 | +0.81 | +1.46 | non |
| trade_imbalance_5s | -0.16 | +0.20 | +0.36 | non |
| trade_imbalance_10s | -0.10 | +0.18 | +0.28 | non |
| trade_imbalance_30s | -0.02 | +0.18 | +0.20 | non |
| microprice_pressure | -0.27 | +0.30 | +0.56 | non |
| vwap_slope_5_30 | +0.10 | +0.59 | +0.49 | non |
| r_5s | -0.22 | +0.71 | +0.92 | non |
| r_15s | +0.01 | +0.67 | +0.66 | non |
| r_30s | -0.04 | +0.61 | +0.65 | non |
| book_flow_alignment | +0.10 | +0.11 | +0.01 | non |
| book_flow_divergence | +0.35 | -0.29 | -0.64 | non |
| absorption_buy_proxy | +0.03 | +0.06 | +0.04 | non |
| absorption_sell_proxy | +0.08 | +0.06 | -0.02 | non |
| liquidity_vacuum | +0.30 | -0.07 | -0.37 | non |
| pressure_score_raw | -0.56 | +0.81 | +1.37 | non |

## Verdict

**Aucun signal microstructure n'a un écart de décile supérieur au coût round-trip.** Sur ces données et à ces horizons, aucun de ces signaux n'est exploitable en taker. C'est cohérent avec le framework §2 : OBI / pressure / imbalance sont des mesures de liquidité, pas des alphas.

> Rappel : un IC positif ne suffit pas. Le signal doit aussi survivre au walk-forward, au retrait du bêta-BTC et tenir sur plusieurs symboles (framework §6-§8).
