# VLTHR Trading Centre Upgrade Plan

## Objective
Apply 3 backtestable feature upgrades to the DQS scoring pipeline, validate each via backtest, and prepare for production deployment. Each upgrade is independent and can be toggled on/off via config.

---

## Current State

**Backtest baseline (Phase 1 fix, control mode):**
- PF: 2.83 | WR: 38.1% | Expectancy: $100.58 | Max DD: 11.08%
- 5/6 gates pass (TP hit rate fails at 36.8%)

**DQS structure (30% weight) already uses:**
- Raw `funding_rate` (threshold-based scoring: <-0.0005 = 100, etc.)
- Raw `oi_delta_pct` (24h change, threshold-based: <-2% = 100, etc.)
- Orderbook imbalance (from enriched data)

**What's missing:**
- No z-score normalization for funding (extreme detection)
- No short-term OI momentum (only 24h delta)
- No LS ratio shift (trader positioning change)
- No TP hit rate optimization

---

## Upgrade 1: Funding Z-Score (Crowding Squeeze Detection)

### Problem
Current scoring uses raw funding rate thresholds. A funding rate of 0.0004 scores 80 (good for SHORT) regardless of whether the 30-day mean is 0.0001 or 0.0005. This misses extreme funding regimes where squeezes are imminent.

### Feature
```python
funding_zscore = (current_funding - mean_30d) / std_30d
```

### Implementation
**File:** `pipeline/engine/adaptive_scorer.py` — `_score_structure_raw()`

1. Add `funding_zscore` parameter to `_score_structure_raw()`
2. Compute in `compute_dqs()` using enriched data:
   ```python
   funding_history = enriched_df["funding_rate"].iloc[-2880:]  # 30 days of 15m bars
   funding_mean = funding_history.mean()
   funding_std = funding_history.std()
   funding_zscore = (funding_rate - funding_mean) / funding_std if funding_std > 0 else 0
   ```
3. Add z-score modifier to funding score:
   ```python
   # Existing threshold score stays
   # Add z-score bonus/penalty
   if direction == "SHORT":
       if funding_zscore > 2.0:   fund_score += 10   # extreme positive = squeeze fuel
       elif funding_zscore > 1.0: fund_score += 5
       elif funding_zscore < -1.0: fund_score -= 10  # not extreme enough
   else:  # LONG
       if funding_zscore < -2.0:  fund_score += 10   # extreme negative = short squeeze fuel
       elif funding_zscore < -1.0: fund_score += 5
       elif funding_zscore > 1.0:  fund_score -= 10
   fund_score = max(0, min(100, fund_score))
   ```
4. Add config flag: `FUNDING_ZSCORE_ENABLED = True` in `portfolio_config.py`

### Backtest Plan
- Run control (no z-score) vs treatment (with z-score) for Jan-Jun 2026
- Compare PF, WR, expectancy, max DD
- **Gate:** PF must not decrease. Expectancy must increase by ≥5%.

### Risk
- 30-day lookback requires 2880 bars of enriched data. Early bars in backtest may have insufficient history. Use expanding window for first 30 days.
- Funding rate updates every 8 hours on Bybit. The 15m enriched data has the same value repeated 32 times. Z-score is effectively computed on ~90 unique funding values. This is fine — std will be meaningful.

---

## Upgrade 2: OI Momentum (Short-Term Positioning)

### Problem
Current `oi_delta_pct` uses a 24-hour lookback (96 bars of 15m). This is too slow. A sudden OI spike in the last 90 minutes (6 bars) signals new positions being opened, which is more actionable for 5-minute scan intervals.

### Feature
```python
oi_momentum_6 = (oi[-1] - oi[-6]) / oi[-6] * 100
```

### Implementation
**File:** `pipeline/engine/adaptive_scorer.py` — `_score_structure_raw()` and `_liquidity_bonus()`

