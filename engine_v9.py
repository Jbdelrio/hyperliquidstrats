"""
engine_v9.py — Artemisia v9 multi-strategy engine.

Architecture: pure asyncio, single event loop.
  Loop A : _orderbook_loop  → fill detection + decision dispatch (book updates)
  Loop B : _trade_loop      → trade-level sensor updates
  Loop C : _minute_loop     → OHLCV bar dispatch (every 60s)
  Loop D : _position_loop   → stop/TP/max_hold check (every 500ms)
  Loop E : _watchdog_loop   → network timeout + BTC vol guard (every 5s)
  Loop F : _dashboard_loop  → terminal output (every 60s)
  Loop G : _control_loop    → GUI command bus via runtime/control.json (every 2s)

Paper mode by default. --live requires "CONFIRMED LIVE" prompt.

Usage:
  python engine_v9.py --paper
  python engine_v9.py --paper --coins BTC,ETH,SOL
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
from strategies.base_strategy import BarData, StrategyConfig, StrategyDecision
from strategies.strategy_manager import StrategyManager
from strategies.s8_ems import S8EconophysicsMakerScalping
from strategies.momentum_long_short import MomentumLongShort
from strategies.breakout_controlled import BreakoutControlled
from strategies.mean_reversion_kalman import MeanReversionKalman
from strategies.funding_arbitrage import FundingArbitrage
from strategies.donchian_trend import DonchianTrendStrategy
from strategies.rsi_bollinger_reversion import RSIBollingerReversionStrategy
from strategies.rotation_momentum import RotationMomentumStrategy
from strategies.relative_value import RelativeValueStrategy
from execution.high_freq_executor import HighFreqExecutor, OpenPosition
from risk.kill_switch import KillSwitch
from risk.adverse_selection_monitor import AdverseSelectionMonitor
from monitoring.pnl_tracker import PnLTracker
from monitoring.decision_logger import DecisionLogger

log = logging.getLogger(__name__)

_STRATEGY_CLASSES = {
    "S8EconophysicsMakerScalping":  S8EconophysicsMakerScalping,
    "MomentumLongShort":            MomentumLongShort,
    "BreakoutControlled":           BreakoutControlled,
    "MeanReversionKalman":          MeanReversionKalman,
    "FundingArbitrage":             FundingArbitrage,
    "DonchianTrendStrategy":        DonchianTrendStrategy,
    "RSIBollingerReversionStrategy": RSIBollingerReversionStrategy,
    "RotationMomentumStrategy":     RotationMomentumStrategy,
    "RelativeValueStrategy":        RelativeValueStrategy,
}


class EngineV9:

    def __init__(self, config_path: str = "config_v9.json",
                 paper: bool = True,
                 symbols: Optional[list[str]] = None,
                 enable_strategies: Optional[list[str]] = None):

        cfg_file = Path(__file__).parent / config_path
        with open(cfg_file) as f:
            self.cfg = json.load(f)

        self.paper      = paper
        risk_p          = self.cfg.get("risk", {})
        log_cfg         = self.cfg.get("logging", {})
        self._runtime_cfg = self.cfg.get("runtime", {})
        self.equity     = float(self.cfg["capital"])

        # ── Collect all symbols from strategy configs ──────────────────
        strategies_cfg = self.cfg.get("strategies", [])
        all_coins: set[str] = set()
        for sc in strategies_cfg:
            for c in sc.get("coins", []):
                all_coins.add(c.upper())

        if symbols:
            all_coins = {s.upper() for s in symbols}
        if not all_coins:
            from data.universe import TOP_COINS
            all_coins = set(TOP_COINS)
        self.symbols = sorted(all_coins)

        # ── Infrastructure ─────────────────────────────────────────────
        self.obm = OrderbookManager(
            self.symbols,
            subscription_delay_s=self.cfg.get("subscription_delay_s", 0.15),
            ws_url=self.cfg.get("websocket_url", "wss://api.hyperliquid.xyz/ws"),
        )
        self.executor = HighFreqExecutor(
            paper=paper,
            trade_log=log_cfg.get("trade_log", "logs/fills_v9.csv"),
            on_fill_cb=self._on_fill,
        )
        self.ks = KillSwitch(
            initial_capital=self.equity,
            daily_dd_pct=risk_p.get("max_dd_daily_pct", 0.030),
            total_dd_pct=risk_p.get("max_dd_total_pct", 0.060),
            max_positions=risk_p.get("max_open_positions", 8),
            max_notional=risk_p.get("max_notional_total", 2000.0),
            network_timeout_s=risk_p.get("network_timeout_s", 20.0),
            max_trades_ph=risk_p.get("max_trades_per_hour", 50),
            max_loss_streak=risk_p.get("max_loss_streak", 6),
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

        # ── Strategy manager ───────────────────────────────────────────
        self.manager = StrategyManager(
            config=self.cfg,
            decision_logger=self.decision_logger,
            kill_switch=self.ks,
        )
        # --strategy flag: override enabled flags from config
        _force_enable = {s.upper() for s in (enable_strategies or [])}

        for sc in strategies_cfg:
            cls_name = sc.get("class", sc.get("name", ""))
            cls = _STRATEGY_CLASSES.get(cls_name)
            if cls is None:
                log.warning("Unknown strategy class: %s", cls_name)
                continue
            coins = [c.upper() for c in sc.get("coins", [])]
            if symbols:
                coins = [c for c in coins if c in self.symbols]

            # If --strategy flag given: enable only those named, disable the rest
            if _force_enable:
                enabled = sc["name"].upper() in _force_enable
            else:
                enabled = sc.get("enabled", True)

            strat_cfg = StrategyConfig(
                name=sc["name"],
                enabled=enabled,
                capital_allocated_usd=float(sc.get("capital_allocated_usd", 100.0)),
                max_positions=int(sc.get("max_positions", 1)),
                max_position_size_usd=float(sc.get("max_position_size_usd", 100.0)),
                coins=coins,
                params=sc.get("params", {}),
                kill_after_consecutive_losses=int(sc.get("kill_after_consecutive_losses", 5)),
                suspend_minutes_on_kill=int(sc.get("suspend_minutes_on_kill", 30)),
            )
            strat = cls(strat_cfg, decision_logger=self.decision_logger)
            self.manager.register(strat)

        # ── Multi-strategy position tracking ──────────────────────────
        self._pair_to_strategy: dict[str, str] = {}   # pair_id  → strat_name
        self._pos_to_strategy:  dict[str, str] = {}   # pos_id   → strat_name

        # ── OHLCV bar accumulator (reset each minute) ──────────────────
        self._bar_acc: dict[str, dict] = {
            s: {"open": None, "high": None, "low": None,
                "close": None, "vol": 0.0, "prev_close": None}
            for s in self.symbols
        }

        # ── Last book state (for taker simulation + exit prices) ───────
        self._last_book: dict = {}

        # ── Global trading controls ────────────────────────────────────
        self._trading_enabled: bool  = True
        self._pause_until:     float = 0.0

        self._dashboard_interval = log_cfg.get("dashboard_interval_s", 60)
        self._running            = False

        log.info("EngineV9 | paper=%s | symbols=%s | equity=%.2f | strategies=%s",
                 paper, self.symbols, self.equity,
                 list(self.manager.strategies.keys()))

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        await self.obm.connect()
        self.ks.record_heartbeat()   # reset watchdog clock after connect
        log.info("Engine V9 running. Ctrl+C to stop.")
        try:
            await asyncio.gather(
                self._orderbook_loop(),
                self._trade_loop(),
                self._minute_loop(),
                self._position_loop(),
                self._watchdog_loop(),
                self._dashboard_loop(),
                self._control_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    # ------------------------------------------------------------------
    # Loop A — orderbook updates
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

            self._last_book[sym] = book

            # Update OHLCV accumulator
            mid = book.mid
            if mid:
                acc = self._bar_acc[sym]
                if acc["open"] is None:
                    acc["open"] = mid
                acc["close"] = mid
                if acc["high"] is None or mid > acc["high"]: acc["high"] = mid
                if acc["low"]  is None or mid < acc["low"]:  acc["low"]  = mid

            # BTC vol guard
            if sym == "BTC" and book.mid:
                self.ks.update_btc_price(book.mid)
                self.ks.record_heartbeat()

            # Fill detection — calls _on_fill internally for each fill
            self.executor.check_fills(sym, book.best_bid, book.best_ask)

            # Adverse selection gate (suspends new entries for this symbol)
            if self.adv_mon.is_suspended(sym, ts):
                self.executor.cancel_quotes(sym)
                s8 = self.manager.get("S8EMS")
                if s8 and hasattr(s8, "clear_pending"):
                    s8.clear_pending(sym)
                continue

            # Strategy decisions
            for strat_name, decision in self.manager.on_orderbook_update(sym, book, ts):
                await self._execute_decision(strat_name, decision, ts)

    # ------------------------------------------------------------------
    # Loop B — trade events
    # ------------------------------------------------------------------

    async def _trade_loop(self) -> None:
        async for event in self.obm.stream_trades():
            if not self._running:
                return
            sym = event.symbol
            acc = self._bar_acc.get(sym)
            if acc is not None:
                acc["vol"] = acc.get("vol", 0.0) + getattr(event, "volume_usd", 0.0)
            self.manager.on_trade_update(sym, event, event.timestamp)

    # ------------------------------------------------------------------
    # Loop C — minute bars
    # ------------------------------------------------------------------

    async def _minute_loop(self) -> None:
        while self._running:
            await asyncio.sleep(60)
            ts = time.time()
            for sym in self.symbols:
                acc   = self._bar_acc.get(sym, {})
                close = acc.get("close")
                if close is None:
                    continue

                open_  = acc.get("open")         or close
                high   = acc.get("high")         or close
                low    = acc.get("low")          or close
                vol    = acc.get("vol",    0.0)
                prev   = acc.get("prev_close")   or close
                ret_1m = (close / prev - 1.0) if prev > 0 else 0.0

                bar = BarData(
                    symbol=sym, ts=ts,
                    open=open_, high=high, low=low, close=close,
                    volume_usd=vol, return_1m=ret_1m,
                )

                # Reset, carrying close forward
                self._bar_acc[sym] = {
                    "open": None, "high": None, "low": None,
                    "close": close, "vol": 0.0, "prev_close": close,
                }

                for strat_name, decision in self.manager.on_bar_minute(sym, bar, ts):
                    await self._execute_decision(strat_name, decision, ts)

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
                for strat_name, decision in self.manager.check_position_exits(sym, book, ts):
                    self._apply_close_action(strat_name, decision, ts)

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
    # Loop F — dashboard (60s)
    # ------------------------------------------------------------------

    async def _dashboard_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._dashboard_interval)
            try:
                self._print_dashboard()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Loop G — control file (2s)
    # ------------------------------------------------------------------

    async def _control_loop(self) -> None:
        control_file = Path(self._runtime_cfg.get("control_file",    "runtime/control.json"))
        result_file  = Path(self._runtime_cfg.get("result_file",     "runtime/control_result.json"))
        status_file  = Path(self._runtime_cfg.get("status_file",     "runtime/strategy_status.json"))
        calib_file   = Path(self._runtime_cfg.get("calibration_file","runtime/calibration_data.json"))
        status_s     = float(self._runtime_cfg.get("status_interval_s", 60))

        control_file.parent.mkdir(parents=True, exist_ok=True)
        last_cmd_id   = None
        last_status_t = -999999.0

        while self._running:
            await asyncio.sleep(2.0)
            ts = time.time()

            if ts - last_status_t >= status_s:
                await self._write_status(status_file, calib_file)
                last_status_t = ts

            try:
                if not control_file.exists():
                    continue
                with open(control_file) as f:
                    cmd = json.load(f)
                cmd_id = cmd.get("command_id")
                if not cmd_id or cmd_id == last_cmd_id:
                    continue
                last_cmd_id = cmd_id
                result = self._process_control_command(cmd, ts)
                result_file.parent.mkdir(parents=True, exist_ok=True)
                with open(result_file, "w") as f:
                    json.dump({"command_id": cmd_id, "result": result}, f)
                    f.flush()
                    import os as _os; _os.fsync(f.fileno())
                # Flush status immediately so GUI reflects changes without waiting
                await self._write_status(status_file, calib_file)
                last_status_t = ts
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                pass

    # ------------------------------------------------------------------
    # Fill callback — called by executor inside check_fills
    # ------------------------------------------------------------------

    def _on_fill(self, fill, pos: OpenPosition) -> None:
        ts = time.time()
        # order_id format: "b_<pair_id>" or "s_<pair_id>"
        order_id   = fill.order_id
        pair_id    = order_id[2:] if len(order_id) > 2 else order_id
        strat_name = self._pair_to_strategy.get(pair_id)

        self.ks.register_open(pos.notional_usd)

        if strat_name:
            strat = self.manager.get(strat_name)
            if strat:
                result = strat.on_fill(
                    fill.symbol, fill.side, fill.price,
                    fill.size_units, ts, pos_id=pos.pos_id,
                )
                if result:
                    max_hold_s = result.get("max_hold_seconds", 60)
                    self.executor.set_position_exits(
                        pos.pos_id,
                        result.get("tp_price"),
                        result.get("stop_price"),
                        ts + max_hold_s,
                    )
                self._pos_to_strategy[pos.pos_id] = strat_name

    # ------------------------------------------------------------------
    # Execute strategy decision
    # ------------------------------------------------------------------

    async def _execute_decision(self, strat_name: str,
                                decision: StrategyDecision, ts: float) -> None:
        action = decision.action
        sym    = decision.symbol
        book   = self._last_book.get(sym)

        # Gate: only PLACE actions are blocked by trading controls / KS
        if action in ("PLACE_QUOTES", "PLACE_BUY", "PLACE_SELL"):
            if not self._trading_enabled or ts < self._pause_until:
                return
            ok, _ = self.ks.can_open()
            if not ok:
                return

        if action == "PLACE_QUOTES":
            pair_id = self.executor.place_quotes(
                sym,
                decision.buy_price, decision.sell_price,
                decision.size, decision.notional_usd,
            )
            if pair_id:
                self._pair_to_strategy[pair_id] = strat_name
                strat = self.manager.get(strat_name)
                if strat and hasattr(strat, "register_pending"):
                    strat.register_pending(sym, [f"b_{pair_id}", f"s_{pair_id}"])
                log.debug("[QUOTE] %s %s buy=%.6f sell=%.6f notional=$%.0f",
                          strat_name, sym,
                          decision.buy_price, decision.sell_price,
                          decision.notional_usd)

        elif action == "CANCEL_QUOTES":
            self.executor.cancel_quotes(sym)
            strat = self.manager.get(strat_name)
            if strat and hasattr(strat, "clear_pending"):
                strat.clear_pending(sym)

        elif action in ("PLACE_BUY", "PLACE_SELL"):
            if book is None:
                return
            strat    = self.manager.get(strat_name)
            notional = decision.notional_usd or (
                strat.config.max_position_size_usd if strat else 50.0
            )
            if action == "PLACE_BUY":
                ask   = book.best_ask or book.mid
                if not ask:
                    return
                buy_p = ask * 1.0001       # fills immediately (best_ask <= buy_p)
                sell_p = 9_999_999.0       # never fills
                size   = decision.size or (notional / max(buy_p, 1e-9))
            else:
                bid   = book.best_bid or book.mid
                if not bid:
                    return
                buy_p  = 0.000_001         # never fills
                sell_p = bid * 0.9999      # fills immediately (best_bid >= sell_p)
                size   = decision.size or (notional / max(sell_p, 1e-9))

            pair_id = self.executor.place_quotes(sym, buy_p, sell_p, size, notional)
            if pair_id:
                self._pair_to_strategy[pair_id] = strat_name
                log.debug("[%s] %s %s notional=$%.0f",
                          action, strat_name, sym, notional)

        elif action == "CLOSE":
            self._apply_close_action(strat_name, decision, ts)

    # ------------------------------------------------------------------
    # Close a strategy position
    # ------------------------------------------------------------------

    def _apply_close_action(self, strat_name: str,
                             decision: StrategyDecision, ts: float) -> None:
        sym    = decision.symbol
        reason = decision.reason
        meta   = decision.metadata
        pos_id = meta.get("pos_id")

        # Locate executor position
        pos = None
        if pos_id:
            pos = next((p for p in self.executor.open_positions
                        if p.pos_id == pos_id), None)
        if pos is None:
            pos = next((p for p in self.executor.open_positions
                        if p.symbol == sym), None)
        if pos is None:
            return

        exit_price = meta.get("exit_price")
        if exit_price is None:
            bk = self._last_book.get(sym)
            exit_price = (getattr(bk, "mid", None) if bk else None) or pos.entry_price

        net_pnl = self.executor.close_position(pos, exit_price, reason, strategy=strat_name)
        hold_s  = meta.get("hold_s", ts - pos.entry_ts)

        self.equity += net_pnl
        self.ks.update_equity(self.equity)
        self.ks.register_close(pos.notional_usd)
        self.ks.record_trade(net_pnl)

        self.adv_mon.record_close(sym, net_pnl < 0)
        self.adv_mon.check_and_suspend(sym, ts)
        self.tracker.record_trade(net_pnl, hold_s, reason)

        strat = self.manager.get(strat_name)
        if strat:
            strat.on_position_closed(sym, net_pnl, reason)

        log.info("[CLOSE] %s %s %s @ %.6f | net=$%.4f | %s",
                 strat_name, sym, pos.side, exit_price, net_pnl, reason)

    # ------------------------------------------------------------------
    # Emergency close (called by KillSwitch)
    # ------------------------------------------------------------------

    def _emergency_close(self, reason: str = "kill_switch") -> None:
        log.critical("EMERGENCY CLOSE ALL: %s", reason)
        self.executor.cancel_all()
        mids = {s: (getattr(self._last_book.get(s), "mid", None) or 0.0)
                for s in self.symbols}
        self.executor.close_all_market(mids, reason)

    # ------------------------------------------------------------------
    # Status / calibration writers
    # ------------------------------------------------------------------

    async def _write_status(self, status_file: Path, calib_file: Path) -> None:
        try:
            status = self.manager.get_status()
            now = time.time()
            # Group open positions by strategy, with unrealised PnL
            pos_by_strat: dict = {}
            for pos in self.executor.open_positions:
                sname = self._pos_to_strategy.get(pos.pos_id, "unknown")
                mid   = getattr(self._last_book.get(pos.symbol), "mid", None) or pos.entry_price
                if pos.side == "BUY":
                    upnl = (mid - pos.entry_price) / pos.entry_price * pos.notional_usd
                else:
                    upnl = (pos.entry_price - mid) / pos.entry_price * pos.notional_usd
                pos_by_strat.setdefault(sname, []).append({
                    "pos_id":         pos.pos_id,
                    "symbol":         pos.symbol,
                    "side":           pos.side,
                    "notional_usd":   round(pos.notional_usd, 2),
                    "entry_price":    pos.entry_price,
                    "current_price":  round(mid, 6),
                    "unrealized_pnl": round(upnl, 4),
                    "hold_s":         int(now - pos.entry_ts),
                    "tp_price":       pos.tp_price,
                    "stop_price":     pos.stop_price,
                })
            for s in status:
                s["open_positions"] = pos_by_strat.get(s["name"], [])
            with open(status_file, "w") as f:
                json.dump(status, f, default=str)
            with open(calib_file, "w") as f:
                json.dump(self.manager.get_calibration_data(), f, default=str)
        except Exception as e:
            log.error("Status write failed: %s", e)

    # ------------------------------------------------------------------
    # Control command processor
    # ------------------------------------------------------------------

    def _process_control_command(self, cmd: dict, ts: float) -> dict:
        command = cmd.get("command", "")
        args    = cmd.get("args", {})
        try:
            if command == "update_strategy":
                name   = args.get("strategy", "")
                action = args.get("action", "")
                if action == "update_params":
                    return self.manager.control(name, "update_params",
                                                params=args.get("params", {}))
                elif action == "set_capital":
                    return self.manager.control(name, "set_capital",
                                                capital_usd=args.get("capital_usd"))
                elif action == "set_coins":
                    return self.manager.control(name, "set_coins",
                                                coins=args.get("coins", []))
                else:
                    return self.manager.control(name, action)

            elif command == "close_position":
                return self._close_position_sync(args.get("pos_id", ""), ts)

            elif command == "flatten_strategy":
                return self._flatten_strategy_sync(args.get("strategy", ""), ts)

            elif command == "flatten_all":
                return self._flatten_all_sync(ts)

            elif command == "pause_all":
                minutes = float(args.get("minutes", 60))
                self._pause_until = ts + minutes * 60
                return {"ok": True, "pause_until": self._pause_until}

            elif command == "reset_capital":
                self.equity = float(args.get("capital_usd", 500.0))
                self.ks.update_equity(self.equity)
                return {"ok": True, "equity": self.equity}

            elif command == "set_trading":
                self._trading_enabled = bool(args.get("enabled", True))
                return {"ok": True, "trading_enabled": self._trading_enabled}

            else:
                return {"ok": False, "error": f"unknown command: {command}"}

        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _close_position_sync(self, pos_id: str, ts: float) -> dict:
        pos = next((p for p in self.executor.open_positions if p.pos_id == pos_id), None)
        if not pos:
            return {"ok": False, "error": f"position {pos_id} not found"}
        mid = getattr(self._last_book.get(pos.symbol), "mid", None) or pos.entry_price
        sname = self._pos_to_strategy.get(pos.pos_id, "")
        net = self.executor.close_position(pos, mid, "manual_close", strategy=sname)
        if sname:
            strat = self.manager.get(sname)
            if strat:
                strat.on_position_closed(pos.symbol, net, "manual_close")
        return {"ok": True, "net_pnl": round(net, 4), "symbol": pos.symbol}

    def _flatten_strategy_sync(self, name: str, ts: float) -> dict:
        strat = self.manager.get(name)
        if strat:
            for coin in strat.config.coins:
                self.executor.cancel_quotes(coin)
        closed = 0
        for pos in list(self.executor.open_positions):
            if self._pos_to_strategy.get(pos.pos_id) == name:
                mid = getattr(self._last_book.get(pos.symbol), "mid", None) or pos.entry_price
                net = self.executor.close_position(pos, mid, "flatten_strategy", strategy=name)
                self.equity += net
                if strat:
                    strat.on_position_closed(pos.symbol, net, "flatten_strategy")
                closed += 1
        return {"ok": True, "closed": closed}

    def _flatten_all_sync(self, ts: float) -> dict:
        self.executor.cancel_all()
        mids  = {s: (getattr(self._last_book.get(s), "mid", None) or 0.0)
                 for s in self.symbols}
        total = self.executor.close_all_market(mids, "flatten_all")
        self.equity += total
        return {"ok": True, "total_pnl": round(total, 4)}

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def _print_dashboard(self) -> None:
        now      = time.time()
        pos_list = self.executor.open_positions
        pos_detail = ", ".join(
            f"{p.symbol}({p.side[0]})${p.notional_usd:.0f}@{p.entry_price:.4g}+{now-p.entry_ts:.0f}s"
            for p in pos_list
        ) or "none"
        strat_summary = " | ".join(
            f"{s.name}({'ON' if s.enabled else 'OFF'})"
            for s in self.manager.strategies.values()
        )
        snap = self.tracker.tick(
            equity=self.equity,
            open_pos=len(pos_list),
            quotes_active=len(self.executor.pending_orders),
            reconnections=self.obm.reconnections,
            pick_rates=self.adv_mon.get_all_pick_rates(),
        )
        text = f"\n{self.tracker.get_dashboard(snap=snap, ks_status=self.ks.status_dict(), pos_detail=pos_detail, bl_detail=strat_summary)}"
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            print(text.encode("utf-8", "replace").decode("utf-8"), flush=True)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        log.info("Shutting down v9...")
        self._running = False
        self.executor.cancel_all()
        mids = {s: (getattr(self._last_book.get(s), "mid", None) or 0.0)
                for s in self.symbols}
        self.executor.close_all_market(mids, "shutdown")
        self.decision_logger.flush()
        await self.obm.stop()
        try:
            self._print_dashboard()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _setup_logging(cfg: dict) -> None:
    import io
    level    = getattr(logging, cfg.get("level", "INFO"), logging.INFO)
    log_file = cfg.get("file", "logs/engine_v9.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    stdout_stream = (
        io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "buffer") else sys.stdout
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(stdout_stream),
            logging.FileHandler(log_file, encoding="utf-8"),
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

    symbols          = [s.strip().upper() for s in args.coins.split(",")]     if args.coins     else None
    enable_strategies = [s.strip()        for s in args.strategy.split(",")]  if args.strategy  else None
    paper             = not args.live

    engine = EngineV9(config_path=args.config, paper=paper,
                      symbols=symbols, enable_strategies=enable_strategies)

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_graceful_stop(engine)))
    except NotImplementedError:
        pass  # Windows

    await engine.run()


async def _graceful_stop(engine: EngineV9) -> None:
    engine._running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Artemisia v9 — multi-strategy")
    parser.add_argument("--config", default="config_v9.json")
    parser.add_argument("--paper",  action="store_true", default=False)
    parser.add_argument("--live",   action="store_true",
                        help="Real money. Only after 14-day paper validation.")
    parser.add_argument("--coins",     type=str, default="",
                        help="Comma-separated coin override, e.g. BTC,ETH,SOL")
    parser.add_argument("--strategy", type=str, default="",
                        help="Enable only these strategies (comma-separated). "
                             "Overrides config enabled flags. "
                             "e.g. MomentumLS,BreakoutControlled")
    args = parser.parse_args()

    if args.live:
        print("WARNING: LIVE mode. Real money at risk.")
        print("Type 'CONFIRMED LIVE' to proceed:")
        if input().strip() != "CONFIRMED LIVE":
            print("Aborted.")
            sys.exit(0)

    asyncio.run(_main(args))
