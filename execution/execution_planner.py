"""
execution/execution_planner.py — Translates a StrategyDecision into an
ExecutionPlan that the executor can place.

Responsibilities:
  - Decide MAKER_SIM vs TAKER_SIM (with policy from config).
  - Compute a safe limit price from the book.
  - Set max_pending_seconds per order type.
  - Verify stop/take-profit are present on directional orders.
  - Verify notional is positive.
  - Emergency CLOSE always uses TAKER_SIM.

The planner does NOT place the order — it returns a plan object. The
engine consumes the plan and calls the executor.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    signal_id: str
    strategy: str
    symbol: str
    side: str                   # "BUY" / "SELL"
    order_type: str             # "MAKER_SIM" | "TAKER_SIM"
    limit_price: float
    notional_usd: float
    stop_loss: float
    take_profit: float
    max_pending_s: int          # 20 for taker, 60 for maker (defaults)
    max_reprice_attempts: int = 1
    planned_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class ExecutionPlanner:
    """
    Order-routing policy:
      - Default order_type = MAKER_SIM (safer, lower cost in paper).
      - Switch to TAKER_SIM when:
            decision.expected_edge_bps > taker_threshold_ratio *
                                          decision.estimated_cost_bps
        AND book spread is reasonably tight.
      - Emergency CLOSE: always TAKER_SIM.

    All thresholds come from the `execution_planner` config section,
    falling back to safe paper defaults if missing.
    """

    # Sensible defaults — `taker_min_edge_ratio = 3` means
    # "switch to TAKER only if expected edge is 3× the round-trip cost".
    _DEFAULT_TAKER_MIN_EDGE_RATIO = 3.0
    _DEFAULT_TAKER_MAX_SPREAD_BPS = 10.0
    _DEFAULT_MAKER_PRICE_OFFSET_BPS = 1.0   # 1 bp inside best bid/ask
    _DEFAULT_MAX_PENDING_TAKER_S    = 20
    _DEFAULT_MAX_PENDING_MAKER_S    = 60
    _DEFAULT_MAX_REPRICE_ATTEMPTS   = 1

    def __init__(self, config: Optional[dict] = None) -> None:
        ep = (config or {}).get("execution_planner", {}) or {}
        paper_sim = (config or {}).get("paper_sim", {}) or {}

        self._taker_min_edge_ratio = float(
            ep.get("taker_min_edge_ratio", self._DEFAULT_TAKER_MIN_EDGE_RATIO)
        )
        self._taker_max_spread_bps = float(
            ep.get("taker_max_spread_bps", self._DEFAULT_TAKER_MAX_SPREAD_BPS)
        )
        self._maker_price_offset_bps = float(
            ep.get("maker_price_offset_bps", self._DEFAULT_MAKER_PRICE_OFFSET_BPS)
        )
        # paper_sim section is preferred (matches HighFreqExecutor config)
        self._taker_expire_s = int(
            paper_sim.get("taker_expire_s",
                          ep.get("max_pending_taker_s",
                                 self._DEFAULT_MAX_PENDING_TAKER_S))
        )
        self._maker_expire_s = int(
            paper_sim.get("maker_expire_s",
                          ep.get("max_pending_maker_s",
                                 self._DEFAULT_MAX_PENDING_MAKER_S))
        )
        self._max_reprice = int(
            paper_sim.get("max_reprice_attempts",
                          ep.get("max_reprice_attempts",
                                 self._DEFAULT_MAX_REPRICE_ATTEMPTS))
        )

    # ------------------------------------------------------------------

    def plan(self, decision, book, *, is_emergency_close: bool = False) -> ExecutionPlan:
        """
        Build an ExecutionPlan from a StrategyDecision + current book.
        Raises ValueError on missing data; the engine should catch and
        skip the order in that case.
        """
        action = getattr(decision, "action", "")
        sym = getattr(decision, "symbol", "")

        if not action or not sym:
            raise ValueError(f"plan: missing action/symbol on decision")

        if action not in ("PLACE_BUY", "PLACE_SELL", "PLACE_QUOTES", "CLOSE"):
            raise ValueError(f"plan: unsupported action {action!r}")

        # Determine side
        if action == "PLACE_BUY":
            side = "BUY"
        elif action == "PLACE_SELL":
            side = "SELL"
        elif action == "PLACE_QUOTES":
            # PLACE_QUOTES is dual-sided market making → MAKER_SIM only
            side = "BUY"  # placeholder; engine handles both legs
        else:  # CLOSE
            side = "CLOSE"

        # Directional: require stop + TP
        if action in ("PLACE_BUY", "PLACE_SELL"):
            stop = getattr(decision, "stop_loss", None)
            tp   = getattr(decision, "take_profit", None)
            if stop is None:
                raise ValueError(f"plan: missing stop_loss for {action} {sym}")
            if tp is None:
                raise ValueError(f"plan: missing take_profit for {action} {sym}")
        else:
            stop = getattr(decision, "stop_loss", None) or 0.0
            tp   = getattr(decision, "take_profit", None) or 0.0

        notional = float(getattr(decision, "notional_usd", 0.0) or 0.0)
        if notional <= 0:
            raise ValueError(f"plan: non-positive notional {notional} for {sym}")

        # Book sanity
        best_bid = getattr(book, "best_bid", None)
        best_ask = getattr(book, "best_ask", None)
        mid      = getattr(book, "mid", None)
        if not best_bid or not best_ask or not mid or best_bid >= best_ask:
            raise ValueError(f"plan: bad book for {sym} bid={best_bid} ask={best_ask}")

        spread_bps = (best_ask - best_bid) / mid * 10_000.0

        # Choose order type
        if is_emergency_close or action == "CLOSE":
            order_type = "TAKER_SIM"
        else:
            order_type = self._select_order_type(decision, spread_bps)

        # Limit price
        limit_price = self._compute_limit_price(
            action=action, side=side, order_type=order_type,
            best_bid=best_bid, best_ask=best_ask, mid=mid,
            decision=decision,
        )

        # max_pending_s
        if order_type == "TAKER_SIM":
            max_pending_s = self._taker_expire_s
        else:
            max_pending_s = self._maker_expire_s

        # Trace
        signal_id = getattr(decision, "signal_id", "") or ""

        plan = ExecutionPlan(
            signal_id=signal_id,
            strategy=getattr(decision, "metadata", {}).get("strategy", ""),
            symbol=sym,
            side=side,
            order_type=order_type,
            limit_price=float(limit_price),
            notional_usd=notional,
            stop_loss=float(stop),
            take_profit=float(tp),
            max_pending_s=int(max_pending_s),
            max_reprice_attempts=int(self._max_reprice),
            planned_at=time.time(),
            metadata={
                "spread_bps":        round(spread_bps, 2),
                "expected_edge_bps": float(getattr(decision, "expected_edge_bps", 0.0) or 0.0),
                "estimated_cost_bps": float(getattr(decision, "estimated_cost_bps", 0.0) or 0.0),
                "reward_risk_ratio": float(getattr(decision, "reward_risk_ratio", 0.0) or 0.0),
            },
        )
        log.debug("[PLAN] %s %s %s notional=%.2f type=%s limit=%.6g pending=%ds",
                  plan.signal_id, plan.symbol, plan.side,
                  plan.notional_usd, plan.order_type,
                  plan.limit_price, plan.max_pending_s)
        return plan

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_order_type(self, decision, spread_bps: float) -> str:
        """Default MAKER; switch to TAKER only with high edge + tight spread."""
        # Allow strategy override
        forced = (getattr(decision, "order_type", "") or "").upper()
        if forced == "MAKER_SIM":
            return "MAKER_SIM"
        if forced == "TAKER_SIM":
            return "TAKER_SIM"

        edge = float(getattr(decision, "expected_edge_bps", 0.0) or 0.0)
        cost = float(getattr(decision, "estimated_cost_bps", 0.0) or 0.0)

        if (cost > 0
                and edge > self._taker_min_edge_ratio * cost
                and spread_bps < self._taker_max_spread_bps):
            return "TAKER_SIM"

        return "MAKER_SIM"

    def _compute_limit_price(self, *, action: str, side: str, order_type: str,
                              best_bid: float, best_ask: float, mid: float,
                              decision) -> float:
        if action == "CLOSE":
            # TAKER close — cross the spread
            return best_bid if side != "BUY" else best_ask

        if action == "PLACE_QUOTES":
            # Engine handles both legs; return a non-zero sentinel.
            return mid

        # Strategy-provided prices, when available, take precedence.
        bp = getattr(decision, "buy_price",  None)
        sp = getattr(decision, "sell_price", None)
        if action == "PLACE_BUY" and bp:
            return float(bp)
        if action == "PLACE_SELL" and sp:
            return float(sp)

        # Otherwise compute from book + order_type.
        offset_frac = self._maker_price_offset_bps / 10_000.0
        if order_type == "MAKER_SIM":
            if action == "PLACE_BUY":
                return best_bid * (1.0 + offset_frac)
            return best_ask * (1.0 - offset_frac)
        # TAKER: cross the spread
        if action == "PLACE_BUY":
            return best_ask
        return best_bid
