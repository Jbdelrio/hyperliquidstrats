"""
funding_arbitrage.py — Capture extreme funding rates on Hyperliquid perps.

When funding > threshold (e.g. 0.03%/h, equivalent to ~260% APR), short the
perp to collect funding.  Directional only (no spot hedge on Hyperliquid).
Funding rates fetched via REST API every minute in on_bar_minute.
"""
import logging
import time
from collections import deque
from typing import Optional

import numpy as np
import requests

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_API_URL = "https://api.hyperliquid.xyz/info"
_API_TIMEOUT = 5.0


class FundingArbitrage(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)

        self._funding:     dict[str, deque] = {c: deque(maxlen=24) for c in config.coins}
        self._r24h:        dict[str, float] = {}
        self._bar_closes:  dict[str, deque] = {c: deque(maxlen=1441) for c in config.coins}
        self._positions:   dict[str, dict]  = {}
        self._last_fetch:  float = 0.0
        self._raw_funding: dict[str, float] = {}

    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2
        return self._check_exit(symbol, mid, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        closes = self._bar_closes.get(symbol)
        if closes is not None:
            closes.append(bar.close)

        # Update 24h return
        c = list(self._bar_closes.get(symbol, []))
        if len(c) >= 1440 and c[-1440] > 0:
            self._r24h[symbol] = (c[-1] / c[-1440] - 1) * 100.0

        # Fetch funding every 60s (shared across symbols)
        if ts - self._last_fetch >= 60:
            self._fetch_funding()
            self._last_fetch = ts

        # Only proceed once per symbol per minute on the first symbol call
        return self._check_entry(symbol, ts)

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        stop_pct = p.get("stop_loss_pct", 3.0) / 100.0
        max_hold = int(p.get("max_hold_cycles", 3) * 3600)
        stop  = price * (1 + stop_pct) if side == "SELL" else price * (1 - stop_pct)

        self._positions[symbol] = {
            "side":       side,
            "entry":      price,
            "size":       size,
            "stop":       stop,
            "opened_at":  ts,
            "max_hold_ts": ts + max_hold,
            "cycles_collected": 0,
            "pos_id":     pos_id,
        }
        return {"tp_price": price * 0.99, "stop_price": stop, "max_hold_seconds": max_hold}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        rates = list(self._funding.get(symbol, []))
        raw   = self._raw_funding.get(symbol, None)
        smoothed = float(np.mean(rates)) if rates else None
        r24h  = self._r24h.get(symbol, None)
        return {"funding_rate": raw, "funding_smoothed": smoothed, "r_24h_pct": r24h}

    # ------------------------------------------------------------------

    def _fetch_funding(self) -> None:
        try:
            resp = requests.post(
                _API_URL,
                json={"type": "metaAndAssetCtxs"},
                timeout=_API_TIMEOUT,
            )
            data = resp.json()
            meta, ctxs = data[0], data[1]
            for i, ctx in enumerate(ctxs):
                if i >= len(meta.get("universe", [])):
                    break
                coin = meta["universe"][i]["name"]
                if coin in self._funding:
                    rate = float(ctx.get("funding", 0.0))
                    self._raw_funding[coin] = rate
                    self._funding[coin].append(rate)
        except Exception as e:
            log.warning("FundingArbitrage: API fetch failed: %s", e)

    def _check_entry(self, symbol: str, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None

        p         = self.config.params
        raw_rate  = self._raw_funding.get(symbol)
        rates_buf = list(self._funding.get(symbol, []))

        if raw_rate is None or len(rates_buf) < 3:
            return None

        smoothed = float(np.mean(rates_buf))
        entry_thr = p.get("funding_entry_threshold_pct_per_hour", 0.03) / 100.0

        if raw_rate < entry_thr:
            if self.decision_logger:
                self.decision_logger.log_skip(symbol, "funding_too_low", timestamp=ts)
            return None

        if smoothed < entry_thr * 0.8:
            if self.decision_logger:
                self.decision_logger.log_skip(symbol, "funding_spike_not_confirmed", timestamp=ts)
            return None

        r24h = self._r24h.get(symbol, 0.0)
        if r24h < p.get("r_24h_min_pct", 5.0):
            if self.decision_logger:
                self.decision_logger.log_skip(symbol, "r24h_too_low", timestamp=ts)
            return None

        notional = self.config.max_position_size_usd
        max_hold = int(p.get("max_hold_cycles", 3) * 3600)

        if self.decision_logger:
            self.decision_logger.log_place(symbol, timestamp=ts,
                                           notional_usd=notional)

        return StrategyDecision(
            action="PLACE_SELL", symbol=symbol,
            reason=f"funding={raw_rate*100:.3f}%/h r24h={r24h:.1f}%",
            notional_usd=notional,
            max_hold_seconds=max_hold,
            metadata={"funding_rate": raw_rate, "smoothed": smoothed, "r24h": r24h},
        )

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p         = self.config.params
        exit_thr  = p.get("funding_exit_threshold_pct_per_hour", 0.005) / 100.0
        raw_rate  = self._raw_funding.get(symbol, 0.0)
        stop_pct  = p.get("stop_loss_pct", 3.0) / 100.0

        side = pos["side"]
        stop_hit = (side == "SELL" and mid >= pos["stop"]) or \
                   (side == "BUY"  and mid <= pos["stop"])
        max_hold = ts >= pos["max_hold_ts"]
        funding_normalized = raw_rate < exit_thr

        if not (stop_hit or max_hold or funding_normalized):
            return None

        if stop_hit:           reason = "stop_loss"
        elif funding_normalized: reason = "funding_normalized"
        else:                  reason = "max_hold_cycles"

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )
