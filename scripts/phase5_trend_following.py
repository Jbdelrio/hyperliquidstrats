"""
phase5_trend_following.py — validation OOS de TrendFollowingVolTarget (PHASE 5).

Harnais walk-forward purgé sur le suivi de tendance EMA-cross en 4h (confidence
HIGH, dérivé du 1h 180j) sur le TOP 20. Sweep (ema_fast × ema_slow). Le sizing
vol-target n'affecte pas l'edge net/trade → on mesure l'edge directionnel.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtesting import walkforward as W
from backtesting.strategy_adapters import ema_cross_run_fn


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    man = json.loads((ROOT / "data" / "historical" / "manifest.json").read_text(encoding="utf-8"))
    coins = [c["name"] for c in man["top_coins"]]   # TOP 20 complet

    grid = [{"ema_fast": f, "ema_slow": s, "min_atr_bps": 20.0,
             "max_hold_bars": 60, "notional": 1000.0}
            for f in (5, 10, 20) for s in (30, 50, 100) if f < s]
    axes = ["ema_fast", "ema_slow"]

    print(f"=== TrendFollowingVolTarget — EMA-cross 4h, TOP 20 ({len(coins)} coins) ===")
    v = W.evaluate_strategy(
        "TrendFollowingVolTarget", ema_cross_run_fn("4h"), coins, grid, axes,
        interval="4h", confidence="HIGH", n_folds=5, embargo_frac=0.02, min_coins=6)
    sig = "significatif" if v.significant else "NON significatif@95%"
    print(f"  best={v.best_params} AvgNet={v.oos_avg_net_bps:+.2f}bps "
          f"plateau={v.plateau} DSR={v.dsr:.2f}({sig}) "
          f"breadth={v.breadth_pos}/{v.n_coins} stress15={v.stress.get(15.0,0):+.1f} "
          f"n_trades={v.oos_n_trades} → {'GO' if v.go else 'NO-GO'}")
    if v.reasons:
        print("  rejet:", " ; ".join(v.reasons))
    if v.warnings:
        print("  ⚠️ ", " ; ".join(v.warnings))
    print("Rapport -> reports/TrendFollowingVolTarget.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
