"""
funding_carry_hedged.py — Funding carry net of costs.

Improved version of FundingArbitrage: computes expected edge
after fees, slippage and a safety buffer. By default runs as a
scanner (allow_unhedged_perp=false). Set allow_unhedged_perp=true
to actually trade on a directional perp-only basis.
"""
import logging
import time
from collections import deque
from typing import Optional

from data.hyperliquid_funding import fetch_hyperliquid_funding_rates
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class FundingCarryHedgedStrategy(BaseStrategy):
    """
    Net expected edge:
      E = funding_bps_per_hour × hours_held
          − taker_fee_bps − slippage_bps − safety_buffer_bps

    If allow_unhedged_perp=false (default): scanner only (SKIP + calibration).
    If allow_unhedged_perp=true: directional perp trade.

    Positive funding → short perp (collect)
    Negative funding → long perp (collect)
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        self._funding_raw:  dict[str, Optional[float]] = {c: None for c in config.coins}
        self._funding_hist: dict[str, deque]            = {c: deque(maxlen=24) for c in config.coins}
        self._bar_closes:   dict[str, deque]            = {c: deque(maxlen=960) for c in config.coins}
        self._positions:    dict[str, dict]             = {}
        self._last_fetch:   float = 0.0

    # ── BaseStrategy ─────────────────────────────────────────────────────────

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
        self._bar_closes[symbol].append(bar.close)

        if ts - self._last_fetch >= 60:
            raw = fetch_hyperliquid_funding_rates(list(self._funding_raw.keys()))
            if raw:
                for coin, info in raw.items():
                    if coin in self._funding_raw:
                        hourly = info["hourly_rate"]
                        self._funding_raw[coin]  = hourly
                        self._funding_hist[coin].append(hourly)
            self._last_fetch = ts

        return self._check_entry(symbol, bar, ts)

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        sl_pct      = p.get("stop_loss_pct", 0.02)
        tp_pct      = p.get("take_profit_pct", 0.012)
        max_hold_s  = int(p.get("max_hold_hours", 8) * 3600)

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "entry_funding": self._funding_raw.get(symbol),
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
        p            = self.config.params
        raw          = self._funding_raw.get(symbol)
        hist         = list(self._funding_hist.get(symbol, []))
        funding_bps  = raw * 10_000 if raw is not None else None
        taker_bps    = p.get("taker_fee_bps", 3.5)
        slip_bps     = p.get("slippage_bps", 2.0)
        buf_bps      = p.get("safety_buffer_bps", 2.0)
        entry_h      = p.get("funding_entry_bps_per_hour", 0.5)

        expected_hold_h = float(p.get("expected_hold_hours", 4))
        expected_edge = None
        if funding_bps is not None:
            expected_edge = abs(funding_bps) * expected_hold_h - taker_bps - slip_bps - buf_bps

        return {
            "funding_bps_per_hour":  round(funding_bps, 4) if funding_bps else None,
            "funding_smoothed_bps":  round(sum(h * 10_000 for h in hist) / len(hist), 4) if hist else None,
            "expected_edge_bps":     round(expected_edge, 3) if expected_edge is not None else None,
            "entry_threshold_bps":   entry_h,
            "expected_hold_hours":   expected_hold_h,
            "action_bias":           self._action_bias(funding_bps, entry_h),
            "allow_unhedged_perp":   p.get("allow_unhedged_perp", False),
            "hedge_mode_available":  False,
            "in_position":           symbol in self._positions,
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

        p = self.config.params
        raw = self._funding_raw.get(symbol)
        if raw is None:
            return None

        hist = list(self._funding_hist.get(symbol, []))
        if len(hist) < 3:
            return None

        funding_bps_per_h = raw * 10_000
        entry_thr         = p.get("funding_entry_bps_per_hour", 0.5)  # bps/h
        exit_thr          = p.get("funding_exit_bps_per_hour",  0.1)
        taker_bps         = p.get("taker_fee_bps", 3.5)
        slip_bps          = p.get("slippage_bps",  2.0)
        buf_bps           = p.get("safety_buffer_bps", 2.0)
        min_edge          = p.get("min_expected_edge_bps", 3.0)
        allow_unhedged    = p.get("allow_unhedged_perp", False)

        # Edge = funding collected over expected hold - round-trip costs
        expected_hold_h = float(p.get("expected_hold_hours", 4))
        expected_edge = abs(funding_bps_per_h) * expected_hold_h - taker_bps - slip_bps - buf_bps

        if abs(funding_bps_per_h) < entry_thr:
            return None
        if expected_edge < min_edge:
            return None

        # Volatility filter (15m returns)
        closes = list(self._bar_closes.get(symbol, []))
        max_ret = p.get("max_abs_return_15m_pct", 2.5) / 100.0
        if len(closes) >= 15 and closes[-15] > 0:
            ret_15m = abs(closes[-1] / closes[-15] - 1)
            if ret_15m > max_ret:
                return None

        if not allow_unhedged:
            return None  # scanner mode

        notional  = self.compute_order_notional()
        max_hold_s = int(p.get("max_hold_hours", 8) * 3600)

        if funding_bps_per_h > 0:
            # Positive funding → short perp to collect
            action = "PLACE_SELL"
            reason = f"funding_carry_short funding={funding_bps_per_h:.2f}bps/h edge={expected_edge:.1f}bps"
        else:
            action = "PLACE_BUY"
            reason = f"funding_carry_long funding={funding_bps_per_h:.2f}bps/h edge={expected_edge:.1f}bps"

        return StrategyDecision(
            action=action, symbol=symbol, reason=reason,
            notional_usd=notional, max_hold_seconds=max_hold_s,
            metadata={"funding_bps": funding_bps_per_h, "expected_edge": expected_edge,
                      "allow_unhedged": True},
        )

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p       = self.config.params
        side    = pos["side"]
        stop_h  = (side == "SELL" and mid >= pos["stop"]) or (side == "BUY" and mid <= pos["stop"])
        tp_h    = (side == "SELL" and mid <= pos["tp"])   or (side == "BUY" and mid >= pos["tp"])
        max_h   = ts >= pos["max_hold_ts"]

        raw = self._funding_raw.get(symbol)
        fund_exit = False
        if raw is not None:
            fund_bps = raw * 10_000
            exit_thr = p.get("funding_exit_bps_per_hour", 0.1)
            fund_exit = abs(fund_bps) < exit_thr

        if not (stop_h or tp_h or max_h or fund_exit):
            return None

        reason = "stop_loss" if stop_h else ("take_profit" if tp_h else ("funding_normalized" if fund_exit else "max_hold"))
        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )

    def _action_bias(self, funding_bps, entry_thr) -> str:
        if funding_bps is None:
            return "no_data"
        if funding_bps > entry_thr:
            return "short_perp_collect"
        if funding_bps < -entry_thr:
            return "long_perp_collect"
        return "neutral"
