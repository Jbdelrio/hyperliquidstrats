"""
backtesting/walkforward.py — Harnais anti-overfit (PHASE 2).

Rend des verdicts GO / NO-GO sur l'OUT-OF-SAMPLE du TOP 20, JAMAIS en tunant sur
l'in-sample. Composants :

  1. Walk-forward PURGÉ : la série est découpée en n_folds blocs temporels ; les
     blocs ≥ 2 sont OOS (le 1er bloc sert d'amorçage/train uniquement). Un EMBARGO
     retire le début de chaque bloc test (anti-fuite par fenêtre glissante). On
     n'évalue QUE les trades dont l'ENTRÉE tombe dans un bloc OOS hors embargo.
  2. Robustesse paramétrique : sweep → métrique OOS par config → détection de
     PLATEAU (les voisins du meilleur sont ~aussi bons) vs PIC isolé (overfit→rejet).
  3. Multiple-testing : Deflated Sharpe Ratio (Bailey & López de Prado) pénalisé
     par le nombre de configs testées ; avertissement si non significatif.
  4. Confiance pondérée par la qualité des données (LOW en 1m ⇒ verdict provisoire).
  5. Critères GO (TOUS requis) : AvgNet_bps OOS > 0 après 14 bps ; plateau ; survit
     au stress 6→15 bps ; Deflated Sharpe significatif ; tient sur ≥ min_coins.

Découplage : le harnais ne sait pas COMMENT les trades sont produits. Chaque
stratégie fournit un `run_fn(params, coin, fee_bps, slippage_bps) -> list[trade]`
(trade = dict avec au moins ts (sortie), hold_s, net, notional). En interne ces
adaptateurs utilisent le BacktestEngine véridique (PHASE 1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from scipy.stats import norm, skew as _skew, kurtosis as _kurt

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

RunFn = Callable[[dict, str, float, float], list]

# RT bps = 2*(fee+slip). Pour viser un coût RT donné on met fee=slip=rt/4.
DEFAULT_BASELINE_RT = 14.0
DEFAULT_STRESS_RT = (6.0, 9.0, 12.0, 15.0)
_EMC = 0.5772156649015329   # Euler–Mascheroni


# ───────────────────────── walk-forward purgé ───────────────────────────────

def fold_bounds(entry_ts: list[float], n_folds: int) -> list[tuple]:
    if not entry_ts:
        return []
    lo, hi = min(entry_ts), max(entry_ts)
    edges = np.linspace(lo, hi, n_folds + 1)
    return [(float(edges[i]), float(edges[i + 1])) for i in range(n_folds)]


def oos_filter(trades: list, bounds: list[tuple], embargo_frac: float,
               skip_first: bool = True) -> list:
    """Garde les trades dont l'ENTRÉE (ts − hold_s) tombe dans un bloc OOS,
    hors zone d'embargo (début de bloc). Le 1er bloc est exclu (train/amorçage)."""
    if not bounds:
        return list(trades)
    blocks = bounds[1:] if skip_first and len(bounds) > 1 else bounds
    out = []
    for t in trades:
        entry_ts = float(t.get("ts", 0)) - float(t.get("hold_s", 0) or 0)
        for (a, b) in blocks:
            emb = (b - a) * embargo_frac
            if (a + emb) <= entry_ts <= b:
                out.append(t)
                break
    return out


# ───────────────────────── métriques OOS ────────────────────────────────────

def net_bps_returns(trades: list) -> list[float]:
    return [float(t["net"]) / float(t["notional"]) * 1e4
            for t in trades if float(t.get("notional", 0) or 0) > 0]


def sharpe_per_trade(returns: list[float]) -> float:
    r = np.asarray(returns, float)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1))


def deflated_sharpe(returns: list[float], n_trials: int,
                    sr_trials_std: float) -> tuple[float, float, float]:
    """Retourne (DSR_proba, SR_observé, SR0_seuil). DSR_proba = P(SR_vrai > SR0)
    en tenant compte de la non-normalité (skew/kurtosis) et du multiple-testing."""
    r = np.asarray(returns, float)
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return 0.0, 0.0, 0.0
    sr = float(r.mean() / r.std(ddof=1))
    sk = float(_skew(r))
    ku = float(_kurt(r, fisher=False))   # kurtosis non-excessive
    if n_trials >= 2 and sr_trials_std > 0:
        z1 = norm.ppf(1.0 - 1.0 / n_trials)
        z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
        sr0 = sr_trials_std * ((1.0 - _EMC) * z1 + _EMC * z2)
    else:
        sr0 = 0.0
    den = math.sqrt(max(1e-12, 1.0 - sk * sr + (ku - 1.0) / 4.0 * sr * sr))
    dsr = float(norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))
    return dsr, sr, float(sr0)


# ───────────────────────── plateau (anti-pic) ───────────────────────────────

