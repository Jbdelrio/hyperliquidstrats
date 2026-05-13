"""
tests/test_micro_live_safety.py — Phase 4 micro-live safety guard.

Verifies that the EngineV9 micro-live mode refuses to start without
the explicit env arm, refuses excessive notionals, and never instantiates
a live order router (live execution remains NotImplementedError).
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
PRESET = REPO / "config" / "presets" / "micro_live_safe.json"


def test_preset_exists_and_safe_defaults():
    assert PRESET.exists(), "Preset file should exist"
    with open(PRESET, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["mode"] == "micro_live"
    assert cfg["paper_mode"] is False
    assert cfg["max_order_notional_usd"] <= 5
    assert cfg["max_daily_loss_usd"] <= 10
    assert "BTC" in cfg["allow_only_symbols"]
    # All scanner / heavy strategies must be disabled
    enabled = [s["name"] for s in cfg["strategies"] if s.get("enabled")]
    for forbidden in ("MetaAlpha", "FundingArbitrage",
                      "RotationMomentum", "OBImbalanceScalper"):
        assert forbidden not in enabled, f"{forbidden} should be disabled"


def test_micro_live_requires_env_arm(monkeypatch, tmp_path):
    """Without ARTEMISIA_ALLOW_MICRO_LIVE=true, engine refuses to start."""
    monkeypatch.delenv("ARTEMISIA_ALLOW_MICRO_LIVE", raising=False)
    from engine_v9 import EngineV9
    # Use the preset as-is — paper=False to trigger the micro_live check
    with pytest.raises(RuntimeError, match=r"ARTEMISIA_ALLOW_MICRO_LIVE"):
        EngineV9(config_path="config/presets/micro_live_safe.json",
                 paper=False)


def test_micro_live_rejects_high_notional(monkeypatch, tmp_path):
    """Even with env armed, notional > $5 is rejected."""
    monkeypatch.setenv("ARTEMISIA_ALLOW_MICRO_LIVE", "true")
    # Craft a malicious-looking variant with too-high notional
    cfg_src = json.loads(PRESET.read_text(encoding="utf-8"))
    cfg_src["max_order_notional_usd"] = 50
    bad = tmp_path / "bad_micro.json"
    bad.write_text(json.dumps(cfg_src), encoding="utf-8")

    # Engine reads relative to repo root — copy file there
    bad_in_repo = REPO / "config" / "presets" / "_bad_micro_TEST.json"
    bad_in_repo.write_text(json.dumps(cfg_src), encoding="utf-8")
    try:
        from engine_v9 import EngineV9
        with pytest.raises(RuntimeError, match=r"max_order_notional_usd"):
            EngineV9(config_path="config/presets/_bad_micro_TEST.json",
                     paper=False)
    finally:
        bad_in_repo.unlink(missing_ok=True)


def test_live_executor_raises_not_implemented():
    """HighFreqExecutor must continue to refuse non-paper mode."""
    from execution.high_freq_executor import HighFreqExecutor
    with pytest.raises(NotImplementedError):
        HighFreqExecutor(paper=False)
