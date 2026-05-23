# Diagnostic Artemisia v9 — 2026-05-21

Scan complet demandé après une session paper aux résultats médiocres.
Run analysé : démarré 2026-05-20 22:53, encore actif au moment du scan
(~20 h de trading, métriques jusqu'à 17:15 le 21).

---

## Verdict en une ligne

Les pertes ne viennent **pas** d'un alpha "pas encore trouvé" ni seulement de
bugs. Elles viennent de **3 causes qui se cumulent**, par ordre d'impact :

1. **Les stratégies qui tradent sont à espérance négative *par conception*** —
   leur logique de sortie garantit mathématiquement de payer les frais sans
   capturer de mouvement. (problème de *design de stratégie*)
2. **3 bugs d'implémentation réels** — dont une métrique de latence cassée qui
   aveugle à moitié le filtre qualité. (problème d'*implémentation*)
3. **Le framework de recherche d'alpha a été écrit mais jamais appliqué** — les
   14 stratégies sont `enabled:true` sans qu'aucune n'ait passé un seul test du
   framework. (problème de *process*)

Réponse directe à « est-ce l'implémentation ou l'alpha » : **les deux**, mais la
cause racine est le n°3 — on paper-trade 14 stratégies non validées, et les 2-3
qui tirent réellement sont conçues pour ne pas pouvoir gagner.

---

## 1. Ce qui s'est réellement passé (les chiffres)

| Métrique | Valeur |
|---|---|
| Durée du run | ~20 h |
| Equity | 6000 → **5985.07** (**−14.93 $**) |
| Trades clôturés | 175 |
| Wins / Losses | **26 / 149** (~15 % winrate) |
| Sorties par TP | 9 |
| Sorties par stop | 5 |
| Sorties par **max_hold (timeout)** | **161** |

Lecture : **92 % des positions sortent en timeout**, pas par TP ni stop. Les
stratégies entrent sur du bruit, dérivent latéralement, expirent, paient les
frais. C'est la signature d'une absence totale d'edge — pas de la malchance.

Trajectoire d'equity (perte qui s'accélère) : −2 $ → −6 $ → −10 $ → −15 $.

---

## 2. Pourquoi « toutes les stratégies ne s'activent pas »

Le moteur a été lancé par la GUI avec un flag explicite :

```
--strategy S8EMS,MomentumLS,BreakoutControlled,MeanReversionKalman,
           FundingArbitrage,DonchianTrend,RSIBollingerReversion,
           RotationMomentum,RelativeValue,SpotPerpBasis,FundingCarryHedged,
           OBImbalanceScalper,VolatilityRegimeBreakout,MetaAlpha
```

Conséquence (cf. `engine_v9.py` lignes 270-272 : `enabled = wanted and cfg_capital > 0`) :

- **Exclues de la liste** → désactivées de force : `AlphaPressureScalper`,
  `BookFlowDivergenceReversal`, `AbsorptionReversal`. Ce sont précisément les
  3 scalpers « fréquents » censés tirer 5-10 trades/h.
- **Dans la liste mais capital = 0** → désactivées aussi : `S8EMS`,
  `FundingArbitrage`, `RelativeValue`, `SpotPerpBasis`, `FundingCarryHedged`.
- **Restantes réellement actives** : `OBImbalanceScalper` + `MeanReversionKalman`
  (tradent) ; les bar strats `MomentumLS`, `BreakoutControlled`, `DonchianTrend`,
  `VolatilityRegimeBreakout`, `RSIBollingerReversion`, `RotationMomentum`,
  `MetaAlpha` sont actives mais **n'ont émis aucune décision de tout le run**
  (warmup 60 barres = 60 min + signaux rares).

→ **Correctif** : dans la GUI, laisser le sélecteur **MOTEUR vide** (la config
décide). Et corriger la GUI pour qu'elle ne persiste pas une sélection obsolète.
Bug annexe : le preset a été édité *pendant* que le moteur tournait — le moteur
garde en mémoire l'ancienne config, les éditions n'ont aucun effet sans restart.

---

## 3. Les 2 stratégies qui tradent sont à EV négative *par conception*

### 3.1 OBImbalanceScalper — sortie sur cheveu (« hair-trigger »)

- Entrée : imbalance > 0.30 soutenue. Sortie : `imbalance_exit_threshold`, qui
  **n'est dans aucune config** → vaut le défaut **0.05** (`_check_exit`).
- L'imbalance du carnet est extrêmement bruitée. Une position ouverte à
  imb=+0.35 voit l'imbalance repasser sous −0.05 en quelques secondes →
  sortie immédiate `imbalance_reversed`.
- Résultat observé : 13/13 trades sortent en 19-34 s, gross ≈ ±quelques cents,
  net toujours négatif (frais 6 bps). **0 gain sur les 12 premiers trades** —
  ce n'est pas de la malchance, c'est déterministe.
- Structurellement : l'OB imbalance sur BTC/ETH vaut ~1-3 bps d'edge par
  événement (Cont-Kukanov-Stoikov : impact linéaire, pente ∝ 1/profondeur).
  Impossible de payer 6-9 bps aller-retour en taker pour récolter 1-3 bps.
  Votre propre `ALPHA_RESEARCH_FRAMEWORK.md` §2 liste « OBI positif → buy »
  comme un **faux alpha** typique.

### 3.2 MeanReversionKalman — TP placé *à l'intérieur* du coût

- `tp = fv` (la fair value Kalman au moment du fill), soit ~1 σ d'innovation
  de l'entrée = quelques bps.
- `tp_fill_mode: market_after_touch` + 2 bps de slippage → un TP « gagnant »
  se remplit quand même **en dessous de l'entrée**. Preuve dans `fills_v9.csv` :
  BUY ETH 2136.1 → exit 2135.67, reason `take_profit`, PnL négatif. 0/6.
- Plus profond : un filtre de Kalman 1-D sur le **mid** ne produit pas une
  « fair value » vers laquelle revenir — il produit un lissage retardé du prix.
  `mid − fv` est un terme de momentum lissé, pas un écart fondamental. Il n'y a
  **aucun ancrage** : la « mean reversion » revient vers une moyenne mobile
  d'elle-même. C'est l'erreur classique du Kalman mono-série.

---

## 4. Bugs d'implémentation trouvés — **corrigés le 2026-05-21**

> Les bugs #1, #2, #3 ci-dessous ont été corrigés ; les 27 tests concernés
> passent. Les correctifs ne prennent effet qu'au **prochain redémarrage** du
> moteur (le process actuel garde l'ancien code en mémoire).

| # | Fichier | Bug | Impact |
|---|---|---|---|
| # | Fichier | Bug | Statut |
|---|---|---|---|
| 1 | `data/orderbook_manager.py` | `latency_ms = recv_ts − ex_ts` comparait l'horloge **locale** à l'horloge **exchange**. Avec un offset d'horloge : latence affichée 70-105 **secondes**, HYPE p95 = 8 397 621 ms (2,3 h), `last_book_age_s` **négatif**, `p95 = 0.0` sur 11/12 symboles. → **166 blocages `latency_p95` faux** dans la MQG. | ✅ **corrigé** — échantillons stockés bruts, offset d'horloge retiré par min-filter à la lecture, frames glitch (> 30 s) écartées de p95/avg ; staleness mesurée sur l'horloge locale (`recv_ts`) |
| 2 | `strategies/orderbook_imbalance_scalper.py` `_check_exit` | `imbalance_exit_threshold` absent de la config → défaut 0.05 (hair-trigger, cf. §3.1) | ✅ **corrigé** — défaut = seuil d'entrée (sortie sur vraie inversion, pas sur un passage à zéro) |
| 3 | `strategies/mean_reversion_kalman.py` `on_fill` | stop codé en dur 2 % (≠ config 0,5 %) et TP = FV brute sans plancher `min_take_profit_pct` | ✅ **corrigé** — `on_fill` aligné sur la logique de décision (stop = `stop_loss_pct`, TP plafonné au plancher) |
| 4 | process | le preset est édité pendant que le moteur tourne → éditions sans effet sans restart | ⚠️ comportement attendu — penser à redémarrer après édition de config |

Note feed : les alts (AVAX, XRP, LINK, OP, ARB…) ont un flux de trades très
mince (0-13 trades / 30 s) → OFI saturé à ±1 *légitimement* (marché mince, pas
un bug). Conclusion : ne pas trader de signal microstructure sur les alts.
BTC/ETH/HYPE ont un flux sain.

---

## 5. Le framework d'alpha a été écrit puis ignoré

`docs/ALPHA_RESEARCH_FRAMEWORK.md` est **excellent** et dit exactement la
bonne chose :

> « Tant qu'aucun de ces tests n'a été passé, ce qu'on a est une **hypothèse**,
> pas un alpha. » — et : passage à `enabled=true` **seulement** après IC stable,
> bucket monotone, PnL net > 0 après 12 bps, walk-forward > 60 %, IC résiduel
> hors-bêta-BTC non nul, sur ≥ 5 jours et ≥ 2 symboles. « **Aucune de ces
> étapes n'est automatique.** »

Or les 14 stratégies sont `enabled:true` dans le preset sans qu'**aucune** n'ait
passé ce gate. Les commentaires du preset le confirment : les paramètres ont été
« desserrés » (`min_net_profit 0.50 → 0.08`, `RR 1.0 → 0.75`) pour *permettre*
les trades, pas pour les rendre *rentables*. RR 0.75 = on risque plus qu'on ne
peut gagner.

**Vous avez écrit le règlement, puis activé 14 stratégies sans l'appliquer.**
C'est la cause racine.

---

## 5bis. Preuve empirique — scan IC sur VOS données (le point décisif)

J'ai calculé le **Spearman IC** de chaque signal microstructure contre les
forward returns, sur **496 800 lignes** de `seconds_features.csv` (12 symboles,
~11 h). Script : `scripts/ic_quickscan.py` · rapport : `reports/ic_quickscan.md`.

**Les signaux NE sont PAS du bruit — ils ont un vrai pouvoir prédictif :**

| Signal | IC 5 s | IC 30 s | IC 120 s |
|---|---|---|---|
| `obi_1` (imbalance carnet) | **+0.191** | +0.097 | +0.051 |
| `microprice_pressure` | +0.125 | +0.057 | +0.029 |
| `pressure_score_raw` | +0.125 | +0.063 | +0.038 |
| `r_5s` (momentum court) | +0.112 | +0.052 | +0.030 |

IC de +0.19 sur 496 k points, signe stable, décroissance monotone avec
l'horizon → c'est un signal **réel**, pas une coïncidence.

**MAIS l'amplitude prédite est ~5× plus petite que le coût :**

| Signal | Rendement décile-haut − décile-bas (30 s) | Coût round-trip | Verdict |
|---|---|---|---|
| `obi_1` | **+1.83 bps** | 10 bps | net **−8 bps** |
| `pressure_score_raw` | +1.37 bps | 10 bps | net −8,6 bps |
| `r_5s` | +0.92 bps | 10 bps | net −9 bps |
| tous les autres | < 1 bps | 10 bps | pire |

**Aucun** des 18 signaux n'a un écart de décile supérieur au coût. Même avec un
timing **parfait**, le meilleur signal capture 1,8 bps et en paie 10.

### Ce que ça tranche définitivement

Ce n'est ni « pas encore trouvé l'alpha » ni « bug d'implémentation ». C'est :

> **Les signaux microstructure ONT un edge réel (IC +0.19), mais le mouvement
> qu'ils prédisent (1-2 bps) est 5 à 10× plus petit que le coût de trading en
> taker (10 bps). Aucun réglage ne comble un écart de coût de 5×.**

C'est précisément ce que votre `ALPHA_RESEARCH_FRAMEWORK.md` §2 annonçait. Le
scan le prouve sur vos propres données. Conséquence directe : **tous les
scalpers tick/seconds sont structurellement morts en taker** — il faut soit
devenir *maker* (encaisser le spread au lieu de le payer), soit trader des
horizons plus longs où le mouvement est grand devant le coût.

---

## 6. Recommandation — se concentrer sur 2-3 stratégies, valider AVANT

### Abandonner : tous les scalpers tick/seconds
`OBImbalanceScalper`, `AlphaPressureScalper`, `BookFlowDivergenceReversal`,
`AbsorptionReversal`, `S8EMS`. Raison **structurelle** (pas de tuning) : en
taker non colocalisé payant 6-9 bps aller-retour, on ne peut pas récolter un
signal microstructure de 1-5 bps. Confirmé par la littérature et par votre
propre framework §2.

### Garder comme candidats (niveau barre — coût amorti sur de gros mouvements)

1. **Momentum cross-sectional** (`MomentumLS` / `RotationMomentum`) — l'alpha
   crypto le mieux documenté. TP/SL en % sur des heures → 8-16 bps de coût =
   petite fraction du mouvement. À valider : retrait du bêta-BTC, walk-forward.

2. **Funding carry delta-neutral** (la *vraie* version : long spot / short perp).
   La littérature : funding BTC positif 322/365 jours en 2024 ; rendements
   documentés 15-35 %/an *neutres au marché*, drawdowns ~2 %. **C'est l'edge
   crypto le plus robuste et le mieux documenté — et c'est celui que vous avez
   désactivé** (`FundingCarryHedged` off car la jambe spot n'est pas câblée).
   Câbler cette jambe spot vaut plus que de tuner tous les scalpers réunis.

3. Éventuellement **une** stratégie barre mean-reversion / breakout, mais
   seulement si elle passe le notebook.

Tout le reste : `enabled:false` jusqu'à ce que le notebook tranche.

### Quel preset utiliser — ne pas en créer un 11e

Le repo contient **déjà 10 presets** (`ideal`, `improved`, `clean`,
`total_safe`, `total_seconds_filtered`, `all_strategies_adaptive`,
`alpha_research`, …). Cette prolifération **est** un symptôme : on a multiplié
les jeux de réglages au lieu d'en valider un. Ne pas en ajouter un onzième.

- **`paper_500_clean.json`** = exactement le preset recentré recommandé :
  5 stratégies barre directionnelles, **tous les scalpers / Kalman / funding
  off**, et surtout les gates **non desserrées** (`RR 1.4`, `min_net_profit 3 $`,
  `min_hold 90 s`). C'est lui qu'il faut lancer, pas `all_strategies_adaptive`
  (`RR 0.75`, 12 stratégies, gates desserrées).
- `paper_500_alpha_research.json` (0 stratégie active) = collecte de features
  pour la recherche, sans trader.
- Recommandé : **supprimer** `ideal` / `improved` / `total_seconds_filtered` /
  `all_strategies_adaptive` pour ne garder que `clean` + `alpha_research`
  (+ `micro_live_safe` plus tard).

---

## 7. Plan d'action concret

1. **Arrêter le moteur actuel** puis le **redémarrer sur `paper_500_clean.json`**
   (sélecteur MOTEUR de la GUI **vide**). Le run actuel saigne sur des
   stratégies non validées ; les correctifs du §4 ne s'appliquent qu'au restart.
2. **Exploiter les données déjà collectées** : `scripts/ic_quickscan.py` est
   fait (cf. §5bis) ; pour aller plus loin, le notebook
   `research/alpha_research_hyperliquid_seconds.ipynb` ajoute walk-forward +
   retrait du bêta-BTC.
3. ~~Corriger les 3 bugs du §4~~ → **fait le 2026-05-21**, effectif au restart.
4. ~~Câbler la jambe spot pour le funding carry delta-neutral~~ → **fait le
   2026-05-21**. `FundingCarryHedged` a un mode `hedged` : sim paper auto-contenue
   à 2 jambes (perp + spot=oraclePx Hyperliquid), accrual de funding horaire,
   sortie funding/basis/maxhold, PnL → `logs/funding_carry_paper.csv`. Activé
   dans `paper_500_clean.json`. Effectif au prochain redémarrage.
5. **Ré-activer les stratégies une par une**, uniquement après que le notebook
   les ait validées selon le §8 de votre propre framework.
6. Pour les scalpers : ne les ré-explorer **que** en mode *maker* (encaisser le
   spread). En *taker*, le §5bis prouve qu'ils ne peuvent pas gagner.

---

## Références littérature

- Cont, Kukanov, Stoikov — *The Price Impact of Order Book Events* (2010) —
  l'OFI a un impact linéaire mais de pente ∝ 1/profondeur : edge minuscule par
  événement. https://arxiv.org/abs/1011.6402 ·
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822
- *Cross-impact of order flow imbalance in equity markets* (Quantitative
  Finance, 2023) — https://www.tandfonline.com/doi/full/10.1080/14697688.2023.2236159
- *Exploring Risk and Return Profiles of Funding Rate Arbitrage on CEX and DEX*
  (ScienceDirect, 2025) —
  https://www.sciencedirect.com/science/article/pii/S2096720925000818
- Ackerer, Hugonnier, Jermann — *Perpetual Futures Pricing* —
  https://finance.wharton.upenn.edu/~jermann/AHJ-main-10.pdf
