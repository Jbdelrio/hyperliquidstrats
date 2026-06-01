"""
backtesting/panel_adapters.py — run_fn pour les stratégies multi-actifs (PHASE 5).

Trois adaptateurs validés par le harnais walk-forward purgé :
  - cross_sectional_reversal_run_fn : réversion transversale (short top-quartile /
    long bottom-quartile du rendement récent). Réutilise l'idée de
    momentum_long_short en INVERSANT les rangs.
  - residual_btc_reversion_run_fn : régresse le rendement de l'alt sur BTC
    (beta glissant), trade la réversion du résidu cumulé (market-neutral).
  - liquidation_cascade_run_fn : bougie range>k·ATR + spike de volume à contre-sens
    → fade (proxy de cascade de liquidation).

Zéro look-ahead : tout signal en t n'utilise que t et le passé ; gross = close[t]→
close[t+H]. Coût taker honnête (cost_rt = 2·(fee+slip)). Panels mis en cache.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting import data_loader

_PANEL: dict[str, pd.DataFrame] = {}


def _wide(coins: list[str], interval: str, field: str) -> pd.DataFrame:
    """Panel large {ts × coin} d'un champ OHLCV, aligné sur la grille de l'intervalle."""
    key = f"{interval}|{field}|{','.join(sorted(coins))}"
    if key in _PANEL:
        return _PANEL[key]
    series = {}
    for c in coins:
        try:
            bars = data_loader.load_historical_bars(c, interval)
        except FileNotFoundError:
            continue
        idx = [int(round(b.ts)) for b in bars]
        val = [getattr(b, {"close": "close", "high": "high", "low": "low",
                            "volume": "volume_usd"}[field]) for b in bars]
        series[c] = pd.Series(val, index=idx)
    df = pd.DataFrame(series).sort_index()
    _PANEL[key] = df
    return df


# ───────────────────────── 1) réversion transversale ────────────────────────

def cross_sectional_reversal_run_fn(coins: list[str], interval: str = "1h"):
    closes = None

    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        nonlocal closes
        if closes is None:
            closes = _wide(coins, interval, "close")
        if coin not in closes.columns:
            return []
        look = int(params["lookback_bars"]); H = int(params["horizon_bars"])
        q = float(params.get("quantile", 0.25))
        rets = np.log(closes).diff(look)            # rendement transversal sur `look`
        # rangs par ligne (0..1) ; top → short (réversion), bottom → long
        ranks = rets.rank(axis=1, pct=True)
        ts = closes.index.to_numpy()
        c = closes[coin].to_numpy(float)
        rk = ranks[coin].to_numpy(float)
        cost_rt = 2.0 * (fee_bps + slip_bps); notional = 1000.0
        trades = []
        i = look
        n = len(c)
        while i < n - H:
            if not np.isfinite(rk[i]) or not np.isfinite(c[i]) or c[i] <= 0 or c[i + H] <= 0:
                i += 1; continue
            sig = -1 if rk[i] >= 1 - q else (1 if rk[i] <= q else 0)
            if sig == 0:
                i += 1; continue
            gross_bps = (c[i + H] - c[i]) / c[i] * 1e4 * sig
            trades.append({"ts": float(ts[i + H]), "hold_s": float(ts[i + H] - ts[i]),
                           "net": notional * (gross_bps - cost_rt) / 1e4, "notional": notional})
            i += H + 1
        return trades
    return run_fn


# ───────────────────────── 2) réversion du résidu vs BTC ────────────────────

def residual_btc_reversion_run_fn(interval: str = "1h"):
    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        if coin == "BTC":
            return []
        df = _wide(["BTC", coin], interval, "close")
        if "BTC" not in df.columns or coin not in df.columns:
            return []
        df = df[["BTC", coin]].dropna()
        if len(df) < 400:
            return []
        ts = df.index.to_numpy()
        rb = np.log(df["BTC"].to_numpy(float))
        ra = np.log(df[coin].to_numpy(float))
        drb = np.diff(rb); dra = np.diff(ra)        # rendements
        W = int(params["beta_window"]); zwin = int(params["z_window"])
        H = int(params["horizon_bars"]); zthr = float(params["z_entry"])
        ca = df[coin].to_numpy(float)
        cost_rt = 2.0 * (fee_bps + slip_bps); notional = 1000.0
        trades = []
        i = max(W, zwin) + 1
        n = len(ca)
        while i < n - H:
            xs = drb[i - W:i]; ys = dra[i - W:i]     # passé seulement
            v = np.var(xs)
            beta = float(np.cov(xs, ys)[0, 1] / v) if v > 0 else 0.0
            resid = dra[i - zwin:i] - beta * drb[i - zwin:i]
            cum = float(np.sum(resid))               # résidu cumulé récent
            sd = float(np.std(resid)) * np.sqrt(zwin)
            if sd <= 0:
                i += 1; continue
            z = cum / sd
            sig = -1 if z >= zthr else (1 if z <= -zthr else 0)   # fade le résidu
            if sig == 0 or ca[i] <= 0 or ca[i + H] <= 0:
                i += 1; continue
            gross_bps = (ca[i + H] - ca[i]) / ca[i] * 1e4 * sig
            trades.append({"ts": float(ts[i + H]), "hold_s": float(ts[i + H] - ts[i]),
                           "net": notional * (gross_bps - cost_rt) / 1e4, "notional": notional})
            i += H + 1
        return trades
    return run_fn


# ───────────────────────── 3) réversion de cascade ──────────────────────────

def liquidation_cascade_run_fn(interval: str = "15m"):
    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        try:
            bars = data_loader.load_historical_bars(coin, interval)
        except FileNotFoundError:
            return []
        if len(bars) < 100:
            return []
        ts = np.array([b.ts for b in bars]); o = np.array([b.open for b in bars])
        h = np.array([b.high for b in bars]); l = np.array([b.low for b in bars])
        c = np.array([b.close for b in bars]); v = np.array([b.volume_usd for b in bars])
        k = float(params["range_atr_mult"]); m = float(params["vol_mult"])
        H = int(params["horizon_bars"]); atr_n = 14
        cost_rt = 2.0 * (fee_bps + slip_bps); notional = 1000.0
        # ATR glissant + volume moyen glissant (causal)
        trades = []
        i = max(atr_n, 20) + 1
        n = len(c)
        while i < n - H:
            trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
                   for j in range(i - atr_n, i)]
            atr = sum(trs) / atr_n
            vavg = float(np.mean(v[i - 20:i]))
            rng = h[i] - l[i]
            down = c[i] < o[i]
            if atr > 0 and vavg > 0 and rng > k * atr and v[i] > m * vavg and c[i] > 0:
                sig = 1 if down else -1            # fade la bougie de cascade
                if c[i + H] > 0:
                    gross_bps = (c[i + H] - c[i]) / c[i] * 1e4 * sig
                    trades.append({"ts": float(ts[i + H]), "hold_s": float(ts[i + H] - ts[i]),
                                   "net": notional * (gross_bps - cost_rt) / 1e4, "notional": notional})
                    i += H + 1; continue
            i += 1
        return trades
    return run_fn
