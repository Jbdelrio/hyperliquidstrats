# METARBITRAGE — moniteur de spread cross-venues (MESURE UNIQUEMENT)

## État : mesure, pas exécution
Ce module **mesure** s'il existe un écart de prix **NET de coûts** capturable entre
venues, sur les coins ≥ 500 M$ de market cap. Il **n'exécute aucun trade** et
**n'utilise aucune clé privée**. C'est volontaire : avant de risquer du capital, on
prouve d'abord l'existence d'un edge net positif et persistant (même discipline
anti-overfit que le reste du projet).

## Pourquoi pas d'exécution live tout de suite
L'arbitrage cross-DEX en REST 30-60s est dominé par les bots MEV (mempool privé,
flash-loans, transactions atomiques, co-location). Un écart « brut » de 0.3 % sur
une API n'est presque jamais capturable après frais (2 × ~0.1 %), slippage, latence
et risque d'inventaire entre les deux jambes non-atomiques. Le premier scan le
montre : sur les coins liquides, le spread brut est de 2-12 bps → **net négatif**
après ~30 bps de coût ; les « gros » spreads sont des **leurres** (token illiquide,
quote périmée, listing différent) → flaggés `SUSPECT`.

## Lancer le moniteur
```bash
python -m metarbitrage.scanner            # boucle 30s
python -m metarbitrage.scanner --once     # un seul scan
```
Écrit `runtime/metarbitrage.json` (lu par le GUI) et logue les nets positifs dans
`data/metarbitrage/opportunities.csv`.

## Voir en live
Onglet **⚡ Metarbitrage** du dashboard Dash Cyborg :
```bash
python gui/app.py        # http://127.0.0.1:8050
```
Vert = net positif (rare, à vérifier) · rouge = net négatif (pas d'arb) ·
⚠️ SUSPECT = leurre intradeable.

## Périmètre et extensions
- **CEX spot** par symbole : Binance, Bybit, OKX, KuCoin, Gate.io (ccxt public).
  Univers sélectionné dynamiquement (CoinGecko, ≥ 500 M$).
- **DEX Solana via Jupiter** (`metarbitrage/jupiter.py`) : prix **exécutable** par
  quote API (slippage AMM + frais de pool inclus), sur un registre curé de tokens
  Solana liquides (SOL, JUP, BONK, WIF, PYTH, JTO, RAY, W…). HYPE/LINK/PEPE/ASTER
  n'ont pas de marché Solana natif liquide → non couverts (documenté).
  Constat : SOL DEX(Jupiter) vs CEX = ~10 bps brut → **NET négatif** lui aussi.
- À ajouter ensuite : Uniswap/PancakeSwap (EVM) — même logique de quote exécutable.
- **Exécution** : sera envisagée UNIQUEMENT si le moniteur démontre, sur plusieurs
  jours, un net positif persistant hors leurres — et alors derrière une gestion de
  clés sécurisée + blackout macro + tests.

## Paramètres (`metarbitrage/scanner.py`)
`MIN_MKTCAP_USD` (500M), `CEX_FEES`, `SLIPPAGE_BPS`, `MIN_NET_BPS`,
`MAX_SANE_GROSS_BPS` (seuil anti-leurre), `MAX_COINS`, `SCAN_INTERVAL_S`.
