"""
tests/test_btc_5min_binary_repl.py — BTC_5MIN_BINARY_REPL unit tests.

Covers: sizing + leverage clamp, warmup gate, feature maths (returns, RV,
z-score), entry signal (long/short), no-trade reasons, risk gates, and the
signal-reversal early exit.

Entry/exit logic is exercised through the internal methods with hand-built
feature dicts so the tests are deterministic (the rolling z-score
distribution is otherwise hard to engineer through the public path).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.base_strategy import StrategyConfig
from strategies.btc_5min_binary_repl import (
    BTC5MinBinaryReplStrategy,
    NT_WARMUP, NT_SPREAD, NT_NO_VOL, NT_WEAK, NT_OBI, NT_FLOW,
    NT_DAILY_LOSS, NT_CONSEC_LOSS, NT_MAX_TRADES, NT_OK,
)


def _strat(params=None, tmp_path=None) -> BTC5MinBinaryReplStrategy:
    p = dict(params or {})
    if tmp_path is not None:
        p.setdefault("feature_log_path", str(tmp_path / "feat.csv"))
        p.setdefault("trade_log_path", str(tmp_path / "trade.csv"))
        p.setdefault("log_features", False)
    cfg = StrategyConfig(
        name="BTC_5MIN_BINARY_REPL", enabled=True,
        capital_allocated_usd=500.0, max_positions=1,
        max_position_size_usd=100.0, coins=["BTC"], params=p,
    )
    return BTC5MinBinaryReplStrategy(cfg)


def _feat(**over) -> dict:
    """A feature dict that PASSES every gate; override keys per test."""
    f = dict(
        ts=2000.0, mid=100_000.0, best_bid=99_999.0, best_ask=100_001.0,
        spread_bps=1.0, obi_5=0.3, obi_10=0.3, flow_60s=0.3,
        book_stale=False, enough_data=True,
        rv_60s_bps=15.0, rv_180s_bps=15.0, rv_300s_bps=15.0,
        return_60s=0.001, return_180s=0.001,
        z_return_60s=1.5, z_return_180s=1.0,
        z_obi_top10=1.5, z_flow_60s=1.2, z_spread_bps=0.0,
        score=1.2, p_up=0.75, model_quality="READY", warmup_seconds=2000,
    )
    f.update(over)
    return f


def _warm(strat):
    """Mark the strategy as past its warmup window."""
    strat._first_ts = 0.0
    strat._last_feat_ts = 2000.0


# ---------------------------------------------------------------------------
# Sizing + leverage
# ---------------------------------------------------------------------------

def test_sizing_default_10x():
    s = _strat()
    notional, margin, lev = s._sizing()
    assert (notional, margin, lev) == (100.0, 10.0, 10.0)


def test_leverage_is_clamped_to_max_leverage():
    # A 50x request with max_leverage 10 must clamp to 10x — not 50x.
    s = _strat({"leverage": 50, "max_leverage": 10})
    notional, margin, lev = s._sizing()
    assert lev == 10.0
    assert notional == 100.0          # min(10 * 10, 100)


def test_sizing_respects_notional_cap():
    s = _strat({"leverage": 10, "max_leverage": 10,
                "max_margin_per_trade_usd": 10, "max_position_notional_usd": 60})
    notional, margin, lev = s._sizing()
    assert notional == 60.0           # min(10 * 10, 60)


# ---------------------------------------------------------------------------
# Feature maths
# ---------------------------------------------------------------------------

def test_return_computation():
    s = _strat()
    for i in range(61):
        s._mid_buf.append((1000.0 + i, 100.0 * (1.0 + i * 0.001)))
    r = s._return(1060.0, 60)
    assert r is not None and r > 0


def test_rv_is_positive_on_moving_price():
    s = _strat()
    for i in range(120):
        s._mid_buf.append((1000.0 + i, 100.0 + (i % 5) * 0.1))
    rv = s._rv_bps(1119.0, 60)
    assert rv is not None and rv > 0


def test_zscore_basic():
    s = _strat()
    for v in [0.0] * 40:
        s._zsrc["obi_10"].append(v)
    assert s._z("obi_10", 0.0) == 0.0          # std 0 → 0
    s._zsrc["obi_10"].clear()
    for v in range(40):
        s._zsrc["obi_10"].append(float(v))
    z = s._z("obi_10", 100.0)
    assert z > 0


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------

def test_warmup_blocks_entry():
    s = _strat()
    s._first_ts = 1900.0
    s._last_feat_ts = 2000.0           # only 100 s elapsed
    dec, reason = s._check_entry(_feat(), 2000.0)
    assert dec is None and reason == NT_WARMUP


def test_model_quality_progression():
    s = _strat()
    assert s._model_quality() == "COLD"
    s._first_ts = 0.0
    s._last_feat_ts = 700.0
    assert s._model_quality() == "WARMING"
    s._last_feat_ts = 2000.0
    assert s._model_quality() == "READY"
    s._last_feat_ts = 4000.0
    assert s._model_quality() == "GOOD"


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

def test_entry_long():
    s = _strat()
    _warm(s)
    dec, reason = s._check_entry(_feat(p_up=0.75, obi_10=0.3, flow_60s=0.3), 2000.0)
    assert reason == NT_OK
    assert dec is not None and dec.action == "PLACE_BUY"
    assert dec.take_profit > dec.buy_price > dec.stop_loss
    assert dec.max_hold_seconds == 300
    assert dec.notional_usd == 100.0


def test_entry_short():
    s = _strat()
    _warm(s)
    dec, reason = s._check_entry(
        _feat(p_up=0.25, obi_10=-0.3, flow_60s=-0.3,
              z_return_60s=-1.5, z_obi_top10=-1.5, z_flow_60s=-1.2, score=-1.2),
        2000.0)
    assert reason == NT_OK
    assert dec is not None and dec.action == "PLACE_SELL"
    assert dec.stop_loss > dec.sell_price > dec.take_profit


def test_entry_decision_has_protective_exits():
    s = _strat()
    _warm(s)
    dec, _ = s._check_entry(_feat(), 2000.0)
    assert dec is not None
    assert dec.stop_loss and dec.take_profit and dec.max_hold_seconds


# ---------------------------------------------------------------------------
# No-trade reasons
# ---------------------------------------------------------------------------

def test_no_trade_spread_too_wide():
    s = _strat(); _warm(s)
    dec, reason = s._check_entry(_feat(spread_bps=5.0), 2000.0)
    assert dec is None and reason == NT_SPREAD


def test_no_trade_low_volatility():
    s = _strat(); _warm(s)
    dec, reason = s._check_entry(_feat(rv_300s_bps=2.0), 2000.0)
    assert dec is None and reason == NT_NO_VOL


def test_no_trade_weak_signal():
    s = _strat(); _warm(s)
    dec, reason = s._check_entry(_feat(p_up=0.52), 2000.0)
    assert dec is None and reason == NT_WEAK


def test_no_trade_obi_not_aligned():
    s = _strat(); _warm(s)
    dec, reason = s._check_entry(_feat(p_up=0.75, obi_10=0.05), 2000.0)
    assert dec is None and reason == NT_OBI


def test_no_trade_flow_not_aligned():
    s = _strat(); _warm(s)
    dec, reason = s._check_entry(_feat(p_up=0.75, obi_10=0.3, flow_60s=0.05), 2000.0)
    assert dec is None and reason == NT_FLOW


# ---------------------------------------------------------------------------
# Risk gates
# ---------------------------------------------------------------------------

def test_risk_daily_loss_limit():
    s = _strat({"max_daily_loss_usd": 20}); _warm(s)
    s._daily_pnl = -25.0
    dec, reason = s._check_entry(_feat(), 2000.0)
    assert dec is None and reason == NT_DAILY_LOSS


def test_risk_consecutive_losses():
    s = _strat({"max_consecutive_losses": 3}); _warm(s)
    s._consec_losses = 3
    dec, reason = s._check_entry(_feat(), 2000.0)
    assert dec is None and reason == NT_CONSEC_LOSS


def test_risk_max_trades_per_hour():
    s = _strat({"max_trades_per_hour": 6}); _warm(s)
    for i in range(6):
        s._trade_ts.append(2000.0 - i * 10)     # 6 trades in the last minute
    dec, reason = s._check_entry(_feat(), 2000.0)
    assert dec is None and reason == NT_MAX_TRADES


# ---------------------------------------------------------------------------
# Position lifecycle + early exit
# ---------------------------------------------------------------------------

def test_on_fill_then_loss_sets_cooldown(tmp_path):
    s = _strat(tmp_path=tmp_path)
    s._pending = {"side": "long", "notional": 100.0, "margin": 10.0, "leverage": 10.0}
    s.on_fill("BTC", "BUY", 100_000.0, 0.001, ts=2000.0)
    assert s._position is not None and s._position["side"] == "long"
    s.on_position_closed("BTC", pnl_net=-1.0, exit_reason="STOP_LOSS_HIT")
    assert s._position is None
    assert s._consec_losses == 1
    assert s._cooldown_until > 2000.0
    assert s._daily_pnl == pytest.approx(-1.0)


def test_early_exit_on_signal_reversal(tmp_path):
    s = _strat(tmp_path=tmp_path)
    s._position = {"side": "long", "entry_px": 100_000.0, "notional": 100.0,
                   "opened_at": 2000.0, "pos_id": "x"}
    # p_up has fallen below 0.50 → long thesis is dead → CLOSE.
    dec = s._check_early_exit(_feat(p_up=0.40), 2100.0)
    assert dec is not None and dec.action == "CLOSE"


def test_no_early_exit_while_thesis_holds(tmp_path):
    s = _strat(tmp_path=tmp_path)
    s._position = {"side": "long", "entry_px": 100_000.0, "notional": 100.0,
                   "opened_at": 2000.0, "pos_id": "x"}
    dec = s._check_early_exit(_feat(p_up=0.70, obi_10=0.3, flow_60s=0.3), 2100.0)
    assert dec is None


def test_in_position_blocks_new_entry(tmp_path):
    s = _strat(tmp_path=tmp_path)
    _warm(s)
    s._position = {"side": "long", "entry_px": 100_000.0, "notional": 100.0,
                   "opened_at": 2000.0, "pos_id": "x"}
    out = s.on_second_features("BTC", {
        "mid": 100_000.0, "best_bid": 99_999.0, "best_ask": 100_001.0,
        "spread_bps": 1.0, "obi_10": 0.3, "obi_5": 0.3,
        "trade_imbalance_60s": 0.3, "enough_data": True, "book_stale": False,
    }, ts=2100.0)
    # While in position the strategy never opens a second one.
    assert out is None or out.action == "CLOSE"


def test_calibration_data_keys():
    s = _strat()
    cal = s.get_calibration_data("BTC")
    for k in ("model_quality", "warmup_pct", "p_up", "decision",
              "no_trade_reason", "leverage", "notional_usd", "in_position"):
        assert k in cal
    assert cal["leverage"] == 10.0
