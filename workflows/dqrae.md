---
description: DQRAE — Decision Quantification & Risk Allocation Engine for quality-first decision measurement and Kelly-based risk sizing
---

# DQRAE Workflow: Decision Quantification & Risk Allocation Engine

## When To Use
- Calibrating risk weights for decision batching systems
- Determining position sizing (Kelly multipliers) per DQS bucket
- Measuring decision quality (feedback delta, decision efficiency, regret)
- Replacing count-based risk allocation with magnitude-based allocation
- Setting veto thresholds (DQS 85+ negative Kelly veto)

## Core Principles

1. **A decision is a tuple, not an event.** Every decision carries `(node_id, magnitude, confidence, feedback_delta, latency, tier)`
2. **Quantity is a health signal, not a risk signal.** Decision count is for throughput monitoring, NOT risk allocation
3. **Risk is allocated by expected loss.** `risk_weight = magnitude * (1 - confidence)`
4. **Feedback must be a delta.** Signed deviation between expected and realized outcome
5. **Comparability requires tiering.** CAPITAL / STRUCTURAL / INFORMATIONAL tiers
6. **Calibration precedes automation.** No auto-execute above magnitude threshold without validated calibration curve

## Decision Tuple
```
Decision = {
  node_id:        string
  timestamp:      ISO8601
  tier:           enum { CAPITAL, STRUCTURAL, INFORMATIONAL }
  magnitude:      float  [0.0 - 1.0]
  confidence:     float  [0.0 - 1.0]
  action:         enum { EXECUTE, ESCALATE, DEFER, VETO }
  feedback_delta: float | null
  latency_ms:     int
  dual_auth_flag: bool
}
```

## Calibration Steps (B1 + B2)

### B1 — Risk Weight Formula
1. Load UCB Layer 3 decisions (9,403 historical decisions)
2. Compute `risk_weight = magnitude * (1 - confidence)` for each
3. Correlate with realized loss (best formula: `regret_based`, corr = 0.211)
4. Compute Kelly fraction per DQS bucket
5. Output: `results/dqrae_b1_risk_calibration.json`

### B2 — OOS Risk Validation
1. Split decisions IS/OOS
2. Validate Wilson lower bounds are conservative on OOS
3. Compute node risk shares (trend_following vs mean_reversion)
4. Output: `results/dqrae_b2_oos_validation.json`

## Key Findings (V7 Calibrated — GOLD STANDARD)

| DQS Bucket | Kelly | Action |
|---|---|---|
| 85+ | Negative | **VETO** — do not trade (overconfident) |
| 80-85 | Best | **2.5x position size** |
| 75-80 | Good | **1.5x position size** |
| 70-75 | Moderate | **1.0x position size** |
| 65-70 | Weak | **0.7x position size** |
| <65 | Minimal | **0.3x position size** |

**Additional V7 Kelly modifiers:**
- Mean Reversion boost: 3.0x (fixes strategy concentration)
- Symbol allocation cap: 25%
- Symbol PnL cap: 30% on dominant symbol (SOLUSDT)
- DOGEUSDT: DISABLED (WR=35%, net negative)

## Risk Weight Formula
- Best: `regret_based` (corr with loss = 0.211)
- Wilson-based risk weight corr with loss: 0.102 (37% better than raw 0.075)
- IS Wilson bounds are conservative on OOS (7/8 buckets)

## Node Risk Shares
- **trend_following**: 65% risk / 67% PnL (efficient)
- **mean_reversion**: 35% risk / 10% PnL (inefficient — V7 applies 3.0x boost instead of 0.5x reduction)

**V7 note:** The original DQRAE recommendation was 0.5x MR reduction. V7 inverts this to a 3.0x BOOST because the goal was strategy concentration balance (TF was 90%+ of PnL). The boost makes MR contribute 47.9% of PnL vs TF 52.1%, passing the 60% concentration threshold.

## Placeholder Registry (Must Replace Before Live Capital)
- AUTOEXEC_CONFIDENCE_FLOOR: 0.90 (borrowed from clinical AI) -> need >= 500 CAPITAL-tier resolved decisions
- DUAL_AUTH_BAND: [0.70, 0.90) (borrowed) -> same
- VETO_BELOW: 0.70 (borrowed) -> same
- FP/FN cost ratio: symmetric (0.5/0.5) -> need real feedback_delta cost computation

## Institutional Hardening (Jul 2026)

### Floating-PnL Drawdown Kill Switch
- `safety_layer.py` now computes true mark-to-market equity: `true_equity = wallet_balance + floating_pnl`
- `floating_pnl` calculated from open trades' unrealized P&L using live parquet close prices
- Drawdown check: `if true_equity < balance * (1 - account_drawdown_halt_pct)` → halt
- Previous bug: DB `equity` = `wallet_balance - margin_used` (available margin, NOT true equity). Margin was treated as loss, causing false drawdown halts.

### Directional Balance Guard
- `PORTFOLIO["max_same_side"] = 3` — max concurrent positions in same direction
- Gate 4d in `portfolio_orchestrator.py`: rejects new positions if >=3 already open LONG or SHORT
- Prevents all-short/all-long concentration risk
- Now also enforced at reprice promotion (PENDING->OPEN)

### Auto-Expiring Circuit Breaker
- `safety_layer.py`: errors older than 1h window auto-marked `resolved=TRUE` before counting
- Prevents permanent pipeline halt from stale critical errors
- Previous bug: false drawdown errors stayed forever, blocking all trading

## Calibration Schedule
- DQRAE risk weights: monthly, >= 500 resolved decisions per tier
- Decision calibration curve: weekly, >= 50 new resolved decisions

## Files
- `Backtest-Engine/backtest_dqrae_risk.py` — B1
- `Backtest-Engine/backtest_dqrae_oos.py` — B2
- `Backtest-Engine/institutional_validation_v7.py` — V7 Kelly implementation
- `Backtest-Engine/results/dqrae_b1_risk_calibration.json`
- `Backtest-Engine/results/dqrae_b2_oos_validation.json`
- `Backtest-Engine/results/institutional_validation_v7.json`
- `PROPRIETARY-ENGINES/DQRAE_Specification_v1.md`
