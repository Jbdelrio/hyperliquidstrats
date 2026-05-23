"""
btc_5min_binary_repl.py — BTC_5MIN_BINARY_REPL

Synthetic replication of "BTC up / down in 5 minutes" binary markets
(Polymarket / Kalshi style) using BTC perpetuals.

  Long  BTC perp  ≈ YES "up"   over the next 300 s
  Short BTC perp  ≈ YES "down" over the next 300 s

This is NOT a risk-free arbitrage. It is a directional, probabilistic
replication: a rule-based estimate of P(S_T > S_0) over a 5-minute horizon,
built from short-horizon momentum, order-book imbalance, aggressor flow,
realised volatility and a spread filter. A trade is only taken when the
expected move is judged to clear fees + spread + slippage.

PAPER-ONLY by default (`paper_only=true`, `live_enabled=false`, the strategy
entry ships `enabled=false`). The engine's HighFreqExecutor handles the actual
paper fills, partial fills, MAKER/TAKER simulation and TP/SL/max-hold exits;
this class produces the decisions, the warmup gate, the cost/risk gates and
the signal-reversal early exit.

Leverage note: in the paper simulator, PnL is a function of NOTIONAL, not of
leverage — `$10 margin × 10x` and `$100 notional` give the identical PnL.
Leverage only changes margin usage / liquidation distance. `leverage` is
clamped to `max_leverage` (default 10). See the module summary for why 10x is
the coherent ceiling for a $1 stop on this horizon.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import time
from collections import deque
from typing import Optional

from strategies.base_strategy import (
    BarData,
    BaseStrategy,
    StrategyConfig,
    StrategyDecision,
)

log = logging.getLogger(__name__)

# Model-quality bands by warmup elapsed (seconds).
_Q_COLD, _Q_WARMING, _Q_READY, _Q_GOOD = "COLD", "WARMING", "READY", "GOOD"

# No-trade reason codes (always returned/logged when no trade is taken).
NT_WARMUP            = "NO_TRADE_WARMUP"
NT_FEATURES          = "NO_TRADE_FEATURES_NOT_READY"
NT_NO_VOL            = "NO_TRADE_NO_VOL"
NT_SPREAD            = "NO_TRADE_SPREAD_TOO_WIDE"
NT_WEAK              = "NO_TRADE_SIGNAL_TOO_WEAK"
NT_OBI               = "NO_TRADE_OBI_NOT_ALIGNED"
NT_FLOW              = "NO_TRADE_FLOW_NOT_ALIGNED"
NT_IN_POSITION       = "NO_TRADE_ALREADY_IN_POSITION"
NT_PENDING           = "NO_TRADE_PENDING_ORDER"
NT_DAILY_LOSS        = "NO_TRADE_DAILY_LOSS_LIMIT"
NT_CONSEC_LOSS       = "NO_TRADE_CONSECUTIVE_LOSS_LIMIT"
NT_MAX_TRADES        = "NO_TRADE_MAX_TRADES_PER_HOUR"
NT_COOLDOWN          = "NO_TRADE_COOLDOWN"
NT_DATA_STALE        = "NO_TRADE_DATA_STALE"
NT_MIN_SIZE          = "NO_TRADE_MIN_SIZE"
NT_OK                = ""   # a trade was taken / no blocking reason


class BTC5MinBinaryReplStrategy(BaseStrategy):
    """BTC 5-minute up/down binary replication on perps. Paper-only."""

    DISPLAY_NAME = "BTC 5min Binary Replication"

    DEFAULT_PARAMS = dict(
        symbol="BTC",
        time_horizon_seconds=300,

        # Warmup
        min_warmup_seconds=1800,
        preferred_warmup_seconds=3600,
        zscore_window_seconds=1800,

        # Feature windows
        price_windows_seconds=[10, 30, 60, 180, 300],
        vol_windows_seconds=[60, 180, 300],

        # Signal thresholds
        long_threshold=0.56,
        short_threshold=0.44,
        strong_long_threshold=0.62,
        strong_short_threshold=0.38,
        min_obi_long=0.15,
        max_obi_short=-0.15,
        min_flow_long=0.20,
        max_flow_short=-0.20,
        min_rv_300s_bps=8.0,
        max_spread_bps=1.5,
        critical_spread_bps=3.0,

        # Sizing
        capital_reference_usd=500.0,
        max_margin_per_trade_usd=10.0,
        leverage=10.0,
        max_leverage=10.0,            # hard ceiling; see module docstring
        max_position_notional_usd=100.0,
        min_notional_usd=10.0,

        # Exits
        take_profit_usd=1.5,
        stop_loss_usd=1.0,
        max_holding_seconds=300,
        early_exit_enabled=True,

        # Execution
        entry_order_type_default="post_only_limit",
        allow_taker_on_strong_signal=True,

        # Risk
        max_daily_loss_usd=20.0,
        max_consecutive_losses=3,
        max_trades_per_hour=6,
        cooldown_after_loss_seconds=300,
        cooldown_after_3_losses_seconds=1800,
        data_stale_seconds=3.0,

        # Modes / logging
        paper_only=True,
        live_enabled=False,
        log_features=True,
        log_signals=True,
        feature_log_path="logs/btc5min_features.csv",
        trade_log_path="logs/btc5min_trades.csv",
        enable_alerts=True,
    )

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        p = config.params

        self.symbol = str(p["symbol"]).upper()
        self._zwin  = int(p["zscore_window_seconds"])

        # Rolling buffers (1 Hz). 7200 s = 2 h of history.
        self._mid_buf:  deque = deque(maxlen=7200)   # (ts, mid)
        self._feat_buf: deque = deque(maxlen=7200)   # (ts, dict) for logging/calib
        # z-score source series — one deque per z-scored quantity.
        self._zsrc: dict[str, deque] = {
            k: deque(maxlen=self._zwin)
            for k in ("return_60s", "return_180s", "obi_10", "flow_60s", "spread_bps")
        }

        self._first_ts: Optional[float] = None
        self._last_feat_ts: float = 0.0

        # Position / order state.
        self._position: Optional[dict] = None    # set on on_fill
        self._pending:  Optional[dict] = None     # set on decision emit
        self._last_features: dict = {}
        self._last_decision: str = "NO_TRADE"
        self._last_no_trade_reason: str = NT_WARMUP

        # Risk state.
        self._daily_pnl: float = 0.0
        self._day_key: str = time.strftime("%Y-%m-%d", time.localtime())
        self._consec_losses: int = 0
        self._trade_ts: deque = deque(maxlen=64)   # entry timestamps (this hour)
        self._cooldown_until: float = 0.0
        self._disabled_for_day: bool = False
        self._n_trades: int = 0
        self._wins: int = 0
        self._last_alert: str = ""

        self._feat_log_ready = False
        self._trade_log_ready = False

    # ── data requirements / warmup ───────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": True,
            "seconds_features": True,
            "bars": [], "funding": False, "external_spot": False,
            "warmup_bars": {},
            "warmup_seconds": int(self.config.params["min_warmup_seconds"]),
        }

    def warmup_status(self) -> dict:
        secs = self._warmup_seconds()
        need = int(self.config.params["min_warmup_seconds"])
        return {self.symbol: {"seconds": (int(secs), need, secs >= need)}}

    def _warmup_seconds(self) -> float:
        if self._first_ts is None:
            return 0.0
        return max(0.0, (self._last_feat_ts or time.time()) - self._first_ts)

    def _model_quality(self) -> str:
        s = self._warmup_seconds()
        p = self.config.params
        if s < 600:
            return _Q_COLD
        if s < p["min_warmup_seconds"]:
            return _Q_WARMING
        if s < p["preferred_warmup_seconds"]:
            return _Q_READY
        return _Q_GOOD

    # ── unused hooks (this strategy is seconds-driven) ───────────────────────

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol, bar, ts):
        return None

    # ── main seconds hook ────────────────────────────────────────────────────

    def on_second_features(self, symbol: str, features: dict, ts: float
                            ) -> Optional[StrategyDecision]:
        if symbol.upper() != self.symbol:
            return None

        if self._first_ts is None:
            self._first_ts = ts
        self._last_feat_ts = ts

        # Roll the day over (resets the daily loss lock).
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day != self._day_key:
            self._day_key = day
            self._daily_pnl = 0.0
            self._disabled_for_day = False

        feat = self._compute_features(features, ts)
        self._last_features = feat
        self._feat_buf.append((ts, feat))

        # Position management takes priority — early-exit on signal reversal.
        if self._position is not None:
            self._last_decision = "IN_POSITION"
            self._last_no_trade_reason = NT_IN_POSITION
            dec = self._check_early_exit(feat, ts)
            if self.config.params["log_features"]:
                self._log_feature_row(ts, feat, "EARLY_EXIT" if dec else "HOLD")
            return dec

        # Expire a stale pending order (executor did not fill it).
        if self._pending is not None:
            if ts - self._pending["ts"] > 8.0:
                self._alert("ORDER_UNFILLED", ts)
                self._pending = None
            else:
                self._last_decision = "NO_TRADE"
                self._last_no_trade_reason = NT_PENDING
                if self.config.params["log_features"]:
                    self._log_feature_row(ts, feat, "NO_TRADE")
                return None

        decision, reason = self._check_entry(feat, ts)
        self._last_no_trade_reason = reason
        self._last_decision = (decision.action if decision is not None else "NO_TRADE")
        if self.config.params["log_features"]:
            self._log_feature_row(ts, feat, self._last_decision)
        return decision

    # ── feature computation ──────────────────────────────────────────────────

    def _compute_features(self, f: dict, ts: float) -> dict:
        """Build the strategy's feature snapshot from the seconds-engine dict
        plus this strategy's own rolling buffers."""
        mid = _num(f.get("mid"))
        if mid and mid > 0:
            self._mid_buf.append((ts, mid))

        spread_bps = _num(f.get("spread_bps"))
        obi_10     = _num(f.get("obi_10"))
        obi_5      = _num(f.get("obi_5"))
        # Aggressor flow: trade imbalance over 60 s (engine feature); fall back
        # to ofi_60s, then 0.0.
        flow_60s = f.get("trade_imbalance_60s")
        if flow_60s is None or not _finite(flow_60s):
            flow_60s = f.get("ofi_60s")
        flow_60s = _num(flow_60s) or 0.0

        out: dict = {
            "ts": ts, "mid": mid, "spread_bps": spread_bps,
            "obi_5": obi_5, "obi_10": obi_10, "flow_60s": flow_60s,
            "book_stale": bool(f.get("book_stale", False)),
            "enough_data": bool(f.get("enough_data", True)),
            "best_bid": _num(f.get("best_bid")) or mid,
            "best_ask": _num(f.get("best_ask")) or mid,
        }

        # Returns over the configured windows.
        for w in self.config.params["price_windows_seconds"]:
            out[f"return_{w}s"] = self._return(ts, int(w))
        # Realised vol over the configured windows (bps).
        for w in self.config.params["vol_windows_seconds"]:
            out[f"rv_{w}s_bps"] = self._rv_bps(ts, int(w))

        # Update z-score source series, then compute z-scores.
        self._zsrc["return_60s"].append(out.get("return_60s") or 0.0)
        self._zsrc["return_180s"].append(out.get("return_180s") or 0.0)
        self._zsrc["obi_10"].append(obi_10 or 0.0)
        self._zsrc["flow_60s"].append(flow_60s or 0.0)
        self._zsrc["spread_bps"].append(spread_bps or 0.0)

        out["z_return_60s"]  = self._z("return_60s",  out.get("return_60s"))
        out["z_return_180s"] = self._z("return_180s", out.get("return_180s"))
        out["z_obi_top10"]   = self._z("obi_10",      obi_10)
        out["z_flow_60s"]    = self._z("flow_60s",    flow_60s)
        out["z_spread_bps"]  = self._z("spread_bps",  spread_bps)

        # Score → probability.
        score = (
            0.25 * out["z_return_60s"]
            + 0.20 * out["z_return_180s"]
            + 0.25 * out["z_obi_top10"]
            + 0.20 * out["z_flow_60s"]
            - 0.10 * out["z_spread_bps"]
        )
        out["score"] = score
        out["p_up"]  = _clamp(1.0 / (1.0 + math.exp(-score)), 0.01, 0.99)
        out["model_quality"] = self._model_quality()
        out["warmup_seconds"] = int(self._warmup_seconds())
        return out

    def _return(self, ts: float, window_s: int) -> Optional[float]:
        past = self._value_at(ts - window_s)
        now  = self._mid_buf[-1][1] if self._mid_buf else None
        if past and now and past > 0:
            return (now - past) / past
        return None

    def _rv_bps(self, ts: float, window_s: int) -> Optional[float]:
        cutoff = ts - window_s
        pts = [(t, m) for (t, m) in self._mid_buf if t >= cutoff and m > 0]
        if len(pts) < 5:
            return None
        ss = 0.0
        for i in range(1, len(pts)):
            p0, p1 = pts[i - 1][1], pts[i][1]
            if p0 > 0:
                r = (p1 - p0) / p0
                ss += r * r
        return math.sqrt(ss) * 10_000.0

    def _value_at(self, target_ts: float) -> Optional[float]:
        """Mid price at the buffered sample closest to target_ts."""
        best = None
        best_dt = None
        for (t, m) in self._mid_buf:
            dt = abs(t - target_ts)
            if best_dt is None or dt < best_dt:
                best, best_dt = m, dt
        # Reject if the closest sample is more than 5 s off the target.
        if best_dt is not None and best_dt <= 5.0:
            return best
        return None

    def _z(self, key: str, value) -> float:
        buf = self._zsrc.get(key)
        if not buf or value is None or not _finite(value) or len(buf) < 30:
            return 0.0
        n = len(buf)
        mean = sum(buf) / n
        var = sum((x - mean) ** 2 for x in buf) / n
        std = math.sqrt(var)
        if std < 1e-12:
            return 0.0
        return _clamp((value - mean) / std, -6.0, 6.0)

    # ── entry logic ──────────────────────────────────────────────────────────

    def _check_entry(self, feat: dict, ts: float) -> "tuple[Optional[StrategyDecision], str]":
        p = self.config.params

        # 1. Warmup
        if self._warmup_seconds() < p["min_warmup_seconds"]:
            return None, NT_WARMUP

        # 2. Data integrity
        if not feat["enough_data"] or feat["book_stale"]:
            return None, NT_FEATURES
        mid = feat["mid"]
        if not mid or mid <= 0:
            return None, NT_FEATURES
        if (ts - self._last_feat_ts) > p["data_stale_seconds"] + 1.0:
            return None, NT_DATA_STALE
        for k in ("z_return_60s", "z_return_180s", "z_obi_top10", "z_flow_60s"):
            if feat.get(k) is None:
                return None, NT_FEATURES

        # 3. Risk gates
        ok, reason = self._risk_gate(ts)
        if not ok:
            return None, reason

        # 4. Microstructure gates
        spread_bps = feat["spread_bps"]
        if spread_bps is None or spread_bps > p["max_spread_bps"]:
            return None, NT_SPREAD
        rv300 = feat.get("rv_300s_bps")
        if rv300 is None or rv300 < p["min_rv_300s_bps"]:
            return None, NT_NO_VOL

        # 5. Signal
        p_up   = feat["p_up"]
        obi_10 = feat["obi_10"] or 0.0
        flow   = feat["flow_60s"] or 0.0

        long_ok  = p_up > p["long_threshold"]
        short_ok = p_up < p["short_threshold"]
        if not (long_ok or short_ok):
            return None, NT_WEAK

        if long_ok:
            if obi_10 <= p["min_obi_long"]:
                return None, NT_OBI
            if flow <= p["min_flow_long"]:
                return None, NT_FLOW
            side = "long"
        else:
            if obi_10 >= p["max_obi_short"]:
                return None, NT_OBI
            if flow >= p["max_flow_short"]:
                return None, NT_FLOW
            side = "short"

        # 6. Sizing
        notional, margin, lev = self._sizing()
        if notional < p["min_notional_usd"]:
            return None, NT_MIN_SIZE

        # 7. Build the decision
        bid = feat["best_bid"] or mid
        ask = feat["best_ask"] or mid
        strong = (p_up > p["strong_long_threshold"]) if side == "long" \
            else (p_up < p["strong_short_threshold"])
        taker = bool(p["allow_taker_on_strong_signal"] and strong)
        order_type = "TAKER_SIM" if taker else "MAKER_SIM"

        tp_usd = float(p["take_profit_usd"])
        sl_usd = float(p["stop_loss_usd"])
        if side == "long":
            entry_px = ask if taker else bid
            tp = entry_px * (1.0 + tp_usd / notional)
            sl = entry_px * (1.0 - sl_usd / notional)
            action, buy_px, sell_px = "PLACE_BUY", entry_px, None
        else:
            entry_px = bid if taker else ask
            tp = entry_px * (1.0 - tp_usd / notional)
            sl = entry_px * (1.0 + sl_usd / notional)
            action, buy_px, sell_px = "PLACE_SELL", None, entry_px

        self._pending = {"side": side, "ts": ts, "notional": notional,
                         "margin": margin, "leverage": lev,
                         "entry_px_intended": entry_px, "p_up": p_up,
                         "order_type": order_type}

        reason_txt = (f"binary_repl_{side} p_up={p_up:.3f} obi={obi_10:+.2f} "
                      f"flow={flow:+.2f} {order_type} lev={lev:.0f}x "
                      f"notional=${notional:.0f}")
        self._alert("POSITION_OPENED", ts)
        dec = StrategyDecision(
            action=action, symbol=self.symbol, reason=reason_txt,
            buy_price=buy_px, sell_price=sell_px,
            notional_usd=notional,
            stop_loss=sl, take_profit=tp,
            max_hold_seconds=int(p["max_holding_seconds"]),
            confidence=abs(p_up - 0.5) * 2.0,
            order_type=order_type,
            strategy_family="binary_repl",
            metadata={"p_up": p_up, "score": feat["score"], "side": side,
                      "margin_usd": margin, "leverage": lev,
                      "model_quality": feat["model_quality"]},
        )
        return dec, NT_OK

    # ── early exit (signal reversal) ─────────────────────────────────────────

    def _check_early_exit(self, feat: dict, ts: float) -> Optional[StrategyDecision]:
        """Engine enforces TP / SL / max-hold from the decision; this adds the
        signal-reversal / spread-blowout early exit."""
        if not self.config.params["early_exit_enabled"] or self._position is None:
            return None
        side  = self._position["side"]
        p_up  = feat.get("p_up")
        obi   = feat.get("obi_10") or 0.0
        flow  = feat.get("flow_60s") or 0.0
        sbps  = feat.get("spread_bps")
        crit  = self.config.params["critical_spread_bps"]

        reason = None
        if sbps is not None and sbps > crit:
            reason = "EARLY_EXIT_SPREAD"
        elif p_up is not None and side == "long" and p_up < 0.50:
            reason = "EARLY_EXIT_SIGNAL_REVERSE"
        elif p_up is not None and side == "short" and p_up > 0.50:
            reason = "EARLY_EXIT_SIGNAL_REVERSE"
        elif side == "long" and (obi < -0.35 or flow < -0.35):
            reason = "EARLY_EXIT_FLOW_REVERSE"
        elif side == "short" and (obi > 0.35 or flow > 0.35):
            reason = "EARLY_EXIT_FLOW_REVERSE"

        if reason is None:
            return None
        self._alert(reason, ts)
        return StrategyDecision(
            action="CLOSE", symbol=self.symbol, reason=reason,
            metadata={"exit_kind": "early", "pos_id": self._position.get("pos_id")},
        )

    # ── risk gates ───────────────────────────────────────────────────────────

    def _risk_gate(self, ts: float) -> "tuple[bool, str]":
        p = self.config.params
        if self._disabled_for_day or self._daily_pnl <= -abs(p["max_daily_loss_usd"]):
            self._disabled_for_day = True
            return False, NT_DAILY_LOSS
        if self._consec_losses >= int(p["max_consecutive_losses"]):
            return False, NT_CONSEC_LOSS
        if ts < self._cooldown_until:
            return False, NT_COOLDOWN
        # Trades in the last hour.
        hour_ago = ts - 3600.0
        recent = [t for t in self._trade_ts if t >= hour_ago]
        if len(recent) >= int(p["max_trades_per_hour"]):
            return False, NT_MAX_TRADES
        return True, NT_OK

    # ── sizing ───────────────────────────────────────────────────────────────

    def _sizing(self) -> "tuple[float, float, float]":
        """Return (notional_usd, margin_usd, leverage)."""
        p = self.config.params
        lev = _clamp(float(p["leverage"]), 1.0, float(p["max_leverage"]))
        margin = float(p["max_margin_per_trade_usd"])
        notional = min(margin * lev, float(p["max_position_notional_usd"]))
        return notional, notional / lev, lev

    # ── fill / close callbacks (engine-driven) ───────────────────────────────

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        notional = (self._pending or {}).get("notional", price * size)
        tp_usd = float(p["take_profit_usd"])
        sl_usd = float(p["stop_loss_usd"])
        if side == "BUY":
            tp = price * (1.0 + tp_usd / notional)
            sl = price * (1.0 - sl_usd / notional)
        else:
            tp = price * (1.0 - tp_usd / notional)
            sl = price * (1.0 + sl_usd / notional)
        self._position = {
            "side":        "long" if side == "BUY" else "short",
            "entry_px":    price,
            "notional":    notional,
            "size":        size,
            "opened_at":   ts,
            "pos_id":      pos_id,
            "margin":      (self._pending or {}).get("margin", notional),
            "leverage":    (self._pending or {}).get("leverage", 1.0),
        }
        self._pending = None
        self._trade_ts.append(ts)
        self._alert("POSITION_FILLED", ts)
        self._log_trade_row("ENTRY", self._position, ts, 0.0, "fill")
        return {"tp_price": tp, "stop_price": sl,
                "max_hold_seconds": int(p["max_holding_seconds"])}

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        p = self.config.params
        pos = self._position or {}
        ts = time.time()
        self._daily_pnl += pnl_net
        self._n_trades += 1
        if pnl_net > 0:
            self._wins += 1
            self._consec_losses = 0
        else:
            self._consec_losses += 1
            self._cooldown_until = ts + float(p["cooldown_after_loss_seconds"])
            if self._consec_losses >= int(p["max_consecutive_losses"]):
                self._cooldown_until = ts + float(p["cooldown_after_3_losses_seconds"])
                self._suspended_until = self._cooldown_until
                self._alert("CONSECUTIVE_LOSS_LIMIT_REACHED", ts)
        if self._daily_pnl <= -abs(p["max_daily_loss_usd"]):
            self._disabled_for_day = True
            self._suspended_until = ts + 6 * 3600.0
            self._alert("DAILY_LOSS_LIMIT_REACHED", ts)
        self._log_trade_row("EXIT", pos, ts, pnl_net, exit_reason)
        self._alert(f"EXIT:{exit_reason}", ts)
        self._position = None
        self._pending = None

    # ── logging / alerts ─────────────────────────────────────────────────────

    def _alert(self, kind: str, ts: float) -> None:
        self._last_alert = kind
        if self.config.params.get("enable_alerts", True):
            log.info("[BTC5MIN ALERT] %s @ %.0f", kind,
                     ts if ts else time.time())

    def _log_feature_row(self, ts: float, feat: dict, decision: str) -> None:
        path = self.config.params["feature_log_path"]
        cols = ["ts", "mid", "spread_bps", "obi_10", "flow_60s",
                "rv_300s_bps", "return_60s", "return_180s",
                "z_return_60s", "z_obi_top10", "z_flow_60s",
                "score", "p_up", "model_quality", "decision", "no_trade_reason"]
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            new = not self._feat_log_ready and (
                not os.path.exists(path) or os.path.getsize(path) == 0)
            self._feat_log_ready = True
            with open(path, "a", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(cols)
                w.writerow([
                    f"{ts:.0f}", _r(feat.get("mid"), 2), _r(feat.get("spread_bps"), 3),
                    _r(feat.get("obi_10"), 4), _r(feat.get("flow_60s"), 4),
                    _r(feat.get("rv_300s_bps"), 2), _r(feat.get("return_60s"), 6),
                    _r(feat.get("return_180s"), 6), _r(feat.get("z_return_60s"), 3),
                    _r(feat.get("z_obi_top10"), 3), _r(feat.get("z_flow_60s"), 3),
                    _r(feat.get("score"), 4), _r(feat.get("p_up"), 4),
                    feat.get("model_quality", ""), decision,
                    self._last_no_trade_reason,
                ])
        except Exception as exc:                           # pragma: no cover
            log.debug("btc5min feature log failed: %s", exc)

    def _log_trade_row(self, kind: str, pos: dict, ts: float,
                       pnl_net: float, reason: str) -> None:
        path = self.config.params["trade_log_path"]
        cols = ["ts", "kind", "side", "notional", "margin", "leverage",
                "entry_px", "pnl_net", "reason"]
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            new = not self._trade_log_ready and (
                not os.path.exists(path) or os.path.getsize(path) == 0)
            self._trade_log_ready = True
            with open(path, "a", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(cols)
                w.writerow([
                    f"{ts:.0f}", kind, pos.get("side", ""),
                    _r(pos.get("notional"), 2), _r(pos.get("margin"), 2),
                    _r(pos.get("leverage"), 1), _r(pos.get("entry_px"), 2),
                    _r(pnl_net, 6), reason,
                ])
        except Exception as exc:                           # pragma: no cover
            log.debug("btc5min trade log failed: %s", exc)

    # ── GUI / introspection ──────────────────────────────────────────────────

    def get_calibration_data(self, symbol: str) -> dict:
        f = self._last_features or {}
        p = self.config.params
        pos = self._position
        notional, margin, lev = self._sizing()
        out = {
            "display_name":      self.DISPLAY_NAME,
            "paper_only":        bool(p["paper_only"]),
            "live_enabled":      bool(p["live_enabled"]),
            "model_quality":     self._model_quality(),
            "warmup_seconds":    int(self._warmup_seconds()),
            "warmup_target_s":   int(p["min_warmup_seconds"]),
            "warmup_pct":        round(min(1.0, self._warmup_seconds()
                                           / max(1, p["min_warmup_seconds"])) * 100, 1),
            "p_up":              _r(f.get("p_up"), 4),
            "score":             _r(f.get("score"), 4),
            "decision":          self._last_decision,
            "no_trade_reason":   self._last_no_trade_reason,
            "spread_bps":        _r(f.get("spread_bps"), 3),
            "obi_top10":         _r(f.get("obi_10"), 4),
            "flow_60s":          _r(f.get("flow_60s"), 4),
            "rv_300s_bps":       _r(f.get("rv_300s_bps"), 2),
            "leverage":          lev,
            "notional_usd":      notional,
            "margin_usd":        margin,
            "trades_today":      self._n_trades,
            "daily_pnl":         round(self._daily_pnl, 4),
            "consecutive_losses": self._consec_losses,
            "win_rate":          round(self._wins / self._n_trades, 3) if self._n_trades else None,
            "cooldown_remaining_s": max(0, int(self._cooldown_until - time.time())),
            "disabled_for_day":  self._disabled_for_day,
            "in_position":       pos is not None,
            "last_alert":        self._last_alert,
        }
        if pos is not None:
            out["position"] = {
                "side":       pos["side"],
                "entry_px":   _r(pos["entry_px"], 2),
                "notional":   _r(pos["notional"], 2),
                "hold_s":     int(time.time() - pos["opened_at"]),
            }
        return out

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = 1 if self._position is not None else 0
        d["model_quality"]        = self._model_quality()
        d["daily_pnl"]            = round(self._daily_pnl, 4)
        d["consecutive_losses"]   = self._consec_losses
        d["btc5min_trades"]       = self._n_trades
        return d


# ── small numeric helpers ────────────────────────────────────────────────────

def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _num(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _r(x, n: int):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None