1. Add `oi_momentum_short` parameter (6-bar change %)
2. Compute in `compute_dqs()`:
   ```python
   if len(enriched_df) >= 7:
       oi_6_ago = float(enriched_df.iloc[idx - 5]["open_interest"])
       oi_momentum_short = ((oi_now - oi_6_ago) / oi_6_ago) * 100 if oi_6_ago > 0 else 0
   ```
3. Use alongside existing 24h `oi_delta_pct`:
   ```python
   # Short-term OI momentum: sudden spike = new money entering
   if direction == "LONG":
       # Rising OI + rising price = new longs = momentum confirmation
       if oi_momentum_short > 1.0:  oi_score += 8
       elif oi_momentum_short < -1.0: oi_score -= 5  # longs unwinding
   else:  # SHORT
       if oi_momentum_short > 2.0:  oi_score += 8    # crowded longs
       elif oi_momentum_short < -1.0: oi_score += 5  # longs fleeing
   oi_score = max(0, min(100, oi_score))
   ```
4. Also feed into `_liquidity_bonus()` as a 4th alignment signal
5. Add config flag: `OI_MOMENTUM_ENABLED = True`

### Backtest Plan
- Run control vs treatment (with 6-bar OI momentum)
- Compare PF, WR, expectancy
- **Gate:** PF must not decrease. Expectancy must increase by ≥3%.

### Risk
- OI data from Bybit is updated every 15 minutes. The 6-bar lookback covers 90 minutes, which is meaningful for 5-minute scan intervals.
- OI can be stale (same value repeated) if Bybit API doesn't update. Add a check: if `oi_now == oi_6_ago`, set momentum to 0.

---

## Upgrade 3: LS Ratio Shift (Contrarian Positioning)

### Problem
The enriched data has `buyRatio` and `sellRatio` but they are not used in DQS scoring at all. Trader positioning shifts are a proven contrarian signal — when retail goes heavily long, shorts tend to win.

### Feature
```python
ls_ratio_shift = buyRatio[-1] - buyRatio[-6]
```

### Implementation
**File:** `pipeline/engine/adaptive_scorer.py` — `_score_structure_raw()` (new sub-component)

1. Add `ls_ratio_shift` parameter
2. Compute in `compute_dqs()`:
   ```python
   if len(enriched_df) >= 7:
       buy_ratio_now = float(enriched_df.iloc[idx]["buyRatio"])
       buy_ratio_6_ago = float(enriched_df.iloc[idx - 5]["buyRatio"])
       ls_ratio_shift = buy_ratio_now - buy_ratio_6_ago
   ```
3. Add to structure score as a contrarian modifier:
   ```python
   # LS ratio shift: contrarian signal
   # Positive shift = traders going long = bearish contrarian
   # Negative shift = traders going short = bullish contrarian
   if direction == "SHORT":
       if ls_ratio_shift > 0.05:   ls_score = 80   # traders piling long = short opportunity
       elif ls_ratio_shift > 0.02: ls_score = 60
       elif ls_ratio_shift > -0.02: ls_score = 40
       else: ls_score = 20  # traders already shorting
   else:  # LONG
       if ls_ratio_shift < -0.05:  ls_score = 80   # traders piling short = long opportunity
       elif ls_ratio_shift < -0.02: ls_score = 60
       elif ls_ratio_shift < 0.02:  ls_score = 40
       else: ls_score = 20  # traders already long
   ```
4. Blend into structure score (reduce existing weights slightly):
   ```python
   # Old: spread * 0.20 + depth * 0.20 + imb * 0.25 + fund * 0.20 + oi * 0.15
   # New: spread * 0.15 + depth * 0.15 + imb * 0.20 + fund * 0.20 + oi * 0.15 + ls * 0.15
   ```
5. Add config flag: `LS_RATIO_ENABLED = True`

### Backtest Plan
- Run control vs treatment (with LS ratio shift)
- Compare PF, WR, expectancy
- **Gate:** PF must not decrease. Expectancy must increase by ≥3%.

