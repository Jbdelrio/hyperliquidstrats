# Verdict OOS — CrossSectionalReversal

*Intervalle 1h · confidence données **MEDIUM** · 9 configs testées · 20 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-9.05) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-10.05) ; edge déflaté ≤ seuil multiple-testing (SR=-0.018 ≤ SR0=0.024)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.02) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'lookback_bars': 1, 'horizon_bars': 24, 'quantile': 0.25}`
- **AvgNet_bps OOS** : -9.05 bps/trade (après 14 bps) · 2569 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.019 (SR=-0.018 vs seuil SR0=0.024) → NON significatif ⚠️
- Breadth : 7/20 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | -1.0 | -4.0 | -7.0 | -10.0 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| VVV | +111.12 |
| FET | +73.14 |
| NEAR | +20.63 |
| ONDO | +17.21 |
| SOL | +5.26 |
| TON | +3.58 |
| WLD | +2.65 |
| TAO | -9.63 |
| SUI | -24.91 |
| DOGE | -24.98 |
| BTC | -28.60 |
| XRP | -29.48 |
| LIT | -31.39 |
| BNB | -32.54 |
| XMR | -35.87 |
| HYPE | -36.74 |
| ETH | -37.13 |
| ASTER | -41.99 |
| XLM | -42.52 |
| ZEC | -45.26 |

### Heatmap robustesse — lookback_bars (lignes) × horizon_bars (cols), AvgNet_bps OOS

| lookback_bars\horizon_bars | 4 | 12 | 24 |
|---|---|---|---|
| 1 | -14.6 | -18.6 | **-9.0** |
| 4 | -13.3 | -15.2 | -29.7 |
| 12 | -12.4 | -14.8 | -22.9 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*