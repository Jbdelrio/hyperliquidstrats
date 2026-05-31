# Verdict OOS — HourlyBreakout_universe

*Intervalle 1h · confidence données **HIGH** · 20 configs testées · 8 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** edge déflaté ≤ seuil multiple-testing (SR=0.025 ≤ SR0=0.046)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.20) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'donchian_period': 15, 'hold_bars': 12, 'min_atr_bps': 25.0, 'min_cost_ratio': 2.0, 'both_directions': True, 'notional': 1000.0}`
- **AvgNet_bps OOS** : +11.23 bps/trade (après 14 bps) · 1624 trades OOS
- Plateau paramétrique : oui ✅
- Deflated Sharpe : DSR=0.198 (SR=0.025 vs seuil SR0=0.046) → NON significatif ⚠️
- Breadth : 5/8 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | +19.2 | +16.2 | +13.2 | +10.2 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| WLD | +61.29 |
| ZEC | +29.56 |
| HYPE | +20.44 |
| FET | +13.09 |
| VVV | +1.95 |
| TAO | -0.60 |
| XMR | -11.86 |
| LIT | -27.77 |

### Heatmap robustesse — donchian_period (lignes) × hold_bars (cols), AvgNet_bps OOS

| donchian_period\hold_bars | 2 | 4 | 6 | 12 |
|---|---|---|---|---|
| 10 | -10.1 | -4.1 | -8.5 | +7.6 |
| 15 | -9.2 | -2.4 | +0.4 | **+11.2** |
| 20 | -7.9 | -1.2 | +5.0 | +10.4 |
| 30 | -7.7 | -2.0 | +0.8 | +7.9 |
| 40 | -7.2 | -1.2 | -0.4 | +1.9 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*