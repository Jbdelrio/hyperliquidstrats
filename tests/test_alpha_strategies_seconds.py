"""Tests for the seconds-feature alpha strategies (all disabled by default)."""
import math
import time

import pytest

from strategies.alpha_pressure_scalper import AlphaPressureScalper
from strategies.absorption_reversal import AbsorptionReversal
from strategies.book_flow_divergence_reversal import BookFlowDivergenceReversal
from strategies.seconds_research_strategy import SecondsResearchStrategy
from strategies.base_strategy import StrategyConfig


def _cfg(name, cls_params=None, enabled=True):
    return StrategyConfig(
        name=name,
        enabled=enabled,
        capital_allocated_usd=10,
        max_positions=1,
        max_position_size_usd=10,
        coins=["BTC"],
        params=dict(cls_params or {}),
    )


def _good_features(**overrides):
    f = dict(
        symbol="BTC",
        mid=100.0,
        best_bid=99.99,
        best_ask=100.01,
        spread_bps=2.0,
        obi_5=0.7,
        trade_imbalance_10s=0.4,
        microprice_pressure=0.0002,
        vwap_slope_5_30=0.0001,
        r_5s=0.0001,
        rv_30s=0.0005,
        buy_volume_usd_30s=2000.0,
        sell_volume_usd_30s=500.0,
        pressure_score_raw=0.8,
        book_flow_divergence=0.6,
        absorption_buy_proxy=0.0005,
        absorption_sell_proxy=0.0,
        enough_data=True,
        book_stale=False,
    )
    f.update(overrides)
    return f


def test_research_only_never_trades():
    s = SecondsResearchStrategy(_cfg("R"))
    out = s.on_second_features("BTC", _good_features(), time.time())
    assert out is None


def test_pressure_scalper_no_trade_when_not_enough_data():
    s = AlphaPressureScalper(_cfg("P"))
    f = _good_features(enough_data=False)
    assert s.on_second_features("BTC", f, time.time()) is None


def test_pressure_scalper_no_trade_when_book_stale():
    s = AlphaPressureScalper(_cfg("P"))
    f = _good_features(book_stale=True)
    assert s.on_second_features("BTC", f, time.time()) is None


def test_pressure_scalper_no_trade_when_spread_too_wide():
    s = AlphaPressureScalper(_cfg("P", {"max_spread_bps": 1.0}))
    f = _good_features(spread_bps=20.0)
    assert s.on_second_features("BTC", f, time.time()) is None


def test_pressure_scalper_no_trade_when_rv_too_high():
    s = AlphaPressureScalper(_cfg("P", {"max_rv_30s": 0.0001}))
    f = _good_features(rv_30s=0.01)
    assert s.on_second_features("BTC", f, time.time()) is None


def test_pressure_scalper_emits_decision_with_sl_tp():
    # Wide enough threshold + small cost to allow a long trade.
    s = AlphaPressureScalper(_cfg("P", {
        "threshold": 0.1, "cost_bps": 1.0, "margin_bps": 0.5,
        "take_profit_bps": 50.0, "stop_loss_bps": 25.0,
    }))
    f = _good_features(pressure_score_raw=0.9)
    d = s.on_second_features("BTC", f, time.time())
    assert d is not None
    assert d.action == "PLACE_BUY"
    assert d.stop_loss is not None and d.take_profit is not None
    assert d.stop_loss < d.take_profit


def test_pressure_scalper_disabled_returns_none():
    s = AlphaPressureScalper(_cfg("P", enabled=False))
    s._enabled = False
    out = s.on_second_features("BTC", _good_features(), time.time())
    assert out is None


def test_book_flow_divergence_requires_disagreement():
    s = BookFlowDivergenceReversal(_cfg("D", {
        "threshold": 0.1, "positive_ti": 0.1, "negative_ti": -0.1,
        "cost_bps": 1.0, "margin_bps": 0.5,
        "take_profit_bps": 50.0, "stop_loss_bps": 25.0,
    }))
    # ti positive (buyer agg) AND obi <= 0 → long signal
    f = _good_features(trade_imbalance_10s=0.5, obi_5=-0.2)
    d = s.on_second_features("BTC", f, time.time())
    assert d is not None
    assert d.action == "PLACE_BUY"


def test_absorption_reversal_buys_on_buy_absorption():
    s = AbsorptionReversal(_cfg("A", {
        "threshold": 1e-9, "cost_bps": 1.0, "margin_bps": 0.5,
        "take_profit_bps": 50.0, "stop_loss_bps": 25.0,
    }))
    f = _good_features(absorption_buy_proxy=1e-3, absorption_sell_proxy=0.0)
    d = s.on_second_features("BTC", f, time.time())
    assert d is not None
    assert d.action == "PLACE_BUY"
