"""
metarbitrage/jupiter.py — venue DEX Solana via Jupiter (quote API), prix EXÉCUTABLE.

On n'utilise PAS un simple "prix mid" : on interroge le quote API de Jupiter pour
une taille donnée (NOTIONAL_USDC) dans les deux sens, ce qui donne un bid/ask
RÉELLEMENT exécutable, **slippage AMM et frais de pool inclus**. C'est la seule
façon honnête de comparer un DEX (AMM, profondeur variable) à un CEX (carnet).

Jupiter fonctionne par adresses de MINT Solana, pas par symbole. On maintient un
registre curé des tokens Solana RÉELLEMENT liquides (un prix DEX n'a de sens que
là). Les tokens non-natifs/illiquides (ex. wrapped fins) sont volontairement exclus.

Lecture seule : quote API uniquement, aucune transaction, aucune clé.
"""
from __future__ import annotations

import time

import requests

_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"   # endpoint courant (v6 déprécié)
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DEC = 6

# Registre curé : symbol -> (mint, decimals). Tokens Solana à liquidité réelle.
SOLANA_MINTS: dict[str, tuple[str, int]] = {
    "SOL":  ("So11111111111111111111111111111111111111112", 9),
    "JUP":  ("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
    "BONK": ("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5),
    "WIF":  ("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 6),
    "JTO":  ("jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL", 9),
    "PYTH": ("HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", 6),
    "RAY":  ("4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", 6),
    "ORCA": ("orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", 6),
    "W":    ("85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ", 6),
    "JLP":  ("27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4", 6),
    "DRIFT": ("DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7", 6),
}

# Coins demandés mais sans marché Solana natif liquide (documenté, non couvert).
NO_LIQUID_SOLANA = {"HYPE", "LINK", "PEPE", "ASTER", "BANANAS31"}


def _quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> dict | None:
    try:
        r = requests.get(_QUOTE_URL, params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": int(amount), "slippageBps": slippage_bps,
            "restrictIntermediateTokens": "true"}, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def jupiter_bid_ask(symbol: str, notional_usdc: float = 500.0) -> dict | None:
    """{bid, ask} exécutables sur Jupiter pour ~notional_usdc, ou None.
    ask = prix d'ACHAT (USDC→token) ; bid = prix de VENTE (token→USDC).
    L'écart ask-bid reflète déjà les frais de pool + le price impact."""
    if symbol not in SOLANA_MINTS:
        return None
    mint, dec = SOLANA_MINTS[symbol]
    # 1) ACHAT : USDC -> token
    q_buy = _quote(USDC_MINT, mint, int(notional_usdc * 10 ** USDC_DEC))
    if not q_buy or "outAmount" not in q_buy:
        return None
    tokens = int(q_buy["outAmount"]) / 10 ** dec
    if tokens <= 0:
        return None
    ask = notional_usdc / tokens
    # 2) VENTE : token -> USDC (sur la quantité qu'on vient d'obtenir)
    q_sell = _quote(mint, USDC_MINT, int(tokens * 10 ** dec))
    if not q_sell or "outAmount" not in q_sell:
        return None
    usdc_out = int(q_sell["outAmount"]) / 10 ** USDC_DEC
    bid = usdc_out / tokens
    if bid <= 0 or ask <= 0:
        return None
    return {"bid": float(bid), "ask": float(ask)}


def fetch_jupiter_tickers(symbols: list[str], notional_usdc: float = 500.0,
                          pause_s: float = 0.2) -> dict:
    """{SYMBOL: {bid, ask}} pour les symboles couverts par SOLANA_MINTS."""
    out = {}
    for sym in symbols:
        if sym not in SOLANA_MINTS:
            continue
        ba = jupiter_bid_ask(sym, notional_usdc)
        if ba:
            out[sym] = ba
        time.sleep(pause_s)
    return out
