"""
garch_vol_breakout.py — Volatility-gated directional micro-scalp.

This is the honest, evidence-based version of the "predict a short burst and
ride it at leverage" idea. The empirical probes (scripts/probe_arima_garch.py)
established two facts on the real seconds data:

  * GARCH / ARIMA give NO directional edge — GARCH's conditional-vol forecast
    correlates with the *magnitude* of the next move (corr ≈ +0.05) but ~0.00
    with its *sign*. So volatility models tell you WHEN it will move, never
    WHICH WAY.
  * Direction comes from microstructure: `microprice_pressure` and `obi_10`
    carry strong forward IC (≈ +0.3 at 15-30s on majors). But on majors the
    move is only ~1-3 bps — smaller than the round-trip cost — so a correct
    directional call still loses after fees. (BTC obi @5s: IC 0.45, net maker
    −2.6 bps.)

The conclusion that drives this strategy: **use the volatility forecast as a
COST GATE, not a direction predictor.** Only take a microstructure-directional
trade when the forecast move over the hold horizon is large enough to clear the
round-trip cost with a safety margin. Then size with explicit leverage and a
LIQUIDATION-aware stop so the asymmetric "tiny target vs. total-margin loss"
trap is bounded.

Volatility forecast: a RiskMetrics / GARCH(1,1)-with-omega≈0 recursion on 1 Hz
returns —  σ²_t = (1-λ)·r²_{t-1} + λ·σ²_{t-1}  — which is cheap to run online,
unlike re-fitting a full GARCH every second. Persistence λ is a param.

Paper-mode only until the live track record matches.
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


class GarchVolBreakoutStrategy(BaseStrategy):
    """Vol-gated, microstructure-directional, leverage-aware scalp. See docstring."""

    DEFAULT_PARAMS = dict(
        # ── horizon / hold ──────────────────────────────────────────────
        horizon_s=30,                  # hold seconds (also the vol scaling horizon)

        # ── volatility forecast (RiskMetrics / GARCH-lite) ──────────────
        vol_lambda=0.97,               # persistence (β); α = 1-λ, ω ≈ 0
        vol_warmup=300,                # samples before the forecast is usable

        # ── COST GATE — the whole point ─────────────────────────────────
        cost_bps_rt=6.0,               # round-trip cost assumed (maker-first ≈ 6)
        min_edge_mult=2.0,             # require forecast move ≥ mult × cost
        # forecast move over horizon (bps) = sigma_1s_bps * sqrt(horizon_s)

        # ── direction (microstructure) ──────────────────────────────────
        mpp_threshold=0.00008,         # |microprice_pressure| gate (fractional)
        require_obi_agree=True,        # obi_10 sign must match microprice_pressure
        obi_min=0.15,                  # |obi_10| floor when confirmation required

        # ── microstructure quality gates ────────────────────────────────
        max_spread_bps=20.0,
        min_trade_volume_30s=50.0,

        # ── sizing / leverage ───────────────────────────────────────────
        # notional = margin_usd * leverage, capped at max_position_size_usd.
        margin_usd=20.0,
        leverage=25.0,                 # backtest sweet-spot region (25-50x), NOT 150x
        maint_margin_frac=0.5,         # liquidation at adverse move = (1-mm)/L
        liq_safety=0.6,                # place stop at liq_safety × liq distance
        tp_return_on_margin=0.25,      # take profit at +25% on margin
        notional_usd=None,             # if set, overrides margin*leverage

        # ── execution / pacing ──────────────────────────────────────────
        cooldown_seconds=20,
        maker_only=True,               # maker-first to make the cost gate passable
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        p = config.params

        self._H: int = int(p["horizon_s"])
        self._lambda: float = float(p["vol_lambda"])
        self._vol_warmup: int = int(p["vol_warmup"])

        # Per-symbol online vol state and last mid (for 1s returns).
        self._var: dict[str, float] = {}            # EWMA of r^2 (fractional^2)
        self._n_vol: dict[str, int] = {c: 0 for c in config.coins}
        self._last_mid: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {c: 0.0 for c in config.coins}
        self._positions: dict[str, dict] = {}

    # ── BaseStrategy interface ───────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": True,
            "seconds_features": True,
            "bars": [], "funding": False, "external_spot": False,
            "warmup_bars": {}, "warmup_seconds": int(self._vol_warmup),
        }

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol, bar, ts):
        return None

    # ── vol recursion ────────────────────────────────────────────────────

    def _update_vol(self, symbol: str, mid: float) -> Optional[float]:
        """Update the EWMA variance with the latest 1s return. Returns the
        current 1-second sigma (fractional) once warmed up, else None."""
        last = self._last_mid.get(symbol)
        self._last_mid[symbol] = mid
        if last is None or last <= 0:
            return None
        r = (mid - last) / last
        lam = self._lambda
        prev = self._var.get(symbol)
        if prev is None:
            self._var[symbol] = r * r
        else:
            self._var[symbol] = (1.0 - lam) * (r * r) + lam * prev
        self._n_vol[symbol] = self._n_vol.get(symbol, 0) + 1
        if self._n_vol[symbol] < self._vol_warmup:
            return None
        return math.sqrt(max(self._var[symbol], 0.0))

    def on_second_features(self, symbol: str, features: dict, ts: float
                           ) -> Optional[StrategyDecision]:
        if symbol not in self._n_vol:
            return None
        mid = features.get("mid")
        if not mid or mid <= 0 or not math.isfinite(mid):
            return None

        sigma_1s = self._update_vol(symbol, float(mid))

        if symbol in self._positions:
            return None
        if sigma_1s is None:
            return None
        if ts < self._cooldown_until.get(symbol, 0.0):
            return None
        if len(self._positions) >= int(self.config.max_positions):
            return None

        p = self.config.params

        # ── COST GATE: forecast move over horizon must clear cost ────────
        fcast_move_bps = sigma_1s * math.sqrt(self._H) * 10_000.0
        cost_bps = float(p["cost_bps_rt"])
        if fcast_move_bps < float(p["min_edge_mult"]) * cost_bps:
            return None

        # ── microstructure quality ───────────────────────────────────────
        sb = features.get("spread_bps")
        if sb is None or not math.isfinite(sb) or sb > float(p["max_spread_bps"]):
            return None
        buy_v = features.get("buy_volume_usd_30s") or features.get("buy_volume_usd_10s") or 0.0
        sell_v = features.get("sell_volume_usd_30s") or features.get("sell_volume_usd_10s") or 0.0
        if (buy_v + sell_v) < float(p["min_trade_volume_30s"]):
            return None

        # ── DIRECTION from microstructure ────────────────────────────────
        mpp = features.get("microprice_pressure")
        if mpp is None or not math.isfinite(mpp) or abs(mpp) < float(p["mpp_threshold"]):
            return None
        side = "long" if mpp > 0 else "short"

        if p["require_obi_agree"]:
            obi = features.get("obi_10")
            if obi is None or not math.isfinite(obi):
                return None
            if abs(obi) < float(p["obi_min"]):
                return None
            if (obi > 0) != (mpp > 0):       # disagree → skip
                return None

        # ── sizing with explicit leverage + liquidation-aware stop ───────
        L = float(p["leverage"])
        if p["notional_usd"]:
            notional = float(p["notional_usd"])
        else:
            notional = float(p["margin_usd"]) * L
        notional = min(notional, float(self.config.max_position_size_usd))

        bid = features.get("best_bid") or mid
        ask = features.get("best_ask") or mid

        # liquidation distance (fractional price move) and our protective stop
        liq_frac = max(1e-6, (1.0 - float(p["maint_margin_frac"])) / L)
        stop_frac = float(p["liq_safety"]) * liq_frac
        tp_frac = float(p["tp_return_on_margin"]) / L      # price move for +X% on margin

        if side == "long":
            entry_px = bid if p["maker_only"] else ask
            stop = entry_px * (1.0 - stop_frac)
            tp = entry_px * (1.0 + tp_frac)
            action, buy_price, sell_price = "PLACE_BUY", entry_px, None
        else:
            entry_px = ask if p["maker_only"] else bid
            stop = entry_px * (1.0 + stop_frac)
            tp = entry_px * (1.0 - tp_frac)
            action, buy_price, sell_price = "PLACE_SELL", None, entry_px

        reason = (f"garchgate_{side}|fcast={fcast_move_bps:.1f}bps>"
                  f"{p['min_edge_mult']}x cost|mpp={mpp:.5f}|L={L:.0f}x")
        return StrategyDecision(
            action=action, symbol=symbol, reason=reason,
            buy_price=buy_price, sell_price=sell_price,
            notional_usd=notional,
            stop_loss=stop, take_profit=tp,
            max_hold_seconds=int(self._H),
            confidence=min(0.9, fcast_move_bps / (4.0 * cost_bps)),
            expected_edge_bps=float(tp_frac * 10_000.0),
            estimated_cost_bps=cost_bps,
            order_type="MAKER_SIM" if p["maker_only"] else "TAKER_SIM",
            strategy_family="garch_vol_breakout",
            metadata={"leverage": L, "margin_usd": float(p["margin_usd"]),
                      "fcast_move_bps": fcast_move_bps},
        )

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        p = self.config.params
        self._positions[symbol] = {
            "side": side, "entry": price, "opened_at": ts,
            "max_hold_ts": ts + float(self._H), "pos_id": pos_id,
        }
        self._cooldown_until[symbol] = ts + float(p["cooldown_seconds"])
        return None

    def check_position_exits(self, symbol, book, ts):
        pos = self._positions.get(symbol)
        if pos is None or ts < pos["max_hold_ts"]:
            return None
        return StrategyDecision(
            action="CLOSE", symbol=symbol,
            reason=f"vol_time_exit|hold_s={int(ts - pos['opened_at'])}",
            metadata={"pos_id": pos.get("pos_id")},
        )

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._positions.pop(symbol, None)
        p = self.config.params
        cd = float(p["cooldown_seconds"]) * (2.0 if pnl_net < 0 else 1.0)
        import time as _t
        self._cooldown_until[symbol] = _t.time() + cd
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        n = self._n_vol.get(symbol, 0)
        sigma = math.sqrt(self._var[symbol]) if symbol in self._var else None
        fcast_bps = sigma * math.sqrt(self._H) * 1e4 if sigma is not None else None
        p = self.config.params
        return {
            "horizon_s": self._H,
            "vol_samples": n,
            "ready": n >= self._vol_warmup,
            "sigma_1s_bps": round(sigma * 1e4, 3) if sigma is not None else None,
            "forecast_move_bps": round(fcast_bps, 2) if fcast_bps is not None else None,
            "cost_gate_bps": round(float(p["min_edge_mult"]) * float(p["cost_bps_rt"]), 2),
            "gate_open": (fcast_bps is not None and
                          fcast_bps >= float(p["min_edge_mult"]) * float(p["cost_bps_rt"])),
            "leverage": float(p["leverage"]),
            "in_position": symbol in self._positions,
        }
