---
description: Presentation workflow — create compelling presentations and reports from system analysis, backtest results, and calibration findings
---

# Presentation Workflow

## When To Use
- Creating executive summaries of system status
- Presenting backtest results to stakeholders
- Documenting research findings in a digestible format
- Creating handover documents for system transitions

## Structure

### 1. Executive Summary (1 page)
- System status: live/shadow/calibration
- Key metric: direction accuracy, PnL, Sharpe, win rate
- Top 3 findings (good or bad)
- Top 3 recommended actions
- Gate status: which gates pass, which fail

### 2. System Overview (1 page)
- Architecture diagram (sensors -> interpretation -> ... -> learning)
- Key components and their status
- Data sources and coverage
- Pipeline flow summary

### 3. Results Dashboard (2-3 pages)
- Performance metrics table (IS vs OOS, per-symbol, per-regime)
- Equity curve (if applicable)
- Direction accuracy breakdown
- Calibration status (feedback delta, gate status)
- Bootstrap analysis (P5, P50, P95)

### 4. Key Findings (2-3 pages)
For each finding:
- What was found (state precisely)
- Evidence (data, metrics, statistical tests)
- Impact (what does this mean for the system?)
- Confidence level (high/medium/low)
- Recommended action

### 5. Risk Assessment (1 page)
- What could go wrong
- What is untested
- What placeholders are still active
- What gates are failing

### 6. Recommendations (1 page)
- Immediate actions (capital protection)
- Near-term actions (edge improvement)
- Medium-term actions (system health)
- Not recommended (with rationale)

## Visualization Templates

### Metrics Table
```
| Metric | IS | OOS | Change | Target |
|---|---|---|---|---|
| Win Rate | 41.5% | 42.8% | -1.3% | > 45% |
| Direction Acc | 73.1%* | 48.9% | -24.2% | > 52% |
| Sharpe | 4.88 | 5.37 | +10.2% | > 2.0 |
| Max DD | -$3,080 | -$2,419 | -21.4% | < $3,000 |
* IS was inflated by label leakage, now fixed
```

### Gate Status Dashboard (V7 GOLD STANDARD)
```
G1: Direction > 52%        [PASS] 71.9% (V7 filtered)
G4: No label leakage       [PASS] (fixed)
G5: IS-OOS robustness > 0.80 [PASS] 0.8848
G8: Calibration error < 0.10 [PASS] Brier 0.247, FD -0.017
G9: Live-shadow parity     [PENDING] (post-deployment)
GOLD: Institutional score >= 85% [PASS] 88/100
```

### Per-Symbol Heatmap (V7)
```
           BTC    ETH    SOL    XRP    BNB    DOGE
Status     ACTIVE ACTIVE ACTIVE ACTIVE ACTIVE DISABLED
PnL        Varies Varies Varies Varies Varies N/A
Strategy   TF     MR     TF     MR     MR     N/A
```
V7 uses isotonic calibration filter (>0.48) instead of per-cell ABSTAIN gate.
DOGEUSDT disabled (WR=35%, net negative).

## VLTHR V7 Presentation Example

**Title:** VLTHR V7 — Institutional Validation & Deployment Status

**Executive Summary:**
- System status: V7 GOLD STANDARD 88/100, ready for shadow deployment
- Key metrics: 1,201 trades, 54% WR, $13,401 PnL, 1.80 PF, MaxDD -$1,288, 71.9% direction accuracy
- Top findings: (1) Isotonic calibration filter removes 87% of base decisions, achieving 54% WR, (2) V3 adaptive scorer uses ctx-only (tech/struct zeroed out, IC was negative), (3) DQS 85+ veto confirmed (negative Kelly)
- Top actions: (1) Deploy shadow mode for G9 validation, (2) Improve robustness pillar (11/15 — WF decay at 75% split), (3) Improve calibration pillar (8/10 — Brier 0.247 > 0.20 target)
- Gate status: G1 PASS, G4 PASS, G5 PASS, G8 PASS, G9 PENDING, GOLD PASS (88/100)

## Output Formats
- Markdown document (for technical audience)
- JSON summary (for programmatic consumption)
- PowerPoint-compatible outline (for executive audience)
