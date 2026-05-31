# Verdict OOS — BTC_5MIN_BINARY

*Intervalle 1s · confidence données **LOW** · 9 configs testées · 1 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-4.32) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-4.82) ; edge déflaté ≤ seuil multiple-testing (SR=-0.279 ≤ SR0=0.505) ; breadth insuffisante (0/1 coins > 0, requis ≥1)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.00) — résultat à confirmer ; données LOW (ex. 1m ~5j) — GO provisoire au mieux

## Métriques (meilleur config, OOS purgé)

- Params : `{'horizon_s': 300, 'decile': 0.1}`
- **AvgNet_bps OOS** : -4.32 bps/trade (après 14 bps) · 136 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.000 (SR=-0.279 vs seuil SR0=0.505) → NON significatif ⚠️
- Breadth : 0/1 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | -0.3 | -1.8 | -3.3 | -4.8 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| BTC | -4.32 |

### Heatmap robustesse — horizon_s (lignes) × decile (cols), AvgNet_bps OOS

| horizon_s\decile | 0.05 | 0.1 | 0.2 |
|---|---|---|---|
| 60 | -5.3 | -5.3 | -5.6 |
| 120 | -5.0 | -5.2 | -5.4 |
| 300 | -5.0 | **-4.3** | -4.5 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*