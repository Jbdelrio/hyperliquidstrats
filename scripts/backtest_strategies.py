"""
backtest_strategies.py — Historical backtest of the 5 directional bar
strategies on 180 days of Binance OHLCV (used as a proxy for Hyperliquid
prices; basis stays small on majors).

Each strategy is instantiated with the params from
`config/presets/paper_500_clean.json` and replayed through
`backtesting.backtest_engine.BacktestEngine`. The engine simulates fills
with a fixed fee + slippage cost model identical to the live paper engine
(taker 3 bps + 4 bps slippage one-way → 14 bps round trip).

Output: `reports/backtest_strategies.md` with per-strategy + walk-forward
metrics.

Usage from the repo root:
    python scripts/backtest_strategies.py
    python scripts/backtest_strategies.py --days 90 --symbols BTC,ETH
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

import numpy as np                       # noqa: E402
import pandas as pd                       # noqa: E402

from strategies.base_strategy import BarData, StrategyConfig    # noqa: E402
from strategies.momentum_long_short import MomentumLongShort    # noqa: E402
from strategies.breakout_controlled import BreakoutControlled   # noqa: E402
from strategies.donchian_trend import DonchianTrendStrategy     # noqa: E402
from strategies.volatility_regime_breakout import VolatilityRegimeBreakoutStrategy  # noqa: E402
from strategies.rsi_bollinger_reversion import RSIBollingerReversionStrategy        # noqa: E402
from backtesting.backtest_engine import BacktestEngine          # noqa: E402
from backtesting.metrics import compute_metrics                  # noqa: E402


DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "AVAX"]
DEFAULT_DAYS = 180
DEFAULT_TIMEFRAME = "1h"
CACHE = _REPO / "backtest" / "data"
PRESET_PATH = _REPO / "config" / "presets" / "paper_500_clean.json"

FEE_BPS = 3.0
SLIPPAGE_BPS = 4.0


# ─── data ───────────────────────────────────────────────────────────────────

def _fetch_ohlcv(coin: str, days: int, tf: str = "1h") -> pd.DataFrame:
    """Download from Binance via ccxt with mtime cache in backtest/data/."""
    import ccxt   # local import — only needed when we download
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{coin}_USDT_{tf}_{days}d.csv"
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < 6:   # use cache if < 6 h old
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            print(f"  cache {coin} {tf}: {len(df)} bars (age {age_h:.1f}h)")
            return df

    exchange = ccxt.binance({"enableRateLimit": True})
    symbol = f"{coin}/USDT"
    since = int((datetime.now(timezone.utc)
                  - timedelta(days=days)).timestamp() * 1000)
    tf_ms = {"1h": 3_600_000, "30m": 1_800_000, "15m": 900_000,
             "5m": 300_000, "1m": 60_000}[tf]
    all_bars = []
    cursor = since
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cursor < end:
        try:
            bars = exchange.fetch_ohlcv(symbol, tf, since=cursor, limit=1000)
        except Exception as exc:
            print(f"  /!\\ fetch failed for {symbol}: {exc}")
            break
        if not bars:
            break
        all_bars.extend(bars)
        cursor = bars[-1][0] + tf_ms
        time.sleep(exchange.rateLimit / 1000)
    if not all_bars:
        return pd.DataFrame()
    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high",
                                          "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(cache_file)
    print(f"  downloaded {coin} {tf}: {len(df)} bars → {cache_file.name}")
    return df


def _to_bars(df: pd.DataFrame, symbol: str) -> list[BarData]:
    bars: list[BarData] = []
    closes = df["close"].to_numpy(dtype=float)
    for i, (ts, row) in enumerate(df.iterrows()):
        if i == 0 or closes[i - 1] <= 0:
            r = 0.0
        else:
            r = (closes[i] - closes[i - 1]) / closes[i - 1]
        bars.append(BarData(
            symbol=symbol, ts=float(ts.timestamp()),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume_usd=float(row["volume"]) * float(row["close"]),
            return_1m=r,
        ))
    return bars


# ─── strategy specs ─────────────────────────────────────────────────────────

def _load_strategy_params(preset_path: Path) -> dict:
    p = json.load(open(preset_path, encoding="utf-8"))
    return {s["name"]: s for s in p.get("strategies", []) if s.get("enabled")}


_CLASSES = {
    "MomentumLS":              MomentumLongShort,
    "BreakoutControlled":      BreakoutControlled,
    "DonchianTrend":           DonchianTrendStrategy,
    "VolatilityRegimeBreakout": VolatilityRegimeBreakoutStrategy,
    "RSIBollingerReversion":   RSIBollingerReversionStrategy,
}


# ─── backtest runner ────────────────────────────────────────────────────────

def _run_one(name: str, klass, cfg_block: dict,
              bars: list[BarData], coins: list[str]) -> dict:
    """Run a single strategy through BacktestEngine. Returns (trades, metrics)."""
    cfg = StrategyConfig(
        name=name, enabled=True,
        capital_allocated_usd=float(cfg_block.get("capital_allocated_usd", 500)),
        max_positions=int(cfg_block.get("max_positions", 2)),
        max_position_size_usd=float(cfg_block.get("max_position_size_usd", 250)),
        coins=coins, params=cfg_block.get("params", {}),
        kill_after_consecutive_losses=99,    # disabled in backtest
        suspend_minutes_on_kill=0,
    )
    engine = BacktestEngine(klass, cfg, bars,
                            fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS)
    trades = engine.run()
    m = compute_metrics(trades)
    return {"name": name, "trades": trades, "metrics": m}


def _equity_metrics(trades: list[dict], init_cap: float) -> dict:
    """Extras on top of compute_metrics: Sharpe-ish, equity series stats."""
    if not trades:
        return {"sharpe": 0.0, "expectancy": 0.0, "avg_hold_h": 0.0,
                "trades_per_day": 0.0, "first_ts": None, "last_ts": None}
    nets = np.array([float(t.get("net", 0) or 0) for t in trades])
    holds = np.array([float(t.get("hold_s", 0) or 0) for t in trades])
    tss   = sorted(float(t.get("ts", 0) or 0) for t in trades)
    span_d = max(1e-6, (tss[-1] - tss[0]) / 86_400.0)
    sharpe = (nets.mean() / nets.std() * np.sqrt(len(nets) / max(span_d, 1)))\
             if len(nets) > 1 and nets.std() > 0 else 0.0
    expectancy = float(nets.mean())
    return {
        "sharpe": float(sharpe),
        "expectancy": expectancy,
        "avg_hold_h": float(holds.mean() / 3600.0) if len(holds) else 0.0,
        "trades_per_day": float(len(nets) / span_d),
        "first_ts": tss[0], "last_ts": tss[-1],
    }


def _walk_forward(trades: list[dict], split: float = 0.6) -> dict:
    """Return {train_pnl, test_pnl, train_n, test_n, sign_consistent}."""
    if not trades:
        return {"train_pnl": 0.0, "test_pnl": 0.0, "train_n": 0, "test_n": 0,
                "sign_consistent": False}
    tss = sorted(t.get("ts", 0) for t in trades)
    cutoff = tss[int(len(tss) * split)]
    train_pnl = sum(t["net"] for t in trades if t["ts"] <= cutoff)
    test_pnl  = sum(t["net"] for t in trades if t["ts"] >  cutoff)
    train_n = sum(1 for t in trades if t["ts"] <= cutoff)
    test_n  = sum(1 for t in trades if t["ts"] >  cutoff)
    sign_consistent = (train_pnl > 0 and test_pnl > 0) \
                       or (train_pnl < 0 and test_pnl < 0)
    return {"train_pnl": float(train_pnl), "test_pnl": float(test_pnl),
            "train_n": train_n, "test_n": test_n,
            "sign_consistent": sign_consistent}


# ─── report ─────────────────────────────────────────────────────────────────

def _verdict(metrics: dict, extra: dict, wf: dict, n_trades: int) -> str:
    pnl = metrics.get("total_pnl", 0.0)
    wr  = metrics.get("win_rate", 0.0)
    pf  = metrics.get("profit_factor", 0.0)
    if n_trades < 20:
        return "❓ too_few_trades"
    if pnl <= 0:
        return "❌ negative"
    if wf.get("test_pnl", 0) <= 0:
        return "⚠️ in_sample_only"
    if pf < 1.1:
        return "⚠️ marginal"
    if extra.get("sharpe", 0) < 0.8:
        return "⚠️ low_sharpe"
    if wf.get("sign_consistent") and pf >= 1.3 and extra.get("sharpe", 0) >= 1.0:
        return "✅ CANDIDATE"
    return "⚠️ partial"


def build_report(results: list[dict], days: int, symbols: list[str],
                 init_cap: float, timeframe: str) -> str:
    out: list[str] = []
    out.append("# Backtest historique — 5 stratégies barres directionnelles\n")
    out.append(f"*Généré {datetime.now().isoformat(timespec='seconds')}*\n")
    out.append(f"- **Période** : {days} jours, {timeframe} candles")
    out.append(f"- **Symboles** : {', '.join(symbols)}")
    out.append(f"- **Capital initial** : ${init_cap:.0f} par stratégie")
    out.append(f"- **Coûts** : fee {FEE_BPS} bps + slippage {SLIPPAGE_BPS} bps "
               f"par côté → {2 * (FEE_BPS + SLIPPAGE_BPS):.0f} bps round-trip")
    out.append(f"- **Source de prix** : Binance (proxy Hyperliquid sur "
               "majors — basis < 5 bps en moyenne)\n")

    out.append("## Récap synthétique\n")
    out.append("| Stratégie | Trades | Net PnL | WR | Profit factor | "
                "Sharpe | Max DD | Sign-consistent (train/test) | Verdict |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        m = r["metrics"]; e = r["extra"]; wf = r["wf"]
        sign = "✓" if wf["sign_consistent"] else "✗"
        out.append(
            f"| {r['name']} | {m['n_trades']} | ${m['total_pnl']:+.2f} | "
            f"{m['win_rate']:.1f}% | {m['profit_factor']:.2f} | "
            f"{e['sharpe']:.2f} | ${m['max_drawdown']:.2f} | "
            f"{sign}  (${wf['train_pnl']:+.1f} / ${wf['test_pnl']:+.1f}) | "
            f"{r['verdict']} |"
        )

    out.append("\n## Détails par stratégie\n")
    for r in results:
        m = r["metrics"]; e = r["extra"]; wf = r["wf"]
        n = m["n_trades"]
        n_wins = int(round(n * m.get("win_rate", 0) / 100.0))
        out.append(f"\n### {r['name']}\n")
        out.append(f"- Trades : **{n}** "
                    f"(W {n_wins} / L {n - n_wins})")
        out.append(f"- Net PnL : **${m['total_pnl']:+.2f}**")
        out.append(f"- Win rate : {m['win_rate']:.1f}%")
        out.append(f"- Profit factor : {m['profit_factor']:.2f}")
        out.append(f"- Sharpe-like : {e['sharpe']:.2f}")
        out.append(f"- Max drawdown : ${m['max_drawdown']:.2f}")
        out.append(f"- Avg hold : {e['avg_hold_h']:.1f} h, "
                    f"{e['trades_per_day']:.2f} trades / jour")
        out.append(f"- Walk-forward (60/40) : train ${wf['train_pnl']:+.2f} "
                    f"({wf['train_n']} trades) / test ${wf['test_pnl']:+.2f} "
                    f"({wf['test_n']} trades) — "
                    f"{'**signe cohérent ✓**' if wf['sign_consistent'] else 'signe incohérent ✗'}")
        out.append(f"- Verdict : **{r['verdict']}**")
        # Exit reason distribution
        ex = m.get("exit_reason_dist", {}) or {}
        if ex:
            out.append(f"- Sorties : "
                        + ", ".join(f"{k}={v}" for k, v in
                                     sorted(ex.items(), key=lambda x: -x[1])))
        # PnL by symbol
        by_sym = m.get("pnl_by_symbol", {}) or {}
        if by_sym:
            out.append("- PnL par symbole : "
                        + ", ".join(f"{k}=${v:+.2f}" for k, v in
                                     sorted(by_sym.items(),
                                            key=lambda x: -x[1])))

    out.append("\n## Interprétation\n")
    out.append(
        "Une stratégie 'se confirme' en backtest si **tous** ces critères "
        "tiennent : net PnL > 0, profit factor > 1.2, Sharpe > 1.0, train "
        "et test du même signe, ≥ 20 trades. Une seule métrique en rouge "
        "= prudence. Deux = pas de confirmation.\n\n"
        "**Attention** : un backtest positif sur 180 j de Binance 1 h ≠ "
        "garantie de PnL paper/live sur Hyperliquid. Les majors traquent à "
        "quelques bps, mais l'exécution réelle (latence, profondeur de "
        "carnet, partial fills) peut dégrader le PnL. Comparer avec ce que "
        "le moteur paper produit sur la même période est l'étape suivante."
    )
    return "\n".join(out)


# ─── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--timeframe", default=DEFAULT_TIMEFRAME,
                    help="OHLCV bar timeframe: 1h, 30m, 15m, 5m, 1m.")
    ap.add_argument("--out", default=None,
                    help="Output report path. Defaults to "
                         "reports/backtest_strategies_<tf>.md")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    days    = int(args.days)
    tf      = args.timeframe
    out_path = args.out or f"reports/backtest_strategies_{tf}.md"

    print(f"=== Backtest sur {days}j, {tf}, "
          f"symboles: {','.join(symbols)} ===")
    print("Téléchargement / cache des OHLCV…")
    raw: dict[str, pd.DataFrame] = {}
    for c in symbols:
        df = _fetch_ohlcv(c, days, tf)
        if df is None or df.empty:
            print(f"  /!\\ {c} : pas de données — skip")
            continue
        raw[c] = df
    if not raw:
        print("ERREUR : aucune donnée téléchargée.")
        return 1

    # Interleave bars across all symbols by ts.
    all_bars: list[BarData] = []
    for sym, df in raw.items():
        all_bars.extend(_to_bars(df, sym))
    all_bars.sort(key=lambda b: b.ts)
    print(f"\nTotal bars (interleaved, sorted) : {len(all_bars)}")

    # Load params from the live preset.
    preset_strats = _load_strategy_params(PRESET_PATH)
    print(f"Strategies dans le preset : "
          f"{list(preset_strats.keys())[:6]}")

    results: list[dict] = []
    coins_in_data = list(raw.keys())
    for name, klass in _CLASSES.items():
        cfg_block = preset_strats.get(name)
        if cfg_block is None:
            print(f"  - {name} : absent du preset, skip")
            continue
        print(f"\n>>> Backtest {name} …")
        r = _run_one(name, klass, cfg_block, all_bars, coins_in_data)
        r["extra"] = _equity_metrics(r["trades"], 500.0)
        r["wf"]    = _walk_forward(r["trades"])
        r["verdict"] = _verdict(r["metrics"], r["extra"], r["wf"],
                                 r["metrics"]["n_trades"])
        m = r["metrics"]; e = r["extra"]
        print(f"   trades={m['n_trades']}  pnl=${m['total_pnl']:+.2f}  "
              f"wr={m['win_rate']:.1f}%  pf={m['profit_factor']:.2f}  "
              f"sharpe={e['sharpe']:.2f}  verdict={r['verdict']}")
        results.append(r)

    report = build_report(results, days, list(raw.keys()), 500.0, tf)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n[backtest] rapport écrit → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
