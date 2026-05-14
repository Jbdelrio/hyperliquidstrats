# Alpha Models — Théorie

Ce document **formalise** les modèles d'alpha que le framework cherche à
tester. Aucune des formules ci-dessous n'est "validée" : ce sont des
hypothèses prédictives qui doivent passer le filtre du notebook
`research/alpha_research_hyperliquid_seconds.ipynb` avant d'être branchées
sur une stratégie même en paper.

## 0. Notations

| Symbole | Définition |
|--------|------------|
| `a` | asset (`BTC`, `ETH`, …) |
| `t` | instant en secondes |
| `h` | horizon futur (s) |
| `m_t^a = (bid_t^a + ask_t^a) / 2` | mid price |
| `r_{t,h}^a = log(m_{t+h}^a / m_t^a)` | log-return forward |
| `F_t` | sigma-algèbre = information disponible à `t` |

But général :
```
alpha_t^a ≈ E[r_{t,h}^a | F_t]
```

Un signal `s_t^a` est candidat alpha si :
- `Cov(s_t^a, r_{t,h}^a) ≠ 0` de manière stable hors-échantillon,
- l'edge attendu dépasse les coûts de transaction,
- l'edge survit au retrait du bêta BTC,
- l'edge n'est pas une simple proxy de spread / vol / liquidité.

---

## 1. Pressure Alpha

### 1.1 Building blocks

**Order Book Imbalance** sur les `k` premiers niveaux :
```
D_bid,t,k^a = somme(size bid_i, i=1..k)
D_ask,t,k^a = somme(size ask_i, i=1..k)
OBI_{t,k}^a = (D_bid,t,k^a - D_ask,t,k^a) / (D_bid,t,k^a + D_ask,t,k^a)
```
`OBI ∈ [-1, +1]`. Positif → carnet acheteur dominant.

**Trade Imbalance** sur fenêtre `w` :
```
V_buy,t,w^a  = somme(volume USD des trades agressifs côté B sur [t-w, t])
V_sell,t,w^a = somme(volume USD des trades agressifs côté A sur [t-w, t])
TI_{t,w}^a   = (V_buy - V_sell) / (V_buy + V_sell)
```
Convention Hyperliquid : `side == "B"` = taker buy, `side == "A"` = taker sell.

**VWAP slope** :
```
VWAP_w,t^a = somme(price * vol_usd sur fenêtre w) / somme(vol_usd sur w)
VS_t^a = VWAP_5s,t^a / VWAP_30s,t^a - 1
```

**Microprice** (Stoikov) :
```
MP_t^a = (ask_t^a * q_bid,t^a + bid_t^a * q_ask,t^a) / (q_bid,t^a + q_ask,t^a)
```
où `q_bid`, `q_ask` sont les sizes au top.

**Microprice pressure** :
```
MPP_t^a = (MP_t^a - m_t^a) / m_t^a
```
Positif → marché penche acheteur.

**Momentum court terme** :
```
MOM_{t,5s}^a = log(m_t^a / m_{t-5s}^a)
```

### 1.2 Pressure score

```
alpha_pressure,t^a =
    0.25 * OBI_{t,5}^a
  + 0.25 * TI_{t,10s}^a
  + 0.20 * tanh(lambda_1 * VS_t^a)
  + 0.15 * tanh(lambda_2 * MPP_t^a)
  + 0.15 * tanh(lambda_3 * MOM_{t,5s}^a)
```
Avec `lambda_1 = lambda_2 = lambda_3 = 1000` (saturation à ~10 bps).

Interprétation : **cohérence** entre carnet, trades, VWAP, microprice et
momentum. Un signal positif requiert plusieurs sources qui pointent dans
la même direction.

---

## 2. Book-Flow Divergence Alpha

```
alpha_div,t^a = TI_{t,10s}^a - OBI_{t,5}^a
```

Intuition : les trades agressifs disent une chose, le carnet en dit une
autre → asymétrie d'information, absorption, ou spoof.

