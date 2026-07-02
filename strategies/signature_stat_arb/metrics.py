"""Performance metrics (§22). Turnover convention is documented explicitly:

    turnover = sum over bars of |change in gross notional| (both legs), in USD.
    ROT_bps  = 1e4 * net_pnl / turnover.

Sharpe/Sortino are annualised from the bar PnL series using the number of bars per
year implied by the bar seconds.
"""
from __future__ import annotations

from typing import Dict, Optional
import numpy as np


def _ann_factor(bar_seconds: float) -> float:
    return np.sqrt(365.0 * 24 * 3600 / max(bar_seconds, 1e-9))


def compute_metrics(pnl: np.ndarray, equity: np.ndarray, turnover: float,
                    n_trades: int, bar_seconds: float, holding_times: Optional[np.ndarray] = None,
                    fees_total: float = 0.0, funding_total: float = 0.0,
                    initial_capital: float = 1000.0, extra: Optional[Dict] = None) -> Dict:
    pnl = np.asarray(pnl, float)
    pnl = pnl[np.isfinite(pnl)]
    net = float(pnl.sum())
    mu, sd = (float(pnl.mean()), float(pnl.std(ddof=1))) if len(pnl) > 2 else (0.0, 0.0)
    ann = _ann_factor(bar_seconds)
    sharpe = ann * mu / sd if sd > 0 else np.nan
    downside = pnl[pnl < 0]
    dd_std = downside.std(ddof=1) if len(downside) > 2 else 0.0
    sortino = ann * mu / dd_std if dd_std > 0 else np.nan
    eq = np.asarray(equity, float)
    peak = np.maximum.accumulate(eq) if len(eq) else np.array([initial_capital])
    dd = (peak - eq) / peak if len(eq) else np.array([0.0])
    max_dd = float(np.nanmax(dd)) if len(dd) else 0.0
    ret = net / initial_capital if initial_capital else np.nan
    calmar = (ret / max_dd) if max_dd > 1e-9 else np.nan
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    hit = float(len(wins) / len(pnl)) if len(pnl) else np.nan
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else np.nan
    rot_bps = 1e4 * net / turnover if turnover > 0 else np.nan
    out = {
        "net_pnl": net, "return_pct": 100 * ret if np.isfinite(ret) else np.nan,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "max_drawdown_pct": 100 * max_dd,
        "hit_ratio": hit, "profit_factor": profit_factor,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "turnover_usd": float(turnover), "return_on_turnover_bps": rot_bps,
        "n_trades": int(n_trades), "fees_total": float(fees_total),
        "funding_total": float(funding_total),
        "avg_holding_bars": float(np.mean(holding_times)) if holding_times is not None and len(holding_times) else np.nan,
        "median_holding_bars": float(np.median(holding_times)) if holding_times is not None and len(holding_times) else np.nan,
        "volatility_bar": sd, "final_equity": float(eq[-1]) if len(eq) else initial_capital,
    }
    if extra:
        out.update(extra)
    return out
