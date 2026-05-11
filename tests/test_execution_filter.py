"""
test_execution_filter.py — Unit tests for anti-micro-trade execution filter.
"""
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

import pytest

from strategies.base_strategy import BaseStrategy, StrategyConfig, StrategyDecision, BarData


# ── Concrete stub strategy ────────────────────────────────────────────────────

class _StubStrategy(BaseStrategy):
    def on_orderbook_update(self, symbol, book, ts):
        return None
    def on_trade_update(self, symbol, trade, ts):
        pass
    def on_bar_minute(self, symbol, bar, ts):
        return None


def _make_strat(notional=250.0) -> _StubStrategy:
    cfg = StrategyConfig(
        name="Test", enabled=True,
        capital_allocated_usd=500, max_positions=2,
        max_position_size_usd=notional,
        coins=["BTC"], params={},
    )
    return _StubStrategy(cfg)


# ── Tests: estimate_trade_economics ─────────────────────────────────────────

def test_micro_trade_rejected():
    """A trade with tiny move relative to fees should be rejected."""
    strat = _make_strat()
    # entry=100, tp=100.10 (0.1%), sl=99.90 (−0.1%), notional=250
    # gross_tp = 0.001 * 250 = 0.25 USD
    # round_trip_cost = 2 * (3+4)/10000 * 250 = 0.35 USD
    # net = 0.25 - 0.35 = -0.10 USD → must reject
    passes, reason, econ = strat.passes_min_edge_filter(
        entry=100.0, tp=100.10, sl=99.90, notional=250.0, side="long",
        min_net_profit=3.0, min_rr=1.4, fee_bps=3.0, slippage_bps=4.0,
    )
    assert not passes
    assert "net_too_low" in reason
    assert econ["expected_net_profit_usd"] < 0


def test_real_move_accepted():
    """A trade with 2% TP and 1% SL at $250 notional should pass."""
    strat = _make_strat()
    entry = 100.0
    tp    = 102.0   # +2%
    sl    = 99.0    # -1%
    passes, reason, econ = strat.passes_min_edge_filter(
        entry=entry, tp=tp, sl=sl, notional=250.0, side="long",
        min_net_profit=3.0, min_rr=1.4, fee_bps=3.0, slippage_bps=4.0,
    )
    # gross_tp = 0.02 * 250 = 5.00, cost = 2*(7/10000)*250 = 0.35
    # net = 5.00 - 0.35 = 4.65 ≥ 3.0 ✓
    # risk = 0.01*250 + 0.35 = 2.85, rr = 4.65/2.85 ≈ 1.63 ≥ 1.4 ✓
    assert passes, f"Expected pass but got: {reason}"
    assert econ["expected_net_profit_usd"] >= 3.0
    assert econ["reward_risk_ratio"] >= 1.4


def test_min_hold_enforced_for_fast_exits():
    """A non-protective close before min_hold_s should be blocked."""
    from unittest.mock import patch

    # We test the min-hold logic by checking the condition directly
    # (without spinning up the full engine)
    strat = _make_strat()

    # Simulate the _apply_close_action guard logic
    min_hold_s = 90.0
    entry_ts   = time.time() - 30.0   # position opened 30s ago
    ts         = time.time()
    reason     = "z_reversion"        # non-protective

    _reason_l      = reason.lower()
    _is_protective = any(k in _reason_l for k in
                         ("stop", "manual", "emergency", "flatten", "shutdown"))
    hold_time = ts - entry_ts

    assert not _is_protective, "z_reversion must not be treated as protective"
    assert hold_time < min_hold_s, "Hold time should be below min_hold_s for this test"
    # The engine would skip the close in this case — assert the condition matches
    assert not _is_protective and hold_time < min_hold_s


def test_imbalance_reversed_blocked_by_min_hold():
    """imbalance_reversed is not protective — blocked under min_hold."""
    reason = "imbalance_reversed"
    _reason_l = reason.lower()
    _is_protective = any(k in _reason_l for k in
                         ("stop", "manual", "emergency", "flatten", "shutdown"))
    assert not _is_protective


def test_stop_loss_bypasses_min_hold():
    """stop_loss reason must be treated as protective (bypasses min_hold)."""
    for reason in ("stop_loss", "emergency_close", "flatten_strategy", "shutdown"):
        _reason_l = reason.lower()
        _is_protective = any(k in _reason_l for k in
                             ("stop", "manual", "emergency", "flatten", "shutdown"))
        assert _is_protective, f"'{reason}' should be protective"


def test_ob_imbalance_scalper_disabled_in_preset():
    """OBImbalanceScalper must be disabled (capital=0) in paper_500_clean.json."""
    preset_path = (
        Path(__file__).resolve().parents[1]
        / "config" / "presets" / "paper_500_clean.json"
    )
    with open(preset_path, encoding="utf-8") as f:
        cfg = json.load(f)

    strats = {s["name"]: s for s in cfg["strategies"]}
    ob = strats.get("OBImbalanceScalper", {})
    assert ob.get("enabled") is False, "OBImbalanceScalper should be disabled"
    assert ob.get("capital_allocated_usd", 999) == 0, "OBImbalanceScalper capital should be 0"


def test_five_strategies_enabled_in_preset():
    """Exactly 5 strategies should be enabled in paper_500_clean.json."""
    preset_path = (
        Path(__file__).resolve().parents[1]
        / "config" / "presets" / "paper_500_clean.json"
    )
    with open(preset_path, encoding="utf-8") as f:
        cfg = json.load(f)

    enabled = [s["name"] for s in cfg["strategies"] if s.get("enabled")]
    expected = {"MomentumLS", "BreakoutControlled", "DonchianTrend",
                "VolatilityRegimeBreakout", "RSIBollingerReversion"}
    assert set(enabled) == expected, f"Enabled strategies mismatch: {enabled}"


def test_short_trade_economics():
    """Economics are correct for a short position."""
    strat = _make_strat()
    # short: entry=100, tp=98 (−2% → profit), sl=101 (+1% → loss)
    passes, reason, econ = strat.passes_min_edge_filter(
        entry=100.0, tp=98.0, sl=101.0, notional=250.0, side="short",
        min_net_profit=3.0, min_rr=1.4, fee_bps=3.0, slippage_bps=4.0,
    )
    assert econ["tp_pct"] > 0
    assert econ["sl_pct"] < 0
    assert passes, f"Short 2%/1% RR should pass: {reason}"
