import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from backtesting import walkforward as W
from backtesting.funding_adapters import funding_extreme_run_fn
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
man = json.loads((ROOT/"data"/"historical"/"manifest.json").read_text(encoding="utf-8"))
coins = [c["name"] for c in man["top_coins"]]
grid = [{"window_bars": w, "horizon_bars": h, "hi_pct": 0.90, "lo_pct": 0.10,
         "min_abs_funding_bps": 0.5} for w in (120,240,480) for h in (3,6,12)]
v = W.evaluate_strategy("FundingExtremeReversal", funding_extreme_run_fn("1h"),
        coins, grid, ["window_bars","horizon_bars"], interval="1h",
        confidence="HIGH", n_folds=5, min_coins=6)
sig = "significatif" if v.significant else "NON-sig@95%"
print(f"best={v.best_params} AvgNet={v.oos_avg_net_bps:+.2f}bps plateau={v.plateau} "
      f"DSR={v.dsr:.2f}({sig}) breadth={v.breadth_pos}/{v.n_coins} "
      f"stress15={v.stress.get(15.0,0):+.1f} n={v.oos_n_trades} -> {'GO' if v.go else 'NO-GO'}")
if v.reasons: print("rejet:", " ; ".join(v.reasons))
