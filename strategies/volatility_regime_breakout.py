"""
volatility_regime_breakout.py — Breakout strategy gated by volatility regime.

Regime detection: ATR(14) / mid × 10000 → bps-denominated relative volatility.
  - HIGH regime  (atr_bps > high_vol_threshold_bps): trend-following breakout
  - LOW  regime  (atr_bps < low_vol_threshold_bps):  mean-reversion mode (SKIP)
  - MID  regime:  disabled by default

Breakout signal: close breaks out of the rolling N-bar Donchian channel
  - Close > channel_high → PLACE_BUY  (bullish breakout)
  - Close < channel_low  → PLACE_SELL (bearish breakout)
"""
import logging
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class VolatilityRegimeBreakoutStrategy(BaseStrategy):
    """
    Trend-following breakout strategy that only fires during high-volatility regimes.
    Uses ATR(14) as volatility proxy and Donchian channel for breakout detection.
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        p   = config.params
        don = int(p.get("donchian_period", 20))
        atr = int(p.get("atr_period", 14))
        buf = max(don, atr) + 5

        self._closes:    dict[str, deque] = {c: deque(maxlen=buf) for c in config.coins}
        self._highs:     dict[str, deque] = {c: deque(maxlen=buf) for c in config.coins}
        self._lows:      dict[str, deque] = {c: deque(maxlen=buf) for c in config.coins}
        self._positions: dict[str, dict]  = {}

    # ── BaseStrategy interface ───────────────────────────────────────────────

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        self._closes[symbol].append(bar.close)
        self._highs[symbol].append(bar.high)
        self._lows[symbol].append(bar.low)
        return self._check_entry(symbol, bar, ts)

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p          = self.config.params
        sl_pct     = p.get("stop_loss_pct", 0.015)
        tp_pct     = p.get("take_profit_pct", 0.025)
        max_hold_s = int(p.get("max_hold_hours", 4) * 3600)

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "pos_id":      pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold_s}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        p   = self.config.params
        don = int(p.get("donchian_period", 20))
        atr = int(p.get("atr_period", 14))
        closes = list(self._closes[symbol])
        highs  = list(self._highs[symbol])
        lows   = list(self._lows[symbol])

        atr_val  = self._atr(highs, lows, closes, atr)
        mid      = closes[-1] if closes else None
        atr_bps  = (atr_val / mid * 10_000) if (atr_val and mid) else None
        ch, cl   = self._donchian(highs, lows, don)

        return {
            "atr_bps":              round(atr_bps, 2) if atr_bps is not None else None,
            "regime":               self._regime(atr_bps, p),
            "donchian_high":        round(ch, 6) if ch else None,
            "donchian_low":         round(cl, 6) if cl else None,
            "last_close":           round(mid, 6) if mid else None,
            "high_vol_threshold":   p.get("high_vol_threshold_bps", 30.0),
            "low_vol_threshold":    p.get("low_vol_threshold_bps", 8.0),
            "bars_collected":       len(closes),
            "in_position":          symbol in self._positions,
        }

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = len(self._positions)
        return d

    # ── Internal ─────────────────────────────────────────────────────────────

    def _check_entry(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None

        p   = self.config.params
        don = int(p.get("donchian_period", 20))
        atr = int(p.get("atr_period", 14))

        closes = list(self._closes[symbol])
        highs  = list(self._highs[symbol])
        lows   = list(self._lows[symbol])

        if len(closes) < max(don, atr) + 1:
            return None

        mid      = closes[-1]
        atr_val  = self._atr(highs, lows, closes, atr)
        atr_bps  = (atr_val / mid * 10_000) if (atr_val and mid > 0) else 0.0

        if self._regime(atr_bps, p) != "high":
            return None

        ch, cl = self._donchian(highs[:-1], lows[:-1], don)  # exclude current bar
        if ch is None or cl is None:
            return None

        notional   = self.compute_order_notional()
        max_hold_s = int(p.get("max_hold_hours", 4) * 3600)

        if bar.close > ch:
            reason = f"vol_breakout_long close={bar.close:.4f} > don_high={ch:.4f} atr_bps={atr_bps:.1f}"
            return StrategyDecision(
                action="PLACE_BUY", symbol=symbol, reason=reason,
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"atr_bps": atr_bps, "donchian_high": ch, "donchian_low": cl},
            )

        if bar.close < cl:
            reason = f"vol_breakout_short close={bar.close:.4f} < don_low={cl:.4f} atr_bps={atr_bps:.1f}"
            return StrategyDecision(
                action="PLACE_SELL", symbol=symbol, reason=reason,
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"atr_bps": atr_bps, "donchian_high": ch, "donchian_low": cl},
            )

        return None

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        side   = pos["side"]
        stop_h = (side == "SELL" and mid >= pos["stop"]) or (side == "BUY" and mid <= pos["stop"])
        tp_h   = (side == "SELL" and mid <= pos["tp"])   or (side == "BUY" and mid >= pos["tp"])
        max_h  = ts >= pos["max_hold_ts"]

        if not (stop_h or tp_h or max_h):
            return None

        reason = "stop_loss" if stop_h else ("take_profit" if tp_h else "max_hold")
        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )

    # ── Indicators ───────────────────────────────────────────────────────────

    @staticmethod
    def _atr(highs: list, lows: list, closes: list, period: int) -> Optional[float]:
        if len(highs) < period + 1:
            return None
        trs = []
        for i in range(1, len(highs)):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    @staticmethod
    def _donchian(highs: list, lows: list, period: int):
        if len(highs) < period or len(lows) < period:
            return None, None
        return max(highs[-period:]), min(lows[-period:])

    def _regime(self, atr_bps: Optional[float], p: dict) -> str:
        if atr_bps is None:
            return "unknown"
        high_thr = p.get("high_vol_threshold_bps", 30.0)
        low_thr  = p.get("low_vol_threshold_bps", 8.0)
        if atr_bps > high_thr:
            return "high"
        if atr_bps < low_thr:
            return "low"
        return "mid"
