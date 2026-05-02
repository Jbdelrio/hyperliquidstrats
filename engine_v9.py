"""
engine_v9.py — Artemisia v9 main engine for S8 EMS.

Architecture: pure asyncio, single event loop.
  Loop A : _orderbook_loop  → fill checks + quote placement  (book updates)
  Loop B : _trade_loop      → Bouchaud + wavelet per trade
  Loop C : _minute_loop     → HAR-RV update (every 60s)
  Loop D : _position_loop   → stop/TP/max_hold check (every 500ms)
  Loop E : _watchdog_loop   → network timeout + BTC vol guard (every 5s)
  Loop F : _dashboard_loop  → terminal output (every 60s)

Paper mode by default. --live requires "CONFIRMED LIVE" prompt.

Usage:
  python engine_v9.py --paper --coins BTC,ETH,SOL,HYPE
  python engine_v9.py --paper                           # all 12 coins
"""
import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from data.orderbook_manager import OrderbookManager
from strategies.s8_ems import (
    S8EconophysicsMakerScalping,
    ACTION_PLACE_QUOTES, ACTION_CANCEL_QUOTES,
    ACTION_MANAGE_POS, ACTION_CLOSE_MARKET,
)
from execution.high_freq_executor import HighFreqExecutor, OpenPosition
from risk.kill_switch import KillSwitch
from risk.adverse_selection_monitor import AdverseSelectionMonitor
from monitoring.pnl_tracker import PnLTracker
from monitoring.decision_logger import DecisionLogger
from data.universe import TOP_COINS

log = logging.getLogger(__name__)


