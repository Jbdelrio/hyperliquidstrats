# Verdict OOS — HourlyBreakout_ZEC

*Intervalle 1h · confidence données **HIGH** · 20 configs testées · 1 coins (TOP 20)*

## 🟡 **GO (non significatif à 95% — à confirmer)**

**Avertissements :** NON significatif à 95% après déflation (DSR=0.86) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'donchian_period': 40, 'hold_bars': 12, 'min_atr_bps': 25.0, 'min_cost_ratio': 2.0, 'both_directions': True, 'notional': 1000.0}`
- **AvgNet_bps OOS** : +70.27 bps/trade (après 14 bps) · 130 trades OOS
- Plateau paramétrique : oui ✅
- Deflated Sharpe : DSR=0.858 (SR=0.148 vs seuil SR0=0.061) → NON significatif ⚠️
- Breadth : 1/1 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | +78.3 | +75.3 | +72.3 | +69.3 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| ZEC | +70.27 |

### Heatmap robustesse — donchian_period (lignes) × hold_bars (cols), AvgNet_bps OOS

| donchian_period\hold_bars | 2 | 4 | 6 | 12 |
|---|---|---|---|---|
| 10 | +5.7 | +21.7 | +20.0 | -1.7 |
| 15 | +2.6 | +18.9 | +17.9 | +29.6 |
| 20 | +7.1 | +18.4 | +18.9 | +31.4 |
| 30 | +11.3 | +21.4 | +24.4 | +36.2 |
| 40 | +15.7 | +33.3 | +25.3 | **+70.3** |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*