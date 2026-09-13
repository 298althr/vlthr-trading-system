---
description: Probe engine workflow — feature discovery, IC validation, Wilson lower bounds, redundancy computation, edge detection
---

# Probe Engine Workflow

## When To Use
- Discovering features with predictive power (Information Coefficient)
- Validating feature significance with Wilson lower bounds
- Computing feature redundancy and correlation matrices
- Determining which features to promote to trusted status

## Probe Types (10)

| Type | Description | Example Features |
|---|---|---|
| trend | Momentum / trend-following probes | ret_96, ema_alignment, adx_strength |
| risk | Drawdown / risk-based probes | drawdown_96, vol_regime |
| orderflow | Volume / OI based probes | volume_ratio, oi_delta |
| regime | Regime classification probes | trend_dir, vol_regime |
| decision | Decision-context probes | dec_log_ret_4h, dec_adx_4h |
| capital | Position / capital probes | kelly_mult, risk_weight |
| auction | Price discovery probes | spread, slippage_est |
| volatility | Volatility structure probes | hurst_exponent, atr_percentile |
| time | Temporal / session probes | session, day_of_week |
| liquidity | Liquidity assessment probes | depth_proxy, volume_profile |
| fractal | Multi-scale structure probes | fractal_dim, wavelet_coef |

## IC Calculation
```python
def rolling_ic(feature, forward_return, window=200):
    """Rolling Spearman rank correlation between feature and forward return."""
    ic = feature.rolling(window).corr(forward_return, method='spearman')
    return ic.mean(), ic.std(), ic.count()
```

## Wilson Lower Bound
```python
def wilson_lower_bound(ic, n, z=1.96):
    """Conservative lower bound on IC significance."""
    if n < 30:
        return 0.0
    p = (ic + 1) / 2  # transform [-1,1] to [0,1]
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n)) / denominator
    spread = z * np.sqrt((p*(1-p) + z*z/(4*n)) / n) / denominator
    return (center - spread) * 2 - 1  # transform back
```

## Steps

1. **Load enriched data** via `confidence_engine.load_enriched(symbol, max_bars=2000)`
2. **Compute forward returns** (4 bars = 1 hour on 15m)
3. **Run all 10 probe types** on IS data
4. **Compute IC, IC std, Wilson lower bound** for each feature
5. **Run on OOS data** to check persistence
6. **Compute redundancy matrix** — correlation between top features
7. **Promote features** with Wilson > 0.05 and OOS persistence to trusted status
8. **Demote features** with `abs(ic) < threshold` (NOT `ic < threshold` — negative IC is valuable)

## Promotion/Demotion Rules
- **Promote:** Wilson lower bound > 0.05 AND OOS persistent (same sign IC)
- **Demote:** `abs(ic) < 0.02` over rolling window
- **Redundancy:** If two features have corr > 0.85, keep the one with higher Wilson
- **Key fix:** Use `abs(ic)` for demotion, NOT `ic` — negative IC (contrarian) is a valid signal

## Post-Leakage-Fix Results
- 192 features probed across 6 symbols
- IS avg |IC|: 0.083, OOS avg |IC|: 0.082
- **0/192 statistically significant** (Wilson > 0.05)
- 129/192 (67.2%) persist OOS
- Top features: risk_drawdown_96 (IC=-0.24), trend_ret_96 (IC=-0.24), regime_trend_dir (IC=-0.23)
- All top features are contrarian (negative IC)
- Trend probe type highest IC (IS=0.158, OOS=0.163)

## Key Insight
No single feature has a standalone edge. Ensemble/combination approaches needed. The previous top feature (dec_log_ret_4h, IC=0.44) was inflated by label leakage and dropped to IC=0.07 after the fix.

## Files
- `engine/v3/discovery/probe_engine.py` — 10 probe types, IC scoring
- `engine/v3/discovery/probe_runner.py` — batch runner, redundancy
- `Backtest-Engine/backtest_phase5_probes.py` — full backtest validation
- `Backtest-Engine/results/phase5_probe_ic_validation.json`
