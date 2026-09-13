# VLTHR 1-Year Backtest — Regime Feature Analysis

**Period:** 2025-07-01 → 2026-07-15  
**Mode:** control  
**Symbols:** BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT  
**Bars:** 36,385 per symbol  

## Headline Results

| Metric | Value |
|--------|-------|
| Total signals | 34,731 |
| Approved | 1,223 (3.5%) |
| Closed trades | 894 |
| Win rate | 32.8% |
| Profit factor | 2.20 |
| Expectancy | $106.66 |
| Max drawdown | 6.97% |
| Sharpe | 45.39 |
| Final balance | $105,354 (from $10K) |
| Total fees | $53,122 |
| Gross PnL | $148,477 |

## Validation Gates

| Gate | Threshold | Result | Status |
|------|-----------|--------|--------|
| Win rate | ≥30% | 32.8% | ✅ PASS |
| Profit factor | ≥1.5 | 2.20 | ✅ PASS |
| Expectancy | >0 | $106.66 | ✅ PASS |
| Max drawdown | <15% | 6.97% | ✅ PASS |
| Expired rate | <30% | 0.9% | ✅ PASS |
| TP hit rate | ≥40% | 29.9% | ❌ FAIL |

## Regime × Strategy Matrix

| Regime | Strategy | n | WR | PF | Exp |
|--------|----------|---|----|----|-----|
| MIXED | mean_reversion | 63 | 44.4% | 4.70 | $320.19 |
| MIXED | trend_following | 364 | 29.1% | 1.45 | $56.70 |
| TRENDING | mean_reversion | 81 | 35.8% | 3.45 | $188.86 |
| TRENDING | trend_following | 367 | 33.0% | 2.80 | $97.89 |
| VOLATILE | mean_reversion | 1 | 100% | inf | $892.78 |
| VOLATILE | trend_following | 15 | 53.3% | 15.61 | $239.33 |
| RANGING | trend_following | 2 | 0% | 0.00 | -$470.40 |

**Key insight:** mean_reversion dominates in every regime. VOLATILE regime has highest PF but very low sample (n=16). RANGING regime is a net loser — only 2 trades, both losses.

## Regime Transitions (Entry → Exit)

| Entry Regime | → MIXED | → RANGING | → TRENDING | → VOLATILE |
|-------------|---------|-----------|------------|------------|
| MIXED (427) | 4 | 44 | 290 | 89 |
| RANGING (2) | 0 | 0 | 1 | 1 |
| TRENDING (448) | 3 | 80 | 252 | 113 |
| VOLATILE (16) | 0 | 4 | 3 | 9 |

**Key insight:** 44% of MIXED entries transition to TRENDING by exit. 25% of TRENDING entries degrade to RANGING or VOLATILE by exit. This suggests regime instability during trade holding.

## HMM Confidence Bands

| Confidence | n | WR | PF | Exp |
|-----------|---|----|----|-----|
| <0.5 | 16 | 25.0% | 5.40 | $113.74 |
| 0.5-0.7 | 17 | 35.3% | 3.06 | $152.23 |
| 0.7-0.9 | 27 | 25.9% | 2.15 | $60.79 |
| >0.9 | 833 | 33.1% | 2.18 | $107.48 |

**Key insight:** HMM confidence is not predictive of trade outcome. 93% of trades have >0.9 confidence, making it a near-constant feature. Low confidence (<0.5) has surprisingly high PF (5.40) but only 16 samples.

## Efficiency Ratio Bands

| ER Band | n | WR | PF | Exp |
|---------|---|----|----|-----|
| <0.2 | 356 | 33.1% | 1.83 | $92.62 |
| 0.2-0.4 | 371 | 34.0% | 2.61 | $123.68 |
| 0.4-0.6 | 146 | 30.1% | 2.63 | $107.32 |
| 0.6-0.8 | 20 | 25.0% | 1.89 | $52.52 |

**Key insight:** ER 0.2-0.6 is the sweet spot (PF ~2.6). Very low ER (<0.2) has lower PF (1.83). High ER (>0.6) has poor performance but low sample.

## ATR Rank Bands (Volatility)

