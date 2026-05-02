"""
kill_switch.py — Aggressive kill switch for S8 EMS.

Hard kills (permanent until restart):
  • Total DD >= 6%
  • Network timeout >= 20s without WS heartbeat

Suspensions (time-limited, engine stops opening only):
  • Daily DD >= 3%   → suspend rest of day
  • Rampage: >25 trades/h → 10-min pause
  • Loss streak: 4 consecutive losses → 30-min pause
  • BTC vol guard: >1.2% in 5 min → 15-min pause
"""
import logging
import time
from collections import deque
from typing import Callable, Optional

log = logging.getLogger(__name__)


class KillSwitch:

    def __init__(self, initial_capital: float,
                 daily_dd_pct:      float = 0.030,
                 total_dd_pct:      float = 0.060,
                 max_positions:     int   = 4,
                 max_notional:      float = 1500.0,
                 network_timeout_s: float = 20.0,
                 max_trades_ph:     int   = 25,
                 max_loss_streak:   int   = 4,
                 btc_move_5m_pct:   float = 0.012,
                 rampage_suspend_s: float = 600.0,
                 streak_suspend_s:  float = 1800.0,
                 volguard_suspend_s: float = 900.0,
                 close_all_cb: Optional[Callable] = None):

        self.initial_capital = initial_capital
        self.equity          = initial_capital
        self.daily_dd_pct    = daily_dd_pct
        self.total_dd_pct    = total_dd_pct
        self.max_positions   = max_positions
        self.max_notional    = max_notional
        self.net_timeout     = network_timeout_s
        self.max_trades_ph   = max_trades_ph
        self.max_loss_streak = max_loss_streak
        self.btc_move_5m_pct = btc_move_5m_pct
        self.close_all_cb    = close_all_cb

        # Suspension timestamps (0 = not suspended)
        self._rampage_until: float   = 0.0
        self._streak_until: float    = 0.0
        self._volguard_until: float  = 0.0
        self._daily_suspend: bool    = False

        # Hard kill
        self._killed: bool  = False
        self._kill_reason   = ""

        # Position / equity tracking
        self._open_count    = 0
        self._open_notional = 0.0
        self._day_start_equity = initial_capital
        self._day_start_ts  = time.time()
        self._peak_equity   = initial_capital

        # Trade / loss tracking
        self._trade_times: deque = deque(maxlen=200)   # timestamps last 200 trades
        self._loss_streak  = 0
        self._consecutive  = 0

        # Network watchdog
        self._last_heartbeat = time.time()

        # BTC prices for vol guard
        self._btc_prices: deque = deque(maxlen=600)    # ~5 min at 0.5s

        log.info("KillSwitch init: capital=%.0f daily_dd=%.1f%% total_dd=%.1f%%",
                 initial_capital, daily_dd_pct * 100, total_dd_pct * 100)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def record_heartbeat(self) -> None:
        self._last_heartbeat = time.time()

    def update_equity(self, equity: float) -> None:
        self.equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Total DD check
        total_dd = (self.initial_capital - equity) / self.initial_capital
        if total_dd >= self.total_dd_pct:
            self._hard_kill(f"total_dd={total_dd*100:.2f}%")

        # Daily DD check
        daily_dd = (self._day_start_equity - equity) / self._day_start_equity
        if daily_dd >= self.daily_dd_pct and not self._daily_suspend:
            self._daily_suspend = True
            if self.close_all_cb:
                self.close_all_cb("daily_dd_exceeded")
            log.warning("Daily DD %.2f%% exceeded — suspended for rest of day", daily_dd * 100)

    def record_trade(self, net_pnl: float) -> None:
        now = time.time()
        self._trade_times.append(now)

        if net_pnl < 0:
            self._loss_streak += 1
        else:
            self._loss_streak = 0

        # Rampage check: >N trades in last hour
        trades_last_hour = sum(1 for t in self._trade_times if now - t < 3600)
        if trades_last_hour > self.max_trades_ph:
            self._rampage_until = now + 600.0   # 10 min
            log.warning("Anti-rampage: %d trades/h > %d → 10 min suspend",
                        trades_last_hour, self.max_trades_ph)

        # Loss streak check
        if self._loss_streak >= self.max_loss_streak:
            self._streak_until = now + 1800.0   # 30 min
            self._loss_streak  = 0
            if self.close_all_cb:
                self.close_all_cb("loss_streak")
            log.warning("Loss streak %d reached → 30 min suspend", self.max_loss_streak)

    def update_btc_price(self, price: float) -> None:
        self._btc_prices.append((time.time(), price))
        self._check_btc_vol()

    def _check_btc_vol(self) -> None:
        now = time.time()
        cutoff = now - 300  # 5 min
        recent = [(ts, p) for ts, p in self._btc_prices if ts >= cutoff]
        if len(recent) < 10:
            return
        prices = [p for _, p in recent]
        move = abs(prices[-1] - prices[0]) / prices[0]
        if move >= self.btc_move_5m_pct:
            self._volguard_until = now + 900.0   # 15 min
            if self.close_all_cb:
                self.close_all_cb("btc_vol_guard")
            log.warning("BTC vol guard: %.2f%% in 5 min → 15 min suspend + close all",
                        move * 100)

    def check_network(self) -> None:
        lag = time.time() - self._last_heartbeat
        if lag > self.net_timeout:
            self._hard_kill(f"network_timeout={lag:.0f}s")

    def _hard_kill(self, reason: str) -> None:
        if not self._killed:
            self._killed = True
            self._kill_reason = reason
            log.critical("HARD KILL: %s", reason)
            if self.close_all_cb:
                self.close_all_cb(reason)

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_open(self, notional: float) -> None:
        self._open_count    += 1
        self._open_notional += notional

    def register_close(self, notional: float) -> None:
        self._open_count    = max(0, self._open_count - 1)
        self._open_notional = max(0.0, self._open_notional - notional)

    # ------------------------------------------------------------------
    # Decision gate
    # ------------------------------------------------------------------

    def can_open(self) -> tuple[bool, str]:
        if self._killed:
            return False, f"killed:{self._kill_reason}"

        now = time.time()
        if self._daily_suspend:
            return False, "daily_dd_suspend"
        if now < self._rampage_until:
            return False, f"rampage({self._rampage_until - now:.0f}s)"
        if now < self._streak_until:
            return False, f"streak({self._streak_until - now:.0f}s)"
        if now < self._volguard_until:
            return False, f"volguard({self._volguard_until - now:.0f}s)"

        if self._open_count >= self.max_positions:
            return False, f"max_pos={self.max_positions}"
        if self._open_notional >= self.max_notional:
            return False, f"max_notional={self.max_notional}"

        return True, ""

    def is_killed(self) -> bool:
        return self._killed

    # ------------------------------------------------------------------
    # Daily reset (call at UTC midnight)
    # ------------------------------------------------------------------

    def daily_reset(self) -> None:
        self._day_start_equity = self.equity
        self._day_start_ts     = time.time()
        self._daily_suspend    = False
        self._trade_times.clear()
        self._loss_streak = 0
        log.info("KillSwitch daily reset. equity=%.2f", self.equity)

    # ------------------------------------------------------------------
    # Status dict for dashboard
    # ------------------------------------------------------------------

    def status_dict(self) -> dict:
        now = time.time()
        daily_dd = (self._day_start_equity - self.equity) / self._day_start_equity * 100
        total_dd = (self.initial_capital - self.equity) / self.initial_capital * 100
        trades_h = sum(1 for t in self._trade_times if now - t < 3600)
        return {
            "killed":             self._killed,
            "kill_reason":        self._kill_reason,
            "daily_dd_pct":       max(0.0, daily_dd),
            "total_dd_pct":       max(0.0, total_dd),
            "trades_last_hour":   trades_h,
            "open_positions":     self._open_count,
            "open_notional":      self._open_notional,
            "rampage_remaining":  max(0.0, self._rampage_until - now),
            "streak_remaining":   max(0.0, self._streak_until - now),
            "volguard_remaining": max(0.0, self._volguard_until - now),
            "last_heartbeat_lag": now - self._last_heartbeat,
        }
