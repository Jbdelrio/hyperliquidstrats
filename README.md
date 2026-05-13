# Artemisia v9 — Multi-Strategy Paper Trading Engine

Moteur de trading multi-stratégies en paper mode, connecté en temps réel à **Hyperliquid** via WebSocket. Architecture asyncio pure, 14 stratégies, cascade de filtres de risque, dashboard Dash, overlay LLM optionnel.

---

## Table des matières

1. [Architecture](#architecture)
2. [Installation](#installation)
3. [Démarrage rapide](#démarrage-rapide)
4. [Configuration](#configuration)
5. [Stratégies](#stratégies)
6. [Système de risque](#système-de-risque)
7. [Flux de données](#flux-de-données)
8. [Dashboard GUI](#dashboard-gui)
9. [Tests & Diagnostic](#tests--diagnostic)
10. [Limitations connues](#limitations-connues)
11. [Structure des fichiers](#structure-des-fichiers)

---

## Architecture

```
Hyperliquid WebSocket (wss://api.hyperliquid.xyz/ws)
        │
        ▼
 OrderbookManager  ─── reconnexion exponentielle 1s→64s
   │          │
  L2 books  Trades
        │
        ▼
    Engine V9  (event loop asyncio unique)
  ┌──────────────────────────────────────┐
  │  Loop A  orderbook  → fills + décisions      │
  │  Loop B  trades     → volume accumulation    │
  │  Loop C  bars 1min  → OHLCV + signaux       │
  │  Loop D  positions  → SL/TP/max_hold (500ms)│
  │  Loop E  watchdog   → network + BTC vol (5s)│
  │  Loop F  dashboard  → log terminal (60s)    │
  │  Loop G  control    → bus GUI (2s)          │
  └──────────────────────────────────────┘
        │
   Gate cascade (par ordre)
   1. StrategyCapitalLedger  (budget par stratégie)
   2. KillSwitch global      (DD total, streak, vol)
   3. ExecutionFilter        (min profit, R:R, cooldown)
   4. LLM Overlay (optionnel, désactivé par défaut)
        │
        ▼
  HighFreqExecutor (paper)
  → Simule les fills quand bid/ask touchent le prix
  → Clôture sur TP / SL / max_hold
```

**Paper mode uniquement.** Le mode live est bloqué (`NotImplementedError`). Aucun ordre réel n'est envoyé.

---

## Installation

### Prérequis

- Python 3.11+
- Windows 10/11 (testé) ou Linux/macOS

### Étapes

```bash
# 1. Cloner le repo
git clone <url>
cd artemisia_v9

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. (Optionnel) Configurer le .env pour le LLM
cp .env.example .env
# Éditer .env : ajouter LLM_ENABLED=true + clé API si LLM souhaité
```

### Dépendances principales

| Package | Usage |
|---------|-------|
| `websockets` | Connexion WebSocket Hyperliquid |
| `numpy` | Calculs indicateurs techniques |
| `dash` + `dash-bootstrap-components` | Dashboard GUI |
| `pydantic` | Validation schémas exchange |
| `pandas` | Analyse CSV logs |

---

## Démarrage rapide

### Option 1 — Terminal (direct)

```bash
# Paper mode avec preset 5 stratégies ($2500 total)
python engine_v9.py --paper --config config/presets/paper_500_clean.json

# Paper mode avec la config principale (14 stratégies, certaines désactivées)
python engine_v9.py --paper --config config_v9.json

# Forcer certaines stratégies seulement
python engine_v9.py --paper --config config_v9.json --strategy MomentumLS BreakoutControlled
```

### Option 2 — Dashboard GUI

```bash
# Démarrage normal
python -m gui.app

# Démarrage vierge (efface tous les anciens logs et métriques)
python -m gui.app --fresh
```

Ouvrir `http://127.0.0.1:8050` dans le navigateur.

Dans l'onglet **Stratégies** :
1. Choisir un preset dans le dropdown **PRESET** (ex. `Paper 500 Clean`)
2. Laisser le dropdown **MOTEUR** vide — la config décide quelles stratégies tournent
3. Cliquer **▶ DÉMARRER**

> **Toggles rapides** : un panel en haut de l'onglet Stratégies permet d'activer/désactiver chaque stratégie en un clic pendant que l'engine tourne. L'effet est visible au prochain refresh (~5s).

### Surveiller les logs en temps réel

```powershell
# Trades exécutés
Get-Content logs\fills_v9.csv -Wait -Tail 20

# Log moteur complet
Get-Content logs\engine_v9.log -Wait -Tail 40

# Dashboard terminal (imprimé toutes les 60s dans la console engine)
```

---

## Configuration

### Fichiers de config

| Fichier | Description |
|---------|-------------|
| `config_v9.json` | Config principale — 14 stratégies, certaines désactivées |
| `config/presets/paper_500_clean.json` | 5 stratégies directionnelles × $500 = $2500 |
| `config/presets/paper_500_per_strategy.json` | 5 actives + 9 inactives détaillées |

### Structure d'une config

```json
{
  "capital": 2500,
  "paper_mode": true,
  "websocket_url": "wss://api.hyperliquid.xyz/ws",
  "execution_filters": {
    "enabled": true,
    "min_expected_net_profit_usd": 3.0,
    "min_reward_risk_ratio": 1.4,
    "taker_fee_bps": 3.0,
    "slippage_bps": 4.0,
    "min_hold_seconds": 90,
    "cooldown_win_s": 300,
    "cooldown_loss_s": 900
  },
  "strategies": [
    {
      "name": "MomentumLS",
      "class": "MomentumLongShort",
      "enabled": true,
      "capital_allocated_usd": 500,
      "max_positions": 2,
      "max_position_size_usd": 250,
      "coins": ["BTC", "ETH", "SOL", ...],
      "params": { ... }
    }
  ],
  "risk": {
    "max_dd_daily_pct": 0.025,
    "max_dd_total_pct": 0.060,
    "max_open_positions": 10,
    "max_pick_rate": 0.85
  }
}
```

### Paramètre clé : PnL paper déterministe

En paper mode, le PnL par trade au TP est toujours le même si le notional et le TP% sont fixes :

```
PnL brut = notional × take_profit_pct
         = min(max_position_size_usd, capital) × tp_pct
         = 250 × 0.025 = $6.25  (pour BreakoutControlled dans paper_500_clean)
```

C'est **normal** : le simulateur paper clôture exactement au prix TP sans slippage. En live, le slippage et la profondeur du carnet introduiraient de la variance.

---

## Stratégies

### Vue d'ensemble

| Stratégie | Type | Signal | Warmup | État défaut |
|-----------|------|---------|--------|-------------|
| **MomentumLS** | Long/Short | Z-score cross-section (15m/1h/4h) | 3 bars | ✅ Actif |
| **BreakoutControlled** | Long | Cassure resistance + volume ratio | 12 bars | ✅ Actif |
| **DonchianTrend** | Long | Donchian upper + filtres EMA + BTC régime | 36 bars | ✅ Actif |
| **RSIBollingerReversion** | Long | RSI<35 + z-score BB < -1.5 + EMA | 100 bars | ✅ Actif |
| **VolatilityRegimeBreakout** | Long | Donchian + ATR > 30bps (high-vol only) | 20 bars | ✅ Actif |
| **S8EMS** | Market-making | Bouchaud impact + Kalman + wavelet | 30 bars | ❌ Désactivé |
| **MeanReversionKalman** | Long/Short | Kalman z-entry=1.5σ + trend_guard | 200 innovations | ❌ Désactivé |
| **FundingArbitrage** | Scanner | Funding > 0.03%/h | Immédiat | 🔍 Scanner |
| **FundingCarryHedged** | Scanner | Funding net de frais > 0.5bps/h | Immédiat | 🔍 Scanner |
| **SpotPerpBasis** | Scanner | Base spot/perp > 20bps | Immédiat | 🔍 Scanner |
| **RelativeValue** | Scanner | Z-score paires regression | 500 bars | 🔍 Scanner |
| **RotationMomentum** | Scanner | Ranking momentum 24h | Immédiat | 🔍 Scanner |
| **OBImbalanceScalper** | Scalping | Imbalance OB > 35%, spread < 8bps | 3 updates | ⚠️ Paper only |
| **MetaAlpha** | Agrégateur | Quorum de votes ≥ 2 pairs | Dépend pairs | ❌ Désactivé |

### Stratégies actives (paper_500_clean)

#### MomentumLS
Score composite : `0.4×z(r15m) + 0.4×z(r1h) + 0.2×z(r4h)`. Les top-K coins → LONG, bottom-K → SHORT. Filtre anti-pump : bloqué si variation 15m > 5%.

#### BreakoutControlled
Détecte une cassure de résistance (N-bar max) avec ratio de volume > 1. Stop sous la résistance, TP à +2.5%. Nécessite 12 barres d'historique.

#### DonchianTrend
Breakout Donchian 36 barres + confirmation 1h EMA + régime BTC 4h. Stop trailing (Donchian mid). Nécessite 36 barres ≈ **36 minutes** de warmup.

#### RSIBollingerReversion
RSI < 25 **ET** z-score Bollinger < -2.2 **ET** price > EMA-1h. Conditions rares — peut ne pas trader pendant des heures. Nécessite **100 barres** ≈ **1h40 de warmup**.

#### VolatilityRegimeBreakout
Breakout Donchian uniquement quand ATR > 35bps (marché actif). Évite les faux breakouts en basse volatilité.

### Stratégies désactivées par défaut

Ces stratégies sont en mode **scanner** (calibration visible mais pas de trades) ou **désactivées** car elles nécessitent :
- **FundingArbitrage / FundingCarryHedged** : une position spot pour hedger (`allow_unhedged_perp=false`)
- **SpotPerpBasis** : un flux de prix spot externe (`external_spot_prices={}`)
- **RelativeValue** : 500 barres de warmup ≈ 8h30 + hedge calculé
- **S8EMS** : calibration des paramètres Bouchaud impact en live
- **MeanReversionKalman** : 200 innovations Kalman pour la variance

---

## Système de risque

### Cascade de gates (dans l'ordre)

```
Décision de la stratégie
         │
         ▼
1. StrategyCapitalLedger.can_open()
   - Budget stratégie disponible ?
   - Pas suspended/killed ?
         │
         ▼ (si OK)
2. KillSwitch.can_open()
   - DD total < 6% ?
   - Pas de suspension active ? (rampage / streak / vol)
         │
         ▼ (si OK)
3. ExecutionFilter
   - Profit attendu net ≥ $3.0 ?
   - R:R ≥ 1.4 ?
   - Cooldown expiré ? (300s après gain, 900s après perte)
   - Hold min = 90s ?
         │
         ▼ (si OK)
4. LLM Overlay (optionnel — désactivé par défaut)
         │
         ▼
   Executor.place_quotes()  → position ouverte
```

### Triggers de suspension

| Déclencheur | Seuil | Durée | Scope |
|-------------|-------|-------|-------|
| DD total | > 6% | Permanent (restart requis) | Global |
| DD journalier | > 3% | Jusqu'à fin de journée | Global |
| Rampage (trop de trades) | > 25 trades/h | 10 min | Global |
| Loss streak | ≥ 4 pertes consécutives | 30 min | Stratégie |
| BTC vol guard | > 1.2% en 5min | 15 min | Global |
| DD stratégie journalier | > 2.5% capital stratégie | 1h | Stratégie |
| DD stratégie total | > 6% capital stratégie | Permanent | Stratégie |
| Adverse selection | > 65% pick rate (30 trades) | 20 min | Symbole |

---

## Flux de données

```
Hyperliquid WS
  → L2 orderbook (toutes les ~100ms par coin)
  → Trades stream (sur chaque transaction)

OrderbookManager
  → _book_q   → Loop A : fills + décisions sur orderbook
  → _trade_q  → Loop B : accumulation volume OHLCV

Loop C (toutes les 60s)
  → Construit BarData(open, high, low, close, vol, return_1m)
  → Dispatch on_bar_minute() à toutes les stratégies actives
  → La plupart des stratégies génèrent leurs signaux ici

Loop D (toutes les 500ms)
  → Vérifie SL / TP / max_hold pour chaque position ouverte
  → Clôture et enregistre le PnL

Loop G (toutes les 2s)
  → Lit runtime/control.json (commandes GUI)
  → Écrit runtime/strategy_status.json (lu par la GUI)
  → Écrit runtime/calibration_data.json (onglet Calibration)
```

---

## Dashboard GUI

### Onglets

| Onglet | Contenu |
|--------|---------|
| **Overview** | PnL temps réel, positions ouvertes, métriques globales |
| **Stratégies** | Contrôles moteur, toggles rapides, cartes par stratégie |
| **Calibration** | Données brutes de chaque stratégie (indicateurs, signaux) |
| **Trades** | Historique des fills (CSV fills_v9.csv) |
| **Décisions** | Log de toutes les décisions et skips (decisions_v9.csv) |
| **Risque** | État KillSwitch, DD, suspension, pick rates |
| **Coins** | Prix live, spread, volume par coin |
| **LLM Overlay** | État LLM, Brier score, signaux modifiés |
| **Exchanges** | État connexions exchange (multi-exchange optionnel) |

### Bus de commandes (GUI → Engine)

La GUI écrit dans `runtime/control.json`. L'engine lit ce fichier toutes les 2 secondes et exécute la commande. Commandes disponibles :

```json
{"command": "update_strategy", "args": {"strategy": "MomentumLS", "action": "enable"}}
{"command": "update_strategy", "args": {"strategy": "MomentumLS", "action": "disable"}}
{"command": "update_strategy", "args": {"strategy": "MomentumLS", "action": "reset"}}
{"command": "flatten_strategy", "args": {"strategy": "MomentumLS"}}
{"command": "flatten_all", "args": {}}
{"command": "pause_all", "args": {"minutes": 60}}
{"command": "set_trading", "args": {"enabled": false}}
{"command": "close_position", "args": {"pos_id": "abc123"}}
```

---

## Tests & Diagnostic

### Suite de tests

```bash
# Tous les tests unitaires (16 suites, ~130 tests)
python -m pytest tests/ -v

# Tests de l'execution filter
python -m pytest tests/test_execution_filter.py -v

# Audit smoke test stratégies (7 tests)
python scripts/smoke_strategies.py

# Diagnostic connexion live (3 minutes)
python scripts/diagnose_live.py --minutes 3 --coins BTC,ETH,SOL,AVAX
```

### Diagnostic connexion live

Le script `scripts/diagnose_live.py` :
1. Vérifie la connexion WebSocket à Hyperliquid
2. Mesure le débit de book-updates par coin
3. Simule 3 minutes de bars live dans les stratégies
4. Affiche la calibration de chaque stratégie (warmup atteint ? signal proche ?)

```
  Connexion WebSocket... OK
  [ 56s] book-updates reçus:    430 | bars: 0 | restant: 124s

  BTC   ✓  updates=  430  (2.4/s)  trades= 182  bars=3
  MomentumLS    ⏳ warmup en cours (3/3 bars)  → proche du signal
  DonchianTrend ⏳ warmup en cours (3/36 bars) → besoin 33 min de plus
```

### Logs disponibles

| Fichier | Contenu |
|---------|---------|
| `logs/engine_v9.log` | Log complet du moteur (INFO + ERROR) |
| `logs/engine_stdout.log` | Stdout de l'engine lancé depuis la GUI |
| `logs/fills_v9.csv` | Tous les trades exécutés (paper) |
| `logs/decisions_v9.csv` | Toutes les décisions et filtres |
| `logs/risk_events.csv` | Événements de risque (suspensions, kills) |
| `metrics_v9/metrics_v9.csv` | Métriques equity/PnL toutes les 60s |
| `runtime/strategy_status.json` | Statut live des stratégies (lu par GUI) |
| `runtime/calibration_data.json` | Données calibration live (onglet Calibration) |

---

## Limitations connues

### Paper mode vs live

| Aspect | Paper mode | Live |
|--------|------------|------|
| Fills | Instantanés dès que bid/ask touche le prix | Dépend de la position dans le carnet |
| Slippage | Fixe (config : 4bps) | Variable selon la profondeur |
| PnL TP | Exactement `notional × tp_pct` | Peut différer selon le market impact |
| Fees | Rebate maker 0.3bps, taker 3bps (fixe) | Taux réels Hyperliquid |
| Latence | 0ms (simulation) | 10-100ms réseau + matching |

### Stratégies incomplètes

- **FundingArbitrage / FundingCarryHedged** : le leg spot n'est pas implémenté. Activer sans hedge serait risqué en live.
- **SpotPerpBasis** : nécessite un flux de prix spot externe non intégré.
- **RelativeValue** : warmup 8h30, beta hedge requis mais la logique de calcul du beta n'est pas exposée dans l'interface paper.

### Warmup des indicateurs

Après le démarrage de l'engine, les stratégies nécessitent un minimum de barres avant de pouvoir signaler :

```
MomentumLS              →   3 min minimum
BreakoutControlled      →  12 min
VolatilityRegimeBreakout→  20 min
DonchianTrend           →  36 min
MeanReversionKalman     → 200 innovations (≈ 3h20)
RSIBollingerReversion   → 100 barres (EMA-1h) ≈ 1h40
RelativeValue           → 500 barres ≈ 8h20
```

**Il est normal de ne voir aucun trade dans les premières 30-60 minutes.**

### LLM Overlay

Le LLM Overlay est **désactivé par défaut** (`LLM_ENABLED=false`). Si activé, il est appelé sur chaque décision d'entrée (sample rate 1.0 = 100% par défaut). Sur un service LLM lent (>500ms), cela introduit de la latence dans la boucle de décision. Configurer `LLM_SAMPLE_RATE=0.1` pour limiter à 10% des appels.

---

## Structure des fichiers

```
artemisia_v9/
├── engine_v9.py                   # Moteur principal (7 loops asyncio)
│
├── config_v9.json                 # Config principale (14 stratégies)
├── config/presets/
│   ├── paper_500_clean.json       # 5 stratégies × $500
│   └── paper_500_per_strategy.json
│
├── strategies/
│   ├── base_strategy.py           # Interface abstraite (BarData, StrategyConfig)
│   ├── strategy_manager.py        # Registry + dispatch événements
│   ├── bar_aggregator.py          # 1m → 15m/1h/4h
│   ├── momentum_long_short.py     # Cross-sectional z-score
│   ├── breakout_controlled.py     # Resistance breakout + volume
│   ├── donchian_trend.py          # Donchian + trend filters
│   ├── rsi_bollinger_reversion.py # RSI + Bollinger reversion
│   ├── volatility_regime_breakout.py
│   ├── mean_reversion_kalman.py   # Kalman z-score (désactivé)
│   ├── funding_arbitrage.py       # Scanner funding (désactivé)
│   ├── funding_carry_hedged.py    # Scanner carry (désactivé)
│   ├── spot_perp_basis.py         # Scanner base (désactivé)
│   ├── relative_value.py          # Pairs trading (désactivé)
│   ├── rotation_momentum.py       # Scanner rotation (désactivé)
│   ├── orderbook_imbalance_scalper.py
│   ├── s8_ems.py                  # Market-making éco (désactivé)
│   └── meta_alpha_strategy.py     # Agrégateur quorum
│
├── data/
│   ├── orderbook_manager.py       # WebSocket Hyperliquid + reconnexion
│   ├── trades_buffer.py           # Buffer circulaire trades
│   ├── universe.py                # Liste TOP_COINS
│   └── hyperliquid_funding.py     # Polling REST funding rates
│
├── execution/
│   ├── high_freq_executor.py      # Simulateur paper (fills, positions, PnL)
│   ├── cost_filter.py             # Filtre viabilité trade
│   └── multi_exchange_executor.py # Multi-exchange (non utilisé en v9)
│
├── risk/
│   ├── kill_switch.py             # Hard kills + suspensions globales
│   ├── strategy_capital_ledger.py # Budget isolé par stratégie
│   └── adverse_selection_monitor.py # Détecteur toxic flow
│
├── monitoring/
│   ├── pnl_tracker.py             # Logger equity CSV
│   └── decision_logger.py         # Logger décisions CSV
│
├── econophysics/
│   ├── bouchaud_impact.py         # Modèle impact marché
│   ├── har_rv.py                  # Volatilité HAR-RV
│   ├── hurst_local.py             # Régime trend/mean-revert
│   ├── kalman_fair_value.py       # Kalman fair value
│   └── wavelet_singularity.py     # Détection singularités
│
├── indicators/technical.py        # EMA, ATR, Donchian, z-score, BB
│
├── exchanges/
│   ├── base.py                    # Interface abstraite exchange
│   ├── hyperliquid_adapter.py     # Wrapper données Hyperliquid
│   ├── binance_adapter.py         # Stub Binance (désactivé)
│   ├── bitget_adapter.py          # Stub Bitget (désactivé)
│   ├── factory.py                 # Factory pattern
│   └── schemas.py                 # Schémas Pydantic exchange
│
├── llm_agents/
│   ├── base.py                    # Interface LLMOverlay
│   ├── agents.py                  # Agents Claude/OpenAI
│   ├── coordinator.py             # Agrégation signal base + LLM
│   ├── calibration.py             # Brier score, Murphy bins
│   ├── feature_builder.py         # Features OHLCV pour LLM
│   ├── providers.py               # SDK wrappers
│   ├── schemas.py                 # Schémas Pydantic LLM
│   ├── config.py                  # LLM_ENABLED, SAMPLE_RATE
│   └── logger.py                  # Prédiction outcome logging
│
├── gui/
│   ├── app.py                     # App Dash (9 tabs, thème Cyborg)
│   ├── engine_controller.py       # Start/stop engine subprocess
│   ├── control_api.py             # Bus de commandes GUI→Engine
│   ├── data_loader.py             # Cache CSV/JSON pour tabs
│   ├── theme.py                   # Palette couleurs Cyborg
│   └── tabs/
│       ├── overview.py            # PnL global, positions
│       ├── strategies.py          # Contrôles + toggles + cartes
│       ├── calibration.py         # Signaux raw par stratégie
│       ├── trades.py              # Historique fills
│       ├── decisions.py           # Log décisions + filtres
│       ├── risk.py                # KillSwitch, DD, suspensions
│       ├── coins.py               # Prix live par coin
│       ├── llm_overlay.py         # LLM état + Brier score
│       └── exchanges.py           # État connexions exchange
│
├── scripts/
│   ├── smoke_strategies.py        # 7 tests audit stratégies
│   ├── smoke_new_strategies.py    # Tests nouvelles stratégies
│   ├── diagnose_live.py           # Diagnostic connexion live
│   └── analyze_decisions.py      # Analyse post-run
│
├── tests/                         # 16 suites de tests pytest
│   ├── test_execution_filter.py
│   ├── test_strategy_capital_ledger.py
│   ├── test_har_rv.py
│   ├── test_kalman.py
│   ├── test_bouchaud.py
│   ├── test_hurst.py
│   ├── test_exchange_*.py
│   └── test_llm_*.py
│
├── logs/                          # Généré à l'exécution
├── metrics_v9/                    # Métriques equity CSV
└── runtime/                       # Fichiers de communication GUI↔Engine
    ├── strategy_status.json       # Statut live (mis à jour toutes les 10s)
    ├── calibration_data.json      # Données calibration live
    ├── control.json               # Commandes GUI → Engine
    └── control_result.json        # Réponses Engine → GUI
```

---

## Feuille de route

### Paper mode validé ✅
- [x] 5 stratégies directionnelles actives
- [x] Cascade de risque 4 niveaux
- [x] Dashboard GUI temps réel
- [x] Tests unitaires complets
- [x] Reconnexion WebSocket automatique

### Avant passage en live ⚠️
- [ ] Backtest individuel de chaque stratégie (2-4 semaines paper)
- [ ] Implémenter le leg spot pour FundingArbitrage/Carry
- [ ] Intégrer un flux de prix spot pour SpotPerpBasis
- [ ] Tester la reconnexion WebSocket sous stress réseau
- [ ] Déplacer le LLM overlay hors du hot path (ou réduire sample_rate à 0.1)
- [ ] Vérifier les seuils de risque contre les profils de liquidité live
- [ ] Valider le suivi heartbeat sur tous les symbols (pas seulement BTC)

---

*Artemisia v9 — Paper trading uniquement. Ne pas utiliser en live sans validation complète.*