| ATR Rank | n | WR | PF | Exp |
|----------|---|----|----|-----|
| low (0-0.25) | 185 | 38.9% | 1.57 | $87.80 |
| med-low (0.25-0.5) | 167 | 35.3% | 1.78 | $90.04 |
| med-high (0.5-0.75) | 212 | 28.3% | 2.00 | $84.04 |
| high (0.75-1.0) | 329 | 31.0% | 4.42 | $141.29 |

**Key insight:** High volatility (ATR rank >0.75) has the best PF (4.42) and expectancy ($141). Low volatility has higher win rate but much lower PF. This suggests TP/SL ratio is more favorable in volatile conditions.

## DQS Band × Direction

| DQS | Direction | n | WR | PF | Exp |
|-----|-----------|---|----|----|-----|
| 50-59 | LONG | 323 | 30.7% | 2.13 | $78.62 |
| 50-59 | SHORT | 12 | 58.3% | 7.27 | $739.86 |
| 60-69 | LONG | 508 | 33.1% | 2.03 | $98.31 |
| 60-69 | SHORT | 2 | 100% | inf | $917.34 |
| 70-79 | LONG | 31 | 32.3% | 3.35 | $230.64 |
| 70-79 | SHORT | 2 | 50.0% | 5.16 | $91.00 |
| 80+ | LONG | 15 | 40.0% | 1.57 | $146.59 |

**Key insight:** SHORT signals are extremely rare (16 out of 894) but highly profitable when they occur (PF 7.27 at DQS 50-59). The pipeline is overwhelmingly LONG-biased. DQS 70-79 LONG has best PF among LONGs.

## Pattern Impact on Trade Outcome

| Pattern | n | WR | PF | Exp | vs Baseline |
|---------|---|----|----|-----|-------------|
| pat_trend_break | 43 | 41.9% | 3.25 | $207.03 | +94% exp |
| pat_wedge | 12 | 33.3% | 2.97 | $155.88 | +46% exp |
| pat_choch | 45 | 42.2% | 2.95 | $132.18 | +24% exp |
| pat_near_support | 40 | 37.5% | 2.71 | $132.92 | +25% exp |
| pat_bos | 81 | 34.6% | 2.23 | $106.55 | baseline |
| pat_rising_support | 121 | 23.1% | 1.95 | $69.85 | -34% exp |
| pat_channel | 38 | 26.3% | 1.32 | $39.57 | -63% exp |
| pat_near_resistance | 38 | 28.9% | 1.28 | $37.61 | -65% exp |
| (no pattern) | 850 | 32.4% | 2.15 | $101.97 | baseline |

**Key insight:** 
- **Best patterns:** trend_break (PF 3.25, +94% expectancy), wedge (PF 2.97), choch (PF 2.95)
- **Worst patterns:** near_resistance (PF 1.28), channel (PF 1.32), rising_support (PF 1.95)
- near_resistance and channel signals are **net detractors** — they underperform no-pattern baseline significantly

## Session × Regime

| Session | Regime | n | WR | PF | Exp |
|---------|--------|---|----|----|-----|
| ny_open | TRENDING | 101 | 40.6% | 2.62 | $153.26 |
| ny_open | MIXED | 76 | 39.5% | 1.95 | $145.45 |
| london | TRENDING | 47 | 34.0% | 2.21 | $137.54 |
| asian | MIXED | 156 | 34.6% | 2.17 | $120.20 |
| asian | TRENDING | 167 | 32.9% | 3.00 | $92.98 |
| ny_late | TRENDING | 133 | 28.6% | 4.81 | $103.41 |
| ny_late | MIXED | 122 | 21.3% | 1.53 | $49.21 |
| london | MIXED | 73 | 32.9% | 1.39 | $68.54 |

**Key insight:** ny_open is the best session (WR 40%+, PF 2.62 in TRENDING). ny_late × MIXED is the worst combination (WR 21.3%, PF 1.53). london × MIXED also weak (PF 1.39).

## Symbol × Regime

