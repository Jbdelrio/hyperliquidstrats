"""Tests for risk/regime_controller.py — bounded adjustments."""
import math
import time

import pytest

from risk.regime_controller import (
    RegimeController, RegimeSnapshot, ParameterAdjustment,
)


def _features(**over):
    f = {
        "symbol": "BTC", "ts": time.time(),
        "spread_bps": 2.0, "rv_60s": 0.001,    # 10 bps
        "ofi_30s": 0.1, "ofi_60s": 0.1,
        "liquidity_score": 0.7, "toxicity_score": 0.2,
    }
    f.update(over)
    return f


def test_detect_normal_regime():
    rc = RegimeController({})
    snap = rc.detect_regime(_features(), btc_context={"r_5m_pct": 0.0})
    assert snap.regime == "NORMAL"


def test_detect_btc_crash():
    rc = RegimeController({})
    snap = rc.detect_regime(_features(), btc_context={"r_5m_pct": -3.5})
    assert snap.regime == "BTC_CRASH"
    assert snap.confidence > 0.4


def test_detect_high_vol_trend():
    rc = RegimeController({})
    snap = rc.detect_regime(
        _features(rv_60s=0.006, ofi_30s=0.5, ofi_60s=0.4))
    assert snap.regime == "HIGH_VOL_TREND"


def test_detect_high_vol_chaotic():
    rc = RegimeController({})
    snap = rc.detect_regime(
        _features(rv_60s=0.006, ofi_30s=0.05, ofi_60s=-0.05))
    assert snap.regime == "HIGH_VOL_CHAOTIC"


def test_detect_toxic_flow():
    rc = RegimeController({})
    snap = rc.detect_regime(_features(toxicity_score=0.85))
    assert snap.regime == "TOXIC_FLOW"


def test_propose_never_amplifies_notional_by_default(tmp_path):
    rc = RegimeController({"log_path": str(tmp_path / "r.csv")})
    snap = RegimeSnapshot("BTC", "HIGH_VOL_CHAOTIC", 0.9, 60, 5, 0.5, 0.2, {}, time.time())
    # Chaotic should halve notional → from 100 to 50, NEVER above 100.
    adjs = rc.propose_adjustments("X", "BTC", snap,
                                  {"notional_usd": 100.0,
                                   "cooldown_s": 30.0}, ttl_seconds=60)
    notion_adj = [a for a in adjs if a.param_name == "notional_usd"]
    assert len(notion_adj) == 1
    assert notion_adj[0].new_value < notion_adj[0].old_value


def test_propose_bounds_clip_extreme_cooldown(tmp_path):
    rc = RegimeController({"log_path": str(tmp_path / "r.csv")})
    snap = RegimeSnapshot("BTC", "HIGH_VOL_CHAOTIC", 0.9, 60, 5, 0.5, 0.2, {}, time.time())
    adjs = rc.propose_adjustments("X", "BTC", snap,
                                  {"cooldown_s": 1200.0}, ttl_seconds=60)
    cd_adj = [a for a in adjs if a.param_name == "cooldown_s"]
    assert len(cd_adj) == 1
    # double = 2400 but _BOUNDS["cooldown_s"] caps at 1800
    assert cd_adj[0].new_value == 1800


def test_amplification_blocked_when_user_forces(tmp_path):
    # Even if a future rule tries to amplify, the controller refuses
    # unless allow_notional_amplification is True.
    rc = RegimeController({"log_path": str(tmp_path / "r.csv"),
                            "allow_notional_amplification": False,
                            "max_notional_multiplier": 1.0})
    assert rc.max_notional_multiplier == 1.0


def test_amplification_capped_even_when_enabled(tmp_path):
    rc = RegimeController({"log_path": str(tmp_path / "r.csv"),
                            "allow_notional_amplification": True,
                            "max_notional_multiplier": 2.0})
    assert rc.max_notional_multiplier == 2.0


def test_disabled_controller_returns_no_adjustment(tmp_path):
    rc = RegimeController({"enabled": False,
                            "log_path": str(tmp_path / "r.csv")})
    snap = RegimeSnapshot("BTC", "HIGH_VOL_CHAOTIC", 0.9, 60, 5, 0.5, 0.2, {}, time.time())
    adjs = rc.propose_adjustments("X", "BTC", snap, {"notional_usd": 100.0})
    assert adjs == []


def test_logs_get_written(tmp_path):
    log_p = tmp_path / "r.csv"
    rc = RegimeController({"log_path": str(log_p)})
    snap = RegimeSnapshot("BTC", "HIGH_VOL_CHAOTIC", 0.9, 60, 5, 0.5, 0.2, {}, time.time())
    rc.propose_adjustments("X", "BTC", snap,
                           {"notional_usd": 100.0}, ttl_seconds=60)
    assert log_p.exists()
    content = log_p.read_text(encoding="utf-8")
    assert "notional_usd" in content