class EngineV9:

    def __init__(self, config_path: str = "config_v9.json",
                 paper: bool = True,
                 symbols: Optional[list[str]] = None):

        cfg_file = Path(__file__).parent / config_path
        with open(cfg_file) as f:
            self.cfg = json.load(f)

        self.paper   = paper
        self.symbols = [s.upper() for s in (symbols or self.cfg.get("symbols", TOP_COINS))]
        self.equity  = float(self.cfg["capital"])

        strat_p = self.cfg.get("strategy", {})
        econ_p  = self.cfg.get("econophysics", {})
        risk_p  = self.cfg.get("risk", {})
        log_cfg = self.cfg.get("logging", {})

        # ── Strategy params merged ──────────────────────────────────
        merged_params = {**strat_p, **econ_p}

        self.obm      = OrderbookManager(
            self.symbols,
            subscription_delay_s=self.cfg.get("subscription_delay_s", 0.15),
            ws_url=self.cfg.get("websocket_url", "wss://api.hyperliquid.xyz/ws"),
        )
        self.strategy = S8EconophysicsMakerScalping(merged_params, self.equity, self.symbols)
        self.executor = HighFreqExecutor(
            paper=paper,
            trade_log=log_cfg.get("trade_log", "logs/fills_v9.csv"),
            on_fill_cb=self._on_fill,
        )
        self.ks = KillSwitch(
            initial_capital=self.equity,
            daily_dd_pct=risk_p.get("max_dd_daily_pct", 0.030),
            total_dd_pct=risk_p.get("max_dd_total_pct", 0.060),
            max_positions=risk_p.get("max_open_positions", 4),
            max_notional=risk_p.get("max_notional_total", 1500.0),
            network_timeout_s=risk_p.get("network_timeout_s", 20.0),
            max_trades_ph=risk_p.get("max_trades_per_hour", 25),
            max_loss_streak=risk_p.get("max_loss_streak", 4),
            btc_move_5m_pct=risk_p.get("btc_move_5m_pct", 0.012),
            rampage_suspend_s=risk_p.get("rampage_suspend_s", 600.0),
            streak_suspend_s=risk_p.get("streak_suspend_s", 1800.0),
            volguard_suspend_s=risk_p.get("volguard_suspend_s", 900.0),
            close_all_cb=self._emergency_close,
        )
        self.adv_mon = AdverseSelectionMonitor(
            threshold=risk_p.get("max_pick_rate", 0.65),
        )
        self.tracker = PnLTracker(
            log_path=log_cfg.get("metrics_log", "metrics_v9/metrics_v9.csv"),
            equity=self.equity,
        )
        self.decision_logger = DecisionLogger(
            path=log_cfg.get("decision_log", "logs/decisions_v9.csv"),
            enabled=log_cfg.get("decision_logging_enabled", True),
        )
        self.strategy.set_decision_logger(self.decision_logger)

        self._dashboard_interval = log_cfg.get("dashboard_interval_s", 60)
        self._last_dashboard     = 0.0
        self._running            = False

        log.info("EngineV9 | paper=%s | symbols=%s | equity=%.2f",
                 paper, self.symbols, self.equity)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True

        await self.obm.connect()
        log.info("Engine V9 running. Ctrl+C to stop.")

        try:
            await asyncio.gather(
                self._orderbook_loop(),
                self._trade_loop(),
                self._minute_loop(),
                self._position_loop(),
                self._watchdog_loop(),
                self._dashboard_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    # ------------------------------------------------------------------
    # Loop A — orderbook updates (fill checks + quote placement)
    # ------------------------------------------------------------------

    async def _orderbook_loop(self) -> None:
        async for update in self.obm.stream_orderbook_updates():
            if not self._running:
                return

            sym  = update.symbol
            book = update.book
            ts   = update.timestamp

            if not book.best_bid or not book.best_ask:
                continue

            # Feed BTC price to vol guard
            if sym == "BTC" and book.mid:
                self.ks.update_btc_price(book.mid)
                self.ks.record_heartbeat()

            # 1. Check fills for any pending orders on this symbol
            fills = self.executor.check_fills(sym, book.best_bid, book.best_ask)
            for fill in fills:
                manage_action = self.strategy.on_fill(
                    sym, fill.side, fill.price,
                    fill.size_units, fill.notional_usd, ts,
                )
                self._apply_manage_action(manage_action)

            # 2. Check adverse selection suspension
            if self.adv_mon.is_suspended(sym, ts):
                self.executor.cancel_quotes(sym)
                self.strategy.clear_pending(sym)
                continue

            # 3. Strategy decision
            ok, why = self.ks.can_open()
            if not ok:
                if why not in (f"max_pos={self.ks.max_positions}",):
                    log.debug("KS blocked: %s", why)
                continue

            action = self.strategy.on_orderbook_update(sym, book, ts)
            if action:
                await self._execute(action, ts)

    # ------------------------------------------------------------------
    # Loop B — trade events (Bouchaud + wavelet)
    # ------------------------------------------------------------------

    async def _trade_loop(self) -> None:
        async for event in self.obm.stream_trades():
            if not self._running:
                return

            action = self.strategy.on_trade_event(
                event.symbol, event.price, event.volume_usd,
                event.best_bid, event.best_ask, event.side, event.timestamp,
            )
            if action and action["action"] == ACTION_CANCEL_QUOTES:
                self.executor.cancel_quotes(event.symbol)
                self.strategy.clear_pending(event.symbol)
                self.tracker.record_wavelet_alert()

    # ------------------------------------------------------------------
    # Loop C — minute close → HAR-RV
    # ------------------------------------------------------------------

    async def _minute_loop(self) -> None:
        while self._running:
            await asyncio.sleep(60)
            for sym in self.symbols:
                ret = self.obm.get_minute_return(sym)
                if ret is not None:
                    self.strategy.on_minute_close(sym, ret)

    # ------------------------------------------------------------------
    # Loop D — position exits (500ms)
    # ------------------------------------------------------------------

    async def _position_loop(self) -> None:
        while self._running:
            await asyncio.sleep(0.5)
            ts = time.time()
            for sym in self.symbols:
                book = self.obm.get_book(sym)
                if not book or not book.mid:
                    continue
                bid = book.best_bid or book.mid
                ask = book.best_ask or book.mid

                exit_action = self.strategy.check_position_exits(
                    sym, book.mid, bid, ask, ts,
                )
                if exit_action:
                    self._apply_close_action(exit_action, ts)

    # ------------------------------------------------------------------
    # Loop E — watchdog (5s)
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        while self._running:
            await asyncio.sleep(5)
            self.ks.check_network()
            if self.ks.is_killed():
                log.critical("Kill switch fired. Stopping engine.")
                self._running = False
                return

    # ------------------------------------------------------------------
    # Loop F — dashboard
    # ------------------------------------------------------------------

    async def _dashboard_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._dashboard_interval)
            self._print_dashboard()

    # ------------------------------------------------------------------
    # Action dispatchers
    # ------------------------------------------------------------------

    async def _execute(self, action: dict, ts: float) -> None:
        atype = action["action"]
        sym   = action["symbol"]

        if atype == ACTION_PLACE_QUOTES:
            pair_id = self.executor.place_quotes(
                sym,
                action["buy_price"], action["sell_price"],
                action["size"], action["notional_usd"],
            )
            if pair_id:
                self.strategy.register_pending(sym, [f"b_{pair_id}", f"s_{pair_id}"])
            log.debug("[QUOTE] %s buy=%.6f sell=%.6f notional=$%.0f mult=%.2f hurst=%s",
                      sym, action["buy_price"], action["sell_price"],
                      action["notional_usd"],
                      action["meta"].get("size_mult", 1.0),
                      action["meta"].get("hurst_regime", "?"))

        elif atype == ACTION_CANCEL_QUOTES:
            self.executor.cancel_quotes(sym)
            self.strategy.clear_pending(sym)

    def _on_fill(self, fill, pos: OpenPosition) -> None:
        """Callback from executor when a pending order fills."""
        self.ks.register_open(pos.notional_usd)

    def _apply_manage_action(self, action: dict) -> None:
        """Apply TP/stop prices to the just-opened position."""
        sym = action["symbol"]
        # Find the position by symbol (most recently opened)
        pos = next(
            (p for p in reversed(self.executor.open_positions) if p.symbol == sym),
            None,
        )
        if pos:
            max_hold_ts = time.time() + self.cfg.get("strategy", {}).get("max_hold_s", 60)
            self.executor.set_position_exits(
                pos.pos_id,
                action["tp_price"], action["stop_price"], max_hold_ts,
            )

    def _apply_close_action(self, action: dict, ts: float) -> None:
        sym    = action["symbol"]
        reason = action["reason"]

        # Find and close the position in executor (it was already cleared in strategy)
        pos = next(
            (p for p in self.executor.open_positions if p.symbol == sym),
            None,
        )
        if pos is None:
            return

        net_pnl = self.executor.close_position(pos, action["exit_price"], reason)
        hold_s  = action["hold_s"]

        self.equity += net_pnl
        self.ks.update_equity(self.equity)
        self.ks.register_close(pos.notional_usd)
        self.ks.record_trade(net_pnl)

        was_loss = net_pnl < 0
        self.adv_mon.record_close(sym, was_loss)
        self.adv_mon.check_and_suspend(sym, ts)

        self.tracker.record_trade(net_pnl, hold_s, reason)

        log.info("[CLOSE] %s %s @ %.6f | net=$%.4f | %s",
                 sym, pos.side, action["exit_price"], net_pnl, reason)

    def _emergency_close(self, reason: str = "kill_switch") -> None:
        log.critical("EMERGENCY CLOSE ALL: %s", reason)
        self.executor.cancel_all()
        mids = {s: (self.obm.get_mid(s) or 0.0) for s in self.symbols}
        self.executor.close_all_market(mids, reason)

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def _print_dashboard(self) -> None:
        now = time.time()
        pos_list = self.executor.open_positions
        pos_detail = ", ".join(
            f"{p.symbol}({p.side[0]})${p.notional_usd:.0f}@{p.entry_price:.4g}+{now-p.entry_ts:.0f}s"
            for p in pos_list
        ) or "none"

        snap = self.tracker.tick(
            equity=self.equity,
            open_pos=len(pos_list),
            quotes_active=len(self.executor.pending_orders),
            reconnections=self.obm.reconnections,
            pick_rates=self.adv_mon.get_all_pick_rates(),
        )
        dashboard = self.tracker.get_dashboard(
            snap=snap,
            ks_status=self.ks.status_dict(),
            pos_detail=pos_detail,
            bl_detail="none",
        )
        print(f"\n{dashboard}", flush=True)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        log.info("Shutting down v9...")
        self._running = False
        self.executor.cancel_all()
        mids = {s: (self.obm.get_mid(s) or 0.0) for s in self.symbols}
        self.executor.close_all_market(mids, "shutdown")
        self.decision_logger.flush()
        await self.obm.stop()
        self._print_dashboard()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _setup_logging(cfg: dict) -> None:
    level = getattr(logging, cfg.get("level", "INFO"), logging.INFO)
    log_file = cfg.get("file", "logs/engine_v9.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


async def _main(args: argparse.Namespace) -> None:
    cfg_path = Path(__file__).parent / args.config
    if not cfg_path.exists():
        print(f"Config not found: {args.config}")
        sys.exit(1)

    with open(cfg_path) as f:
        cfg = json.load(f)
    _setup_logging(cfg.get("logging", {}))

    symbols = [s.strip().upper() for s in args.coins.split(",")] if args.coins else None
    paper   = not args.live

    engine = EngineV9(config_path=args.config, paper=paper, symbols=symbols)

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_graceful_stop(engine)))
    except NotImplementedError:
        pass  # Windows — Ctrl+C raises KeyboardInterrupt, caught by asyncio.run()

    await engine.run()


async def _graceful_stop(engine: EngineV9) -> None:
    engine._running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Artemisia v9 — S8 EMS")
    parser.add_argument("--config", default="config_v9.json")
    parser.add_argument("--paper",  action="store_true", default=True)
    parser.add_argument("--live",   action="store_true",
                        help="Real money. Only after 14-day paper validation.")
    parser.add_argument("--coins",  type=str, default="",
                        help="Comma-separated, e.g. BTC,ETH,SOL")
    args = parser.parse_args()

    if args.live:
        print("WARNING: LIVE mode. Real money at risk.")
        print("Type 'CONFIRMED LIVE' to proceed:")
        if input().strip() != "CONFIRMED LIVE":
            print("Aborted.")
            sys.exit(0)

    asyncio.run(_main(args))
