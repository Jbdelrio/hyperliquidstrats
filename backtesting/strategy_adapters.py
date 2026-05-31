"""
backtesting/strategy_adapters.py — adaptateurs run_fn pour le harnais walk-forward.

Branche les vrais signaux de stratégie sur le BacktestEngine véridique (PHASE 1)
et les données historiques (PHASE 0), en exposant la signature attendue par
walkforward.evaluate_strategy :  run_fn(params, coin, fee_bps, slippage_bps) -> trades.

Règle ZEC respectée : le breakout est rejoué en **time-stop pur** (pas de stop
intrabar), levier passé en metadata pour la modélisation de liquidation. On ne
touche ni au fichier strategies/hourly_breakout.py ni à sa config live.
"""
from __future__ import annotations

from typing import Optional

from strategies.base_strategy import BaseStrategy, StrategyConfig, StrategyDecision
from strategies.hourly_breakout import HourlyBreakoutStrategy
from strategies.trend_following_vol_target import TrendFollowingVolTargetStrategy
from backtesting.backtest_engine import BacktestEngine
from backtesting import data_loader

_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class _BarBreakout(BaseStrategy):
    """Breakout de canal sur barres NATIVES (zéro agrégation interne).
    Réutilise HourlyBreakoutStrategy.breakout_signal. Time-stop pur."""

    def __init__(self, config: StrategyConfig, **kw):
        super().__init__(config, **kw)
        p = config.params
        self._period = int(p["donchian_period"])
        self._hold_s = int(p["hold_bars"]) * int(p.get("bar_seconds", 3600))
        self._min_atr = float(p.get("min_atr_bps", 0.0))
        self._min_cost_ratio = float(p.get("min_cost_ratio", 0.0))
        self._cost_bps = float(p.get("cost_bps_rt", 14.0))
        self._both = bool(p.get("both_directions", True))
        self._lev = float(p.get("leverage", 1.0))
        self._notional = float(p.get("notional", 1000.0))
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._in_pos = False

    def on_orderbook_update(self, symbol, book, ts): return None
    def on_trade_update(self, symbol, trade, ts): return None

    @staticmethod
    def _atr_bps(highs, lows, closes, n=14):
        if len(closes) < n + 1:
            return 0.0
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                   abs(lows[i] - closes[i - 1])) for i in range(len(closes) - n, len(closes))]
        atr = sum(trs) / len(trs)
        return (atr / closes[-1] * 1e4) if closes[-1] > 0 else 0.0

    def on_bar_minute(self, symbol, bar, ts) -> Optional[StrategyDecision]:
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        if self._in_pos or len(self._closes) < self._period + 1:
            return None
        sig = HourlyBreakoutStrategy.breakout_signal(self._closes, self._period)
        if sig == 0 or (sig < 0 and not self._both):
            return None
        if self._atr_bps(self._highs, self._lows, self._closes) < self._min_atr:
            return None
        window = self._closes[-(self._period + 1):-1]
        ch_lo, ch_hi = min(window), max(window)
        if ch_lo > 0:
            range_bps = (ch_hi - ch_lo) / ch_lo * 1e4
            if range_bps < self._min_cost_ratio * self._cost_bps:
                return None
        action = "PLACE_BUY" if sig > 0 else "PLACE_SELL"
        return StrategyDecision(
            action=action, symbol=symbol,
            buy_price=bar.close if sig > 0 else None,
            sell_price=bar.close if sig < 0 else None,
            notional_usd=self._notional,
            max_hold_seconds=self._hold_s,           # time-stop pur (miroir ZEC)
            reason=f"bt_breakout_{'long' if sig>0 else 'short'}",
            metadata={"leverage": self._lev},        # pour la liquidation intrabar
        )

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._in_pos = True
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._in_pos = False
        super().on_position_closed(symbol, pnl_net, exit_reason)