| Symbol | Regime | n | WR | PF | Exp |
|--------|--------|---|----|----|-----|
| BNBUSDT | TRENDING | 13 | 61.5% | 7.52 | $710.89 |
| SOLUSDT | VOLATILE | 8 | 62.5% | 22.63 | $249.35 |
| BTCUSDT | VOLATILE | 7 | 42.9% | 11.39 | $227.89 |
| XRPUSDT | MIXED | 60 | 43.3% | 4.42 | $308.64 |
| XRPUSDT | TRENDING | 68 | 30.9% | 2.25 | $89.06 |
| BTCUSDT | TRENDING | 167 | 37.7% | 2.60 | $105.38 |
| SOLUSDT | TRENDING | 200 | 29.0% | 3.04 | $91.64 |
| SOLUSDT | MIXED | 177 | 29.4% | 1.60 | $69.53 |
| BTCUSDT | MIXED | 187 | 28.9% | 1.32 | $44.56 |

**Key insight:** BTCUSDT × MIXED is the worst performer (PF 1.32, n=187). BNBUSDT is excellent but low frequency (n=16 total). SOLUSDT has highest volume but mediocre PF in MIXED regime.

## Feature Correlations with PnL

| Feature | Correlation (r) |
|---------|-----------------|
| rsi_entry | -0.1102 (strongest) |
| kelly_fraction | +0.0665 |
| atr_rank_entry | +0.0448 |
| atr_pct_entry | +0.0424 |
| tp_dist_atr | +0.0363 |
| dqs_score | +0.0401 |
| adx_entry | -0.0291 |
| open_risk_at_entry | -0.0244 |
| efficiency_ratio_entry | -0.0140 |
| hmm_confidence | -0.0108 |

**Key insight:** No single feature has strong correlation with PnL. RSI at entry has the strongest (negative) correlation — lower RSI entries tend to be more profitable. This aligns with mean_reversion outperformance.

## Monthly Performance

| Month | n | WR | PnL | Exp |
|-------|---|----|-----|-----|
| 2025-07 | 141 | 39.0% | $14,120 | $100.15 |
| 2025-08 | 99 | 35.4% | $14,781 | $149.31 |
| 2025-09 | 89 | 40.4% | $21,261 | $238.89 |
| 2025-10 | 77 | 31.2% | $9,687 | $125.81 |
| 2025-11 | 51 | 23.5% | $3,411 | $66.90 |
| 2025-12 | 72 | 29.2% | $3,474 | $48.25 |
| 2026-01 | 66 | 21.2% | -$1,006 | -$15.25 |
| 2026-02 | 51 | 37.3% | $5,810 | $113.94 |
| 2026-03 | 62 | 37.1% | $10,266 | $165.58 |
| 2026-04 | 68 | 35.3% | $2,760 | $40.60 |
| 2026-05 | 51 | 25.5% | $2,107 | $41.33 |
| 2026-06 | 49 | 22.4% | $4,678 | $95.48 |
| 2026-07 | 17 | 35.3% | $4,224 | $248.51 |

**Key insight:** Only 1 losing month (Jan 2026, -$1K). Best months: Sep 2025 ($21K), Aug 2025 ($15K), Jul 2025 ($14K). Performance degrades in Nov-Feb (low volatility period). Recovery in Feb-Mar 2026.

## Investigation 1: SHORT Signal Scarcity

**Problem:** Only 16 SHORT trades closed (1.8% of 894) despite SHORTs having PF 7.27.

### Root Cause Analysis

Traced signal flow: scorer → V2 filters → logging. Three distinct blockers found:

#### Blocker 1: trend_following SHORTs 100% blocked by V2 daily bias filter
- **Symbols:** BTCUSDT, SOLUSDT (both trend_following)
- **V2 filter:** `if side == "SHORT" and daily_bias == "BULL": return False` (v2_filters.py:208)
- **Evidence:** BTCUSDT — 1,010 SHORTs generated by scorer, 683 passed DQS threshold, ALL 683 rejected by daily bias BULL. SOLUSDT — 504 SHORTs generated, 350 passed DQS, ALL 350 rejected by daily bias BULL.
- **Root cause:** BTC/SOL were in a bull market for most of the backtest period. The daily bias filter correctly blocks counter-trend SHORTs for trend_following strategy.
- **Verdict:** Working as designed. trend_following should not short in bull markets.

