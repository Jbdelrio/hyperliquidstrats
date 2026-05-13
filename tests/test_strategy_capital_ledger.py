"""
tests/test_strategy_capital_ledger.py — Unit tests for StrategyCapitalLedger.

Run with:  python -m pytest tests/test_strategy_capital_ledger.py -v
"""
import time
import sys
import os

# Allow running from repo root without install
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from risk.strategy_capital_ledger import (
    StrategyCapitalLedger,
    _DAILY_DD_SUSPEND_PCT,
    _TOTAL_DD_KILL_PCT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_ledger(risk_log: str = "logs/risk_events_test.csv") -> StrategyCapitalLedger:
    ledger = StrategyCapitalLedger(risk_log_path=risk_log)
    ledger.register_strategy("MomentumLS", 500.0)
    return ledger


# ---------------------------------------------------------------------------
# 1. Budget enforcement — cannot open 3×$200 with $500 capital
# ---------------------------------------------------------------------------

def test_budget_blocks_third_position():
    ledger = fresh_ledger()
    # Open 2 positions of $200 — should succeed
    ok1, _ = ledger.can_open("MomentumLS", 200.0)
    assert ok1
    ledger.reserve_notional("MomentumLS", 200.0)
    ledger.register_open("MomentumLS", 200.0)

    ok2, _ = ledger.can_open("MomentumLS", 200.0)
    assert ok2
    ledger.reserve_notional("MomentumLS", 200.0)
    ledger.register_open("MomentumLS", 200.0)

    # 3rd $200 would exceed $500 budget → blocked
    ok3, reason = ledger.can_open("MomentumLS", 200.0)
    assert not ok3
    assert "budget_exceeded" in reason


# ---------------------------------------------------------------------------
# 2. max_positions=2, max_pos_size=125 → max 2×$125 = $250 used
# ---------------------------------------------------------------------------

def test_sizing_respects_slot_cap():
    ledger = fresh_ledger()
    # Each slot = 500/2 = 250, hard_cap = 125 → each order = $125
    # Open two $125 slots
    ledger.reserve_notional("MomentumLS", 125.0)
    ledger.register_open("MomentumLS", 125.0)
    ledger.reserve_notional("MomentumLS", 125.0)
    ledger.register_open("MomentumLS", 125.0)

    # Total open = 250, remaining = 250 — can open another if budget allows
    ok, _ = ledger.can_open("MomentumLS", 50.0)
    assert ok   # 250 + 50 = 300 < 500

    # But can't open $300 more (250+300 > 500)
    ok2, reason = ledger.can_open("MomentumLS", 300.0)
    assert not ok2
    assert "budget_exceeded" in reason


# ---------------------------------------------------------------------------
# 3. reserve_notional → fill → register_open transitions correctly
# ---------------------------------------------------------------------------

def test_reserve_to_open_transition():
    ledger = fresh_ledger()
    ledger.reserve_notional("MomentumLS", 100.0)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["reserved_notional"] == 100.0
    assert s["open_notional"]     == 0.0
    assert s["available_capital"] == 400.0   # 500 - 100

    # Fill confirmed → promote reserved → open
    ledger.register_open("MomentumLS", 100.0)

    s2 = ledger.get_strategy_status("MomentumLS")
    assert s2["reserved_notional"] == 0.0
    assert s2["open_notional"]     == 100.0
    assert s2["available_capital"] == 400.0   # still 400 (open = used)


# ---------------------------------------------------------------------------
# 4. close frees open_notional and records PnL
# ---------------------------------------------------------------------------

def test_close_frees_notional_and_pnl():
    ledger = fresh_ledger()
    ledger.reserve_notional("MomentumLS", 100.0)
    ledger.register_open("MomentumLS", 100.0)

    pnl = 1.50
    ledger.register_close("MomentumLS", 100.0, pnl)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["open_notional"]   == 0.0
    assert s["realized_pnl"]    == pytest.approx(pnl, abs=1e-6)
    assert s["available_capital"] == 500.0   # all free


# ---------------------------------------------------------------------------
# 5. Daily DD suspension
# ---------------------------------------------------------------------------

def test_daily_dd_suspends_strategy():
    ledger = fresh_ledger()
    # Lose enough to trigger daily DD
    daily_loss = -500.0 * _DAILY_DD_SUSPEND_PCT - 0.01  # just over threshold
    ledger.register_close("MomentumLS", 0.0, daily_loss)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"] == "suspended"
    assert s["daily_dd_pct"] >= _DAILY_DD_SUSPEND_PCT * 100

    ok, reason = ledger.can_open("MomentumLS", 10.0)
    assert not ok
    assert "suspended" in reason


# ---------------------------------------------------------------------------
# 6. Total DD kill
# ---------------------------------------------------------------------------

def test_total_dd_kills_strategy():
    ledger = fresh_ledger()
    total_loss = -500.0 * _TOTAL_DD_KILL_PCT - 0.01   # just over threshold
    ledger.register_close("MomentumLS", 0.0, total_loss)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"] == "killed"

    ok, reason = ledger.can_open("MomentumLS", 10.0)
    assert not ok
    assert "killed" in reason


# ---------------------------------------------------------------------------
# 7. enable_strategy clears suspension (not kill)
# ---------------------------------------------------------------------------

def test_enable_clears_suspension():
    ledger = fresh_ledger()
    daily_loss = -500.0 * _DAILY_DD_SUSPEND_PCT - 0.01
    ledger.register_close("MomentumLS", 0.0, daily_loss)
    assert ledger.get_strategy_status("MomentumLS")["state"] == "suspended"

    ledger.enable_strategy("MomentumLS")
    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"] == "active"


# ---------------------------------------------------------------------------
# 8. release_reserved cancels reservation correctly
# ---------------------------------------------------------------------------

def test_release_reserved():
    ledger = fresh_ledger()
    ledger.reserve_notional("MomentumLS", 200.0)
    ledger.release_reserved("MomentumLS", 200.0)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["reserved_notional"] == 0.0
    assert s["available_capital"] == 500.0


# ---------------------------------------------------------------------------
# 9. Unknown strategy is blocked (not crashed)
# ---------------------------------------------------------------------------

def test_unknown_strategy_blocked():
    ledger = fresh_ledger()
    ok, reason = ledger.can_open("UnknownStrat", 100.0)
    assert not ok
    assert "unknown_strategy" in reason


# ---------------------------------------------------------------------------
# 10. reset_strategy clears all state
# ---------------------------------------------------------------------------

def test_reset_strategy():
    ledger = fresh_ledger()
    ledger.reserve_notional("MomentumLS", 100.0)
    ledger.register_open("MomentumLS", 100.0)
    ledger.register_close("MomentumLS", 100.0, -50.0)

    ledger.reset_strategy("MomentumLS")

    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"]           == "active"
    assert s["realized_pnl"]    == 0.0
    assert s["open_notional"]   == 0.0
    assert s["available_capital"] == 500.0


# ---------------------------------------------------------------------------
# 11. Multiple strategies are isolated
# ---------------------------------------------------------------------------

def test_strategies_isolated():
    ledger = StrategyCapitalLedger(risk_log_path="logs/risk_events_test.csv")
    ledger.register_strategy("StratA", 500.0)
    ledger.register_strategy("StratB", 500.0)

    # Kill StratA
    ledger.register_close("StratA", 0.0, -500.0 * _TOTAL_DD_KILL_PCT - 0.01)
    assert ledger.get_strategy_status("StratA")["state"] == "killed"

    # StratB must be unaffected
    ok, _ = ledger.can_open("StratB", 100.0)
    assert ok


# ---------------------------------------------------------------------------
# 12. Wins/losses tracking
# ---------------------------------------------------------------------------

def test_wins_losses_tracking():
    ledger = fresh_ledger()
    ledger.register_close("MomentumLS", 50.0,  2.0)   # win
    ledger.register_close("MomentumLS", 50.0,  1.5)   # win
    ledger.register_close("MomentumLS", 50.0, -0.5)   # loss

    s = ledger.get_strategy_status("MomentumLS")
    assert s["wins"]         == 2
    assert s["losses"]       == 1
    assert s["trades_today"] == 3


# ---------------------------------------------------------------------------
# Phase 2: Unrealized PnL drives peak-DD suspension
# ---------------------------------------------------------------------------

def test_unrealized_loss_suspends_before_realized_close():
    """A large unrealized loss must suspend the strategy BEFORE any close."""
    # Tighter DD threshold so we hit it quickly
    ledger = StrategyCapitalLedger(risk_log_path="logs/risk_events_test.csv",
                                   max_drawdown_pct=10.0,
                                   suspend_on_dd_minutes=60)
    ledger.register_strategy("MomentumLS", 500.0)

    # Open a position, then mark the unrealized PnL deep underwater.
    ledger.reserve_notional("MomentumLS", 200.0)
    ledger.register_open("MomentumLS", 200.0)

    # 12% unrealized loss on initial capital → peak-DD = 12% > 10%
    ledger.update_unrealized("MomentumLS", -60.0)

    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"] == "suspended", f"Expected suspended, got {s['state']}"
    assert s["realized_pnl"] == 0.0, "No realized close should have happened"
    assert s["unrealized_pnl"] == pytest.approx(-60.0, abs=1e-6)

    ok, reason = ledger.can_open("MomentumLS", 50.0)
    assert not ok
    assert "suspended" in reason and "peak_dd" in reason


def test_unrealized_gain_lifts_peak_equity():
    """When equity rises, peak_equity must rise too."""
    ledger = fresh_ledger()
    ledger.reserve_notional("MomentumLS", 200.0)
    ledger.register_open("MomentumLS", 200.0)

    ledger.update_unrealized("MomentumLS", +50.0)
    s = ledger.get_strategy_status("MomentumLS")
    assert s["peak_equity"] == pytest.approx(550.0, abs=1e-6)
    assert s["state"] == "active"


def test_peak_dd_uses_peak_not_initial():
    """Peak-to-trough drawdown must use peak, not initial capital."""
    ledger = StrategyCapitalLedger(risk_log_path="logs/risk_events_test.csv",
                                   max_drawdown_pct=15.0,
                                   suspend_on_dd_minutes=60)
    ledger.register_strategy("MomentumLS", 500.0)

    # First, equity rises to 600
    ledger.update_unrealized("MomentumLS", +100.0)
    s = ledger.get_strategy_status("MomentumLS")
    assert s["peak_equity"] == pytest.approx(600.0, abs=1e-6)

    # Now equity = 510 → drawdown = (600 - 510)/600 = 15.0% → suspend
    ledger.update_unrealized("MomentumLS", +10.0)
    s = ledger.get_strategy_status("MomentumLS")
    assert s["state"] == "suspended"


def test_configurable_max_dd_threshold():
    """The max_drawdown_pct parameter is honored."""
    # Tight: 5% peak-DD
    ledger_tight = StrategyCapitalLedger(risk_log_path="logs/risk_events_test.csv",
                                          max_drawdown_pct=5.0)
    ledger_tight.register_strategy("S", 500.0)
    # 6% UPnL loss
    ledger_tight.update_unrealized("S", -30.0)
    assert ledger_tight.get_strategy_status("S")["state"] == "suspended"

    # Loose: 30% peak-DD — same loss should NOT suspend
    ledger_loose = StrategyCapitalLedger(risk_log_path="logs/risk_events_test.csv",
                                          max_drawdown_pct=30.0)
    ledger_loose.register_strategy("S", 500.0)
    ledger_loose.update_unrealized("S", -30.0)
    assert ledger_loose.get_strategy_status("S")["state"] == "active"
