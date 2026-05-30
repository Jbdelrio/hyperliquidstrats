"""
funding_carry_hedged.py — Funding carry, three modes.

  hedged=true              → DELTA-NEUTRAL carry paper simulation.
                             Opens a perp leg AND an opposite spot leg
                             (spot = Hyperliquid oracle/index price), accrues
                             funding hour by hour, marks both legs to market,
                             and exits on funding normalisation / basis blow-out
                             / max hold. Fully self-contained: it emits NO
                             engine decisions, keeps its own book + PnL, and
                             logs realised trades to logs/funding_carry_paper.csv.
                             This is the real "spot leg wired" mode.

  allow_unhedged_perp=true → LEGACY directional perp-only carry (engine
                             decisions, single perp leg, price stop/TP).

  else                     → scanner (calibration only, no trades).

Why a self-contained sim for the hedged mode: a delta-neutral carry is a
2-leg market-neutral book whose PnL is dominated by funding accrual, not by
the directional move of either leg. The engine's executor models single
directional perp positions with price stop/TP — it cannot represent a paired
hedge. Simulating the pair inside the strategy keeps the carry honest (perp
PnL ≈ −spot PnL, funding is the edge) without distorting the directional
engine equity.
"""
import csv
import logging
import os
import time
from collections import deque
from typing import Optional

from data.hyperliquid_funding import (
    fetch_hyperliquid_funding_and_prices,
    fetch_hyperliquid_funding_rates,
)
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_DEFAULT_PAPER_LOG = "logs/funding_carry_paper.csv"
_LOG_COLUMNS = [
    "ts_open", "ts_close", "symbol", "direction", "notional",
    "perp_entry", "perp_exit", "spot_entry", "spot_exit", "hold_h",
    "perp_pnl", "spot_pnl", "funding_collected",
    "entry_fees", "exit_fees", "net_pnl", "exit_reason",
    "entry_funding_bps_h", "exit_funding_bps_h", "entry_mode",
]


