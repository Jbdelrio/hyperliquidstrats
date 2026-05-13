"""
tests/test_backtesting.py — Phase 7 backtesting skeleton tests.

Covers metrics.compute_metrics(), data_loader.load_fills_as_trades(),
and a minimal BacktestEngine smoke pass.
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtesting.metrics import compute_metrics
from backtesting.data_loader import load_fills_as_trades, load_ohlcv


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metrics_empty_trades():
    m = compute_metrics([])
    assert m["n_trades"] == 0
    assert m["total_pnl"] == 0.0
    assert m["win_rate"] == 0.0


def test_metrics_basic_aggregation():
    trades = [
        {"ts": 1.0, "symbol": "BTC", "strategy": "MomentumLS",
         "side": "BUY", "notional": 100, "entry": 100, "exit": 102,
         "gross": 2.0, "fee": 0.5, "net": 1.5,
         "hold_s": 120, "reason": "take_profit"},
        {"ts": 2.0, "symbol": "ETH", "strategy": "MomentumLS",
         "side": "BUY", "notional": 100, "entry": 100, "exit": 99,
         "gross": -1.0, "fee": 0.5, "net": -1.5,
         "hold_s": 60, "reason": "stop_loss"},
        {"ts": 3.0, "symbol": "BTC", "strategy": "BreakoutControlled",
         "side": "SELL", "notional": 200, "entry": 100, "exit": 98,
         "gross": 4.0, "fee": 1.0, "net": 3.0,
         "hold_s": 240, "reason": "take_profit"},
    ]
    m = compute_metrics(trades)
    assert m["n_trades"] == 3
    assert m["total_pnl"] == pytest.approx(3.0, abs=1e-6)
    assert m["win_rate"] == pytest.approx(66.67, abs=0.1)
    assert m["pnl_by_symbol"]["BTC"]  == pytest.approx(4.5, abs=1e-6)
    assert m["pnl_by_symbol"]["ETH"]  == pytest.approx(-1.5, abs=1e-6)
    assert m["pnl_by_strategy"]["MomentumLS"] == pytest.approx(0.0, abs=1e-6)
    assert m["pnl_by_strategy"]["BreakoutControlled"] == pytest.approx(3.0, abs=1e-6)
    assert m["exit_reason_dist"]["take_profit"] == 2
    assert m["exit_reason_dist"]["stop_loss"]   == 1
    assert m["profit_factor"] > 0


def test_metrics_max_drawdown():
    """Equity: +10, -20, +5 → peak after t1 = 10, trough = -10 → DD = 20."""
    trades = [
        {"ts": 1, "net": 10},
        {"ts": 2, "net": -20},
        {"ts": 3, "net": 5},
    ]
    m = compute_metrics(trades)
    assert m["max_drawdown"] == pytest.approx(20.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

def test_load_fills_missing_returns_empty(tmp_path):
    p = tmp_path / "missing.csv"
    assert load_fills_as_trades(str(p)) == []


def test_load_fills_parses_csv(tmp_path):
    p = tmp_path / "fills_test.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "symbol", "side", "notional",
                    "entry", "exit", "gross", "fee", "net",
                    "hold_s", "reason", "strategy", "slippage_bps"])
        w.writerow(["2026-05-13T10:00:00", "BTC", "BUY", "100",
                    "50000", "50500", "1.0", "0.3", "0.7",
                    "120", "take_profit", "MomentumLS", "3.5"])
        w.writerow(["1747000000", "ETH", "SELL", "100",
                    "3000", "2970", "1.0", "0.3", "0.7",
                    "60", "take_profit", "BreakoutControlled", ""])

    trades = load_fills_as_trades(str(p))
    assert len(trades) == 2
    assert trades[0]["symbol"] == "BTC"
    assert trades[0]["strategy"] == "MomentumLS"
    assert trades[0]["net"] == pytest.approx(0.7, abs=1e-6)
    assert trades[0]["slippage_bps"] == pytest.approx(3.5, abs=1e-6)
    assert trades[0]["ts"] > 0
    # Numeric epoch row
    assert trades[1]["ts"] == pytest.approx(1747000000.0, abs=1.0)


def test_load_ohlcv_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        load_ohlcv("BTC", "1m", 0, 0)


# ---------------------------------------------------------------------------
# BacktestEngine smoke
# ---------------------------------------------------------------------------

def test_backtest_engine_smoke():
    """Minimal bar-replay: feed bars to a stub strategy, get trades back."""
    from strategies.base_strategy import (
        BaseStrategy, StrategyConfig, StrategyDecision, BarData,
    )
    from backtesting.backtest_engine import BacktestEngine

    class _Stub(BaseStrategy):
        def __init__(self, cfg):
            super().__init__(cfg)
            self._opened = False
        def on_orderbook_update(self, *_a, **_kw): return None
        def on_trade_update(self, *_a, **_kw): return None
        def on_bar_minute(self, symbol, bar, ts):
            # Open one long on the first bar, ride it for a few bars
            if not self._opened:
                self._opened = True
                return StrategyDecision(
                    action="PLACE_BUY", symbol=symbol,
                    notional_usd=100.0,
                    take_profit=bar.close * 1.02,
                    stop_loss=bar.close * 0.99,
                    max_hold_seconds=600,
                )
            return None

    cfg = StrategyConfig(
        name="Stub", enabled=True,
        capital_allocated_usd=500, max_positions=1,
        max_position_size_usd=100, coins=["BTC"], params={},
    )
    bars = []
    base = 100.0
    for i in range(20):
        # Rising price — should hit TP
        c = base * (1.0 + i * 0.003)
        bars.append(BarData(
            symbol="BTC", ts=i * 60.0,
            open=c, high=c * 1.001, low=c * 0.999, close=c,
            volume_usd=1000.0, return_1m=0.003,
        ))

    engine = BacktestEngine(_Stub, cfg, bars)
    trades = engine.run()
    assert len(trades) >= 1
    # Either TP hit or flush at end — both record net & a reason
    assert all("reason" in t for t in trades)
    m = engine.metrics()
    assert m["n_trades"] >= 1
