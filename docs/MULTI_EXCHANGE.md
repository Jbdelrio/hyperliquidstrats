# Multi-Exchange Architecture

## Exchange Adapter Layer

```
exchanges/
  base.py              ← Abstract interface (BaseExchangeAdapter)
  schemas.py           ← Normalized data models
  hyperliquid_adapter.py ← Primary exchange (wraps OrderbookManager)
  binance_adapter.py   ← Data + optional live
  bitget_adapter.py    ← Data + optional live
  factory.py           ← get_exchange() / get_enabled_exchanges()
```

All adapters implement the same interface. Execution flows through the existing `HighFreqExecutor` for Hyperliquid and through `MultiExchangeExecutor` for future live support on Binance/Bitget.

## Enabling Binance (data-only)

```bash
# In .env:
BINANCE_ENABLED=true
ENABLED_EXCHANGES=hyperliquid,binance
BINANCE_TESTNET=true       # start on testnet
BINANCE_LIVE_TRADING=false # never trade on Binance until ready
```

The engine will then fetch Binance ticker/orderbook data for cross-exchange comparison in the LLM overlay. No orders will be sent to Binance.

## Enabling Bitget (data-only)

```bash
BITGET_ENABLED=true
ENABLED_EXCHANGES=hyperliquid,binance,bitget
BITGET_TESTNET=true
BITGET_LIVE_TRADING=false
```

## Using Testnet

All adapters default to testnet mode. The `TESTNET=true` flag routes REST calls to the testnet endpoint. Data quality may differ from production.

## Activating Live Trading (Binance/Bitget)

Live trading on non-default exchanges requires **three** explicit confirmations:

1. `GLOBAL_LIVE_TRADING=true`
2. `BINANCE_LIVE_TRADING=true` (or BITGET)
3. `BINANCE_API_KEY` + `BINANCE_API_SECRET` populated

If any of these are missing → `place_order()` returns `status="blocked_live_disabled"`.

**Current status:** Binance and Bitget `place_order()` raise `NotImplementedError` even when live is enabled — a safety scaffold. Implement only when live infrastructure is validated.

## How the LLM Uses Cross-Exchange Data

When multiple exchanges are enabled, `factory.collect_cross_exchange_data()` runs before the LLM overlay and populates `MarketSnapshot.cross_exchange_data` with:

```json
{
  "binance": {
    "mid": 65002.5,
    "spread_bps": 0.8,
    "funding_rate": 0.00008,
    "orderbook_imbalance": 0.05
  },
  "bitget": { ... }
}
```

The `CrossExchangeAgent` analyses this data and can:
- Flag `bad_execution_venue` if Hyperliquid spread is significantly worse
- Flag `price_deviation_high` if prices diverge > 15 bps
- Flag `weak_cross_exchange_confirmation` if signal exists only on one exchange

The LLM **cannot**:
- Execute orders on any exchange
- Increase position size based on cross-exchange confirmation
- Implement inter-exchange arbitrage

## Why the LLM Cannot Execute

- `BaseExchangeAdapter` is never imported in `llm_agents/`
- `llm_agents/` only imports from `llm_agents/` (fully self-contained)
- The engine injects `MarketSnapshot` data (read-only struct) into the overlay
- The overlay returns `LLMDecision` (read-only struct) to the engine
- The engine's `_execute_decision()` does the actual execution

## Risks

| Risk | Mitigation |
|---|---|
| Latency (REST vs WebSocket) | Cross-exchange data fetched async, not on critical path |
| Symbol mismatch | `normalize_symbol()` in each adapter |
| Fee differences | `get_fees()` exposed per adapter; LLM informed |
| Liquidation differences | Out of scope — LLM flags liquidity risk only |
| Funding disagreement | Fetched and passed to LLM; LLM can flag |
| Slippage on target venue | MicrostructureAgent and CrossExchangeAgent flag high spread |

## Future: Smart Order Routing, Arbitrage Monitoring

Not currently activated. Possible future modules:

- **Best Venue Selection**: route execution to the exchange with the best spread+depth
- **Arbitrage Monitor**: detect persistent price divergence (alert only, no auto-trade)
- **Smart Order Routing**: split large orders across exchanges for better fill

These would be implemented as separate opt-in modules, never activated automatically.
