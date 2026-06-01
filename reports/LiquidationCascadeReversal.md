# Verdict OOS — LiquidationCascadeReversal

*Intervalle 15m · confidence données **MEDIUM** · 9 configs testées · 20 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-6.13) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-7.13) ; edge déflaté ≤ seuil multiple-testing (SR=-0.057 ≤ SR0=0.038)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.05) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'range_atr_mult': 4.0, 'vol_mult': 3.0, 'horizon_bars': 4}`
- **AvgNet_bps OOS** : -6.13 bps/trade (après 14 bps) · 293 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.048 (SR=-0.057 vs seuil SR0=0.038) → NON significatif ⚠️
- Breadth : 8/20 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | +1.9 | -1.1 | -4.1 | -7.1 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| FET | +44.41 |
| TAO | +36.66 |
| NEAR | +24.50 |
| ONDO | +11.98 |
| ASTER | +9.33 |
| LIT | +7.44 |
| XMR | +4.91 |
| VVV | +0.34 |
| WLD | -1.72 |
| BNB | -5.25 |
| SUI | -6.37 |
| TON | -8.74 |
| BTC | -9.74 |
| SOL | -11.58 |
| ETH | -12.83 |
| XLM | -14.40 |
| DOGE | -17.37 |
| XRP | -22.26 |
| ZEC | -40.36 |
| HYPE | -53.22 |

### Heatmap robustesse — range_atr_mult (lignes) × horizon_bars (cols), AvgNet_bps OOS

| range_atr_mult\horizon_bars | 2 | 4 | 8 |
|---|---|---|---|
| 2.0 | -11.1 | -16.3 | -15.5 |
| 3.0 | -9.3 | -13.0 | -15.2 |
| 4.0 | -6.9 | **-6.1** | -15.1 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*