#### Blocker 2: XRPUSDT SHORTs blocked by V2 daily RSI check
- **Symbol:** XRPUSDT (mean_reversion, rsi_overbought=70)
- **V2 filter:** `if side == "SHORT" and daily_bias == "BULL" and rsi_daily < 70: return False` (v2_filters.py:211)
- **Evidence:** 15m RSI reaches 70 in 3,423 bars (triggering SHORT direction), but daily RSI rarely reaches 70 simultaneously. V2 filter blocks all SHORTs when daily bias is BULL and daily RSI < 70.
- **Root cause:** rsi_overbought=70 is too high for 15m, and the V2 daily RSI check adds a second, stricter gate.
- **Fix candidate:** Lower rsi_overbought from 70 to 65 (matching ETHUSDT). This would generate more SHORT signals, but V2 filter would still block them unless daily RSI is also >= 65.

#### Blocker 3: BNBUSDT LONGs blocked by V2 daily RSI check
- **Symbol:** BNBUSDT (mean_reversion, rsi_oversold=30)
- **V2 filter:** `if side == "LONG" and daily_bias == "BEAR" and rsi_daily > 30: return False` (v2_filters.py:213)
- **Evidence:** 15m RSI <= 30 in 3,471 bars (triggering LONG direction), but daily bias is BEAR and daily RSI is 53 (> 30). All 95 LONGs that passed DQS were rejected by V2.
- **Root cause:** BNB daily bias was BEAR for most of the period, and daily RSI stayed above 30. The V2 filter blocks MR LONGs when daily trend is bearish and daily RSI isn't oversold.
- **Result:** BNB only generates SHORTs (693 signals, all SHORT), making it the only symbol with SHORT-only exposure.

### Directional Imbalance Summary

| Symbol | Strategy | LONGs | SHORTs | Primary Blocker |
|--------|----------|-------|--------|-----------------|
| BTCUSDT | trend_following | 11,266 | 0 | V2 daily bias BULL |
| SOLUSDT | trend_following | (all) | 0 | V2 daily bias BULL |
| ETHUSDT | mean_reversion | 5,471 | 5,704 | None (balanced, rsi_overbought=65) |
| XRPUSDT | mean_reversion | 8,073 | 0 | V2 daily RSI < 70 |
| BNBUSDT | mean_reversion | 0 | 693 | V2 daily RSI > 30 (blocks LONGs) |

### Key Intelligence for Live Trader

1. **The pipeline is structurally LONG-biased in bull markets.** 4 of 5 symbols generate zero SHORTs. Only ETHUSDT has balanced direction (47% SHORT).
2. **V2 daily bias filter is the primary gate.** It overrides the scorer's direction logic using daily-timeframe confirmation. This is intelligent (prevents counter-trend signals) but creates directional concentration risk.
3. **ETHUSDT is the only balanced symbol** because rsi_overbought=65 is low enough for 15m RSI to trigger SHORTs, and the V2 daily RSI check is more likely to pass (daily RSI reaches 65 more often than 70).
4. **BNBUSDT is the only SHORT-only symbol** — it acts as a partial hedge during bear periods but has very low signal volume (693 vs 10K+ for others).
5. **Lowering XRP rsi_overbought from 70 to 65** would generate more SHORT signals, but the V2 daily RSI check would still filter most of them. The V2 filter is the binding constraint, not the scorer threshold.
6. **SHORT signals that DO survive (16 closed trades) have PF 7.27** — extremely profitable. The V2 filter is high-precision but extremely low-recall.

## Investigation 2: Pattern-Based Filtering

**Problem:** pat_near_resistance (PF 1.28) and pat_channel (PF 1.32) underperform no-pattern baseline (PF 2.15) by 60%+.

### Current State
Pattern fields (`pat_*`) are **not used by the scorer or V2 filters** — they're purely logged in the backtest CSV. Adding pattern-based filtering would require new logic in the scorer or V2 filters.

### Statistical Significance

