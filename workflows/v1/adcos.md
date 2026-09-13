---
description: ADCOS — Adaptive Decision Control Operating System for full-pipeline orchestration and decision-system validation
---

# ADCOS Workflow: Adaptive Decision Control Operating System

## When To Use
- Orchestrating the full VLTHR pipeline from sensors to learning
- Validating decision quality across the entire system (not just trade outcomes)
- Replaying history through every node to measure decision quality at each stage
- Setting up continuous calibration loops

## Philosophy
ADCOS optimizes around "Was every decision in the pipeline the highest-quality decision available given the information at that point in time?" — NOT "Did the trade make money?"

## Pipeline Stages (Replay History Through Every Node)

1. **Sensors** — Data ingestion (Bybit OHLCV, Binance metrics, Chainticks liquidations)
   - Validate: timestamps monotonic, no gaps, OHLC relationships hold
   - Measure: uptime, latency, gap rate per source

2. **Interpretation** — Feature computation (indicators, microstructure, context)
   - Validate: no look-ahead bias (timestamp alignment for multi-TF merges)
   - Measure: NaN rate, feature drift, warmup period coverage

3. **Validation** — Data quality gates, calibration checks
   - Validate: calibration curve refit schedule, versioning
   - Measure: calibration error, Brier score

4. **Forecast** — Direction prediction, regime detection
   - Validate: direction accuracy vs UCB optimal_side (>52% required)
   - Measure: IC, Wilson lower bound, direction accuracy per symbol/regime

5. **Scenario Generation** — Regime classification, robustness scoring
   - Validate: IS-OOS robustness correlation (>0.8)
   - Measure: regime accuracy, consistency across regimes

6. **Objectives** — Strategy selection (trend_following vs mean_reversion)
   - Validate: strategy-regime alignment
   - Measure: per-strategy win rate, expectancy, risk share

7. **Decision Generation** — DQS scoring, side determination, ABSTAIN gate
   - Validate: DQS calibration, no leaked features
   - Measure: DQS distribution, direction accuracy by DQS bucket

8. **Decision Tournament** — DRVE pairwise verification, ripple stability
   - Validate: tournament PnL improvement over baseline
   - Measure: AUC, pairwise win rate, reserve decision value

9. **Capital Allocation** — DQRAE Kelly sizing, risk budget
   - Validate: Kelly multipliers by DQS bucket, negative Kelly veto
   - Measure: risk-weighted return, drawdown, Sharpe

10. **Execution Simulation** — SL/TP, costs, slippage
    - Validate: realistic cost model (fees + slippage + funding)
    - Measure: SL hit rate, TP hit rate, time exit rate, cost as % of gross

11. **Outcome** — Trade resolution, PnL attribution
    - Validate: trade logging completeness
    - Measure: win rate, expectancy, profit factor, total PnL

12. **Learning** — Feedback delta, calibration update
    - Validate: feedback_delta unbiased, calibration curve improving
    - Measure: feedback_delta, decision efficiency, avg regret

13. **Calibration** — Refit models, update thresholds
    - Validate: schedule adherence (weekly for decision curve, monthly for DQRAE)
    - Measure: calibration error trend, placeholder replacement status

14. **Repeat** — Continuous stream, not batch

## Acceptance Gates (Sequential)

| Gate | Criteria | Blocks |
|---|---|---|
| G1 | Direction accuracy > 52% vs UCB optimal | Capital deployment |
| G4 | No label leakage in features | All downstream phases |
| G5 | IS-OOS robustness > 0.80 | Scenario-dependent decisions |
| G8 | Calibration error < 0.10 | Auto-execution |
| G9 | Live-shadow parity within ±15% | Live capital |

## Placeholder Registry
Every borrowed prior must be registered until replaced with real data:
- DQRAE AUTOEXEC_CONFIDENCE_FLOOR: 0.90 (borrowed from clinical AI)
- DQRAE DUAL_AUTH_BAND: [0.70, 0.90) (borrowed)
- DQRAE VETO_BELOW: 0.70 (borrowed)
- Kelly multipliers: pending live shadow validation
- Regime thresholds: pending empirical distribution from UCB

## Output
- Per-node health scorecard
- Decision quality audit trail
- Calibration version log
- Gate status dashboard
