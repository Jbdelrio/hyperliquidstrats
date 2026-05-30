# Analyse globale des stratégies + verdict levier / ARIMA-GARCH — 2026-05-30

Objectif : simuler chaque stratégie à 500 $ de capital, comparer, et trancher
honnêtement sur l'idée « micro-marge à levier extrême (100-200x) pour capturer
un burst de volatilité en 15-30 s ».

Tout est reproductible :
- `python scripts/backtest_strategies.py --days 90 --timeframe 15m` (barres)
- `python scripts/backtest_alpha.py` (signaux décile)
- `python scripts/backtest_leverage_scalp.py` (**nouveau** — levier + liquidation)
- `python scripts/probe_arima_garch.py` (**nouveau** — ARIMA/GARCH vs microstructure)

---

## 1. Classement des stratégies à 500 $

### Stratégies « barres » (OHLCV 15m, 90 j, BTC/ETH/SOL/AVAX)

| Stratégie | Trades | Net PnL | WR | Verdict |
|---|---:|---:|---:|---|
| MomentumLS | 0 | $0 | — | ne trade pas (filtres trop serrés en 15m) |
| BreakoutControlled | 0 | $0 | — | ne trade pas |
| RSIBollingerReversion | 1 | +$5.56 | 100% | échantillon nul |
| DonchianTrend | 38 | **−$85** | 10.5% | ❌ saigne |
| VolatilityRegimeBreakout | 562 | **−$240** | 40.2% | ❌ sur-trade, sous le coût |

→ **Aucune stratégie barre n'a d'edge.** Soit elles ne tradent pas, soit elles
paient les frais sans signal (VolBreakout perd 240 $ en sur-tradant). Ce sont
des suiveurs de tendance sur des majors qui n'ont pas tendance à 15m.

### Signaux décile microstructure (data seconds, ~4.6 j, 21 coins)

Top par PnL out-of-sample (notional fixe 250 $, sans levier) :

| Signal | Trades | WR | Net PnL | Sharpe |
|---|---:|---:|---:|---:|
| INJ liquidity_vacuum 300s | 20 | 35% | **+$20.0** | 13.7 |
| INJ trade_imbalance_30s 120s | 34 | 35% | **+$14.6** | 9.8 |
| WLD liquidity_vacuum 300s | 24 | 58% | +$8.6 | 68 |
| WLD obi_10 120s | 35 | 49% | +$8.3 | 17 |
| WLD rv_60s 30s (maker) | 39 | 56% | +$7.6 | 15 |
| COMP obi_10 300s | 22 | 27% | −$10.2 | ❌ |
| AAVE obi_10 120s short | 124 | 14% | −$20.9 | ❌ |

→ Le seul edge réel du système est là : **microstructure sur altcoins à
120-300 s**. Mais échantillons minuscules (20-40 trades sur 4.6 j) et le paper
live de 25 h avait déjà donné −46 $ (frais > edge). Edge fragile, pas mort.

**Conclusion §1 :** à 500 $ chacune, la « gagnante » du backtest est
**INJ liquidity_vacuum 300s**, suivie de **INJ trade_imbalance 120s**. Toutes
les stratégies barres sont à jeter ou à reparamétrer.

---

## 2. Le levier — la vérité chiffrée

`scripts/backtest_leverage_scalp.py` modélise ce que les autres backtests
ignorent : **notional = marge × levier**, le **chemin de prix seconde par
seconde**, et la **LIQUIDATION** (perte de 100 % de la marge si le mouvement
adverse atteint ~`(1−mm)/L`). Marge 20 $/trade, TP +25 % sur marge, compte 500 $.

Rendement du compte par niveau de levier :

| Signal | 1x | 25x | 50x | 100x | 150x | 200x |
|---|---:|---:|---:|---:|---:|---:|
| INJ TI 120s (edge réel) | +0.2% | +5.9% | +10.9% | +19.5% | +29.2% | +42.2% |
| INJ LV 300s (edge réel) | +0.3% | +4.8% | +11.4% | +24.7% | +29.1% | +42.9% |
| WLD LV 300s | +0.1% | **+3.2%** | +0.2% | +2.1% | −15.5% | −19.2% |
| WLD OBI 120s | +0.1% | +3.3% | +5.9% | **+9.8%** | −9.0% | −8.1% |
| **WLD rv 30s (ton idée)** | +0.0% | +0.8% | +1.5% | +2.8% | +5.9% | +9.8% |
| **BANANA rv 30s (ton idée)** | −0.2% | −6.0% | −12.0% | −24.0% | −35.9% | **−49.0%** |
| **KAITO rv 15s (ton idée)** | −0.2% | −5.9% | −11.8% | −22.6% | −35.8% | **−48.2%** |

Trois leçons dures :

1. **Le levier n'est PAS du rendement.** Rendement/marge = `L · (g − coût)`. Le
   levier multiplie l'edge *net*. Si `g < coût` (cas des entrées rv sans
   direction : BANANA, KAITO), augmenter L **accélère la perte** — −49 % du
   compte à 200x. Ton idée exacte (entrer sur un burst de vol) est la **pire**
   du tableau.

2. **La liquidation est asymétrique et mortelle.** Un trade gagnant fait
   +25 % de marge ; une liquidation perd **100 %**. Quand L monte, la distance
   de liquidation `~0.5/L` rétrécit, le taux de liquidation grimpe, et même un
   signal positif à bas levier s'effondre (WLD LV : +3.2 % @25x → −15.5 %
   @150x, 24 % de liquidations).

