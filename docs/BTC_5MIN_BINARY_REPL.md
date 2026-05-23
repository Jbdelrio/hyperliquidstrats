# BTC_5MIN_BINARY_REPL — réplication synthétique "BTC up/down 5 min"

Réplication directionnelle du format binaire "BTC up or down in 5 minutes"
(Polymarket / Kalshi) via le perp BTC :

- **Long perp** ≈ YES "up" sur 300 s
- **Short perp** ≈ YES "down" sur 300 s

Ce n'est **pas** un arbitrage risk-free. C'est une estimation probabiliste
règle-à-règle de `P(S_T > S_0)` sur 5 min, à partir de momentum court,
order-book imbalance, aggressor flow, volatilité réalisée et spread.

**PAPER uniquement, désactivée par défaut.** `enabled:false`, `paper_only:true`,
`live_enabled:false`.

---

## 1. Activer

Dans `config/presets/paper_500_clean.json`, stratégie `BTC_5MIN_BINARY_REPL` :
passer `"enabled": false` → `true`. Le moteur doit être redémarré pour
recharger la config (la GUI : ■ STOP puis ▶ DÉMARRER, sélecteur MOTEUR vide).

`seconds_features.enabled` doit rester `true` dans le preset — la stratégie est
alimentée par le hook `on_second_features` (1 Hz).

## 2. Lancer en paper

```
python engine_v9.py --paper --config config/presets/paper_500_clean.json
```

ou via la GUI (`launch_gui.bat`). La stratégie est paper-only ; le live est
verrouillé (`live_enabled:false`) et n'est pas implémenté.

## 3. Vérifier le warmup

La stratégie **ne trade jamais** avant 30 min de collecte (`min_warmup_seconds:
1800`). États (`model_quality`) :

| État | Warmup écoulé | Trade ? |
|---|---|---|
| COLD | < 10 min | non |
| WARMING | 10–30 min | non (signaux calculés/loggés) |
| READY | 30–60 min | **oui** |
| GOOD | > 60 min | oui |

Visible dans l'onglet **Calibration** de la GUI (`model_quality`, `warmup_pct`)
et dans `runtime/calibration_data.json`.

## 4. Lire les logs

| Fichier | Contenu |
|---|---|
| `logs/btc5min_features.csv` | Snapshot/seconde : mid, spread, obi, flow, rv, score, p_up, décision, **no_trade_reason** |
| `logs/btc5min_trades.csv` | Entrées + sorties : side, notional, margin, leverage, entry_px, net_pnl, raison |
| `logs/engine_v9.log` | Lignes `[BTC5MIN ALERT] …` (warmup, fills, exits, limites de risque) |

Quand elle ne trade pas, la stratégie renvoie **toujours** une raison :
`NO_TRADE_WARMUP`, `NO_TRADE_SPREAD_TOO_WIDE`, `NO_TRADE_NO_VOL`,
`NO_TRADE_SIGNAL_TOO_WEAK`, `NO_TRADE_OBI_NOT_ALIGNED`,
`NO_TRADE_FLOW_NOT_ALIGNED`, `NO_TRADE_DAILY_LOSS_LIMIT`,
`NO_TRADE_CONSECUTIVE_LOSS_LIMIT`, `NO_TRADE_MAX_TRADES_PER_HOUR`,
`NO_TRADE_COOLDOWN`, `NO_TRADE_DATA_STALE`, etc.

## 5. Paramètres à régler (`params` du preset)

**Plus agressif** (plus de trades) : baisser `long_threshold`/monter
`short_threshold` vers 0.50, baisser `min_obi_long`/`min_flow_long`, baisser
`min_rv_300s_bps`, monter `max_trades_per_hour`.

**Plus conservateur** : l'inverse — `long_threshold` 0.60+, `min_rv_300s_bps`
12+, `max_spread_bps` 1.0.

Sorties : `take_profit_usd`, `stop_loss_usd`, `max_holding_seconds`.
Risque : `max_daily_loss_usd`, `max_consecutive_losses`, `cooldown_*`.

## 6. Levier — lire avant de toucher

Défaut : `leverage:10`, `max_leverage:10`. La stratégie clampe
`leverage` à `max_leverage`.

**Dans le simulateur paper, le levier seul ne change PAS le PnL.** Le PnL =
`notional × (Δprix/prix)`. `10 $ de marge × 10x = 100 $ de notionnel` et
`100 $ de notionnel` donnent le **même** PnL. Le levier ne fait que fixer la
marge (`notional/levier`) et, en live, la distance de liquidation.

Ce qui augmente le PnL, c'est le **notionnel** — plafonné ici à 100 $.

**Pourquoi 10x est le plafond cohérent** — avec un stop de 1 $ :

| Levier | Notionnel (marge 10 $) | Distance du stop 1 $ | Verdict |
|---|---|---|---|
| 10x | 100 $ | 0.10 % | OK — au-dessus du bruit/spread |
| 50x | 500 $ | 0.02 % | stop ≈ bruit 1 s |
| 100x | 1000 $ | 0.01 % | **stop < spread → stop-out instantané** |

Le spread BTC ≈ 0.5–1.5 bps (0.005–0.015 %). À 100x, le stop de 1 $ est plus
serré que le spread : la position est coupée par le rebond bid/ask **avant**
qu'un mouvement se développe. Et en live, à 100x, un mouvement adverse de 1 %
(BTC le fait en 5 min couramment) = 10 $ = toute la marge = **liquidation**.

50-100x ne donne donc pas « petit risque, +2 $ rapide » — ça donne « stoppé par
le spread, ou liquidé par le bruit ordinaire ». Pour dépasser 10x il faut
monter `max_leverage` **et** `leverage` dans le preset, en connaissance de cause.

## 7. Limites actuelles de l'implémentation

- **Edge non validé.** Le scan IC (`reports/ic_quickscan.md`) a montré, sur les
  données de ce projet, que les signaux microstructure courts (obi, flow,
  returns) prédisent ~1-2 bps alors que le coût aller-retour ≈ 8-10 bps. Cette
  stratégie utilise exactement ces signaux à ces horizons → elle est très
  probablement à espérance négative. **À valider sur ses propres logs paper
  avant toute confiance.**
- Pas d'exécution live (verrouillée). Pas de couche multi-exchange dédiée :
  utilise le feed Hyperliquid du moteur.
- Fills / partial fills / post-only timeout : gérés par le `HighFreqExecutor`
  du moteur (MAKER_SIM/TAKER_SIM), pas réimplémentés.
- Le backtester du repo (`backtesting/`) n'est pas câblé à cette stratégie.
- Alertes = lignes de log `[BTC5MIN ALERT]` + champ `last_alert` en calibration.
  Pas d'alerte sonore.

## 8. TODO version suivante

- Valider/abandonner via le notebook de recherche d'alpha (IC, walk-forward,
  retrait du bêta-BTC) **avant** d'envisager le live.
- Câbler la stratégie au backtester pour mesurer expectancy / profit factor /
  drawdown sur historique 1s.
- Pondérations du score (`0.25/0.20/0.25/0.20/-0.10`) à recalibrer sur données,
  pas à l'intuition.
- Couche d'exécution live dédiée (séparée) si et seulement si le paper prouve
  une expectancy positive nette de coûts.
