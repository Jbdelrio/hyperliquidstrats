# Hyperliquid top-20 — comportement des coins & calibration

*2026-05-30T15:03:07 · 5000 bars max/TF · cost ref 6.0bps RT*

`typ_move` = médiane |retour close-to-close| (bps). `tradeable` = % de barres dont |move| > coût. `AC1` = autocorr lag-1 (+momentum/−reversion). `VR5` = variance ratio (>1 trend, <1 revert). `ER` = efficiency ratio (haut=directionnel). `H` = Hurst (>0.5 trend).


## Timeframe 1m

| Coin | maxLev | typ_move bps | vol bps | tradeable | AC1 | VR5 | ER | H | tag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC | 40x | 1.9 | 4.8 | ✗ 14% | -0.000 | 1.02 | 0.25 | 0.49 | NOISE |
| HYPE | 10x | 9.0 | 16.0 | ✓ 65% | -0.001 | 0.97 | 0.23 | 0.49 | NOISE |
| ETH | 25x | 2.5 | 6.2 | ✗ 21% | -0.008 | 1.01 | 0.23 | 0.50 | NOISE |
| ZEC | 10x | 9.2 | 17.9 | ✓ 65% | -0.005 | 0.97 | 0.22 | 0.49 | NOISE |
| SOL | 20x | 3.4 | 7.1 | ✗ 28% | +0.019 | 0.99 | 0.23 | 0.47 | NOISE |
| NEAR | 10x | 12.9 | 23.3 | ✓ 74% | +0.007 | 0.95 | 0.21 | 0.46 | NOISE |
| LIT | 5x | 12.5 | 29.3 | ✓ 73% | -0.025 | 0.90 | 0.23 | 0.48 | REVERT |
| XLM | 5x | 19.4 | 37.7 | ✓ 78% | -0.055 | 0.88 | 0.22 | 0.48 | REVERT |
| XRP | 20x | 3.1 | 6.9 | ✗ 28% | +0.016 | 1.04 | 0.24 | 0.51 | NOISE |
| XMR | 5x | 6.3 | 15.4 | ✓ 51% | +0.007 | 0.95 | 0.25 | 0.53 | NOISE |
| SUI | 10x | 5.6 | 11.0 | ✗ 47% | -0.003 | 0.97 | 0.22 | 0.49 | NOISE |
| XPL | 10x | 12.1 | 23.5 | ✓ 72% | -0.002 | 0.96 | 0.23 | 0.51 | NOISE |
| WLD | 10x | 12.4 | 23.0 | ✓ 73% | +0.001 | 0.94 | 0.22 | 0.48 | NOISE |
| INJ | 5x | 13.5 | 25.1 | ✓ 75% | -0.034 | 0.94 | 0.22 | 0.49 | REVERT |
| BNB | 10x | 2.2 | 4.8 | ✗ 15% | +0.041 | 1.08 | 0.26 | 0.50 | TREND |
| VVV | 3x | 11.1 | 21.3 | ✓ 69% | +0.033 | 1.02 | 0.23 | 0.52 | TREND |
| TON | 10x | 6.3 | 12.3 | ✓ 52% | -0.022 | 0.95 | 0.22 | 0.47 | NOISE |
| DOGE | 10x | 3.6 | 7.4 | ✗ 28% | +0.016 | 1.02 | 0.24 | 0.49 | NOISE |
| TAO | 5x | 6.5 | 12.6 | ✓ 53% | -0.014 | 0.93 | 0.23 | 0.50 | NOISE |
| ASTER | 5x | 2.8 | 7.2 | ✗ 25% | -0.030 | 0.93 | 0.24 | 0.47 | NOISE |

## Timeframe 15m

