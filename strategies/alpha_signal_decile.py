"""
alpha_signal_decile.py — Decile-threshold alpha strategy.

Design (per alpha_discovery_2026-05-25):
  - One strategy instance watches ONE seconds-feature on N coins.
  - Maintains a rolling top/bottom decile threshold per coin (over the
    last `window_size` samples).
  - When feature crosses the configured threshold:
      side="long"  → PLACE_BUY  if feature ≥ top-decile
      side="short" → PLACE_SELL if feature ≤ bottom-decile
  - Holds the position for `horizon_s` seconds, then closes (time stop via
    `max_hold_seconds` on the decision).
  - Adds a small protective stop_loss_bps + take_profit_bps in case the
    engine's max_hold isn't honored cleanly.

This strategy is the productionised output of the alpha discovery pipeline.
Each entry in the preset corresponds to one validated signal from
`reports/alpha_discovery.json`.

Paper-mode only. No live until the live PnL track-record matches the
backtest expectation.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Optional

from strategies.base_strategy import (
    BaseStrategy,
    StrategyConfig,
    StrategyDecision,
)

log = logging.getLogger(__name__)


class AlphaSignalDecileStrategy(BaseStrategy):
    """Top-decile feature crossing → time-stopped position. See module docstring."""

    DEFAULT_PARAMS = dict(
        # The signal definition.
        feature="liquidity_vacuum",        # which seconds_feature to watch
        horizon_s=300,                      # hold seconds
        decile=0.10,                        # threshold quantile (top/bottom 10%)
        side="long",                        # 'long' = enter when feature high; 'short' = low

        # Rolling threshold window.
        window_size=14400,                  # rolling samples for threshold (4h at 1Hz)
        min_window_samples=1800,            # before trading: need 30 min of data

        # Risk gates.
        cooldown_seconds=20,
        max_spread_bps=25.0,                # skip if spread too wide
        min_trade_volume_30s=50.0,          # need any trade activity

        # Cost model used for decisions; protective levels.
        cost_bps_rt=9.0,
        margin_bps=2.0,
        stop_loss_bps=80.0,                 # wide protective stop (8 std normally enough)
        take_profit_bps=80.0,               # symmetric — let max_hold fire most of the time

        # Sizing.
        notional_usd=250.0,

        # Execution.
        maker_only=False,                   # default taker; backtest used taker costs

        # ── v2 upgrades (all default-off → backward compatible) ──────────
        # (1) Directional confirmation. When on, a decile crossing only fires
        #     if the high-IC microstructure features agree with the side.
        #     The probe showed microprice_pressure / obi_10 carry the real
        #     directional IC; the raw decile feature alone often does not.
        confirm_direction=False,
        confirm_mpp_threshold=0.0,          # |microprice_pressure| floor
        confirm_obi_min=0.0,                # |obi_10| floor (0 = ignore obi)

        # (2) Cost gate. Require the forecast move over the horizon to clear
        #     the round-trip cost: rv_30s · sqrt(H/30) · 1e4 ≥ mult · cost_bps.
        #     This is what stops majors-at-seconds (move < cost) from trading.
        #     0 disables the gate.
        min_expected_move_mult=0.0,

        # (3) Leverage-aware sizing. When margin_usd AND leverage are set,
        #     notional = margin_usd · leverage (capped at max_position_size),
        #     the protective stop is placed at liq_safety × the liquidation
        #     distance (1-mm)/L, and TP targets +tp_return_on_margin on margin.
        #     Leave margin_usd/leverage None to keep the legacy bps sizing.
        margin_usd=None,
        leverage=None,
        maint_margin_frac=0.5,
        liq_safety=0.6,
        tp_return_on_margin=None,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        p = config.params

        self._feature: str = str(p["feature"])
        self._horizon_s: int = int(p["horizon_s"])
        self._decile: float = float(p["decile"])
        self._side: str = str(p["side"]).lower()
        if self._side not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got {self._side}")

        self._window_size: int = int(p["window_size"])
        self._min_warmup: int = int(p["min_window_samples"])

        # Per-symbol rolling window of feature values.
        self._buf: dict[str, deque] = {
            c: deque(maxlen=self._window_size) for c in config.coins
        }
        # Per-symbol cooldown until ts.
        self._cooldown_until: dict[str, float] = {c: 0.0 for c in config.coins}
        # Per-symbol open position bookkeeping (paper-side; the engine tracks the real one).
        self._positions: dict[str, dict] = {}

    # ── BaseStrategy interface ───────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": True,
            "seconds_features": True,
            "bars": [], "funding": False, "external_spot": False,
            "warmup_bars": {},
            "warmup_seconds": int(self._min_warmup),
        }

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol, bar, ts):
        return None

    def on_second_features(self, symbol: str, features: dict, ts: float
                           ) -> Optional[StrategyDecision]:
        if symbol not in self._buf:
            return None

        # Pull feature; ignore if missing.
        val = features.get(self._feature)
        if val is None or not isinstance(val, (int, float)) or not math.isfinite(val):
            return None

        # Update rolling buffer regardless of trade outcome.
        self._buf[symbol].append(float(val))

        # Position-side path: let max_hold fire via engine, no early exit here.
        if symbol in self._positions:
            return None

        # Warmup.
        n = len(self._buf[symbol])
        if n < self._min_warmup:
            return None

        # Symbol-level cooldown.
        if ts < self._cooldown_until.get(symbol, 0.0):
            return None

        # Capacity.
        if len(self._positions) >= int(self.config.max_positions):
            return None

        p = self.config.params

        # Quick microstructure gates.
        sb = features.get("spread_bps")
        if sb is None or not math.isfinite(sb) or sb > float(p["max_spread_bps"]):
            return None
        buy_v = features.get("buy_volume_usd_30s") or features.get("buy_volume_usd_10s") or 0.0
        sell_v = features.get("sell_volume_usd_30s") or features.get("sell_volume_usd_10s") or 0.0
        if (buy_v + sell_v) < float(p["min_trade_volume_30s"]):
            return None

        # Decile threshold computation. Use last `window_size` samples.
        buf_list = list(self._buf[symbol])
        if self._side == "long":
            # Enter when feature is in top decile.
            thr = self._quantile(buf_list, 1.0 - self._decile)
            cond = val >= thr
            action_side = "long"
        else:
            thr = self._quantile(buf_list, self._decile)
            cond = val <= thr
            action_side = "short"

        if not cond:
            return None

        # ── (2) Cost gate: forecast move over horizon must clear the cost ──
        move_mult = float(p["min_expected_move_mult"])
        if move_mult > 0.0:
            rv30 = features.get("rv_30s")
            if rv30 is None or not math.isfinite(rv30):
                return None
            fcast_move_bps = float(rv30) * math.sqrt(self._horizon_s / 30.0) * 10_000.0
            if fcast_move_bps < move_mult * float(p["cost_bps_rt"]):
                return None

        # ── (1) Directional confirmation from high-IC microstructure ──────
        if p["confirm_direction"]:
            mpp = features.get("microprice_pressure")
            if mpp is None or not math.isfinite(mpp):
                return None
            if abs(mpp) < float(p["confirm_mpp_threshold"]):
                return None
            want_pos = (action_side == "long")
            if (mpp > 0) != want_pos:        # microstructure disagrees with side
                return None
            if float(p["confirm_obi_min"]) > 0.0:
                obi = features.get("obi_10")
                if obi is None or not math.isfinite(obi) or abs(obi) < float(p["confirm_obi_min"]):
                    return None
                if (obi > 0) != want_pos:
                    return None

        # Build the decision.
        mid = features.get("mid")
        if not mid or mid <= 0:
            return None
        bid = features.get("best_bid") or mid
        ask = features.get("best_ask") or mid

        # ── (3) Sizing: leverage-aware if configured, else legacy bps ─────
        use_lev = p["margin_usd"] is not None and p["leverage"] is not None
        if use_lev:
            L = float(p["leverage"])
            notional = min(float(p["margin_usd"]) * L,
                           float(self.config.max_position_size_usd))
            liq_frac = max(1e-6, (1.0 - float(p["maint_margin_frac"])) / L)
            sl_frac = float(p["liq_safety"]) * liq_frac
            tpr = p["tp_return_on_margin"]
            tp_frac = (float(tpr) / L) if tpr is not None else (float(p["take_profit_bps"]) / 10_000.0)
        else:
            notional = float(p["notional_usd"])
            sl_frac = float(p["stop_loss_bps"]) / 10_000.0
            tp_frac = float(p["take_profit_bps"]) / 10_000.0

        if action_side == "long":
            entry_px = bid if p["maker_only"] else ask
            stop = entry_px * (1.0 - sl_frac)
            tp   = entry_px * (1.0 + tp_frac)
            action = "PLACE_BUY"
            buy_price, sell_price = entry_px, None
        else:
            entry_px = ask if p["maker_only"] else bid
            stop = entry_px * (1.0 + sl_frac)
            tp   = entry_px * (1.0 - tp_frac)
            action = "PLACE_SELL"
            buy_price, sell_price = None, entry_px

        edge_bps = tp_frac * 10_000.0
        reason = (f"decile_{self._side}|feat={self._feature}|val={val:.4f}|"
                  f"thr={thr:.4f}|H={self._horizon_s}s"
                  + (f"|L={float(p['leverage']):.0f}x" if use_lev else ""))

        return StrategyDecision(
            action=action, symbol=symbol, reason=reason,
            buy_price=buy_price, sell_price=sell_price,
            notional_usd=notional,
            stop_loss=stop, take_profit=tp,
            max_hold_seconds=int(self._horizon_s),
            confidence=0.5,
            expected_edge_bps=edge_bps,
            estimated_cost_bps=float(p["cost_bps_rt"]),
            order_type="MAKER_SIM" if p["maker_only"] else "TAKER_SIM",
            strategy_family="alpha_decile",
        )

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        # Track the position locally so we don't try to enter again until it
        # closes. Real SL/TP/max_hold are enforced by the engine's executor
        # using the decision metadata.
        p = self.config.params
        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "opened_at":   ts,
            "max_hold_ts": ts + float(self._horizon_s),
            "pos_id":      pos_id,
        }
        # Set short post-fill cooldown to avoid immediate re-entry on noise.
        self._cooldown_until[symbol] = ts + float(p["cooldown_seconds"])
        return None  # SL/TP/max_hold already on the original decision

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        """Belt-and-braces time exit if the engine didn't fire max_hold."""
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        if ts < pos["max_hold_ts"]:
            return None
        return StrategyDecision(
            action="CLOSE", symbol=symbol,
            reason=f"decile_time_exit|hold_s={int(ts-pos['opened_at'])}",
            metadata={"pos_id": pos.get("pos_id")},
        )

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        # Slight extra cooldown on losing exits.
        p = self.config.params
        cd = float(p["cooldown_seconds"]) * (2.0 if pnl_net < 0 else 1.0)
        import time as _t
        self._cooldown_until[symbol] = _t.time() + cd
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        n = len(self._buf.get(symbol, []))
        thr_long = thr_short = None
        if n >= self._min_warmup:
            buf_list = list(self._buf[symbol])
            thr_long = self._quantile(buf_list, 1.0 - self._decile)
            thr_short = self._quantile(buf_list, self._decile)
        return {
            "feature": self._feature,
            "side": self._side,
            "horizon_s": self._horizon_s,
            "decile": self._decile,
            "window_samples": n,
            "warmup_target": self._min_warmup,
            "ready": n >= self._min_warmup,
            "thr_top_decile": round(thr_long, 6) if thr_long is not None else None,
            "thr_bottom_decile": round(thr_short, 6) if thr_short is not None else None,
            "in_position": symbol in self._positions,
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _quantile(buf: list, q: float) -> float:
        """Tiny pure-python quantile (no numpy dep here) on a sorted copy.
        For window_size up to ~15k this stays fast enough at 1Hz."""
        if not buf:
            return 0.0
        sorted_buf = sorted(buf)
        idx = max(0, min(len(sorted_buf) - 1, int(round(q * (len(sorted_buf) - 1)))))
        return float(sorted_buf[idx])
