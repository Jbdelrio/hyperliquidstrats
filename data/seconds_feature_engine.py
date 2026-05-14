"""
seconds_feature_engine.py — Sub-second / per-second microstructure feature engine.

Maintains rolling buffers PER SYMBOL fed by:
  • `update_from_book(symbol, book, ts)` — called for every l2Book update
  • `update_from_trade(symbol, trade, ts)` — called for every taker trade

Exposes a snapshot dict per symbol via `get_features(symbol)` that includes
order-book features, trade-flow features, VWAPs, returns, realized vol,
data-quality flags, and a small set of "alpha raw" derived signals.

Design notes
------------
- WebSocket-only : NEVER calls REST. Pulls everything from in-memory
  buffers fed by the existing `OrderbookManager`.
- Buffers are time-bounded (`max_history_seconds`, default 300 s) AND
  size-bounded (defensive caps) — pruning happens on read so the engine
  thread never blocks.
- Robust to NaN / missing data : every getter returns NaN-safe floats or
  the sentinel `nan`. Strategies must check `enough_data` and
  `book_stale` before acting on a signal.
- No global state. One `SecondsFeatureEngine` per `EngineV9` instance.

The formulas are documented in `docs/ALPHA_MODELS_THEORY.md`.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


_NaN = float("nan")


def _safe_div(a: float, b: float, default: float = _NaN) -> float:
    if b is None or not math.isfinite(b) or b == 0.0:
        return default
    if a is None or not math.isfinite(a):
        return default
    return a / b


def _sign(x: float) -> float:
    if x is None or not math.isfinite(x):
        return 0.0
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


def _tanh(x: float) -> float:
    if x is None or not math.isfinite(x):
        return 0.0
    return math.tanh(x)


# ----------------------------------------------------------------------
# Per-symbol state
# ----------------------------------------------------------------------

@dataclass
class _BookTick:
    ts: float
    mid: float
    best_bid: float
    best_ask: float
    spread_bps: float
    bid_depth: tuple  # (d1, d3, d5, d10)
    ask_depth: tuple
    obi: tuple        # (obi_1, obi_3, obi_5, obi_10)
    microprice: float


@dataclass
class _TradeTick:
    ts: float
    price: float
    size: float
    volume_usd: float
    side: str  # "B" / "A"


@dataclass
class _SymbolState:
    book_history: deque = field(default_factory=lambda: deque(maxlen=20_000))
    trades: deque = field(default_factory=lambda: deque(maxlen=50_000))
    mid_at_ts: deque = field(default_factory=lambda: deque(maxlen=20_000))
    last_book_ts: float = 0.0
    # For LV z-score : rolling spread/rv samples (per second, light)
    z_spread_samples: deque = field(default_factory=lambda: deque(maxlen=600))
    z_rv_samples: deque = field(default_factory=lambda: deque(maxlen=600))
    last_z_sample_ts: float = 0.0


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------

class SecondsFeatureEngine:
    """Rolling per-second microstructure feature engine."""

    DEFAULT_DEPTH_LEVELS = (1, 3, 5, 10)
    DEFAULT_TI_WINDOWS = (5.0, 10.0, 30.0)
    DEFAULT_VWAP_WINDOWS = (5.0, 15.0, 30.0)
    DEFAULT_RET_WINDOWS = (5.0, 15.0, 30.0, 60.0)
    DEFAULT_RV_WINDOWS = (30.0, 60.0)

    def __init__(self, symbols: list[str], config: Optional[dict] = None):
        config = config or {}
        self.symbols = [s.upper() for s in symbols]
        self.max_history_seconds = float(config.get("max_history_seconds", 300.0))
        self.stale_book_s = float(config.get("stale_book_s", 5.0))
        self.min_data_seconds = float(config.get("min_data_seconds", 30.0))
        # Tanh saturation lambdas (cf. docs/ALPHA_MODELS_THEORY.md)
        self.lambda_vwap_slope = float(config.get("lambda_vwap_slope", 1000.0))
        self.lambda_micropressure = float(config.get("lambda_micropressure", 1000.0))
        self.lambda_momentum = float(config.get("lambda_momentum", 1000.0))
        self._states: dict[str, _SymbolState] = {s: _SymbolState() for s in self.symbols}

    # ------------------------------------------------------------------
    # Updaters (called from engine loops)
    # ------------------------------------------------------------------

    def update_from_book(self, symbol: str, book: Any, ts: float) -> None:
        """Ingest an L2 book snapshot.

        `book` is expected to have attributes:
          - bids: list[(price, size)]
          - asks: list[(price, size)]
          - best_bid, best_ask, mid, spread_bps  (Optional[float])
        """
        sym = symbol.upper()
        if sym not in self._states:
            return
        st = self._states[sym]

        try:
            bid = book.best_bid
            ask = book.best_ask
        except Exception:
            return
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return

        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10_000.0 if mid > 0 else _NaN

        bid_depths = self._depths(getattr(book, "bids", []), self.DEFAULT_DEPTH_LEVELS)
        ask_depths = self._depths(getattr(book, "asks", []), self.DEFAULT_DEPTH_LEVELS)
        obis = tuple(
            _safe_div(b - a, b + a, 0.0)
            for b, a in zip(bid_depths, ask_depths)
        )

        # Microprice : q_bid / q_ask are the top-of-book sizes.
        try:
            q_bid = float(book.bids[0][1]) if book.bids else 0.0
            q_ask = float(book.asks[0][1]) if book.asks else 0.0
        except Exception:
            q_bid = q_ask = 0.0
        denom = q_bid + q_ask
        if denom > 0:
            microprice = (ask * q_bid + bid * q_ask) / denom
        else:
            microprice = mid

        tick = _BookTick(
            ts=ts, mid=mid, best_bid=bid, best_ask=ask,
            spread_bps=spread_bps,
            bid_depth=bid_depths, ask_depth=ask_depths,
            obi=obis, microprice=microprice,
        )
        st.book_history.append(tick)
        st.mid_at_ts.append((ts, mid))
        st.last_book_ts = ts

        self._prune(st, ts)

    def update_from_trade(self, symbol: str, trade: Any, ts: float) -> None:
        sym = symbol.upper()
        if sym not in self._states:
            return
        st = self._states[sym]
        try:
            price = float(trade.price)
            size = float(trade.size)
            vol_usd = float(getattr(trade, "volume_usd", price * size))
            side = getattr(trade, "side", "B")
        except Exception:
            return
        if price <= 0 or size <= 0:
            return
        st.trades.append(_TradeTick(ts=ts, price=price, size=size,
                                    volume_usd=vol_usd, side=side))
        self._prune(st, ts)

    # ------------------------------------------------------------------
    # Read-out
    # ------------------------------------------------------------------

    def get_features(self, symbol: str) -> dict:
        sym = symbol.upper()
        if sym not in self._states:
            return {"symbol": sym, "enough_data": False, "book_stale": True}
        st = self._states[sym]
        wall = time.time()
        # Data clock : use the latest book ts as the windowing reference so
        # the engine works on replayed / synthetic data too. Wall-clock is
        # only used to detect a stale book.
        data_now = st.last_book_ts if st.last_book_ts > 0 else wall
        if st.last_book_ts > 0:
            last_update_age_s = max(0.0, wall - st.last_book_ts)
        else:
            last_update_age_s = float("inf")
        book_stale = last_update_age_s > self.stale_book_s
        oldest_book_ts = st.book_history[0].ts if st.book_history else data_now
        history_span_s = data_now - oldest_book_ts if st.book_history else 0.0
        enough_data = (
            len(st.book_history) >= 10
            and history_span_s >= self.min_data_seconds
            and not book_stale
        )

        if not st.book_history:
            feats: dict[str, Any] = {
                "symbol": sym,
                "ts": data_now,
                "book_stale": book_stale,
                "enough_data": False,
                "last_update_age_s": last_update_age_s,
            }
            return feats

        last = st.book_history[-1]

        # Book features
        feats = {
            "symbol": sym,
            "ts": last.ts,
            "best_bid": last.best_bid,
            "best_ask": last.best_ask,
            "mid": last.mid,
            "spread_bps": last.spread_bps,
            "bid_depth_1": last.bid_depth[0],
            "ask_depth_1": last.ask_depth[0],
            "bid_depth_3": last.bid_depth[1],
            "ask_depth_3": last.ask_depth[1],
            "bid_depth_5": last.bid_depth[2],
            "ask_depth_5": last.ask_depth[2],
            "bid_depth_10": last.bid_depth[3],
            "ask_depth_10": last.ask_depth[3],
            "obi_1": last.obi[0],
            "obi_3": last.obi[1],
            "obi_5": last.obi[2],
            "obi_10": last.obi[3],
            "microprice": last.microprice,
            "microprice_pressure": _safe_div(last.microprice - last.mid, last.mid, 0.0),
            "book_stale": book_stale,
            "enough_data": enough_data,
            "last_update_age_s": last_update_age_s,
        }

        # Trade-flow features
        for w in self.DEFAULT_TI_WINDOWS:
            buy_vol, sell_vol, n_tr = self._trade_window_stats(st, data_now, w)
            tag = self._fmt_window(w)
            feats[f"buy_volume_usd_{tag}"] = buy_vol
            feats[f"sell_volume_usd_{tag}"] = sell_vol
            denom = buy_vol + sell_vol
            feats[f"trade_imbalance_{tag}"] = (
                (buy_vol - sell_vol) / denom if denom > 0 else _NaN
            )
            feats[f"trade_count_{tag}"] = n_tr

        # VWAPs
        for w in self.DEFAULT_VWAP_WINDOWS:
            feats[f"vwap_{self._fmt_window(w)}"] = self._vwap(st, data_now, w)

        v5 = feats.get("vwap_5s")
        v30 = feats.get("vwap_30s")
        feats["vwap_slope_5_30"] = (
            (v5 / v30 - 1.0)
            if (v5 is not None and v30 is not None
                and v30 > 0 and math.isfinite(v5) and math.isfinite(v30))
            else _NaN
        )

        # Returns at multiple horizons
        for w in self.DEFAULT_RET_WINDOWS:
            feats[f"r_{self._fmt_window(w)}"] = self._return_back(st, data_now, w)

        # Realized vol
        for w in self.DEFAULT_RV_WINDOWS:
            feats[f"rv_{self._fmt_window(w)}"] = self._realized_vol(st, data_now, w)

        # Sample z-stats once per second
        if data_now - st.last_z_sample_ts >= 1.0:
            sb = feats.get("spread_bps")
            rv30 = feats.get("rv_30s")
            if sb is not None and math.isfinite(sb):
                st.z_spread_samples.append(sb)
            if rv30 is not None and math.isfinite(rv30):
                st.z_rv_samples.append(rv30)
            st.last_z_sample_ts = data_now

        z_sp = self._zscore(feats.get("spread_bps"), st.z_spread_samples)
        z_rv = self._zscore(feats.get("rv_30s"), st.z_rv_samples)
        feats["liquidity_vacuum"] = (
            (z_sp + z_rv) if (math.isfinite(z_sp) and math.isfinite(z_rv)) else _NaN
        )

        # Alpha raw signals (cf. ALPHA_MODELS_THEORY.md)
        obi5 = feats["obi_5"]
        ti10 = feats.get("trade_imbalance_10s")
        r5 = feats.get("r_5s")
        feats["book_flow_alignment"] = (
            _sign(obi5) * _sign(ti10) if ti10 is not None else 0.0
        )
        feats["book_flow_divergence"] = (
            (ti10 - obi5)
            if (ti10 is not None and math.isfinite(ti10)
                and obi5 is not None and math.isfinite(obi5))
            else _NaN
        )
        # Absorption proxies — robustly coerce NaN to 0 (NaN propagates through max).
        ti10_safe = ti10 if (ti10 is not None and math.isfinite(ti10)) else 0.0
        r5_safe = r5 if (r5 is not None and math.isfinite(r5)) else 0.0
        feats["absorption_sell_proxy"] = max(ti10_safe, 0.0) * max(-r5_safe, 0.0)
        feats["absorption_buy_proxy"] = max(-ti10_safe, 0.0) * max(r5_safe, 0.0)

        # Pressure score (raw)
        mpp = feats["microprice_pressure"]
        vs = feats["vwap_slope_5_30"]
        pressure = 0.0
        pressure += 0.25 * (obi5 if math.isfinite(obi5) else 0.0)
        pressure += 0.25 * (ti10 if (ti10 is not None and math.isfinite(ti10)) else 0.0)
        pressure += 0.20 * _tanh(self.lambda_vwap_slope * (vs if math.isfinite(vs) else 0.0))
        pressure += 0.15 * _tanh(self.lambda_micropressure * (mpp if math.isfinite(mpp) else 0.0))
        pressure += 0.15 * _tanh(self.lambda_momentum * (r5 if (r5 is not None and math.isfinite(r5)) else 0.0))
        feats["pressure_score_raw"] = pressure

        return feats

    def get_all_features(self) -> list[dict]:
        return [self.get_features(s) for s in self.symbols]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _depths(levels: list, ns: tuple) -> tuple:
        cum = 0.0
        out = []
        idx = 0
        target = ns[idx]
        for i, lvl in enumerate(levels, start=1):
            try:
                _, sz = lvl
                cum += float(sz)
            except Exception:
                continue
            while idx < len(ns) and i >= target:
                out.append(cum)
                idx += 1
                if idx < len(ns):
                    target = ns[idx]
        # If book shallower than max level, pad with last cumulative.
        while len(out) < len(ns):
            out.append(cum)
        return tuple(out)

    @staticmethod
    def _fmt_window(w: float) -> str:
        if w >= 1.0 and float(w).is_integer():
            return f"{int(w)}s"
        return f"{w}s".replace(".", "_")

    def _prune(self, st: _SymbolState, now: float) -> None:
        cutoff = now - self.max_history_seconds
        bh = st.book_history
        while bh and bh[0].ts < cutoff:
            bh.popleft()
        mh = st.mid_at_ts
        while mh and mh[0][0] < cutoff:
            mh.popleft()
        tb = st.trades
        while tb and tb[0].ts < cutoff:
            tb.popleft()

    @staticmethod
    def _trade_window_stats(st: _SymbolState, now: float, w: float) -> tuple[float, float, int]:
        cutoff = now - w
        buy = 0.0
        sell = 0.0
        n = 0
        # Trades are appended in time order ⇒ iterate from right.
        for tr in reversed(st.trades):
            if tr.ts < cutoff:
                break
            n += 1
            if tr.side == "B":
                buy += tr.volume_usd
            elif tr.side == "A":
                sell += tr.volume_usd
        return buy, sell, n

    @staticmethod
    def _vwap(st: _SymbolState, now: float, w: float) -> float:
        cutoff = now - w
        num = 0.0
        denom = 0.0
        for tr in reversed(st.trades):
            if tr.ts < cutoff:
                break
            num += tr.price * tr.volume_usd
            denom += tr.volume_usd
        return num / denom if denom > 0 else _NaN

    @staticmethod
    def _return_back(st: _SymbolState, now: float, w: float) -> float:
        bh = st.book_history
        if not bh:
            return _NaN
        last = bh[-1]
        target_ts = last.ts - w
        if target_ts < bh[0].ts:
            return _NaN
        # Linear backward scan — buffers are bounded so O(n) is OK.
        ref = None
        for tick in reversed(bh):
            if tick.ts <= target_ts:
                ref = tick
                break
        if ref is None or ref.mid <= 0:
            return _NaN
        try:
            return float(np.log(last.mid / ref.mid))
        except Exception:
            return _NaN

    @staticmethod
    def _realized_vol(st: _SymbolState, now: float, w: float) -> float:
        cutoff = now - w
        rets = []
        prev_mid = None
        for tick in st.book_history:
            if tick.ts < cutoff:
                continue
            if prev_mid is not None and prev_mid > 0 and tick.mid > 0:
                try:
                    rets.append(math.log(tick.mid / prev_mid))
                except Exception:
                    pass
            prev_mid = tick.mid
        if len(rets) < 5:
            return _NaN
        arr = np.asarray(rets, dtype=float)
        return float(np.sqrt(np.sum(arr * arr)))

    @staticmethod
    def _zscore(x: Optional[float], samples: deque) -> float:
        if x is None or not math.isfinite(x):
            return _NaN
        if len(samples) < 30:
            return _NaN
        arr = np.fromiter(samples, dtype=float, count=len(samples))
        mu = float(np.nanmean(arr))
        sd = float(np.nanstd(arr))
        if sd <= 0 or not math.isfinite(sd):
            return _NaN
        return (x - mu) / sd