3. **Il existe un levier optimal, jamais le plus haut.** Pour les signaux avec
   edge, le sweet-spot est **25-50x** (WLD OBI culmine à 100x puis plonge).
   Au-delà, tu paies la taxe de liquidation.

> Note exécution : Hyperliquid ne propose **pas** 100-200x (BTC 40x, alts
> souvent 3-20x). Les colonnes 150-200x sont théoriques.

---

## 3. ARIMA / GARCH — peuvent-ils prédire la direction ? Non.

`scripts/probe_arima_garch.py`, sur la vraie data seconds :

**AR(1) / ARIMA — taux de réussite directionnel (50 % = pile ou face) :**

| Coin | 15s | 30s | 120s | 300s |
|---|---:|---:|---:|---:|
| BTC | 51.3% | 50.4% | 48.2% | 46.2% |
| WLD | 50.4% | 50.3% | 46.0% | 45.1% |
| BANANA | 45.6% | 44.3% | 44.6% | 43.1% |

→ ~50 %. Aucun edge directionnel. (BANANA < 50 % = anti-tendance.)

**GARCH(1,1) — corrélation de la vol prévue avec :**

| Coin | l'**amplitude** \|r\| | le **signe** de r |
|---|---:|---:|
| BTC | +0.060 | **+0.007** |
| WLD | +0.067 | **+0.014** |
| KAITO | +0.022 | **−0.000** |

→ GARCH prédit (faiblement) **quand** ça bouge, **jamais dans quel sens**.
C'est mathématique : un modèle de variance n'a aucune information de signe.

**Microstructure — IC Spearman vs rendement futur (là est la direction) :**

| Coin | Feature | 15s | 30s | 120s | 300s |
|---|---|---:|---:|---:|---:|
| BTC | obi_10 | **+0.323** | +0.250 | +0.125 | +0.061 |
| BTC | microprice_pressure | +0.319 | +0.241 | +0.119 | +0.064 |
| ETH | microprice_pressure | +0.283 | +0.202 | +0.095 | +0.042 |

→ **La direction courte est prédictible — mais par la microstructure (carnet),
pas par un modèle temporel.** BTC `obi_3` à 5s : spearman **0.45** (énorme).

**MAIS** le mur (depuis `alpha_discovery.json`) : BTC obi_3 5s, mouvement décile
= **1.4 bps**, net taker **−8.6 bps**, net maker **−2.6 bps**. La direction est
juste, mais **le mouvement est plus petit que le coût**. Et le levier multiplie
mouvement ET coût à l'identique → il ne répare rien.

---

## 4. Ce que j'ai construit (l'idée, faite correctement)

L'erreur de l'idée d'origine : utiliser GARCH/burst pour **entrer**. La bonne
utilisation : GARCH (la vol) sert de **gate de coût**, la microstructure donne
la **direction**, et on n'ouvre que quand le mouvement prévu dépasse le coût.

**`strategies/garch_vol_breakout.py`** (`GarchVolBreakoutStrategy`, enregistrée
dans le moteur) :
- Vol forecast online (RiskMetrics / GARCH-lite `σ²=(1−λ)r²+λσ²`, pas de re-fit
  par seconde).
- **Gate de coût** : on ne trade que si `σ·√H` (mouvement prévu) ≥ `mult × coût`.
- **Direction** : `microprice_pressure` + confirmation `obi_10` (les features à
  forte IC).
- **Sizing levier explicite** : `notional = marge × levier`, stop placé à
  `0.6 × distance_liquidation`, TP à +25 % sur marge. Levier par défaut **25x**
  (le sweet-spot du §2), maker-first pour passer le gate de coût.

**`strategies/alpha_signal_decile.py`** — amélioré (rétrocompatible, nouveaux
params off par défaut) :
- `confirm_direction` : un croisement décile ne déclenche que si
  `microprice_pressure`/`obi_10` confirment le sens (ajoute l'IC qui manquait).
- `min_expected_move_mult` : gate de coût via `rv_30s·√(H/30)` — empêche de
  trader là où mouvement < coût (le piège majors-en-secondes).
- Sizing levier (`margin_usd`+`leverage`) avec stop anti-liquidation et TP en
  % de marge.

**`config/presets/paper_500_leverage_research.json`** — prêt à lancer :
`GarchVol_MAJORS` (BTC/ETH/SOL), `GarchVol_ALTS` (INJ/WLD/HYPE), et deux décile
levier-25x (`INJ_LV300`, `INJ_TI120`). Tous à 500 $, maker-first, levier 25x.

---

## 5. Recommandations

1. **Abandonne le 100-200x sur burst de vol.** Le backtest le prouve : sans
   edge directionnel, c'est −49 % de compte. Et ce n'est pas exécutable sur HL.
2. **Garde le levier modéré (25-50x) UNIQUEMENT sur les signaux à edge réel**
   (INJ liquidity_vacuum / trade_imbalance à 120-300s). Là, 25-50x transforme
   +0.3 % en +5-11 % de compte, liquidations < 5 %.
3. **Maker-first partout.** Passer de 9 → 3-6 bps de coût rend rentables des
   mouvements 1.5-3x plus petits — c'est le seul vrai levier « gratuit ».
4. **Le gate de coût est la clé** : ne jamais ouvrir si mouvement prévu < 2×
   coût. C'est ce qui distingue les deux nouvelles stratégies du sur-trading.
5. **Prochaine étape** : lancer `paper_500_leverage_research.json` en paper
   24-48 h, comparer AvgGross live (le seul prédicteur honnête, cf. paper run
   précédent) entre GarchVol et les décile, et ne live-tester que ce qui tient.