- **Long potentiel** : `TI > 0` ET `OBI ≤ 0` (acheteurs avalent l'ask
  malgré un carnet qui semblait vendeur).
- **Short potentiel** : `TI < 0` ET `OBI ≥ 0`.

---

## 3. Absorption Alpha

```
ABS_sell,t^a = max(TI_{t,10s}^a, 0)  * max(-MOM_{t,5s}^a, 0)
ABS_buy,t^a  = max(-TI_{t,10s}^a, 0) * max( MOM_{t,5s}^a, 0)
```

- `ABS_sell > 0` : acheteurs agressifs mais prix ne monte pas →
  absorption vendeuse → signal **short**.
- `ABS_buy > 0` : vendeurs agressifs mais prix ne baisse pas →
  absorption acheteuse → signal **long**.

---

## 4. Liquidity Vacuum / Risk Filter

```
LV_t^a = z(spread_t^a) + z(RV_{t,30s}^a)
```

`z(.)` = z-score sur fenêtre glissante (par défaut 600 s).

Usage :
- **Filtre risque** : si `LV_t^a` trop haut → ne pas trader.
- **Breakout amplifier** : pondérer le signal pressure par `max(LV, 0)`,
  mais réduire la taille :
```
size_t = size_0 / (1 + max(LV_t^a, 0))
```

---

## 5. BTC Residual Alpha

Le retour brut d'un alt est largement expliqué par BTC. Pour identifier
un vrai alpha **idiosyncratique** :

```
r_{t,h}^a = beta_t^a * r_{t,h}^{BTC} + epsilon_{t,h}^a

beta_t^a = Cov_{rolling}(r^a, r^{BTC}) / Var_{rolling}(r^{BTC})
epsilon_{t,h}^a = r_{t,h}^a - beta_t^a * r_{t,h}^{BTC}
```

Tester `IC_resid = Corr(alpha_t^a, epsilon_{t,h}^a)`. Un signal qui ne
prédit que le bêta BTC n'est **pas** un alpha — c'est un proxy bêta.

---

## 6. Regime-Conditioned Alpha

Définir un régime `G_t ∈ {low_vol, normal_vol, high_vol, BTC_trending,
BTC_ranging, liquidity_vacuum, ...}` et conditionner :

```
alpha_t^a = somme_{g} 1_{G_t = g} * f_g(X_t^a)
```

Un signal peut marcher en trend et échouer en range — ne **jamais**
moyenner aveuglément.

---

## 7. Composite Alpha (microstructure-only)

```
alpha_final,t^a =
    0.40 * alpha_pressure,t^a
  + 0.20 * alpha_div,t^a
  + 0.20 * alpha_abs,t^a
  + 0.10 * alpha_resid,t^a
  + 0.10 * alpha_regime,t^a
```

Poids initiaux **manuels** : ils n'ont aucune valeur tant que chaque
sous-signal n'a pas passé son IC / bucket / walk-forward. Les poids
seront re-pondérés par le notebook (régression contrainte ou tri par IC
net après coûts).

### Modèle de coûts

```
C_t^a = fee_entry + fee_exit + spread_t^a + slippage_t^a + funding_buffer_t^a
```

Avec sur Hyperliquid (paper, taker simulé) :
- `fee_entry = fee_exit ≈ 3 bps` (placeholder, à recalibrer).
- `spread_t^a` = `(ask - bid) / mid * 10000 / 2` (half-spread).
- `slippage_t^a` ≈ 2–6 bps selon taille / book depth.

Condition d'ouverture (un trade ne se déclenche que si) :
```
|alpha_final,t^a|_bps  >  C_t^a + margin_bps
```

### Sizing

```
N_t^a = min(
    N_max,                      # plafond stratégie
    RiskUSD / (SL_bps / 10000), # taille issue du risque
    N_liquidity                 # taille issue de la liquidité top-of-book
)
```

Pour capital **500 $**, démarrer avec `N_max ∈ [5, 25] USD`, 1 position
max. **Aucun de ces nombres ne sera utilisé en live** dans cette tâche.

---

## 8. Funding Carry Alpha