### Risk
- `buyRatio` / `sellRatio` may not be available for all symbols in historical data. Check data availability before enabling.
- LS ratio is a sentiment indicator, not a direct price driver. Keep weight low (15%) to avoid over-fitting.

---

## Upgrade 4: TP Hit Rate Optimization

### Problem
TP hit rate gate fails at 36.8% vs 40% threshold. Current TP multipliers are 3x-5x ATR depending on tier. These are too wide for the 5-minute scan interval.

### Solution
Reduce TP multipliers by 20% across all tiers. This increases TP hit rate at the cost of smaller wins per trade. PF should remain stable because more frequent wins compensate for smaller size.

### Implementation
**File:** `pipeline/engine/portfolio_config.py` — `RISK_TIERS`

1. Current:
   ```python
   "fair":    {"tp_mult": 3.0, ...}
   "good":    {"tp_mult": 4.0, ...}
   "strong":  {"tp_mult": 5.0, ...}
   ```
2. New (20% reduction):
   ```python
   "fair":    {"tp_mult": 2.4, ...}
   "good":    {"tp_mult": 3.2, ...}
   "strong":  {"tp_mult": 4.0, ...}
   ```
3. Keep SL multipliers unchanged (3.0x across all tiers)
4. This changes the risk/reward ratio:
   - Fair: RR 1.0 → 0.80
   - Good: RR 1.33 → 1.07
   - Strong: RR 1.67 → 1.33

### Backtest Plan
- Run control (current TP) vs treatment (tightened TP)
- Compare TP hit rate, PF, WR, expectancy, max DD
- **Gate:** TP hit rate ≥ 40%. PF must not decrease below 2.0.

### Risk
- Tighter TP means smaller wins. If PF drops below 2.0, revert.
- May increase trade churn (more TP hits = more re-entries). Monitor fee impact.

---

## Implementation Order

| Order | Upgrade | Effort | Risk | Expected Impact |
|---|---|---|---|---|
| 1 | TP hit rate optimization | 5 min (config change) | Low | Fix failing gate |
| 2 | Funding z-score | 30 min | Low | Better squeeze detection |
| 3 | OI momentum (6-bar) | 20 min | Low | Faster positioning signal |
| 4 | LS ratio shift | 30 min | Medium | Contrarian edge |

**Total effort:** ~1.5 hours implementation + 2-3 hours backtest runs

---

## Backtest Protocol

For each upgrade:
1. Run control backtest (no upgrade): `python3 backtest_runner.py --mode control --start 2026-01-01 --end 2026-06-30`
2. Run treatment backtest (with upgrade): `python3 backtest_runner.py --mode treatment_a_phase1 --start 2026-01-01 --end 2026-06-30`
3. Compare results in the backtest report
4. **Pass criteria:** PF does not decrease, expectancy increases by ≥3%, max DD stays below 15%
5. If pass: keep upgrade enabled, move to next
6. If fail: revert upgrade, document why, move to next

After all 4 upgrades are validated individually, run a combined backtest with all 4 enabled to check for interaction effects.

---

## Production Deployment Checklist

- [ ] All 4 upgrades pass individual backtests
- [ ] Combined backtest passes (all 4 enabled)
- [ ] All 6 validation gates pass (including TP hit rate ≥ 40%)
- [ ] Docker rebuild: `docker compose build`
- [ ] Start services: `docker compose up -d`
- [ ] Verify WebSocket service connects: `docker logs vlthr-bybit-ws`
- [ ] Verify executor in demo mode: place a test order on Bybit Demo
- [ ] Run pipeline for 1 hour in demo mode
- [ ] Check reconciliation: `SELECT * FROM paper_trades WHERE status = 'OPEN'` matches Bybit positions
- [ ] Monitor for 24 hours before switching to mainnet

---

## Do Not Touch

- Kelly V7 bands (DQS-tiered)
- MR boost 3.0x
- Calibration gate 0.40
- Crowding reduction 50%
- DQ gate threshold 60
- Max active trades 8
- Scan interval 300s
- Max leverage 4x
- Max portfolio risk 12%
