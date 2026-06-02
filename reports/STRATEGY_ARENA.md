# Strategy Arena — comparateur & sélecteur live

*2026-06-02T15:52:24.379163+00:00 · contrainte 500 $/strat*

**Règle d'éligibilité live** : LIVE_READY = OOS GO ET AvgGross live ≥ 9.0 bps sur ≥ 30 trades.

## 🎯 Recommandation
AUCUNE strat éligible live (aucune n'est à la fois GO en OOS ET AvgGross live ≥ coût). → tout reste en PAPER. Ne PAS promouvoir sur du PnL paper seul.

## Classement

| Strat | Statut | OOS | OOS AvgNet | DSR | Live n | Live AvgGross | Live net | WR |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Breakout 1h ZEC | 🟡 PAPER | 🟡 GO* | 70.27 | 0.86 | 0 | — | — | — |
| Breakout 1h univers | 🔴 REJECT | ❌ NO-GO | 5.51 | 0.19 | 0 | — | — | — |
| Décile (maker) | 🔴 REJECT | ❌ NO-GO | -0.49 | 0.0 | 0 | — | — | — |
| Binaire BTC 5x | 🔴 REJECT | ❌ NO-GO | -4.32 | 0.0 | 0 | — | — | — |
| Réversion cascade 15m | 🔴 REJECT | ❌ NO-GO | -6.13 | 0.05 | 0 | — | — | — |
| Réversion résidu/BTC 1h | 🔴 REJECT | ❌ NO-GO | -7.79 | 0.16 | 0 | — | — | — |
| Décile (taker) | 🔴 REJECT | ❌ NO-GO | -8.7 | 0.0 | 0 | — | — | — |
| Réversion funding 1h | 🔴 REJECT | ❌ NO-GO | -8.75 | 0.07 | 0 | — | — | — |
| Réversion transversale 1h | 🔴 REJECT | ❌ NO-GO | -9.05 | 0.02 | 0 | — | — | — |
| Trend EMA 4h | 🔴 REJECT | ❌ NO-GO | -73.54 | 0.03 | 0 | — | — | — |

*🟡 GO\* = GO mais non significatif à 95% (à confirmer). Une strat REJECT (NO-GO OOS) n'est jamais promue, quel que soit son PnL paper — c'est la garde anti-overfit. AvgGross live est le prédicteur honnête (le net est bruité).*