class FundingCarryHedgedStrategy(BaseStrategy):
    """
    Net expected edge of the carry:
      E = |funding_bps_per_hour| × expected_hold_hours
          − round_trip_cost_bps(both legs) − safety_buffer_bps

    Positive funding → short perp + long spot  (short collects funding)
    Negative funding → long perp  + short spot (long collects funding)
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        p = config.params
        coins = config.coins

        # Funding state (hourly rate; kept for calibration + back-compat).
        self._funding_raw:  dict[str, Optional[float]] = {c: None for c in coins}
        self._funding_hist: dict[str, deque]           = {c: deque(maxlen=48) for c in coins}
        self._bar_closes:   dict[str, deque]           = {c: deque(maxlen=960) for c in coins}
        self._last_fetch:   float = 0.0

        # Spot/perp reference prices from metaAndAssetCtxs.
        self._prices:   dict[str, dict]            = {}
        self._perp_mid: dict[str, Optional[float]] = {c: None for c in coins}
        # Latest L2 book per coin (used for depth-aware slippage estimation).
        self._perp_book: dict[str, object] = {}

        # Legacy unhedged-perp engine positions.
        self._positions: dict[str, dict] = {}

        # Hedged-mode self-contained carry book.
        self._carry: dict[str, dict] = {}
        self._realized_pnl: float = 0.0
        self._n_trades:     int   = 0
        self._wins:         int   = 0
        self._used_notional: float = 0.0

        self._hedged = bool(p.get("hedged", False))
        self._paper_log_path = p.get("paper_log_path", _DEFAULT_PAPER_LOG)
        self._log_ready = False

    # ── data requirements ────────────────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {
            "orderbook": True, "trades": False, "seconds_features": False,
            "bars": ["1m"], "funding": True, "external_spot": True,
            "warmup_bars": {"1m": 3},
        }

    # ── BaseStrategy interface ───────────────────────────────────────────────

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        mid = getattr(book, "mid", None)
        if mid is None:
            bid, ask = book.best_bid, book.best_ask
            if bid is None or ask is None:
                return None
            mid = (bid + ask) / 2.0
        self._perp_mid[symbol] = mid
        self._perp_book[symbol] = book

        if self._hedged:
            return None  # hedged carry is managed entirely in on_bar_minute
        if symbol in self._positions:
            return self._check_exit(symbol, mid, ts)
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        self._bar_closes[symbol].append(bar.close)

        # Refresh funding + spot/perp prices once per cycle (first symbol of
        # the minute triggers it; the others reuse the cached snapshot).
        refresh_s = float(self.config.params.get("refresh_interval_s", 60))
        if ts - self._last_fetch >= refresh_s:
            self._refresh_funding(ts)
            self._last_fetch = ts

        if self._hedged:
            self._manage_carry(symbol, ts)
            return None

        return self._check_entry(symbol, bar, ts)

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        # Only used by the legacy unhedged-perp mode.
        p = self.config.params
        sl_pct      = p.get("stop_loss_pct", 0.02)
        tp_pct      = p.get("take_profit_pct", 0.012)
        max_hold_s  = int(p.get("max_hold_hours", 8) * 3600)

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "entry_funding": self._funding_raw.get(symbol),
            "pos_id":      pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold_s}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if self._hedged or symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    # ── Hedged delta-neutral carry sim ───────────────────────────────────────

    def _refresh_funding(self, ts: float) -> None:
        """Pull funding + oracle/mark prices for the universe."""
        try:
            data = fetch_hyperliquid_funding_and_prices(list(self._funding_raw.keys()))
        except Exception as exc:                       # pragma: no cover
            log.debug("FundingCarryHedged refresh failed: %s", exc)
            return
        if not data:
            # Fall back to the funding-only fetcher (keeps calibration alive).
            raw = fetch_hyperliquid_funding_rates(list(self._funding_raw.keys()))
            for coin, info in (raw or {}).items():
                if coin in self._funding_raw:
                    self._funding_raw[coin] = info["hourly_rate"]
                    self._funding_hist[coin].append(info["hourly_rate"])
            return
        for coin, info in data.items():
            if coin not in self._funding_raw:
                continue
            f = info["funding_hourly"]
            self._funding_raw[coin] = f
            self._funding_hist[coin].append(f)
            self._prices[coin] = info

    def _book_impact_bps(self, book, notional_usd: float, side: str) -> Optional[float]:
        """VWAP impact in bps for a market order of `notional_usd` on `book`.

        side='BUY'  walks asks  (we lift offers, pay above mid)
        side='SELL' walks bids  (we hit bids,    sell below mid)

        Returns None if the book is empty / not deep enough for `notional_usd`.
        """
        if book is None:
            return None
        levels = book.asks if side == "BUY" else book.bids
        mid = getattr(book, "mid", None)
        if not levels or not mid or mid <= 0:
            return None
        remaining = float(notional_usd)
        cost_usd = 0.0
        filled_size = 0.0
        for price, size in levels:
            if remaining <= 0:
                break
            lvl_notional = price * size
            take_notional = min(lvl_notional, remaining)
            if price <= 0:
                continue
            take_size = take_notional / price
            cost_usd   += take_size * price
            filled_size += take_size
            remaining  -= take_notional
        if remaining > 0 or filled_size <= 0:
            return None  # not enough depth — caller falls back to configured slip
        vwap = cost_usd / filled_size
        return abs(vwap - mid) / mid * 10_000.0

    def _estimate_perp_slippage_bps(self, coin: str, notional_usd: float) -> Optional[float]:
        """Round-trip-symmetric depth estimate: average of buy + sell side impact
        for `notional_usd` against the latest cached L2 book for `coin`."""
        book = self._perp_book.get(coin)
        if book is None:
            return None
        buy  = self._book_impact_bps(book, notional_usd, "BUY")
        sell = self._book_impact_bps(book, notional_usd, "SELL")
        if buy is None or sell is None:
            return None
        return (buy + sell) / 2.0

    def _leg_cost_bps(self, coin: Optional[str] = None,
                      notional_usd: Optional[float] = None) -> float:
        """One-direction cost across BOTH legs (perp + spot), in bps.

        Two execution modes via params["entry_mode"]:

        - "taker" (default): pay taker fee + slippage on both legs. If `coin`
          and `notional_usd` are given and a cached L2 book exists, the perp
          slippage is depth-aware (microstructure); the configured floor is a
          lower bound so we never under-estimate reality.

        - "maker": post-only on both legs. Pay maker fees (much lower) but
          carry a `maker_no_fill_penalty_bps` charge to account for the EV
          cost of unfilled quotes (you sometimes have to cross to complete
          the hedge). This is the honest paper-side proxy for fill risk —
          a real fill simulator is out of scope for this layer.
        """
        p = self.config.params
        mode = str(p.get("entry_mode", "taker")).lower()

        if mode == "maker":
            perp_fee = float(p.get("maker_perp_fee_bps", 1.5))
            spot_fee = float(p.get("maker_spot_fee_bps", 4.0))
            # Per-LEG share of the round-trip no-fill penalty. _leg_cost_bps is
            # called twice (open + close), so dividing by 2 here gives the
            # configured penalty as a round-trip total.
            no_fill_per_leg = float(p.get("maker_no_fill_penalty_bps", 5.0)) / 2.0
            return perp_fee + spot_fee + no_fill_per_leg

        # Taker mode (original).
        perp_fee = float(p.get("taker_fee_bps", 4.5))
        perp_slp_floor = float(p.get("slippage_bps", 2.0))
        spot_fee = float(p.get("spot_fee_bps", 7.0))
        spot_slp = float(p.get("spot_slippage_bps", 2.0))

        perp_slp = perp_slp_floor
        if coin is not None and notional_usd is not None and notional_usd > 0:
            est = self._estimate_perp_slippage_bps(coin, notional_usd)
            if est is not None:
                perp_slp = max(perp_slp_floor, est)
        return perp_fee + perp_slp + spot_fee + spot_slp

    def _spot_price(self, coin: str) -> Optional[float]:
        info = self._prices.get(coin)
        if not info:
            return None
        px = info.get("oracle_px")
        return px if (px and px > 0) else None

    def _perp_price(self, coin: str) -> Optional[float]:
        px = self._perp_mid.get(coin)
        if px and px > 0:
            return px
        info = self._prices.get(coin)
        if info:
            mk = info.get("mark_px")
            if mk and mk > 0:
                return mk
        return None

    def _basis_bps(self, coin: str) -> Optional[float]:
        info = self._prices.get(coin)
        if not info:
            return None
        mark, oracle = info.get("mark_px"), info.get("oracle_px")
        if not mark or not oracle or oracle <= 0:
            return None
        return (mark - oracle) / oracle * 10_000.0

    def _manage_carry(self, symbol: str, ts: float) -> None:
        """Per-symbol, per-minute: accrue funding + exit, else try to enter."""
        if symbol in self._carry:
            self._accrue_funding(symbol, ts)
            reason = self._carry_exit_reason(symbol, ts)
            if reason:
                self._close_carry(symbol, ts, reason)
            return
        if len(self._carry) >= self.config.max_positions:
            return
        self._try_open_carry(symbol, ts)

    def _accrue_funding(self, coin: str, ts: float) -> None:
        pos = self._carry.get(coin)
        if pos is None:
            return
        dt_h = (ts - pos["last_funding_ts"]) / 3600.0
        if dt_h <= 0:
            return
        f = self._funding_raw.get(coin)
        if f is not None:
            # short_perp collects positive funding; long_perp collects negative.
            sign = 1.0 if pos["direction"] == "short_perp" else -1.0
            pos["accrued_funding"] += sign * f * pos["notional"] * dt_h
        pos["last_funding_ts"] = ts

    def _mark(self, pos: dict, coin: str) -> tuple[float, float]:
        """Return (perp_leg_pnl, spot_leg_pnl) in USD for a carry position.

        Works whether or not `pos` is still in self._carry, so it is safe to
        call after the position has been popped on close."""
        n = pos["notional"]
        perp_now = self._perp_price(coin) or pos["perp_now"]
        spot_now = self._spot_price(coin) or pos["spot_now"]
        pos["perp_now"], pos["spot_now"] = perp_now, spot_now
        if pos["direction"] == "short_perp":     # short perp, long spot
            perp_pnl = (pos["perp_entry"] - perp_now) / pos["perp_entry"] * n
            spot_pnl = (spot_now - pos["spot_entry"]) / pos["spot_entry"] * n
        else:                                    # long perp, short spot
            perp_pnl = (perp_now - pos["perp_entry"]) / pos["perp_entry"] * n
            spot_pnl = (pos["spot_entry"] - spot_now) / pos["spot_entry"] * n
        return perp_pnl, spot_pnl

    def _try_open_carry(self, coin: str, ts: float) -> None:
        p = self.config.params
        f = self._funding_raw.get(coin)
        if f is None:
            return
        if len(self._funding_hist.get(coin, [])) < 3:
            return

        funding_bps_h = f * 10_000.0
        entry_thr = float(p.get("funding_entry_bps_per_hour", 0.5))
        if abs(funding_bps_h) < entry_thr:
            return

        expected_hold_h = float(p.get("expected_hold_hours", 24))
        buf_bps = float(p.get("safety_buffer_bps", 2.0))
        min_edge = float(p.get("min_expected_edge_bps", 3.0))
        # Use the prospective entry notional so depth-aware slippage reflects
        # the actual order we'd send. compute_order_notional reads capital_allocated.
        probe_notional = self.compute_order_notional()
        expected_edge = (abs(funding_bps_h) * expected_hold_h
                         - 2.0 * self._leg_cost_bps(coin, probe_notional) - buf_bps)
        if expected_edge < min_edge:
            return

        # Basis sanity — skip anomalous quotes.
        basis = self._basis_bps(coin)
        max_basis = float(p.get("max_basis_bps", 60.0))
        if basis is not None and abs(basis) > max_basis:
            return

        perp_entry = self._perp_price(coin)
        spot_entry = self._spot_price(coin)
        if not perp_entry or not spot_entry:
            return

        notional = self.compute_order_notional()
        cap = self.config.capital_allocated_usd
        if self._used_notional + notional > cap + 1e-6:
            return

        direction = "short_perp" if funding_bps_h > 0 else "long_perp"
        entry_fees = notional * self._leg_cost_bps(coin, notional) / 10_000.0
        max_hold_s = float(p.get("max_hold_hours", 72)) * 3600.0

        self._carry[coin] = {
            "direction":           direction,
            "notional":            notional,
            "perp_entry":          perp_entry,
            "spot_entry":          spot_entry,
            "perp_now":            perp_entry,
            "spot_now":            spot_entry,
            "opened_at":           ts,
            "last_funding_ts":     ts,
            "max_hold_ts":         ts + max_hold_s,
            "accrued_funding":     0.0,
            "entry_fees":          entry_fees,
            "entry_funding_bps_h": funding_bps_h,
            "entry_mode":          str(p.get("entry_mode", "taker")).lower(),
        }
        self._used_notional += notional
        log.info("[FundingCarry] OPEN %s %s notional=$%.0f funding=%.3fbps/h "
                 "edge≈%.1fbps perp=%.4f spot=%.4f",
                 coin, direction, notional, funding_bps_h, expected_edge,
                 perp_entry, spot_entry)

    def _carry_exit_reason(self, coin: str, ts: float) -> Optional[str]:
        pos = self._carry[coin]
        p = self.config.params

        if ts >= pos["max_hold_ts"]:
            return "max_hold"

        f = self._funding_raw.get(coin)
        if f is not None:
            exit_thr = float(p.get("funding_exit_bps_per_hour", 0.15))
            if abs(f * 10_000.0) < exit_thr:
                return "funding_normalized"

        basis = self._basis_bps(coin)
        max_basis = float(p.get("max_basis_bps", 60.0))
        if basis is not None and abs(basis) > max_basis:
            return "basis_blowout"

        perp_pnl, spot_pnl = self._mark(pos, coin)
        unreal = perp_pnl + spot_pnl + pos["accrued_funding"] - pos["entry_fees"]
        stop_usd = float(p.get("stop_loss_pct", 0.02)) * pos["notional"]
        if unreal < -stop_usd:
            return "stop_loss"
        return None

    def _close_carry(self, coin: str, ts: float, reason: str) -> None:
        pos = self._carry.pop(coin, None)
        if pos is None:
            return
        perp_pnl, spot_pnl = self._mark(pos, coin)
        exit_fees = pos["notional"] * self._leg_cost_bps(coin, pos["notional"]) / 10_000.0
        net = (perp_pnl + spot_pnl + pos["accrued_funding"]
               - pos["entry_fees"] - exit_fees)

        self._realized_pnl += net
        self._n_trades += 1
        if net > 0:
            self._wins += 1
        self._used_notional = max(0.0, self._used_notional - pos["notional"])

        f = self._funding_raw.get(coin)
        self._log_carry_row(coin, pos, ts, reason, perp_pnl, spot_pnl,
                            exit_fees, net, (f or 0.0) * 10_000.0)
        log.info("[FundingCarry] CLOSE %s %s net=$%.4f (perp=%.4f spot=%.4f "
                 "funding=%.4f) hold=%.1fh reason=%s",
                 coin, pos["direction"], net, perp_pnl, spot_pnl,
                 pos["accrued_funding"], (ts - pos["opened_at"]) / 3600.0, reason)
        # Reuse the streak/kill bookkeeping (uses symbol just for logging).
        super().on_position_closed(coin, net, reason)

    def _log_carry_row(self, coin, pos, ts, reason, perp_pnl, spot_pnl,
                       exit_fees, net, exit_funding_bps_h) -> None:
        try:
            path = self._paper_log_path
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            if not self._log_ready:
                self._log_ready = True
            new_file = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, "a", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if new_file:
                    w.writerow(_LOG_COLUMNS)
                w.writerow([
                    f"{pos['opened_at']:.0f}", f"{ts:.0f}", coin,
                    pos["direction"], f"{pos['notional']:.2f}",
                    f"{pos['perp_entry']:.6f}", f"{pos['perp_now']:.6f}",
                    f"{pos['spot_entry']:.6f}", f"{pos['spot_now']:.6f}",
                    f"{(ts - pos['opened_at']) / 3600.0:.3f}",
                    f"{perp_pnl:.6f}", f"{spot_pnl:.6f}",
                    f"{pos['accrued_funding']:.6f}",
                    f"{pos['entry_fees']:.6f}", f"{exit_fees:.6f}",
                    f"{net:.6f}", reason,
                    f"{pos['entry_funding_bps_h']:.4f}",
                    f"{exit_funding_bps_h:.4f}",
                    pos.get("entry_mode", "taker"),
                ])
        except Exception as exc:                       # pragma: no cover
            log.debug("FundingCarry log write failed: %s", exc)

    # ── legacy unhedged-perp directional path ────────────────────────────────

    def _check_entry(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None

        p = self.config.params
        raw = self._funding_raw.get(symbol)
        if raw is None:
            return None

        hist = list(self._funding_hist.get(symbol, []))
        if len(hist) < 3:
            return None

        funding_bps_per_h = raw * 10_000
        entry_thr      = p.get("funding_entry_bps_per_hour", 0.5)
        taker_bps      = p.get("taker_fee_bps", 3.5)
        slip_bps       = p.get("slippage_bps",  2.0)
        buf_bps        = p.get("safety_buffer_bps", 2.0)
        min_edge       = p.get("min_expected_edge_bps", 3.0)
        allow_unhedged = p.get("allow_unhedged_perp", False)

        expected_hold_h = float(p.get("expected_hold_hours", 4))
        expected_edge = abs(funding_bps_per_h) * expected_hold_h - taker_bps - slip_bps - buf_bps

        if abs(funding_bps_per_h) < entry_thr:
            return None
        if expected_edge < min_edge:
            return None

        closes = list(self._bar_closes.get(symbol, []))
        max_ret = p.get("max_abs_return_15m_pct", 2.5) / 100.0
        if len(closes) >= 15 and closes[-15] > 0:
            ret_15m = abs(closes[-1] / closes[-15] - 1)
            if ret_15m > max_ret:
                return None

        if not allow_unhedged:
            return None  # scanner mode

        notional   = self.compute_order_notional()
        max_hold_s = int(p.get("max_hold_hours", 8) * 3600)

        if funding_bps_per_h > 0:
            action = "PLACE_SELL"
            reason = f"funding_carry_short funding={funding_bps_per_h:.2f}bps/h edge={expected_edge:.1f}bps"
        else:
            action = "PLACE_BUY"
            reason = f"funding_carry_long funding={funding_bps_per_h:.2f}bps/h edge={expected_edge:.1f}bps"

        return StrategyDecision(
            action=action, symbol=symbol, reason=reason,
            notional_usd=notional, max_hold_seconds=max_hold_s,
            metadata={"funding_bps": funding_bps_per_h, "expected_edge": expected_edge,
                      "allow_unhedged": True},
        )

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p       = self.config.params
        side    = pos["side"]
        stop_h  = (side == "SELL" and mid >= pos["stop"]) or (side == "BUY" and mid <= pos["stop"])
        tp_h    = (side == "SELL" and mid <= pos["tp"])   or (side == "BUY" and mid >= pos["tp"])
        max_h   = ts >= pos["max_hold_ts"]

        raw = self._funding_raw.get(symbol)
        fund_exit = False
        if raw is not None:
            fund_bps = raw * 10_000
            exit_thr = p.get("funding_exit_bps_per_hour", 0.1)
            fund_exit = abs(fund_bps) < exit_thr

        if not (stop_h or tp_h or max_h or fund_exit):
            return None

        reason = "stop_loss" if stop_h else ("take_profit" if tp_h else ("funding_normalized" if fund_exit else "max_hold"))
        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )

    # ── calibration / stats ──────────────────────────────────────────────────

    def get_calibration_data(self, symbol: str) -> dict:
        p            = self.config.params
        raw          = self._funding_raw.get(symbol)
        hist         = list(self._funding_hist.get(symbol, []))
        funding_bps  = raw * 10_000 if raw is not None else None
        taker_bps    = p.get("taker_fee_bps", 3.5)
        slip_bps     = p.get("slippage_bps", 2.0)
        buf_bps      = p.get("safety_buffer_bps", 2.0)
        entry_h      = p.get("funding_entry_bps_per_hour", 0.5)

        expected_hold_h = float(p.get("expected_hold_hours", 4))
        expected_edge = None
        if funding_bps is not None:
            # Kept identical to the original formula (smoke-tested).
            expected_edge = abs(funding_bps) * expected_hold_h - taker_bps - slip_bps - buf_bps

        out = {
            "funding_bps_per_hour":  round(funding_bps, 4) if funding_bps else None,
            "funding_smoothed_bps":  round(sum(h * 10_000 for h in hist) / len(hist), 4) if hist else None,
            "expected_edge_bps":     round(expected_edge, 3) if expected_edge is not None else None,
            "entry_threshold_bps":   entry_h,
            "expected_hold_hours":   expected_hold_h,
            "action_bias":           self._action_bias(funding_bps, entry_h),
            "allow_unhedged_perp":   p.get("allow_unhedged_perp", False),
            "hedge_mode_available":  True,
            "hedged":                self._hedged,
            "in_position":           symbol in self._positions or symbol in self._carry,
        }

        if self._hedged:
            info  = self._prices.get(symbol, {})
            basis = self._basis_bps(symbol)
            out.update({
                "oracle_px":           round(info.get("oracle_px", 0.0), 6) or None,
                "mark_px":             round(info.get("mark_px", 0.0), 6) or None,
                "basis_bps":           round(basis, 3) if basis is not None else None,
                "carry_realized_pnl":  round(self._realized_pnl, 4),
                "carry_trades":        self._n_trades,
                "carry_win_rate":      round(self._wins / self._n_trades, 3) if self._n_trades else None,
                "carry_used_notional": round(self._used_notional, 2),
            })
            pos = self._carry.get(symbol)
            if pos is not None:
                perp_pnl, spot_pnl = self._mark(pos, symbol)
                out["carry_position"] = {
                    "direction":        pos["direction"],
                    "notional":         round(pos["notional"], 2),
                    "hold_hours":       round((time.time() - pos["opened_at"]) / 3600.0, 3),
                    "accrued_funding":  round(pos["accrued_funding"], 4),
                    "perp_leg_pnl":     round(perp_pnl, 4),
                    "spot_leg_pnl":     round(spot_pnl, 4),
                    "unrealized_pnl":   round(perp_pnl + spot_pnl + pos["accrued_funding"]
                                              - pos["entry_fees"], 4),
                }
        return out

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = len(self._positions) + len(self._carry)
        if self._hedged:
            d["carry_open"]         = len(self._carry)
            d["carry_realized_pnl"] = round(self._realized_pnl, 4)
            d["carry_trades"]       = self._n_trades
            d["carry_used_notional"] = round(self._used_notional, 2)
        return d

    def _action_bias(self, funding_bps, entry_thr) -> str:
        if funding_bps is None:
            return "no_data"
        if funding_bps > entry_thr:
            return "short_perp_collect"
        if funding_bps < -entry_thr:
            return "long_perp_collect"
        return "neutral"
