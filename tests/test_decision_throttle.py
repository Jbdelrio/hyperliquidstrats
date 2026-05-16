"""Tests for risk/decision_throttle.py."""
from risk.decision_throttle import DecisionThrottle


def _cfg(**over):
    cfg = dict(
        enabled=True,
        min_seconds_between_entries_per_symbol=60,
        min_seconds_between_entries_per_strategy=120,
        max_entries_per_symbol_per_hour=3,
        max_entries_global_per_hour=6,
    )
    cfg.update(over)
    return cfg


def test_non_entry_action_always_passes():
    t = DecisionThrottle(_cfg())
    ok, _ = t.check("S", "BTC", "CANCEL_QUOTES", now=0.0)
    assert ok


def test_symbol_gap_block():
    t = DecisionThrottle(_cfg())
    t.record_entry("S", "BTC", now=0.0)
    ok, reason = t.check("S2", "BTC", "PLACE_BUY", now=30.0)
    assert not ok
    assert reason.startswith("symbol_gap")


def test_symbol_gap_clear_after_window():
    t = DecisionThrottle(_cfg(min_seconds_between_entries_per_strategy=1))
    t.record_entry("S", "BTC", now=0.0)
    ok, _ = t.check("S2", "BTC", "PLACE_BUY", now=120.0)
    assert ok


def test_strategy_gap_block():
    t = DecisionThrottle(_cfg(min_seconds_between_entries_per_symbol=1))
    t.record_entry("S", "BTC", now=0.0)
    # Different symbol but same strategy — blocked by strategy_gap.
    ok, reason = t.check("S", "ETH", "PLACE_BUY", now=60.0)
    assert not ok
    assert reason.startswith("strategy_gap")


def test_hourly_cap_per_symbol():
    t = DecisionThrottle(_cfg(
        min_seconds_between_entries_per_symbol=1,
        min_seconds_between_entries_per_strategy=1,
        max_entries_per_symbol_per_hour=2,
    ))
    t.record_entry("A", "BTC", now=0.0)
    t.record_entry("B", "BTC", now=10.0)
    ok, reason = t.check("C", "BTC", "PLACE_BUY", now=20.0)
    assert not ok
    assert reason.startswith("hourly_cap_symbol")


def test_hourly_cap_global():
    t = DecisionThrottle(_cfg(
        min_seconds_between_entries_per_symbol=1,
        min_seconds_between_entries_per_strategy=1,
        max_entries_per_symbol_per_hour=99,
        max_entries_global_per_hour=2,
    ))
    t.record_entry("A", "BTC", now=0.0)
    t.record_entry("B", "ETH", now=10.0)
    ok, reason = t.check("C", "SOL", "PLACE_BUY", now=20.0)
    assert not ok
    assert reason == "hourly_cap_global"


def test_disabled_passes_all():
    t = DecisionThrottle(_cfg(enabled=False))
    ok, _ = t.check("S", "BTC", "PLACE_BUY", now=0.0)
    assert ok


def test_stats_tracked():
    t = DecisionThrottle(_cfg())
    t.record_entry("S", "BTC", now=0.0)
    t.check("S", "BTC", "PLACE_BUY", now=10.0)
    assert t.stats.total_blocked == 1
    assert "symbol_gap" in t.stats.blocks_by_reason
