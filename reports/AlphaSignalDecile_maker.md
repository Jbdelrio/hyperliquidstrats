# Verdict OOS — AlphaSignalDecile_maker

*Intervalle 1s · confidence données **LOW** · 9 configs testées · 8 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-0.49) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-0.99) ; edge déflaté ≤ seuil multiple-testing (SR=-0.007 ≤ SR0=0.172)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.00) — résultat à confirmer ; données LOW (ex. 1m ~5j) — GO provisoire au mieux

## Métriques (meilleur config, OOS purgé)

- Params : `{'horizon_s': 300, 'decile': 0.1}`
- **AvgNet_bps OOS** : -0.49 bps/trade (après 14 bps) · 337 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.001 (SR=-0.007 vs seuil SR0=0.172) → NON significatif ⚠️
- Breadth : 3/8 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | +3.5 | +2.0 | +0.5 | -1.0 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| INJ | +26.17 |
| KAITO | +5.45 |
| WLD | +3.18 |
| OP | -0.70 |
| APE | -3.43 |
| ARB | -5.47 |
| BANANA | -6.78 |
| COMP | -14.75 |

### Heatmap robustesse — horizon_s (lignes) × decile (cols), AvgNet_bps OOS

| horizon_s\decile | 0.05 | 0.1 | 0.2 |
|---|---|---|---|
| 60 | -5.3 | -5.8 | -6.1 |
| 120 | -4.8 | -5.6 | -5.4 |
| 300 | -2.7 | **-0.5** | -0.7 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*