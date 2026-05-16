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
# Alpha Research framework (Phase: seconds features) — all disabled by default.
from strategies.seconds_research_strategy import SecondsResearchStrategy
from strategies.alpha_pressure_scalper import AlphaPressureScalper
from strategies.book_flow_divergence_reversal import BookFlowDivergenceReversal
from strategies.absorption_reversal import AbsorptionReversal
from strategies.funding_arbitrage_enhanced import FundingArbitrageEnhanced
from execution.high_freq_executor import HighFreqExecutor, OpenPosition
from execution.execution_planner import ExecutionPlanner
from risk.kill_switch import KillSwitch
from risk.adverse_selection_monitor import AdverseSelectionMonitor
from risk.strategy_capital_ledger import StrategyCapitalLedger
from risk.portfolio_risk_manager import PortfolioRiskManager
from risk.sanity_check_engine import SanityCheckEngine
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
    # ── Alpha Research framework (paper-only, disabled by default) ───────
    "SecondsResearchStrategy":            SecondsResearchStrategy,
    "AlphaPressureScalper":               AlphaPressureScalper,
    "BookFlowDivergenceReversal":         BookFlowDivergenceReversal,
    "AbsorptionReversal":                 AbsorptionReversal,
    "FundingArbitrageEnhanced":           FundingArbitrageEnhanced,
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

        # ── Phase 4: micro-live safe mode guards ──────────────────────
        # When mode=="micro_live" and paper_mode is false, the bot only
        # starts after several explicit safety checks pass. Live order
        # routing is NOT implemented in this codebase — HighFreqExecutor
        # raises NotImplementedError on paper=False. The checks below are
        # defense-in-depth so the engine never even initialises in an
        # ambiguous "almost-live" state.
        _mode = self.cfg.get("mode", "paper")
        if _mode == "micro_live" and not self.cfg.get("paper_mode", True):
            import os as _os_micro
            arm_var = self.cfg.get("require_env_arm", "ARTEMISIA_ALLOW_MICRO_LIVE")
            if str(_os_micro.environ.get(arm_var, "")).lower() != "true":
                raise RuntimeError(
                    f"Micro-live mode requires env var {arm_var}=true. "
                    f"Refusing to start."
                )
            max_notional = float(self.cfg.get("max_order_notional_usd", 0))
            if max_notional <= 0 or max_notional > 5:
                raise RuntimeError(
                    f"Micro-live mode: max_order_notional_usd must be ≤ 5 USD, "
                    f"got {max_notional}. Refusing to start."
                )
            # Loud safety banner — repeated in the engine log
            banner = (
                "\n" + "=" * 72 + "\n"
                "  ARTEMISIA MICRO-LIVE SAFE MODE ARMED\n"
                f"  max_order_notional_usd : ${max_notional:.2f}\n"
                f"  max_daily_loss_usd     : ${float(self.cfg.get('max_daily_loss_usd', 0)):.2f}\n"
                f"  max_total_loss_usd     : ${float(self.cfg.get('max_total_loss_usd', 0)):.2f}\n"
                f"  max_open_positions     : {self.cfg.get('max_open_positions', 0)}\n"
                f"  max_trades_per_hour    : {self.cfg.get('max_trades_per_hour', 0)}\n"
                f"  allow_only_symbols     : {self.cfg.get('allow_only_symbols', [])}\n"
                "  Live execution is currently NOT IMPLEMENTED — engine will\n"
                "  refuse to instantiate a live executor (NotImplementedError).\n"
                + "=" * 72
            )
            for line in banner.splitlines():
                log.warning(line)
            try:
                print(banner, flush=True)
            except Exception:
                pass

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
        # Realistic-fill knobs (Phase 1) — read from config or use defaults
        _paper_sim = self.cfg.get("paper_simulation", {}) or {}
        self.executor = HighFreqExecutor(
            paper=paper,
            trade_log=log_cfg.get("trade_log", "logs/fills_v9.csv"),
            on_fill_cb=self._on_fill,
            config={
                "paper_latency_ms":           _paper_sim.get("paper_latency_ms", 150),
                "max_pending_seconds_taker":  _paper_sim.get("max_pending_seconds_taker", 30),
                "max_pending_seconds_maker":  _paper_sim.get("max_pending_seconds_maker", 120),
                "base_slippage_bps":          _paper_sim.get("base_slippage_bps", 2.0),
                # Phase 8 (audit): conservative exits + fees-from-config.
                "tp_fill_mode":               _paper_sim.get("tp_fill_mode", "legacy"),
                "stop_fill_mode":             _paper_sim.get("stop_fill_mode", "legacy"),
                "fees":                       self.cfg.get("fees", {}) or {},
            },
            orders_log=log_cfg.get("orders_log", "logs/orders_v9.csv"),
        )
        # ── Alpha Research framework — SecondsFeatureEngine (opt-in) ────
        # Disabled by default for backward compatibility. Enabled via
        # `seconds_features.enabled = true` in config.
        sf_cfg = self.cfg.get("seconds_features", {}) or {}
        self.seconds_features = None
        self._seconds_logger = None
        self._seconds_feature_interval = float(sf_cfg.get("feature_interval_s", 1.0))
        if sf_cfg.get("enabled", False):
            try:
                from data.seconds_feature_engine import SecondsFeatureEngine
                self.seconds_features = SecondsFeatureEngine(self.symbols, config=sf_cfg)
                log.info("SecondsFeatureEngine enabled (max_history=%.0fs, interval=%.2fs)",
                         self.seconds_features.max_history_seconds,
                         self._seconds_feature_interval)
            except Exception as _sf_exc:
                log.warning("SecondsFeatureEngine init failed (ignored): %s", _sf_exc)
                self.seconds_features = None
            if self.seconds_features is not None and sf_cfg.get("log_enabled", True):
                try:
                    from monitoring.seconds_feature_logger import SecondsFeatureLogger
                    self._seconds_logger = SecondsFeatureLogger(
                        path=sf_cfg.get("log_path", "logs/seconds_features.csv"),
                        min_interval_s=float(sf_cfg.get("log_interval_s", 1.0)),
                    )
                    log.info("SecondsFeatureLogger writing to %s", self._seconds_logger.path)
                except Exception as _sl_exc:
                    log.warning("SecondsFeatureLogger init failed (ignored): %s", _sl_exc)
                    self._seconds_logger = None

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

            # If --strategy flag given: enable only those named, disable the rest.
            # BUT: never force-enable a strategy that the preset intentionally
            # disabled with zero capital (e.g. SpotPerpBasis needs an external
            # spot feed that isn't wired). Force-enable still allows budget>0
            # strategies that are simply disabled by default.
            cfg_capital = float(sc.get("capital_allocated_usd", 0.0))
            if _force_enable:
                wanted = sc["name"].upper() in _force_enable
                enabled = wanted and cfg_capital > 0.0
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

        # ── Portfolio risk manager (concentration / family / net limits) ──
        _pf = self.cfg.get("portfolio_risk", {}) or {}
        self.portfolio_risk = PortfolioRiskManager(
            max_coin_exposure_pct=float(_pf.get("max_coin_exposure_pct", 0.35)),
            max_net_exposure_pct=float(_pf.get("max_net_exposure_pct", 0.60)),
            max_family_exposure_pct=float(_pf.get("max_family_exposure_pct", 0.40)),
            max_correlated_same_dir=int(_pf.get("max_correlated_same_dir", 2)),
        )

        # ── Phase-6: SanityCheckEngine — first-line gate ────────────────
        self.sanity = SanityCheckEngine()

        # ── Phase 4/6 (Audit): MarketQualityGate + DecisionThrottle ─────
        # Both opt-in via config — existing presets remain unaffected.
        self.market_quality_gate = None
        mqg_cfg = self.cfg.get("market_quality_gate", {}) or {}
        if mqg_cfg.get("enabled", False):
            try:
                from risk.market_quality_gate import MarketQualityGate
                self.market_quality_gate = MarketQualityGate(mqg_cfg)
                log.info("MarketQualityGate enabled")
            except Exception as _mqg_exc:
                log.warning("MarketQualityGate init failed: %s", _mqg_exc)
                self.market_quality_gate = None

        self.decision_throttle = None
        dt_cfg = self.cfg.get("decision_throttle", {}) or {}
        if dt_cfg.get("enabled", False):
            try:
                from risk.decision_throttle import DecisionThrottle
                self.decision_throttle = DecisionThrottle(dt_cfg)
                log.info("DecisionThrottle enabled")
            except Exception as _dt_exc:
                log.warning("DecisionThrottle init failed: %s", _dt_exc)
                self.decision_throttle = None

        # ── Phase-6: ExecutionPlanner — chooses MAKER/TAKER + max_pending
        self.exec_planner = ExecutionPlanner(self.cfg)

        # ── Phase-6: daily trade counters used by SanityCheckEngine ────
        self._trades_today: int = 0
        self._trades_this_hour_ts: list[float] = []
        self._daily_pnl: float = 0.0
        self._day_start_ts: float = time.time()
        self._last_book_ts: float = 0.0

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
        # Phase 5: percentage-of-notional floor so small trades aren't always
        # rejected by a fixed absolute USD floor. Effective required net is
        # max(absolute, pct_of_notional * notional).
        self._min_profit_pct  = float(ef.get("min_expected_net_profit_pct_of_notional",
                                             0.004))
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

        # ── Phase-6 LLM mode (OFF / OBSERVER / RISK_OVERLAY) ────────────
        try:
            from llm_agents.config import LLM_MODE as _lm
            self._llm_mode: str = (_lm or "OFF").upper()
        except Exception:
            self._llm_mode = "OFF"
        # GUI override: runtime/llm_mode.json (read by control loop)
        self._llm_decisions_log = log_cfg.get("llm_decisions_log",
                                              "logs/llm_decisions_v9.csv")
        Path(self._llm_decisions_log).parent.mkdir(parents=True, exist_ok=True)
        if not Path(self._llm_decisions_log).exists():
            try:
                import csv as _csv
                with open(self._llm_decisions_log, "w", newline="",
                          encoding="utf-8") as _f:
                    _csv.writer(_f).writerow([
                        "ts", "signal_id", "strategy", "symbol", "side",
                        "notional_in", "notional_out",
                        "llm_mode", "llm_decision", "llm_reason",
                        "llm_confidence", "risk_flags",
                    ])
            except Exception:
                pass

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
            coros = [
                self._orderbook_loop(),
                self._trade_loop(),
                self._minute_loop(),
                self._position_loop(),
                self._watchdog_loop(),
                self._dashboard_loop(),
                self._control_loop(),
                self._arbitrage_monitor_loop(),
            ]
            if self.seconds_features is not None:
                coros.append(self._seconds_loop())
            await asyncio.gather(*coros)
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
            self._last_book_ts = ts

            # Alpha Research: feed seconds feature engine on every book tick
            if self.seconds_features is not None:
                try:
                    self.seconds_features.update_from_book(sym, book, ts)
                except Exception as _sfe:
                    log.debug("seconds_features book update error: %s", _sfe)

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
            # Alpha Research: feed seconds feature engine on every trade
            if self.seconds_features is not None:
                try:
                    self.seconds_features.update_from_trade(sym, event, event.timestamp)
                except Exception as _sfe:
                    log.debug("seconds_features trade update error: %s", _sfe)
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

                # Phase-6: BTC 5m return → broadcast to strategies that
                # accept it (currently RSIBollingerReversion). 5 minutes
                # = 5 most-recent BTC bars.
                if sym == "BTC" and len(hist) >= 6:
                    try:
                        btc_5m = (hist[-1]["close"] / hist[-6]["close"]) - 1.0
                        for st in self.manager.strategies.values():
                            if hasattr(st, "set_btc_context"):
                                try:
                                    st.set_btc_context(btc_5m)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                for strat_name, decision in self.manager.on_bar_minute(sym, bar, ts):
                    await self._execute_decision(strat_name, decision, ts)

    # ------------------------------------------------------------------
    # Loop D — position exits (500ms)
    # ------------------------------------------------------------------

    async def _position_loop(self) -> None:
        while self._running:
            await asyncio.sleep(0.5)
            ts = time.time()

            # Phase 1: expire stale orders and release reserved capital.
            # We dedupe per pair so we only release reserve once per pair_id
            # (BUY+SELL siblings share the same pair_id and reserve).
            try:
                expired = self.executor.expire_stale_orders(ts)
            except Exception as _exp_exc:
                expired = []
                log.debug("expire_stale_orders error (ignored): %s", _exp_exc)
            _seen_expired_pairs: set[str] = set()
            for o in expired:
                pid = o.pair_id
                if pid in _seen_expired_pairs:
                    continue
                _seen_expired_pairs.add(pid)
                # Release reservation; ignore strategies that don't own the pair
                if pid in self._pair_to_reserved:
                    sname, rnotional = self._pair_to_reserved.pop(pid)
                    self.ledger.release_reserved(sname, rnotional)
                # Clear strategy mapping + clear strategy-side pending tracking
                sname2 = self._pair_to_strategy.pop(pid, "")
                if sname2:
                    st = self.manager.get(sname2)
                    if st and hasattr(st, "clear_pending"):
                        st.clear_pending(o.symbol)
                log.info("[EXPIRE] %s %s pair=%s type=%s age=%.1fs",
                         o.symbol, o.side, pid, o.order_type,
                         ts - o.placed_at)

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
                    # Phase-6: bookkeeping for SanityCheckEngine caps
                    self._daily_pnl += net_pnl
                    self._trades_today += 1
                    self._trades_this_hour_ts.append(ts)
                    if sname:
                        self.ledger.register_close(sname, pos.notional_usd, net_pnl)
                        self.portfolio_risk.register_close(
                            strategy=sname, symbol=pos.symbol,
                            side=pos.side, notional=pos.notional_usd,
                        )
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
    # Loop S — seconds features (Alpha Research framework)
    # ------------------------------------------------------------------

    async def _seconds_loop(self) -> None:
        """Sample SecondsFeatureEngine snapshots once per second.

        For each symbol with `enough_data`, log the snapshot (rate-limited
        inside the logger) and dispatch `on_second_features` to enabled
        strategies that implement the hook.
        """
        if self.seconds_features is None:
            return
        # Small initial pause so book buffers have a chance to fill.
        await asyncio.sleep(2.0)
        # NOTE: runtime/calibration_data.json is the legacy file expected
        # by the GUI calibration tab — its format is
        # {strategy_name: {coin: {...}}}.  We do NOT overwrite it here.
        # Seconds-feature snapshots + data feed health + gate stats are
        # written to a separate file so the GUI keeps working unchanged.
        data_feed_status_path = Path(self._runtime_cfg.get(
            "data_feed_status_file", "runtime/data_feed_status.json"))
        data_feed_status_path.parent.mkdir(parents=True, exist_ok=True)
        last_calib_write = 0.0
        while self._running:
            await asyncio.sleep(self._seconds_feature_interval)
            ts = time.time()
            all_feats: dict[str, dict] = {}
            for sym in self.symbols:
                try:
                    feats = self.seconds_features.get_features(sym)
                except Exception as _e:
                    log.debug("seconds_features get_features(%s) error: %s", sym, _e)
                    continue
                all_feats[sym] = feats
                if self._seconds_logger is not None and feats.get("mid") is not None:
                    try:
                        self._seconds_logger.log(feats)
                    except Exception as _le:
                        log.debug("seconds_features logger error: %s", _le)
                # Dispatch only if the snapshot has enough data — strategies
                # may still apply their own gates.
                try:
                    for strat_name, decision in self.manager.on_second_features(
                            sym, feats, ts):
                        await self._execute_decision(strat_name, decision, ts)
                except Exception as _de:
                    log.debug("on_second_features dispatch error: %s", _de)

            # Write the latest features snapshot to runtime calibration file
            # (debounced to every 5 seconds to keep disk I/O light).
            if ts - last_calib_write >= 5.0 and all_feats:
                try:
                    health = None
                    try:
                        health = self.obm.health_snapshot()
                    except Exception:
                        health = None
                    payload = {
                        "ts": ts,
                        "seconds_features": all_feats,
                        "data_feed_health": health,
                    }
                    if self.market_quality_gate is not None:
                        payload["market_quality_gate_stats"] = {
                            "total_evaluated": self.market_quality_gate.stats.total_evaluated,
                            "total_blocked": self.market_quality_gate.stats.total_blocked,
                            "blocks_by_reason": dict(self.market_quality_gate.stats.blocks_by_reason),
                        }
                        # Expose key thresholds so the GUI Triggers tab can
                        # render per-coin verdicts without re-reading config.
                        mqg_cfg = self.market_quality_gate.cfg
                        payload["market_quality_gate_cfg"] = {
                            "max_book_age_s": mqg_cfg.get("max_book_age_s"),
                            "max_trade_age_s": mqg_cfg.get("max_trade_age_s"),
                            "max_spread_bps_by_symbol": mqg_cfg.get("max_spread_bps_by_symbol"),
                            "min_volume_30s_usd_by_symbol": mqg_cfg.get("min_volume_30s_usd_by_symbol"),
                            "max_realized_vol_60s_bps": mqg_cfg.get("max_realized_vol_60s_bps"),
                            "max_toxicity_score": mqg_cfg.get("max_toxicity_score"),
                            "min_liquidity_score": mqg_cfg.get("min_liquidity_score"),
                            "ofi_block_threshold": mqg_cfg.get("ofi_block_threshold"),
                            "depth_block_threshold": mqg_cfg.get("depth_block_threshold"),
                        }
                    if self.decision_throttle is not None:
                        payload["decision_throttle_stats"] = {
                            "total_evaluated": self.decision_throttle.stats.total_evaluated,
                            "total_blocked": self.decision_throttle.stats.total_blocked,
                            "blocks_by_reason": dict(self.decision_throttle.stats.blocks_by_reason),
                        }
                    with open(data_feed_status_path, "w", encoding="utf-8") as _f:
                        json.dump(payload, _f, default=str)
                    last_calib_write = ts
                except Exception as _ce:
                    log.debug("data_feed_status write error: %s", _ce)

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
        llm_mode_file = Path("runtime/llm_mode.json")
        status_s     = float(self._runtime_cfg.get("status_interval_s", 60))

        control_file.parent.mkdir(parents=True, exist_ok=True)
        last_cmd_id   = None
        last_status_t = -999999.0

        # Pick up any persisted LLM mode from a previous session.
        try:
            if llm_mode_file.exists():
                with open(llm_mode_file, encoding="utf-8") as f:
                    saved = json.load(f)
                m = (saved.get("mode") or "").upper()
                if m in ("OFF", "OBSERVER", "RISK_OVERLAY"):
                    self._llm_mode = m
                    log.info("[ENGINE] LLM mode loaded from runtime/llm_mode.json: %s", m)
        except Exception:
            pass

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

            # Portfolio-level open registration (Phase 3)
            self.portfolio_risk.register_open(
                strategy=strat_name,
                symbol=fill.symbol,
                side=fill.side,
                notional=fill.notional_usd,
            )

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

        # ── PLACE actions: SANITY → ledger → portfolio → KS → EF → LLM → execute ──
        if action in ("PLACE_QUOTES", "PLACE_BUY", "PLACE_SELL"):
            if not self._trading_enabled or ts < self._pause_until:
                return

            strat = self.manager.get(strat_name)
            requested_notional = decision.notional_usd or (
                strat.config.max_position_size_usd if strat else 50.0
            )

            if requested_notional <= 0:
                log.debug("[BLOCK] %s %s zero notional — no capital allocated",
                          strat_name, sym)
                return

            # Gate 0: SanityCheckEngine — first-line guard on the decision
            # itself. Catches bad book, bad stop/TP, missing fields,
            # stale data, daily-trade caps before any state mutation.
            try:
                pending_syms = self._symbols_with_pending()
                open_syms    = self._symbols_with_positions()
                # Make sure notional is filled on the decision for the
                # validator (some strategies leave it None).
                if not decision.notional_usd:
                    decision.notional_usd = requested_notional
                strategy_cfg_for_sanity = {
                    "max_position_size_usd": (
                        strat.config.max_position_size_usd if strat else 1e18
                    ),
                }
                strategy_states = {
                    n: s.state(ts) for n, s in self.manager.strategies.items()
                }
                engine_state_for_sanity = {
                    "now":                    ts,
                    "last_book_ts":           self._last_book_ts,
                    "last_heartbeat_ts":      getattr(self.ks, "_last_heartbeat", ts),
                    "daily_pnl":              self._daily_pnl,
                    "trades_today":           self._trades_today,
                    "trades_this_hour":       self._trades_in_last_hour(ts),
                    "pending_symbols":        pending_syms,
                    "open_position_symbols":  open_syms,
                    "allow_multi_position":   self.cfg.get(
                        "allow_multi_position", True),
                    "strategy_states":        strategy_states,
                    "btc_vol_guard":          (
                        ts < getattr(self.ks, "_volguard_until", 0.0)
                    ),
                }
                ok_sa, sa_reason, sa_details = self.sanity.validate_decision(
                    decision=decision, strategy_name=strat_name,
                    book=book, engine_state=engine_state_for_sanity,
                    config=self.cfg, strategy_config=strategy_cfg_for_sanity,
                )
                if not ok_sa:
                    log.info("[SANITY] BLOCK %s %s → %s details=%s",
                             strat_name, sym, sa_reason, sa_details)
                    self._log_risk_event(
                        strat_name, sym, action, requested_notional,
                        False, sa_reason,
                    )
                    return
            except Exception as _sa_exc:
                # Defensive: a buggy sanity check should never crash the
                # engine. Log and continue without blocking.
                log.debug("SanityCheck error (ignored): %s", _sa_exc)

            # Gate 0b: MarketQualityGate (microstructure quality)
            if self.market_quality_gate is not None:
                try:
                    side_for_mqg = "long" if action == "PLACE_BUY" else (
                        "short" if action == "PLACE_SELL" else "long"
                    )
                    feats_for_mqg = {}
                    if self.seconds_features is not None:
                        try:
                            feats_for_mqg = self.seconds_features.get_features(sym)
                        except Exception:
                            feats_for_mqg = {}
                    health_for_mqg = None
                    try:
                        health_for_mqg = self.obm.health_snapshot()
                    except Exception:
                        health_for_mqg = None
                    ok_mqg, mqg_reason, mqg_details = self.market_quality_gate.evaluate(
                        sym, side_for_mqg, feats_for_mqg, book=book,
                        health=health_for_mqg, now=ts,
                    )
                    if not ok_mqg:
                        log.info("[MQG] BLOCK %s %s %s → %s",
                                 strat_name, sym, side_for_mqg, mqg_reason)
                        self._log_risk_event(
                            strat_name, sym, action, requested_notional,
                            False, f"market_quality:{mqg_reason}")
                        try:
                            self.decision_logger.log_skip(
                                symbol=sym,
                                reason=f"market_quality:{mqg_reason}",
                                timestamp=ts,
                                mid=feats_for_mqg.get("mid"),
                                spread_bps=feats_for_mqg.get("spread_bps"),
                                obi=feats_for_mqg.get("obi_5"),
                            )
                        except Exception:
                            pass
                        return
                except Exception as _mqg_exc:
                    log.debug("MQG error (ignored): %s", _mqg_exc)

            # Gate 0c: DecisionThrottle (rate-limit entries)
            if self.decision_throttle is not None:
                ok_dt, dt_reason = self.decision_throttle.check(
                    strat_name, sym, action, now=ts)
                if not ok_dt:
                    log.info("[THROTTLE] BLOCK %s %s %s → %s",
                             strat_name, sym, action, dt_reason)
                    self._log_risk_event(
                        strat_name, sym, action, requested_notional,
                        False, f"throttle:{dt_reason}")
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

            # Gate 2: portfolio-level concentration / family / net limits.
            # Determine intended side for the directional case; PLACE_QUOTES
            # quotes both sides so we treat them as net-neutral and only
            # enforce coin & family limits (side="BUY" probe is a no-op for
            # the net check when both sides are quoted).
            _side = "BUY" if action in ("PLACE_BUY", "PLACE_QUOTES") else "SELL"
            ok_pf, pf_reason = self.portfolio_risk.can_open(
                strategy=strat_name, symbol=sym, side=_side,
                notional=requested_notional,
                total_capital=self.equity,
            )
            if not ok_pf:
                self.ledger.release_reserved(strat_name, requested_notional)
                log.info("[PORTFOLIO] BLOCK %s %s $%.0f → %s",
                         strat_name, sym, requested_notional, pf_reason)
                self._log_risk_event(strat_name, sym, action,
                                     requested_notional, False, pf_reason)
                return

            # Gate 3: global KillSwitch
            ok_ks, _ = self.ks.can_open()
            if not ok_ks:
                self.ledger.release_reserved(strat_name, requested_notional)
                return

            # Gate 4: execution filter (min net profit + cooldown)
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

            # Gate 5: LLM mode (OFF / OBSERVER / RISK_OVERLAY)
            try:
                decision = await asyncio.to_thread(
                    self._apply_llm_mode_sync,
                    strat_name, decision, ts, book,
                )
                if decision.action == "SKIP":
                    self.ledger.release_reserved(strat_name,
                                                 requested_notional)
                    return
                action = decision.action
                # The LLM may have reduced the notional. Re-sync the
                # requested_notional we already reserved so we don't
                # leak budget when the size shrinks.
                new_notional = float(decision.notional_usd or 0.0)
                if 0 < new_notional < requested_notional:
                    delta = requested_notional - new_notional
                    self.ledger.release_reserved(strat_name, delta)
                    requested_notional = new_notional
            except Exception as _llm_exc:
                log.debug("LLM mode dispatcher error (ignored): %s", _llm_exc)

            self._log_risk_event(strat_name, sym, action,
                                 requested_notional, True, "")

            # ── Place ──────────────────────────────────────────────────
            if action == "PLACE_QUOTES":
                notional_for_order = decision.notional_usd or requested_notional
                # PLACE_QUOTES comes from S8EMS-style market-making strategies
                pair_id = self.executor.place_quotes(
                    sym, decision.buy_price, decision.sell_price,
                    decision.size, notional_for_order,
                    order_type="MAKER_SIM",
                    signal_id=getattr(decision, "signal_id", ""),
                    strategy=strat_name)
                if pair_id:
                    self._pair_to_strategy[pair_id] = strat_name
                    self._pair_to_reserved[pair_id] = (strat_name,
                                                        requested_notional)
                    st = self.manager.get(strat_name)
                    if st and hasattr(st, "register_pending"):
                        st.register_pending(sym, [f"b_{pair_id}",
                                                   f"s_{pair_id}"])
                    if self.decision_throttle is not None:
                        try:
                            self.decision_throttle.record_entry(strat_name, sym, now=ts)
                        except Exception:
                            pass
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

                # Phase-6: ExecutionPlanner decides MAKER vs TAKER + limit.
                try:
                    plan = self.exec_planner.plan(decision, book)
                except ValueError as _plan_exc:
                    log.info("[PLAN] BLOCK %s %s → %s", strat_name, sym, _plan_exc)
                    self.ledger.release_reserved(strat_name, requested_notional)
                    return

                # Build the dual-leg call: only the directional leg uses
                # the planned limit price; the other leg is unfillable.
                if action == "PLACE_BUY":
                    buy_p  = plan.limit_price
                    sell_p = 9_999_999.0
                    size   = decision.size or (notional / max(buy_p, 1e-9))
                else:
                    buy_p  = 0.000_001
                    sell_p = plan.limit_price
                    size   = decision.size or (notional / max(sell_p, 1e-9))

                pair_id = self.executor.place_quotes(
                    sym, buy_p, sell_p, size, notional,
                    order_type=plan.order_type,
                    signal_id=plan.signal_id, strategy=strat_name)
                if pair_id:
                    self._pair_to_strategy[pair_id] = strat_name
                    self._pair_to_reserved[pair_id] = (strat_name, notional)
                    if self.decision_throttle is not None:
                        try:
                            self.decision_throttle.record_entry(strat_name, sym, now=ts)
                        except Exception:
                            pass
                    log.debug("[%s] %s %s notional=$%.0f type=%s",
                              action, strat_name, sym, notional, plan.order_type)
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

        # Phase 5: effective minimum-net-profit is the larger of
        #   - absolute USD floor (default 3.0)
        #   - pct-of-notional floor (default 0.4% of position size)
        # This unblocks small positions (e.g. $3 micro-live) while still
        # keeping large positions (e.g. $250) under a meaningful absolute floor.
        min_required = max(self._min_net_profit,
                           self._min_profit_pct * float(notional))
        passes, blocked, econ = strat.passes_min_edge_filter(
            entry=entry, tp=tp, sl=sl, notional=notional, side=side,
            min_net_profit=min_required, min_rr=self._min_rr,
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
        # Phase-6 trade counters used by SanityCheckEngine
        self._daily_pnl += net_pnl
        self._trades_today += 1
        self._trades_this_hour_ts.append(ts)

        self.ledger.register_close(strat_name, pos.notional_usd, net_pnl)
        self.portfolio_risk.register_close(
            strategy=strat_name, symbol=pos.symbol,
            side=pos.side, notional=pos.notional_usd,
        )
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
    # Phase-6 LLM mode dispatcher (OFF / OBSERVER / RISK_OVERLAY)
    # ------------------------------------------------------------------

    def _apply_llm_mode_sync(self, strat_name: str,
                              decision: StrategyDecision,
                              ts: float, book) -> StrategyDecision:
        """
        Resolve LLM mode and apply its multiplier to the decision.

        Hard guarantees (also enforced inside llm_agents/modes.py):
          - multiplier is always in [0, 1] (LLM can NEVER scale UP).
          - action is never changed by the LLM (only set to SKIP if BLOCK).
          - stop_loss / take_profit are never touched.
        """
        from llm_agents.modes import apply_llm_mode
        from llm_agents.schemas import LLMSnapshot

        mode = self._llm_mode

        # OFF mode: cheap path, no snapshot construction.
        if mode == "OFF":
            return decision

        # Sampling: skip a fraction of calls to keep API costs bounded.
        if mode == "RISK_OVERLAY" and self._llm_sample_rate < 1.0:
            import random as _random
            if _random.random() > self._llm_sample_rate:
                return decision

        sym = decision.symbol
        # Build the lean snapshot
        spread_bps = 0.0
        if book and book.best_bid and book.best_ask and book.mid:
            spread_bps = (book.best_ask - book.best_bid) / book.mid * 10_000.0
        side = "BUY" if decision.action == "PLACE_BUY" else (
            "SELL" if decision.action == "PLACE_SELL" else "NEUTRAL"
        )
        entry = (book.best_ask if side == "BUY"
                 else (book.best_bid if side == "SELL" else book.mid)) or 0.0
        snapshot = LLMSnapshot(
            symbol=sym, strategy=strat_name, side=side,
            entry=float(entry or 0.0),
            stop=float(decision.stop_loss or 0.0),
            take_profit=float(decision.take_profit or 0.0),
            notional=float(decision.notional_usd or 0.0),
            spread_bps=spread_bps,
            expected_edge_bps=float(decision.expected_edge_bps or 0.0),
            estimated_cost_bps=float(decision.estimated_cost_bps or 0.0),
            reward_risk_ratio=float(decision.reward_risk_ratio or 0.0),
            open_positions_count=len(self.executor.open_positions),
            daily_pnl=self._daily_pnl,
            signal_reason=decision.reason,
        )

        result = apply_llm_mode(
            mode=mode, overlay=self._llm_overlay,
            decision=decision, snapshot=snapshot,
            risk_overlay_callable=None,
        )

        notional_in  = float(decision.notional_usd or 0.0)
        notional_out = notional_in * result.multiplier

        # Log to llm_decisions_v9.csv (best effort, never raises)
        self._log_llm_decision(
            ts=ts, signal_id=getattr(decision, "signal_id", ""),
            strategy=strat_name, symbol=sym, side=side,
            notional_in=notional_in, notional_out=notional_out,
            llm_mode=mode, llm_decision=result.action,
            llm_reason=result.reason,
            llm_confidence=result.confidence,
            risk_flags=result.risk_flags,
        )

        # Apply the result
        if result.multiplier <= 0.0:
            import dataclasses
            return dataclasses.replace(
                decision, action="SKIP",
                reason=f"llm_block: {result.reason}",
            )
        if result.multiplier < 1.0:
            import dataclasses
            return dataclasses.replace(decision, notional_usd=notional_out)
        return decision

    def _log_llm_decision(self, *, ts: float, signal_id: str,
                           strategy: str, symbol: str, side: str,
                           notional_in: float, notional_out: float,
                           llm_mode: str, llm_decision: str,
                           llm_reason: str, llm_confidence: float,
                           risk_flags: list) -> None:
        try:
            import csv as _csv
            with open(self._llm_decisions_log, "a", newline="",
                      encoding="utf-8") as _f:
                w = _csv.writer(_f)
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    signal_id, strategy, symbol, side,
                    round(notional_in, 4), round(notional_out, 4),
                    llm_mode, llm_decision, llm_reason,
                    round(float(llm_confidence or 0.0), 4),
                    "|".join(risk_flags or []),
                ])
        except Exception as exc:
            log.debug("llm decisions log write failed: %s", exc)

    # ------------------------------------------------------------------
    # Phase-6 helpers
    # ------------------------------------------------------------------

    def _symbols_with_pending(self) -> set:
        return {o.symbol for o in self.executor.pending_orders}

    def _symbols_with_positions(self) -> set:
        return {p.symbol for p in self.executor.open_positions}

    def _trades_in_last_hour(self, now: float) -> int:
        # Keep only the last-hour timestamps.
        cutoff = now - 3600.0
        self._trades_this_hour_ts = [t for t in self._trades_this_hour_ts
                                     if t >= cutoff]
        return len(self._trades_this_hour_ts)

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

            import os as _os

            def _atomic_write(path: Path, data) -> None:
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, default=str)
                try:
                    _os.replace(tmp, path)
                except OSError:
                    # Windows: destination locked by GUI reader — write directly
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(data, f, default=str)
                    finally:
                        try:
                            tmp.unlink(missing_ok=True)
                        except OSError:
                            pass

            _atomic_write(status_file, status)
            _atomic_write(calib_file, self.manager.get_calibration_data())
        except Exception as e:
            log.debug("Status write failed: %s", e)

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

            elif command == "set_llm_mode":
                mode = str(args.get("mode", "OFF")).upper()
                if mode not in ("OFF", "OBSERVER", "RISK_OVERLAY"):
                    return {"ok": False, "error": f"invalid mode: {mode}"}
                self._llm_mode = mode
                # Persist for next start
                try:
                    Path("runtime").mkdir(parents=True, exist_ok=True)
                    with open("runtime/llm_mode.json", "w",
                              encoding="utf-8") as f:
                        json.dump({"mode": mode, "ts": ts}, f)
                except Exception:
                    pass
                log.info("[ENGINE] set_llm_mode → %s", mode)
                return {"ok": True, "llm_mode": mode}

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
            self.portfolio_risk.register_close(
                strategy=strategy_name, symbol=pos.symbol,
                side=pos.side, notional=pos.notional_usd,
            )
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
        # Dedupe noisy recurring blocks (e.g. sanity rejecting every book
        # tick) — keep at most ONE risk-event per (strategy, symbol, reason
        # head) per minute. Successful actions and other entries always pass.
        if not allowed and block_reason:
            head = block_reason.split(":", 1)[0]
            key = (strategy, symbol, head)
            now = time.time()
            if not hasattr(self, "_risk_event_dedupe"):
                self._risk_event_dedupe = {}
                self._risk_event_counts = {}
            self._risk_event_counts[key] = self._risk_event_counts.get(key, 0) + 1
            last_logged = self._risk_event_dedupe.get(key, 0.0)
            if now - last_logged < 60.0:
                # Suppress; we still keep the counter for stats.
                return
            self._risk_event_dedupe[key] = now
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
        if self._seconds_logger is not None:
            try:
                self._seconds_logger.flush()
            except Exception:
                pass
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