### 8.1 Notation

Pour un perp `(e, a)` (exchange `e`, asset `a`) à l'instant `t` :
- `F_{e,a,t}` = funding rate horaire (taux peer-to-peer payé par les
  longs aux shorts si `F > 0`, l'inverse sinon).
- Direction qui **reçoit** le funding :
```
s_{e,a,t} = -1   si F_{e,a,t} > 0   (short reçoit)
s_{e,a,t} = +1   si F_{e,a,t} < 0   (long reçoit)
```

Sur Hyperliquid l'API renvoie un taux 8h ; on le convertit en horaire
via `hourly = raw_8h / 8` (cf. `data/hyperliquid_funding.py`).

### 8.2 Carry brut attendu sur horizon H

```
E[Carry_{e,a,t,H}] = N * somme_{i=1..H} E[abs(F_{e,a,t+i})]
```

`N` = notional USD.

### 8.3 Coûts

```
Cost_{e,a,t} = fee_entry + fee_exit
             + entry_spread_cost + exit_spread_cost
             + expected_slippage
             + funding_uncertainty_buffer
             + borrow_or_collateral_cost
```

### 8.4 Single-exchange (NON delta-neutral)

```
alpha_funding_single = E[Carry] - Cost - DirectionalRiskPenalty - FundingUncertaintyPenalty
```

`DirectionalRiskPenalty = N * sigma_{a,H} * risk_multiplier`.

**Important** : ce mode n'est pas delta-neutral. Sur petit capital
(500 $) il n'a de sens qu'en **research_only**, ou avec un hedge
externe.

### 8.5 Cross-exchange (Hyperliquid ↔ Aster)

Funding spread :
```
FundingSpread_{HL,Aster,a,t} = F_{HL,a,t} - F_{Aster,a,t}
```

Si `FundingSpread > 0` → short HL + long Aster capture le spread (longs
sur HL paient, longs sur Aster reçoivent quand `F_Aster < 0` ou paient
moins).

Carry spread brut :
```
E[CarrySpreadGross] = N * somme_{i=1..H} E[F_{short_leg,i} - F_{long_leg,i}]
```

Net :
```
CarrySpreadNet = CarrySpreadGross
               - fees_HL - fees_Aster
               - spread_cost_HL - spread_cost_Aster
               - slippage_HL - slippage_Aster
               - rebalancing_buffer
               - funding_uncertainty_buffer
```

### 8.6 Conditions d'ouverture

```
OpenCrossExchange si :
    |FundingSpread_bps|       > min_funding_spread_bps
ET  CarrySpreadNet            > min_net_carry_usd
ET  liquidity_score_HL        > min_liquidity_score
ET  liquidity_score_Aster     > min_liquidity_score
ET  |basis_diff_bps|          < max_basis_bps
ET  liquidation_buffer_HL     > min_liq_buffer
ET  liquidation_buffer_Aster  > min_liq_buffer
```

### 8.7 Conditions de fermeture

- funding spread retombe sous `close_threshold_bps`,
- `basis_risk` augmente trop,
- un leg n'est plus correctement hedge (`hedge_error > max_hedge_error_usd`),
- carry net déjà capturé `>= target_net_usd`,
- `max_hold_hours` atteint,
- liquidité dégradée.

---

## 9. Combinaison multi-horizon

Microstructure (seconds) et funding (hours) n'ont **pas** le même
horizon. Ne jamais sommer naïvement. Séparation claire :

- `MicrostructureAlphaEngine` : horizon seconds–minutes, fréquence haute.
- `FundingCarryEngine` : horizon heures–jours, fréquence basse.
- `MetaAllocator` : combine lentement (par exemple : funding sert de
  filtre directionnel pour les signaux microstructure ; ou allocation
  séparée du capital).

---

## 10. Avertissement

Aucune de ces formules n'est garantie prédictive. La théorie sert à
**définir** un signal mesurable ; seul le notebook le valide. Tant qu'un
signal n'a pas passé les critères du framework de recherche, il reste
une hypothèse — pas un alpha.
