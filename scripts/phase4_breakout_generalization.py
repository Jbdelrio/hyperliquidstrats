"""
phase4_breakout_generalization.py — l'edge breakout généralise-t-il (PHASE 4) ?

Question tranchée : le breakout 1h tient-il en OOS purgé sur le DÉCILE haut-vol du
TOP 20 (généralisation), ou seulement sur ZEC (overfit mono-actif) ?

Univers = top-K coins par ATR_bps médian (1h). Harnais walk-forward purgé +
Deflated Sharpe + plateau + stress de coût. Ne touche pas au ZEC live.

Sortie : reports/HourlyBreakout_universe.md, reports/HourlyBreakout_ZEC.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtesting import data_loader, walkforward as W
from backtesting.strategy_adapters import breakout_run_fn, _BarBreakout


def atr_bps_median(coin: str) -> float:
    try:
        bars = data_loader.load_historical_bars(coin, "1h")
    except FileNotFoundError:
        return 0.0
    if len(bars) < 30:
        return 0.0
    h = [b.high for b in bars]; l = [b.low for b in bars]; c = [b.close for b in bars]
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) / c[i])
    return float(np.median(trs) * 1e4)


def top_vol_universe(k: int = 8) -> list[str]:
    import json
    man = json.loads((ROOT / "data" / "historical" / "manifest.json").read_text(encoding="utf-8"))
    coins = [c["name"] for c in man["top_coins"]]
    scored = [(c, atr_bps_median(c)) for c in coins]
    scored = [(c, a) for c, a in scored if a > 0]
    scored.sort(key=lambda x: -x[1])
    print("ATR_bps médian (1h) — top:", [(c, round(a)) for c, a in scored[:k]])
    return [c for c, _ in scored[:k]]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    grid = [{"donchian_period": p, "hold_bars": h, "min_atr_bps": 25.0,
             "min_cost_ratio": 2.0, "both_directions": True, "notional": 1000.0}
            for p in (10, 15, 20, 30, 40) for h in (2, 4, 6, 12)]
    axes = ["donchian_period", "hold_bars"]
    run_fn = breakout_run_fn(interval="1h", leverage=5.0)

    # 1) Univers haut-vol (généralisation)
    universe = top_vol_universe(k=8)
    print(f"\n=== Harnais breakout 1h — univers haut-vol {universe} ===")
    v_uni = W.evaluate_strategy(
        "HourlyBreakout_universe", run_fn, universe, grid, axes,
        interval="1h", confidence="HIGH", n_folds=5, embargo_frac=0.02,
        min_coins=4)
    print(f"  best={v_uni.best_params} AvgNet={v_uni.oos_avg_net_bps:+.2f}bps "
          f"plateau={v_uni.plateau} DSR={v_uni.dsr:.2f} breadth={v_uni.breadth_pos}/{v_uni.n_coins} "
          f"stress15={v_uni.stress.get(15.0,0):+.1f} → {'GO' if v_uni.go else 'NO-GO'}")

    # 2) ZEC seul (test overfit mono-actif)
    print(f"\n=== Harnais breakout 1h — ZEC seul ===")
    v_zec = W.evaluate_strategy(
        "HourlyBreakout_ZEC", run_fn, ["ZEC"], grid, axes,
        interval="1h", confidence="HIGH", n_folds=5, embargo_frac=0.02,
        min_coins=1)
    print(f"  best={v_zec.best_params} AvgNet={v_zec.oos_avg_net_bps:+.2f}bps "
          f"plateau={v_zec.plateau} DSR={v_zec.dsr:.2f} "
          f"stress15={v_zec.stress.get(15.0,0):+.1f} → {'GO' if v_zec.go else 'NO-GO'}")

    # 3) verdict de généralisation
    print("\n=== VERDICT GÉNÉRALISATION ===")
    if v_uni.go:
        print("  L'edge breakout GÉNÉRALISE sur le décile haut-vol (pas un overfit ZEC).")
    elif v_zec.go and not v_uni.go:
        print("  ⚠️ L'edge tient sur ZEC mais NE généralise PAS → suspicion d'overfit mono-actif.")
    else:
        print("  L'edge ne passe pas le harnais OOS purgé (ni univers ni ZEC).")
    print("Rapports -> reports/HourlyBreakout_universe.md, reports/HourlyBreakout_ZEC.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
