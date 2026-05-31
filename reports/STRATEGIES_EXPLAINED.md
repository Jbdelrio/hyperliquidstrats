# Artemisia v9 — les stratégies expliquées (math, intuition, alpha)

*2026-05-31. Pour chaque stratégie : l'intuition, la formule, d'où viendrait
l'alpha, la logique de sortie, et un verdict honnête à la lumière des backtests
et du paper run.*

Conventions : `mid = (best_bid+best_ask)/2`, `r_t = ln(P_t/P_{t-1})`, un mouvement
en **bps** = `Δ/P × 10⁴`. Coût aller-retour de référence : **~9 bps taker**,
**~3-6 bps maker**. Règle d'or : une stratégie n'a d'alpha que si son **gain brut
moyen par trade (AvgGross, bps) dépasse ce coût**.

---

## Famille A — Directionnel sur barres (minutes → heures)

### A1. HourlyBreakout *(la stratégie validée — ZEC)*
**Intuition.** Sur un actif à forte volatilité, une clôture horaire qui sort d'un
range récent signale une rupture de régime que la foule prolonge quelques heures.

**Formule.** Canal sur les `N` dernières clôtures 1h (défaut `N=20`) :
```
upper = max(C_{t-N..t-1}),  lower = min(C_{t-N..t-1})
signal = +1 si C_t > upper   (long)
         −1 si C_t < lower   (short)
```
Gates : volatilité `ATR_bps ≥ min_atr_bps` (≈ il faut du mouvement), et coût
`range_bps = (upper−lower)/lower·10⁴ ≥ min_cost_ratio × cost`. Sizing :
`notional = marge × levier`, stop anti-liquidation à `liq_safety × (1−mm)/L`,
sortie au **time-stop** `max_hold_hours`.

**Alpha.** Momentum de cassure : `E[r_{t→t+H} | breakout] > 0` sur les coins à
forte vol (ZEC, vol ~148 bps/h). Validé OOS (walk-forward 60/40, robuste au sweep
period×hold, survit au coût taker 6→15 bps). C'est le **seul edge barre** confirmé
du top-20 HL.

**Verdict.** ✅ À faire tourner (ZEC, taker). WLD/HYPE désactivés (mirages maker).

### A2. MomentumLS (cross-sectional long/short)
**Intuition.** Les coins qui montent le plus continuent (momentum transversal) ;
on longe les forts, on shorte les faibles.

**Formule.** Pour chaque coin, z-scores des rendements multi-horizons puis score
composite et rang percentile :
```
z_h(i) = (r_h(i) − μ_h)/σ_h        pour h ∈ {15m, 1h, 4h}
score(i) = w₁z_15m + w₂z_1h + w₃z_4h
rank(i) = percentile(score(i))  ∈ [0,1]
long si rank ≥ long_percentile (0.75) · short si rank ≤ 0.25
```
Sortie : `stop_loss_pct`, `take_profit_pct`, `max_hold_hours`.

**Alpha.** Auto-corrélation positive des rendements relatifs entre actifs.

**Verdict.** ❌ Paper : **0/11 gagnants**, AvgGross −5.5 bps. Le momentum
transversal ne tient pas sur cet univers / cet horizon. À désactiver.

### A3. DonchianTrend
**Intuition.** Suivi de tendance filtré multi-timeframe.
**Formule.** Cassure Donchian sur 15m `C_t > max(H_{t-N..t-1})`, **ET** filtre de
tendance `C_1h > EMA_1h`, **ET** régime `BTC_4h > EMA_200(4h)`, **ET** volume
`V_15m > k·SMA(V)`, **ET** cost-filter `range_bps ≥ min_ratio·cost`. Trailing-stop
= Donchian mid. **Long-only.**
**Alpha.** Persistance de tendance alignée sur le régime BTC.
**Verdict.** ⚠️ Backtest 15m : −$85. Trop de filtres → rare et tardif. Le bon
horizon de cassure est 1h (→ A1), pas 15m.

### A4. VolatilityRegimeBreakout
**Intuition.** Ne trader les cassures que quand la volatilité est élevée (régime
porteur), des deux côtés.
**Formule.** Régime via `ATR_bps(14)` : si `ATR_bps > high_vol_threshold` →
breakout Donchian(20) : `C > channel_high → long`, `C < channel_low → short`.
**Alpha.** Les cassures « payent » surtout en haute volatilité (mouvement > coût).
**Verdict.** ❌ Backtest 15m : **−$240** (562 trades, sur-trading sous le coût).
L'idée est bonne (= A1) mais le canal sur barres 1m/15m est trop bruité ; il faut
le canal **1h**.

