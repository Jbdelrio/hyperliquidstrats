# Alpha Research Framework — Implementation Plan

Date : 2026-05-15
Branche : main
Statut : implémentation incrémentale, **paper-only**, jamais live.

## 1. Architecture existante (audit)

### Boucles asyncio dans `engine_v9.py`
- `_orderbook_loop` (ligne 424) : reçoit `BookUpdate`, met à jour OHLCV intra-min, déclenche `executor.check_fills`, dispatch `manager.on_orderbook_update`.
- `_trade_loop` (ligne 473) : reçoit `TradeEvent`, accumule volume bar, dispatch `manager.on_trade_update`.
- `_minute_loop` (ligne 487) : génère `BarData` et dispatch `on_bar_minute`.
- `_position_loop`, `_watchdog_loop`, `_dashboard_loop`, `_control_loop`, `_arbitrage_monitor_loop` : déjà en place.

### Données disponibles
- `data/orderbook_manager.py` : `OrderbookManager` expose `get_book`, `get_mid`, `get_trades`, `get_vwap`, `is_stale`, et stream async pour books + trades. WebSocket-first, REST jamais touché côté book/trade.
- `data/trades_buffer.py` : `TradesBuffer` deque taille fixe, `add`, `get_recent`, `get_vwap`, `__len__`. Pas de prune temporel ni d’aggrégation buy/sell.
- `data/hyperliquid_funding.py` : fetcher REST `metaAndAssetCtxs` retournant `{coin: {raw_8h, hourly_rate, hourly_bps}}`.

### Strategies
- `BaseStrategy` (`strategies/base_strategy.py`) : 3 méthodes abstraites (`on_orderbook_update`, `on_trade_update`, `on_bar_minute`) + crochets optionnels (`on_fill`, `check_position_exits`, `on_position_closed`, `get_calibration_data`).
- 14 stratégies enregistrées dans `_STRATEGY_CLASSES` (engine_v9.py ligne 58).
- `StrategyManager` dispatch les events. Pas encore de méthode `on_second_features`.

### Loggers
- `monitoring/decision_logger.py` : CSV thread-safe avec buffer/flush.
- `monitoring/pnl_tracker.py`.

### Tests existants
- ~25 fichiers `tests/test_*.py`. Tous passent (255/255 d'après les commits récents).

## 2. Plan d’implémentation (ordre)

| # | Livrable | Risque casse |
|---|----------|--------------|
| 1 | `docs/ALPHA_MODELS_THEORY.md` (théorie pure, zéro code) | nul |
| 2 | `data/trades_buffer.py` : ajout méthodes time-based (API existante préservée) | faible — couvert par tests |
| 3 | `data/seconds_feature_engine.py` (module isolé) | nul |
| 4 | `monitoring/seconds_feature_logger.py` (module isolé) | nul |
| 5 | `strategies/base_strategy.py` : ajout `on_second_features` non abstraite | faible |
| 6 | `engine_v9.py` : init `SecondsFeatureEngine` derrière flag config, hook book/trade, ajout `_seconds_loop`, register nouvelles strategies | moyen — vérifier que paper actuel ne casse pas |
| 7 | `strategies/seconds_research_strategy.py` (no-op) | nul |
| 8 | `strategies/alpha_pressure_scalper.py`, `book_flow_divergence_reversal.py`, `absorption_reversal.py` (disabled par défaut) | nul |
| 9 | `research/alpha_research_hyperliquid_seconds.ipynb` + `scripts/run_alpha_research.py` | nul |
| 10 | `data/funding_data.py`, `data/exchange_adapters/{hyperliquid,aster}_funding.py` | nul |
| 11 | `strategies/funding_arbitrage_enhanced.py` (research_only) + `risk/funding_risk_manager.py` + `monitoring/funding_logger.py` + `scripts/scan_funding_opportunities.py` | nul |
| 12 | `research/funding_arbitrage_research.ipynb` | nul |
| 13 | `config/presets/paper_500_alpha_research.json`, `paper_500_funding_research.json` | nul |
| 14 | Tests unitaires (≥9 fichiers) | nul |
| 15 | `docs/ALPHA_RESEARCH_FRAMEWORK.md`, `docs/DATA_COLLECTION_SECONDS.md`, MAJ `README.md` | nul |

## 3. Points d’intégration `engine_v9.py`

```python
# __init__:
sf_cfg = self.cfg.get("seconds_features", {}) or {}
self.seconds_features = None
self._seconds_logger = None
if sf_cfg.get("enabled", False):
    from data.seconds_feature_engine import SecondsFeatureEngine
    self.seconds_features = SecondsFeatureEngine(self.symbols, config=sf_cfg)
    if sf_cfg.get("log_enabled", True):
        from monitoring.seconds_feature_logger import SecondsFeatureLogger
        self._seconds_logger = SecondsFeatureLogger(
            path=sf_cfg.get("log_path", "logs/seconds_features.csv"),
            min_interval_s=sf_cfg.get("log_interval_s", 1.0),
        )

# _orderbook_loop:
if self.seconds_features is not None:
    self.seconds_features.update_from_book(sym, book, ts)

# _trade_loop:
if self.seconds_features is not None:
    self.seconds_features.update_from_trade(sym, event, event.timestamp)

# run():
await asyncio.gather(
    ...
    self._seconds_loop(),
)
```

`_seconds_loop` tourne toutes les `feature_interval_s` (défaut 1 s), récupère les features, log (avec rate-limit), puis dispatche aux strategies via `manager.on_second_features(symbol, features, ts)`.

## 4. Compatibilité

- Toutes les configs existantes (`paper_500_clean.json`, etc.) continuent de fonctionner — `seconds_features` absent ou `enabled=false`.
- Aucune stratégie existante n’est supprimée ni modifiée.
- `on_second_features` reste optionnelle.
- Aucun secret ne sera commit.
- Live execution reste `NotImplementedError` (cf. micro-live guard ligne 96-130).

## 5. Critères « signal candidat alpha »

Avant qu’un signal soit branché ne serait-ce qu’en paper trading :
1. Spearman IC stable et non nul sur plusieurs jours.
2. Bucket analysis monotone ou cohérente.
3. Profit factor net > 1 après frais + slippage.
4. Walk-forward positif (train≠test).
5. IC résiduel hors-bêta-BTC non nul.
6. Robuste sur ≥ 2 symboles.
7. Pas de dépendance à des fills irréalistes (best bid/ask only, jamais mieux que le top du book sans queue model).
8. PnL pas concentré sur 1 jour ou 1 coin.

Aucun de ces critères n’est vérifié *a priori* pour les modèles fournis : ce sont des **hypothèses** à tester via le notebook.
