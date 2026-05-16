"""
regime_controller.py — Detect market regimes from seconds features and
emit BOUNDED parameter adjustments for strategies.

Design principles
-----------------
1. The controller NEVER increases the notional. It can only reduce it
   (multiplier ∈ [min_notional_multiplier, 1.0]). The user must enable
   `allow_notional_amplification` explicitly to lift the cap.
2. Adjustments expire (`ttl_seconds`) and revert on expiry — the engine
   handles revert in its control loop (same mechanism as manual params).
3. Adjustments are logged to `logs/regime_adaptations.csv` so every
   change is auditable.
4. Bounded params only: `take_profit_pct`, `stop_loss_pct`,
   `max_hold_seconds`, `cooldown_s`, `cooldown_seconds`,
   `imbalance_entry_threshold`, `spread_bps_max`,
   `min_persistence_updates`, `notional_usd` (reduction only by default).

Regimes (kept deliberately simple)
----------------------------------
- LOW_VOL_RANGE        — small rv, tight spread, OFI near 0
- HIGH_VOL_TREND       — high rv with directional drift
- HIGH_VOL_CHAOTIC     — high rv, no direction, |OFI| flips
- BTC_CRASH            — BTC down > X% on 5m
- ILLIQUID_WIDE_SPREAD — spread above per-coin cap
- TOXIC_FLOW           — toxicity_score high
- NORMAL               — none of the above
"""
from __future__ import annotations

import csv
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Module-level safety bounds — strategies can override on a per-param basis.
_BOUNDS = {
    "take_profit_pct":   (0.0001, 0.05),       # 1 bps - 500 bps
    "take_profit_bps":   (1.0, 500.0),
    "stop_loss_pct":     (0.0001, 0.05),
    "stop_loss_bps":     (1.0, 500.0),
    "max_hold_seconds":  (10, 7200),
    "max_hold_hours":    (1, 48),
    "cooldown_s":        (5, 1800),
    "cooldown_seconds":  (5, 1800),
    "imbalance_entry_threshold": (0.05, 0.90),
    "spread_bps_max":    (1.0, 50.0),          # adjustment cannot RAISE above 50bps
    "min_persistence_updates": (1, 10),
    "notional_usd":      (1.0, 1_000.0),
}

_ADAPTABLE_PARAMS = set(_BOUNDS.keys())


@dataclass
class RegimeSnapshot:
    symbol: str
    regime: str
    confidence: float
    realized_vol_bps: float
    spread_bps: float
    liquidity_score: float
    toxicity_score: float
    btc_context: dict
    timestamp: float


@dataclass
class ParameterAdjustment:
    strategy: str
    symbol: str
    param_name: str
    old_value: float
    new_value: float
    reason: str
    regime: str
    confidence: float
    expires_at: float


@dataclass
class RegimeAdjustmentStats:
    total_evaluated: int = 0
    total_applied: int = 0
    total_rejected_bounds: int = 0
    by_regime: dict = field(default_factory=dict)


