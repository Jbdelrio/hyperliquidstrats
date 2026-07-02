# SIG-ARB — Signature-based statistical arbitrage (backtest-only)

An independent strategy module inspired by *"Signature-Based Optimal Execution for
Statistical Arbitrage with Path-Dependent Trading Signals"*. It is added **alongside**
the existing Pulse / positioning strategies and changes none of their behaviour.

> **SECURITY.** This module is **backtest-only**. `StrategyConfig.backtest_only`
> defaults to `True` and there is **no live-order code path**. The GUI endpoint
> forces `backtest_only=True`. No secrets/keys are used or logged.

---

## 1. Intuition

A pair (or residual vs. a factor basket) is *statistical arbitrage*: the spread
between two crypto assets tends to mean-revert. The strategy separates two layers
that most implementations blur together:

- **Layer 1 — Alpha.** *Will the spread converge or diverge over a short horizon?*
  Instead of a single `z-score`, it summarises the recent **path** of a small
  information vector `Z_t` with its **truncated signature** `x_t`, then maps it to
  an expected edge `alpha_t`.
- **Layer 2 — Optimal execution.** *Given that edge, how fast should we trade and
  how much should we hold?* A continuous linear **speed policy** `v_t = B x_t` is
  learned by minimising a convex objective that trades edge against execution cost,
  inventory risk, dollar-neutrality and a terminal-liquidation penalty.

So the pipeline is a **policy**, not a rule:

```
market history -> information path Z_t -> signature x_t -> alpha_t
              -> trading speed v_t=Bx_t -> target inventory Q_t -> simulated orders
```

## 2. Alpha vs. execution — why the split matters

The alpha model only *predicts*. The optimizer only *acts*. You can have a good
signal and still lose money by trading it badly (overtrading fees, holding too much
inventory into a vol spike). Keeping them separate lets you diagnose which layer is
responsible (see the benchmark `optimal policy without costs` vs `with costs`).

## 3. Spread construction (`spread_model.py`)

`s_t` is built causally by one of:

| method  | hedge ratio                                   |
|---------|-----------------------------------------------|
| `ratio` | fixed `beta0`                                 |
| `ols`   | rolling OLS residual                          |
| `ridge` | rolling ridge (stabilised beta)               |
| `kalman`| state-space `(alpha_t, beta_t)` random walk (filtered) |
| `factor`| residual vs. a BTC/ETH factor basket (market-neutral) |

**Kalman filter.** State `x=[alpha,beta]`, observation `logP1 = alpha + beta·logP2`.
Random-walk state (process variance `Q·I`), observation variance `R`. Only the
*filtered* (causal) estimate is used — never a smoother, which would peek ahead.

**Diagnostics** (all reported, plus a synthetic quality score): half-life, ADF
p-value, Engle-Granger cointegration p-value, variance ratio, zero-crossings, hedge
stability, extreme-excursion frequency. A per-bar `tradeable` mask disables new
entries when the regime breaks (non-stationary, unstable hedge, `|z|` too large).

## 4. Signatures & Lévy area (`signature_features.py`)

The signature of a path truncated at level `N` is the collection of iterated
integrals up to order `N`. For discrete data we use the piecewise-linear signature,
computed by folding per-segment signatures with **Chen's identity**. `iisignature`
is used if importable; otherwise an **exact numpy fallback** (levels 1–3) is used —
the two agree (tested). Dimension `m = sum_{k=0}^{N} d^k` (`d` = active channels).

The information path channels are toggleable: `normalized_time, spread, zscore,
asset_1_return, asset_2_return, btc_return, order_flow_imbalance, realized_volatility,
book_spread, funding`. Normalization is **leak-free** (trailing-window statistics, or
train-only fit for `train_fit`).

**Lévy area** `A_ij = S_ij − S_ji` is the antisymmetric part of the level-2
signature; it encodes signed area / lead-lag *ordering* between channels. It is
**not** a causality claim, and it is presented as a path feature only.

## 5. Alpha model (`alpha_model.py`)

- `zscore` : `alpha_t = −gain · z_t` (no fitting).
- `ridge`  : `K = argmin ||y − XK||² + λ||K||²`, target `y = −(s_{t+h} − s_t)`.
- `elasticnet` : ridge + L1 (sklearn if present).

`K` is fit **on the training slice only**; rows without a full horizon ahead are
`NaN` and excluded (no look-ahead). Out-of-sample IC / rank-IC / R² / decile
performance are reported.

## 6. Execution optimizer (`execution_optimizer.py`)

