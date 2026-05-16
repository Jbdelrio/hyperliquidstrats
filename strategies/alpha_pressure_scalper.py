"""
alpha_pressure_scalper.py — Pressure-score scalper (DISABLED BY DEFAULT).

Uses the `pressure_score_raw` feature emitted by
`data/seconds_feature_engine.py`. Trades only if every gate passes :
  - enough_data and not book_stale,
  - spread_bps <= max_spread_bps,
  - rv_30s <= max_rv_30s,
  - |pressure| >= threshold,
  - expected_edge_bps > cost_bps + margin_bps.

This strategy is paper-only.  It is *not* validated by the alpha-research
notebook — calibration must come from the notebook output before any real
trading happens. Default: enabled=false.
"""
from __future__ import annotations

import csv
import logging
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from strategies.base_strategy import (
    BarData,
    BaseStrategy,
    StrategyConfig,
    StrategyDecision,
)

log = logging.getLogger(__name__)

_ALPHA_SIGNAL_LOG = "logs/alpha_signals.csv"
_ALPHA_SIGNAL_FIELDS = [
    "ts", "strategy", "symbol", "action", "reason",
    "pressure_score_raw", "book_flow_divergence",
    "absorption_buy_proxy", "absorption_sell_proxy",
    "obi_5", "trade_imbalance_10s",
    "spread_bps", "rv_30s",
    "expected_edge_bps", "cost_bps",
    "enough_data", "book_stale",
]
_signal_log_lock = threading.Lock()
_signal_log_initialized = False


def _log_alpha_signal(row: dict) -> None:
    """Append a row to logs/alpha_signals.csv. Thread-safe."""
    global _signal_log_initialized
    path = Path(_ALPHA_SIGNAL_LOG)
    with _signal_log_lock:
        if not _signal_log_initialized:
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            if new:
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    csv.DictWriter(fh, fieldnames=_ALPHA_SIGNAL_FIELDS).writeheader()
            _signal_log_initialized = True
        try:
            with open(path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=_ALPHA_SIGNAL_FIELDS,
                                   extrasaction="ignore")
                w.writerow(row)
        except Exception as e:
            log.debug("alpha signals log write failed: %s", e)


