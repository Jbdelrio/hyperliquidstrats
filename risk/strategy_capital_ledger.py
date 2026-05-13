"""
risk/strategy_capital_ledger.py — Per-strategy capital accounting and risk gate.

Each strategy has an isolated budget (default 500 USD). This is the FIRST gate
before the global KillSwitch. Rules enforced here:

  - Can't open if: open_notional + reserved_notional + requested > capital
  - Can't open if: state is suspended or killed
  - Suspend if daily loss >= 2.5% of initial_capital (1h auto-lift)
  - Kill    if total loss >= 6.0% of initial_capital (permanent until restart)

The global KillSwitch remains the final safety net.
"""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DAILY_DD_SUSPEND_PCT = 0.025   # 2.5% daily DD → suspend
_TOTAL_DD_KILL_PCT    = 0.060   # 6.0% total DD → kill
_SUSPEND_DURATION_S   = 3600    # 1h default suspension on daily-DD breach


# ---------------------------------------------------------------------------
# Per-strategy record
# ---------------------------------------------------------------------------

@dataclass
class StrategyLedger:
    name:                str
    initial_capital_usd: float

    realized_pnl:      float = 0.0
    unrealized_pnl:    float = 0.0
    open_notional:     float = 0.0
    reserved_notional: float = 0.0

    daily_pnl:    float = 0.0
    day_start_ts: float = field(default_factory=time.time)
    peak_equity:  float = 0.0

    trades_today: int = 0
    wins:         int = 0
    losses:       int = 0

    enabled:         bool  = True
    suspended:       bool  = False
    killed:          bool  = False
    suspended_until: float = 0.0
    kill_reason:     str   = ""
    suspend_reason:  str   = ""

    last_update_ts: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.peak_equity = self.initial_capital_usd

    # ── Derived properties ────────────────────────────────────────────

    @property
    def equity(self) -> float:
        return self.initial_capital_usd + self.realized_pnl

    @property
    def available_capital(self) -> float:
        used = self.open_notional + self.reserved_notional
        return max(0.0, self.initial_capital_usd - used)

    @property
    def drawdown_pct(self) -> float:
        if self.initial_capital_usd <= 0:
            return 0.0
        return max(0.0,
                   (self.initial_capital_usd - self.equity)
                   / self.initial_capital_usd * 100)

    @property
    def daily_dd_pct(self) -> float:
        if self.initial_capital_usd <= 0:
            return 0.0
        return max(0.0, -self.daily_pnl / self.initial_capital_usd * 100)

    def is_active(self) -> bool:
        """True iff the strategy may open new positions right now."""
        if self.killed:
            return False
        if self.suspended:
            if time.time() >= self.suspended_until:
                self.suspended = False      # auto-lift when timer expires
            else:
                return False
        return self.enabled

    def daily_reset(self, now: float) -> None:
        self.daily_pnl    = 0.0
        self.trades_today = 0
        self.wins         = 0
        self.losses       = 0
        self.day_start_ts = now


# ---------------------------------------------------------------------------
# Ledger — manages all strategy records
# ---------------------------------------------------------------------------