Spread inventory `Q_t` is a scalar. Because `Q_t = θᵀ c_t` (with `c_t = dt·cumsum(x)`
and `θ = Bᵀ`), the empirical objective is **quadratic in θ**:

```
maximise  J(θ) = Σ_t [ Q_t·alpha_t − λ v_t² − φ σ_t² Q_t² − η (netdollar_t Q_t)² ] dt
                 − γ Q_T²  − ρ ||θ||²
       => θ* = ½ M⁻¹ h,  solved with np.linalg.solve (never an explicit inverse).
```

Roles of the weights: **ρ** shrinks `||B||`; **φ** penalises inventory risk; **η**
enforces dollar-neutrality; **γ** forces liquidation before the horizon; **λ**
penalises trading speed. Episodes reset `Q` so `γ` is meaningful. The raw solved `θ`
is kept (so `ρ→||B||` monotonicity is observable) and a single positive **scale**
normalises the policy to a target inventory utilisation.

**No-trade band, caps and terminal liquidation** are applied in
`simulate_inventory`: `v` is capped at `maximum_velocity`, `Q` at `inventory_cap`,
inventory freezes when the desired change is below the band, and every episode ends
flat.

## 7. Costs, fills, two-leg, risk

- **Costs** (`costs.py`): `C(v) = a|v| + b v² + c·1{v≠0}` + fees/spread/slippage/
  funding/latency, all per-exchange configurable — no hard-coded "current" fees.
- **Fills** (`fill_models.py`): `simple`, `bid_ask`, `maker_prob` (probabilistic
  maker fill; a full L2 queue model is only meaningful with L2 data, which is not
  assumed). Every fill records which model produced it.
- **Two-leg** (`two_leg_execution.py`): converts a spread target into per-leg
  notionals and tracks hedge error / dollar imbalance / desync incidents.
- **Risk** (`risk_manager.py`): sizes `Q`→USD under gross/net/leverage/position
  limits; kill-switch on drawdown / daily-loss; prudent 300$ & 1000$ presets.

## 8. Walk-forward (`walk_forward.py`)

Strictly temporal, **no shuffling**. `purge` drops training rows whose label horizon
overlaps the test window; `embargo` removes a gap after each test window from future
training. Scalers and all fitted objects (`K`, `B`, hedge ratios) use train data only.

## 9. Benchmarks (`backtest.py`)

Run on the **same test window, capital and cost model**: buy&hold each leg, classic
`z` entry/exit, continuous `−z` position. (The naive z-score comparators overtrade
and bleed fees — which is exactly what the cost-aware optimizer avoids.)

## 10. Limits / simplifications (stated honestly)

- Signatures are collinear by construction → the optimizer matrix is often
  ill-conditioned; this is **surfaced** as `stability_warning` and mitigated by `ρ`
  and the scale normalisation. Treat a huge condition number with suspicion.
- The backtest operates on the **decision grid**; long spans / high frequencies are
  **coarsened** to keep runs bounded (the effective cadence is reported in
  `data_meta`). This is aggregation of real data, never invented data.
- `maker_prob` / L2 fills are approximations (no L2 book data assumed).
- VECM (§5.6) is intentionally **not** implemented (no `statsmodels` dependency); the
  `factor` residual covers the market-neutral use case.
- The GUI consolidates the paper's many diagnostic pages into one results panel plus
  the full JSON returned by `/api/sigarb/run` (exportable).

## 11. Running a first backtest

**From the GUI:** open the **SIG-ARB** panel (bottom of the dashboard) → keep
`source = demo` for a first smoke run → **Lancer le backtest**. Demo results are
badged as synthetic. Switch `source = binance` (or `file`) for real data.

**From Python:**

```python
from strategies.signature_stat_arb import StrategyConfig, run_backtest
cfg = StrategyConfig()
cfg.data.source = "binance"          # real public klines
cfg.data.symbol_1, cfg.data.symbol_2 = "BTC", "ETH"
cfg.data.start, cfg.data.end = "2024-01-01", "2024-02-01"
res = run_backtest(cfg, walk_forward=True)
print(res["aggregate"])              # median/mean/ dispersion of fold Sharpes
```

**Tests:** `python -m pytest tests/test_signature_stat_arb.py -q` (21 tests).

## 12. Optional dependencies

- `iisignature` — faster signatures (exact numpy fallback otherwise).
- `scikit-learn` — `elasticnet` alpha (ridge fallback otherwise).
- `arch` — ADF / cointegration p-values (already in `requirements.txt`).
