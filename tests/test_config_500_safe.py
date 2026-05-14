"""tests/test_config_500_safe.py — Validate the paper_500_total_safe preset."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_PRESET_PATH = (
    Path(__file__).resolve().parents[1]
    / "config" / "presets" / "paper_500_total_safe.json"
)


@pytest.fixture(scope="module")
def cfg():
    assert _PRESET_PATH.exists(), f"Preset missing: {_PRESET_PATH}"
    with open(_PRESET_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_preset_exists(cfg):
    assert cfg is not None
    assert cfg.get("capital") == 500


def test_max_positions_is_one(cfg):
    """With 500$ total, exactly one position at a time."""
    assert cfg["risk"]["max_open_positions"] == 1


def test_max_notional_total_lte_100(cfg):
    assert cfg["risk"]["max_notional_total"] <= 100


def test_max_position_size_lte_50(cfg):
    """No single position may exceed $50."""
    for s in cfg["strategies"]:
        if not s.get("enabled"):
            continue
        assert s["max_position_size_usd"] <= 50, (
            f"{s['name']} exceeds 50$ position cap"
        )


def test_only_three_strategies_enabled(cfg):
    enabled = [s["name"] for s in cfg["strategies"] if s.get("enabled")]
    assert sorted(enabled) == sorted([
        "MomentumLS", "BreakoutControlled", "RSIBollingerReversion",
    ])


def test_live_mode_is_false(cfg):
    assert cfg.get("paper_mode") is True


def test_sanity_check_block_present(cfg):
    sc = cfg.get("sanity_check", {})
    assert "max_spread_bps" in sc
    assert sc["max_spread_bps"] <= 50
    assert sc["min_reward_risk_ratio"] >= 1.5
    assert sc["max_order_notional_usd"] <= 50


def test_execution_filters_strict(cfg):
    ef = cfg.get("execution_filters", {})
    assert ef.get("enabled") is True
    assert ef["min_reward_risk_ratio"] >= 1.5


def test_btc_vol_guard_units_fraction(cfg):
    """btc_move_5m_pct must be a fraction (< 0.5), not a percent."""
    assert cfg["risk"]["btc_move_5m_pct"] < 0.5


def test_loads_into_engine_state(cfg):
    """Sanity check: the preset can be consumed by the engine config layer
    (no NaN, no missing required keys)."""
    # Required top-level keys for engine_v9.py
    assert cfg["capital"] > 0
    assert "websocket_url" in cfg
    assert "strategies" in cfg
    assert "logging" in cfg
    assert "runtime" in cfg


def test_strategy_capital_sums_to_at_most_capital(cfg):
    """Per-strategy capital cannot collectively exceed total capital."""
    total = sum(s.get("capital_allocated_usd", 0) for s in cfg["strategies"])
    # 3 × 150 = 450 ≤ 500
    assert total <= cfg["capital"]
