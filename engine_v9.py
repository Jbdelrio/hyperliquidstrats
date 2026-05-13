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
from strategies.spot_perp_basis import SpotPerpBasisStrategy
from strategies.funding_carry_hedged import FundingCarryHedgedStrategy
from strategies.orderbook_imbalance_scalper import OrderBookImbalanceScalper
from strategies.volatility_regime_breakout import VolatilityRegimeBreakoutStrategy
from strategies.meta_alpha_strategy import MetaAlphaStrategy
from execution.high_freq_executor import HighFreqExecutor, OpenPosition
from risk.kill_switch import KillSwitch
from risk.adverse_selection_monitor import AdverseSelectionMonitor
from risk.strategy_capital_ledger import StrategyCapitalLedger
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
    # ── New strategies (Phase 2) ──────────────────────────────────────────
    "SpotPerpBasisStrategy":              SpotPerpBasisStrategy,
    "FundingCarryHedgedStrategy":         FundingCarryHedgedStrategy,
    "OrderBookImbalanceScalper":          OrderBookImbalanceScalper,
    "VolatilityRegimeBreakoutStrategy":   VolatilityRegimeBreakoutStrategy,
    "MetaAlphaStrategy":                  MetaAlphaStrategy,
}


class EngineV9:

    def __init__(self, config_path: str = "config_v9.json",
                 paper: bool = True,
                 symbols: Optional[list[str]] = None,
                 enable_strategies: Optional[list[str]] = None,
                 exchange: str = "hyperliquid"):

        cfg_file = Path(__file__).parent / config_path
        with open(cfg_file, encoding="utf-8", errors="replace") as f:
            self.cfg = json.load(f)

        self.paper      = paper
        self.exchange   = exchange
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

        # ── Wire MetaAlpha peer references ────────────────────────────
        self._wire_meta_alpha_peers()

        # ── Per-strategy capital ledger (first risk gate) ──────────────
        self.ledger = StrategyCapitalLedger(
            risk_log_path=log_cfg.get("risk_events_log",
                                      "logs/risk_events.csv")
        )
        for sc in strategies_cfg:
            name = sc.get("name", "")
            cap  = float(sc.get("capital_allocated_usd", 100.0))
            if name:
                self.ledger.register_strategy(name, cap)

        # ── Multi-strategy position tracking ──────────────────────────
        self._pair_to_strategy: dict[str, str] = {}    # pair_id  → strat_name
        self._pos_to_strategy:  dict[str, str] = {}    # pos_id   → strat_name
        # pair_id → (strat_name, reserved_notional) — cleared on fill or cancel
        self._pair_to_reserved: dict[str, tuple[str, float]] = {}

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

        # ── Execution filter settings ──────────────────────────────────
        ef = self.cfg.get("execution_filters", {})
        self._ef_enabled      = ef.get("enabled", True)
        self._min_net_profit  = float(ef.get("min_expected_net_profit_usd", 3.0))
        self._min_rr          = float(ef.get("min_reward_risk_ratio", 1.4))
        self._ef_fee_bps      = float(ef.get("taker_fee_bps", 3.0))
        self._ef_slippage_bps = float(ef.get("slippage_bps", 4.0))
        self._min_hold_s      = float(ef.get("min_hold_seconds", 90.0))
        self._cooldown_win_s  = float(ef.get("cooldown_win_s", 300.0))
        self._cooldown_loss_s = float(ef.get("cooldown_loss_s", 900.0))
        self._cooldown: dict[tuple[str, str], float] = {}

        # ── OHLCV bars history (for LLM feature builder) ───────────────
        from llm_agents.config import LLM_MAX_OHLCV_ROWS as _LLM_MAX_BARS
        self._bars_history: dict[str, list] = {s: [] for s in self.symbols}
        self._llm_max_bars = _LLM_MAX_BARS

        # ── LLM Overlay (optional, disabled by default) ─────────────────
        self._llm_overlay = None
        try:
            from llm_agents.config import LLM_ENABLED
            if LLM_ENABLED:
                from llm_agents.base import LLMOverlay
                self._llm_overlay = LLMOverlay()
                log.info("LLM overlay enabled | arch=%s", self._llm_overlay.architecture)
            else:
                log.debug("LLM overlay disabled (LLM_ENABLED=false)")
        except Exception as _llm_init_exc:
            log.warning("LLM overlay init failed (ignored): %s", _llm_init_exc)

        # ── LLM sampling rate (skip N% of calls to reduce API cost) ───────
        try:
            from llm_agents.config import LLM_SAMPLE_RATE as _llm_sr
            self._llm_sample_rate: float = float(_llm_sr)
        except Exception:
            self._llm_sample_rate = 1.0   # default: evaluate every call

        # ── Exchange factory (optional, multi-exchange data) ────────────
        try:
            from exchanges.factory import set_orderbook_manager
            set_orderbook_manager(self.obm)
        except Exception:
            pass

        # Write initial status immediately in __init__ so GUI sees strategies
        # even before the WebSocket connects (avoids "Moteur non démarré" during startup)
        try:
            _sf0 = Path(self._runtime_cfg.get("status_file", "runtime/strategy_status.json"))
            _sf0.parent.mkdir(parents=True, exist_ok=True)
            with open(_sf0, "w", encoding="utf-8") as _f0:
                json.dump(self.manager.get_status(), _f0, default=str)
        except Exception:
            pass

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
        log.info("Engine V9 running. exchange=%s paper=%s strategies=%s",
                 self.exchange, self.paper,
                 [n for n, s in self.manager.strategies.items() if s.enabled])
        # Refresh status after connect (adds any runtime-enriched state)
        try:
            _sf = Path(self._runtime_cfg.get("status_file", "runtime/strategy_status.json"))
            _sf.parent.mkdir(parents=True, exist_ok=True)
            with open(_sf, "w", encoding="utf-8") as _f:
                json.dump(self.manager.get_status(), _f, default=str)
        except Exception:
            pass
        try:
            await asyncio.gather(
                self._orderbook_loop(),
                self._trade_loop(),
                self._minute_loop(),
                self._position_loop(),
                self._watchdog_loop(),
                self._dashboard_loop(),
                self._control_loop(),
                self._arbitrage_monitor_loop(),
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

                # Store bar in history for LLM feature builder
                hist = self._bars_history.setdefault(sym, [])
                hist.append({
                    "ts": ts, "open": open_, "high": high, "low": low,
                    "close": close, "volume_usd": vol,
                })
                if len(hist) > self._llm_max_bars:
                    hist.pop(0)

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

                # Executor-level exits: TP / stop-loss / max_hold set by on_fill
                for pos, exit_price, ex_reason in self.executor.check_exits(
                        sym, book.mid, book.best_bid or book.mid,
                        book.best_ask or book.mid):
                    sname   = self._pos_to_strategy.get(pos.pos_id, "")
                    net_pnl = self.executor.close_position(pos, exit_price,
                                                           ex_reason, strategy=sname)
                    hold_s  = ts - pos.entry_ts
                    self.equity += net_pnl
                    self.ks.update_equity(self.equity)
                    self.ks.register_close(pos.notional_usd)
                    self.ks.record_trade(net_pnl)
                    if sname:
                        self.ledger.register_close(sname, pos.notional_usd, net_pnl)
                    self._pos_to_strategy.pop(pos.pos_id, None)
                    self.adv_mon.record_close(sym, net_pnl < 0)
                    self.adv_mon.check_and_suspend(sym, ts)
                    self.tracker.record_trade(net_pnl, hold_s, ex_reason)
                    cooldown_s = self._cooldown_win_s if net_pnl >= 0 else self._cooldown_loss_s
                    self._cooldown[(sname, sym)] = ts + cooldown_s
                    if strat := self.manager.get(sname):
                        strat.on_position_closed(sym, net_pnl, ex_reason)

                # Strategy-level exits (signal reversal, momentum exit, etc.)
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
            # LLM outcome tracking — feed current mid prices to calibration logger
            if self._llm_overlay is not None:
                try:
                    await asyncio.to_thread(self._update_llm_outcomes)
                except Exception:
                    pass
            # Always write LLM status (even when disabled) so GUI toggle is responsive
            try:
                await asyncio.to_thread(self._write_llm_status)
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
        order_id   = fill.order_id
        pair_id    = order_id[2:] if len(order_id) > 2 else order_id
        strat_name = self._pair_to_strategy.get(pair_id)

        self.ks.register_open(pos.notional_usd)

        if strat_name:
            # Promote reserved → open in the ledger
            if pair_id in self._pair_to_reserved:
                _, rnotional = self._pair_to_reserved.pop(pair_id)
                self.ledger.register_open(strat_name, rnotional)
            else:
                log.warning("[LEDGER] fill without reserved: pair=%s strat=%s sym=%s",
                            pair_id, strat_name, fill.symbol)
                self.ledger.register_open(strat_name, fill.notional_usd)

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
        else:
            log.warning("[LEDGER] CRITICAL: fill without strategy pair=%s sym=%s",
                        pair_id, fill.symbol)

    # ------------------------------------------------------------------
    # Execute strategy decision
    # ------------------------------------------------------------------

    async def _execute_decision(self, strat_name: str,
                                decision: StrategyDecision, ts: float) -> None:
        action = decision.action
        sym    = decision.symbol
        book   = self._last_book.get(sym)

        # ── PLACE actions: ledger gate → KS gate → LLM → execute ──────
        if action in ("PLACE_QUOTES", "PLACE_BUY", "PLACE_SELL"):
            if not self._trading_enabled or ts < self._pause_until:
                return

            strat = self.manager.get(strat_name)
            requested_notional = decision.notional_usd or (
                strat.config.max_position_size_usd if strat else 50.0
            )

            if requested_notional <= 0:
                log.warning("[BLOCK] %s %s zero notional — strategy has no allocated capital",
                            strat_name, sym)
                return

            # Gate 1: per-strategy ledger (budget + state)
            ok_ledger, ledger_reason = self.ledger.can_open(strat_name,
                                                            requested_notional)
            if not ok_ledger:
                log.info("[LEDGER] BLOCK %s %s $%.0f → %s",
                         strat_name, sym, requested_notional, ledger_reason)
                self._log_risk_event(strat_name, sym, action,
                                     requested_notional, False,
                                     f"strategy_budget_blocked:{ledger_reason}")
                return
            self.ledger.reserve_notional(strat_name, requested_notional)

            # Gate 2: global KillSwitch
            ok_ks, _ = self.ks.can_open()
            if not ok_ks:
                self.ledger.release_reserved(strat_name, requested_notional)
                return

            # Gate 3: execution filter (min net profit + cooldown)
            ok_ef, ef_reason = self._check_execution_filter(
                strat_name, decision, ts, book)
            if not ok_ef:
                self.ledger.release_reserved(strat_name, requested_notional)
                log.debug("[EXEC_FILTER] BLOCK %s %s: %s",
                          strat_name, sym, ef_reason)
                if self.decision_logger:
                    self.decision_logger.log_filter_skip(
                        symbol=sym, strategy=strat_name,
                        blocked_reason=ef_reason,
                        notional_usd=requested_notional, timestamp=ts)
                return

            # Gate 4: LLM overlay (optional)
            if self._llm_overlay is not None:
                import random as _random
                if _random.random() < self._llm_sample_rate:
                    try:
                        decision = await asyncio.to_thread(
                            self._apply_llm_overlay_sync,
                            strat_name, decision, ts, book)
                        if decision.action == "SKIP":
                            self.ledger.release_reserved(strat_name,
                                                         requested_notional)
                            return
                        action = decision.action
                    except Exception as _llm_exc:
                        log.debug("LLM overlay error (ignored): %s", _llm_exc)

            self._log_risk_event(strat_name, sym, action,
                                 requested_notional, True, "")

            # ── Place ──────────────────────────────────────────────────
            if action == "PLACE_QUOTES":
                notional_for_order = decision.notional_usd or requested_notional
                pair_id = self.executor.place_quotes(
                    sym, decision.buy_price, decision.sell_price,
                    decision.size, notional_for_order)
                if pair_id:
                    self._pair_to_strategy[pair_id] = strat_name
                    self._pair_to_reserved[pair_id] = (strat_name,
                                                        requested_notional)
                    st = self.manager.get(strat_name)
                    if st and hasattr(st, "register_pending"):
                        st.register_pending(sym, [f"b_{pair_id}",
                                                   f"s_{pair_id}"])
                    log.debug("[QUOTE] %s %s buy=%.6f sell=%.6f notional=$%.0f",
                              strat_name, sym, decision.buy_price,
                              decision.sell_price, notional_for_order)
                else:
                    # Symbol already has pending — release reserve
                    self.ledger.release_reserved(strat_name, requested_notional)

            elif action in ("PLACE_BUY", "PLACE_SELL"):
                if book is None:
                    self.ledger.release_reserved(strat_name, requested_notional)
                    return
                strat2   = self.manager.get(strat_name)
                notional = decision.notional_usd or (
                    strat2.config.max_position_size_usd if strat2 else 50.0)
                if action == "PLACE_BUY":
                    ask = book.best_ask or book.mid
                    if not ask:
                        self.ledger.release_reserved(strat_name,
                                                     requested_notional)
                        return
                    buy_p  = ask * 1.0001
                    sell_p = 9_999_999.0
                    size   = decision.size or (notional / max(buy_p, 1e-9))
                else:
                    bid = book.best_bid or book.mid
                    if not bid:
                        self.ledger.release_reserved(strat_name,
                                                     requested_notional)
                        return
                    buy_p  = 0.000_001
                    sell_p = bid * 0.9999
                    size   = decision.size or (notional / max(sell_p, 1e-9))
                pair_id = self.executor.place_quotes(sym, buy_p, sell_p,
                                                     size, notional)
                if pair_id:
                    self._pair_to_strategy[pair_id] = strat_name
                    self._pair_to_reserved[pair_id] = (strat_name, notional)
                    log.debug("[%s] %s %s notional=$%.0f",
                              action, strat_name, sym, notional)
                else:
                    self.ledger.release_reserved(strat_name, requested_notional)
            return   # done with PLACE actions

        # ── Non-PLACE actions ──────────────────────────────────────────
        if action == "CANCEL_QUOTES":
            # Release reserved notional for this strategy's pending on sym
            seen_pairs: set[str] = set()
            for order in list(self.executor.pending_orders):
                if order.symbol != sym:
                    continue
                pid = order.pair_id
                if pid in seen_pairs:
                    continue
                if self._pair_to_strategy.get(pid) == strat_name:
                    seen_pairs.add(pid)
                    if pid in self._pair_to_reserved:
                        _, rn = self._pair_to_reserved.pop(pid)
                        self.ledger.release_reserved(strat_name, rn)
            self.executor.cancel_quotes(sym)
            st = self.manager.get(strat_name)
            if st and hasattr(st, "clear_pending"):
                st.clear_pending(sym)

        elif action == "CLOSE":
            self._apply_close_action(strat_name, decision, ts)

    # ------------------------------------------------------------------
    # Execution filter gate
    # ------------------------------------------------------------------

    def _check_execution_filter(self, strat_name: str,
                                 decision: StrategyDecision,
                                 ts: float, book) -> "tuple[bool, str]":
        if not self._ef_enabled:
            return True, ""

        key = (strat_name, decision.symbol)
        resume_ts = self._cooldown.get(key, 0.0)
        if ts < resume_ts:
            return False, f"cooldown:{resume_ts - ts:.0f}s_remaining"

        if decision.action == "PLACE_QUOTES":
            return True, ""

        tp       = decision.take_profit
        sl       = decision.stop_loss
        notional = decision.notional_usd
        if tp is None or sl is None or notional is None or notional <= 0:
            return True, ""

        strat = self.manager.get(strat_name)
        if strat is None or book is None:
            return True, ""

        side  = "long" if decision.action == "PLACE_BUY" else "short"
        entry = (book.best_ask if side == "long" else book.best_bid) or book.mid
        if not entry:
            return True, ""

        passes, blocked, econ = strat.passes_min_edge_filter(
            entry=entry, tp=tp, sl=sl, notional=notional, side=side,
            min_net_profit=self._min_net_profit, min_rr=self._min_rr,
            fee_bps=self._ef_fee_bps, slippage_bps=self._ef_slippage_bps,
        )
        return passes, blocked

    # ------------------------------------------------------------------
    # Close a strategy position
    # ------------------------------------------------------------------

    def _apply_close_action(self, strat_name: str,
                             decision: StrategyDecision, ts: float) -> None:
        sym    = decision.symbol
        reason = decision.reason
        meta   = decision.metadata
        pos_id = meta.get("pos_id")

        # Locate position — prefer by pos_id, then by strategy ownership on sym
        pos = None
        if pos_id:
            pos = next((p for p in self.executor.open_positions
                        if p.pos_id == pos_id), None)

        if pos is None:
            # Find positions owned by this strategy on this symbol
            strat_pos = [p for p in self.executor.open_positions
                         if p.symbol == sym
                         and self._pos_to_strategy.get(p.pos_id) == strat_name]
            if len(strat_pos) > 1:
                log.error("ambiguous_close_without_pos_id: %s %s has %d positions",
                          strat_name, sym, len(strat_pos))
                return
            elif strat_pos:
                pos = strat_pos[0]

        if pos is None:
            return

        # Minimum hold time guard (skip non-protective closes < 60s)
        _reason_l     = (reason or "").lower()
        _is_protective = any(k in _reason_l for k in
                             ("stop", "manual", "emergency", "flatten", "shutdown"))
        if not _is_protective and (ts - pos.entry_ts) < self._min_hold_s:
            log.debug("Skip early close %s %s after %.1fs (min_hold=%.0fs)",
                      strat_name, sym, ts - pos.entry_ts, self._min_hold_s)
            return

        exit_price = meta.get("exit_price")
        if exit_price is None:
            bk = self._last_book.get(sym)
            exit_price = (getattr(bk, "mid", None) if bk else None) or pos.entry_price

        net_pnl = self.executor.close_position(pos, exit_price, reason,
                                               strategy=strat_name)
        hold_s  = meta.get("hold_s", ts - pos.entry_ts)

        self.equity += net_pnl
        self.ks.update_equity(self.equity)
        self.ks.register_close(pos.notional_usd)
        self.ks.record_trade(net_pnl)

        self.ledger.register_close(strat_name, pos.notional_usd, net_pnl)
        self._pos_to_strategy.pop(pos.pos_id, None)

        self.adv_mon.record_close(sym, net_pnl < 0)
        self.adv_mon.check_and_suspend(sym, ts)
        self.tracker.record_trade(net_pnl, hold_s, reason)

        strat = self.manager.get(strat_name)
        if strat:
            strat.on_position_closed(sym, net_pnl, reason)

        cooldown_s = self._cooldown_win_s if net_pnl >= 0 else self._cooldown_loss_s
        self._cooldown[(strat_name, sym)] = ts + cooldown_s

        log.info("[CLOSE] %s %s %s @ %.6f | net=$%.4f | %s",
                 strat_name, sym, pos.side, exit_price, net_pnl, reason)

    # ------------------------------------------------------------------
    # LLM overlay (sync, called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _apply_llm_overlay_sync(self, strat_name: str,
                                 decision: StrategyDecision,
                                 ts: float, book) -> StrategyDecision:
        """
        Build a MarketSnapshot, evaluate LLM overlay, modify decision.
        Runs in a thread to avoid blocking the asyncio event loop.
        Never raises — returns original decision on any error.
        """
        try:
            from llm_agents.feature_builder import build_market_snapshot
            from exchanges.factory import collect_cross_exchange_data

            sym  = decision.symbol
            bars = list(self._bars_history.get(sym, []))

            account_state = {
                "equity":         self.equity,
                "open_positions": len(self.executor.open_positions),
                "notional_open":  sum(p.notional_usd for p in self.executor.open_positions),
                "daily_dd_pct":   getattr(self.ks, "_daily_dd_pct", None),
            }
            market_data = {
                "book":          book,
                "bars":          bars,
                "funding_rate":  None,
                "open_interest": None,
            }
            strategy_outputs = {
                strat_name: {
                    "action":       decision.action,
                    "reason":       decision.reason,
                    "notional_usd": decision.notional_usd,
                }
            }

            # Cross-exchange data (only if enabled and multiple exchanges configured)
            cross_ex = collect_cross_exchange_data(sym, exclude="hyperliquid")

            snapshot = build_market_snapshot(
                symbol=sym,
                market_data=market_data,
                strategy_outputs=strategy_outputs,
                account_state=account_state,
                exchange="hyperliquid",
                cross_exchange_data=cross_ex,
            )

            llm_dec = self._llm_overlay.evaluate(snapshot, strategy_context=strat_name)
            return self._llm_overlay.modify_decision(decision, llm_dec)

        except Exception as exc:
            log.debug("_apply_llm_overlay_sync error (ignored): %s", exc)
            return decision

    # ------------------------------------------------------------------
    # LLM outcome tracking (called from dashboard loop)
    # ------------------------------------------------------------------

    def _update_llm_outcomes(self) -> None:
        """Feed current mid prices to the LLM prediction logger for Brier tracking."""
        try:
            pred_logger = getattr(
                getattr(self._llm_overlay, "logger", None), "_pred_logger", None
            )
            if pred_logger is None:
                return
            mids = {}
            for sym in self.symbols:
                book = self._last_book.get(sym)
                mid  = getattr(book, "mid", None)
                if mid:
                    mids[sym] = mid
            if mids:
                pred_logger.update_outcomes(mids)
        except Exception as exc:
            log.debug("LLM outcome update error (ignored): %s", exc)

    def _write_llm_status(self) -> None:
        """Write runtime/llm_status.json for the GUI LLM Overlay tab."""
        active = self._llm_overlay is not None
        decisions: dict = {}
        if active:
            try:
                from llm_agents.logger import last_decisions
                decisions = last_decisions()
            except Exception:
                pass
        try:
            status_path = Path("runtime/llm_status.json")
            status_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "enabled":        active,
                "sample_rate":    self._llm_sample_rate,
                "architecture":   getattr(self._llm_overlay, "architecture", "unknown") if active else "—",
                "last_decisions": decisions,
                "ts":             time.time(),
            }
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            log.debug("LLM status write error (ignored): %s", exc)

    # ------------------------------------------------------------------
    # Loop H — arbitrage monitor (alert-only, 30s)
    # ------------------------------------------------------------------

    async def _arbitrage_monitor_loop(self) -> None:
        """
        Compare funding rates and basis across symbols to detect
        cross-symbol arbitrage opportunities.  ALERT ONLY — no orders sent.
        Writes runtime/arb_alerts.json for the GUI.
        """
        arb_path = Path("runtime/arb_alerts.json")
        arb_path.parent.mkdir(parents=True, exist_ok=True)
        interval = 30.0
        while self._running:
            await asyncio.sleep(interval)
            try:
                alerts = await asyncio.to_thread(self._scan_arb_opportunities)
                if alerts:
                    for a in alerts:
                        log.info("[ARB-MONITOR] %s", a)
                with open(arb_path, "w") as f:
                    json.dump({
                        "ts": time.time(),
                        "alerts": alerts,
                    }, f, indent=2)
            except Exception as exc:
                log.debug("Arbitrage monitor error (ignored): %s", exc)

    def _scan_arb_opportunities(self) -> list[dict]:
        """
        Heuristic cross-symbol arbitrage scan.
        Detects: (1) funding outliers, (2) large cross-symbol basis divergence.
        Returns list of alert dicts (alert-only, no orders).
        """
        alerts = []
        try:
            from strategies.funding_carry_hedged import _fetch_all_funding
            rates = _fetch_all_funding()

            # Funding rate outlier: any symbol > 3× median absolute rate
            abs_rates = {s: abs(r) for s, r in rates.items() if s in self.symbols}
            if abs_rates:
                sorted_r = sorted(abs_rates.values())
                median_r = sorted_r[len(sorted_r) // 2]
                thr = max(median_r * 3.0, 0.5 / 10_000)  # 3× median or min 0.5bps/h
                for sym, r in abs_rates.items():
                    if r > thr:
                        alerts.append({
                            "type":    "funding_outlier",
                            "symbol":  sym,
                            "rate_bps_per_h": round(r * 10_000, 3),
                            "median_bps":     round(median_r * 10_000, 3),
                        })

            # Cross-symbol mid spread extremes
            mids = {}
            for sym in self.symbols:
                book = self._last_book.get(sym)
                mid  = getattr(book, "mid", None)
                if mid:
                    mids[sym] = mid

            # Check BTC-normalised prices (BTC vs ETH correlation monitor)
            btc_mid = mids.get("BTC")
            eth_mid = mids.get("ETH")
            if btc_mid and eth_mid:
                ratio = eth_mid / btc_mid
                # Alert if ratio deviates >15% from typical ~0.05-0.07 range
                if ratio > 0.10 or ratio < 0.02:
                    alerts.append({
                        "type":     "cross_price_ratio_extreme",
                        "symbols":  ["BTC", "ETH"],
                        "eth_btc_ratio": round(ratio, 6),
                    })
        except Exception as exc:
            log.debug("_scan_arb_opportunities error: %s", exc)
        return alerts

    # ------------------------------------------------------------------
    # Emergency close (called by KillSwitch)
    # ------------------------------------------------------------------

    def _wire_meta_alpha_peers(self) -> None:
        """Register all non-MetaAlpha strategies as peers of MetaAlphaStrategy."""
        meta = self.manager.get("MetaAlpha")
        if meta is None or not hasattr(meta, "register_peer"):
            return
        for name, strat in self.manager.strategies.items():
            if name == "MetaAlpha":
                continue
            meta.register_peer(name, strat)
        log.info("MetaAlpha wired with %d peers: %s",
                 len(meta._peers), list(meta._peers.keys()))

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

            # Group open positions by strategy with unrealised PnL
            pos_by_strat: dict = {}
            upnl_by_strat: dict[str, float] = {}
            for pos in self.executor.open_positions:
                sname = self._pos_to_strategy.get(pos.pos_id, "unknown")
                mid   = (getattr(self._last_book.get(pos.symbol), "mid", None)
                         or pos.entry_price)
                ep = pos.entry_price or 0.0
                if ep > 0:
                    if pos.side == "BUY":
                        upnl = (mid - ep) / ep * pos.notional_usd
                    else:
                        upnl = (ep - mid) / ep * pos.notional_usd
                else:
                    upnl = 0.0
                upnl_by_strat[sname] = upnl_by_strat.get(sname, 0.0) + upnl
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

            # Count pending orders per strategy
            pending_by_strat: dict[str, int] = {}
            seen_pairs_p: set[str] = set()
            for order in self.executor.pending_orders:
                pid = order.pair_id
                if pid not in seen_pairs_p:
                    seen_pairs_p.add(pid)
                    sn = self._pair_to_strategy.get(pid, "unknown")
                    pending_by_strat[sn] = pending_by_strat.get(sn, 0) + 1

            for s in status:
                name = s["name"]
                s["open_positions"]       = pos_by_strat.get(name, [])
                s["pending_orders_count"] = pending_by_strat.get(name, 0)
                # Update ledger unrealized PnL then embed ledger snapshot
                total_upnl = upnl_by_strat.get(name, 0.0)
                self.ledger.update_unrealized(name, total_upnl)
                s["ledger"] = self.ledger.get_strategy_status(name)

            # Atomic write
            _tmp_s = status_file.with_suffix(".tmp")
            with open(_tmp_s, "w", encoding="utf-8") as f:
                json.dump(status, f, default=str)
            import os as _os; _os.replace(_tmp_s, status_file)

            _tmp_c = calib_file.with_suffix(".tmp")
            with open(_tmp_c, "w", encoding="utf-8") as f:
                json.dump(self.manager.get_calibration_data(), f, default=str)
            _os.replace(_tmp_c, calib_file)
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
                    r = self.manager.control(name, "set_capital",
                                             capital_usd=args.get("capital_usd"))
                    if r.get("ok"):
                        self.ledger.set_capital(name,
                                                float(args.get("capital_usd", 500)))
                    return r
                elif action == "set_coins":
                    return self.manager.control(name, "set_coins",
                                                coins=args.get("coins", []))
                elif action == "disable":
                    mode = args.get("mode", "disable_only")
                    return self._disable_strategy(name, mode)
                elif action == "enable":
                    r = self.manager.control(name, "enable")
                    if r.get("ok"):
                        self.ledger.enable_strategy(name)
                    return r
                elif action == "reset":
                    r = self.manager.control(name, "reset")
                    if r.get("ok"):
                        self.ledger.reset_strategy(name)
                    return r
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

            elif command == "set_llm":
                enabled = bool(args.get("enabled", False))
                if enabled and self._llm_overlay is None:
                    try:
                        from llm_agents.base import LLMOverlay
                        self._llm_overlay = LLMOverlay()
                        log.info("LLM overlay enabled at runtime")
                    except Exception as exc:
                        return {"ok": False, "error": f"LLM init failed: {exc}"}
                elif not enabled:
                    self._llm_overlay = None
                    log.info("LLM overlay disabled at runtime")
                return {"ok": True, "llm_enabled": self._llm_overlay is not None}

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
        cancelled = self._cancel_pending_for_strategy(name)
        closed    = self._flatten_strategy(name, "flatten_strategy")
        return {"ok": True, "cancelled": cancelled, "closed": closed}

    def _flatten_all_sync(self, ts: float) -> dict:
        self.executor.cancel_all()
        # Release all reserved notional
        for pid, (sname, rn) in list(self._pair_to_reserved.items()):
            self.ledger.release_reserved(sname, rn)
        self._pair_to_reserved.clear()
        mids  = {s: (getattr(self._last_book.get(s), "mid", None) or 0.0)
                 for s in self.symbols}
        total = self.executor.close_all_market(mids, "flatten_all")
        self.equity += total
        return {"ok": True, "total_pnl": round(total, 4)}

    # ------------------------------------------------------------------
    # Strategy lifecycle helpers (cancel / flatten / disable with mode)
    # ------------------------------------------------------------------

    def _cancel_pending_for_strategy(self, strategy_name: str) -> int:
        """Cancel all pending orders for a strategy; release reserved notional."""
        cancelled = 0
        seen_pairs: set[str] = set()
        for order in list(self.executor.pending_orders):
            pid = order.pair_id
            if pid in seen_pairs:
                continue
            if self._pair_to_strategy.get(pid) == strategy_name:
                seen_pairs.add(pid)
                if pid in self._pair_to_reserved:
                    _, rn = self._pair_to_reserved.pop(pid)
                    self.ledger.release_reserved(strategy_name, rn)
                self.executor.cancel_quotes(order.symbol)
                cancelled += 1
        return cancelled

    def _flatten_strategy(self, strategy_name: str,
                          reason: str = "manual_flatten") -> int:
        """Close all open positions belonging to strategy_name."""
        strat   = self.manager.get(strategy_name)
        closed  = 0
        ts      = time.time()
        for pos in list(self.executor.open_positions):
            if self._pos_to_strategy.get(pos.pos_id) != strategy_name:
                continue
            mid = (getattr(self._last_book.get(pos.symbol), "mid", None)
                   or pos.entry_price)
            net = self.executor.close_position(pos, mid, reason,
                                               strategy=strategy_name)
            self.equity += net
            self.ks.update_equity(self.equity)
            self.ks.register_close(pos.notional_usd)
            self.ks.record_trade(net)
            self.ledger.register_close(strategy_name, pos.notional_usd, net)
            self._pos_to_strategy.pop(pos.pos_id, None)
            hold_s = ts - pos.entry_ts
            self.tracker.record_trade(net, hold_s, reason)
            if strat:
                strat.on_position_closed(pos.symbol, net, reason)
            closed += 1
        return closed

    def _disable_strategy(self, name: str, mode: str) -> dict:
        """
        Disable a strategy with explicit cleanup:
          disable_only    — just disable, positions keep running
          disable_cancel  — disable + cancel pending orders
          disable_flatten — disable + cancel pending + close positions
        """
        res = self.manager.control(name, "disable")
        if not res.get("ok"):
            return res
        self.ledger.disable_strategy(name)
        details = {"ok": True, "mode": mode, "cancelled": 0, "closed": 0}
        if mode in ("disable_cancel", "disable_flatten"):
            details["cancelled"] = self._cancel_pending_for_strategy(name)
        if mode == "disable_flatten":
            details["closed"] = self._flatten_strategy(name,
                                                        "disable_flatten")
        log.info("[ENGINE] disable_strategy %s mode=%s → %s", name, mode, details)
        return details

    # ------------------------------------------------------------------
    # Risk event logger (delegates to ledger)
    # ------------------------------------------------------------------

    def _log_risk_event(self, strategy: str, symbol: str, action: str,
                         requested_notional: float, allowed: bool,
                         block_reason: str) -> None:
        global_open = sum(p.notional_usd for p in self.executor.open_positions)
        self.ledger.log_risk_event(
            strategy=strategy, symbol=symbol, action=action,
            requested_notional=requested_notional, allowed=allowed,
            block_reason=block_reason,
            global_open_notional=global_open,
            global_equity=self.equity,
        )

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
        # Delete status file on shutdown so GUI stale-detection kicks in immediately
        # (age > 30s → ENGINE OFF badge) without showing all-DISABLED from [] list
        try:
            _sf = Path(self._runtime_cfg.get("status_file", "runtime/strategy_status.json"))
            _sf.unlink(missing_ok=True)
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

    with open(cfg_path, encoding="utf-8", errors="replace") as f:
        cfg = json.load(f)
    _setup_logging(cfg.get("logging", {}))

    symbols          = [s.strip().upper() for s in args.coins.split(",")]     if args.coins     else None
    enable_strategies = [s.strip()        for s in args.strategy.split(",")]  if args.strategy  else None
    paper             = not args.live

    # Write runtime config for GUI status display
    _rt = Path("runtime")
    _rt.mkdir(parents=True, exist_ok=True)
    import os as _os_pid
    with open(_rt / "engine_config.json", "w") as _f:
        json.dump({
            "exchange":            args.exchange,
            "paper":               paper,
            "started_at":          time.time(),
            "config_path":         args.config,
            "selected_strategies": enable_strategies or [],
            "pid":                 _os_pid.getpid(),
        }, _f)
    log.info("[ENGINE] started — exchange=%s paper=%s config=%s selected_strategies=%s",
             args.exchange, paper, args.config, enable_strategies)

    engine = EngineV9(config_path=args.config, paper=paper,
                      symbols=symbols, enable_strategies=enable_strategies,
                      exchange=args.exchange)

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
    # Fix Windows cp1252 terminal encoding — must be FIRST
    import io as _io
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
    parser.add_argument("--exchange", type=str, default="hyperliquid",
                        help="Primary data exchange: hyperliquid (default), binance, bitget")
    args = parser.parse_args()

    if args.live:
        print("WARNING: LIVE mode. Real money at risk.")
        print("Type 'CONFIRMED LIVE' to proceed:")
        if input().strip() != "CONFIRMED LIVE":
            print("Aborted.")
            sys.exit(0)

    asyncio.run(_main(args))
