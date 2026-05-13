"""
diagnose_live.py — Vérifie la connexion à Hyperliquid et le flux de données
vers les stratégies en temps réel. Pas de trades, pas de paper positions.

Usage:
    python scripts/diagnose_live.py [--minutes 3] [--coins BTC,ETH,SOL]

Produit un rapport:
  • WS connecté ou non
  • Nombre de book-updates/s par coin
  • Bars accumulés par stratégie après N minutes
  • Calibration data de chaque stratégie (signal_active, conditions, warmup)
  • Pourquoi BreakoutControlled a toujours le même PnL (explication mathématique)
"""
import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.orderbook_manager import OrderbookManager
from strategies.base_strategy import BarData, StrategyConfig
from strategies.breakout_controlled import BreakoutControlled
from strategies.donchian_trend import DonchianTrendStrategy
from strategies.momentum_long_short import MomentumLongShort
from strategies.rsi_bollinger_reversion import RSIBollingerReversionStrategy
from strategies.volatility_regime_breakout import VolatilityRegimeBreakoutStrategy


# ── Config par défaut identique à paper_500_clean.json ────────────────────────

STRAT_CONFIGS = [
    {
        "cls": BreakoutControlled,
        "name": "BreakoutControlled",
        "coins": ["BTC", "ETH", "SOL", "AVAX"],
        "params": {
            "lookback_bars": 12, "bo_max_pct": 8.0, "vr_min": 1.0,
            "spread_bps_max": 25.0, "take_profit_pct": 2.5,
            "stop_below_resistance_pct": 1.5, "max_hold_hours": 24,
        },
    },
    {
        "cls": DonchianTrendStrategy,
        "name": "DonchianTrend",
        "coins": ["BTC", "ETH", "SOL"],
        "params": {
            "donchian_n": 36, "ema_1h_period": 50, "btc_regime_ema": 200,
            "vol_period": 20, "vol_multiplier": 1.2,
            "stop_loss_pct": 0.01, "take_profit_pct": 0.025,
            "min_cost_ratio": 2.5, "max_hold_hours": 48, "cooldown_s": 90,
        },
    },
    {
        "cls": MomentumLongShort,
        "name": "MomentumLS",
        "coins": ["BTC", "ETH", "SOL", "AVAX", "LINK", "ARB"],
        "params": {
            "rerank_seconds": 60, "top_k_long": 2, "bottom_k_short": 2,
            "long_percentile_min": 0.75, "short_percentile_max": 0.25,
            "spread_bps_max": 25.0, "stop_loss_pct": 0.01,
            "take_profit_pct": 0.02, "max_hold_hours": 48,
        },
    },
    {
        "cls": RSIBollingerReversionStrategy,
        "name": "RSIBollingerReversion",
        "coins": ["BTC", "ETH", "SOL"],
        "params": {
            "rsi_period": 14, "rsi_oversold": 25, "zscore_period": 30,
            "zscore_entry": -2.2, "bb_period": 20, "bb_k": 2.0,
            "ema_1h_period": 100, "stop_loss_pct": 0.01,
            "take_profit_pct": 0.02, "min_cost_ratio": 2.5, "cooldown_s": 90,
        },
    },
    {
        "cls": VolatilityRegimeBreakoutStrategy,
        "name": "VolatilityRegimeBreakout",
        "coins": ["BTC", "ETH", "SOL"],
        "params": {
            "donchian_period": 20, "atr_period": 14,
            "high_vol_threshold_bps": 35.0, "low_vol_threshold_bps": 10.0,
            "stop_loss_pct": 0.012, "take_profit_pct": 0.025, "max_hold_hours": 6,
        },
    },
]


def _make_strat(cfg_dict):
    all_coins = cfg_dict["coins"]
    sc = StrategyConfig(
        name=cfg_dict["name"], enabled=True,
        capital_allocated_usd=500, max_positions=2,
        max_position_size_usd=250, coins=all_coins,
        params=cfg_dict["params"],
    )
    return cfg_dict["cls"](sc)


def _print_divider(title=""):
    w = 70
    if title:
        pad = (w - len(title) - 2) // 2
        print("─" * pad + f" {title} " + "─" * pad)
    else:
        print("─" * w)


# ── Main async runner ──────────────────────────────────────────────────────────

