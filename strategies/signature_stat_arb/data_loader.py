"""Data loading for the signature stat-arb backtest (§3, §5, §32).

Sources:
    file   : parquet/csv with columns [ts, close] (+ optional bid, ask, ofi, funding)
    binance: public REST klines (real data), resampled to the target frequency
    demo   : a synthetic *cointegrated* pair — ALWAYS flagged is_demo=True so results
             built on it are never mistaken for real market data (§32/§37).

All series are aligned on a common integer-second grid at the configured
market-data frequency. Gaps beyond ``max_gap_seconds`` are left as NaN (not
forward-filled) so the backtest can exclude them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List
import numpy as np
import pandas as pd

from .config import DataConfig, freq_seconds


@dataclass
class PairData:
    ts: np.ndarray                    # int seconds
    p1: np.ndarray
    p2: np.ndarray
    log_p1: np.ndarray
    log_p2: np.ndarray
    factors_log: Optional[np.ndarray] = None      # (T, k)
    ofi: Optional[np.ndarray] = None              # order-flow imbalance (leg1-leg2 or leg1)
    funding: Optional[np.ndarray] = None
    book_spread_bps: Optional[np.ndarray] = None
    btc_log: Optional[np.ndarray] = None
    is_demo: bool = False
    meta: Dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.ts)


def load_pair(cfg: DataConfig) -> PairData:
    src = cfg.source
    if src == "auto":
        src = "file" if (cfg.file_1 and cfg.file_2) else ("binance" if cfg.exchange == "binance" else "demo")
    if src == "file":
        return _load_files(cfg)
    if src == "binance":
        return _load_binance(cfg)
    if src == "demo":
        return _demo_pair(cfg)
    raise ValueError(f"unknown data source {src}")


# --------------------------------------------------------------------------- #
#  File loader
# --------------------------------------------------------------------------- #
def _read_series(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_parquet(path)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("ts") or cols.get("time") or cols.get("timestamp")
    pcol = cols.get("close") or cols.get("price") or cols.get("c")
    if tcol is None or pcol is None:
        raise ValueError(f"{path}: need ts + close/price columns")
    out = pd.DataFrame({"ts": df[tcol], "close": df[pcol].astype(float)})
    t = out["ts"]
    if pd.api.types.is_datetime64_any_dtype(t):
        out["ts"] = t.view("int64") // 10 ** 9
    else:
        v = t.astype("int64")
        out["ts"] = np.where(v > 1e12, v // 1000, v)
    for opt in ("bid", "ask", "ofi", "funding"):
        if opt in cols:
            out[opt] = df[cols[opt]].astype(float)
    return out.dropna(subset=["close"]).drop_duplicates("ts").sort_values("ts")


def _load_files(cfg: DataConfig) -> PairData:
    step = freq_seconds(cfg.market_data_frequency)
    d1 = _read_series(cfg.file_1); d2 = _read_series(cfg.file_2)
    grid = _grid(min(d1["ts"].iloc[0], d2["ts"].iloc[0]),
                 max(d1["ts"].iloc[-1], d2["ts"].iloc[-1]), step)
    p1 = _on_grid(d1, grid, cfg)
    p2 = _on_grid(d2, grid, cfg)
    m = np.isfinite(p1["close"]) & np.isfinite(p2["close"])
    ts = grid[m]
    pd1 = PairData(ts=ts, p1=p1["close"][m], p2=p2["close"][m],
                   log_p1=np.log(p1["close"][m]), log_p2=np.log(p2["close"][m]),
                   book_spread_bps=p1.get("book_spread_bps"),
                   ofi=p1.get("ofi"), funding=p1.get("funding"),
                   meta={"source": "file", "symbol_1": cfg.symbol_1, "symbol_2": cfg.symbol_2})
    return pd1


def _grid(t0, t1, step):
    return np.arange(int(t0) // step * step, int(t1) + step, step, dtype="int64")


def _on_grid(df: pd.DataFrame, grid: np.ndarray, cfg: DataConfig) -> Dict[str, np.ndarray]:
    s = pd.Series(df["close"].to_numpy(), index=df["ts"].to_numpy())
    s = s[~s.index.duplicated()]
    limit = None if cfg.gap_fill != "ffill" else max(1, cfg.max_gap_seconds // freq_seconds(cfg.market_data_frequency))
    close = s.reindex(grid)
    if cfg.gap_fill == "ffill":
        close = close.ffill(limit=limit)
    out = {"close": close.to_numpy()}
    if "bid" in df and "ask" in df:
        bid = pd.Series(df["bid"].to_numpy(), index=df["ts"].to_numpy()).reindex(grid).ffill()
        ask = pd.Series(df["ask"].to_numpy(), index=df["ts"].to_numpy()).reindex(grid).ffill()
        mid = (bid + ask) / 2
        out["book_spread_bps"] = ((ask - bid) / mid * 1e4).to_numpy()
    for opt in ("ofi", "funding"):
        if opt in df:
            out[opt] = pd.Series(df[opt].to_numpy(), index=df["ts"].to_numpy()).reindex(grid).ffill().to_numpy()
    return out


# --------------------------------------------------------------------------- #
#  Binance loader (real public data)
# --------------------------------------------------------------------------- #
def _load_binance(cfg: DataConfig) -> PairData:
    step = freq_seconds(cfg.market_data_frequency)
    native = "1s" if step < 60 else "1m"
    s1 = _binance_klines(cfg.symbol_1 + cfg.quote, native, cfg.start, cfg.end)
    s2 = _binance_klines(cfg.symbol_2 + cfg.quote, native, cfg.start, cfg.end)
    btc = None
    if "btc_return" in getattr(cfg, "factor_symbols", []) or True:
        try:
            btc = _binance_klines("BTC" + cfg.quote, native, cfg.start, cfg.end)
        except Exception:
            btc = None
    grid = _grid(max(s1["ts"].iloc[0], s2["ts"].iloc[0]),
                 min(s1["ts"].iloc[-1], s2["ts"].iloc[-1]), step)
    c1 = pd.Series(s1["close"].to_numpy(), index=s1["ts"].to_numpy()).reindex(grid).ffill(limit=cfg.max_gap_seconds // step).to_numpy()
    c2 = pd.Series(s2["close"].to_numpy(), index=s2["ts"].to_numpy()).reindex(grid).ffill(limit=cfg.max_gap_seconds // step).to_numpy()
    m = np.isfinite(c1) & np.isfinite(c2)
    ts = grid[m]; c1 = c1[m]; c2 = c2[m]
    btc_log = None
    if btc is not None:
        cb = pd.Series(btc["close"].to_numpy(), index=btc["ts"].to_numpy()).reindex(ts).ffill().to_numpy()
        btc_log = np.log(cb) if np.isfinite(cb).all() else None
    return PairData(ts=ts, p1=c1, p2=c2, log_p1=np.log(c1), log_p2=np.log(c2), btc_log=btc_log,
                    meta={"source": "binance", "native_interval": native,
                          "symbol_1": cfg.symbol_1, "symbol_2": cfg.symbol_2})


def _binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    import urllib.request, json, time as _t
    base = "https://api.binance.com/api/v3/klines"
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    t1 = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows = []
    cur = t0
    while cur < t1:
        url = f"{base}?symbol={symbol}&interval={interval}&startTime={cur}&endTime={t1}&limit=1000"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        if not data:
            break
        rows.extend(data)
        cur = data[-1][0] + 1
        if len(data) < 1000:
            break
        _t.sleep(0.05)
    if not rows:
        raise RuntimeError(f"no klines for {symbol}")
    df = pd.DataFrame(rows)
    return pd.DataFrame({"ts": (df[0].astype("int64") // 1000), "close": df[4].astype(float)}).drop_duplicates("ts")


# --------------------------------------------------------------------------- #
#  Demo generator (SYNTHETIC — clearly labelled)
# --------------------------------------------------------------------------- #
def _demo_pair(cfg: DataConfig) -> PairData:
    """Cointegrated synthetic pair for smoke-tests and GUI-without-data.

    P1 and P2 share a random-walk trend; their log-spread is a mean-reverting OU
    process, so the strategy has something real to find. is_demo=True. NEVER treat
    a demo run as a market result.
    """
    step = freq_seconds(cfg.market_data_frequency)
    try:
        t0 = int(pd.Timestamp(cfg.start, tz="UTC").timestamp())
        t1 = int(pd.Timestamp(cfg.end, tz="UTC").timestamp())
        n = max(2000, min(200000, (t1 - t0) // step))
    except Exception:
        n = 20000
    rng = np.random.default_rng(12345)
    trend = np.cumsum(rng.standard_normal(n) * 0.0008)          # shared market trend
    # OU spread
    kappa, sig = 0.02, 0.01
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = s[i - 1] - kappa * s[i - 1] + sig * rng.standard_normal()
    log_p1 = 10.5 + trend + 0.5 * s + rng.standard_normal(n) * 0.0003
    log_p2 = 8.0 + trend - 0.5 * s + rng.standard_normal(n) * 0.0003
    ts = np.arange(n, dtype="int64") * step + (t0 if 't0' in dir() else 0)
    ofi = np.tanh(rng.standard_normal(n) * 0.5)
    return PairData(ts=ts, p1=np.exp(log_p1), p2=np.exp(log_p2), log_p1=log_p1, log_p2=log_p2,
                    btc_log=10.5 + trend, ofi=ofi, is_demo=True,
                    book_spread_bps=np.full(n, 1.0),
                    meta={"source": "demo", "warning": "SYNTHETIC DEMO DATA - not a market result",
                          "symbol_1": cfg.symbol_1, "symbol_2": cfg.symbol_2})
