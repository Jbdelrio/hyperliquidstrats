"""
leader_bias.py — BTC/ETH directional bias as a FREE confirmation filter.

The lead-lag study (reports/leadlag_btc_eth.md) showed BTC/ETH → altcoin lead-lag
is real but too small to trade as a standalone signal (net < cost). Its one
free use is as a *gate*: when an alt strategy already wants to enter, veto the
trade if the leaders (BTC/ETH) are moving strongly AGAINST it — altcoins have
beta ~1.3 to BTC, so a long breakout while BTC is dumping is a low-quality entry.

No extra round-trip is paid; the filter only removes (or keeps) trades the host
strategy would otherwise take. The decision logic lives in `gate()` (a
staticmethod) so the live strategy and the backtest share identical behaviour.
"""
from __future__ import annotations

from collections import deque
from typing import Optional


class LeaderBias:
    """Tracks recent leader (BTC/ETH) returns and answers a gate query.

    Online use: feed each leader's latest price via `update(sym, price)`, then
    call `passes(sign, ...)`. The window is in *samples* (e.g. hourly bars).
    """

    def __init__(self, leaders: list[str], window: int = 4):
        self._leaders = list(leaders)
        self._window = int(window)
        self._hist: dict[str, deque] = {s: deque(maxlen=window + 1) for s in leaders}

    def update(self, symbol: str, price: float) -> None:
        if symbol in self._hist and price and price > 0:
            self._hist[symbol].append(float(price))

    def returns_bps(self) -> dict[str, float]:
        """Return each leader's return over the window, in bps (NaN-free, missing
        leaders omitted)."""
        out: dict[str, float] = {}
        for s, h in self._hist.items():
            if len(h) >= 2 and h[0] > 0:
                out[s] = (h[-1] - h[0]) / h[0] * 1e4
        return out

    def passes(self, sign: int, min_bps: float = 30.0,
               mode: str = "veto_opposite") -> bool:
        """Apply the gate to a desired trade direction `sign` (+1 long / -1 short)."""
        return self.gate(sign, self.returns_bps(), min_bps, mode)

    # ── shared decision logic (used by strategy AND backtest) ────────────

    @staticmethod
    def gate(sign: int, leader_rets_bps: dict[str, float],
             min_bps: float = 30.0, mode: str = "veto_opposite") -> bool:
        """
        sign            : desired trade direction (+1 long / -1 short)
        leader_rets_bps : {leader: return over the window in bps}
        min_bps         : a leader move is "strong" only if |ret| >= min_bps
        mode:
          "veto_opposite"   block iff ANY leader moved strongly AGAINST `sign`.
          "require_agree"   pass iff >=1 leader moved strongly WITH `sign`
                            and none moved strongly against.
          "require_all"     pass iff every leader that moved strongly agrees
                            (and at least one moved).
        Returns True = take the trade, False = skip.
        """
        if sign == 0 or not leader_rets_bps:
            return True
        strong_with = strong_against = 0
        for r in leader_rets_bps.values():
            if abs(r) < min_bps:
                continue
            if (r > 0) == (sign > 0):
                strong_with += 1
            else:
                strong_against += 1

        if mode == "veto_opposite":
            return strong_against == 0
        if mode == "require_agree":
            return strong_with >= 1 and strong_against == 0
        if mode == "require_all":
            return strong_with >= 1 and strong_against == 0
        return True
