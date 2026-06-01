import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from backtesting import walkforward as W
from backtesting.panel_adapters import (cross_sectional_reversal_run_fn,
    residual_btc_reversion_run_fn, liquidation_cascade_run_fn)
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
man = json.loads((ROOT/"data"/"historical"/"manifest.json").read_text(encoding="utf-8"))
coins = [c["name"] for c in man["top_coins"]]

def show(v):
    sig = "sig" if v.significant else "NON-sig@95%"
    print(f"  best={v.best_params} AvgNet={v.oos_avg_net_bps:+.2f}bps plateau={v.plateau} "
          f"DSR={v.dsr:.2f}({sig}) breadth={v.breadth_pos}/{v.n_coins} "
          f"stress15={v.stress.get(15.0,0):+.1f} n={v.oos_n_trades} -> {'GO' if v.go else 'NO-GO'}")
    if v.reasons: print("    rejet:", " ; ".join(v.reasons))

print("=== CrossSectionalReversal (1h) ===")
g1 = [{"lookback_bars": lb, "horizon_bars": h, "quantile": 0.25}
      for lb in (1,4,12) for h in (4,12,24)]
show(W.evaluate_strategy("CrossSectionalReversal", cross_sectional_reversal_run_fn(coins,"1h"),
     coins, g1, ["lookback_bars","horizon_bars"], interval="1h", confidence="MEDIUM", min_coins=6))

print("=== ResidualBTCReversion (1h) ===")
g2 = [{"beta_window":120,"z_window":48,"z_entry":z,"horizon_bars":h}
      for z in (1.5,2.0,2.5) for h in (4,12,24)]
show(W.evaluate_strategy("ResidualBTCReversion", residual_btc_reversion_run_fn("1h"),
     [c for c in coins if c!="BTC"], g2, ["z_entry","horizon_bars"], interval="1h",
     confidence="MEDIUM", min_coins=6))

print("=== LiquidationCascadeReversal (15m) ===")
g3 = [{"range_atr_mult":k,"vol_mult":3.0,"horizon_bars":h}
      for k in (2.0,3.0,4.0) for h in (2,4,8)]
show(W.evaluate_strategy("LiquidationCascadeReversal", liquidation_cascade_run_fn("15m"),
     coins, g3, ["range_atr_mult","horizon_bars"], interval="15m", confidence="MEDIUM", min_coins=6))
