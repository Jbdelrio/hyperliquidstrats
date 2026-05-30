"""
funding_carry_diagnostic.py — Empirical diagnostic of the Hyperliquid funding
universe for the delta-neutral carry strategy.

What it answers:
  1. Across the FULL HL perp universe (~230 coins), how is funding distributed
     right now? Where would FundingCarryHedged actually find an edge?
  2. For the top candidates, what does the 30-day funding history look like?
     Median, 75/90/95th percentile, regime stability.
  3. At various round-trip cost levels (5 / 10 / 15 / 31 bps), how many
     opportunities would have been tradeable per coin?
  4. What is the empirical break-even hold time at each cost level?

Output: reports/funding_carry_diagnostic.md.

Usage:
    python scripts/funding_carry_diagnostic.py
    python scripts/funding_carry_diagnostic.py --top 40 --days 30 --min-vol-m 1.0
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

# Allow running both as `python scripts/funding_carry_diagnostic.py` and as -m.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = "https://api.hyperliquid.xyz/info"
API_TIMEOUT = 10.0
COST_LEVELS_RT_BPS = [5.0, 10.0, 15.0, 31.0]
HOLD_HOURS_PROJ = [4, 8, 24, 48]


@dataclass
class CoinSnap:
    coin: str
    funding_bps_h: float
    day_vol_usd: float
    open_interest_usd: float
    oracle_px: float
    mark_px: float
    basis_bps: Optional[float] = None


@dataclass
class CoinHistory:
    coin: str
    samples_bps_h: list = field(default_factory=list)
    timestamps: list = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.samples_bps_h)

    def abs_quantiles(self) -> dict:
        if not self.samples_bps_h:
            return {}
        a = sorted(abs(x) for x in self.samples_bps_h)
        def q(p: float) -> float:
            i = max(0, min(len(a) - 1, int(round(p * (len(a) - 1)))))
            return a[i]
        return {
            "median": q(0.5), "p75": q(0.75), "p90": q(0.9),
            "p95": q(0.95), "p99": q(0.99), "max": a[-1],
        }


def _post(payload: dict) -> dict | list:
    r = requests.post(API_URL, json=payload, timeout=API_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_universe() -> list[CoinSnap]:
    data = _post({"type": "metaAndAssetCtxs"})
    meta, ctxs = data[0], data[1]
    out: list[CoinSnap] = []
    for i, ctx in enumerate(ctxs):
        if i >= len(meta["universe"]):
            break
        coin = meta["universe"][i]["name"]
        try:
            funding = float(ctx.get("funding", 0.0) or 0.0)
            vol = float(ctx.get("dayNtlVlm", 0.0) or 0.0)
            oi  = float(ctx.get("openInterest", 0.0) or 0.0)
            oracle = float(ctx.get("oraclePx", 0.0) or 0.0)
            mark = float(ctx.get("markPx", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        basis = None
        if oracle and mark:
            basis = (mark - oracle) / oracle * 10_000.0
        out.append(CoinSnap(
            coin=coin, funding_bps_h=funding * 10_000.0,
            day_vol_usd=vol, open_interest_usd=oi,
            oracle_px=oracle, mark_px=mark, basis_bps=basis,
        ))
    return out


def fetch_funding_history(coin: str, days: int) -> CoinHistory:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows = _post({"type": "fundingHistory", "coin": coin,
                  "startTime": start_ms, "endTime": end_ms})
    hist = CoinHistory(coin=coin)
    if not isinstance(rows, list):
        return hist
    for r in rows:
        try:
            f = float(r["fundingRate"])
            t = int(r["time"])
        except (KeyError, TypeError, ValueError):
            continue
        hist.samples_bps_h.append(f * 10_000.0)
        hist.timestamps.append(t)
    return hist


def project_costs(hist: CoinHistory, cost_rt_bps: float,
                  hold_h: float, buffer_bps: float = 2.0) -> dict:
    """For each hourly snapshot, compute the expected edge given hold and cost.
    A snapshot is `tradeable` if expected_edge_bps > 0.
    Returns counts + medians."""
    if not hist.samples_bps_h:
        return {"n": 0, "tradeable": 0, "tradeable_pct": 0.0,
                "median_edge_bps_when_tradeable": 0.0,
                "annualized_yield_pct_when_tradeable": 0.0}
    edges = [abs(f) * hold_h - cost_rt_bps - buffer_bps for f in hist.samples_bps_h]
    tradeable = [e for e in edges if e > 0]
    n_t = len(tradeable)
    med_e = statistics.median(tradeable) if tradeable else 0.0
    # Annualised yield from median |funding| above floor.
    abs_when_tradeable = [abs(f) for f, e in zip(hist.samples_bps_h, edges) if e > 0]
    med_f = statistics.median(abs_when_tradeable) if abs_when_tradeable else 0.0
    annualised = med_f * 8760 / 100.0  # bps/h * 8760h/year / 100 = %/year
    return {
        "n": hist.n, "tradeable": n_t,
        "tradeable_pct": 100.0 * n_t / hist.n,
        "median_edge_bps_when_tradeable": med_e,
        "annualized_yield_pct_when_tradeable": annualised,
    }


def break_even_hours(median_abs_bps_h: float, cost_rt_bps: float,
                     buffer_bps: float = 2.0) -> Optional[float]:
    if median_abs_bps_h <= 0:
        return None
    return (cost_rt_bps + buffer_bps) / median_abs_bps_h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40,
                    help="Top-N coins by |funding| to fetch history for")
    ap.add_argument("--days", type=int, default=30,
                    help="Days of funding history per coin")
    ap.add_argument("--min-vol-m", type=float, default=0.5,
                    help="Min 24h notional volume (in $M) to include in 'tradeable' bucket")
    ap.add_argument("--out", default="reports/funding_carry_diagnostic.md")
    args = ap.parse_args()

    print(f"[1/3] Fetching HL universe…")
    snaps = fetch_universe()
    snaps.sort(key=lambda s: -abs(s.funding_bps_h))
    print(f"      {len(snaps)} perps, top |funding| = {abs(snaps[0].funding_bps_h):.3f} bps/h ({snaps[0].coin})")

    # Pick coins to deep-dive: top-N by |funding|, plus the configured ones
    # (so we always show BTC/ETH/SOL/HYPE for comparison).
    must_include = {"BTC", "ETH", "SOL", "HYPE"}
    top_set = {s.coin for s in snaps[:args.top]} | must_include
    deep = [s for s in snaps if s.coin in top_set]
    deep.sort(key=lambda s: -abs(s.funding_bps_h))

    print(f"[2/3] Fetching {args.days}d funding history for {len(deep)} coins…")
    histories: dict[str, CoinHistory] = {}
    for i, s in enumerate(deep, 1):
        try:
            histories[s.coin] = fetch_funding_history(s.coin, args.days)
            print(f"      [{i:3d}/{len(deep)}] {s.coin:<10} n={histories[s.coin].n}")
        except Exception as exc:
            print(f"      [{i:3d}/{len(deep)}] {s.coin:<10} FAILED: {exc}")
        time.sleep(0.05)  # be polite to the API

    print(f"[3/3] Writing report…")
    write_report(snaps, deep, histories, args)
    print(f"      -> {args.out}")


def write_report(snaps, deep, histories, args) -> None:
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    lines: list[str] = []
    w = lines.append

    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    w(f"# FundingCarryHedged — diagnostic empirique\n")
    w(f"*Généré {ts}, univers Hyperliquid, {args.days}j d'historique, "
      f"top {args.top} coins par |funding| actuel.*\n")

    # ── §1 — snapshot global
    w("## 1. Snapshot funding actuel — univers complet HL\n")
    above_05 = sum(1 for s in snaps if abs(s.funding_bps_h) > 0.5)
    above_1  = sum(1 for s in snaps if abs(s.funding_bps_h) > 1.0)
    above_2  = sum(1 for s in snaps if abs(s.funding_bps_h) > 2.0)
    above_5  = sum(1 for s in snaps if abs(s.funding_bps_h) > 5.0)
    w(f"- Total perps scannés : **{len(snaps)}**")
    w(f"- Coins avec |funding| > 0.5 bps/h : **{above_05}** "
      f"({above_05/len(snaps)*100:.1f}%)")
    w(f"- Coins avec |funding| > 1.0 bps/h : **{above_1}** "
      f"({above_1/len(snaps)*100:.1f}%)")
    w(f"- Coins avec |funding| > 2.0 bps/h : **{above_2}** "
      f"({above_2/len(snaps)*100:.1f}%)")
    w(f"- Coins avec |funding| > 5.0 bps/h : **{above_5}** "
      f"({above_5/len(snaps)*100:.1f}%)\n")

    # Top 25 table by |funding|.
    w("### Top 25 par |funding| courant\n")
    w("| Coin | funding bps/h | annualisé % | vol 24h $M | OI $M | basis bps |")
    w("|---|---:|---:|---:|---:|---:|")
    for s in snaps[:25]:
        annu = s.funding_bps_h * 8760 / 100.0
        basis = f"{s.basis_bps:+.1f}" if s.basis_bps is not None else "—"
        w(f"| {s.coin} | {s.funding_bps_h:+.3f} | {annu:+.1f} | "
          f"{s.day_vol_usd/1e6:.2f} | {s.open_interest_usd/1e6:.2f} | {basis} |")
    w("")

    # ── §2 — historique 30j sur top candidats
    w(f"## 2. Historique funding {args.days}j — top candidats\n")
    w("Pour chaque coin, on calcule la distribution de |funding bps/h| sur "
      f"les {args.days} derniers jours (1 sample / heure).\n")
    w("| Coin | n | médiane | p75 | p90 | p95 | p99 | max |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    # Sort by p75 to surface coins with sustained high funding (not just spikes).
    coins_sorted = sorted(
        histories.items(),
        key=lambda kv: -(kv[1].abs_quantiles().get("p75", 0.0)),
    )
    for coin, hist in coins_sorted[:25]:
        if hist.n == 0:
            continue
        q = hist.abs_quantiles()
        w(f"| {coin} | {hist.n} | {q['median']:.3f} | {q['p75']:.3f} | "
          f"{q['p90']:.3f} | {q['p95']:.3f} | {q['p99']:.3f} | {q['max']:.3f} |")
    w("")

    # ── §3 — projection par niveau de coûts
    w("## 3. Projection : opportunités tradeables par niveau de coûts\n")
    w("Pour chaque coin et chaque niveau de coût RT, on compte le pourcentage "
      "de samples horaires où `|funding_bps/h| × hold_h - cost_rt - 2 > 0` "
      "(avec buffer sécurité 2 bps).\n")
    for hold_h in HOLD_HOURS_PROJ:
        w(f"### Hold = {hold_h}h\n")
        header = ["Coin", "ann. % méd."]
        for c in COST_LEVELS_RT_BPS:
            header.append(f"{c:.0f} bps RT")
        w("| " + " | ".join(header) + " |")
        w("|---" + ":|---:" * (len(header) - 1) + ":|")
        for coin, hist in coins_sorted[:25]:
            if hist.n == 0:
                continue
            row = [coin]
            med_abs = hist.abs_quantiles().get("median", 0.0)
            row.append(f"{med_abs * 8760 / 100.0:+.1f}")
            for c in COST_LEVELS_RT_BPS:
                proj = project_costs(hist, c, hold_h)
                pct = proj["tradeable_pct"]
                edge = proj["median_edge_bps_when_tradeable"]
                if pct == 0:
                    row.append("—")
                else:
                    row.append(f"{pct:.1f}% / +{edge:.1f}bps")
            w("| " + " | ".join(row) + " |")
        w("")

    # ── §4 — break-even hours
    w("## 4. Break-even hold (heures de portage minimum)\n")
    w("Combien d'heures de hold faut-il pour couvrir le coût RT, en se basant "
      "sur la médiane historique de |funding|.\n")
    w("| Coin | médiane bps/h | 5 bps RT | 10 bps RT | 15 bps RT | 31 bps RT |")
    w("|---|---:|---:|---:|---:|---:|")
    for coin, hist in coins_sorted[:25]:
        if hist.n == 0:
            continue
        med = hist.abs_quantiles().get("median", 0.0)
        if med <= 0:
            continue
        row = [coin, f"{med:.3f}"]
        for c in COST_LEVELS_RT_BPS:
            h = break_even_hours(med, c)
            row.append(f"{h:.1f}h" if h is not None else "—")
        w("| " + " | ".join(row) + " |")
    w("")

    # ── §5 — recommandations
    w("## 5. Lecture honnête\n")
    if above_1 < 5:
        w("- Le funding est **structurellement bas** sur la majorité de "
          "l'univers HL en ce moment. Seuls quelques coins offrent un edge "
          "potentiel ; tout le reste serait perdant net après coûts, "
          "peu importe la calibration.")
    w("- L'edge funding existe **uniquement** sur les coins en haut du §2. "
      "Recalibrer FundingCarryHedged sans étendre l'univers ne changera rien.")
    w("- La microstructure (slippage depth-aware, maker mode) ne fabrique "
      "pas d'edge — elle réduit les coûts. Combinée à l'extension d'univers, "
      "elle ouvre la zone tradeable (cf. §3, colonnes 5-10 bps RT).")
    w("- Filtrer par liquidité : seuls les coins avec **vol 24h > "
      f"${args.min_vol_m:.1f}M** sont vraiment tradeables sans impact "
      "majeur sur le PnL.\n")
    # List coins matching liquidity floor + sustained funding (p75 > 0.5).
    liquid_high_funding = []
    for coin, hist in coins_sorted:
        if hist.n == 0:
            continue
        q = hist.abs_quantiles()
        if q.get("p75", 0) < 0.5:
            continue
        snap = next((s for s in snaps if s.coin == coin), None)
        if snap is None or snap.day_vol_usd < args.min_vol_m * 1e6:
            continue
        liquid_high_funding.append((coin, q["p75"], snap.day_vol_usd / 1e6))
    if liquid_high_funding:
        w("### Univers candidat (p75 |funding| ≥ 0.5 bps/h ET vol 24h ≥ "
          f"${args.min_vol_m:.1f}M)\n")
        w("| Coin | p75 |funding| bps/h | vol 24h $M |")
        w("|---|---:|---:|")
        for coin, p75, vol in liquid_high_funding:
            w(f"| {coin} | {p75:.3f} | {vol:.2f} |")
        w("")
    else:
        w("**Aucun coin** ne passe à la fois le filtre p75 ≥ 0.5 bps/h ET le "
          f"filtre vol 24h ≥ ${args.min_vol_m:.1f}M sur la fenêtre actuelle. "
          "Le strat est probablement **non-viable dans le régime de marché "
          "actuel**, peu importe la calibration microstructure.\n")

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # Also dump a machine-readable JSON sidecar so other tools can reuse it.
    json_path = args.out.replace(".md", ".json")
    payload = {
        "generated_at": ts,
        "universe_size": len(snaps),
        "above_thresholds_bps_h": {
            "0.5": above_05, "1.0": above_1, "2.0": above_2, "5.0": above_5
        },
        "deep_coins": [s.coin for s in deep],
        "histories": {
            c: {
                "n": h.n,
                "quantiles_abs_bps_h": h.abs_quantiles(),
            } for c, h in histories.items() if h.n > 0
        },
        "candidate_universe": [
            {"coin": c, "p75_abs_bps_h": p75, "day_vol_usd_m": vol}
            for c, p75, vol in liquid_high_funding
        ],
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


if __name__ == "__main__":
    main()
