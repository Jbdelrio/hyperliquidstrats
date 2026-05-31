# Verdict OOS — AlphaSignalDecile_taker

*Intervalle 1s · confidence données **LOW** · 9 configs testées · 8 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-8.70) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-9.20) ; edge déflaté ≤ seuil multiple-testing (SR=-0.133 ≤ SR0=0.355) ; breadth insuffisante (1/8 coins > 0, requis ≥3)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.00) — résultat à confirmer ; données LOW (ex. 1m ~5j) — GO provisoire au mieux

## Métriques (meilleur config, OOS purgé)

- Params : `{'horizon_s': 300, 'decile': 0.2}`
- **AvgNet_bps OOS** : -8.70 bps/trade (après 14 bps) · 372 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.000 (SR=-0.133 vs seuil SR0=0.355) → NON significatif ⚠️
- Breadth : 1/8 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | -4.7 | -6.2 | -7.7 | -9.2 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| INJ | +12.75 |
| KAITO | -2.01 |
| WLD | -3.85 |
| ARB | -10.00 |
| OP | -10.06 |
| APE | -12.18 |
| BANANA | -17.14 |
| COMP | -23.67 |

### Heatmap robustesse — horizon_s (lignes) × decile (cols), AvgNet_bps OOS

| horizon_s\decile | 0.05 | 0.1 | 0.2 |
|---|---|---|---|
| 60 | -15.1 | -14.7 | -14.1 |
| 120 | -14.5 | -14.5 | -13.3 |
| 300 | -12.6 | -9.5 | **-8.7** |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*