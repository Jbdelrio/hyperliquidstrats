"""Risk manager (§16, §17): converts the target inventory Q_t (fraction in
[-cap, cap]) into a per-leg USD target that respects all limits, and enforces
kill-switch / drawdown / daily-loss / holding-time gates.

Sizing: target spread notional = Q_t * maximum_position_per_leg, then clamped so
that gross/net/leverage/position limits hold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
import numpy as np

from .config import RiskConfig


@dataclass
class RiskState:
    equity: float
    day_start_equity: float
    peak_equity: float
    day_id: int = -1
    halted: bool = False
    halt_reason: Optional[str] = None


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.state = RiskState(equity=cfg.initial_capital,
                               day_start_equity=cfg.initial_capital,
                               peak_equity=cfg.initial_capital)

    def size_target(self, q_fraction: float, beta: float) -> float:
        """Map inventory fraction -> target spread notional (USD), limit-clamped."""
        if self.state.halted:
            return 0.0
        cfg = self.cfg
        target = float(np.clip(q_fraction, -1.0, 1.0)) * cfg.maximum_position_per_leg
        # per-leg cap
        target = float(np.clip(target, -cfg.maximum_position_per_leg, cfg.maximum_position_per_leg))
        # gross exposure of the pair ~ |Q| + |beta*Q|
        gross_per_unit = 1.0 + abs(beta)
        if gross_per_unit > 0:
            max_by_gross = cfg.maximum_gross_exposure / gross_per_unit
            target = float(np.clip(target, -max_by_gross, max_by_gross))
        # net exposure ~ |1 - beta| * |Q|
        net_per_unit = abs(1.0 - beta)
        if net_per_unit > 1e-9:
            max_by_net = cfg.maximum_net_exposure / net_per_unit
            target = float(np.clip(target, -max_by_net, max_by_net))
        # leverage cap on gross vs equity
        max_by_lev = cfg.maximum_leverage * self.state.equity / max(gross_per_unit, 1e-9)
        target = float(np.clip(target, -max_by_lev, max_by_lev))
        return target

    def update_equity(self, equity: float, ts: float) -> None:
        s = self.state
        s.equity = equity
        s.peak_equity = max(s.peak_equity, equity)
        day = int(ts // 86400)
        if day != s.day_id:
            s.day_id = day
            s.day_start_equity = equity
        self._check_halts()

    def _check_halts(self):
        if not self.cfg.kill_switch or self.state.halted:
            return
        s = self.state
        dd = (s.peak_equity - s.equity) / s.peak_equity * 100 if s.peak_equity > 0 else 0.0
        day_loss = (s.day_start_equity - s.equity) / s.day_start_equity * 100 if s.day_start_equity > 0 else 0.0
        if dd >= self.cfg.maximum_drawdown_pct:
            s.halted = True; s.halt_reason = "MAX_DRAWDOWN"
        elif day_loss >= self.cfg.maximum_daily_loss_pct:
            s.halted = True; s.halt_reason = "DAILY_LOSS_LIMIT"

    def snapshot(self) -> Dict:
        s = self.state
        return {"equity": s.equity, "peak_equity": s.peak_equity,
                "drawdown_pct": (s.peak_equity - s.equity) / s.peak_equity * 100 if s.peak_equity > 0 else 0.0,
                "halted": s.halted, "halt_reason": s.halt_reason}
