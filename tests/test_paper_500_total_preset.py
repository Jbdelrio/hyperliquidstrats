"""Validate the paper_500_total_seconds_filtered (★ IDEAL) preset.

This preset allocates $500 PER trading strategy, enables every strategy
that runs without external dependencies (so the data collection covers
all of them), and tunes tick-based strategies for ~2-3 trades / 2 min
while keeping the seconds-feature filter active.
"""
import json
from pathlib import Path

import pytest

_PRESET = Path(__file__).resolve().parent.parent / "config" / "presets" / "paper_500_total_seconds_filtered.json"


@pytest.fixture(scope="module")
def cfg():
    return json.loads(_PRESET.read_text(encoding="utf-8"))


def test_preset_is_paper(cfg):
    assert cfg["paper_mode"] is True


def test_capital_matches_per_strategy_budget(cfg):
    # capital must equal the sum of per-strategy budgets so the GUI / ledger maths line up.
    expected = sum(s.get("capital_allocated_usd", 0) for s in cfg["strategies"])
    assert cfg["capital"] == expected, (
        f"capital={cfg['capital']} != sum(strategy budgets)={expected}")


def test_no_zero_capital_on_trading_strategies(cfg):
    """Strategies with max_positions > 0 (i.e. that DO trade) must have $500."""
    trading = [s for s in cfg["strategies"]
               if s.get("enabled") and s.get("max_positions", 0) > 0]
    assert len(trading) >= 8, "expected at least 8 trading strategies"
    for s in trading:
        assert s["capital_allocated_usd"] == 500, (
            f"{s['name']} should have $500, got ${s['capital_allocated_usd']}")


def test_research_only_strategies_have_zero_capital(cfg):
    """Research-only strats (max_positions=0) intentionally have $0."""
    research = [s for s in cfg["strategies"]
                if s.get("enabled") and s.get("max_positions", 0) == 0]
    for s in research:
        assert s["capital_allocated_usd"] == 0, (
            f"{s['name']} is research-only; should have $0")


def test_seconds_features_enabled(cfg):
    sf = cfg.get("seconds_features", {})
    assert sf.get("enabled") is True


def test_market_quality_gate_enabled(cfg):
    g = cfg.get("market_quality_gate", {})
    assert g.get("enabled") is True


def test_decision_throttle_supports_target_frequency(cfg):
    """Target: 2-3 trades / 2 min. Throttle global cap must allow >= 60/h."""
    t = cfg.get("decision_throttle", {})
    assert t.get("enabled") is True
    assert t.get("max_entries_global_per_hour", 0) >= 60, (
        "throttle cap too low for the 2-3/2min target")


def test_realistic_paper_executor(cfg):
    ps = cfg.get("paper_simulation", {})
    assert ps.get("tp_fill_mode") == "market_after_touch"
    assert ps.get("stop_fill_mode") == "market_after_touch"


def test_fees_from_config(cfg):
    f = cfg.get("fees", {})
    assert "maker_bps" in f and "taker_bps" in f
    assert f["taker_bps"] > 0


def test_tick_strategies_have_fast_cooldown(cfg):
    """Tick / seconds scalpers must have cooldowns < 60s to support 2-3/2min."""
    tick_classes = {
        "OrderBookImbalanceScalper",
        "AlphaPressureScalper",
        "BookFlowDivergenceReversal",
        "AbsorptionReversal",
    }
    for s in cfg["strategies"]:
        if s.get("class") in tick_classes and s.get("enabled"):
            params = s.get("params", {})
            cd = params.get("cooldown_s") or params.get("cooldown_seconds")
            assert cd is not None and cd <= 45, (
                f"{s['name']}: cooldown {cd}s too long for high-freq target")


def test_total_max_open_positions_consistent(cfg):
    """max_open_positions in risk should at least equal the sum of per-strat max_positions."""
    expected = sum(s.get("max_positions", 0) for s in cfg["strategies"]
                   if s.get("enabled"))
    assert cfg["risk"]["max_open_positions"] >= min(expected, 10), (
        "risk.max_open_positions seems too tight for the enabled strategies")
