"""tests/test_sanity_check_engine.py — SanityCheckEngine unit tests."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import pytest

from risk.sanity_check_engine import SanityCheckEngine
from strategies.base_strategy import StrategyDecision


# ── Helpers ────────────────────────────────────────────────────────────


@dataclass
class _Book:
    best_bid: Optional[float]
    best_ask: Optional[float]

    @property
    def mid(self):
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


def _book(bid=100.0, ask=100.10):
    return _Book(best_bid=bid, best_ask=ask)


def _engine_state(**overrides):
    base = {
        "now":                   time.time(),
        "last_book_ts":          time.time(),
        "last_heartbeat_ts":     time.time(),
        "daily_pnl":             0.0,
        "trades_today":          0,
        "trades_this_hour":      0,
        "btc_vol_guard":         False,
        "pending_symbols":       set(),
        "open_position_symbols": set(),
        "allow_multi_position":  True,
        "strategy_states":       {"S": "ACTIVE"},
    }
    base.update(overrides)
    return base


def _decision(action="PLACE_BUY", symbol="BTC",
              notional=20.0, stop_loss=99.0, take_profit=102.0,
              reward_risk_ratio=2.0):
    return StrategyDecision(
        action=action, symbol=symbol,
        notional_usd=notional,
        stop_loss=stop_loss, take_profit=take_profit,
        reward_risk_ratio=reward_risk_ratio,
    )


def _config():
    return {
        "sanity_check": {
            "max_spread_bps": 50.0,
            "min_reward_risk_ratio": 1.2,
            "stale_book_s": 30.0,
            "stale_heartbeat_s": 20.0,
        },
    }


# ── Tests ──────────────────────────────────────────────────────────────


def test_null_decision_blocked():
    sce = SanityCheckEngine()
    ok, code, _ = sce.validate_decision(
        None, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_null_decision"


def test_invalid_action_blocked():
    sce = SanityCheckEngine()
    bad = StrategyDecision(action="BUY_FOREVER", symbol="BTC", notional_usd=10.0)
    ok, code, _ = sce.validate_decision(
        bad, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_invalid_action"


def test_crossed_book_blocked():
    sce = SanityCheckEngine()
    crossed = _Book(best_bid=101.0, best_ask=100.0)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", crossed, _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_crossed_book"


def test_spread_too_wide_blocked():
    sce = SanityCheckEngine()
    # 200 bps spread > 50 bps default
    wide = _Book(best_bid=100.0, best_ask=102.01)
    ok, code, _ = sce.validate_decision(
        _decision(stop_loss=99.0, take_profit=110.0),
        "S", wide, _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_spread_too_wide"


def test_notional_too_large_blocked():
    sce = SanityCheckEngine()
    ok, code, _ = sce.validate_decision(
        _decision(notional=1000.0),
        "S", _book(), _engine_state(),
        _config(),
        strategy_config={"max_position_size_usd": 50.0},
    )
    assert not ok
    assert code == "sanity_notional_too_large"


def test_notional_exceeds_cap_blocked():
    sce = SanityCheckEngine()
    cfg = _config()
    cfg["sanity_check"]["max_order_notional_usd"] = 30.0
    ok, code, _ = sce.validate_decision(
        _decision(notional=40.0),
        "S", _book(), _engine_state(), cfg,
    )
    assert not ok
    assert code == "sanity_notional_exceeds_cap"


def test_missing_stop_blocked():
    sce = SanityCheckEngine()
    d = _decision(stop_loss=None)
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_missing_stop"


def test_missing_tp_blocked():
    sce = SanityCheckEngine()
    d = _decision(take_profit=None)
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_missing_tp"


def test_bad_stop_buy_blocked():
    sce = SanityCheckEngine()
    # BUY: stop must be BELOW entry (≈ ask = 100.10)
    d = _decision(action="PLACE_BUY", stop_loss=110.0, take_profit=120.0)
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_bad_stop_buy"


def test_bad_tp_buy_blocked():
    sce = SanityCheckEngine()
    d = _decision(action="PLACE_BUY", stop_loss=99.0, take_profit=99.5)
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_bad_tp_buy"


def test_bad_stop_sell_blocked():
    sce = SanityCheckEngine()
    d = _decision(action="PLACE_SELL", stop_loss=99.0, take_profit=95.0)
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_bad_stop_sell"


def test_rr_too_low_blocked():
    sce = SanityCheckEngine()
    d = _decision(
        action="PLACE_BUY",
        stop_loss=99.0,        # risk = 1.10 below entry 100.10
        take_profit=100.50,    # reward = 0.40 above entry
        reward_risk_ratio=0.0  # force derivation from prices
    )
    ok, code, _ = sce.validate_decision(
        d, "S", _book(), _engine_state(), _config()
    )
    assert not ok
    assert code == "sanity_rr_too_low"


def test_valid_decision_passes():
    sce = SanityCheckEngine()
    ok, code, details = sce.validate_decision(
        _decision(), "S", _book(), _engine_state(), _config()
    )
    assert ok, f"expected pass, got {code} ({details})"
    assert code == ""


def test_disabled_strategy_blocked():
    sce = SanityCheckEngine()
    es = _engine_state(strategy_states={"S": "DISABLED"})
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, _config()
    )
    assert not ok
    assert code == "sanity_strategy_not_active"


def test_daily_loss_limit_blocked():
    sce = SanityCheckEngine()
    cfg = _config()
    cfg["sanity_check"]["daily_loss_limit_usd"] = 50.0
    es = _engine_state(daily_pnl=-60.0)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, cfg
    )
    assert not ok
    assert code == "sanity_daily_loss_limit"


def test_daily_trade_limit_blocked():
    sce = SanityCheckEngine()
    cfg = _config()
    cfg["sanity_check"]["max_trades_per_day"] = 5
    es = _engine_state(trades_today=5)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, cfg
    )
    assert not ok
    assert code == "sanity_daily_trade_limit"


def test_hourly_trade_limit_blocked():
    sce = SanityCheckEngine()
    cfg = _config()
    cfg["sanity_check"]["max_trades_per_hour"] = 2
    es = _engine_state(trades_this_hour=2)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, cfg
    )
    assert not ok
    assert code == "sanity_hourly_trade_limit"


def test_stale_book_blocked():
    sce = SanityCheckEngine()
    now = time.time()
    es = _engine_state(now=now, last_book_ts=now - 600)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, _config()
    )
    assert not ok
    assert code == "sanity_stale_book"


def test_pending_order_blocks_new_one():
    sce = SanityCheckEngine()
    es = _engine_state(pending_symbols={"BTC"})
    ok, code, _ = sce.validate_decision(
        _decision(symbol="BTC"), "S", _book(), es, _config()
    )
    assert not ok
    assert code == "sanity_pending_order_exists"


def test_existing_position_blocks_when_multi_disabled():
    sce = SanityCheckEngine()
    es = _engine_state(
        open_position_symbols={"BTC"},
        allow_multi_position=False,
    )
    ok, code, _ = sce.validate_decision(
        _decision(symbol="BTC"), "S", _book(), es, _config()
    )
    assert not ok
    assert code == "sanity_existing_position"


def test_close_action_bypasses_directional_checks():
    """CLOSE doesn't need stop/TP/RR/book; only symbol is required."""
    sce = SanityCheckEngine()
    d = StrategyDecision(action="CLOSE", symbol="BTC")
    ok, code, _ = sce.validate_decision(
        d, "S", None, _engine_state(), _config()
    )
    assert ok
    assert code == ""


def test_btc_vol_guard_blocks():
    sce = SanityCheckEngine()
    es = _engine_state(btc_vol_guard=True)
    ok, code, _ = sce.validate_decision(
        _decision(), "S", _book(), es, _config()
    )
    assert not ok
    assert code == "sanity_btc_vol_guard"
