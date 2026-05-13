"""
backtesting/backtest_engine.py — Minimal bar-replay backtester.

This is a *skeleton* — no WebSocket, no order book, no tick-level fills.
Bars are replayed once per minute (or whatever interval the user provides)
through `strategy.on_bar_minute(symbol, bar, ts)`. Fills are simulated at
the bar close with a simple fee/slippage model.

Use this for sanity-checking a strategy's signal logic against historical
bars before live-paper deployment. For high-frequency strategies that
depend on order-book microstructure (S8EMS, OBImbalanceScalper), this
engine will under-represent the realistic environment.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .metrics import compute_metrics

log = logging.getLogger(__name__)


# Default cost model — matches paper executor (taker round-trip 6 bps + slip).
_DEFAULT_FEE_BPS      = 3.0
_DEFAULT_SLIPPAGE_BPS = 4.0


@dataclass
class _SimPosition:
    pos_id:       str
    strategy:     str
    symbol:       str
    side:         str           # "BUY" or "SELL"
    notional:     float
    entry:        float
    entry_ts:     float
    tp:           float = 0.0
    sl:           float = 0.0
    max_hold_ts:  float = 0.0


class BacktestEngine:
    """
    Bar-replay backtester for a single strategy class.

    Usage:
        engine = BacktestEngine(MomentumLongShort, strategy_cfg, bars)
        trades = engine.run()
        metrics = compute_metrics(trades)

    bars: a list of BarData (or compatible) objects sorted by ts and grouped
    by symbol — the engine iterates symbol-by-symbol per timestamp.
    """

    def __init__(self, strategy_cls, config, bars: list,
                 fee_bps: float = _DEFAULT_FEE_BPS,
                 slippage_bps: float = _DEFAULT_SLIPPAGE_BPS):
        from strategies.base_strategy import StrategyConfig  # local import

        if not isinstance(config, StrategyConfig):
            raise TypeError("config must be a StrategyConfig instance")

        self.strategy = strategy_cls(config)
        self.bars = sorted(bars, key=lambda b: getattr(b, "ts", 0))
        self.fee_bps = float(fee_bps)
        self.slippage_bps = float(slippage_bps)

        self._positions: dict[str, _SimPosition] = {}
        self._closed_trades: list[dict] = []

    # ------------------------------------------------------------------

    def run(self) -> list[dict]:
        """Iterate bars, dispatch to strategy, simulate fills, return trades."""
        for bar in self.bars:
            sym = getattr(bar, "symbol", "")
            ts  = float(getattr(bar, "ts", 0))
            close = float(getattr(bar, "close", 0))
            if not sym or close <= 0:
                continue

            # 1. Stop / TP / max_hold on any open position for this symbol
            self._process_exits(sym, close, ts)

            # 2. Feed bar to strategy → maybe a decision
            try:
                decision = self.strategy.on_bar_minute(sym, bar, ts)
            except Exception as exc:
                log.debug("Strategy raised on bar %s @%s: %s", sym, ts, exc)
                decision = None

            if decision is None:
                continue

            action = getattr(decision, "action", None)
            if action == "PLACE_BUY":
                self._open(strat_name=self.strategy.config.name,
                           symbol=sym, side="BUY",
                           bar_close=close, ts=ts, decision=decision)
            elif action == "PLACE_SELL":
                self._open(strat_name=self.strategy.config.name,
                           symbol=sym, side="SELL",
                           bar_close=close, ts=ts, decision=decision)
            elif action == "CLOSE":
                # Forced close on signal
                self._close_for(sym, close, ts, reason="signal_close")

        # Flush any remaining open positions at the last bar's close
        if self.bars:
            last = self.bars[-1]
            self._close_for(getattr(last, "symbol", ""),
                            float(getattr(last, "close", 0) or 0),
                            float(getattr(last, "ts", 0) or 0),
                            reason="eob_flush")
            # Also close any other open positions at their symbol's last close
            for pid in list(self._positions.keys()):
                pos = self._positions[pid]
                self._close_trade(pos, pos.entry, pos.entry_ts, "eob_flush")
        return self._closed_trades

    # ------------------------------------------------------------------

    def _open(self, strat_name: str, symbol: str, side: str,
              bar_close: float, ts: float, decision) -> None:
        notional = float(getattr(decision, "notional_usd", 0) or 0)
        if notional <= 0:
            notional = float(getattr(self.strategy.config,
                                     "max_position_size_usd", 0) or 0)
        if notional <= 0:
            return
        # One position per symbol per strategy (simple model)
        existing = [p for p in self._positions.values()
                    if p.symbol == symbol and p.strategy == strat_name]
        if existing:
            return

        # Apply entry slippage one-way
        slip = self.slippage_bps / 10_000.0
        entry = bar_close * (1.0 + slip) if side == "BUY" else bar_close * (1.0 - slip)
        tp = float(getattr(decision, "take_profit", 0) or 0)
        sl = float(getattr(decision, "stop_loss", 0) or 0)
        max_hold_s = float(getattr(decision, "max_hold_seconds", 0) or 0)

        pos = _SimPosition(
            pos_id=str(uuid.uuid4())[:8],
            strategy=strat_name, symbol=symbol, side=side,
            notional=notional, entry=entry, entry_ts=ts,
            tp=tp, sl=sl,
            max_hold_ts=(ts + max_hold_s) if max_hold_s > 0 else 0.0,
        )
        self._positions[pos.pos_id] = pos

    def _process_exits(self, symbol: str, close: float, ts: float) -> None:
        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            if pos.symbol != symbol:
                continue
            reason = None
            if pos.max_hold_ts and ts >= pos.max_hold_ts:
                reason = "max_hold"
            elif pos.side == "BUY":
                if pos.sl and close <= pos.sl:
                    reason = "stop_loss"
                elif pos.tp and close >= pos.tp:
                    reason = "take_profit"
            else:
                if pos.sl and close >= pos.sl:
                    reason = "stop_loss"
                elif pos.tp and close <= pos.tp:
                    reason = "take_profit"
            if reason:
                self._close_trade(pos, close, ts, reason)

    def _close_for(self, symbol: str, close: float, ts: float, reason: str) -> None:
        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            if pos.symbol != symbol:
                continue
            self._close_trade(pos, close, ts, reason)

    def _close_trade(self, pos: _SimPosition, exit_price: float,
                     ts: float, reason: str) -> None:
        # Exit slippage (one-way)
        slip = self.slippage_bps / 10_000.0
        exit_eff = (exit_price * (1.0 - slip)
                    if pos.side == "BUY"
                    else exit_price * (1.0 + slip))
        if pos.side == "BUY":
            gross = (exit_eff - pos.entry) / pos.entry * pos.notional
        else:
            gross = (pos.entry - exit_eff) / pos.entry * pos.notional
        round_trip_fee = 2.0 * (self.fee_bps / 10_000.0) * pos.notional
        net = gross - round_trip_fee

        self._closed_trades.append({
            "ts":       ts,
            "symbol":   pos.symbol,
            "strategy": pos.strategy,
            "side":     pos.side,
            "notional": round(pos.notional, 2),
            "entry":    pos.entry,
            "exit":     exit_eff,
            "gross":    round(gross, 6),
            "fee":      round(round_trip_fee, 6),
            "net":      round(net, 6),
            "hold_s":   round(ts - pos.entry_ts, 1),
            "reason":   reason,
        })
        self._positions.pop(pos.pos_id, None)

    # ------------------------------------------------------------------

    def metrics(self) -> dict:
        """Helper: compute_metrics on this engine's closed trades."""
        return compute_metrics(self._closed_trades)
