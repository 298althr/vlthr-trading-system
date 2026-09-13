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

### Gate Status Dashboard
```
G1: Direction > 52%        [FAIL] 48.9%
G4: No label leakage       [PASS] (fixed)
G5: IS-OOS robustness > 0.80 [PASS] 0.8848
G8: Calibration error < 0.10 [PENDING]
G9: Live-shadow parity     [PENDING]
```

### Per-Symbol Heatmap
```
           BTC    ETH    SOL    XRP    BNB    DOGE
Direction  47.2%  50.1%  52.0%  45.1%  49.7%  47.2%
Status     ABSTAIN ABSTAIN OK    ABSTAIN ABSTAIN ABSTAIN
PnL OOS    +$2352 +$2114 +$2418 -$1207  -$250   +$306
```

## VLTHR Presentation Example

**Title:** V3 Discovery Engine — Status & Findings

**Executive Summary:**
- System status: V2 live (calibration gate active), V3 shadow mode
- Key metric: 48.9% direction accuracy (below chance — G1 FAIL)
- Top findings: (1) Label leakage fixed, (2) No individual feature has standalone edge, (3) ABSTAIN gate blocks 31/42 cells
- Top actions: (1) Investigate contrarian ensemble, (2) Backfill real L/S data, (3) Do NOT deploy live capital
- Gate status: G4 PASS, G5 PASS, G1 FAIL, G8/G9 PENDING

## Output Formats
- Markdown document (for technical audience)
- JSON summary (for programmatic consumption)
- PowerPoint-compatible outline (for executive audience)