class StrategyCapitalLedger:
    """
    Central per-strategy capital accounting.
    Designed for single-threaded asyncio use — no locks needed.
    """

    def __init__(self, risk_log_path: str = "logs/risk_events.csv") -> None:
        self._ledgers: dict[str, StrategyLedger] = {}
        self._risk_log_path = risk_log_path
        Path(risk_log_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_strategy(self, name: str, capital_usd: float) -> None:
        self._ledgers[name] = StrategyLedger(
            name=name,
            initial_capital_usd=capital_usd,
        )
        log.info("[Ledger] registered %s capital=$%.0f", name, capital_usd)

    # ------------------------------------------------------------------
    # Decision gate
    # ------------------------------------------------------------------

    def can_open(self, name: str,
                 requested_notional: float) -> tuple[bool, str]:
        """
        Returns (True, "") if the strategy may open a position of this size.
        Returns (False, reason) otherwise — caller should log and skip.
        """
        ledger = self._ledgers.get(name)
        if ledger is None:
            return False, f"unknown_strategy:{name}"

        if not ledger.is_active():
            if ledger.killed:
                return False, f"killed:{ledger.kill_reason}"
            if ledger.suspended:
                remaining = max(0.0, ledger.suspended_until - time.time())
                return False, (f"suspended:{ledger.suspend_reason}"
                               f":{remaining:.0f}s_remaining")
            return False, "disabled"

        used = ledger.open_notional + ledger.reserved_notional + requested_notional
        if used > ledger.initial_capital_usd:
            return False, (
                f"budget_exceeded:"
                f"open={ledger.open_notional:.0f}"
                f"+reserved={ledger.reserved_notional:.0f}"
                f"+req={requested_notional:.0f}"
                f">{ledger.initial_capital_usd:.0f}"
            )
        return True, ""

    # ------------------------------------------------------------------
    # Notional lifecycle
    # ------------------------------------------------------------------

    def reserve_notional(self, name: str, notional: float) -> None:
        """Reserve capital before an order is placed."""
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.reserved_notional = max(0.0,
                                           ledger.reserved_notional + notional)
            ledger.last_update_ts = time.time()

    def release_reserved(self, name: str, notional: float) -> None:
        """Release reserved capital (order cancelled or failed)."""
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.reserved_notional = max(0.0,
                                           ledger.reserved_notional - notional)
            ledger.last_update_ts = time.time()

    def register_open(self, name: str, notional: float) -> None:
        """Fill confirmed: promote reserved → open."""
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.reserved_notional = max(0.0,
                                           ledger.reserved_notional - notional)
            ledger.open_notional     = max(0.0,
                                           ledger.open_notional     + notional)
            ledger.last_update_ts = time.time()

    def register_close(self, name: str,
                       notional: float, pnl: float) -> None:
        """Position closed: free open_notional, record PnL, check risk limits."""
        ledger = self._ledgers.get(name)
        if not ledger:
            return
        ledger.open_notional  = max(0.0, ledger.open_notional - notional)
        ledger.realized_pnl  += pnl
        ledger.daily_pnl     += pnl
        ledger.trades_today  += 1
        if pnl >= 0:
            ledger.wins   += 1
        else:
            ledger.losses += 1
        if ledger.equity > ledger.peak_equity:
            ledger.peak_equity = ledger.equity
        ledger.last_update_ts = time.time()
        self._check_risk_limits(ledger)
        self._maybe_daily_reset(ledger)

    def update_unrealized(self, name: str, unrealized_pnl: float) -> None:
        """Update mark-to-market PnL for display purposes."""
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.unrealized_pnl = unrealized_pnl

    # ------------------------------------------------------------------
    # Admin
    # ------------------------------------------------------------------

    def reset_strategy(self, name: str) -> None:
        """Reset to initial state (keeps capital). Clears kill/suspend."""
        ledger = self._ledgers.get(name)
        if not ledger:
            return
        cap = ledger.initial_capital_usd
        self._ledgers[name] = StrategyLedger(name=name,
                                              initial_capital_usd=cap)
        log.info("[Ledger] reset %s", name)

    def set_capital(self, name: str, capital_usd: float) -> None:
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.initial_capital_usd = capital_usd
            ledger.peak_equity = max(ledger.peak_equity, capital_usd)
            log.info("[Ledger] set_capital %s → $%.0f", name, capital_usd)

    def enable_strategy(self, name: str) -> None:
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.enabled         = True
            ledger.suspended       = False
            ledger.suspended_until = 0.0
            ledger.suspend_reason  = ""
            log.info("[Ledger] enabled %s", name)

    def disable_strategy(self, name: str) -> None:
        ledger = self._ledgers.get(name)
        if ledger:
            ledger.enabled = False
            log.info("[Ledger] disabled %s", name)

    def daily_reset_all(self) -> None:
        now = time.time()
        for ledger in self._ledgers.values():
            ledger.daily_reset(now)
        log.info("[Ledger] daily reset all strategies")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_strategy_status(self, name: str) -> dict:
        ledger = self._ledgers.get(name)
        if not ledger:
            return {}
        now = time.time()
        if ledger.killed:
            state = "killed"
        elif ledger.suspended and now < ledger.suspended_until:
            state = "suspended"
        elif not ledger.enabled:
            state = "disabled"
        else:
            state = "active"
        return {
            "initial_capital_usd": round(ledger.initial_capital_usd, 2),
            "equity":              round(ledger.equity,               2),
            "realized_pnl":        round(ledger.realized_pnl,         4),
            "unrealized_pnl":      round(ledger.unrealized_pnl,       4),
            "open_notional":       round(ledger.open_notional,         2),
            "reserved_notional":   round(ledger.reserved_notional,     2),
            "available_capital":   round(ledger.available_capital,     2),
            "daily_pnl":           round(ledger.daily_pnl,            4),
            "drawdown_pct":        round(ledger.drawdown_pct,          3),
            "daily_dd_pct":        round(ledger.daily_dd_pct,          3),
            "peak_equity":         round(ledger.peak_equity,           2),
            "trades_today":        ledger.trades_today,
            "wins":                ledger.wins,
            "losses":              ledger.losses,
            "state":               state,
            "kill_reason":         ledger.kill_reason,
            "suspend_reason":      ledger.suspend_reason,
            "suspended_until":     ledger.suspended_until,
            "last_update_ts":      ledger.last_update_ts,
        }

    def get_all_status(self) -> dict:
        return {name: self.get_strategy_status(name)
                for name in self._ledgers}

    # ------------------------------------------------------------------
    # Risk event CSV logging
    # ------------------------------------------------------------------

    def log_risk_event(self, strategy: str, symbol: str, action: str,
                       requested_notional: float, allowed: bool,
                       block_reason: str,
                       global_open_notional: float,
                       global_equity: float) -> None:
        ledger = self._ledgers.get(strategy)
        try:
            write_hdr = not Path(self._risk_log_path).exists()
            with open(self._risk_log_path, "a", newline="",
                      encoding="utf-8") as f:
                w = csv.writer(f)
                if write_hdr:
                    w.writerow([
                        "ts", "strategy", "symbol", "action",
                        "requested_notional", "allowed", "block_reason",
                        "strat_open_notional", "strat_reserved_notional",
                        "strat_available_capital",
                        "global_open_notional", "global_equity",
                    ])
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    strategy, symbol, action,
                    round(requested_notional, 2),
                    "1" if allowed else "0",
                    block_reason,
                    round(ledger.open_notional,     2) if ledger else "",
                    round(ledger.reserved_notional, 2) if ledger else "",
                    round(ledger.available_capital, 2) if ledger else "",
                    round(global_open_notional, 2),
                    round(global_equity, 2),
                ])
        except Exception as exc:
            log.debug("risk_event log write failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_risk_limits(self, ledger: StrategyLedger) -> None:
        if ledger.killed:
            return

        # Total DD → permanent kill
        if ledger.drawdown_pct >= _TOTAL_DD_KILL_PCT * 100:
            ledger.killed      = True
            ledger.kill_reason = f"total_dd={ledger.drawdown_pct:.2f}%"
            log.critical("[Ledger] KILL %s: total DD %.2f%% >= %.1f%%",
                         ledger.name, ledger.drawdown_pct,
                         _TOTAL_DD_KILL_PCT * 100)
            return

        # Daily DD → time-limited suspension
        if (ledger.daily_dd_pct >= _DAILY_DD_SUSPEND_PCT * 100
                and not ledger.suspended):
            ledger.suspended       = True
            ledger.suspended_until = time.time() + _SUSPEND_DURATION_S
            ledger.suspend_reason  = f"daily_dd={ledger.daily_dd_pct:.2f}%"
            log.warning("[Ledger] SUSPEND %s: daily DD %.2f%% >= %.1f%%",
                        ledger.name, ledger.daily_dd_pct,
                        _DAILY_DD_SUSPEND_PCT * 100)

    def _maybe_daily_reset(self, ledger: StrategyLedger) -> None:
        now = time.time()
        if now - ledger.day_start_ts >= 86400:
            ledger.daily_reset(now)
            log.info("[Ledger] daily reset %s", ledger.name)