class _AlphaScalperBase(BaseStrategy):
    """Common gating logic for the 3 seconds-feature alpha strategies."""

    SIGNAL_KEY: str = ""  # set by subclass

    DEFAULT_PARAMS = dict(
        threshold=0.5,
        max_spread_bps=8.0,
        max_rv_30s=0.005,
        min_trade_volume_30s=500.0,
        cost_bps=10.0,
        margin_bps=3.0,
        stop_loss_bps=15.0,
        take_profit_bps=20.0,
        max_hold_seconds=120,
        cooldown_seconds=30,
        notional_usd=10.0,
        maker_only=True,
        max_positions=1,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        # ${symbol} → ts of last decision (for cooldown)
        self._cooldown_until: dict[str, float] = {}
        # ${symbol} → simple in-strategy position tracker (paper-only).
        # NOTE the engine tracks real positions via HighFreqExecutor; we
        # only use this to throttle re-entries.
        self._open_positions: dict[str, dict] = {}

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": True,
            "seconds_features": True,
            "bars": [], "funding": False, "external_spot": False,
            "warmup_bars": {}, "warmup_seconds": 90,
        }

    # --- Required BaseStrategy hooks (no-op in seconds path) ------------

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol, bar, ts):
        return None

    # --- Seconds hook ----------------------------------------------------

    def on_second_features(self, symbol: str, features: dict, ts: float
                           ) -> Optional[StrategyDecision]:
        if not self._enabled:
            return None
        p = self.config.params

        # Hard gates
        if not features.get("enough_data"):
            return self._skip(symbol, ts, "not_enough_data", features)
        if features.get("book_stale"):
            return self._skip(symbol, ts, "book_stale", features)

        sb = features.get("spread_bps")
        if sb is None or not math.isfinite(sb) or sb > p["max_spread_bps"]:
            return self._skip(symbol, ts, f"spread_too_wide:{sb}", features)

        rv30 = features.get("rv_30s")
        if rv30 is not None and math.isfinite(rv30) and rv30 > p["max_rv_30s"]:
            return self._skip(symbol, ts, f"rv_too_high:{rv30:.5f}", features)

        # Volume floor — be sure trades actually exist.
        buy_v = features.get("buy_volume_usd_30s") or 0.0
        sell_v = features.get("sell_volume_usd_30s") or 0.0
        if (buy_v + sell_v) < p["min_trade_volume_30s"]:
            return self._skip(symbol, ts, "low_volume", features)

        # Cooldown
        if ts < self._cooldown_until.get(symbol, 0.0):
            return None

        # Max positions
        if len(self._open_positions) >= int(p["max_positions"]):
            return None

        # Compute side + signal score
        side, score = self._signal_side_and_score(features)
        if side is None or not math.isfinite(score):
            return None
        if abs(score) < p["threshold"]:
            return None

        # Net-edge sanity : edge_bps ≥ cost_bps + margin_bps.
        # Conservative edge estimate : take_profit_bps * |normalized score|.
        edge_bps = float(p["take_profit_bps"]) * min(abs(score), 1.0)
        if edge_bps < p["cost_bps"] + p["margin_bps"]:
            return self._skip(symbol, ts,
                              f"edge_below_costs:{edge_bps:.2f}",
                              features, extra={"expected_edge_bps": edge_bps})

        # Build decision
        mid = features.get("mid")
        if not mid or mid <= 0:
            return None
        bid = features.get("best_bid") or mid
        ask = features.get("best_ask") or mid
        notional = float(p["notional_usd"])

        sl_bps = float(p["stop_loss_bps"])
        tp_bps = float(p["take_profit_bps"])
        if side == "long":
            entry_px = bid if p["maker_only"] else ask
            stop = entry_px * (1.0 - sl_bps / 10_000.0)
            tp = entry_px * (1.0 + tp_bps / 10_000.0)
            action = "PLACE_BUY"
            buy_price, sell_price = entry_px, None
        else:
            entry_px = ask if p["maker_only"] else bid
            stop = entry_px * (1.0 + sl_bps / 10_000.0)
            tp = entry_px * (1.0 - tp_bps / 10_000.0)
            action = "PLACE_SELL"
            buy_price, sell_price = None, entry_px

        # Cooldown + position bookkeeping
        self._cooldown_until[symbol] = ts + float(p["cooldown_seconds"])
        self._open_positions[symbol] = {"side": side, "ts": ts}

        d = StrategyDecision(
            action=action,
            symbol=symbol,
            reason=f"{self.SIGNAL_KEY}|score={score:.3f}|edge_bps={edge_bps:.2f}",
            buy_price=buy_price,
            sell_price=sell_price,
            notional_usd=notional,
            stop_loss=stop,
            take_profit=tp,
            max_hold_seconds=int(p["max_hold_seconds"]),
            confidence=min(abs(score), 1.0),
            expected_edge_bps=edge_bps,
            estimated_cost_bps=float(p["cost_bps"]),
            order_type="MAKER_SIM" if p["maker_only"] else "TAKER_SIM",
            strategy_family="alpha_seconds",
        )
        _log_alpha_signal({
            "ts": ts, "strategy": self.config.name, "symbol": symbol,
            "action": action, "reason": d.reason,
            "pressure_score_raw": features.get("pressure_score_raw"),
            "book_flow_divergence": features.get("book_flow_divergence"),
            "absorption_buy_proxy": features.get("absorption_buy_proxy"),
            "absorption_sell_proxy": features.get("absorption_sell_proxy"),
            "obi_5": features.get("obi_5"),
            "trade_imbalance_10s": features.get("trade_imbalance_10s"),
            "spread_bps": sb,
            "rv_30s": rv30,
            "expected_edge_bps": edge_bps,
            "cost_bps": p["cost_bps"],
            "enough_data": features.get("enough_data"),
            "book_stale": features.get("book_stale"),
        })
        return d

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        super().on_position_closed(symbol, pnl_net, exit_reason)
        self._open_positions.pop(symbol, None)

    # --- Subclass hook ---------------------------------------------------

    def _signal_side_and_score(self, features: dict) -> tuple[Optional[str], float]:
        raise NotImplementedError

    # --- helpers ---------------------------------------------------------

    def _skip(self, symbol: str, ts: float, reason: str, features: dict,
              extra: Optional[dict] = None) -> None:
        if self.decision_logger is not None and hasattr(self.decision_logger, "log_skip"):
            try:
                self.decision_logger.log_skip(
                    symbol=symbol, reason=reason,
                    timestamp=ts,
                    mid=features.get("mid"),
                    spread_bps=features.get("spread_bps"),
                    obi=features.get("obi_5"),
                )
            except Exception:
                pass
        return None


class AlphaPressureScalper(_AlphaScalperBase):
    """Trades the composite `pressure_score_raw`. Disabled by default."""

    SIGNAL_KEY = "pressure_score_raw"

    def _signal_side_and_score(self, features: dict) -> tuple[Optional[str], float]:
        score = features.get("pressure_score_raw")
        if score is None or not math.isfinite(score):
            return None, 0.0
        if score > 0:
            return "long", float(score)
        if score < 0:
            return "short", float(score)
        return None, 0.0