| Pattern | n | Exp (pattern) | Exp (no pattern) | p-value | Significant? |
|---------|---|---------------|-------------------|---------|-------------|
| pat_trend_break | 43 | $207.03 | $101.97 | 0.1427 | No |
| pat_choch | 45 | $132.18 | $105.70 | 0.4409 | No |
| pat_near_support | 40 | $132.92 | $105.82 | 0.5435 | No |
| pat_bos | 81 | $106.55 | $107.08 | 0.6878 | No |
| pat_wedge | 12 | $155.88 | $106.37 | 0.9286 | No |
| pat_rising_support | 121 | $69.85 | $112.86 | 0.1634 | No |
| pat_channel | 38 | $39.57 | $110.03 | 0.3820 | No |
| pat_near_resistance | 38 | $37.61 | $110.12 | 0.7998 | No |

**No pattern reaches statistical significance** (all p > 0.1). Sample sizes (12-121) are too small for confident conclusions. However, directional consistency across regimes and directions provides actionable signal.

### Pattern × Regime Breakdown

| Pattern | Regime | n | WR | PF | Exp |
|---------|--------|---|----|----|-----|
| trend_break | MIXED | 23 | 47.8% | 8.60 | $340.52 |
| trend_break | TRENDING | 19 | 36.8% | 1.48 | $68.02 |
| choch | TRENDING | 21 | 42.9% | 3.56 | $132.71 |
| choch | MIXED | 23 | 43.5% | 2.68 | $139.59 |
| near_resistance | MIXED | 28 | 28.6% | 1.14 | $21.44 |
| near_resistance | TRENDING | 8 | 37.5% | 2.34 | $111.57 |
| channel | MIXED | 17 | 23.5% | 0.93 | -$13.96 |
| channel | TRENDING | 21 | 28.6% | 2.23 | $82.90 |
| rising_support | TRENDING | 77 | 26.0% | 1.93 | $67.69 |
| rising_support | MIXED | 44 | 18.2% | 1.98 | $73.62 |

**Key insight:** trend_break excels in MIXED regime (PF 8.60) but is mediocre in TRENDING (PF 1.48). channel in MIXED is a net loser (PF 0.93, exp -$14).

### Pattern Combinations (n>=5)

| Combination | n | WR | PF | Exp |
|-------------|---|----|----|-----|
| trend_break + wedge | 5 | 20.0% | 8.67 | $285.46 |
| choch + near_support | 7 | 57.1% | 3.17 | $179.62 |
| wedge + channel | 5 | 40.0% | 2.48 | $217.29 |
| trend_break + bos | 15 | 26.7% | 1.99 | $78.65 |
| rising_support + channel | 23 | 17.4% | 1.03 | $2.72 |
| bos + channel | 6 | 33.3% | 0.97 | -$3.78 |
| bos + rising_support + channel | 5 | 20.0% | 0.59 | -$72.30 |
| bos + rising_support | 18 | 11.1% | 0.37 | -$82.59 |
| trend_break + bos + rising_support | 7 | 0.0% | 0.00 | -$149.75 |

**Key insight:** bos + rising_support is a strong net loser (PF 0.37, n=18, exp -$82.59). This combination has 11.1% win rate — almost all trades lose.

### Key Intelligence for Live Trader

1. **Patterns are NOT used in scoring or filtering.** They're purely informational. Adding pattern-based DQS modifiers is a potential enhancement.
2. **No statistical significance** — sample sizes too small (12-121). Any pattern filter should be a soft DQS modifier, not a hard reject.
3. **Best patterns:** trend_break (+103% exp), wedge (+47%), choch (+24%). trend_break in MIXED regime is exceptional (PF 8.60).
4. **Worst patterns:** near_resistance (-66% exp), channel (-64% exp), rising_support (-38% exp).
5. **Dangerous combination:** bos + rising_support (PF 0.37, exp -$82.59) — should be penalized or filtered.
6. **Pattern impact is regime-dependent:** trend_break is excellent in MIXED but mediocre in TRENDING. channel is a net loser in MIXED but acceptable in TRENDING.
7. **Recommendation:** If patterns are added to scoring, use as DQS bonus/penalty:
   - +5 DQS for trend_break in MIXED regime
   - +3 DQS for choch
   - -5 DQS for near_resistance
   - -5 DQS for channel in MIXED regime
   - -10 DQS for bos + rising_support combination

## Investigation 3: Regime-Aware Allocation

**Problem:** mean_reversion (PF 4.1) outperforms trend_following (PF 1.87) but gets fewer trades (145 vs 748). BTCUSDT × MIXED (PF 1.32, n=187) drags portfolio.