### A5. RSIBollingerReversion
**Intuition.** Rebond contre-tendance après survente.
**Formule.** Entrée long si simultanément `RSI(14) < 30`, `C < BB_lower`,
`z-score < −2` où `BB = SMA(20) ± 2σ`, `z = (C − SMA)/σ`, filtre `C > EMA_1h`
(pas contre une grosse tendance). Sortie `stop_loss_pct / take_profit_pct`.
**Alpha.** Sur-réaction court terme → réversion vers la moyenne.
**Verdict.** ⚠️ ~0 trade en backtest (filtres rares). Réversion validée seulement
sur LIT 15m (marginal). Basse priorité.

### A6. BreakoutControlled
**Intuition.** Cassure de résistance « propre » (volatilité contrôlée).
**Formule.** `C_t > résistance(lookback)` avec ratio de volatilité
`vr = σ_court/σ_long ≥ vr_min` et force de clôture `close_strength ≥ cs_min` ;
cassure bornée `bo_pct ≤ bo_max`. Stop sous la résistance, TP `take_profit_pct`.
**Alpha.** Cassures « contrôlées » (pas en sur-extension) prolongent mieux.
**Verdict.** ❓ 0 trade en backtest 15m. Non testé sérieusement.

### A7. RotationMomentum
**Intuition.** Détenir en permanence le top-K momentum, tourner à chaque rebalance.
**Formule.** `mom(i) = r sur momentum_lookback` (24 barres) → tri → long top-K (3),
rebalance toutes les `rebalance_minutes`, CLOSE les coins sortis du classement.
**Alpha.** Idem A2 (momentum), version « portefeuille tournant ».
**Verdict.** ⚠️ Même faiblesse que MomentumLS sur cet univers.

---

## Famille B — Microstructure / order-flow (secondes)

> Constat transversal (probe IC + paper) : la **direction** courte est portée par
> `obi_10` et `microprice_pressure` (IC +0.32 à 15 s sur BTC), **mais** le mouvement
> (~1-3 bps) est < coût → non rentable net sur majors. D'où l'importance du
> **gate de coût**.

