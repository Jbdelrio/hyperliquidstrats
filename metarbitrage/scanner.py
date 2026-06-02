"""
metarbitrage/scanner.py — moniteur de spread cross-venues (CEX spot), lecture seule.

Sélectionne dynamiquement l'univers (coins ≥ MIN_MKTCAP via CoinGecko), récupère
les tickers SPOT bid/ask sur plusieurs CEX (ccxt public, sans clé), et calcule le
spread **NET de coûts** entre la venue la moins chère (achat) et la plus chère
(vente). Écrit runtime/metarbitrage.json (consommé par le GUI) + logue dans
data/metarbitrage/opportunities.csv.

NET = (best_bid − best_ask)/best_ask − frais_achat − frais_vente − slippage.
On ne compare que du SPOT vs SPOT (like-for-like). Hyperliquid est un perp
(instrument différent) → exclu de l'arb (un écart perp/spot = basis, pas arb).

⚠️ MESURE UNIQUEMENT — aucune exécution, aucune clé. Lance :
    python -m metarbitrage.scanner            # boucle continue (30s)
    python -m metarbitrage.scanner --once     # un seul scan
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
LOG_CSV = ROOT / "data" / "metarbitrage" / "opportunities.csv"
OUT_JSON = RUNTIME / "metarbitrage.json"

# ── paramètres ───────────────────────────────────────────────────────────────
MIN_MKTCAP_USD = 500_000_000          # ≥ 500 M$ de capitalisation
QUOTE = "USDT"
SCAN_INTERVAL_S = 30
MIN_NET_BPS = 5.0                     # seuil d'affichage d'une "opportunité" nette
SLIPPAGE_BPS = 5.0                    # tampon conservateur de slippage par côté
MAX_COINS = 40                       # plafond d'univers (perf)
# Au-delà, un "spread" est presque sûrement un LEURRE (quote périmée, token
# illiquide, listing différent entre venues) — pas un arb réel. On le signale
# SUSPECT et on l'exclut du décompte net-positif honnête.
MAX_SANE_GROSS_BPS = 100.0
# Rejet d'outliers : un prix qui s'écarte de plus de SANITY_DEV de la MÉDIANE
# cross-venues est presque sûrement un mauvais token (DexScreener renvoie des
# tokens-arnaque au même symbole) ou une quote périmée → exclu du calcul.
SANITY_DEV = 0.20

# CEX spot publics (ccxt, sans clé) + frais taker spot (fraction).
CEX_FEES = {
    "binance": 0.0010, "bybit": 0.0010, "okx": 0.0010,
    "kucoin": 0.0010, "gateio": 0.0010,
}
# DEX Solana via Jupiter : le quote API renvoie déjà un prix exécutable (frais de
# pool + slippage inclus dans le bid/ask), donc on n'ajoute qu'un coût de gas ~négl.
JUPITER_FEE = 0.0003
ENABLE_JUPITER = True
VENUE_FEES = {**CEX_FEES, "jupiter": JUPITER_FEE}

# Venues additionnelles demandées : Hyperliquid + Aster (PERP), PancakeSwap +
# DexScreener (SPOT). Un spread perp↔spot = BASIS (funding), pas un arb atomique.
ENABLE_EXTRA_VENUES = True
FORCE_COINS = {"HYPE": "hyperliquid", "ASTER": "aster-2", "SOL": "solana",
               "BTC": "bitcoin", "ETH": "ethereum"}   # symbol -> id CoinGecko
from metarbitrage import venues as _venues
ALL_FEES = {**VENUE_FEES, **_venues.VENUE_FEE}
VENUE_TYPE = {**{c: "spot" for c in CEX_FEES}, "jupiter": "spot", **_venues.VENUE_TYPE}

_UNIVERSE_CACHE: dict = {"ts": 0.0, "coins": []}
_COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"


def get_universe(min_mktcap: float = MIN_MKTCAP_USD, ttl_s: float = 1800) -> list[dict]:
    """Top coins par market cap (CoinGecko free), filtrés ≥ min_mktcap. Caché 30 min.
    Exclut les stablecoins. Retourne [{symbol, mktcap}]."""
    now = time.time()
    if _UNIVERSE_CACHE["coins"] and now - _UNIVERSE_CACHE["ts"] < ttl_s:
        return _UNIVERSE_CACHE["coins"]
    stables = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD", "PYUSD"}
    coins = []
    try:
        r = requests.get(_COINGECKO, params={
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 100, "page": 1}, timeout=20)
        for c in r.json():
            sym = str(c.get("symbol", "")).upper()
            mc = float(c.get("market_cap") or 0)
            if sym and sym not in stables and mc >= min_mktcap:
                coins.append({"symbol": sym, "mktcap": mc, "id": c.get("id")})
    except Exception as e:
        print(f"  [warn] CoinGecko univers échec: {e}")
        return _UNIVERSE_CACHE["coins"]
    coins = coins[:MAX_COINS]
    _UNIVERSE_CACHE.update(ts=now, coins=coins)
    return coins


_ADDR_CACHE: dict = {"ts": 0.0, "map": {}}


def resolve_addresses(universe: list[dict], ttl_s: float = 3600) -> dict:
    """{SYMBOL: adresse de contrat canonique} via CoinGecko platforms (id de
    coins/markets → adresse). Préfère BSC (PancakeSwap) puis ETH/Solana. Caché 1h."""
    now = time.time()
    if _ADDR_CACHE["map"] and now - _ADDR_CACHE["ts"] < ttl_s:
        return _ADDR_CACHE["map"]
    ids = {c["id"]: c["symbol"] for c in universe if c.get("id")}
    if not ids:
        return {}
    try:
        lst = requests.get("https://api.coingecko.com/api/v3/coins/list",
                           params={"include_platform": "true"}, timeout=25).json()
    except Exception as e:
        print(f"  [warn] résolution adresses échouée: {e}")
        return _ADDR_CACHE["map"]
    byid = {c.get("id"): c for c in lst if isinstance(c, dict)}
    pref = ["binance-smart-chain", "ethereum", "solana", "base", "arbitrum-one", "hyperliquid"]
    out = {}
    for cid, sym in ids.items():
        plats = {k: v for k, v in ((byid.get(cid, {}) or {}).get("platforms", {}) or {}).items() if v}
        if not plats:
            continue
        addr = next((plats[ch] for ch in pref if plats.get(ch)), next(iter(plats.values())))
        out[sym.upper()] = addr
    _ADDR_CACHE.update(ts=now, map=out)
    return out


def _fetch_exchange_tickers(name: str) -> dict:
    """{SYMBOL: {bid, ask}} pour le SPOT d'un CEX via ccxt public. {} si échec."""
    try:
        import ccxt
        ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 15000})
        ex.options = {**getattr(ex, "options", {}), "defaultType": "spot"}
        tickers = ex.fetch_tickers()
    except Exception as e:
        print(f"  [warn] {name}: {e.__class__.__name__}")
        return {}
    out = {}
    for sym, t in tickers.items():
        # ne garder que les paires SPOT */QUOTE
        if not sym.endswith(f"/{QUOTE}"):
            continue
        base = sym.split("/")[0]
        bid, ask = t.get("bid"), t.get("ask")
        if bid and ask and bid > 0 and ask > 0:
            out[base] = {"bid": float(bid), "ask": float(ask)}
    return out


