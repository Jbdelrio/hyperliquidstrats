# Verdict OOS — TrendFollowingVolTarget

*Intervalle 4h · confidence données **HIGH** · 9 configs testées · 20 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-50.85) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-51.85) ; edge déflaté ≤ seuil multiple-testing (SR=-0.028 ≤ SR0=0.055)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.09) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'ema_fast': 20, 'ema_slow': 100, 'min_atr_bps': 20.0, 'max_hold_bars': 60, 'notional': 1000.0}`
- **AvgNet_bps OOS** : -50.85 bps/trade (après 14 bps) · 259 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.087 (SR=-0.028 vs seuil SR0=0.055) → NON significatif ⚠️
- Breadth : 11/20 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | -42.9 | -45.9 | -48.9 | -51.9 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| NEAR | +655.14 |
| ONDO | +399.62 |
| XLM | +358.75 |
| VVV | +351.79 |
| HYPE | +294.93 |
| XMR | +291.92 |
| BNB | +198.23 |
| BTC | +115.22 |
| DOGE | +74.70 |
| SOL | +49.47 |
| ETH | +14.41 |
| SUI | -26.32 |
| ZEC | -61.34 |
| XRP | -105.56 |
| ASTER | -280.34 |
| LIT | -456.26 |
| FET | -459.47 |
| TAO | -591.64 |
| WLD | -615.04 |
| TON | -1256.48 |

### Heatmap robustesse — ema_fast (lignes) × ema_slow (cols), AvgNet_bps OOS

| ema_fast\ema_slow | 30 | 50 | 100 |
|---|---|---|---|
| 5 | -223.7 | -93.0 | -85.1 |
| 10 | -251.9 | -125.3 | -67.2 |
| 20 | -211.1 | -100.1 | **-50.9** |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*