"""
backtesting/funding_adapters.py — run_fn pour FundingExtremeReversal (PHASE 5).

Aligne le funding historique (Phase 0) sur les barres 1h, calcule un percentile
glissant CAUSAL du funding, et fade le prix quand le funding est extrême. Coût
taker honnête. Zéro look-ahead : signal en t (funding/percentile sur le passé),
gross = close[t] → close[t+H].
"""
from __future__ import annotations

import numpy as np

from backtesting import data_loader
from strategies.funding_extreme_reversal import FundingExtremeReversalStrategy as _F


def funding_extreme_run_fn(interval: str = "1h"):
    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        try:
            bars = data_loader.load_historical_bars(coin, interval)
        except FileNotFoundError:
            return []
        fund = data_loader.load_funding_series(coin)        # [(ts_s, rate)]
        if len(bars) < 300 or len(fund) < 60:
            return []
        bt = np.array([b.ts for b in bars]); bc = np.array([b.close for b in bars])
        ft = np.array([t for t, _ in fund]); fr = np.array([r for _, r in fund])
        # funding aligné : dernière valeur de funding ≤ ts de barre (causal)
        idx = np.searchsorted(ft, bt, side="right") - 1
        f_on_bar = np.where(idx >= 0, fr[np.clip(idx, 0, len(fr) - 1)], np.nan)

        W = int(params["window_bars"]); H = int(params["horizon_bars"])
        hi, lo = float(params["hi_pct"]), float(params["lo_pct"])
        min_abs = float(params.get("min_abs_funding_bps", 0.5))
        notional = 1000.0
        cost_rt = 2.0 * (fee_bps + slip_bps)
        trades = []
        i = W
        n = len(bars)
        while i < n - H:
            f_now = f_on_bar[i]
            hist = f_on_bar[i - W:i]
            hist = hist[np.isfinite(hist)]
            if not np.isfinite(f_now) or len(hist) < 30:
                i += 1; continue
            sig = _F.signal_from_funding(f_now, list(hist), hi, lo, min_abs)
            if sig == 0 or bc[i] <= 0:
                i += 1; continue
            gross_bps = (bc[i + H] - bc[i]) / bc[i] * 1e4 * sig
            net_bps = gross_bps - cost_rt
            trades.append({"ts": bt[i + H], "hold_s": bt[i + H] - bt[i],
                           "net": notional * net_bps / 1e4, "notional": notional})
            i += H + 1
        return trades
    return run_fn
