# Data Collection — Seconds Features

## 1. WebSocket-first

Toute la collecte microstructure passe par le WebSocket Hyperliquid
(`wss://api.hyperliquid.xyz/ws`). Pas un appel REST n'est nécessaire
côté book/trade :

- `data/orderbook_manager.py` souscrit `l2Book` + `trades` par symbole,
- `data/seconds_feature_engine.py` se nourrit uniquement de ces flux,
- `monitoring/seconds_feature_logger.py` écrit dans `logs/seconds_features.csv`.

REST est réservé à : funding rates (basse fréquence), métadonnées,
account state. Le scanner funding rafraîchit au plus toutes les 60 s
(cf. `data/exchange_adapters/hyperliquid_funding.py`).

## 2. Ne pas spammer REST

Règle d'or :
- pas plus d'**un fetch funding toutes les 60 s** par exchange,
- jamais de REST dans `_orderbook_loop` ou `_trade_loop`,
- adapters mettent en cache et exposent `min_refresh_interval_s`.

Une violation = ban temporaire de l'exchange + désynchronisation des
features.

## 3. Univers initial restreint

Commencer avec **BTC, ETH, SOL, HYPE** uniquement. Raisons :

- liquidité suffisante pour des fills réalistes en paper,
- bandwidth WebSocket maîtrisé,
- BTC sert de référence pour la résidualisation,
- 4 symboles ⇒ 4 lignes / s ⇒ ~345 k lignes / jour ⇒ ~30 MB / jour CSV.

Élargir l'univers seulement quand le framework est stable.

## 4. Une ligne par seconde par symbole

Le logger est rate-limité à `log_interval_s` (défaut 1 s). Logger chaque
tick L2 produirait :
- des centaines de lignes/s par symbole,
- des fichiers de plusieurs GB par jour,
- du bruit pur (les ticks consécutifs sont quasi-identiques).

Une snapshot/s capture la dynamique microstructure sans saturer le disque.

## 5. Stale book detection

`SecondsFeatureEngine` marque `book_stale=true` si la dernière mise à
jour book a plus de `stale_book_s` (défaut 5 s) d'ancienneté. Les
stratégies de seconde **doivent** vérifier ce flag avant de trader.

Cas typiques où `book_stale` saute :
- coupure WebSocket en cours de reconnect,
- exchange en plein "circuit breaker",
- symbole illiquide sans trade ni MAJ pendant plusieurs s.

## 6. Data quality flags

| Flag | Signification |
|------|---------------|
| `enough_data` | ≥ 10 ticks ET ≥ `min_data_seconds` d'historique |
| `book_stale` | `now - last_book_ts > stale_book_s` |
| `last_update_age_s` | âge en s du dernier book |

Toute stratégie qui ignore ces flags va tôt ou tard trader sur un book
mort.

## 7. Limites mémoire

- buffers bornés à `max_history_seconds * symbols * (~50 ticks/s)`,
- prune par `_prune` à chaque update ⇒ pas de fuite,
- `z_spread_samples` / `z_rv_samples` cappées à 600 (10 minutes à 1 Hz).

Sur 4 symboles avec 300 s de buffer, l'usage mémoire reste sous quelques
dizaines de MB.

## 8. Vérification rapide

```
python engine_v9.py --paper --config config/presets/paper_500_alpha_research.json
# attendre 5 minutes minimum
ls -lh logs/seconds_features.csv
wc -l logs/seconds_features.csv
```

Attendu : ~300 lignes/symbole/5 min, donc ~1200 lignes après 5 min.

Si beaucoup moins : vérifier `engine_v9.log` pour `book_stale` ou erreurs
WebSocket. Si plus, le rate-limit est cassé — vérifier
`min_interval_s` du logger.
