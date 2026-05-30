"""
hourly_breakout.py — 1-hour range-breakout, both directions (validated edge).

This productionises the one bar-level edge that survived walk-forward on the HL
top-20 (see reports/hl_top20_behavior.md): a 1-hour close-break of the prior
`donchian_period` hourly closes, held for a few hours, on high-volatility coins
(ZEC strongest, also ETH / HYPE / WLD / XLM), net of cost.

Why a new class and not DonchianTrend / VolatilityRegimeBreakout:
  * DonchianTrend breaks on 15m bars and is long-only.
  * VolatilityRegimeBreakout breaks on raw 1m bars (≈20-min channel).
  * The validated signal is a 1-HOUR channel, BOTH directions, multi-hour hold.

The core rule lives in `breakout_signal()` (a staticmethod) so the backtest
(`scripts/backtest_hourly_breakout.py`) replays the EXACT same logic on the
cached 1h candles — no logic drift between research and production.

Execution: aggregates the engine's 1-minute bars up to `tf_minutes` (60),
fires on each completed hourly bar, exits on a time stop (`max_hold_hours`) with
an optional liquidation-aware protective stop when leverage is configured.
Paper-only until the live track record matches.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision
from strategies.leader_bias import LeaderBias

log = logging.getLogger(__name__)


@dataclass
class _Pos:
    pos_id: str
    side: str           # "BUY" | "SELL"
    entry: float
    opened_ts: float
    max_hold_ts: float


class HourlyBreakoutStrategy(BaseStrategy):
    """1h close-break, both directions, vol- and cost-gated. See module docstring."""

    DEFAULT_PARAMS = dict(
        tf_minutes=60,                 # channel timeframe (1h)
        donchian_period=20,            # lookback in hourly bars (the validated 20)
        max_hold_hours=4,              # time stop (the validated hold)
        both_directions=True,          # allow shorts

        # Volatility gate — only trade when the hourly move clears the cost.
        # atr_bps measured over the channel; require >= min_atr_bps.
        min_atr_bps=25.0,
        # Cost gate: channel range (bps) must be >= min_cost_ratio × round-trip.
        cost_bps_rt=6.0,
        min_cost_ratio=2.0,

        cooldown_minutes=60,           # min minutes between entries per coin

        # Warmup backfill: seed the hourly aggregator from cached HL candles at
        # startup so the strategy is ready IMMEDIATELY instead of waiting
        # ~(period+16) hours of live data. Uses data/processed/hl_candles_<tf>.
        warmup_from_parquet=True,

        # ── Sizing ──────────────────────────────────────────────────────
        # Default: notional = capital/max_positions capped at max_position_size.
        # Leverage mode: set margin_usd + leverage → notional = margin×lev, with
        # a liquidation-aware protective stop. tp/sl as fraction of price.
        margin_usd=None,
        leverage=None,
        maint_margin_frac=0.5,
        liq_safety=0.6,
        # Optional fixed protective levels (fractions). None → pure time stop
        # (matches the validation, which had no intrabar stop).
        stop_loss_pct=None,
        take_profit_pct=None,
        maker_only=True,

        # ── Leader-bias filter (free directional confirmation) ──────────
        # When on, veto a breakout that fights a strong BTC/ETH move. The
        # leader symbols must be in `coins` so the engine feeds their bars;
        # they are tracked but never traded by this strategy.
        leader_bias_enabled=False,
        leader_symbols=("BTC", "ETH"),
        leader_window_hours=4,         # leader return measured over this window
        leader_min_bps=40.0,           # a leader move counts as "strong" above this
        leader_mode="veto_opposite",   # veto_opposite | require_agree | require_all
    )

    def _build_leader(self):
        p = self.config.params
        if not p["leader_bias_enabled"]:
            return None, []
        syms = [s for s in p["leader_symbols"] if s in self.config.coins]
        if not syms:
            log.warning("HourlyBreakout %s: leader_bias on but no leader symbol "
                        "in coins %s — filter inert", self.name, self.config.coins)
            return None, []
        return LeaderBias(syms, window=int(p["leader_window_hours"])), syms

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        p = config.params

        self._tf = int(p["tf_minutes"])
        self._period = int(p["donchian_period"])
        self._max_hold_s = float(p["max_hold_hours"]) * 3600.0
        self._cooldown_s = float(p["cooldown_minutes"]) * 60.0

        self._agg: dict[str, BarAggregator] = {
            c: BarAggregator(c, self._tf, maxlen=max(300, self._period + 50))
            for c in config.coins
        }
        self._pos: dict[str, _Pos] = {}
        self._last_entry_ts: dict[str, float] = {c: 0.0 for c in config.coins}

        # Leader-bias filter (None when disabled). `_leader_syms` are tracked
        # but never traded.
        self._leader, self._leader_syms = self._build_leader()

        # Seed the aggregators from cached candles so we trade without waiting
        # ~(period+16)h of live data.
        if p["warmup_from_parquet"]:
            self._seed_warmup()

    def _seed_warmup(self) -> None:
        """Preload completed hourly bars from data/processed/hl_candles_<tf>.parquet
        into each coin's aggregator (trading coins AND leaders), so breakout and
        leader-bias are usable immediately. Best-effort: silent on any miss."""
        tf_file = {60: "hl_candles_1h", 15: "hl_candles_15m", 1: "hl_candles_1m"}.get(self._tf)
        if tf_file is None:
            return
        path = Path(__file__).resolve().parents[1] / "data" / "processed" / f"{tf_file}.parquet"
        if not path.exists():
            return
        try:
            import pandas as pd
            df = pd.read_parquet(path, columns=["ts_open", "symbol", "o", "h", "l", "c", "v"])
        except Exception as e:
            log.warning("HourlyBreakout %s: warmup parquet read failed: %s", self.name, e)
            return
        need = self._period + 30
        for coin, agg in self._agg.items():
            g = df[df["symbol"] == coin].sort_values("ts_open").tail(need)
            seeded = 0
            for row in g.itertuples(index=False):
                try:
                    c = float(row.c)
                    if c <= 0:
                        continue
                    bar = BarData(symbol=coin, ts=float(row.ts_open) / 1000.0,
                                  open=float(row.o), high=float(row.h), low=float(row.l),
                                  close=c, volume_usd=float(row.v) * c, return_1m=0.0)
                    agg._bars.append(bar)            # seed completed bar directly
                    seeded += 1
                except Exception:
                    continue
            if seeded and coin in self._leader_syms and self._leader is not None:
                lw = int(self.config.params["leader_window_hours"]) + 1
                for b in list(agg._bars)[-lw:]:
                    self._leader.update(coin, b.close)
            if seeded:
                log.info("HourlyBreakout %s: warmup-seeded %s with %d %dm bars",
                         self.name, coin, seeded, self._tf)

    # ── shared signal (used by strategy AND backtest) ────────────────────

    @staticmethod
    def breakout_signal(closes: list[float], period: int) -> int:
        """+1 long / -1 short / 0 none. Break of prior `period` closes by the
        latest close. `closes` includes the current bar as the last element."""
        if len(closes) < period + 1:
            return 0
        window = closes[-(period + 1):-1]      # prior `period` closes, excl. current
        c = closes[-1]
        if c > max(window):
            return 1
        if c < min(window):
            return -1
        return 0

    @staticmethod
    def _atr_bps(highs: list[float], lows: list[float], closes: list[float],
                 n: int = 14) -> float:
        if len(closes) < n + 1:
            return 0.0
        trs = []
        for i in range(len(closes) - n, len(closes)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr = sum(trs) / len(trs)
        c = closes[-1]
        return (atr / c * 1e4) if c > 0 else 0.0

    # ── BaseStrategy interface ───────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": False, "seconds_features": False,
            "bars": ["1m"], "funding": False, "external_spot": False,
            "warmup_bars": {"1m": (self._period + 16) * self._tf},
        }

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float
                      ) -> Optional[StrategyDecision]:
        agg = self._agg.get(symbol)
        if agg is None:
            return None
        completed = agg.update(bar)
        if completed is None:
            return None                          # not a new hourly bar yet

        # Leader symbols: update the bias tracker on each completed hourly bar
        # and never trade them.
        if symbol in self._leader_syms:
            if self._leader is not None:
                self._leader.update(symbol, completed.close)
            return None

        if symbol in self._pos:
            return None
        if ts - self._last_entry_ts.get(symbol, 0.0) < self._cooldown_s:
            return None
        if len(self._pos) >= int(self.config.max_positions):
            return None

        closes = agg.closes()
        highs = agg.highs()
        lows = agg.lows()
        if len(closes) < self._period + 1:
            return None

        p = self.config.params
        sig = self.breakout_signal(closes, self._period)
        if sig == 0:
            return None
        if sig < 0 and not p["both_directions"]:
            return None

        # Leader-bias gate: veto a breakout that fights a strong BTC/ETH move.
        if self._leader is not None:
            if not self._leader.passes(sig, float(p["leader_min_bps"]),
                                       str(p["leader_mode"])):
                return None

        # Volatility gate
        atr_bps = self._atr_bps(highs, lows, closes, 14)
        if atr_bps < float(p["min_atr_bps"]):
            return None

        # Cost gate on the channel width
        window = closes[-(self._period + 1):-1]
        ch_hi, ch_lo = max(window), min(window)
        range_bps = (ch_hi - ch_lo) / ch_lo * 1e4 if ch_lo > 0 else 0.0
        if range_bps < float(p["min_cost_ratio"]) * float(p["cost_bps_rt"]):
            return None

        close = closes[-1]
        # Sizing
        use_lev = p["margin_usd"] is not None and p["leverage"] is not None
        if use_lev:
            L = float(p["leverage"])
            notional = min(float(p["margin_usd"]) * L,
                           float(self.config.max_position_size_usd))
            liq_frac = max(1e-6, (1.0 - float(p["maint_margin_frac"])) / L)
            default_sl_frac = float(p["liq_safety"]) * liq_frac
        else:
            notional = min(self.config.capital_allocated_usd / max(self.config.max_positions, 1),
                           float(self.config.max_position_size_usd))
            default_sl_frac = None

        slp = p["stop_loss_pct"]
        tpp = p["take_profit_pct"]
        sl_frac = float(slp) if slp is not None else default_sl_frac
        tp_frac = float(tpp) if tpp is not None else None

        if sig > 0:
            action = "PLACE_BUY"
            buy_price, sell_price = close, None
            stop = close * (1.0 - sl_frac) if sl_frac else None
            tp = close * (1.0 + tp_frac) if tp_frac else None
        else:
            action = "PLACE_SELL"
            buy_price, sell_price = None, close
            stop = close * (1.0 + sl_frac) if sl_frac else None
            tp = close * (1.0 - tp_frac) if tp_frac else None

        reason = (f"h1_breakout_{'long' if sig > 0 else 'short'}|close={close:.5g}|"
                  f"ch=[{ch_lo:.5g},{ch_hi:.5g}]|atr={atr_bps:.0f}bps"
                  + (f"|L={float(p['leverage']):.0f}x" if use_lev else ""))
        return StrategyDecision(
            action=action, symbol=symbol, reason=reason,
            buy_price=buy_price, sell_price=sell_price,
            notional_usd=notional, stop_loss=stop, take_profit=tp,
            max_hold_seconds=int(self._max_hold_s),
            confidence=0.5, expected_edge_bps=range_bps,
            estimated_cost_bps=float(p["cost_bps_rt"]),
            order_type="MAKER_SIM" if p["maker_only"] else "TAKER_SIM",
            strategy_family="hourly_breakout",
            metadata={"atr_bps": atr_bps, "channel_hi": ch_hi, "channel_lo": ch_lo},
        )

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._pos[symbol] = _Pos(pos_id=pos_id, side=side, entry=price,
                                 opened_ts=ts, max_hold_ts=ts + self._max_hold_s)
        self._last_entry_ts[symbol] = ts
        return None

    def check_position_exits(self, symbol, book, ts):
        pos = self._pos.get(symbol)
        if pos is None or ts < pos.max_hold_ts:
            return None
        return StrategyDecision(
            action="CLOSE", symbol=symbol,
            reason=f"h1_time_exit|hold_h={(ts - pos.opened_ts)/3600:.1f}",
            metadata={"pos_id": pos.pos_id})

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._pos.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        agg = self._agg.get(symbol)
        if agg is None:
            return {}
        closes = agg.closes()
        ready = len(closes) >= self._period + 1
        sig = self.breakout_signal(closes, self._period) if ready else 0
        atr_bps = self._atr_bps(agg.highs(), agg.lows(), closes, 14) if ready else 0.0
        return {
            "tf_minutes": self._tf,
            "bars_1h": len(closes),
            "ready": ready,
            "signal": sig,
            "atr_bps": round(atr_bps, 1),
            "in_position": symbol in self._pos,
        }
