---
description: Hypothesis engine workflow — Bayesian hypothesis generation, testing, lifecycle management, and promotion/refutation
---

# Hypothesis Engine Workflow

## When To Use
- Generating and testing trading hypotheses (momentum, reversal, lead-lag, regime, seasonal, structural)
- Bayesian updating of hypothesis confidence with new evidence
- Managing hypothesis lifecycle (untested -> testing -> confirmed/refuted -> retired)
- Accumulating evidence across symbols and time

## Hypothesis Types (6)

| Type | Description | Test Logic |
|---|---|---|
| reversal | Price reverses after extreme move | Mean reversion test |
| momentum | Price continues in direction of move | FOLLOW = momentum continuation |
| lead_lag | One asset leads another | Cross-asset correlation at lag |
| regime | Behavior differs by market regime | Per-regime conditional test |
| seasonal | Behavior differs by time/session | NEUTRAL = session/vol tests |
| structural | Structural market patterns | Custom per hypothesis |

## 12 Default Templates
1. `rsi_oversold_funding_negative` — RSI < 30 + negative funding = LONG reversal
2. `rsi_overbought_funding_positive` — RSI > 70 + positive funding = SHORT reversal
3. `funding_extreme_reversal` — Extreme funding predicts reversal
4. `ls_ratio_extreme` — Extreme LS ratio predicts contrarian move
5. `volume_surge_momentum` — Volume surge predicts momentum continuation
6. `oi_surge_overbought` — OI surge + overbought = SHORT
7. `mean_reversion_in_low_adx` — Low ADX = mean reversion works
8. `adx_strong_trend_ema_align` — High ADX + EMA align = trend following works
9. `trend_following_in_high_adx` — High ADX = trend following works
10. `btc_leads_alts_15m` — BTC leads altcoins by 15m
11. `us_session_volatility` — US session has higher volatility
12. `weekend_low_volume` — Weekend has lower volume

## Bayesian Updating (Beta-Binomial Conjugate Prior)
```python
def bayesian_update(prior, n_success, n_total, N=100):
    """Beta-Binomial conjugate prior update.
    prior: current posterior (0-1)
    n_success: evidence_for count
    n_total: total evidence count
    N: pseudo-count (controls prior weight)
    """
    alpha = prior * N + n_success
    beta = (1 - prior) * N + (n_total - n_success)
    posterior = alpha / (alpha + beta)
    return posterior
```

**Key fix:** Use `N=100` with `prior * N` (linear weighting). Previous bug used `prior_n = int(prior * 100)` causing quadratic weighting where high priors got 81x more weight.

## Lifecycle States
```
untested -> testing -> confirmed
                   -> refuted -> retired
confirmed (protected unless posterior < 0.3)
```

**Key fix:** Only promote UNTESTED -> TESTING. CONFIRMED hypotheses are protected unless posterior drops below 0.3. Previous bug demoted CONFIRMED -> TESTING on every new evidence batch.

## Steps

1. **Load enriched data** with `max_bars=2000` (NOT default 500 — that's a silent cap)
2. **Load existing hypotheses** from `hypothesis_registry` DB table
3. **For each hypothesis:**
   a. Compute dynamic conditions (funding z-score, LS ratio, volume ratio)
   b. Find matching bars where conditions are met
   c. Test each matching bar (FOLLOW, REVERT, or NEUTRAL logic)
   d. Bayesian update with new evidence
   e. Check for promotion/refutation
4. **Write updated hypotheses** to DB
5. **Report summary:** confirmed count, testing count, untested count

## Test Logic
- **FOLLOW:** Price goes in direction of signal (momentum)
- **REVERT:** Price goes opposite to signal (mean reversion)
- **NEUTRAL:** Test is about a property (volatility level, volume level), not direction
- **TREND/REVERT:** Must use per-bar direction matching, NOT single autocorrelation value for all bars

## Confirmed Results (Post Bug Fixes — Pre-V7)
- `us_session_volatility` (P=0.999) — US session has higher volatility
- `weekend_low_volume` (P=0.998) — Weekend has lower volume
- `funding_extreme_reversal` (P=0.523) — Weak reversal signal at funding extremes

> **V7 Note:** V7 does not rely on individual hypothesis results for direction prediction. The V3 adaptive scorer uses ctx-only weighting informed by probe IC results, combined with isotonic calibration filtering. Hypothesis results remain valuable for understanding market structure but are not directly in the V7 decision path.

## Testing (Near Chance)
- `rsi_oversold_funding_negative` (P=0.534)
- `rsi_overbought_funding_positive` (P=0.522)
- `mean_reversion_in_low_adx` (P=0.490)
- `adx_strong_trend_ema_align` (P=0.478)
- `trend_following_in_high_adx` (P=0.477)
- `btc_leads_alts_15m` (P=0.443)

## Files
- `engine/v3/discovery/hypothesis_engine.py` — core engine
- `engine/v3/discovery/hypothesis_runner.py` — batch runner
- `Backtest-Engine/backtest_phase6_hypotheses.py` — walk-forward validation
