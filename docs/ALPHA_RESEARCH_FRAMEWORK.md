# Alpha Research Framework

## 1. Qu'est-ce qu'un *alpha* ?

Un **alpha** est un signal `s_t^a` dont la valeur en t informe sur le
**retour futur** `r_{t,h}^a` d'un instrument `a` sur un horizon `h`, et
qui survit à :

- les **frais** (entry + exit),
- le **spread / slippage**,
- le **bêta** marché (BTC dans le cas crypto),
- les **contrôles** classiques (volatilité, OBI, spread, momentum),
- l'**out-of-sample** (walk-forward),
- la concentration mono-symbole et mono-jour.

Tant qu'aucun de ces tests n'a été passé, ce qu'on a est une
**hypothèse**, pas un alpha.

## 2. Pourquoi un signal "classique" n'est pas un alpha

Quelques exemples de pièges courants :

| Signal | Raison classique | Pourquoi ce n'est probablement pas un alpha |
|--------|------------------|----------------------------------------------|
| OBI positif → buy | "Le carnet est acheteur" | OBI corrèle avec spread étroit et liquidité — l'edge est mangé par les frais et le slippage. |
| Momentum positif → buy | "Le prix monte" | Le prix monte aussi sur BTC ⇒ bêta, pas alpha idiosyncratique. |
| Funding élevé → short | "Recevoir le funding" | La position est directionnelle ; le risque de drawdown explose le carry. |
| Spread étroit → trade plus | "Tradable" | Pure mesure de liquidité, pas de prédiction. |

Un signal devient candidat alpha **seulement** quand il bat ces
contrôles dans le notebook `research/alpha_research_hyperliquid_seconds.ipynb`.

## 3. Pipeline de recherche

```
engine_v9.py (paper, preset alpha_research)
   │
   ▼
logs/seconds_features.csv
   │
   ▼
research/alpha_research_hyperliquid_seconds.ipynb     ← exploration
scripts/run_alpha_research.py                         ← CI / reports
   │
   ▼
reports/alpha_research_report.md
   │
   ▼
décision humaine : enable=true (paper) ou keep=false
```

## 4. Forward returns

Pour chaque t et chaque horizon `h ∈ {5, 15, 30, 60, 120, 300}` s :
```
fwd_ret_<h>s = log(mid_{t+h} / mid_t)
```

Sans forward returns, on régresse un signal sur un retour passé — on
**ne** mesure pas la prédictivité, on mesure le hasard.

## 5. Coûts

Sur Hyperliquid (paper, ordres taker) :
- fee ≈ 3 bps,
- slippage ≈ 2–6 bps selon profondeur,
- spread ≈ 2–10 bps selon coin.

Coût round-trip typique : **8 à 16 bps**. Un signal qui produit
+5 bps en IC × take_profit n'est **pas** rentable.

Le notebook teste 3 niveaux de coûts (8 / 12 / 16 bps) systématiquement.

## 6. Pourquoi enlever le bêta BTC

```
r_t^a = beta * r_t^{BTC} + residual
```

Si le signal ne prédit que `r_t^a`, il peut juste prédire `r_t^{BTC}` —
ce qui revient à acheter du BTC via un alt. Ce n'est pas idiosyncratique
et ça n'amène **aucun** edge contre une exposition BTC.

Le test : recalculer l'IC contre `fwd_ret_<h>s_resid`. S'il s'effondre,
le signal est un proxy bêta — pas un alpha.

## 7. Walk-forward

Tout backtest sur un seul dataset surfit. On découpe :
- train_window = 3600 s,
- test_window  = 600 s,
- rolling.

On calibre les seuils sur le train, on évalue sur le test. Si la majorité
des fenêtres test sont positives, le signal a une chance de tenir.

## 8. Lecture du rapport

| Métrique | Bon | Mauvais |
|----------|-----|---------|
| Spearman IC | > 0.02 en valeur absolue, stable | < 0.005, change de signe |
| Bucket | quasi-monotone | bruyant, drivé par 1 décile |
| Net bps | > 0 après 12 bps de coûts | ≤ 0 |
| Profit factor | > 1.2 | < 1.0 |
| Walk-forward | > 60 % des folds positifs | < 50 % |
| Résiduel IC | non nul après BTC + controls | s'effondre |

## 9. Passage en paper trading

Un signal peut passer à `enabled=true` dans la stratégie correspondante
**seulement** quand :

1. Tous les critères du §8 sont remplis sur ≥ 5 jours de données ;
2. Le signal est testé sur ≥ 2 symboles ;
3. Les paramètres (`threshold`, `cost_bps`, `take_profit_bps`,
   `stop_loss_bps`, `cooldown_seconds`) viennent du notebook, pas
   d'une intuition ;
4. Une revue indépendante valide les paramètres ;
5. Le micro-live reste désactivé tant que le paper trading n'a pas
   produit ≥ N trades stables (N à définir, typiquement 200+).

**Aucune de ces étapes n'est automatique.**

## 10. Anti-patterns à éviter

- Calibrer un signal puis le tester sur la même fenêtre → surfit garanti.
- Comparer profit factor sans coûts → metric trompeuse.
- Trader sur 1 seul symbole pour booster les stats.
- Mélanger naïvement un signal seconds (microstructure) avec un signal
  funding (heures) — ils n'ont pas le même horizon.
- Ajouter des règles "manuelles" (no-trade le vendredi soir, etc.) en
  réaction au backtest — c'est de l'optimisation post-hoc.