async def run_diagnosis(symbols: list[str], duration_s: int):
    print()
    _print_divider("DIAGNOSTIC CONNEXION LIVE — Artemisia v9")
    print(f"  Symbols: {symbols}")
    print(f"  Durée: {duration_s}s ({duration_s//60}m {duration_s%60}s)")
    print(f"  WS: wss://api.hyperliquid.xyz/ws")
    print()

    # ── Build OBM & strategies ─────────────────────────────────────────────
    obm = OrderbookManager(symbols, subscription_delay_s=0.15)
    strats = [_make_strat(c) for c in STRAT_CONFIGS]
    # Filter strat coins to subscribed symbols
    sym_set = set(symbols)
    for s in strats:
        s.config.coins = [c for c in s.config.coins if c in sym_set]

    # Tracking
    book_count:  dict[str, int]   = defaultdict(int)
    trade_count: dict[str, int]   = defaultdict(int)
    bar_count:   dict[str, int]   = defaultdict(int)
    start_ts = time.time()
    connect_ok = False

    # Bar accumulator (same logic as engine)
    bar_acc: dict[str, dict] = {
        s: {"open": None, "high": None, "low": None,
            "close": None, "vol": 0.0} for s in symbols
    }
    last_bar_ts: dict[str, float] = {s: time.time() for s in symbols}

    # ── Connect ────────────────────────────────────────────────────────────
    print("  Connexion WebSocket...", end="", flush=True)
    try:
        await asyncio.wait_for(obm.connect(), timeout=15.0)
        connect_ok = True
        print(" OK")
    except Exception as e:
        print(f" ÉCHEC: {e}")
        _print_report(book_count, trade_count, bar_count, strats, symbols,
                      connect_ok, duration_s)
        return

    # ── Background book loop ────────────────────────────────────────────────
    async def _book_loop():
        async for upd in obm.stream_orderbook_updates():
            sym  = upd.symbol
            book = upd.book
            ts   = upd.timestamp
            if sym not in sym_set:
                continue

            book_count[sym] += 1

            mid = book.mid
            if mid:
                acc = bar_acc[sym]
                if acc["open"] is None:
                    acc["open"] = mid
                acc["close"] = mid
                if acc["high"] is None or mid > acc["high"]: acc["high"] = mid
                if acc["low"]  is None or mid < acc["low"]:  acc["low"]  = mid

            # Emit minute bar?
            now = time.time()
            if now - last_bar_ts[sym] >= 60.0:
                acc = bar_acc[sym]
                if acc["close"] is not None:
                    prev_close = acc.get("prev_close") or acc["close"]
                    r1m = (acc["close"] / prev_close - 1.0) if prev_close else 0.0
                    bar = BarData(
                        symbol=sym,
                        ts=now,
                        open=acc["open"] or acc["close"],
                        high=acc["high"] or acc["close"],
                        low=acc["low"]  or acc["close"],
                        close=acc["close"],
                        volume_usd=acc["vol"],
                        return_1m=r1m,
                    )
                    for s in strats:
                        if sym in s.config.coins:
                            s.on_bar_minute(sym, bar, now)
                    bar_count[sym] += 1
                    bar_acc[sym] = {"open": None, "high": None, "low": None,
                                    "close": None, "vol": 0.0,
                                    "prev_close": acc["close"]}
                    last_bar_ts[sym] = now

                    # Also feed orderbook to strategies
                for s in strats:
                    if sym in s.config.coins:
                        s.on_orderbook_update(sym, book, ts)

            if time.time() - start_ts >= duration_s:
                return

    async def _trade_loop():
        async for ev in obm.stream_trades():
            bar_acc[ev.symbol]["vol"] = (
                bar_acc[ev.symbol].get("vol", 0.0) + ev.volume_usd)
            trade_count[ev.symbol] += 1
            if time.time() - start_ts >= duration_s:
                return

    # ── Progress ticker ─────────────────────────────────────────────────────
    async def _ticker():
        t0 = time.time()
        while time.time() - t0 < duration_s:
            elapsed = int(time.time() - t0)
            remaining = duration_s - elapsed
            total_books = sum(book_count.values())
            print(f"\r  [{elapsed:>3}s] book-updates reçus: {total_books:>6} | "
                  f"bars: {sum(bar_count.values())} | "
                  f"restant: {remaining}s   ", end="", flush=True)
            await asyncio.sleep(2.0)
        print()

    # Run all loops together, stop after duration_s
    try:
        await asyncio.wait_for(
            asyncio.gather(_book_loop(), _trade_loop(), _ticker()),
            timeout=duration_s + 5,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    finally:
        await obm.stop()

    _print_report(book_count, trade_count, bar_count, strats, symbols,
                  connect_ok, duration_s)


def _print_report(book_count, trade_count, bar_count, strats, symbols,
                  connect_ok, duration_s):
    elapsed = duration_s
    print()
    _print_divider("RAPPORT")

    # ── 1. Connexion ──────────────────────────────────────────────────────
    print("\n1. CONNEXION EXCHANGE")
    status = "✓ OK" if connect_ok else "✗ ÉCHEC — vérifier réseau/VPN"
    print(f"   Hyperliquid WS: {status}")

    # ── 2. Débit de données ───────────────────────────────────────────────
    print("\n2. DÉBIT DONNÉES (book-updates)")
    for sym in symbols:
        n = book_count.get(sym, 0)
        rate = n / max(elapsed, 1)
        trade_n = trade_count.get(sym, 0)
        bars_n  = bar_count.get(sym, 0)
        ok = "✓" if n > 10 else "✗ PAS DE DONNÉES"
        print(f"   {sym:<6} {ok}  updates={n:>5}  ({rate:.1f}/s)  "
              f"trades={trade_n:>4}  bars_accumulés={bars_n}")

    # ── 3. Calibration par stratégie ──────────────────────────────────────
    print("\n3. CALIBRATION STRATÉGIES (snapshot fin de session)")
    warmup_needed = {
        "BreakoutControlled":        12,
        "DonchianTrend":             36,
        "MomentumLS":                 3,
        "RSIBollingerReversion":    100,
        "VolatilityRegimeBreakout":  20,
    }
    for s in strats:
        name = s.config.name
        need = warmup_needed.get(name, 20)
        elapsed_bars = max(bar_count.values()) if bar_count else 0
        warmup_ok = elapsed_bars >= need
        wup_status = f"✓ warmup OK ({elapsed_bars}/{need} bars)" if warmup_ok \
                     else f"⏳ warmup en cours ({elapsed_bars}/{need} bars — besoin de {need - elapsed_bars} min de plus)"

        print(f"\n   ── {name} ──")
        print(f"      {wup_status}")

        for coin in s.config.coins[:3]:
            try:
                cal = s.get_calibration_data(coin)
                # Pretty-print key fields
                kv_pairs = []
                for k, v in cal.items():
                    if v is None:
                        continue
                    if isinstance(v, float):
                        kv_pairs.append(f"{k}={v:.4f}")
                    elif isinstance(v, bool):
                        kv_pairs.append(f"{k}={'Y' if v else 'N'}")
                    elif isinstance(v, (int, str)):
                        kv_pairs.append(f"{k}={v}")
                print(f"      {coin}: " + "  ".join(kv_pairs[:8]))
            except Exception as exc:
                print(f"      {coin}: calibration error — {exc}")

    # ── 4. Explication PnL fixe BreakoutControlled ────────────────────────
    print()
    _print_divider("EXPLICATION PnL FIXE BREAKOUTCONTROLLED")
    print("""
  Le PnL de $6.25 par trade est NORMAL et ATTENDU en paper mode.

  Mathématique:
    notional  = min(max_position_size_usd, capital) = min(250, 500) = $250
    take_profit_pct = 2.5%
    gross PnL = notional × tp_pct = 250 × 0.025 = $6.25

  Pourquoi toujours exactement $6.25?
    → L'exécuteur paper remplit toujours au prix TP exact (pas de slippage sur TP)
    → Le notional est toujours 250 (fixe dans la config)
    → Donc 250 × 2.5% = $6.25 déterministe

  Ce n'est PAS un bug. C'est la simulation paper qui fonctionne correctement.
  En live, le slippage et la profondeur du carnet varieraient légèrement.
""")

    # ── 5. Pourquoi les autres stratégies ne tradent pas ──────────────────
    _print_divider("POURQUOI LES AUTRES STRATÉGIES NE TRADENT PAS")
    print("""
  Causes probables (par ordre de priorité):

  A) WARMUP INSUFFISANT
     DonchianTrend     → besoin de 36 bars (36 min de données)
     RSIBollingerRev   → besoin de 100 bars (100 min) pour l'EMA-1h
     VolRegimeBreakout → besoin de 20 bars  (20 min) pour l'ATR
     MomentumLS        → besoin de 3 bars   (3 min, relativement rapide)

     Laisser tourner l'engine au moins 2h avant d'espérer des trades
     de DonchianTrend ou RSIBollingerReversion.

  B) CONDITIONS DE MARCHÉ RARES
     RSIBollingerRev: RSI < 25 ET z-score < -2.2 → conditions extrêmes
     DonchianTrend:   nouveau high/low sur 36 barres → peut attendre des heures
     MomentumLS:      percentile ≥ 75% requis → filtre strict

  C) EXECUTION FILTER COOLDOWN (5 min après gain, 15 min après perte)
     Une fois BreakoutControlled a tradé BTC, BreakoutControlled-BTC
     est en cooldown 300s. Mais les AUTRES stratégies ne sont PAS bloquées.

  D) KILL SWITCH / RAMPAGE
     Si le KillSwitch a été déclenché dans une session précédente,
     relancer le GUI efface l'état (engine redémarre fresh).

  RECOMMANDATION: laisser tourner l'engine 90-120 minutes en paper mode
  et observer le dashboard terminal (affiché toutes les 60s) pour voir
  les stratégies générer des signaux.
""")

    _print_divider()
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnostic connexion live Artemisia v9")
    parser.add_argument("--minutes", type=int, default=3,
                        help="Durée de la session de diagnostic (défaut: 3 minutes)")
    parser.add_argument("--coins", type=str, default="BTC,ETH,SOL,AVAX",
                        help="Coins à surveiller (défaut: BTC,ETH,SOL,AVAX)")
    args = parser.parse_args()

    syms = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    secs = args.minutes * 60

    print(f"\n  Lancement diagnostic pour {args.minutes} minute(s)...")
    print(f"  (Raccourcir avec Ctrl+C si nécessaire)\n")

    try:
        asyncio.run(run_diagnosis(syms, secs))
    except KeyboardInterrupt:
        print("\n\n  Interrompu par Ctrl+C — rapport partiel ci-dessus.")
