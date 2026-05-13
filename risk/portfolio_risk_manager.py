"""
risk/portfolio_risk_manager.py — Cross-strategy concentration guard.

Sits between the per-strategy ledger gate and the global KillSwitch gate
in the decision pipeline. Prevents the bot as a whole from over-concentrating
on a single coin / direction / strategy family.

All limits are configurable in __init__ with sensible defaults.

Block reasons emitted:
  - portfolio_coin_limit
  - portfolio_net_limit
  - portfolio_family_limit
  - portfolio_correlated_limit
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# Strategy → family mapping. Strategies that share a family compete for
# the same family-exposure budget. Names match config "name" fields used
# elsewhere in the codebase (engine_v9.py, gui/tabs/overview.py).
STRATEGY_FAMILIES: dict[str, str] = {
    # Momentum / trend-following
    "MomentumLS":               "momentum",
    "RotationMomentum":         "momentum",
    # Breakout / range-break
    "BreakoutControlled":       "breakout",
    "DonchianTrend":            "breakout",
    "VolatilityRegimeBreakout": "breakout",
    # Mean reversion
    "MeanReversionKalman":      "mean_reversion",
    "RSIBollingerReversion":    "mean_reversion",
    "RelativeValue":            "mean_reversion",
    # Market making
    "S8EMS":                    "market_making",
    "OBImbalanceScalper":       "market_making",
    # Funding / basis carry
    "FundingArbitrage":         "funding",
    "FundingCarryHedged":       "funding",
    "SpotPerpBasis":            "funding",
    # Meta
    "MetaAlpha":                "meta",
}


def family_of(strategy: str) -> str:
    """Returns the family for a given strategy name, or 'other' if unmapped."""
    return STRATEGY_FAMILIES.get(strategy, "other")


@dataclass
class _Position:
    """Internal record of an open or reserved position."""
    strategy: str
    symbol:   str
    side:     str   # "BUY" / "SELL"
    notional: float


class PortfolioRiskManager:
    """
    Cross-strategy exposure & concentration limits.
    Designed for single-threaded asyncio use (no locks).
    """

    def __init__(
        self,
        max_coin_exposure_pct: float       = 0.35,
        max_net_exposure_pct: float        = 0.60,
        max_family_exposure_pct: float     = 0.40,
        max_correlated_same_dir: int       = 2,
    ) -> None:
        self.max_coin_exposure_pct    = float(max_coin_exposure_pct)
        self.max_net_exposure_pct     = float(max_net_exposure_pct)
        self.max_family_exposure_pct  = float(max_family_exposure_pct)
        self.max_correlated_same_dir  = int(max_correlated_same_dir)

        # Each open or reserved position is tracked individually so we can
        # count "correlated same-direction" positions per coin.
        self._positions: list[_Position] = []

    # ------------------------------------------------------------------
    # Aggregations
    # ------------------------------------------------------------------

    def coin_exposure_usd(self) -> dict[str, float]:
        """Absolute notional per coin (long + short, summed)."""
        agg: dict[str, float] = defaultdict(float)
        for p in self._positions:
            agg[p.symbol] += p.notional
        return dict(agg)

    def long_exposure_usd(self) -> float:
        return sum(p.notional for p in self._positions if p.side == "BUY")

    def short_exposure_usd(self) -> float:
        return sum(p.notional for p in self._positions if p.side == "SELL")

    def net_exposure_usd(self) -> float:
        """Long minus short (signed)."""
        return self.long_exposure_usd() - self.short_exposure_usd()

    def family_exposure_usd(self) -> dict[str, float]:
        agg: dict[str, float] = defaultdict(float)
        for p in self._positions:
            agg[family_of(p.strategy)] += p.notional
        return dict(agg)

    def correlated_count(self, symbol: str, side: str) -> int:
        """Count of positions on (symbol, side) — across strategies."""
        return sum(1 for p in self._positions
                   if p.symbol == symbol and p.side == side)

    # ------------------------------------------------------------------
    # Decision gate
    # ------------------------------------------------------------------

    def can_open(self, strategy: str, symbol: str, side: str,
                 notional: float, total_capital: float) -> tuple[bool, str]:
        """
        Returns (True, "") if the requested position is acceptable
        under all portfolio limits; (False, reason) otherwise.

        total_capital is the bot-wide capital denominator (typically
        engine.equity or sum of strategy capitals).
        """
        if total_capital <= 0 or notional <= 0:
            return True, ""

        # 1. Per-coin concentration
        coin_cap = total_capital * self.max_coin_exposure_pct
        coin_exp = self.coin_exposure_usd().get(symbol, 0.0)
        if coin_exp + notional > coin_cap:
            return False, (
                f"portfolio_coin_limit:{symbol}:"
                f"{coin_exp:.0f}+{notional:.0f}>{coin_cap:.0f}"
            )

        # 2. Family concentration
        fam = family_of(strategy)
        fam_cap = total_capital * self.max_family_exposure_pct
        fam_exp = self.family_exposure_usd().get(fam, 0.0)
        if fam_exp + notional > fam_cap:
            return False, (
                f"portfolio_family_limit:{fam}:"
                f"{fam_exp:.0f}+{notional:.0f}>{fam_cap:.0f}"
            )

        # 3. Net directional exposure
        # If adding this position would push |net| beyond limit, block.
        net_cap = total_capital * self.max_net_exposure_pct
        delta = notional if side == "BUY" else -notional
        new_net = self.net_exposure_usd() + delta
        if abs(new_net) > net_cap:
            return False, (
                f"portfolio_net_limit:net={new_net:+.0f}|"
                f"limit=±{net_cap:.0f}"
            )

        # 4. Correlated same-direction on same coin
        same_dir = self.correlated_count(symbol, side)
        if same_dir >= self.max_correlated_same_dir:
            return False, (
                f"portfolio_correlated_limit:{symbol}/{side}:"
                f"{same_dir}>={self.max_correlated_same_dir}"
            )

        return True, ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register_open(self, strategy: str, symbol: str,
                      side: str, notional: float) -> None:
        if notional <= 0:
            return
        self._positions.append(_Position(
            strategy=strategy, symbol=symbol, side=side, notional=notional,
        ))

    def register_close(self, strategy: str, symbol: str,
                       side: str, notional: float) -> None:
        """
        Remove the matching position. We match by (strategy, symbol, side)
        and pick the closest notional to handle partial-fill edge cases.
        """
        candidates = [
            i for i, p in enumerate(self._positions)
            if p.strategy == strategy and p.symbol == symbol and p.side == side
        ]
        if not candidates:
            return
        # Pick the one with the closest notional
        best = min(candidates,
                   key=lambda i: abs(self._positions[i].notional - notional))
        self._positions.pop(best)

    def reset(self) -> None:
        self._positions.clear()

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def snapshot(self, total_capital: float = 0.0) -> dict:
        """Return a readable dict for logs / GUI."""
        coin = self.coin_exposure_usd()
        fam  = self.family_exposure_usd()
        return {
            "positions":          len(self._positions),
            "coin_exposure_usd":  {k: round(v, 2) for k, v in coin.items()},
            "family_exposure_usd": {k: round(v, 2) for k, v in fam.items()},
            "long_usd":           round(self.long_exposure_usd(), 2),
            "short_usd":          round(self.short_exposure_usd(), 2),
            "net_usd":            round(self.net_exposure_usd(), 2),
            "total_capital":      round(total_capital, 2),
        }