| Coin | maxLev | typ_move bps | vol bps | tradeable | AC1 | VR5 | ER | H | tag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC | 40x | 8.7 | 18.3 | ✓ 64% | -0.028 | 0.93 | 0.21 | 0.47 | NOISE |
| HYPE | 10x | 22.9 | 43.8 | ✓ 85% | -0.013 | 0.97 | 0.22 | 0.53 | NOISE |
| ETH | 25x | 10.8 | 23.6 | ✓ 70% | -0.001 | 0.99 | 0.22 | 0.49 | NOISE |
| ZEC | 10x | 31.5 | 61.1 | ✓ 90% | -0.011 | 0.99 | 0.21 | 0.53 | NOISE |
| SOL | 20x | 12.3 | 24.8 | ✓ 73% | -0.012 | 0.99 | 0.22 | 0.49 | NOISE |
| NEAR | 10x | 23.0 | 52.2 | ✓ 85% | +0.009 | 0.96 | 0.21 | 0.53 | NOISE |
| LIT | 5x | 33.5 | 76.8 | ✓ 90% | -0.048 | 0.88 | 0.22 | 0.46 | REVERT |
| XLM | 5x | 14.9 | 43.8 | ✓ 77% | +0.012 | 1.02 | 0.23 | 0.55 | TREND |
| XRP | 20x | 11.0 | 22.4 | ✓ 70% | -0.009 | 0.99 | 0.22 | 0.47 | NOISE |
| XMR | 5x | 19.5 | 38.7 | ✓ 82% | -0.007 | 0.96 | 0.21 | 0.45 | REVERT |
| SUI | 10x | 18.9 | 40.5 | ✓ 82% | +0.001 | 1.00 | 0.22 | 0.53 | NOISE |
| XPL | 10x | 29.1 | 57.0 | ✓ 88% | -0.015 | 0.95 | 0.22 | 0.49 | NOISE |
| WLD | 10x | 25.0 | 55.4 | ✓ 87% | -0.036 | 0.95 | 0.22 | 0.54 | REVERT |
| INJ | 5x | 26.5 | 57.2 | ✓ 87% | +0.027 | 1.07 | 0.22 | 0.47 | NOISE |
| BNB | 10x | 8.9 | 17.7 | ✓ 64% | -0.020 | 0.97 | 0.22 | 0.49 | NOISE |
| VVV | 3x | 40.7 | 78.5 | ✓ 91% | -0.003 | 1.06 | 0.22 | 0.46 | NOISE |
| TON | 10x | 23.8 | 62.8 | ✓ 85% | -0.026 | 0.95 | 0.22 | 0.51 | NOISE |
| DOGE | 10x | 14.4 | 29.3 | ✓ 78% | +0.010 | 1.00 | 0.21 | 0.46 | NOISE |
| TAO | 5x | 24.3 | 46.7 | ✓ 86% | +0.025 | 1.05 | 0.21 | 0.51 | NOISE |
| ASTER | 5x | 12.7 | 29.2 | ✓ 72% | -0.033 | 0.92 | 0.21 | 0.43 | REVERT |

## Timeframe 1h