class RegimeController:

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.snapshot_interval_s = float(cfg.get("snapshot_interval_s", 30.0))
        # Multipliers strictly clip notional changes.
        self.max_notional_multiplier = float(cfg.get("max_notional_multiplier", 1.0))
        self.min_notional_multiplier = float(cfg.get("min_notional_multiplier", 0.25))
        self.allow_notional_amplification = bool(
            cfg.get("allow_notional_amplification", False))
        if not self.allow_notional_amplification:
            self.max_notional_multiplier = min(self.max_notional_multiplier, 1.0)
        self.log_path = Path(cfg.get("log_path", "logs/regime_adaptations.csv"))
        self.allowed_regimes = set(cfg.get(
            "adjust_only_in_regimes",
            ["LOW_VOL_RANGE", "HIGH_VOL_TREND", "HIGH_VOL_CHAOTIC",
             "BTC_CRASH", "TOXIC_FLOW", "ILLIQUID_WIDE_SPREAD", "NORMAL"],
        ))
        self.stats = RegimeAdjustmentStats()
        self._snapshots_by_symbol: dict[str, RegimeSnapshot] = {}

    # ----------------------------------------------------------------
    # Regime detection
    # ----------------------------------------------------------------

    def detect_regime(self, features: dict,
                      btc_context: Optional[dict] = None,
                      max_spread_for_symbol: float = 10.0) -> RegimeSnapshot:
        """Return a RegimeSnapshot from a SecondsFeatureEngine snapshot."""
        sym = (features.get("symbol") or "").upper()
        ts = float(features.get("ts", time.time()))
        spread = features.get("spread_bps")
        spread = float(spread) if (spread is not None and math.isfinite(spread)) else float("nan")
        rv60 = features.get("rv_60s")
        rv_bps = (abs(float(rv60)) * 10_000.0
                  if (rv60 is not None and math.isfinite(rv60)) else float("nan"))
        ofi30 = features.get("ofi_30s") or 0.0
        ofi60 = features.get("ofi_60s") or 0.0
        liq = features.get("liquidity_score") or 0.5
        tox = features.get("toxicity_score") or 0.0
        btc_context = btc_context or {}
        btc_r5m = float(btc_context.get("r_5m_pct") or 0.0)

        # Rules — first match wins.
        regime = "NORMAL"
        confidence = 0.6
        if btc_r5m <= -2.0:
            regime = "BTC_CRASH"
            confidence = min(1.0, abs(btc_r5m) / 5.0)
        elif math.isfinite(spread) and spread > max_spread_for_symbol:
            regime = "ILLIQUID_WIDE_SPREAD"
            confidence = min(1.0, spread / (max_spread_for_symbol * 2.0))
        elif tox > 0.75:
            regime = "TOXIC_FLOW"
            confidence = tox
        elif math.isfinite(rv_bps) and rv_bps > 40.0:
            # High vol — trending if OFI is persistently same sign, else chaotic
            if abs(ofi30) > 0.30 and abs(ofi60) > 0.20 and ofi30 * ofi60 > 0:
                regime = "HIGH_VOL_TREND"
            else:
                regime = "HIGH_VOL_CHAOTIC"
            confidence = min(1.0, rv_bps / 80.0)
        elif math.isfinite(rv_bps) and rv_bps < 10.0 and abs(ofi30) < 0.15:
            regime = "LOW_VOL_RANGE"
            confidence = 0.7

        snap = RegimeSnapshot(
            symbol=sym, regime=regime, confidence=confidence,
            realized_vol_bps=rv_bps if math.isfinite(rv_bps) else 0.0,
            spread_bps=spread if math.isfinite(spread) else 0.0,
            liquidity_score=float(liq), toxicity_score=float(tox),
            btc_context=btc_context, timestamp=ts,
        )
        self._snapshots_by_symbol[sym] = snap
        self.stats.by_regime[regime] = self.stats.by_regime.get(regime, 0) + 1
        return snap

    def get_snapshot(self, symbol: str) -> Optional[RegimeSnapshot]:
        return self._snapshots_by_symbol.get((symbol or "").upper())

    # ----------------------------------------------------------------
    # Bounded parameter adjustment
    # ----------------------------------------------------------------

    def propose_adjustments(self, strategy_name: str, symbol: str,
                            snap: RegimeSnapshot,
                            current_params: dict,
                            ttl_seconds: float = 600.0) -> list[ParameterAdjustment]:
        """Return a list of ParameterAdjustment respecting bounds."""
        if not self.enabled:
            return []
        if snap.regime not in self.allowed_regimes:
            return []

        proposals: list[tuple[str, float, str]] = []  # (param, new, reason)
        r = snap.regime

        if r == "HIGH_VOL_CHAOTIC":
            # Halve notional, double cooldown.
            if "notional_usd" in current_params:
                proposals.append(("notional_usd",
                                  float(current_params["notional_usd"]) * 0.5,
                                  "chaotic_vol_size_down"))
            if "cooldown_s" in current_params:
                proposals.append(("cooldown_s",
                                  float(current_params["cooldown_s"]) * 2.0,
                                  "chaotic_vol_cooldown_up"))
            if "cooldown_seconds" in current_params:
                proposals.append(("cooldown_seconds",
                                  float(current_params["cooldown_seconds"]) * 2.0,
                                  "chaotic_vol_cooldown_up"))

        elif r == "BTC_CRASH":
            # Reduce notional aggressively + lengthen cooldown.
            if "notional_usd" in current_params:
                proposals.append(("notional_usd",
                                  float(current_params["notional_usd"]) * 0.4,
                                  "btc_crash_size_down"))
            if "cooldown_s" in current_params:
                proposals.append(("cooldown_s",
                                  float(current_params["cooldown_s"]) * 2.0,
                                  "btc_crash_cooldown_up"))

        elif r == "TOXIC_FLOW":
            # Skip — strategy should be paused upstream by MQG.
            # We just shrink notional in case any trade leaks through.
            if "notional_usd" in current_params:
                proposals.append(("notional_usd",
                                  float(current_params["notional_usd"]) * 0.5,
                                  "toxic_flow_size_down"))

        elif r == "ILLIQUID_WIDE_SPREAD":
            # Tighten the persistence requirement for scalpers (require
            # more conviction before trading wide-spread markets).
            if "min_persistence_updates" in current_params:
                proposals.append(("min_persistence_updates",
                                  float(current_params["min_persistence_updates"]) + 1,
                                  "wide_spread_persist_up"))

        elif r == "HIGH_VOL_TREND":
            # Allow slightly longer holds for trend strategies.
            if "max_hold_seconds" in current_params:
                proposals.append(("max_hold_seconds",
                                  float(current_params["max_hold_seconds"]) * 1.5,
                                  "trend_hold_extend"))

        elif r == "LOW_VOL_RANGE":
            # Tighter TP/SL for mean-reversion is fine.
            if "take_profit_bps" in current_params:
                proposals.append(("take_profit_bps",
                                  float(current_params["take_profit_bps"]) * 0.8,
                                  "low_vol_tp_tighten"))

        # Apply bounds + notional multiplier cap.
        out: list[ParameterAdjustment] = []
        for param, new_val, reason in proposals:
            self.stats.total_evaluated += 1
            old_val = current_params.get(param)
            if old_val is None:
                continue
            # Notional special-case: clamp the multiplier
            if param == "notional_usd":
                mult = new_val / max(float(old_val), 1e-9)
                if not self.allow_notional_amplification and mult > 1.0:
                    self.stats.total_rejected_bounds += 1
                    continue
                mult = max(self.min_notional_multiplier,
                           min(mult, self.max_notional_multiplier))
                new_val = float(old_val) * mult
            # Generic bounds
            lo, hi = _BOUNDS.get(param, (None, None))
            if lo is not None and new_val < lo:
                new_val = lo
            if hi is not None and new_val > hi:
                new_val = hi
            # Sanity: skip if no change after clamping
            if abs(new_val - float(old_val)) < 1e-9:
                continue
            adj = ParameterAdjustment(
                strategy=strategy_name, symbol=symbol,
                param_name=param,
                old_value=float(old_val), new_value=float(new_val),
                reason=reason, regime=snap.regime,
                confidence=snap.confidence,
                expires_at=time.time() + ttl_seconds,
            )
            out.append(adj)
            self.stats.total_applied += 1
            self._log(adj)
        return out

    # ----------------------------------------------------------------

    def _log(self, adj: ParameterAdjustment) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.log_path.exists()
            with open(self.log_path, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new_file:
                    w.writerow(["ts", "strategy", "symbol", "regime",
                                "confidence", "param_name",
                                "old_value", "new_value", "reason",
                                "expires_at"])
                w.writerow([
                    f"{time.time():.3f}", adj.strategy, adj.symbol, adj.regime,
                    f"{adj.confidence:.3f}", adj.param_name,
                    adj.old_value, adj.new_value, adj.reason, adj.expires_at,
                ])
        except Exception as e:
            log.debug("regime log write failed: %s", e)

    def reset_stats(self) -> None:
        self.stats = RegimeAdjustmentStats()
