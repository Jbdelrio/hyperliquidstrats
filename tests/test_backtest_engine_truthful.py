"""
test_backtest_engine_truthful.py — PHASE 1 : sorties intrabar, funding, liquidation.

Vérifie le moteur véridique sur 5 cas isolés avec une stratégie "one-shot" qui
ouvre une seule position sur la première barre, puis laisse le moteur gérer la
sortie via le high/low des barres suivantes.
"""
from __future__ import annotations

import pytest

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision
from backtesting.backtest_engine import BacktestEngine


class _OneShot(BaseStrategy):
    """Ouvre une position sur la 1ère barre selon config.params, puis rien."""
    def __init__(self, config, **kw):
        super().__init__(config, **kw)
        self._fired = False

    def on_orderbook_update(self, symbol, book, ts): return None
    def on_trade_update(self, symbol, trade, ts): return None

    def on_bar_minute(self, symbol, bar, ts):
        if self._fired:
            return None
        self._fired = True
        p = self.config.params
        return StrategyDecision(
            action=p.get("action", "PLACE_BUY"), symbol=symbol,
            notional_usd=p["notional"], stop_loss=p.get("sl", 0.0),
            take_profit=p.get("tp", 0.0), max_hold_seconds=p.get("max_hold", 0),
            metadata={"leverage": p.get("leverage", 1.0)},
        )


def _bar(ts, o, h, l, c):
    return BarData(symbol="X", ts=float(ts), open=o, high=h, low=l, close=c,
                   volume_usd=1e6, return_1m=0.0)


def _cfg(**params):
    return StrategyConfig(name="OneShot", coins=["X"], max_positions=1,
                          max_position_size_usd=params.get("notional", 1000),
                          params=params)


SLIP = 4.0 / 10_000.0
FEE = 3.0 / 10_000.0


def test_stop_traversed_intrabar_but_close_above():
    """Le low de la barre traverse le stop alors que le close reste au-dessus :
    sortie AU stop, pas au close."""
    bars = [_bar(0, 100, 100, 100, 100),         # entrée @ close=100
            _bar(60, 100, 100.5, 98.5, 100.2)]   # low 98.5 < sl, close 100.2 > sl
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, sl=99.0), bars)
    trades = eng.run()
    assert len(trades) == 1
    t = trades[0]
    assert t["reason"] == "stop_loss"
    entry = 100 * (1 + SLIP)
    assert t["exit"] == pytest.approx(99.0 * (1 - SLIP), rel=1e-9)
    # le PnL utilise bien le niveau de stop, pas le close 100.2
    assert t["exit"] < 100.2


def test_tp_and_stop_same_bar_pessimistic_stop_first():
    """Stop ET TP dans la même barre → règle pessimiste : stop d'abord."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(60, 100, 101.0, 98.5, 100.0)]   # high≥tp(101) ET low≤sl(99)
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, sl=99.0, tp=101.0), bars)
    trades = eng.run()
    assert len(trades) == 1
    assert trades[0]["reason"] == "stop_loss"


def test_take_profit_when_only_tp_in_range():
    """Seul le TP est traversé → take_profit au niveau du TP."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(60, 100, 101.5, 99.8, 101.2)]   # high≥tp, low ne touche pas sl
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, sl=99.0, tp=101.0), bars)
    trades = eng.run()
    assert trades[0]["reason"] == "take_profit"
    assert trades[0]["exit"] == pytest.approx(101.0 * (1 - SLIP), rel=1e-9)


def test_funding_accrued_over_boundaries():
    """3 frontières de funding pendant la détention (long, f>0) → le long PAIE."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(100, 100, 100.1, 99.9, 100.0),
            _bar(400, 100, 100.1, 99.9, 100.0)]  # pas de stop/tp → flush au dernier close
    # frontières à t=50,150,250 (toutes dans [entry_ts=0, exit_ts]); rate 1e-4
    funding = {"X": [(50, 1e-4), (150, 1e-4), (250, 1e-4), (9999, 1e-4)]}
    eng = BacktestEngine(_OneShot, _cfg(notional=1000), bars, funding_by_symbol=funding)
    trades = eng.run()
    t = trades[0]
    # 3 frontières franchies (50,150,250 ; 9999 hors fenêtre), long paie f>0
    assert t["funding"] == pytest.approx(-3 * 1e-4 * 1000, rel=1e-9)  # -0.30


def test_intrabar_liquidation():
    """Position 5x sans stop ; le low franchit le prix de liquidation → perte = marge."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(60, 100, 100.0, 70.0, 95.0)]    # chute intrabar à 70
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, leverage=5.0), bars)
    trades = eng.run()
    t = trades[0]
    assert t["reason"] == "liquidation"
    assert t["leverage"] == 5.0
    assert t["margin"] == pytest.approx(200.0, rel=1e-9)            # 1000/5
    assert t["gross"] == pytest.approx(-200.0, rel=1e-9)           # marge perdue
    # net = -marge - frais d'entrée (3 bps × notional) = -200 - 0.30
    assert t["net"] == pytest.approx(-200.0 - FEE * 1000, rel=1e-6)


def test_stop_precedes_liquidation():
    """Si un stop est posé au-dessus du prix de liquidation, il est servi d'abord."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(60, 100, 100.0, 70.0, 95.0)]
    # sl=90 > liq_price(≈80.5 à 5x) → stop d'abord
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, leverage=5.0, sl=90.0), bars)
    trades = eng.run()
    assert trades[0]["reason"] == "stop_loss"


def test_no_leverage_consistency():
    """Sans levier ni stop/tp : sortie au flush (dernier close), pas de liquidation."""
    bars = [_bar(0, 100, 100, 100, 100),
            _bar(60, 100, 102, 99, 101)]          # monte, pas de niveau touché
    eng = BacktestEngine(_OneShot, _cfg(notional=1000, leverage=1.0), bars)
    trades = eng.run()
    t = trades[0]
    assert t["reason"] == "eob_flush"
    assert t["leverage"] == 1.0
    # gross > 0 (le prix est monté de 100 à 101) malgré le slippage
    assert t["gross"] > 0
    entry = 100 * (1 + SLIP)
    exit_eff = 101 * (1 - SLIP)
    assert t["gross"] == pytest.approx((exit_eff - entry) / entry * 1000, rel=1e-6)