def breakout_run_fn(interval: str = "1h", leverage: float = 5.0):
    """Construit un run_fn(params, coin, fee_bps, slip_bps) -> trades, qui rejoue
    le breakout sur les barres historiques `interval` via le moteur véridique
    (funding accru, liquidation intrabar)."""
    bar_seconds = _INTERVAL_SECONDS[interval]

    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        try:
            bars = data_loader.load_historical_bars(coin, interval)
        except FileNotFoundError:
            return []
        if len(bars) < 60:
            return []
        funding = {coin: data_loader.load_funding_series(coin)}
        cfg = StrategyConfig(
            name=f"bt_breakout_{coin}", coins=[coin], max_positions=1,
            max_position_size_usd=params.get("notional", 1000.0),
            params={**params, "bar_seconds": bar_seconds, "leverage": leverage,
                    "cost_bps_rt": 2.0 * (fee_bps + slip_bps)},
        )
        eng = BacktestEngine(_BarBreakout, cfg, bars,
                             fee_bps=fee_bps, slippage_bps=slip_bps,
                             funding_by_symbol=funding)
        return eng.run()
    return run_fn


class _EmaTrend(BaseStrategy):
    """Suivi de tendance EMA-cross sur barres natives (réutilise trend_sign).
    À plat → entre dans le sens de la tendance ; en position → retourne au flip."""

    def __init__(self, config: StrategyConfig, **kw):
        super().__init__(config, **kw)
        p = config.params
        self._fast = int(p["ema_fast"]); self._slow = int(p["ema_slow"])
        self._hold_s = int(p.get("max_hold_bars", 60)) * int(p.get("bar_seconds", 14400))
        self._min_atr = float(p.get("min_atr_bps", 20.0))
        self._notional = float(p.get("notional", 1000.0))
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._side: Optional[str] = None

    def on_orderbook_update(self, s, b, t): return None
    def on_trade_update(self, s, tr, t): return None

    def on_bar_minute(self, symbol, bar, ts):
        self._closes.append(bar.close); self._highs.append(bar.high); self._lows.append(bar.low)
        if len(self._closes) < self._slow:
            return None
        sign = TrendFollowingVolTargetStrategy.trend_sign(self._closes, self._fast, self._slow)
        if self._side is not None:
            flip = (sign < 0 and self._side == "BUY") or (sign > 0 and self._side == "SELL")
            if flip:
                return StrategyDecision(action="CLOSE", symbol=symbol, reason="trend_flip")
            return None
        atr = TrendFollowingVolTargetStrategy.atr_bps(self._highs, self._lows, self._closes)
        if sign == 0 or atr < self._min_atr:
            return None
        if sign > 0:
            return StrategyDecision(action="PLACE_BUY", symbol=symbol, buy_price=bar.close,
                                    notional_usd=self._notional, max_hold_seconds=self._hold_s,
                                    reason="ema_long", metadata={"leverage": 1.0})
        return StrategyDecision(action="PLACE_SELL", symbol=symbol, sell_price=bar.close,
                                notional_usd=self._notional, max_hold_seconds=self._hold_s,
                                reason="ema_short", metadata={"leverage": 1.0})

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._side = side
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._side = None
        super().on_position_closed(symbol, pnl_net, exit_reason)


def ema_cross_run_fn(interval: str = "4h"):
    """run_fn EMA-cross trend-following sur barres `interval` via le moteur véridique."""
    bar_seconds = _INTERVAL_SECONDS[interval]

    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        try:
            bars = data_loader.load_historical_bars(coin, interval)
        except FileNotFoundError:
            return []
        if len(bars) < int(params.get("ema_slow", 30)) + 10:
            return []
        funding = {coin: data_loader.load_funding_series(coin)}
        cfg = StrategyConfig(
            name=f"bt_ema_{coin}", coins=[coin], max_positions=1,
            max_position_size_usd=params.get("notional", 1000.0),
            params={**params, "bar_seconds": bar_seconds},
        )
        eng = BacktestEngine(_EmaTrend, cfg, bars, fee_bps=fee_bps,
                             slippage_bps=slip_bps, funding_by_symbol=funding)
        return eng.run()
    return run_fn
