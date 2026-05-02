# Artemisia v9 — Monitoring GUI

Dash dashboard (Cyborg theme) for monitoring the S8 EMS bot in real-time.
Reads CSV logs **in read-only mode** — completely separate process from the engine.

## Lancement

```bash
# Dans un terminal séparé du bot :
python -m gui.app

# Puis ouvrir :
# http://127.0.0.1:8050
```

## Dépendances

```bash
pip install dash dash-bootstrap-components plotly pandas
```

## Tabs

| Tab | Contenu |
|-----|---------|
| **Overview** | Equity curve, cards PnL/WR/drawdown, PnL par coin |
| **Decisions** | Répartition PLACE/SKIP, top skip reasons, table par coin, what-if spread slider |
| **Trades** | Historique des 100 derniers fills avec filtres coin/reason |
| **Coins** | Par coin : PnL, distribution spread, distribution Hurst, top skips |
| **Risk** | Drawdown daily/total, historique des pertes |

## Fichiers lus

| Fichier | Source |
|---------|--------|
| `logs/decisions_v9.csv` | Decision logger (engine) |
| `logs/fills_v9.csv` | HighFreqExecutor |
| `metrics_v9/metrics_v9.csv` | PnLTracker |

Auto-refresh toutes les **5 secondes** via `dcc.Interval`.
