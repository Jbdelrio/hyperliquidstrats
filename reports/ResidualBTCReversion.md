# Verdict OOS — ResidualBTCReversion

*Intervalle 1h · confidence données **MEDIUM** · 9 configs testées · 19 coins (TOP 20)*

## ❌ **NO-GO**

**Raisons du rejet :** AvgNet_bps OOS ≤ 0 (-7.79) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-8.79) ; edge déflaté ≤ seuil multiple-testing (SR=-0.016 ≤ SR0=0.071)

**Avertissements :** NON significatif à 95% après déflation (DSR=0.16) — résultat à confirmer

## Métriques (meilleur config, OOS purgé)

- Params : `{'beta_window': 120, 'z_window': 48, 'z_entry': 2.5, 'horizon_bars': 12}`
- **AvgNet_bps OOS** : -7.79 bps/trade (après 14 bps) · 128 trades OOS
- Plateau paramétrique : non ❌ (pic isolé)
- Deflated Sharpe : DSR=0.163 (SR=-0.016 vs seuil SR0=0.071) → NON significatif ⚠️
- Breadth : 7/19 coins à AvgNet>0

### Stress de coût (AvgNet_bps OOS par coût RT)

| RT bps | 6 | 9 | 12 | 15 |
|---|---|---|---|---|
| AvgNet | +0.2 | -2.8 | -5.8 | -8.8 |

### AvgNet_bps OOS par coin

| Coin | AvgNet bps |
|---|---:|
| ZEC | +915.71 |
| ONDO | +184.51 |
| HYPE | +183.73 |
| XLM | +166.50 |
| WLD | +154.34 |
| ASTER | +93.41 |
| XRP | +15.60 |
| DOGE | -7.90 |
| FET | -11.29 |
| SUI | -20.87 |
| ETH | -21.52 |
| TON | -27.15 |
| XMR | -47.89 |
| SOL | -53.35 |
| TAO | -106.95 |
| BNB | -112.11 |
| LIT | -173.82 |
| NEAR | -189.77 |
| VVV | -403.48 |

### Heatmap robustesse — z_entry (lignes) × horizon_bars (cols), AvgNet_bps OOS

| z_entry\horizon_bars | 4 | 12 | 24 |
|---|---|---|---|
| 1.5 | -24.9 | -43.3 | -60.3 |
| 2.0 | -50.8 | -60.4 | -105.1 |
| 2.5 | -30.2 | **-7.8** | -39.3 |

*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. Confidence LOW (1m) ⇒ GO provisoire.*