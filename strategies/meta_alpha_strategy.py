"""
meta_alpha_strategy.py — Signal aggregator (meta-strategy).

Listens to calibration data from peer strategies and fires only when a
configurable quorum agrees on direction.

Design: MetaAlphaStrategy itself never fetches data — it reads
`get_calibration_data()` from a registry of peer strategies injected at
runtime via `register_peer()`.  This keeps it fully decoupled.

Signal scoring per peer:
  +1  → peer signals BUY  (action_bias == "long_perp_collect" | buy_pressure | bullish)
  −1  → peer signals SELL (action_bias == "short_perp_collect" | sell_pressure | bearish)
   0  → neutral / no_data

Net score ≥ min_agreement_score  → PLACE_BUY
Net score ≤ −min_agreement_score → PLACE_SELL
"""
import logging
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_BUY_LABELS  = {"buy_pressure", "long_perp_collect", "bullish", "long"}
_SELL_LABELS = {"sell_pressure", "short_perp_collect", "bearish", "short"}


class MetaAlphaStrategy(BaseStrategy):
    """
    Quorum-based meta-strategy.  Fires when at least `min_agreement_score`
    peer strategies agree on direction.

    Register peers after construction:
        meta.register_peer("funding_carry_hedged", funding_strategy)
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        self._peers:     dict[str, "BaseStrategy"] = {}
        self._positions: dict[str, dict]            = {}
        self._score_hist: dict[str, deque]          = {c: deque(maxlen=60) for c in config.coins}

    def register_peer(self, name: str, strategy: "BaseStrategy") -> None:
        self._peers[name] = strategy
        log.info("MetaAlpha: registered peer '%s'", name)

    # ── BaseStrategy interface ───────────────────────────────────────────────

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        return self._check_entry(symbol, bar.close, ts)

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p          = self.config.params
        sl_pct     = p.get("stop_loss_pct", 0.012)
        tp_pct     = p.get("take_profit_pct", 0.018)
        max_hold_s = int(p.get("max_hold_hours", 6) * 3600)

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "pos_id":      pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold_s}

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
        p         = self.config.params
        votes     = self._collect_votes(symbol)
        net_score = sum(votes.values())
        min_agr   = p.get("min_agreement_score", 2)
        return {
            "peer_votes":          votes,
            "net_score":           net_score,
            "min_agreement_score": min_agr,
            "quorum_reached":      abs(net_score) >= min_agr,
            "direction":           "BUY" if net_score >= min_agr else ("SELL" if net_score <= -min_agr else "NEUTRAL"),
            "peers_registered":    list(self._peers.keys()),
            "in_position":         symbol in self._positions,
        }

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = len(self._positions)
        d["peers_registered"]     = len(self._peers)
        return d

    # ── Internal ─────────────────────────────────────────────────────────────

    def _collect_votes(self, symbol: str) -> dict[str, int]:
        """Query each peer's calibration data and map to {peer_name: vote}."""
        votes = {}
        for name, strat in self._peers.items():
            try:
                cal   = strat.get_calibration_data(symbol)
                bias  = cal.get("action_bias") or cal.get("signal") or ""
                votes[name] = self._label_to_vote(str(bias).lower())
            except Exception as exc:
                log.debug("MetaAlpha: peer '%s' error: %s", name, exc)
                votes[name] = 0
        return votes

    @staticmethod
    def _label_to_vote(label: str) -> int:
        if any(b in label for b in _BUY_LABELS):
            return 1
        if any(s in label for s in _SELL_LABELS):
            return -1
        return 0

    def _check_entry(self, symbol: str, price: float, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None
        if not self._peers:
            return None

        p         = self.config.params
        min_agr   = p.get("min_agreement_score", 2)
        votes     = self._collect_votes(symbol)
        net_score = sum(votes.values())
        self._score_hist[symbol].append(net_score)

        notional   = self.config.max_position_size_usd
        max_hold_s = int(p.get("max_hold_hours", 6) * 3600)

        if net_score >= min_agr:
            reason = f"meta_alpha_buy score={net_score}/{len(votes)} votes={votes}"
            return StrategyDecision(
                action="PLACE_BUY", symbol=symbol, reason=reason,
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"net_score": net_score, "votes": votes},
            )

        if net_score <= -min_agr:
            reason = f"meta_alpha_sell score={net_score}/{len(votes)} votes={votes}"
            return StrategyDecision(
                action="PLACE_SELL", symbol=symbol, reason=reason,
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"net_score": net_score, "votes": votes},
            )

        return None

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p         = self.config.params
        side      = pos["side"]
        stop_h    = (side == "SELL" and mid >= pos["stop"]) or (side == "BUY" and mid <= pos["stop"])
        tp_h      = (side == "SELL" and mid <= pos["tp"])   or (side == "BUY" and mid >= pos["tp"])
        max_h     = ts >= pos["max_hold_ts"]

        # Exit if quorum flips or dissolves
        min_agr   = p.get("min_agreement_score", 2)
        votes     = self._collect_votes(symbol)
        net_score = sum(votes.values())
        quorum_lost = abs(net_score) < min_agr or (
            (side == "BUY" and net_score < 0) or (side == "SELL" and net_score > 0)
        )

        if not (stop_h or tp_h or max_h or quorum_lost):
            return None

        if stop_h:         reason = "stop_loss"
        elif tp_h:         reason = "take_profit"
        elif quorum_lost:  reason = "quorum_lost"
        else:              reason = "max_hold"

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "net_score": net_score, "pos_id": pos.get("pos_id")},
        )