| Coin | maxLev | typ_move bps | vol bps | tradeable | AC1 | VR5 | ER | H | tag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC | 40x | 21.2 | 49.4 | ✓ 84% | +0.005 | 1.03 | 0.23 | 0.48 | NOISE |
| HYPE | 10x | 54.7 | 105.5 | ✓ 94% | -0.016 | 0.95 | 0.23 | 0.49 | NOISE |
| ETH | 25x | 27.1 | 67.8 | ✓ 87% | +0.026 | 1.06 | 0.24 | 0.48 | NOISE |
| ZEC | 10x | 67.4 | 148.0 | ✓ 95% | +0.006 | 1.01 | 0.25 | 0.53 | NOISE |
| SOL | 20x | 31.3 | 73.2 | ✓ 89% | +0.020 | 1.08 | 0.24 | 0.46 | NOISE |
| NEAR | 10x | 47.6 | 101.6 | ✓ 93% | -0.005 | 1.04 | 0.23 | 0.52 | NOISE |
| LIT | 5x | 77.6 | 160.5 | ✓ 95% | -0.040 | 0.83 | 0.22 | 0.47 | REVERT |
| XLM | 5x | 36.7 | 81.2 | ✓ 91% | +0.020 | 1.08 | 0.24 | 0.49 | NOISE |
| XRP | 20x | 29.7 | 67.9 | ✓ 89% | +0.018 | 1.07 | 0.24 | 0.46 | NOISE |
| XMR | 5x | 45.9 | 93.1 | ✓ 93% | -0.042 | 0.88 | 0.21 | 0.51 | REVERT |
| SUI | 10x | 41.5 | 93.1 | ✓ 92% | +0.016 | 1.07 | 0.24 | 0.49 | NOISE |
| XPL | 10x | 70.5 | 145.9 | ✓ 96% | -0.043 | 0.92 | 0.24 | 0.45 | REVERT |
| WLD | 10x | 49.1 | 105.6 | ✓ 93% | +0.045 | 1.05 | 0.24 | 0.47 | TREND |
| INJ | 5x | 49.4 | 105.9 | ✓ 93% | +0.005 | 0.99 | 0.23 | 0.45 | NOISE |
| BNB | 10x | 22.9 | 50.5 | ✓ 85% | +0.009 | 1.02 | 0.23 | 0.48 | NOISE |
| VVV | 3x | 78.2 | 163.3 | ✓ 95% | +0.034 | 1.09 | 0.24 | 0.49 | TREND |
| TON | 10x | 37.4 | 85.7 | ✓ 91% | -0.001 | 0.99 | 0.22 | 0.57 | TREND |
| DOGE | 10x | 32.9 | 75.4 | ✓ 90% | +0.005 | 1.05 | 0.24 | 0.46 | NOISE |
| TAO | 5x | 55.8 | 107.3 | ✓ 94% | +0.001 | 0.99 | 0.22 | 0.49 | NOISE |
| ASTER | 5x | 39.4 | 96.4 | ✓ 90% | -0.050 | 0.86 | 0.20 | 0.45 | REVERT |

## Recommandation par coin

| Coin | vol24 $M | maxLev | Reco |
|---|---:|---:|---|
| BTC | 2165 | 40x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| HYPE | 1402 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| ETH | 899 | 25x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| ZEC | 196 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| SOL | 146 | 20x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| NEAR | 121 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| LIT | 66 | 5x | MEAN-REVERSION @ 1h (move 77.6bps, AC1 -0.040, VR5 0.83). RSI/Bollinger or decile-reversal. |
| XLM | 62 | 5x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| XRP | 62 | 20x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| XMR | 34 | 5x | MEAN-REVERSION @ 1h (move 45.9bps, AC1 -0.042, VR5 0.88). RSI/Bollinger or decile-reversal. |
| SUI | 21 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| XPL | 18 | 10x | MEAN-REVERSION @ 1h (move 70.5bps, AC1 -0.043, VR5 0.92). RSI/Bollinger or decile-reversal. |
| WLD | 15 | 10x | MOMENTUM/BREAKOUT @ 1h (move 49.1bps, AC1 +0.045, ER 0.24). Lev ≤ 10x. |
| INJ | 15 | 5x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| BNB | 13 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| VVV | 13 | 3x | MOMENTUM/BREAKOUT @ 1h (move 78.2bps, AC1 +0.034, ER 0.24). Lev ≤ 3x. |
| TON | 13 | 10x | MOMENTUM/BREAKOUT @ 1h (move 37.4bps, AC1 -0.001, ER 0.22). Lev ≤ 10x. |
| DOGE | 13 | 10x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| TAO | 11 | 5x | NOISE @ 1h — microstructure-only (obi/microprice), maker-first; no bar-level edge. |
| ASTER | 10 | 5x | MEAN-REVERSION @ 1h (move 39.4bps, AC1 -0.050, VR5 0.86). RSI/Bollinger or decile-reversal. |

## Hypothèses

- **Trending @1h** (momentum/breakout candidats) : WLD, VVV, TON
- **Mean-reverting @1h** (reversion candidats) : LIT, XMR, XPL, ASTER
- **Mouvement < coût sur tous les TF** (à éviter sans maker) : —
- Le `maxLev` HL plafonne le levier réalisable : BTC 40x, ETH 25x, alts 5-10x. Tout backtest au-delà est théorique.
- Plus le TF est court, plus `typ_move` rétrécit vers le coût → l'edge directionnel à 1m est presque toujours mangé par les frais ; viser 15m-1h pour le bar-trading, et la microstructure (sub-seconde) seulement en maker.