### Current Architecture

- **Strategy is fixed per symbol** in `portfolio_config.py`: BTCUSDT/SOLUSDT = trend_following, ETHUSDT/XRPUSDT/BNBUSDT = mean_reversion
- **V2 regime override is DISABLED** (`REGIME_STRATEGY_OVERRIDE_ENABLED = False`). The V2 filter has code to switch strategy based on HMM regime (trending→TF, ranging→MR), but it's turned off.
- **MR gets 3x Kelly boost** (`MR_BOOST = 3.0`): MR kelly bands are [0.9, 2.1, 3.0, 4.5, 7.5] vs TF bands [0.3, 0.7, 1.0, 1.5, 2.5]. MR deploys 3.8x more capital per trade on average.
- **Kelly allocation efficiency:** MR generates $172.66 PnL per kelly unit vs TF $207.56 per kelly unit. TF is actually more capital-efficient despite lower PF, because MR's 3x boost means it deploys more capital per trade.

### Strategy × Regime × Symbol Matrix

| Strategy | Regime | Symbol | n | WR | PF | Exp |
|----------|--------|--------|---|----|----|-----|
| MR | MIXED | XRPUSDT | 60 | 43.3% | 4.42 | $308.64 |
| MR | TRENDING | XRPUSDT | 68 | 30.9% | 2.25 | $89.06 |
| MR | TRENDING | BNBUSDT | 13 | 61.5% | 7.52 | $710.89 |
| TF | MIXED | SOLUSDT | 177 | 29.4% | 1.60 | $69.53 |
| TF | MIXED | BTCUSDT | 187 | 28.9% | 1.32 | $44.56 |
| TF | TRENDING | SOLUSDT | 200 | 29.0% | 3.04 | $91.64 |
| TF | TRENDING | BTCUSDT | 167 | 37.7% | 2.60 | $105.38 |

**Key insight:** TF in MIXED regime is consistently weak across both BTC (PF 1.32) and SOL (PF 1.60). TF in TRENDING is acceptable (PF 2.60-3.04). MR in MIXED is exceptional (PF 4.42 for XRP).

### Session × Strategy

| Session | Strategy | n | WR | PF | Exp |
|---------|----------|---|----|----|-----|
| ny_open | MR | 31 | 54.8% | 8.06 | $503.82 |
| ny_late | MR | 40 | 40.0% | 6.47 | $264.43 |
| london | MR | 28 | 35.7% | 2.12 | $185.70 |
| asian | MR | 46 | 32.6% | 2.69 | $107.98 |
| ny_open | TF | 152 | 37.5% | 1.65 | $82.34 |
| asian | TF | 280 | 34.3% | 2.44 | $107.66 |
| ny_late | TF | 217 | 22.6% | 1.78 | $46.39 |
| london | TF | 99 | 33.3% | 1.45 | $65.55 |

**Key insight:** MR dominates in every session. ny_open × MR is exceptional (PF 8.06, WR 54.8%). ny_late × TF is the worst (PF 1.78, WR 22.6%).

### DQS Threshold Sweep: TF in MIXED

| DQS Threshold | n | WR | PF | Exp | Total PnL |
|-------------|---|----|----|-----|-----------|
| >= 50 (current) | 364 | 29.1% | 1.45 | $56.70 | $20,640 |
| >= 55 | 315 | 29.5% | 1.50 | $63.44 | $19,984 |
| >= 60 | 277 | 30.0% | 1.53 | $67.98 | $18,831 |
| >= 65 | 54 | 31.5% | 1.78 | $125.24 | $6,763 |
| >= 70 | 25 | 40.0% | 2.87 | $226.63 | $5,666 |

**Key insight:** Raising DQS threshold for TF in MIXED from 50 to 65 would cut 310 trades but improve PF from 1.45 to 1.78. However, total PnL drops from $20.6K to $6.8K — the filtered-out trades are still net positive. Raising to 70 improves PF to 2.87 but keeps only 25 trades.

### Portfolio Counterfactuals

