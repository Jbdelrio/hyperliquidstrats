"""
s8_ems.py — S8 Econophysics Maker Scalping.

Orchestrates 5 sensors:
  1. HurstLocalEstimator      → size multiplier (regime)
  2. HARRealizedVolatility    → size multiplier (vol prediction)
  3. WaveletSingularityDetector → cancel quotes on singularity
  4. BouchaudImpactModel      → quote skew (order-flow pressure)
  5. KalmanFairValue          → clean fair value + drift skew

All computation is stateless from the engine's perspective — state lives
inside each CoinState instance maintained here.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from econophysics.hurst_local import HurstLocalEstimator
from econophysics.har_rv import HARRealizedVolatility
from econophysics.wavelet_singularity import WaveletSingularityDetector
from econophysics.bouchaud_impact import BouchaudImpactModel
from econophysics.kalman_fair_value import KalmanFairValue

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-coin state
# ---------------------------------------------------------------------------

@dataclass
class CoinState:
    symbol: str
    hurst:   HurstLocalEstimator     = field(default_factory=lambda: HurstLocalEstimator(window=300))
    har_rv:  HARRealizedVolatility   = field(default_factory=HARRealizedVolatility)
    wavelet: WaveletSingularityDetector = field(default_factory=WaveletSingularityDetector)
    bouchaud: BouchaudImpactModel    = field(default_factory=BouchaudImpactModel)
    kalman:  KalmanFairValue         = field(default_factory=lambda: KalmanFairValue())

    # Position (None = flat)
    open_position: Optional[dict] = None

    # Quote refresh tracker
    last_quote_ts: float = 0.0

    # Stats
    fills: int = 0
    pnl: float = 0.0


# ---------------------------------------------------------------------------
# Action type constants
# ---------------------------------------------------------------------------
ACTION_PLACE_QUOTES   = "place_quotes"
ACTION_CANCEL_QUOTES  = "cancel_quotes"
ACTION_MANAGE_POS     = "manage_position"
ACTION_CLOSE_MARKET   = "close_market"


# ---------------------------------------------------------------------------
# Main strategy class
# ---------------------------------------------------------------------------

class S8EconophysicsMakerScalping:
    """
    Stateless decision-maker; all per-coin state stored in CoinState objects.

    Engine calls:
      on_orderbook_update()  → returns action or None
      on_trade_event()       → returns action or None (wavelet cancel)
      on_fill()              → returns manage_position action
      on_minute_close()      → updates HAR-RV (no action)
      check_position_exits() → returns close_market or None
    """

    def __init__(self, params: dict, capital: float, symbols: list[str]):
        self.capital  = capital
        self.symbols  = [s.upper() for s in symbols]
        self.p        = params

        self.coin_states: dict[str, CoinState] = {
            s: CoinState(symbol=s) for s in self.symbols
        }

        self.total_trades  = 0
        self.daily_pnl     = 0.0

        # Per-symbol pending order IDs (so engine can cancel)
        self._pending_ids: dict[str, list[str]] = {s: [] for s in self.symbols}

    # ------------------------------------------------------------------
    # 1. Orderbook update → quote decision
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, timestamp: float) -> Optional[dict]:
        """
        Main decision gate. Called on every L2 update.
        Returns action dict or None.
        """
        state = self.coin_states.get(symbol)
        if state is None:
            return None

        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None

        mid    = (bid + ask) / 2
        spread = ask - bid
        spread_bps = spread / mid * 10_000

        # Update Kalman with current mid (VWAP fed from engine later via get_vwap)
        fv, drift = state.kalman.update(mid)

        # Update Hurst
        state.hurst.update(mid)

        # ── Filters ──────────────────────────────────────────────────
        min_bps = self.p.get("min_spread_bps", 4.0)
        max_bps = self.p.get("max_spread_bps", 20.0)
        if not (min_bps <= spread_bps <= max_bps):
            return self._cancel_action(symbol)

        if state.wavelet.is_alert_active(timestamp):
            return self._cancel_action(symbol)

        hurst_mult = state.hurst.get_size_multiplier()
        if hurst_mult <= 0.0:
            return self._cancel_action(symbol)

        har_mult = state.har_rv.get_size_multiplier()
        if har_mult < 0.3:
            return self._cancel_action(symbol)

        # Position already open — no new quotes
        if state.open_position is not None:
            return None

        # Don't requote if fresh enough
        refresh_s = self.p.get("quote_refresh_s", 5.0)
        if (timestamp - state.last_quote_ts) < refresh_s:
            return None

        # ── Compute quotes ───────────────────────────────────────────
        return self._compute_quotes(state, bid, ask, mid, spread, fv, drift,
                                    hurst_mult, har_mult, timestamp)

    # ------------------------------------------------------------------
    # 2. Trade event → wavelet singularity check
    # ------------------------------------------------------------------

    def on_trade_event(self, symbol: str, price: float, volume_usd: float,
                       best_bid: float, best_ask: float,
                       side: str, timestamp: float) -> Optional[dict]:
        state = self.coin_states.get(symbol)
        if state is None:
            return None

        state.bouchaud.add_trade(timestamp, price, volume_usd,
                                  best_bid, best_ask, side)

        alert = state.wavelet.update(price, timestamp)
        if alert:
            log.debug("[%s] Wavelet singularity alert", symbol)
            return self._cancel_action(symbol)

        return None

    # ------------------------------------------------------------------
    # 3. Fill → open position + manage legs
    # ------------------------------------------------------------------

    def on_fill(self, symbol: str, side: str, fill_price: float,
                size_units: float, notional_usd: float, timestamp: float) -> dict:
        state = self.coin_states[symbol]
        state.fills += 1
        self.total_trades += 1

        stop_bps   = self.p.get("stop_loss_bps", 30) / 10_000
        max_hold_s = self.p.get("max_hold_s", 60)
        tp_capture = 0.60

        # TP distance = stop_distance × tp_capture (symmetric around stop size)
        stop_dist = fill_price * stop_bps

        if side == "BUY":
            tp_price   = fill_price + stop_dist * tp_capture
            stop_price = fill_price * (1 - stop_bps)
        else:
            tp_price   = fill_price - stop_dist * tp_capture
            stop_price = fill_price * (1 + stop_bps)

        state.open_position = {
            "side":        side,
            "entry":       fill_price,
            "size":        size_units,
            "notional":    notional_usd,
            "tp":          tp_price,
            "stop":        stop_price,
            "opened_at":   timestamp,
            "max_hold_ts": timestamp + max_hold_s,
        }

        close_side = "SELL" if side == "BUY" else "BUY"
        return {
            "action":     ACTION_MANAGE_POS,
            "symbol":     symbol,
            "tp_price":   round(tp_price, 8),
            "stop_price": round(stop_price, 8),
            "close_side": close_side,
            "size":       size_units,
            "notional":   notional_usd,
        }

    # ------------------------------------------------------------------
    # 4. Minute close → update HAR-RV
    # ------------------------------------------------------------------

    def on_minute_close(self, symbol: str, return_1m: float) -> None:
        state = self.coin_states.get(symbol)
        if state:
            state.har_rv.update(return_1m)

    # ------------------------------------------------------------------
    # 5. Exit check (called every 500ms by engine)
    # ------------------------------------------------------------------

    def check_position_exits(self, symbol: str, mid: float,
                              best_bid: float, best_ask: float,
                              timestamp: float) -> Optional[dict]:
        state = self.coin_states.get(symbol)
        if state is None or state.open_position is None:
            return None

        pos = state.open_position
        side = pos["side"]

        stop_hit = (side == "BUY"  and mid <= pos["stop"]) or \
                   (side == "SELL" and mid >= pos["stop"])
        # Maker TP: BUY limit-sell fills when bid rises to tp;
        #           SELL limit-buy fills when ask drops to tp
        tp_hit   = (side == "BUY"  and best_bid >= pos["tp"]) or \
                   (side == "SELL" and best_ask <= pos["tp"])
        max_hold = timestamp >= pos["max_hold_ts"]

        if not (stop_hit or tp_hit or max_hold):
            return None

        reason = "stop_loss" if stop_hit else ("take_profit" if tp_hit else "max_hold")
        if reason == "stop_loss":
            exit_price = best_bid if side == "BUY" else best_ask
        elif reason == "take_profit":
            exit_price = pos["tp"]
        else:
            exit_price = mid

        # PnL
        if side == "BUY":
            pnl = (exit_price - pos["entry"]) / pos["entry"] * pos["notional"]
        else:
            pnl = (pos["entry"] - exit_price) / pos["entry"] * pos["notional"]

        # Fees: maker entry (+0.3 bps), maker TP (−0.3 bps rebate), taker stop (+3 bps)
        entry_rebate = pos["notional"] * 0.3 / 10_000
        if reason == "take_profit":
            exit_fee = -pos["notional"] * 0.3 / 10_000   # rebate
        else:
            exit_fee = pos["notional"] * 3.0 / 10_000
        net_pnl = pnl + entry_rebate - exit_fee

        state.pnl += net_pnl
        self.daily_pnl += net_pnl
        state.open_position = None

        close_side = "SELL" if side == "BUY" else "BUY"
        return {
            "action":      ACTION_CLOSE_MARKET,
            "symbol":      symbol,
            "close_side":  close_side,
            "exit_price":  round(exit_price, 8),
            "size":        pos["size"],
            "notional":    pos["notional"],
            "net_pnl":     net_pnl,
            "reason":      reason,
            "hold_s":      timestamp - pos["opened_at"],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_quotes(self, state: CoinState,
                        bid: float, ask: float, mid: float, spread: float,
                        fv: float, drift: float,
                        hurst_mult: float, har_mult: float,
                        timestamp: float) -> Optional[dict]:
        # Bouchaud skew
        bouchaud_skew = state.bouchaud.get_quote_skew(timestamp, spread)
        # Drift anticipation (5-tick horizon)
        drift_skew = drift * 5

        total_skew = bouchaud_skew + drift_skew

        # Sizing
        base_notional = self.capital * self.p.get("base_notional_pct", 0.04)
        size_mult = hurst_mult * har_mult
        notional_usd = base_notional * size_mult * self.p.get("max_leverage", 5)

        if notional_usd < 10:
            return None

        size_units = notional_usd / max(fv, 1e-9)

        buy_price  = fv - spread / 2 + total_skew
        sell_price = fv + spread / 2 + total_skew

        # POST_ONLY guard
        if buy_price >= ask:
            buy_price = ask - ask * 5e-5
        if sell_price <= bid:
            sell_price = bid + bid * 5e-5
        if buy_price >= sell_price:
            return None

        state.last_quote_ts = timestamp

        return {
            "action":       ACTION_PLACE_QUOTES,
            "symbol":       state.symbol,
            "buy_price":    round(buy_price, 8),
            "sell_price":   round(sell_price, 8),
            "size":         size_units,
            "notional_usd": notional_usd,
            "meta": {
                "hurst":           state.hurst.h,
                "hurst_regime":    state.hurst.get_regime(),
                "har_ratio":       state.har_rv.get_vol_ratio(),
                "bouchaud_skew":   bouchaud_skew,
                "drift_skew":      drift_skew,
                "kalman_fv":       fv,
                "spread_bps":      spread / mid * 10_000,
                "size_mult":       size_mult,
            },
        }

    def _cancel_action(self, symbol: str) -> Optional[dict]:
        ids = self._pending_ids.get(symbol, [])
        if not ids:
            return None
        return {
            "action":   ACTION_CANCEL_QUOTES,
            "symbol":   symbol,
            "order_ids": list(ids),
        }

    def register_pending(self, symbol: str, order_ids: list[str]) -> None:
        self._pending_ids[symbol] = order_ids

    def clear_pending(self, symbol: str) -> None:
        self._pending_ids[symbol] = []