def scan_once() -> dict:
    universe = get_universe()
    # Forcer l'écosystème (HYPE/ASTER…) même hors top CoinGecko.
    have = {c["symbol"] for c in universe}
    for sym, cgid in FORCE_COINS.items():
        if sym not in have:
            universe.append({"symbol": sym, "mktcap": 0, "id": cgid})
    symbols = [c["symbol"] for c in universe]

    venue_data = {name: _fetch_exchange_tickers(name) for name in CEX_FEES}
    # Venue DEX Solana (Jupiter) — prix exécutables sur les tokens Solana liquides.
    if ENABLE_JUPITER:
        try:
            from metarbitrage.jupiter import fetch_jupiter_tickers
            venue_data["jupiter"] = fetch_jupiter_tickers(symbols)
        except Exception as e:
            print(f"  [warn] jupiter: {e.__class__.__name__}")
    # Venues demandées : Hyperliquid, Aster (perp) + PancakeSwap, DexScreener (spot).
    if ENABLE_EXTRA_VENUES:
        try:
            venue_data["hyperliquid"] = _venues.hyperliquid_tickers()
            venue_data["aster"] = _venues.aster_tickers()
            dx = _venues.dexscreener_tickers(resolve_addresses(universe))
            venue_data["pancakeswap"] = dx.get("pancakeswap", {})
            venue_data["dexscreener"] = dx.get("dexscreener", {})
        except Exception as e:
            print(f"  [warn] extra venues: {e.__class__.__name__}")
    live_venues = [v for v, d in venue_data.items() if d]

    opportunities = []
    for coin in universe:
        sym = coin["symbol"]
        quotes = []   # (venue, bid, ask)
        for v in live_venues:
            d = venue_data[v].get(sym)
            if d:
                quotes.append((v, d["bid"], d["ask"]))
        if len(quotes) < 2:
            continue
        # Rejet d'outliers vs médiane (démasque les faux tokens DexScreener / quotes stale)
        import statistics
        mids = [(b + a) / 2 for _, b, a in quotes]
        med = statistics.median(mids)
        if med > 0:
            quotes = [q for q in quotes if abs((q[1] + q[2]) / 2 - med) / med <= SANITY_DEV]
        if len(quotes) < 2:
            continue
        # acheter là où l'ASK est le plus bas, vendre là où le BID est le plus haut
        buy_venue, _, buy_ask = min(quotes, key=lambda q: q[2])
        sell_venue, sell_bid, _ = max(quotes, key=lambda q: q[1])
        if buy_venue == sell_venue or buy_ask <= 0:
            continue
        gross_bps = (sell_bid - buy_ask) / buy_ask * 1e4
        cost_bps = (ALL_FEES[buy_venue] + ALL_FEES[sell_venue]) * 1e4 + 2 * SLIPPAGE_BPS
        net_bps = gross_bps - cost_bps
        suspect = gross_bps > MAX_SANE_GROSS_BPS    # leurre probable (illiquide/stale)
        buy_type = VENUE_TYPE.get(buy_venue, "spot")
        sell_type = VENUE_TYPE.get(sell_venue, "spot")
        basis = buy_type != sell_type               # perp↔spot = basis, pas arb atomique
        opportunities.append({
            "coin": sym, "mktcap_m": round(coin["mktcap"] / 1e6),
            "buy_venue": buy_venue, "buy_ask": buy_ask, "buy_type": buy_type,
            "sell_venue": sell_venue, "sell_bid": sell_bid, "sell_type": sell_type,
            "gross_bps": round(gross_bps, 2), "cost_bps": round(cost_bps, 2),
            "net_bps": round(net_bps, 2), "n_venues": len(quotes),
            "suspect": suspect, "basis": basis,
        })
    opportunities.sort(key=lambda o: -o["net_bps"])
    # "net positif HONNÊTE" = net ≥ seuil, non suspect (leurre), et MÊME instrument
    # (un spread perp↔spot = basis, pas un arbitrage capturable).
    positive = [o for o in opportunities
                if o["net_bps"] >= MIN_NET_BPS and not o["suspect"] and not o["basis"]]

    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe_size": len(universe), "live_venues": live_venues,
        "n_positive_net": len(positive),
        "best_net_bps": opportunities[0]["net_bps"] if opportunities else None,
        "opportunities": opportunities[:25],
        "note": "MESURE UNIQUEMENT — net de frais+slippage. Aucune exécution.",
    }
    RUNTIME.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    _log_csv(positive)
    return snapshot


def _log_csv(positive: list[dict]) -> None:
    if not positive:
        return
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG_CSV.exists()
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "coin", "buy_venue", "buy_ask", "sell_venue",
                        "sell_bid", "gross_bps", "cost_bps", "net_bps"])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for o in positive:
            w.writerow([ts, o["coin"], o["buy_venue"], o["buy_ask"], o["sell_venue"],
                        o["sell_bid"], o["gross_bps"], o["cost_bps"], o["net_bps"]])


def main() -> int:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=SCAN_INTERVAL_S)
    args = ap.parse_args()
    print("METARBITRAGE monitor — MESURE UNIQUEMENT (aucun trade, aucune clé)")
    while True:
        t0 = time.time()
        snap = scan_once()
        print(f"[{snap['ts']}] univers={snap['universe_size']} venues={snap['live_venues']} "
              f"net≥{MIN_NET_BPS}bps: {snap['n_positive_net']} | "
              f"meilleur net={snap['best_net_bps']}bps ({time.time()-t0:.1f}s)")
        if args.once:
            return 0
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
