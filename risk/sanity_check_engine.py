"""
risk/sanity_check_engine.py — First-line sanity gate on every StrategyDecision.

Sits BEFORE the per-strategy ledger gate in the decision pipeline.
Its job is to reject decisions that violate basic invariants the rest of
the pipeline assumes:

  - decision object well-formed (action, symbol, notional)
  - orderbook present, non-crossed, reasonable spread
  - stop/TP present on directional orders, on the correct side of entry
  - reward/risk ratio above the configured floor
  - notional within absolute caps and per-strategy slot
  - strategy currently ACTIVE (not killed/suspended/disabled)
  - no duplicate pending order on the symbol
  - market data fresh (book + heartbeat) — stale data → block
  - daily loss / daily trade / hourly trade caps respected
  - BTC vol guard inactive

The class is intentionally pure — no I/O, no orderbook mutation, no
ledger updates. The engine consumes the rejection reason and emits a
risk event.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ── Default thresholds (overridable via the `config` dict) ────────────
_DEFAULT_MAX_SPREAD_BPS    = 50.0
_DEFAULT_MIN_RR            = 1.2
_DEFAULT_MIN_NET_PROFIT    = 0.0
_DEFAULT_STALE_BOOK_S      = 30.0
_DEFAULT_STALE_HB_S        = 20.0

_VALID_ACTIONS = frozenset({
    "PLACE_BUY", "PLACE_SELL", "PLACE_QUOTES",
    "CANCEL_QUOTES", "CLOSE", "SKIP",
})

# Directional actions require a stop and a take-profit
_DIRECTIONAL_ACTIONS = frozenset({"PLACE_BUY", "PLACE_SELL"})


@dataclass
class _Thresholds:
    """Resolved threshold set for a single validate_decision() call."""
    max_spread_bps:                  float
    min_rr:                          float
    min_expected_net_profit_usd:     float
    max_order_notional_usd:          Optional[float]
    max_position_size_usd:           float
    stale_book_s:                    float
    stale_heartbeat_s:               float
    daily_loss_limit_usd:            Optional[float]
    max_trades_per_day:              Optional[int]
    max_trades_per_hour:             Optional[int]


def _resolve_thresholds(config: dict, strategy_cfg: Optional[dict]) -> _Thresholds:
    sc = (config or {}).get("sanity_check", {}) or {}
    risk = (config or {}).get("risk", {}) or {}
    strat = strategy_cfg or {}

    return _Thresholds(
        max_spread_bps=float(sc.get("max_spread_bps", _DEFAULT_MAX_SPREAD_BPS)),
        min_rr=float(sc.get("min_reward_risk_ratio", _DEFAULT_MIN_RR)),
        min_expected_net_profit_usd=float(
            sc.get("min_expected_net_profit_usd", _DEFAULT_MIN_NET_PROFIT)
        ),
        max_order_notional_usd=(
            float(sc["max_order_notional_usd"])
            if "max_order_notional_usd" in sc
            else (float(config["max_order_notional_usd"])
                  if isinstance(config, dict) and "max_order_notional_usd" in config
                  else None)
        ),
        max_position_size_usd=float(strat.get("max_position_size_usd", 1e18)),
        stale_book_s=float(sc.get("stale_book_s", _DEFAULT_STALE_BOOK_S)),
        stale_heartbeat_s=float(sc.get("stale_heartbeat_s", _DEFAULT_STALE_HB_S)),
        daily_loss_limit_usd=(
            float(sc["daily_loss_limit_usd"])
            if "daily_loss_limit_usd" in sc else None
        ),
        max_trades_per_day=(
            int(sc["max_trades_per_day"])
            if "max_trades_per_day" in sc
            else (int(risk["max_trades_per_day"])
                  if "max_trades_per_day" in risk else None)
        ),
        max_trades_per_hour=(
            int(sc["max_trades_per_hour"])
            if "max_trades_per_hour" in sc
            else (int(risk["max_trades_per_hour"])
                  if "max_trades_per_hour" in risk else None)
        ),
    )


class SanityCheckEngine:
    """
    Stateless validator. One instance can be shared across all strategies.

    Usage:
        engine = SanityCheckEngine()
        ok, code, details = engine.validate_decision(
            decision, strategy_name, book, engine_state, config,
        )
    """

    def __init__(self,
                 strategy_states: Optional[dict] = None) -> None:
        """
        strategy_states — optional dict {strategy_name: "ACTIVE"/"SUSPENDED"/...}
                          used to block decisions for inactive strategies.
                          If None, the validator assumes ACTIVE.
        """
        self._strategy_states = strategy_states or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_decision(
        self,
        decision,                # StrategyDecision (or None)
        strategy_name: str,
        book,                    # OrderBook (or None)
        engine_state: dict,
        config: dict,
        strategy_config: Optional[dict] = None,
    ) -> "tuple[bool, str, dict]":
        """
        Returns (ok, reason_code, details_dict).
        ok=True  → all checks passed.
        ok=False → reason_code is one of "sanity_*", details_dict contains
                    the offending values.

        engine_state is a dict with optional keys:
          - last_book_ts:    float — most-recent book timestamp (any symbol)
          - last_heartbeat_ts: float — last WS heartbeat ts
          - daily_pnl:       float — realised daily PnL (signed)
          - trades_today:    int
          - trades_this_hour: int
          - btc_vol_guard:   bool — True if KillSwitch volguard is active
          - pending_symbols: set[str] — symbols with pending orders
          - open_position_symbols: set[str] — symbols with open positions
          - allow_multi_position: bool — defaults False
        """
        # 1. decision well-formed -----------------------------------------------
        if decision is None:
            return False, "sanity_null_decision", {}

        action = getattr(decision, "action", None)
        if action not in _VALID_ACTIONS:
            return False, "sanity_invalid_action", {"action": action}

        # CANCEL/CLOSE/SKIP are administrative — they bypass most other
        # sanity checks because they remove exposure rather than add it.
        if action in ("CANCEL_QUOTES", "CLOSE", "SKIP"):
            sym = getattr(decision, "symbol", "")
            if not sym:
                return False, "sanity_missing_symbol", {"action": action}
            return True, "", {"administrative": True}

        sym = getattr(decision, "symbol", "")
        if not sym:
            return False, "sanity_missing_symbol", {}

        # 2. orderbook sanity ---------------------------------------------------
        if book is None:
            return False, "sanity_no_book", {"symbol": sym}

        best_bid = getattr(book, "best_bid", None)
        best_ask = getattr(book, "best_ask", None)
        mid      = getattr(book, "mid", None)
        if best_bid is None or best_ask is None:
            return False, "sanity_bad_book", {
                "symbol": sym, "best_bid": best_bid, "best_ask": best_ask,
            }
        if best_bid >= best_ask:
            return False, "sanity_crossed_book", {
                "symbol": sym, "best_bid": best_bid, "best_ask": best_ask,
            }
        if mid is None or mid <= 0:
            return False, "sanity_zero_mid", {"symbol": sym, "mid": mid}

        thr = _resolve_thresholds(config, strategy_config)

        spread_bps = (best_ask - best_bid) / mid * 10_000.0
        if spread_bps > thr.max_spread_bps:
            return False, "sanity_spread_too_wide", {
                "symbol": sym, "spread_bps": round(spread_bps, 2),
                "max_spread_bps": thr.max_spread_bps,
            }

        # 3. notional sanity ----------------------------------------------------
        notional = getattr(decision, "notional_usd", None) or 0.0
        if notional <= 0:
            return False, "sanity_zero_notional", {"symbol": sym, "notional": notional}
        if notional > thr.max_position_size_usd:
            return False, "sanity_notional_too_large", {
                "symbol": sym, "notional": notional,
                "max_position_size_usd": thr.max_position_size_usd,
            }
        if (thr.max_order_notional_usd is not None
                and notional > thr.max_order_notional_usd):
            return False, "sanity_notional_exceeds_cap", {
                "symbol": sym, "notional": notional,
                "max_order_notional_usd": thr.max_order_notional_usd,
            }

        # 4. directional-only checks: stop, TP, RR, expected profit -------------
        if action in _DIRECTIONAL_ACTIONS:
            stop = getattr(decision, "stop_loss", None)
            tp   = getattr(decision, "take_profit", None)

            if stop is None:
                return False, "sanity_missing_stop", {
                    "symbol": sym, "action": action,
                }
            if tp is None:
                return False, "sanity_missing_tp", {
                    "symbol": sym, "action": action,
                }

            # Reference entry price: use the side that we would lift.
            entry = best_ask if action == "PLACE_BUY" else best_bid

            if action == "PLACE_BUY":
                if stop >= entry:
                    return False, "sanity_bad_stop_buy", {
                        "symbol": sym, "stop_loss": stop, "entry": entry,
                    }
                if tp <= entry:
                    return False, "sanity_bad_tp_buy", {
                        "symbol": sym, "take_profit": tp, "entry": entry,
                    }
            else:  # PLACE_SELL
                if stop <= entry:
                    return False, "sanity_bad_stop_sell", {
                        "symbol": sym, "stop_loss": stop, "entry": entry,
                    }
                if tp >= entry:
                    return False, "sanity_bad_tp_sell", {
                        "symbol": sym, "take_profit": tp, "entry": entry,
                    }

            # 5. reward/risk ratio -----------------------------------------
            rr = getattr(decision, "reward_risk_ratio", 0.0) or 0.0
            if rr <= 0.0:
                # Try to derive from entry/tp/sl distances when strategy
                # didn't fill the field.
                if action == "PLACE_BUY":
                    reward = max(tp - entry, 0.0)
                    risk   = max(entry - stop, 1e-9)
                else:
                    reward = max(entry - tp, 0.0)
                    risk   = max(stop - entry, 1e-9)
                rr = reward / risk
            if rr < thr.min_rr:
                return False, "sanity_rr_too_low", {
                    "symbol": sym, "rr": round(rr, 3),
                    "min_rr": thr.min_rr,
                }

            # 6. expected net profit floor ---------------------------------
            net = getattr(decision, "expected_net_profit_usd", 0.0) or 0.0
            if (thr.min_expected_net_profit_usd > 0
                    and net > 0
                    and net < thr.min_expected_net_profit_usd):
                return False, "sanity_net_profit_too_low", {
                    "symbol": sym, "expected_net_profit_usd": round(net, 4),
                    "min_expected_net_profit_usd": thr.min_expected_net_profit_usd,
                }

        # 7. strategy state -----------------------------------------------------
        state = (engine_state.get("strategy_states") or self._strategy_states
                 ).get(strategy_name, "ACTIVE")
        if state not in ("ACTIVE", "active", "Active"):
            return False, "sanity_strategy_not_active", {
                "strategy": strategy_name, "state": state,
            }

        # 8. duplicates ---------------------------------------------------------
        pending = engine_state.get("pending_symbols") or set()
        if sym in pending:
            return False, "sanity_pending_order_exists", {"symbol": sym}

        if not engine_state.get("allow_multi_position", False):
            open_syms = engine_state.get("open_position_symbols") or set()
            if sym in open_syms:
                return False, "sanity_existing_position", {"symbol": sym}

        # 9. data freshness -----------------------------------------------------
        now = engine_state.get("now", time.time())
        last_book_ts = engine_state.get("last_book_ts")
        if last_book_ts is not None and (now - last_book_ts) > thr.stale_book_s:
            return False, "sanity_stale_book", {
                "last_book_ts": last_book_ts, "now": now,
                "age_s": round(now - last_book_ts, 1),
                "limit_s": thr.stale_book_s,
            }

        last_hb_ts = engine_state.get("last_heartbeat_ts")
        if last_hb_ts is not None and (now - last_hb_ts) > thr.stale_heartbeat_s:
            return False, "sanity_stale_heartbeat", {
                "last_heartbeat_ts": last_hb_ts, "now": now,
                "age_s": round(now - last_hb_ts, 1),
                "limit_s": thr.stale_heartbeat_s,
            }

        # 10. daily loss / trade caps ------------------------------------------
        if thr.daily_loss_limit_usd is not None:
            daily_pnl = float(engine_state.get("daily_pnl", 0.0))
            if daily_pnl < -abs(thr.daily_loss_limit_usd):
                return False, "sanity_daily_loss_limit", {
                    "daily_pnl": round(daily_pnl, 4),
                    "limit_usd": thr.daily_loss_limit_usd,
                }

        if thr.max_trades_per_day is not None:
            trades_today = int(engine_state.get("trades_today", 0))
            if trades_today >= thr.max_trades_per_day:
                return False, "sanity_daily_trade_limit", {
                    "trades_today": trades_today,
                    "max": thr.max_trades_per_day,
                }

        if thr.max_trades_per_hour is not None:
            trades_h = int(engine_state.get("trades_this_hour", 0))
            if trades_h >= thr.max_trades_per_hour:
                return False, "sanity_hourly_trade_limit", {
                    "trades_this_hour": trades_h,
                    "max": thr.max_trades_per_hour,
                }

        # 11. BTC vol guard -----------------------------------------------------
        if engine_state.get("btc_vol_guard"):
            return False, "sanity_btc_vol_guard", {}

        return True, "", {
            "spread_bps": round(spread_bps, 2),
            "notional": notional,
            "rr": getattr(decision, "reward_risk_ratio", 0.0) or 0.0,
        }