| Scenario | n | PnL | PF | ΔPnL |
|----------|---|-----|----|------|
| Baseline | 893 | $95,578 | 2.21 | — |
| Block ny_late × MIXED | 771 | $89,574 | 2.32 | -$6,004 |
| TF MIXED DQS >= 65 | 553 | $75,910 | 2.93 | -$19,668 |
| TF MIXED DQS >= 70 | 554 | $80,604 | 3.24 | -$14,974 |
| Block BTC × MIXED | 521 | $71,803 | 3.16 | -$23,775 |
| Block SOL × MIXED | 512 | $69,756 | 3.21 | -$25,822 |

**Critical finding:** All filtering scenarios REDUCE total PnL despite improving PF. The "bad" trades (TF in MIXED, BTC × MIXED) are still net positive — they just have lower PF. Filtering them improves quality but reduces total profit.

### Key Intelligence for Live Trader

1. **REGIME_STRATEGY_OVERRIDE is DISABLED.** The V2 filter has regime-based strategy switching code but it's off. Enabling it would route MIXED regime → mean_reversion, which could significantly improve performance.
2. **MR gets 3x Kelly boost but TF is more capital-efficient** ($207.56/kelly vs $172.66/kelly). The boost compensates for MR having fewer signals, not to make MR more profitable per dollar.
3. **TF in MIXED is weak but still profitable.** Filtering it improves PF (1.45→2.87) but reduces total PnL by $14-20K. The live trader should consider whether PF or total PnL is the priority.
4. **ny_late × MIXED is the worst session-regime combo** (PF 1.53, WR 21.3%). Blocking it improves PF from 2.21 to 2.32 but loses $6K PnL.
5. **DQS threshold of 65 for TF in MIXED** is the sweet spot — PF jumps from 1.45 to 1.78 while retaining $6.8K PnL. This is a soft filter, not a hard block.
6. **Enabling V2 regime override** (MIXED→MR) would be the highest-impact change, but requires backtest validation since BTC/SOL would switch from TF to MR in MIXED regime, changing their entire signal generation logic.
7. **MR in ny_open is exceptional** (PF 8.06, WR 54.8%). The live trader should ensure MR signals in ny_open session get maximum Kelly allocation.

## Investigation 4: TP/SL Ratio Tuning

**Problem:** TP hit rate is 29.9% (267/894), failing the 40% gate threshold. SL hits dominate at 66.6%.

### Root Cause: Dynamic SL Time Decay

The dynamic SL system (`backtest_runner.py:1025-1070`) has three rules:

1. **Break-even stop** (line 1039-1044): When PnL > 75% of TP distance, SL moves to entry ± 0.3%. Protective, not problematic.
2. **Time decay** (line 1046-1056): After 3 hours, SL tightens by **0.25 ATR per hour**. This is the primary culprit.
3. **ATR expansion** (line 1058-1068): Widens SL when volatility spikes. Rarely triggers.

**Impact of time decay:** With `max_trade_hours` of 12-48, trades held 10+ hours lose 1.75+ ATR of SL distance. The effective SL is **1.51 ATR** vs configured 2.88 ATR. 84.5% of SL-hit trades have MAE < configured SL distance, confirming the time decay is stopping them out prematurely.

### Effective vs Configured TP/SL

| Metric | Configured | Effective (actual) |
|--------|-----------|-------------------|
| TP distance | 4.0 ATR | 4.85 ATR (MFE for TP hits) |
| SL distance | 2.88 ATR | 1.51 ATR (MAE for SL hits) |
| TP/SL ratio | 1.39 | 3.22 |

The effective ratio is 3.22:1, which is actually very favorable. The problem is that the tight effective SL (1.51 ATR) stops trades out before they can reach the 4.0 ATR TP.

### MFE Distribution (all closed trades)

| MFE Threshold | Trades | % of total |
|--------------|--------|-----------|
| >= 2.0 ATR | 479 | 53.6% |
| >= 2.5 ATR | 413 | 46.2% |
| >= 3.0 ATR | 356 | 39.9% |
| >= 3.5 ATR | 310 | 34.7% |
| >= 4.0 ATR | 264 | 29.6% |

MFE >= 4.0 ATR (29.6%) closely matches actual TP hit rate (29.9%), confirming TP is at ~4.0 ATR.

### Per-Symbol TP Distance for 40% Hit Rate

