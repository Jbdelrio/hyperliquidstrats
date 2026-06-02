"""
watch_strategy_performance.py — live performance watcher for the paper run.

Every `--interval` minutes it:
  1. regenerates reports/strategy_performance.md (full per-strategy/per-symbol
     breakdown, via scripts/analyze_strategy_performance.py),
  2. prints a compact one-line summary per strategy (trades, net, AvgGross bps,
     WR) so you can eyeball the honest edge vs the round-trip cost,
  3. appends that summary to reports/strategy_perf_timeline.csv so the AvgGross
     TREND over the run is itself tracked (the early-warning of decay).

Zero model cost — run it once in a terminal alongside the engine and leave it.

Usage (PowerShell):
    python scripts/watch_strategy_performance.py --interval 10
    python scripts/watch_strategy_performance.py --once          # single pass
    python scripts/watch_strategy_performance.py --cost_bps 9 --strategy H1Breakout_ZEC
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FILLS = ROOT / "logs" / "fills_v9.csv"
OUT_MD = ROOT / "reports" / "strategy_performance.md"
TIMELINE = ROOT / "reports" / "strategy_perf_timeline.csv"
ANALYZER = ROOT / "scripts" / "analyze_strategy_performance.py"


def regenerate_full_report(fills: Path, out: Path) -> bool:
    """Run the existing analyzer to refresh the full markdown report."""
    try:
        r = subprocess.run(
            [sys.executable, str(ANALYZER), "--fills", str(fills), "--out", str(out)],
            capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception as e:
        print(f"  [warn] analyzer failed: {e}")
        return False


def compact_summary(fills_path: Path, cost_bps: float) -> pd.DataFrame:
    """Per-strategy compact metrics from the raw fills, incl. AvgGross bps."""
    if not fills_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(fills_path)
    except Exception:
        return pd.DataFrame()
    if df.empty or "net" not in df.columns:
        return pd.DataFrame()
    df["net"] = pd.to_numeric(df["net"], errors="coerce").fillna(0.0)
    df["fee"] = pd.to_numeric(df.get("fee"), errors="coerce").fillna(0.0)
    df["gross"] = pd.to_numeric(df.get("gross"), errors="coerce").fillna(df["net"] + df["fee"])
    df["notional"] = pd.to_numeric(df.get("notional"), errors="coerce").replace(0, np.nan)
    df["gross_bps"] = df["gross"] / df["notional"] * 1e4
    strat = df["strategy"] if "strategy" in df.columns else pd.Series(["?"] * len(df))
    df = df.assign(strategy=strat)
    g = df.groupby("strategy").agg(
        trades=("net", "size"),
        net=("net", "sum"),
        avg_gross_bps=("gross_bps", "mean"),
        win_rate=("net", lambda x: float((x > 0).mean()) * 100),
    ).reset_index()
    # edge vs cost: AvgGross must clear the round-trip cost
    g["edge_vs_cost"] = g["avg_gross_bps"] - cost_bps
    g["verdict"] = np.where(g["avg_gross_bps"] >= cost_bps + 1, "OK",
                    np.where(g["avg_gross_bps"] >= cost_bps * 0.6, "WATCH", "CUT?"))
    return g.sort_values("net", ascending=False)


def append_timeline(g: pd.DataFrame) -> None:
    """Append every strategy's snapshot so the AvgGross trend of each is tracked."""
    if g.empty:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = g.assign(ts=now)[["ts", "strategy", "trades", "net",
                            "avg_gross_bps", "win_rate", "verdict"]]
    header = not TIMELINE.exists()
    out.to_csv(TIMELINE, mode="a", header=header, index=False)


def one_pass(cost_bps: float, focus: str | None) -> None:
    ok = regenerate_full_report(FILLS, OUT_MD)
    g = compact_summary(FILLS, cost_bps)
    stamp = datetime.now().strftime("%H:%M:%S")
    if g.empty:
        print(f"[{stamp}] no fills yet (waiting for {FILLS.name}) "
              f"{'[report ok]' if ok else ''}")
        return
    print(f"[{stamp}] cost ref {cost_bps:.0f}bps RT — AvgGross must clear it:")
    for _, r in g.iterrows():
        flag = {"OK": "✅", "WATCH": "⚠️ ", "CUT?": "❌"}.get(r["verdict"], "")
        print(f"   {flag} {r['strategy']:20s} n={int(r['trades']):4d}  "
              f"net=${r['net']:+8.2f}  AvgGross={r['avg_gross_bps']:+6.1f}bps  "
              f"(edge {r['edge_vs_cost']:+5.1f})  WR={r['win_rate']:.0f}%")
    append_timeline(g)
    _arena_alert()


_PREV_READY: set = set()


def _arena_alert() -> None:
    """Recalcule l'Arena (met à jour runtime/strategy_arena.json pour le GUI 🏆) et
    alerte dès qu'une stratégie devient LIVE-READY (GO OOS + AvgGross live ≥ coût)."""
    global _PREV_READY
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.strategy_arena import build_arena
        a = build_arena()
    except Exception as e:
        print(f"   [arena] recalcul échoué: {e.__class__.__name__}")
        return
    ready = {r["strategy"] for r in a["strategies"] if r["status"] == "LIVE_READY"}
    print(f"   [arena] LIVE-READY: {len(ready)} · {a['recommendation'][:88]}")
    for s in sorted(ready - _PREV_READY):
        print(f"   🟢🟢🟢 NOUVELLE STRAT LIVE-READY : {s} → éligible promotion live !")
    _PREV_READY = ready


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10.0, help="minutes between passes")
    ap.add_argument("--cost_bps", type=float, default=9.0, help="round-trip cost reference (taker=9)")
    ap.add_argument("--strategy", default="H1Breakout_ZEC", help="strategy to track in the timeline CSV (others still shown)")
    ap.add_argument("--fills", default=None, help="override fills CSV path (default logs/fills_v9.csv)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    args = ap.parse_args()

    global FILLS
    if args.fills:
        FILLS = Path(args.fills)

    print(f"watch_strategy_performance — every {args.interval:.0f} min · cost ref "
          f"{args.cost_bps:.0f}bps · focus {args.strategy}")
    print(f"  full report  -> {OUT_MD.relative_to(ROOT)}")
    print(f"  AvgGross trend -> {TIMELINE.relative_to(ROOT)}")
    print("  Ctrl+C to stop.\n")

    if args.once:
        one_pass(args.cost_bps, args.strategy)
        return 0
    try:
        while True:
            one_pass(args.cost_bps, args.strategy)
            time.sleep(max(60.0, args.interval * 60.0))
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
