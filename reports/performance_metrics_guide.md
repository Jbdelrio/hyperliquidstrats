# Guide des métriques de suivi de performance — paper H1Breakout_ZEC

*2026-05-30 · à lire avant/pendant le paper run*

## Où sont loguées les métriques

| Fichier | Granularité | Contenu | Écrit par |
|---|---|---|---|
| `metrics_v9/metrics_v9.csv` | 1 ligne / minute | équity, pnl_min/hour/day, win_rate, avg_hold, wins/losses/stops/tps/max_holds | `monitoring/pnl_tracker.py` |
| `logs/fills_v9.csv` | 1 ligne / trade fermé | symbol, side, notional, entry, exit, **gross**, fee, **net**, hold_s, reason, strategy, slippage_bps, total_fees_usd, exit_mode | moteur à la clôture |
| `logs/decisions_v9.csv` | 1 ligne / décision | skips, places, blocks + raison, expected_net_profit | `monitoring/decision_logger.py` |

**Rapport agrégé** (à lancer quand tu veux pendant le run) :
```bash
python scripts/analyze_strategy_performance.py --fills logs/fills_v9.csv --out reports/strategy_performance.md
```
→ produit PnL/WR/expectancy/profit factor/**AvgGross**/slippage/fees **par stratégie et par symbole**, par régime de marché, et l'effet des adaptations de paramètres.

## Les métriques, expliquées

- **Net PnL** — somme des `net` (gross − frais). Le résultat final, mais bruité sur peu de trades.
- **AvgGross / trade (bps)** ⭐ — `gross / notional × 1e4`, moyenné. **C'est LE prédicteur honnête** : c'est l'edge brut du signal, *avant* frais. Sur le run paper précédent (AlphaDecile), c'est la seule métrique qui prédisait la rentabilité — le net était trop bruité. **Règle : AvgGross doit dépasser le coût aller-retour pour être net-positif.** Pour ZEC en taker, coût ≈ **9 bps** → il faut AvgGross_live > ~9-10 bps.
- **Expectancy / trade** — `net` moyen. Positif = gagnant en moyenne après frais.
- **Win rate** — % de trades net>0. ⚠️ Pour un breakout, **48-50% est NORMAL et sain** : l'edge vient de l'asymétrie (gagnants > perdants), pas de la fréquence de victoire. Ne panique pas sur un WR < 50%.
- **Profit factor** — `Σ gains / Σ pertes`. >1 rentable ; >1.3 correct pour du breakout.
- **Max drawdown** — pire creux cumulé. À surveiller vs ton capital (500 $).
- **Avg fees / trade** & **Fees / (|net|+fees)** — part des frais. Si les frais mangent >50% de l'edge, le signal est trop fin.
- **Avg slippage bps** — écart entre prix attendu et exécuté. Sur un breakout taker, surveille-le : s'il explose, l'edge backtest (qui suppose fill au close) est optimiste.
- **Trades / hour** — fréquence. ZEC backtest ≈ 2.5 trades/jour (hold 4h).
- **Exit mode** (`time` / `stop_loss` / `take_profit`) — répartition des sorties. ZEC est surtout time-stop (4h) ; beaucoup de `stop_loss` = le marché te sort avant l'horizon (mauvais signe).

## Référence backtest ZEC (taker, 9 bps, 5x) — à comparer au live

| Métrique | Valeur backtest |
|---|---|
| Trades | 517 (≈ 2.5 / jour) |
| Total net | +12 084 bps |
| Train / Test (60/40) | +6 012 / +6 072 (OOS+) |
| **AvgGross attendu** | ≈ **+32 bps/trade** (net +23 après 9 bps) |
| Win rate | 48% |
| Rendement compte (5x, $100 marge) | +18.5% sur ~208 j |
| Robustesse coût | OOS+ à 6/9/12/**15** bps |

## Tripwires (quand couper / garder)

- ✅ **Garder** si après ≥ 30 trades : **AvgGross_live ≥ 10 bps** ET net cumulé > 0 ET pas de drawdown > ~15% du capital.
- ⚠️ **Surveiller** si AvgGross entre 5 et 10 bps (l'edge se dégrade vers le coût) ou slippage moyen > 5 bps.
- ❌ **Couper** si AvgGross_live < coût (≈9 bps) sur ≥ 50 trades — le signal ne survit pas live, exactement comme les 4 AlphaDecile désactivés. Le `kill_after_consecutive_losses: 15` du preset coupe déjà automatiquement les séries noires.

> Pourquoi AvgGross et pas le net : le net sur 30 trades est dominé par 2-3 gros mouvements (variance énorme). AvgGross mesure l'edge moyen avec beaucoup moins de bruit — il converge plus vite vers la vérité. Si AvgGross > coût de façon stable, le net suivra mécaniquement.