def _param_key(p: dict, axes: list[str]) -> tuple:
    return tuple(p[a] for a in axes)


def detect_plateau(grid_metric: dict, best_key: tuple, axes: list[str],
                   frac_required: float = 0.5) -> tuple[bool, list[float]]:
    """grid_metric: {param_tuple: metric_OOS}. Plateau si ≥ frac_required des
    voisins immédiats (±1 cran sur un axe) ont une métrique ≥ max(0, 0.5×best)."""
    best = grid_metric[best_key]
    axis_vals = [sorted({k[i] for k in grid_metric}) for i in range(len(axes))]
    neighbors = []
    for i in range(len(axes)):
        vals = axis_vals[i]
        try:
            idx = vals.index(best_key[i])
        except ValueError:
            continue
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(vals):
                nk = list(best_key); nk[i] = vals[j]; nk = tuple(nk)
                if nk in grid_metric:
                    neighbors.append(grid_metric[nk])
    if not neighbors:
        return False, []
    thr = max(0.0, 0.5 * best)
    good = sum(1 for v in neighbors if v >= thr)
    return (good / len(neighbors)) >= frac_required, neighbors


# ───────────────────────── évaluation principale ────────────────────────────

@dataclass
class StratVerdict:
    name: str
    interval: str
    confidence: str
    best_params: dict
    n_trials: int
    oos_avg_net_bps: float
    oos_n_trades: int
    breadth_pos: int
    n_coins: int
    plateau: bool
    dsr: float
    sr: float
    sr0: float
    stress: dict             # {rt_bps: avg_net_bps}
    per_coin: dict           # {coin: avg_net_bps}
    go: bool
    reasons: list = field(default_factory=list)


def evaluate_strategy(
    name: str, run_fn: RunFn, coins: list[str], param_grid: list[dict],
    sweep_axes: list[str], interval: str, confidence: str, *,
    n_folds: int = 5, embargo_frac: float = 0.02, min_coins: int = 3,
    baseline_rt: float = DEFAULT_BASELINE_RT, stress_rt: tuple = DEFAULT_STRESS_RT,
    dsr_significant: float = 0.95, write_report: bool = True,
) -> StratVerdict:
    fee_base = slip_base = baseline_rt / 4.0   # RT = 2*(fee+slip)

    # 1) chaque config → OOS agrégé (tous coins) + par coin
    per_config: dict = {}   # key -> {avg_net_bps, returns, per_coin, n}
    for params in param_grid:
        key = _param_key(params, sweep_axes)
        all_oos, per_coin = [], {}
        for coin in coins:
            try:
                trades = run_fn(params, coin, fee_base, slip_base)
            except Exception:
                trades = []
            bounds = fold_bounds([float(t["ts"]) - float(t.get("hold_s", 0) or 0)
                                  for t in trades], n_folds)
            oos = oos_filter(trades, bounds, embargo_frac)
            all_oos.extend(oos)
            rc = net_bps_returns(oos)
            per_coin[coin] = float(np.mean(rc)) if rc else 0.0
        rets = net_bps_returns(all_oos)
        per_config[key] = {
            "params": params,
            "avg_net_bps": float(np.mean(rets)) if rets else 0.0,
            "returns": rets, "per_coin": per_coin, "n": len(all_oos),
        }

    # 2) meilleur config (par AvgNet_bps OOS) + dispersion des SR (déflation)
    best_key = max(per_config, key=lambda k: per_config[k]["avg_net_bps"])
    best = per_config[best_key]
    sr_trials = [sharpe_per_trade(c["returns"]) for c in per_config.values()
                 if len(c["returns"]) >= 2]
    sr_trials_std = float(np.std(sr_trials, ddof=1)) if len(sr_trials) >= 2 else 0.0

    # 3) plateau
    grid_metric = {k: v["avg_net_bps"] for k, v in per_config.items()}
    plateau, neighbors = detect_plateau(grid_metric, best_key, sweep_axes)

    # 4) Deflated Sharpe sur le meilleur OOS
    dsr, sr, sr0 = deflated_sharpe(best["returns"], len(param_grid), sr_trials_std)

    # 5) stress de coût sur le meilleur config
    stress = {}
    for rt in stress_rt:
        f = s = rt / 4.0
        all_oos = []
        for coin in coins:
            try:
                trades = run_fn(best["params"], coin, f, s)
            except Exception:
                trades = []
            bounds = fold_bounds([float(t["ts"]) - float(t.get("hold_s", 0) or 0)
                                  for t in trades], n_folds)
            all_oos.extend(oos_filter(trades, bounds, embargo_frac))
        rr = net_bps_returns(all_oos)
        stress[rt] = float(np.mean(rr)) if rr else 0.0

    # 6) breadth
    breadth_pos = sum(1 for v in best["per_coin"].values() if v > 0)

    # 7) critères GO
    reasons = []
    c_net = best["avg_net_bps"] > 0
    c_plateau = plateau
    c_stress = stress.get(max(stress_rt), -1) > 0 if stress else False
    c_dsr = dsr >= dsr_significant
    c_breadth = breadth_pos >= min_coins
    if not c_net: reasons.append(f"AvgNet_bps OOS ≤ 0 ({best['avg_net_bps']:.2f})")
    if not c_plateau: reasons.append("pas de plateau (pic isolé = overfit)")
    if not c_stress: reasons.append(f"ne survit pas au stress {max(stress_rt):.0f}bps ({stress.get(max(stress_rt),0):.2f})")
    if not c_dsr: reasons.append(f"Deflated Sharpe non significatif (DSR={dsr:.2f} < {dsr_significant})")
    if not c_breadth: reasons.append(f"breadth insuffisante ({breadth_pos}/{len(coins)} coins > 0, requis ≥{min_coins})")
    go = c_net and c_plateau and c_stress and c_dsr and c_breadth

    verdict = StratVerdict(
        name=name, interval=interval, confidence=confidence,
        best_params=best["params"], n_trials=len(param_grid),
        oos_avg_net_bps=best["avg_net_bps"], oos_n_trades=best["n"],
        breadth_pos=breadth_pos, n_coins=len(coins), plateau=plateau,
        dsr=dsr, sr=sr, sr0=sr0, stress=stress, per_coin=best["per_coin"],
        go=go, reasons=reasons,
    )
    if write_report:
        _write_report(verdict, per_config, sweep_axes, best_key)
    return verdict


