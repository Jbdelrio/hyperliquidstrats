"""Tests for the engine's manual paper-trading commands (Phase C)."""
import asyncio
import time

import pytest

from engine_v9 import EngineV9


@pytest.fixture(scope="module")
def engine():
    asyncio.set_event_loop(asyncio.new_event_loop())
    return EngineV9(
        config_path="config/presets/paper_500_all_strategies_adaptive.json",
        paper=True,
    )


def test_manual_set_param_changes_value(engine):
    r = engine._process_control_command({
        "command": "manual_set_param",
        "args": {"strategy": "OBImbalanceScalper",
                 "param_name": "imbalance_entry_threshold",
                 "value": 0.42, "ttl_seconds": 30,
                 "reason": "test"},
    }, 1_000.0)
    assert r["ok"] is True
    assert r["new"] == 0.42
    # Verify on the strategy directly
    s = engine.manager.strategies["OBImbalanceScalper"]
    assert s.config.params["imbalance_entry_threshold"] == 0.42


def test_manual_set_param_rejects_unknown_strategy(engine):
    r = engine._process_control_command({
        "command": "manual_set_param",
        "args": {"strategy": "DOESNOTEXIST",
                 "param_name": "x", "value": 1.0},
    }, 1_000.0)
    assert r["ok"] is False


def test_manual_open_rejects_unknown_symbol(engine):
    r = engine._process_control_command({
        "command": "manual_open",
        "args": {"symbol": "ZZZ", "side": "BUY", "notional_usd": 25},
    }, 1_000.0)
    assert r["ok"] is False


def test_manual_open_requires_book(engine):
    # Symbol is known but no book in _last_book → must fail gracefully.
    engine._last_book.pop("BTC", None)
    r = engine._process_control_command({
        "command": "manual_open",
        "args": {"symbol": "BTC", "side": "BUY", "notional_usd": 25},
    }, 1_000.0)
    assert r["ok"] is False
    assert "book" in r["error"].lower()


def test_manual_flatten_symbol_returns_ok(engine):
    r = engine._process_control_command({
        "command": "manual_flatten_symbol",
        "args": {"symbol": "BTC", "reason": "test"},
    }, 1_000.0)
    assert r["ok"] is True


def test_unknown_command_rejected(engine):
    r = engine._process_control_command({"command": "no_such_cmd"}, 1_000.0)
    assert r["ok"] is False
