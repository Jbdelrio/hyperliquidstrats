"""
pnl_tracker.py — Per-minute metrics logging + terminal dashboard for S8 EMS.
"""
import csv
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class MinuteSnap:
    ts: float
    equity: float
    open_pos: int
    quotes_active: int
    fills_min: int
    pnl_min: float
    pnl_hour: float
    pnl_day: float
    win_rate: float
    avg_hold_s: float
    wins: int
    losses: int
    stops: int
    tps: int
    max_holds: int
    reconnections: int
    wavelet_alerts: int
    pick_rate_avg: float


class PnLTracker:

    def __init__(self, log_path: str = "metrics_v9/metrics_v9.csv",
                 equity: float = 500.0):
        self.log_path = log_path
        self.initial_equity = equity

        self._min_pnl: deque = deque(maxlen=60)
        self._cur_min_pnl: float = 0.0
        self._cur_min_fills: int = 0
        self._min_start: float = time.time()

        self._day_pnl: float = 0.0
        self._day_start: float = time.time()
        self._wins = self._losses = self._stops = self._tps = self._max_holds = 0
        self._holds: deque = deque(maxlen=500)
        self._wavelet_alerts: int = 0

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def record_trade(self, net_pnl: float, hold_s: float, reason: str) -> None:
        self._day_pnl      += net_pnl
        self._cur_min_pnl  += net_pnl
        self._cur_min_fills += 1
        self._holds.append(hold_s)

        if net_pnl > 0:
            self._wins += 1
        else:
            self._losses += 1

        if reason == "stop_loss":
            self._stops += 1
        elif reason == "take_profit":
            self._tps += 1
        else:
            self._max_holds += 1

    def record_wavelet_alert(self) -> None:
        self._wavelet_alerts += 1

    def tick(self, equity: float, open_pos: int, quotes_active: int,
             reconnections: int, pick_rates: dict[str, float]) -> MinuteSnap:
        now = time.time()
        if now - self._min_start >= 60:
            self._min_pnl.append(self._cur_min_pnl)
            self._cur_min_pnl   = 0.0
            self._cur_min_fills = 0
            self._min_start     = now

        pnl_hour   = sum(self._min_pnl)
        total      = self._wins + self._losses
        win_rate   = self._wins / total if total > 0 else 0.0
        avg_hold   = sum(self._holds) / len(self._holds) if self._holds else 0.0
        avg_pr     = sum(pick_rates.values()) / len(pick_rates) if pick_rates else 0.0

        snap = MinuteSnap(
            ts=now, equity=equity, open_pos=open_pos,
            quotes_active=quotes_active, fills_min=self._cur_min_fills,
            pnl_min=self._cur_min_pnl, pnl_hour=pnl_hour, pnl_day=self._day_pnl,
            win_rate=win_rate, avg_hold_s=avg_hold,
            wins=self._wins, losses=self._losses,
            stops=self._stops, tps=self._tps, max_holds=self._max_holds,
            reconnections=reconnections, wavelet_alerts=self._wavelet_alerts,
            pick_rate_avg=avg_pr,
        )
        self._write_csv(snap)

        # Daily reset at UTC midnight
        if now - self._day_start > 86400:
            self._reset_daily(now)

        return snap

    def get_dashboard(self, snap: MinuteSnap, ks_status: dict,
                      pos_detail: str, bl_detail: str) -> str:

        total = snap.wins + snap.losses
        wr_s  = f"{snap.win_rate*100:.1f}%" if total > 0 else "—"
        h_s   = f"{snap.avg_hold_s:.0f}s" if snap.avg_hold_s > 0 else "—"
        dd_d  = ks_status.get("daily_dd_pct", 0.0)
        dd_t  = ks_status.get("total_dd_pct", 0.0)

        def bar(v: float, lim: float, w: int = 10) -> str:
            f = round(min(v / max(lim, 0.001), 1.0) * w)
            return "▓" * f + "░" * (w - f)

        susp = []
        if ks_status.get("rampage_remaining", 0) > 0:
            susp.append(f"RAMPAGE {ks_status['rampage_remaining']:.0f}s")
        if ks_status.get("streak_remaining", 0) > 0:
            susp.append(f"STREAK {ks_status['streak_remaining']:.0f}s")
        if ks_status.get("volguard_remaining", 0) > 0:
            susp.append(f"VOLGUARD {ks_status['volguard_remaining']:.0f}s")
        susp_s = " | ".join(susp) if susp else "none"

        killed_s = f" !! KILLED: {ks_status.get('kill_reason','')} !!" \
                   if ks_status.get("killed") else "  [PAPER]"

        w = 65
        lines = [
            "┌" + "─" * (w - 2) + "┐",
            f"│ S8 ECONOPHYSICS MAKER SCALPING{killed_s:<33}│",
            "├" + "─" * (w - 2) + "┤",
            f"│ Equity: ${snap.equity:>8.2f}  │  Quotes: {snap.quotes_active:<4}  │  Pos: {snap.open_pos:<2}           │",
            f"│ PnL today: ${snap.pnl_day:+9.4f}  │  Trades: {total:<5} │  WR: {wr_s:<7}     │",
            f"│ PnL 1h:   ${snap.pnl_hour:+9.4f}  │  TP:{snap.tps:<4} Stop:{snap.stops:<4} Hold:{snap.max_holds:<3} {h_s:<5}│",
            "├" + "─" * (w - 2) + "┤",
            f"│ Sensors: Wavelet alerts today: {snap.wavelet_alerts:<4}  Pick rate: {snap.pick_rate_avg*100:4.1f}%          │",
            f"│ Open: {pos_detail:<57}│",
            "├" + "─" * (w - 2) + "┤",
            f"│ Daily DD:  {bar(dd_d, 3.0)} {dd_d:5.2f}% / 3.0%                        │",
            f"│ Total DD:  {bar(dd_t, 6.0)} {dd_t:5.2f}% / 6.0%                        │",
            "├" + "─" * (w - 2) + "┤",
            f"│ Suspend: {susp_s:<54}│",
            "└" + "─" * (w - 2) + "┘",
        ]
        return "\n".join(lines)

    def _write_csv(self, snap: MinuteSnap) -> None:
        try:
            write_hdr = not Path(self.log_path).exists()
            with open(self.log_path, "a", newline="") as f:
                w = csv.writer(f)
                if write_hdr:
                    w.writerow(["ts", "equity", "open_pos", "quotes",
                                 "fills_min", "pnl_min", "pnl_hour", "pnl_day",
                                 "win_rate", "avg_hold_s", "wins", "losses",
                                 "stops", "tps", "max_holds", "reconnections",
                                 "wavelet_alerts", "pick_rate"])
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(snap.ts)),
                    round(snap.equity, 2), snap.open_pos, snap.quotes_active,
                    snap.fills_min,
                    round(snap.pnl_min, 6), round(snap.pnl_hour, 6),
                    round(snap.pnl_day, 6), round(snap.win_rate, 4),
                    round(snap.avg_hold_s, 1), snap.wins, snap.losses,
                    snap.stops, snap.tps, snap.max_holds, snap.reconnections,
                    snap.wavelet_alerts, round(snap.pick_rate_avg, 4),
                ])
        except Exception as e:
            log.error("Metrics write failed: %s", e)

    def _reset_daily(self, now: float) -> None:
        self._day_start = now
        self._day_pnl   = 0.0
        self._wins = self._losses = self._stops = self._tps = self._max_holds = 0
        self._holds.clear()
        self._min_pnl.clear()
        self._wavelet_alerts = 0
        log.info("PnL tracker daily reset")
