"""
backtesting/metrics.py — Pure functions for trade-level metrics.

A "trade" is a dict with at least:
  ts, symbol, strategy, side, notional, entry, exit, gross, fee, net, hold_s, reason
"""
from __future__ import annotations

import math
from collections import defaultdict


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) else default


def _max_drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough drawdown of an equity curve, in absolute USD."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    dd_max = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd_max = max(dd_max, peak - e)
    return dd_max


def _sharpe_approx(returns: list[float]) -> float:
    """Naive Sharpe — mean/std of per-trade returns. Returns 0 if not meaningful."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var  = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std  = math.sqrt(var)
    return _safe_div(mean, std, 0.0)


def compute_metrics(trades: list[dict]) -> dict:
    """
    Aggregate trade-level metrics. Returns a dict with:
      total_pnl, win_rate, avg_win, avg_loss, expectancy, profit_factor,
      max_drawdown, sharpe_approx, trades_per_day, avg_hold_time_s,
      pnl_by_symbol, pnl_by_strategy, exit_reason_dist
    """
    if not trades:
        return {
            "n_trades":         0,
            "total_pnl":        0.0,
            "win_rate":         0.0,
            "avg_win":          0.0,
            "avg_loss":         0.0,
            "expectancy":       0.0,
            "profit_factor":    0.0,
            "max_drawdown":     0.0,
            "sharpe_approx":    0.0,
            "trades_per_day":   0.0,
            "avg_hold_time_s":  0.0,
            "pnl_by_symbol":    {},
            "pnl_by_strategy":  {},
            "exit_reason_dist": {},
        }

    nets = [float(t.get("net", 0) or 0) for t in trades]
    notionals = [float(t.get("notional", 0) or 0) for t in trades]
    holds = [float(t.get("hold_s", 0) or 0) for t in trades]

    total_pnl = sum(nets)
    wins  = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = 100.0 * len(wins) / len(nets) if nets else 0.0
    profit_factor = _safe_div(sum(wins), abs(sum(losses)), default=0.0)
    expectancy = total_pnl / len(nets) if nets else 0.0

    # Build cumulative equity from chronological order if ts is present.
    sorted_trades = sorted(trades, key=lambda t: float(t.get("ts", 0) or 0))
    eq = []
    running = 0.0
    for t in sorted_trades:
        running += float(t.get("net", 0) or 0)
        eq.append(running)
    max_dd = _max_drawdown(eq)

    # Trade returns for naive Sharpe (% of notional)
    rets = []
    for t in trades:
        n = float(t.get("notional", 0) or 0)
        if n > 0:
            rets.append(float(t.get("net", 0) or 0) / n)
    sharpe = _sharpe_approx(rets)

    # Trades per day
    ts_vals = [float(t.get("ts", 0) or 0) for t in trades
               if t.get("ts") not in (None, 0, "0")]
    trades_per_day = 0.0
    if ts_vals:
        span_s = max(ts_vals) - min(ts_vals)
        if span_s > 0:
            trades_per_day = len(trades) * 86400.0 / span_s

    avg_hold = sum(holds) / len(holds) if holds else 0.0

    # Per-symbol / per-strategy PnL
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    pnl_by_strategy: dict[str, float] = defaultdict(float)
    exit_reason_dist: dict[str, int] = defaultdict(int)
    for t in trades:
        sym = str(t.get("symbol", ""))
        strat = str(t.get("strategy", ""))
        reason = str(t.get("reason", ""))
        n = float(t.get("net", 0) or 0)
        if sym:
            pnl_by_symbol[sym] += n
        if strat:
            pnl_by_strategy[strat] += n
        if reason:
            exit_reason_dist[reason] += 1

    return {
        "n_trades":         len(trades),
        "total_pnl":        round(total_pnl, 4),
        "win_rate":         round(win_rate, 2),
        "avg_win":          round(avg_win, 4),
        "avg_loss":         round(avg_loss, 4),
        "expectancy":       round(expectancy, 4),
        "profit_factor":    round(profit_factor, 3),
        "max_drawdown":     round(max_dd, 4),
        "sharpe_approx":    round(sharpe, 3),
        "trades_per_day":   round(trades_per_day, 2),
        "avg_hold_time_s":  round(avg_hold, 1),
        "pnl_by_symbol":    {k: round(v, 4) for k, v in pnl_by_symbol.items()},
        "pnl_by_strategy":  {k: round(v, 4) for k, v in pnl_by_strategy.items()},
        "exit_reason_dist": dict(exit_reason_dist),
    }
