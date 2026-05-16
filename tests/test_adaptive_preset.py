"""Validate the paper_500_all_strategies_adaptive preset."""
import json
from pathlib import Path

import pytest

_PRESET = Path(__file__).resolve().parent.parent / "config" / "presets" / "paper_500_all_strategies_adaptive.json"


@pytest.fixture(scope="module")
def cfg():
    return json.loads(_PRESET.read_text(encoding="utf-8"))


def test_capital_matches_sum_of_strategy_budgets(cfg):
    expected = sum(s.get("capital_allocated_usd", 0) for s in cfg["strategies"])
    assert cfg["capital"] == expected


def test_every_trading_strategy_has_500(cfg):
    trading = [s for s in cfg["strategies"]
               if s.get("enabled") and s.get("max_positions", 0) > 0]
    for s in trading:
        assert s["capital_allocated_usd"] == 500


def test_funding_basis_strategies_disabled_with_reason(cfg):
    """Funding/basis strategies must NOT be enabled — they need external feeds."""
    must_be_disabled = {
        "FundingArbitrage", "FundingCarryHedged",
        "SpotPerpBasis", "RelativeValue",
    }
    by_name = {s["name"]: s for s in cfg["strategies"]}
    for n in must_be_disabled:
        if n in by_name:
            s = by_name[n]
            assert not s.get("enabled"), f"{n} must be disabled"
            assert s.get("capital_allocated_usd", 0) == 0
            assert "_disabled_reason" in s, f"{n} missing _disabled_reason explanation"


def test_regime_controller_block_present(cfg):
    rc = cfg.get("regime_controller", {})
    assert rc.get("enabled") is True
    assert rc.get("max_notional_multiplier", 0) <= 1.0
    assert rc.get("min_notional_multiplier", 0) >= 0.0


def test_paper_simulation_is_conservative(cfg):
    ps = cfg.get("paper_simulation", {})
    assert ps.get("tp_fill_mode") == "market_after_touch"
    assert ps.get("stop_fill_mode") == "market_after_touch"


def test_runtime_paths_include_regime_status_file(cfg):
    rt = cfg.get("runtime", {})
    assert "regime_status_file" in rt or rt.get("status_file")