def _write_report(v: StratVerdict, per_config: dict, axes: list[str], best_key: tuple) -> None:
    L = [f"# Verdict OOS — {v.name}\n",
         f"*Intervalle {v.interval} · confidence données **{v.confidence}** · "
         f"{v.n_trials} configs testées · {v.n_coins} coins (TOP 20)*\n"]
    tag = "✅ **GO**" if v.go else "❌ **NO-GO**"
    if v.go and v.confidence == "LOW":
        tag = "🟡 **GO (PROVISOIRE — données LOW)**"
    L.append(f"## {tag}\n")
    if v.reasons:
        L.append("**Raisons du rejet :** " + " ; ".join(v.reasons) + "\n")
    L.append("## Métriques (meilleur config, OOS purgé)\n")
    L.append(f"- Params : `{v.best_params}`")
    L.append(f"- **AvgNet_bps OOS** : {v.oos_avg_net_bps:+.2f} bps/trade (après 14 bps) · {v.oos_n_trades} trades OOS")
    L.append(f"- Plateau paramétrique : {'oui ✅' if v.plateau else 'non ❌ (pic isolé)'}")
    L.append(f"- Deflated Sharpe : DSR={v.dsr:.3f} (SR={v.sr:.3f} vs seuil SR0={v.sr0:.3f}) "
             f"→ {'significatif ✅' if v.dsr >= 0.95 else 'NON significatif ⚠️'}")
    L.append(f"- Breadth : {v.breadth_pos}/{v.n_coins} coins à AvgNet>0")
    L.append("\n### Stress de coût (AvgNet_bps OOS par coût RT)\n")
    L.append("| RT bps | " + " | ".join(f"{int(k)}" for k in sorted(v.stress)) + " |")
    L.append("|---|" + "---|" * len(v.stress))
    L.append("| AvgNet | " + " | ".join(f"{v.stress[k]:+.1f}" for k in sorted(v.stress)) + " |")
    L.append("\n### AvgNet_bps OOS par coin\n")
    L.append("| Coin | AvgNet bps |\n|---|---:|")
    for c, val in sorted(v.per_coin.items(), key=lambda x: -x[1]):
        L.append(f"| {c} | {val:+.2f} |")
    # heatmap si exactement 2 axes
    if len(axes) == 2:
        L.append(f"\n### Heatmap robustesse — {axes[0]} (lignes) × {axes[1]} (cols), AvgNet_bps OOS\n")
        xs = sorted({k[0] for k in per_config}); ys = sorted({k[1] for k in per_config})
        L.append("| " + axes[0] + "\\" + axes[1] + " | " + " | ".join(str(y) for y in ys) + " |")
        L.append("|---|" + "---|" * len(ys))
        for x in xs:
            row = [f"| {x}"]
            for y in ys:
                m = per_config.get((x, y), {}).get("avg_net_bps")
                cell = "—" if m is None else (f"**{m:+.1f}**" if (x, y) == best_key else f"{m:+.1f}")
                row.append(cell)
            L.append(" | ".join(row) + " |")
    L.append("\n*GO requiert TOUT : AvgNet OOS>0 · plateau · survit 15 bps · DSR significatif · breadth≥min. "
             "Confidence LOW (1m) ⇒ GO provisoire.*")
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{v.name}.md").write_text("\n".join(L), encoding="utf-8")