### B1. OBImbalanceScalper
**Intuition.** Un déséquilibre de profondeur du carnet précède un micro-mouvement
de prix dans le sens du côté lourd.
**Formule.**
```
imbalance = (bid_depth − ask_depth) / (bid_depth + ask_depth)  ∈ [−1, +1]
long si imbalance > θ (0.30) pendant k updates consécutifs ; short si < −θ
```
Sortie : `imbalance_reversed`, `stop_loss_pct` (0.4%), `take_profit_pct` (0.3%),
`max_hold_seconds` (120).
**Alpha.** Pression de carnet → continuation à très court terme.
**Verdict.** ❌ Paper : 63 trades, AvgGross +0.8 bps < coût, WR 14%, sortie en
34 s (l'imbalance se retourne = bruit). Edge réel mais **sous le coût**.

### B2. MeanReversionKalman
**Intuition.** Estimer une « juste valeur » lissée par filtre de Kalman ; trader la
réversion quand le prix s'en écarte.
**Formule.** Kalman sur le mid → fair value `fv_t` (bruit de process `q=1e-6`, bruit
d'observation `R=1e-4`). Déviation normalisée par l'innovation :
```
z_t = (mid_t − fv_t) / σ_innov
long si z ≤ −z_entry · short si z ≥ +z_entry   (z_entry=1.5)
exit si |z| ≤ z_exit (0.2) · stop si |z| ≥ z_stop (3.5)
```
**Alpha.** Mauvaise valorisation transitoire vs le fair value → réversion.
**Verdict.** ❌ Paper : 34 trades, gross ≈ 0, WR 3%, **11 stops à 0.84 s**. La
déviation **continue** au lieu de reverter → le « fair value » Kalman ne capture
pas de vraie mispricing sur des majors liquides. Calibration des stops trop serrée
(z_stop=3.5σ touché instantanément), mais surtout pas d'edge.

### B3. AlphaPressureScalper
**Intuition.** Combine plusieurs pressions micro (microprice, flow, OBI) en un
« pressure score » directionnel.
**Formule (concept).** `pressure = f(microprice_pressure, trade_imbalance, obi)` ;
entrée quand `|pressure| > seuil`. Sortie rapide sur retournement.
**Alpha.** Agrégation de signaux micro corrélés au prochain tick.
**Verdict.** ⚠️ Même mur que B1 : signal réel mais mouvement < coût.

### B4. BookFlowDivergenceReversal
**Intuition.** Quand le **flux de trades** et le **carnet** divergent (ex. flux
acheteur mais carnet vendeur lourd), le prix reverte vers le carnet.
**Formule (concept).** `divergence = sign(trade_imbalance) ≠ sign(obi)` avec
magnitude ; entrée contre le flux, vers le côté carnet.
**Alpha.** Le flux agressif épuisé face à une grosse liquidité passive → réversion.
**Verdict.** ⚠️ Désactivée. Plausible mais non validée net de coût.

### B5. AbsorptionReversal
**Intuition.** Quand un gros flux est « absorbé » par la liquidité passive sans
faire bouger le prix, c'est un signe de retournement (les agressifs s'épuisent).
**Formule (concept).** `absorption = volume_agressif / |Δprix|` élevé → réversion
dans le sens opposé au flux absorbé (proxies `absorption_buy/sell`).
**Alpha.** Épuisement des ordres marché contre un mur passif.
**Verdict.** ⚠️ Désactivée. Idée microstructure classique, à valider.

### B6. AlphaSignalDecile *(améliorée cette session)*
**Intuition.** Productionise les signaux du pipeline de découverte : surveiller UNE
feature seconde, entrer quand elle franchit son décile extrême, tenir un horizon fixe.
**Formule.** Fenêtre glissante de la feature `X` ; seuil = quantile décile :
```
long si X_t ≥ Q_{1−d}(X)   (d=0.10)   · short si X_t ≤ Q_d(X)
sortie : time-stop à horizon_s (ex. 300 s)
```
Améliorations ajoutées : **gate de coût** `rv_30s·√(H/30)·10⁴ ≥ mult·cost`,
**confirmation directionnelle** (microprice_pressure/obi doivent être d'accord),
**sizing levier** + stop anti-liquidation.
**Alpha.** Les extrêmes de `liquidity_vacuum`, `trade_imbalance`, `obi` à 120-300 s
sur altcoins prédisent le rendement futur (validé OOS sur petit échantillon).
**Verdict.** ⚠️ Edge réel mais ténu (INJ LV/TI). Paper live : ~0 net (fees mangent).
Les gates ajoutés visent à ne trader que quand mouvement > coût.

### B7. GarchVolBreakout *(construite cette session)*
**Intuition.** La vol prédit **quand** ça bouge (pas le sens). On l'utilise comme
**gate de coût** : ne trader la direction microstructure que si le mouvement prévu
dépasse le coût.
**Formule.** Vol GARCH-lite (RiskMetrics) en ligne :
```
σ²_t = (1−λ)·r²_{t-1} + λ·σ²_{t-1}   (λ=0.97)
mouvement prévu sur H : σ_t·√H  (en bps)
ENTRÉE si σ_t·√H ≥ min_edge_mult·cost  ET  |microprice_pressure| > seuil
        direction = sign(microprice_pressure), confirmée par obi_10
```
Sizing levier + stop anti-liquidation, maker-first, time-stop.
**Alpha.** GARCH(1,1) : `Var(r_t|ℱ) = ω + α r²_{t-1} + β σ²_{t-1}`. Empiriquement
`corr(σ̂, |r|) > 0` mais `corr(σ̂, signe r) ≈ 0` → la vol ne donne QUE le timing/
sizing ; l'edge directionnel vient de la microstructure, filtré par le coût.
**Verdict.** 🔬 Recherche. Logiquement la « bonne » version de l'idée burst ; à
tester en paper.

### B8. S8EMS (econophysics maker scalping)
**Intuition.** Market-making econophysique : poster des quotes des deux côtés et
capturer le spread + rebalancer selon un signal de fair value.
**Formule (concept).** Quotes `bid = fv − δ`, `ask = fv + δ` autour d'un fair value,
skew selon l'inventaire et la pression ; PnL ≈ spread capté − sélection adverse.
**Alpha.** Rente du spread bid-ask si la sélection adverse est maîtrisée.
**Verdict.** ⚠️ Désactivée. Le market-making demande des rebates/latence qu'on n'a
pas en paper ; non prioritaire.

---

## Famille C — Funding / basis / valeur relative (neutre au marché)

### C1. FundingCarryHedged
**Intuition.** Encaisser le **funding** d'un perp tout en étant couvert, pour un
rendement quasi neutre au prix.
**Formule.** Funding payé périodiquement : `PnL_funding = −f · notional` pour un
long (si `f>0` le long paie). Carry visé en shortant le perp à funding positif (on
**reçoit** `f`), couverture du delta. Rendement net :
```
carry_net = f − coûts_exécution − slippage_depth
```
Univers recalibré : CHIP/STABLE/COMP/APE (funding élevé), slippage profondeur-aware.
**Alpha.** Prime de financement structurelle payée par les longs à effet de levier.
**Verdict.** 🟡 Neutre-marché, dépend de funding élevé persistant. Le plus
« défendable » structurellement, mais capacité limitée.

### C2. FundingArbitrage / FundingArbEnhanced
**Intuition.** Arbitrer les écarts de funding entre venues/instruments.
**Formule.** `spread_funding = f_A − f_B` ; position longue là où on reçoit, short
où on paie, net du coût. Enhanced = filtres de profondeur/coût + multi-venue.
**Alpha.** Désalignement temporaire des taux de financement.
**Verdict.** 🟡 Dépend de l'accès multi-venue et de la persistance du spread.

### C3. SpotPerpBasis
**Intuition.** Le perp s'écarte du spot (basis) ; on parie sur la convergence.
**Formule.** `basis = (P_perp − P_spot)/P_spot`. Short perp / long spot si
`basis > seuil` (perp cher), inverse sinon. Converge via le funding.
**Alpha.** Mean-reversion du basis ancrée par le mécanisme de funding.
**Verdict.** 🟡 Faible basis sur majors → peu d'opportunités après coût.

### C4. RelativeValue
**Intuition.** Deux actifs co-intégrés ; trader l'écart (pairs trading).
**Formule.** `spread = P_A − β·P_B`, z-score du spread ; long A/short B si
`z < −z_entry`, inverse si `z > +z_entry`, sortie à `z≈0`.
**Alpha.** Réversion d'un spread stationnaire entre actifs liés.
**Verdict.** ⚠️ Désactivée. Demande une vraie co-intégration stable (rare en crypto).

---

## Famille D — Réplication binaire 5 min (levier)

### D1. BTC_5MIN_BINARY_REPL / BTC_BINARY_HIGHLEV
**Intuition.** Répliquer une « option binaire » 5 min : estimer la probabilité que
BTC monte sur l'horizon, entrer à levier si la proba penche assez, viser un gain en
% de marge.
**Formule.** Score de probabilité `p_up = σ(w·features)` (OBI, flow, rv, microprice).
```
long si p_up ≥ long_threshold (0.55) ET obi ≥ min_obi ET flow ≥ min_flow
notional = marge × levier (100-150x) ; TP/SL en % de marge
early-exit si p_up se retourne sous early_exit_threshold
```
**Alpha.** Si `p_up` est bien calibrée (> 0.5 quand ça monte), l'espérance à levier
est positive.
**Verdict.** ❌ Paper : 0/17 gagnants, plus gros poste de **frais** (HIGHLEV $9.45)
et de **perte** (early-exits −$14). Les early-exits coupent à 164 s sur le moindre
retournement de signal ; `p_up` n'est pas assez calibrée pour battre coût+levier.
Cf. analyse levier : à 100-150x la **liquidation est asymétrique** (gain partiel vs
perte totale de marge) → espérance négative sans edge directionnel fort. À couper.

---

## Famille E — Méta / agrégation

### E1. MetaAlpha
**Intuition.** Combiner plusieurs sous-signaux et n'agir qu'au **quorum** (consensus).
**Formule (concept).** `vote = Σ sign(signal_k)·confiance_k` ; trade si
`|vote| ≥ quorum`. Réduit le bruit d'un signal isolé.
**Alpha.** Diversification : un consensus de signaux faibles peut dépasser le coût
là où chacun seul échoue.
**Verdict.** 🟡 Ne vaut que ce que valent ses entrées. Comme les sous-signaux sont
sous le coût, le quorum l'est aussi.

---

## Famille F — Overlays / filtres (pas des stratégies autonomes)

### F1. leader_bias (filtre BTC/ETH)
**Intuition.** Ne pas prendre une position alt qui va **contre** un mouvement fort
de BTC/ETH (les alts ont β≈1.3 vs BTC).
**Formule.**
```
ret_leader = rendement BTC/ETH sur la fenêtre
veto le trade si un leader a bougé > min_bps À CONTRE-SENS du signal
```
**Alpha.** Pas un générateur d'alpha — un **filtre gratuit** (pas d'aller-retour
en plus) qui retire des entrées de mauvaise qualité.
**Verdict.** ⚠️ A/B : n'aide que les coins **couplés** à BTC (HYPE) ; **nuit** aux
coins à momentum idiosyncratique (ZEC). OFF par défaut.

---

## Synthèse — où est l'alpha ?

| Niveau | Constat |
|---|---|
| **Edge barre confirmé** | **HourlyBreakout sur ZEC** (1h, forte vol), survit au coût taker. |
| Edge micro réel mais < coût | obi/microprice à 15-30 s (majors), décile altcoins 120-300 s. Maker-first obligatoire ; gate de coût indispensable. |
| Neutre-marché structurel | FundingCarryHedged (capacité limitée). |
| Sans edge / à couper | Momentum L/S, scalpers carnet, Kalman, binaires à levier extrême. |

**Principe directeur** : le timeframe doit être assez long pour que le **mouvement
dépasse le coût**. Les pertes du paper run viennent de stratégies qui tradent **sous
le coût** et **coupent trop vite** (cf. `reports/diagnostic_run_2026-05-31.md`).
Concentrer le capital sur le seul edge net-positif (ZEC), pas diluer sur le zoo.
