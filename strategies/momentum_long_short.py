"""
momentum_long_short.py — Cross-sectional buy-winners / sell-losers intraday.

Score: M_i = 0.4*z(r_15m) + 0.4*z(r_1h) + 0.2*z(r_4h)
Reranks every `rerank_seconds` (default 300s).
Top-K coins → LONG candidates, Bottom-K → SHORT candidates.
"""
import logging
import time
from collections import deque
from typing import Optional

import numpy as np

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_NEED_BARS = 241  # 4h warmup at 1-min bars


class MomentumLongShort(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        p = config.params

        self._closes:  dict[str, deque] = {c: deque(maxlen=_NEED_BARS + 5) for c in config.coins}
        self._volumes: dict[str, deque] = {c: deque(maxlen=1440) for c in config.coins}

        self._scores:      dict[str, float] = {}   # percentile 0-1
        self._raw_scores:  dict[str, float] = {}   # composite z-score
        self._longs:   set[str] = set()
        self._shorts:  set[str] = set()
        self._last_rerank_ts: float = 0.0

        # Open positions: symbol → {side, entry, tp, stop, trail_ref, notional, pos_id, opened_at}
        self._positions: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        p = self.config.params
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None

        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000

        # ── Exit checks for open position ──────────────────────────
        if symbol in self._positions:
            exit_d = self._check_exit(symbol, mid, bid, ask, ts)
            if exit_d:
                return exit_d

        # ── Entry checks ───────────────────────────────────────────
        if symbol in self._positions:
            return None

        max_pos = self.config.max_positions
        if len(self._positions) >= max_pos:
            self._log_skip(symbol, "max_positions_reached", ts, mid, spread_bps)
            return None

        in_long  = symbol in self._longs
        in_short = symbol in self._shorts
        if not in_long and not in_short:
            return None

        # Filters
        if spread_bps > p.get("spread_bps_max", 15.0):
            self._log_skip(symbol, "spread_too_wide", ts, mid, spread_bps)
            return None

        pct = self._scores.get(symbol, 0.5)
        # Longs: need high percentile; shorts: need low percentile
        long_pct_min  = p.get("long_percentile_min",  p.get("score_threshold", 75) / 100.0)
        short_pct_max = p.get("short_percentile_max", 1.0 - p.get("score_threshold", 75) / 100.0)
        if in_long  and pct < long_pct_min:
            self._log_skip(symbol, "score_too_low", ts, mid, spread_bps)
            return None
        if in_short and pct > short_pct_max:
            self._log_skip(symbol, "score_too_low", ts, mid, spread_bps)
            return None

        # Phase-6 absolute score threshold: longs need pct >= threshold,
        # shorts need (1 - pct) >= threshold. Default 0 disables the gate.
        min_score = float(p.get("min_score_threshold", 0.0))
        if min_score > 0.0:
            score_long  = pct
            score_short = 1.0 - pct
            score = score_long if in_long else score_short
            if score < min_score:
                self._log_skip(symbol, "min_score_threshold", ts, mid, spread_bps)
                return None

        # Anti-pump: check r_15m
        closes = list(self._closes.get(symbol, []))
        if len(closes) >= 16:
            r15m = abs(np.log(closes[-1] / closes[-16])) if closes[-16] > 0 else 99.0
            if r15m > p.get("r_15m_max_pct", 4.0) / 100.0:
                self._log_skip(symbol, "anti_pump_r15m", ts, mid, spread_bps)
                return None

        notional = min(
            self.config.max_position_size_usd,
            self.config.capital_allocated_usd / max(max_pos, 1),
        )
        side = "BUY" if in_long else "SELL"
        entry_price = ask if side == "BUY" else bid
        stop_pct = p.get("stop_loss_pct", 2.5) / 100.0
        tp_pct   = p.get("take_profit_pct", 3.0) / 100.0

        if side == "BUY":
            tp_price   = entry_price * (1 + tp_pct)
            stop_price = entry_price * (1 - stop_pct)
        else:
            tp_price   = entry_price * (1 - tp_pct)
            stop_price = entry_price * (1 + stop_pct)

        if decision_logger := self.decision_logger:
            decision_logger.log_place(
                symbol, timestamp=ts, mid=mid, spread_bps=spread_bps,
                notional_usd=notional,
            )

        size = notional / max(entry_price, 1e-9)
        action = "PLACE_BUY" if side == "BUY" else "PLACE_SELL"

        # ── Enrich decision with risk metrics (Phase-6) ──────────
        fee_bps = float(p.get("taker_fee_bps", 4.5))
        slip_bps = float(p.get("slippage_bps", 4.5))
        cost_bps = 2 * (fee_bps + slip_bps)
        risk_usd = notional * stop_pct
        reward_usd = notional * tp_pct
        rr = reward_usd / risk_usd if risk_usd > 0 else 0.0
        expected_edge_bps = tp_pct * 10_000.0
        expected_net = reward_usd - (cost_bps / 10_000.0) * notional

        return StrategyDecision(
            action=action, symbol=symbol,
            buy_price=entry_price if side == "BUY" else None,
            sell_price=entry_price if side == "SELL" else None,
            size=size, notional_usd=notional,
            stop_loss=stop_price, take_profit=tp_price,
            max_hold_seconds=int(self.config.params.get("max_hold_hours", 4) * 3600),
            metadata={"side": side, "trail_ref": entry_price},
            strategy_family="momentum",
            order_type="TAKER_SIM",
            confidence=abs(pct - 0.5) * 2.0,
            estimated_cost_bps=cost_bps,
            expected_edge_bps=expected_edge_bps,
            expected_net_profit_usd=expected_net,
            risk_usd=risk_usd,
            reward_risk_ratio=rr,
        )

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        closes  = self._closes.get(symbol)
        volumes = self._volumes.get(symbol)
        if closes is None:
            return None
        closes.append(bar.close)
        volumes.append(bar.volume_usd)

        # Periodic reranking
        rerank_s = self.config.params.get("rerank_seconds", 300)
        if ts - self._last_rerank_ts >= rerank_s:
            self._rerank(ts)
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        stop_pct = p.get("stop_loss_pct", 2.5) / 100.0
        tp_pct   = p.get("take_profit_pct", 3.0) / 100.0
        max_hold = int(p.get("max_hold_hours", 4) * 3600)

        if side == "BUY":
            tp    = price * (1 + tp_pct)
            stop  = price * (1 - stop_pct)
        else:
            tp    = price * (1 - tp_pct)
            stop  = price * (1 + stop_pct)

        self._positions[symbol] = {
            "side":       side,
            "entry":      price,
            "size":       size,
            "notional":   size * price,
            "tp":         tp,
            "stop":       stop,
            "trail_ref":  price,
            "opened_at":  ts,
            "max_hold_ts": ts + max_hold,
            "pos_id":     pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        bid = getattr(book, "best_bid", None)
        ask = getattr(book, "best_ask", None)
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, bid or mid, ask or mid, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        pct   = self._scores.get(symbol, None)
        raw   = self._raw_scores.get(symbol, None)
        closes = list(self._closes.get(symbol, []))
        r15m = r1h = r4h = None
        if len(closes) >= 241:
            if closes[-16] > 0:  r15m = np.log(closes[-1] / closes[-16])
            if closes[-61] > 0:  r1h  = np.log(closes[-1] / closes[-61])
            if closes[-241] > 0: r4h  = np.log(closes[-1] / closes[-241])
        side = "long" if symbol in self._longs else ("short" if symbol in self._shorts else "none")
        return {
            "momentum_score":    float(pct) if pct is not None else None,
            "raw_score":         float(raw) if raw is not None else None,
            "r_15m": float(r15m) if r15m is not None else None,
            "r_1h":  float(r1h)  if r1h  is not None else None,
            "r_4h":  float(r4h)  if r4h  is not None else None,
            "in_longs":  symbol in self._longs,
            "in_shorts": symbol in self._shorts,
            "candidate_side": side,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rerank(self, ts: float) -> None:
        p = self.config.params
        coins_with_data = []
        r15m_map, r1h_map, r4h_map = {}, {}, {}

        for coin in self.config.coins:
            closes = list(self._closes.get(coin, []))
            if len(closes) < _NEED_BARS:
                continue
            try:
                r15m = np.log(closes[-1] / closes[-16])  if closes[-16] > 0 else None
                r1h  = np.log(closes[-1] / closes[-61])  if closes[-61] > 0 else None
                r4h  = np.log(closes[-1] / closes[-241]) if closes[-241] > 0 else None
                if r15m is None or r1h is None or r4h is None:
                    continue
            except (IndexError, ZeroDivisionError, ValueError):
                continue
            r15m_map[coin] = r15m
            r1h_map[coin]  = r1h
            r4h_map[coin]  = r4h
            coins_with_data.append(coin)

        if len(coins_with_data) < 3:
            return

        def z_score(d: dict) -> dict:
            vals = np.array([d[c] for c in coins_with_data])
            mu, sigma = vals.mean(), vals.std()
            if sigma < 1e-12:
                return {c: 0.0 for c in coins_with_data}
            return {c: float((d[c] - mu) / sigma) for c in coins_with_data}

        z15 = z_score(r15m_map)
        z1h = z_score(r1h_map)
        z4h = z_score(r4h_map)

        raw_scores = {
            c: 0.4 * z15[c] + 0.4 * z1h[c] + 0.2 * z4h[c]
            for c in coins_with_data
        }

        # Keep raw composite z-scores AND convert to 0-1 percentile
        self._raw_scores = raw_scores.copy()
        sorted_coins = sorted(raw_scores, key=raw_scores.get)
        n = len(sorted_coins)
        self._scores = {c: i / max(n - 1, 1) for i, c in enumerate(sorted_coins)}

        k_long  = int(p.get("top_k_long",    4))
        k_short = int(p.get("bottom_k_short", 4))
        self._longs  = set(sorted_coins[-k_long:])
        self._shorts = set(sorted_coins[:k_short])
        self._last_rerank_ts = ts

        log.debug("MomentumLS rerank: longs=%s shorts=%s", self._longs, self._shorts)

    def _check_exit(self, symbol: str, mid: float,
                    bid: float, ask: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        side = pos["side"]
        p    = self.config.params
        trail_pct = p.get("trailing_stop_pct", 1.5) / 100.0

        # Update trailing reference
        if side == "BUY"  and mid > pos["trail_ref"]: pos["trail_ref"] = mid
        if side == "SELL" and mid < pos["trail_ref"]: pos["trail_ref"] = mid

        # Compute trailing stop level
        if side == "BUY":
            trail_stop = pos["trail_ref"] * (1 - trail_pct)
            stop_hit   = mid <= max(pos["stop"], trail_stop)
            tp_hit     = bid >= pos["tp"]
        else:
            trail_stop = pos["trail_ref"] * (1 + trail_pct)
            stop_hit   = mid >= min(pos["stop"], trail_stop)
            tp_hit     = ask <= pos["tp"]

        max_hold = ts >= pos["max_hold_ts"]

        # Momentum exit: coin left the candidates set
        mom_exit = (side == "BUY"  and symbol not in self._longs  and
                    symbol not in self._shorts) or \
                   (side == "SELL" and symbol not in self._shorts and
                    symbol not in self._longs)

        if not (stop_hit or tp_hit or max_hold or mom_exit):
            return None

        if stop_hit:   reason, exit_price = "stop_loss",   (bid if side == "BUY" else ask)
        elif tp_hit:   reason, exit_price = "take_profit", pos["tp"]
        elif mom_exit: reason, exit_price = "momentum_exit", mid
        else:          reason, exit_price = "max_hold",    mid

        hold_s = ts - pos["opened_at"]
        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": exit_price, "hold_s": hold_s, "pos_id": pos.get("pos_id")},
        )

    def _log_skip(self, symbol, reason, ts, mid, spread_bps):
        if self.decision_logger:
            self.decision_logger.log_skip(symbol, reason, timestamp=ts,
                                          mid=mid, spread_bps=spread_bps)