| Symbol | Current TP | TP for 40% hit | Current TP Rate | SL Mult |
|--------|-----------|---------------|-----------------|---------|
| XRPUSDT | 4.0 | 3.25 | 35.5% | 2.5 |
| SOLUSDT | 4.0 | 2.75 | 27.9% | 2.5 |
| BTCUSDT | 4.0 | 3.25 | 31.5% | 2.5 |
| BNBUSDT | 4.0 | 4.50+ | 56.2% | 2.0 |

BNBUSDT already exceeds 40% (56.2%). SOLUSDT needs the most aggressive TP tightening (2.75 ATR).

### Per-Strategy TP Hit Rate

| Strategy | TP Rate | TP MFE | SL MAE | Effective SL |
|----------|---------|--------|--------|-------------|
| mean_reversion | 38.0% | 4.69 | 1.51 | 1.51 |
| trend_following | 29.7% | 4.89 | 1.51 | 1.51 |

MR is close to the 40% gate (38.0%). TF is the primary drag at 29.7%.

### SL-Hit Trade Analysis

- 30.9% of SL-hit trades had MFE >= 2.0 ATR (went halfway to TP before reversing)
- 11.8% had MFE >= 3.0 ATR (went 75% of the way to TP before reversing)
- 8.9% had MFE >= 80% of TP distance (nearly hit TP before reversing)
- Median MFE for SL-hit trades: 1.42 ATR (35% of TP distance)
- Median bars to MFE: 6 (price went favorable early, then reversed)

### Key Intelligence for Live Trader

1. **Dynamic SL time decay is the root cause.** The 0.25 ATR/hour tightening after hour 3 reduces effective SL from 2.88 to 1.51 ATR. This stops out 84.5% of losing trades before they reach the configured SL distance.
2. **Two approaches to fix the TP hit rate gate:**
   - **Option A: Tighten TP** from 4.0 to 3.0-3.25 ATR. This would achieve ~40% hit rate but reduce per-trade profit by 20-25%. PnL impact: TP wins shrink from $628 avg to ~$471-510 avg, but more trades hit TP.
   - **Option B: Loosen time decay** from 0.25 to 0.15 ATR/hour. This lets trades breathe longer, giving them more time to reach TP. Effective SL would be ~2.0 ATR instead of 1.51.
3. **Option B is likely better** because it preserves the 4.0 ATR TP distance (larger wins) while reducing premature stop-outs. The tradeoff is slightly larger losses on SL hits.
4. **SOLUSDT is the worst performer** (27.9% TP rate, needs TP at 2.75 ATR for 40%). It has the most SL hits (269) and lowest TP hit rate. Consider symbol-specific TP tuning.
5. **BNBUSDT already passes** at 56.2% TP hit rate. No changes needed.
6. **MR is close to passing** (38.0%). A small TP tightening to 3.75 ATR or slight time decay loosening would push it over 40%.
7. **The break-even stop (rule 1) is working well.** 8.9% of SL-hit trades nearly reached TP (80%+ MFE), but the break-even stop protected most of these by moving SL to entry. Without it, losses would be larger.
8. **Exit order matters:** The backtest checks SL before TP (conservative). If both are hit in the same bar, SL is counted. This slightly depresses TP hit rate. The live trader should verify this matches exchange behavior (Bybit fills orders sequentially, not simultaneously).

## Actionable Findings

1. **mean_reversion should get higher allocation** — PF 4.1 vs trend_following 1.87, consistently across regimes
2. **SHORT signals are underutilized** — only 16 SHORTs vs 878 LONGs, but SHORTs have PF 7.27
3. **Filter out near_resistance and channel patterns** — both underperform no-pattern baseline by 60%+
4. **Consider reducing BTCUSDT × MIXED exposure** — 187 trades at PF 1.32 drags portfolio
5. **ny_late × MIXED should be filtered** — WR 21.3%, PF 1.53, worst session-regime combo
6. **High ATR rank is favorable** — PF 4.42 at ATR rank >0.75 vs PF 1.57 at low ATR
7. **TP hit rate (29.9%) is the only failing gate** — SL hits (66.6%) dominate, suggesting TP distances may be too far relative to SL
8. **HMM confidence is not useful** — 93% of trades have >0.9 confidence, no discriminative power
