---
description: UCB construction workflow — Unified Calibration Benchmark with 4 layers (price trajectory labels, microstructure features, decision quality scores, feedback delta)
---

# UCB Construction Workflow

## When To Use
- Building or updating the Unified Calibration Benchmark
- Generating ground truth labels for direction accuracy
- Creating microstructure feature datasets
- Backfilling decision quality scores from historical trades

## 4 Layers

### Layer 1 — Price Trajectory Labels (Ground Truth)
**Purpose:** Determine the optimal side (LONG/SHORT/FLAT) for each historical bar over a variable hold period (1-12h).

**Steps:**
1. Load 15m OHLCV for all 6 symbols (3.5 years)
2. For each bar, compute forward price trajectory over 1-12h (4 to 48 bars)
3. Determine `optimal_side`: LONG if max favorable upside > threshold, SHORT if max favorable downside > threshold, FLAT if neither
4. Record `optimal_hold_hours`, `optimal_entry`, `optimal_exit`, `max_adverse_excursion`, `max_favorable_excursion`
5. Output: `data/ucb/layer1_labels.parquet`

**Distribution:** LONG 49.3%, SHORT 48.9%, FLAT 1.8% (735,714 total labels)

### Layer 2 — Microstructure Features
**Purpose:** Build enriched feature dataset aligned with UCB labels.

**Steps:**
1. Start with enriched 15m bars (technical indicators from confidence_engine)
2. Enrich with Binance futures metrics (LS ratio, OI, funding rate — 5min granularity)
3. Enrich with Chainticks liquidations (event-based, aggregate to 15m)
4. Compute 33+ features: RSI, ADX, EMA, ATR, volume ratios, funding z-score, etc.
5. Output: `data/ucb/layer2_microstructure.parquet`

**Coverage:** 736,002 bars, Binance metrics 14.2% coverage, liquidations 0.4% coverage

### Layer 3 — Decision Quality Scores
**Purpose:** Backfill historical trades with decision quality metrics using UCB labels as ground truth.

**Steps:**
1. Load V2 backtest trade history (IS + OOS)
2. Match each trade to UCB Layer 1 labels by timestamp
3. Compute per-trade metrics:
   - `feedback_delta`: signed deviation between predicted and realized outcome
   - `decision_efficiency`: trade PnL / optimal PnL
   - `regret`: optimal PnL - trade PnL
4. Aggregate: feedback_delta mean, decision efficiency, avg regret
5. Output: `data/ucb/layer3_decisions.parquet`

**Key Metrics (Pre-V7):**
- 9,403 decisions (7,143 IS + 2,260 OOS)
- 100% match rate to UCB labels
- Feedback delta: -0.052 (system is overconfident)
- Decision efficiency: 12.1% (trades capture only 12% of optimal PnL)
- Avg regret: $163.65

> **V7 Note:** V7 achieves 88/100 institutional score with 1,201 filtered trades, 54% WR, $13,401 PnL. The UCB layers remain the foundation for V7's isotonic calibration. The pre-V7 metrics above reflect the unfiltered decision pool; V7's calibration filter addresses the overconfidence issue.

### Layer 4 — Feedback Delta Capture (Live)
**Purpose:** Continuously capture feedback from live shadow decisions.

**Steps:**
1. Log each shadow decision with predicted confidence
2. When outcome resolves, compute `feedback_delta = realized - predicted`
3. Check: feedback_delta should be unbiased (mean ~ 0)
4. If biased: system is overconfident (negative delta) or underconfident (positive delta)

## Files
- `Backtest-Engine/build_ucb_labels.py` — Layer 1
- `Backtest-Engine/build_ucb_microstructure.py` — Layer 2
- `Backtest-Engine/build_ucb_decisions.py` — Layer 3
- `Backtest-Engine/enrich_ucb_metrics.py` — Binance metrics enrichment
- `Backtest-Engine/enrich_ucb_liquidations.py` — Liquidation enrichment
