# Verdict OOS — FundingExtremeReversal

*Intervalle 1h · confidence données **HIGH** · 9 configs testées · 20 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-8.75) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-9.75) ; edge déflaté ≤ seuil multiple-testing (SR=-0.019 ≤ SR0=0.055)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.07) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'window_bars': 480, 'horizon_bars': 12, 'hi_pct': 0.9, 'lo_pct': 0.1, 'min_abs_funding_bps': 0.5}`
- **AvgNet_bps OOS** : -8.75 bps/trade (après 14 bps) · 387 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.074 (SR=-0.019 vs seuil SR0=0.055) → NON significatif ⚠️
- Breadth : 9/20 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | -0.8 | -3.8 | -6.8 | -9.8 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| ETH | +706.24 |
| XRP | +294.89 |
| BNB | +244.83 |
| DOGE | +166.67 |
| ZEC | +101.73 |
| HYPE | +55.90 |
| FET | +33.17 |
| XLM | +33.07 |
| LIT | +9.28 |
| SUI | +0.00 |
| XMR | -1.80 |
| ONDO | -8.97 |
| TAO | -24.03 |
| SOL | -24.72 |
| TON | -27.67 |
| WLD | -47.87 |
| NEAR | -72.25 |
| VVV | -87.99 |
| ASTER | -96.30 |
| BTC | -484.21 |

### Heatmap robustesse — window_bars (lignes) × horizon_bars (cols), AvgNet_bps OOS

| window_bars\horizon_bars | 3 | 6 | 12 |
|---|---|---|---|
| 120 | -28.8 | -36.3 | -20.2 |
| 240 | -25.5 | -43.7 | -26.8 |
| 480 | -19.8 | -26.6 | **-8.8** |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*