"""
backtesting/backtest_engine.py — Backtester véridique (bar-replay).

PHASE 1 — réécriture honnête des sorties :
  * `_process_exits` consomme bar.high / bar.low : un stop/TP est touché si le
    RANGE de la barre traverse le niveau (plus seulement le close). Fill AU
    niveau (+ slippage), pas au close.
  * Règle PESSIMISTE : si stop et TP sont tous deux dans la barre → le stop
    (l'adverse) est servi d'abord. Idem un stop est servi avant la liquidation
    (plus éloignée).
  * Accrual de FUNDING : à chaque frontière de funding traversée pendant la
    détention, on applique funding_rate × notional × signe (un long paie si f>0).
  * LIQUIDATION intrabar pour les positions à levier : prix de liquidation déduit
    du levier et de la marge de maintenance ; si low/high le franchit dans la
    barre → liquidation à ce niveau, perte = marge.

Zéro look-ahead : la décision est calculée sur la barre t (on_bar_minute) et
remplie au CLOSE de t ; les sorties d'une position ne sont évaluées qu'à partir
de la barre suivante, via son high/low. Aucune information future n'est utilisée.

L'interface publique (run(), metrics()) est inchangée. `funding_by_symbol` et
`maint_margin_rate` sont des paramètres optionnels (rétro-compatibles).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from .metrics import compute_metrics

log = logging.getLogger(__name__)

_DEFAULT_FEE_BPS = 3.0
_DEFAULT_SLIPPAGE_BPS = 4.0
_DEFAULT_MAINT_MARGIN_RATE = 0.005   # 0.5% — marge de maintenance par défaut


@dataclass
class _SimPosition:
    pos_id:       str
    strategy:     str
    symbol:       str
    side:         str           # "BUY" | "SELL"
    notional:     float
    entry:        float
    entry_ts:     float
    tp:           float = 0.0
    sl:           float = 0.0
    max_hold_ts:  float = 0.0
    leverage:     float = 1.0
    margin:       float = 0.0
    liq_price:    float = 0.0   # 0 = pas de liquidation possible (levier 1x)


class BacktestEngine:
    """Bar-replay backtester véridique pour une stratégie. Voir docstring module."""

    def __init__(self, strategy_cls, config, bars: list,
                 fee_bps: float = _DEFAULT_FEE_BPS,
                 slippage_bps: float = _DEFAULT_SLIPPAGE_BPS,
                 funding_by_symbol: Optional[dict] = None,
                 maint_margin_rate: float = _DEFAULT_MAINT_MARGIN_RATE):
        from strategies.base_strategy import StrategyConfig
        if not isinstance(config, StrategyConfig):
            raise TypeError("config must be a StrategyConfig instance")

        self.strategy = strategy_cls(config)
        self.bars = sorted(bars, key=lambda b: getattr(b, "ts", 0))
        self.fee_bps = float(fee_bps)
        self.slippage_bps = float(slippage_bps)
        self.maint_margin_rate = float(maint_margin_rate)
        # {symbol: [(ts_seconds, funding_rate_per_period), ...]} trié par ts.
        self.funding: dict[str, list] = {
            k: sorted(v, key=lambda x: x[0])
            for k, v in (funding_by_symbol or {}).items()
        }

        self._positions: dict[str, _SimPosition] = {}
        self._closed_trades: list[dict] = []

    # ------------------------------------------------------------------

    def run(self) -> list[dict]:
        for bar in self.bars:
            sym = getattr(bar, "symbol", "")
            ts = float(getattr(bar, "ts", 0))
            close = float(getattr(bar, "close", 0))
            if not sym or close <= 0:
                continue

            # 1) Sorties intrabar sur les positions ouvertes (range de la barre)
            self._process_exits(sym, bar, ts)

            # 2) Barre → décision éventuelle (fill au close de t)
            try:
                decision = self.strategy.on_bar_minute(sym, bar, ts)
            except Exception as exc:
                log.debug("Strategy raised on bar %s @%s: %s", sym, ts, exc)
                decision = None
            if decision is None:
                continue

            action = getattr(decision, "action", None)
            if action == "PLACE_BUY":
                self._open(self.strategy.config.name, sym, "BUY", close, ts, decision)
            elif action == "PLACE_SELL":
                self._open(self.strategy.config.name, sym, "SELL", close, ts, decision)
            elif action == "CLOSE":
                self._close_for(sym, close, ts, reason="signal_close")

        # Flush des positions restantes au dernier close de leur symbole
        last_close = {getattr(b, "symbol", ""): float(getattr(b, "close", 0) or 0)
                      for b in self.bars}
        last_ts = {getattr(b, "symbol", ""): float(getattr(b, "ts", 0) or 0)
                   for b in self.bars}
        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            self._close_trade(pos, last_close.get(pos.symbol, pos.entry),
                              last_ts.get(pos.symbol, pos.entry_ts), "eob_flush")
        return self._closed_trades

    # ------------------------------------------------------------------

    def _open(self, strat_name: str, symbol: str, side: str,
              bar_close: float, ts: float, decision) -> None:
        notional = float(getattr(decision, "notional_usd", 0) or 0)
        if notional <= 0:
            notional = float(getattr(self.strategy.config, "max_position_size_usd", 0) or 0)
        if notional <= 0:
            return
        if any(p.symbol == symbol and p.strategy == strat_name
               for p in self._positions.values()):
            return

        slip = self.slippage_bps / 10_000.0
        entry = bar_close * (1.0 + slip) if side == "BUY" else bar_close * (1.0 - slip)
        tp = float(getattr(decision, "take_profit", 0) or 0)
        sl = float(getattr(decision, "stop_loss", 0) or 0)
        max_hold_s = float(getattr(decision, "max_hold_seconds", 0) or 0)

        meta = getattr(decision, "metadata", {}) or {}
        leverage = float(meta.get("leverage", 1.0) or 1.0)
        leverage = max(1.0, leverage)
        margin = notional / leverage
        liq_price = self._liq_price(side, entry, leverage)

        pos = _SimPosition(
            pos_id=str(uuid.uuid4())[:8], strategy=strat_name, symbol=symbol,
            side=side, notional=notional, entry=entry, entry_ts=ts, tp=tp, sl=sl,
            max_hold_ts=(ts + max_hold_s) if max_hold_s > 0 else 0.0,
            leverage=leverage, margin=margin, liq_price=liq_price,
        )
        self._positions[pos.pos_id] = pos

    def _liq_price(self, side: str, entry: float, leverage: float) -> float:
        """Prix de liquidation : mouvement adverse de (1/L − mm_rate).
        Levier 1x ⇒ distance ~99.5% ⇒ liquidation de fait impossible."""
        liq_frac = (1.0 / leverage) - self.maint_margin_rate
        if liq_frac <= 0:
            liq_frac = 1e-6
        return entry * (1.0 - liq_frac) if side == "BUY" else entry * (1.0 + liq_frac)

    def _process_exits(self, symbol: str, bar, ts: float) -> None:
        high = float(getattr(bar, "high", getattr(bar, "close", 0)) or 0)
        low = float(getattr(bar, "low", getattr(bar, "close", 0)) or 0)
        close = float(getattr(bar, "close", 0) or 0)

        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            if pos.symbol != symbol:
                continue

            reason = exit_px = None
            if pos.side == "BUY":
                # Niveaux adverses (du plus proche au plus éloigné) : stop puis liq.
                if pos.sl and low <= pos.sl:
                    reason, exit_px = "stop_loss", pos.sl
                elif pos.liq_price and low <= pos.liq_price:
                    reason, exit_px = "liquidation", pos.liq_price
                elif pos.tp and high >= pos.tp:
                    reason, exit_px = "take_profit", pos.tp
            else:  # SELL
                if pos.sl and high >= pos.sl:
                    reason, exit_px = "stop_loss", pos.sl
                elif pos.liq_price and high >= pos.liq_price:
                    reason, exit_px = "liquidation", pos.liq_price
                elif pos.tp and low <= pos.tp:
                    reason, exit_px = "take_profit", pos.tp

            # Time-stop : seulement si aucun évènement de prix intrabar.
            if reason is None and pos.max_hold_ts and ts >= pos.max_hold_ts:
                reason, exit_px = "max_hold", close

            if reason:
                self._close_trade(pos, exit_px, ts, reason)

    def _close_for(self, symbol: str, close: float, ts: float, reason: str) -> None:
        for pid in list(self._positions.keys()):
            pos = self._positions[pid]
            if pos.symbol == symbol:
                self._close_trade(pos, close, ts, reason)

    # ------------------------------------------------------------------

    def _funding_accrued(self, pos: _SimPosition, exit_ts: float) -> float:
        """Somme du funding payé/reçu sur les frontières franchies pendant la
        détention. Un long PAIE quand f>0 (pnl négatif), un short REÇOIT."""
        series = self.funding.get(pos.symbol)
        if not series:
            return 0.0
        sign = -1.0 if pos.side == "BUY" else +1.0   # long paie f>0
        total = 0.0
        for t, rate in series:
            if pos.entry_ts < t <= exit_ts:
                total += sign * rate * pos.notional
        return total

    def _close_trade(self, pos: _SimPosition, exit_price: float,
                     ts: float, reason: str) -> None:
        funding_pnl = self._funding_accrued(pos, ts)

        if reason == "liquidation":
            # La position perd la totalité de la marge (collateral). Le funding
            # déjà accru jusqu'à la liquidation s'applique aussi.
            gross = -pos.margin
            fee = (self.fee_bps / 10_000.0) * pos.notional        # frais d'entrée seuls
            net = gross + funding_pnl - fee
            exit_eff = exit_price
        else:
            slip = self.slippage_bps / 10_000.0
            exit_eff = (exit_price * (1.0 - slip) if pos.side == "BUY"
                        else exit_price * (1.0 + slip))
            if pos.side == "BUY":
                gross = (exit_eff - pos.entry) / pos.entry * pos.notional
            else:
                gross = (pos.entry - exit_eff) / pos.entry * pos.notional
            fee = 2.0 * (self.fee_bps / 10_000.0) * pos.notional
            net = gross - fee + funding_pnl

        self._closed_trades.append({
            "ts": ts, "symbol": pos.symbol, "strategy": pos.strategy,
            "side": pos.side, "notional": round(pos.notional, 2),
            "leverage": pos.leverage, "margin": round(pos.margin, 4),
            "entry": pos.entry, "exit": exit_eff,
            "gross": round(gross, 6), "fee": round(fee, 6),
            "funding": round(funding_pnl, 6), "net": round(net, 6),
            "hold_s": round(ts - pos.entry_ts, 1), "reason": reason,
        })
        self._positions.pop(pos.pos_id, None)

    # ------------------------------------------------------------------

    def metrics(self) -> dict:
        return compute_metrics(self._closed_trades)
