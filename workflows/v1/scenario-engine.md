---
description: Scenario engine workflow — regime detection, robustness scoring, IS/OOS validation, per-regime accuracy assessment
---

# Scenario Engine Workflow

## When To Use
- Detecting market regimes (bull_trend, bear_trend, sideways, high_vol, low_vol, crisis, liquidity_shock, normal)
- Scoring regime robustness across IS/OOS
- Validating that regime detection aligns with forward return behavior
- Building ABSTAIN gates based on per-regime direction accuracy

## 8 Regimes

| Regime | Detection Criteria | Expected Behavior |
|---|---|---|
| bull_trend | ADX > 25 + EMA50 > EMA200 | Positive forward returns |
| bear_trend | ADX > 25 + EMA50 < EMA200 | Negative forward returns |
| sideways | ADX < 20 | Low absolute returns |
| high_vol | ATR rank > 0.90 | High variance returns |
| low_vol | ATR rank < 0.10 | Low variance returns |
| crisis | Return < -5sigma + ATR > 2x median + volume > 2x median | Extreme negative |
| liquidity_shock | Volume > 5x median + spread proxy extreme | Dislocation |
| normal | Default | Baseline |

## Regime Detection
```python
def detect_regime(df):
    """Assign regime label to each bar based on ADX, EMA, ATR, volume."""
    n = len(df)
    regimes = pd.Series(["normal"] * n, index=df.index)
    
    for i in range(n):
        if i < 200:  # warmup
            regimes.iloc[i] = "normal"
            continue
        
        # Priority: crisis > liquidity_shock > trend > vol > normal
        if is_crisis: regimes.iloc[i] = "crisis"
        elif is_liq_shock: regimes.iloc[i] = "liquidity_shock"
        elif adx > 25:
            if ema_50 > ema_200: regimes.iloc[i] = "bull_trend"
            else: regimes.iloc[i] = "bear_trend"
        elif adx < 20: regimes.iloc[i] = "sideways"
        elif atr_rank > 0.90: regimes.iloc[i] = "high_vol"
        elif atr_rank < 0.10: regimes.iloc[i] = "low_vol"
        else: regimes.iloc[i] = "normal"
    
    return regimes
```

## Robustness Scoring
- **Consistency:** Does the regime produce the same forward return sign in IS and OOS?
- **Dominant regime:** Which regime is most common for this symbol?
- **Worst regime:** Which regime has the worst performance?
- **IS-OOS correlation:** Should be > 0.80 for robust regime detection

## Regime Accuracy Validation
```python
def validate_regime_accuracy(regimes, fwd_returns):
    """Check that regimes correctly classify forward return behavior."""
    for regime in ALL_REGIMES:
        mask = regimes == regime
        regime_returns = fwd_returns[mask]
        # Check expected behavior:
        # bull_trend: positive mean return
        # bear_trend: negative mean return
        # sideways: low absolute mean return
        # high_vol: high std
        # low_vol: low std
```

## VLTHR Results
- 62.5% regime accuracy
- 0.8848 IS-OOS robustness correlation (strong)
- Per-regime direction accuracy (from ABSTAIN gate):
  - sideways: 53.7% [OK]
  - crisis: 53.8% [OK]
  - liquidity_shock: 51.3% [ABSTAIN]
  - normal: 49.3% [ABSTAIN]
  - bull_trend: 48.2% [ABSTAIN]
  - bear_trend: 47.3% [ABSTAIN]
  - low_vol: 41.7% [ABSTAIN]
  - high_vol: 44.3% [ABSTAIN]

## Key Insight
Only sideways and crisis regimes have direction accuracy > 52%. The system has no directional edge in trending or high/low vol regimes. This is counterintuitive — the system works best in non-trending conditions.

## Files
- `engine/v3/discovery/scenario_engine.py` — regime detection, robustness
- `engine/v3/discovery/scenario_runner.py` — batch runner
- `Backtest-Engine/backtest_phase7_scenarios.py` — full validation
