# Backtest historique — 5 stratégies barres directionnelles

*Généré 2026-05-23T20:42:22*

- **Période** : 180 jours, 1h candles
- **Symboles** : BTC, ETH, SOL, AVAX
- **Capital initial** : $500 par stratégie
- **Coûts** : fee 3.0 bps + slippage 4.0 bps par côté → 14 bps round-trip
- **Source de prix** : Binance (proxy Hyperliquid sur majors — basis < 5 bps en moyenne)

## Récap synthétique

| Stratégie | Trades | Net PnL | WR | Profit factor | Sharpe | Max DD | Sign-consistent (train/test) | Verdict |
|---|---|---|---|---|---|---|---|---|
| MomentumLS | 0 | $+0.00 | 0.0% | 0.00 | 0.00 | $0.00 | ✗  ($+0.0 / $+0.0) | ❓ too_few_trades |
| BreakoutControlled | 0 | $+0.00 | 0.0% | 0.00 | 0.00 | $0.00 | ✗  ($+0.0 / $+0.0) | ❓ too_few_trades |
| DonchianTrend | 10 | $-16.26 | 20.0% | 0.43 | -0.12 | $19.47 | ✓  ($-6.3 / $-9.9) | ❓ too_few_trades |
| VolatilityRegimeBreakout | 790 | $-128.51 | 43.0% | 0.91 | -0.07 | $353.59 | ✓  ($-80.0 / $-48.5) | ❌ negative |
| RSIBollingerReversion | 0 | $+0.00 | 0.0% | 0.00 | 0.00 | $0.00 | ✗  ($+0.0 / $+0.0) | ❓ too_few_trades |

## Détails par stratégie


### MomentumLS

- Trades : **0** (W 0 / L 0)
- Net PnL : **$+0.00**
- Win rate : 0.0%
- Profit factor : 0.00
- Sharpe-like : 0.00
- Max drawdown : $0.00
- Avg hold : 0.0 h, 0.00 trades / jour
- Walk-forward (60/40) : train $+0.00 (0 trades) / test $+0.00 (0 trades) — signe incohérent ✗
- Verdict : **❓ too_few_trades**

### BreakoutControlled

- Trades : **0** (W 0 / L 0)
- Net PnL : **$+0.00**
- Win rate : 0.0%
- Profit factor : 0.00
- Sharpe-like : 0.00
- Max drawdown : $0.00
- Avg hold : 0.0 h, 0.00 trades / jour
- Walk-forward (60/40) : train $+0.00 (0 trades) / test $+0.00 (0 trades) — signe incohérent ✗
- Verdict : **❓ too_few_trades**

### DonchianTrend

- Trades : **10** (W 2 / L 8)
- Net PnL : **$-16.26**
- Win rate : 20.0%
- Profit factor : 0.43
- Sharpe-like : -0.12
- Max drawdown : $19.47
- Avg hold : 12.4 h, 0.08 trades / jour
- Walk-forward (60/40) : train $-6.31 (7 trades) / test $-9.94 (3 trades) — **signe cohérent ✓**
- Verdict : **❓ too_few_trades**
- Sorties : stop_loss=8, take_profit=2
- PnL par symbole : BTC=$-3.55, ETH=$-3.55, SOL=$-9.15

### VolatilityRegimeBreakout

- Trades : **790** (W 340 / L 450)
- Net PnL : **$-128.51**
- Win rate : 43.0%
- Profit factor : 0.91
- Sharpe-like : -0.07
- Max drawdown : $353.59
- Avg hold : 6.0 h, 4.42 trades / jour
- Walk-forward (60/40) : train $-80.01 (475 trades) / test $-48.50 (315 trades) — **signe cohérent ✓**
- Verdict : **❌ negative**
- Sorties : max_hold=789, eob_flush=1
- PnL par symbole : AVAX=$+15.26, SOL=$-34.38, BTC=$-52.10, ETH=$-57.29

### RSIBollingerReversion

- Trades : **0** (W 0 / L 0)
- Net PnL : **$+0.00**
- Win rate : 0.0%
- Profit factor : 0.00
- Sharpe-like : 0.00
- Max drawdown : $0.00
- Avg hold : 0.0 h, 0.00 trades / jour
- Walk-forward (60/40) : train $+0.00 (0 trades) / test $+0.00 (0 trades) — signe incohérent ✗
- Verdict : **❓ too_few_trades**

## Interprétation

Une stratégie 'se confirme' en backtest si **tous** ces critères tiennent : net PnL > 0, profit factor > 1.2, Sharpe > 1.0, train et test du même signe, ≥ 20 trades. Une seule métrique en rouge = prudence. Deux = pas de confirmation.

**Attention** : un backtest positif sur 180 j de Binance 1 h ≠ garantie de PnL paper/live sur Hyperliquid. Les majors traquent à quelques bps, mais l'exécution réelle (latence, profondeur de carnet, partial fills) peut dégrader le PnL. Comparer avec ce que le moteur paper produit sur la même période est l'étape suivante.