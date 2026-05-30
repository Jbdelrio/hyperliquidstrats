# Backtest historique — 5 stratégies barres directionnelles

*Généré 2026-05-30T11:46:18*

- **Période** : 90 jours, 15m candles
- **Symboles** : BTC, ETH, SOL, AVAX
- **Capital initial** : $500 par stratégie
- **Coûts** : fee 3.0 bps + slippage 4.0 bps par côté → 14 bps round-trip
- **Source de prix** : Binance (proxy Hyperliquid sur majors — basis < 5 bps en moyenne)

## Récap synthétique

| Stratégie | Trades | Net PnL | WR | Profit factor | Sharpe | Max DD | Sign-consistent (train/test) | Verdict |
|---|---|---|---|---|---|---|---|---|
| MomentumLS | 0 | $+0.00 | 0.0% | 0.00 | 0.00 | $0.00 | ✗  ($+0.0 / $+0.0) | ❓ too_few_trades |
| BreakoutControlled | 0 | $+0.00 | 0.0% | 0.00 | 0.00 | $0.00 | ✗  ($+0.0 / $+0.0) | ❓ too_few_trades |
| DonchianTrend | 38 | $-85.38 | 10.5% | 0.25 | -0.53 | $98.01 | ✓  ($-47.5 / $-37.9) | ❌ negative |
| VolatilityRegimeBreakout | 562 | $-240.70 | 40.2% | 0.75 | -0.26 | $245.45 | ✓  ($-87.6 / $-153.1) | ❌ negative |
| RSIBollingerReversion | 1 | $+5.56 | 100.0% | 0.00 | 0.00 | $0.00 | ✗  ($+5.6 / $+0.0) | ❓ too_few_trades |

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

- Trades : **38** (W 4 / L 34)
- Net PnL : **$-85.38**
- Win rate : 10.5%
- Profit factor : 0.25
- Sharpe-like : -0.53
- Max drawdown : $98.01
- Avg hold : 10.2 h, 0.61 trades / jour
- Walk-forward (60/40) : train $-47.47 (23 trades) / test $-37.91 (15 trades) — **signe cohérent ✓**
- Verdict : **❌ negative**
- Sorties : stop_loss=33, take_profit=4, max_hold=1
- PnL par symbole : SOL=$-1.17, BTC=$-24.33, ETH=$-28.03, AVAX=$-31.84

### VolatilityRegimeBreakout

- Trades : **562** (W 226 / L 336)
- Net PnL : **$-240.70**
- Win rate : 40.2%
- Profit factor : 0.75
- Sharpe-like : -0.26
- Max drawdown : $245.45
- Avg hold : 6.0 h, 6.28 trades / jour
- Walk-forward (60/40) : train $-87.60 (338 trades) / test $-153.10 (224 trades) — **signe cohérent ✓**
- Verdict : **❌ negative**
- Sorties : max_hold=562
- PnL par symbole : ETH=$-21.49, AVAX=$-40.38, BTC=$-59.13, SOL=$-119.71

### RSIBollingerReversion

- Trades : **1** (W 1 / L 0)
- Net PnL : **$+5.56**
- Win rate : 100.0%
- Profit factor : 0.00
- Sharpe-like : 0.00
- Max drawdown : $0.00
- Avg hold : 4.2 h, 1000000.00 trades / jour
- Walk-forward (60/40) : train $+5.56 (1 trades) / test $+0.00 (0 trades) — signe incohérent ✗
- Verdict : **❓ too_few_trades**
- Sorties : take_profit=1
- PnL par symbole : BTC=$+5.56

## Interprétation

Une stratégie 'se confirme' en backtest si **tous** ces critères tiennent : net PnL > 0, profit factor > 1.2, Sharpe > 1.0, train et test du même signe, ≥ 20 trades. Une seule métrique en rouge = prudence. Deux = pas de confirmation.

**Attention** : un backtest positif sur 180 j de Binance 1 h ≠ garantie de PnL paper/live sur Hyperliquid. Les majors traquent à quelques bps, mais l'exécution réelle (latence, profondeur de carnet, partial fills) peut dégrader le PnL. Comparer avec ce que le moteur paper produit sur la même période est l'étape suivante.