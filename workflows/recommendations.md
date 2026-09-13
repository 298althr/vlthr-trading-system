---
description: Recommendations workflow — generate actionable, evidence-based recommendations from system analysis, backtest results, and calibration data
---

# Recommendations Workflow

## When To Use
- Generating production recommendations from backtest or calibration results
- Prioritizing action items from system audits
- Creating deployment go/no-go recommendations
- Synthesizing findings from multiple engines into actionable advice

## Framework

### 1. Gather Evidence
Collect all relevant data from:
- UCB layers (ground truth, features, decision quality)
- Probe results (IC, Wilson LB, persistence)
- Hypothesis results (confirmed/refuted, posteriors)
- Scenario results (regime accuracy, robustness)
- Decision results (direction accuracy, PnL, Sharpe)
- Backtest results (IS/OOS, stress tests, bootstrap)
- Calibration data (feedback delta, gate status)

### 2. Classify by Confidence
- **High confidence:** Multiple independent sources agree, statistically significant, OOS validated
- **Medium confidence:** Single source, marginally significant, needs more data
- **Low confidence:** Speculative, insufficient data, or contradicted by other sources

### 3. Classify by Impact
- **Capital-affecting:** Position sizing, veto thresholds, gate configuration
- **Structural:** Architecture changes, new systems, pipeline modifications
- **Informational:** Monitoring, logging, documentation

### 4. Generate Recommendations
Format: `[Action] — [Evidence] — [Confidence] — [Impact]`

Examples:
- "Veto DQS 85+ trades — negative Kelly in DQRAE-B1, confirmed in OOS — High confidence — Capital-affecting"
- "Apply crowd_ls_ratio > 3.0 veto — Sharpe +10% in CRDS-B5 — High confidence — Capital-affecting"
- "Mean reversion 3.0x BOOST — fixes strategy concentration, TF 52.1% / MR 47.9% PnL balance — High confidence — Capital-affecting"
- "Deploy live capital with G9 monitoring — V7 88/100 GOLD STANDARD, 71.9% direction accuracy on filtered trades — High confidence — Capital-affecting"

### 5. Prioritize
1. Capital protection (vetoes, gates, calibration filter)
2. Edge improvement (features, strategies, data sources)
3. Risk optimization (position sizing, drawdown reduction)
4. System health (calibration, monitoring, bug fixes)

### 6. Specify Preconditions
- What gates must pass before this recommendation is actionable?
- What data is needed to validate this recommendation?
- What would falsify this recommendation?

## VLTHR V7 Current Recommendations (GOLD STANDARD 88/100)

### Immediate (Capital Protection)
1. **Isotonic calibration entry filter** — `calibrated_win_prob > 0.48` filters 87% of base decisions, achieves 54% WR on remaining 1,201 trades — High confidence — Capital-affecting
2. **DQS 85+ veto** — negative Kelly, do not trade — High confidence — Capital-affecting
3. **Crowd LS ratio > 3.0 veto** — improves Sharpe by 10% — High confidence — Capital-affecting
4. **Mean reversion 3.0x BOOST** — fixes strategy concentration (TF 52.1% / MR 47.9% PnL balance, passes 60% concentration threshold) — High confidence — Capital-affecting
5. **DOGEUSDT disabled** — WR=35%, net negative — High confidence — Capital-affecting
6. **Symbol PnL cap 30%, allocation cap 25%** — prevents SOLUSDT dominance — High confidence — Capital-affecting
7. **Portfolio gates at reprice promotion** (Jul 2026) — prevents risk gate bypass at PENDING→OPEN — High confidence — Capital-affecting
8. **Floating-PnL drawdown kill switch** (Jul 2026) — true mark-to-market equity prevents false halts and catches real drawdowns — High confidence — Capital-affecting
9. **Flash-wick protection** (Jul 2026) — 10% spike filter prevents false SL/TP triggers — High confidence — Capital-affecting

### Near-Term (Edge Improvement)
10. **Improve robustness pillar (11/15)** — WF decay at 75% split = 45.7% (FAIL), investigate parameter stability — Medium confidence — Structural
11. **Improve calibration pillar (8/10)** — Brier 0.247 > 0.20 target, need better predicted vs realized calibration — Medium confidence — Structural
12. **Backfill real L/S ratio data** — replace proxy, improve CRDS calibration — Medium confidence — Structural
13. **Add orderbook/aggTrade data** — 9 features currently NaN, may unlock structure domain edge — Low confidence — Structural
14. **TP/SL auto-tuning** (Jul 2026) — `tune_tp_from_history()` tightens TP if 0 hits + >5 TIME_EXITs — needs ≥20 closed trades to activate — Medium confidence — Capital-affecting

### Medium-Term (System Health)
15. **Replace placeholder priors** — DQRAE thresholds borrowed from clinical AI, need >= 500 CAPITAL-tier resolved decisions — High confidence — Structural
16. **Bear market testing** — system untested in sustained downtrend — Medium confidence — Structural
17. **Pass G9 (live-shadow parity)** — deploy shadow mode, verify ±15% parity before full live capital — High confidence — Capital-affecting
18. **Immutable audit trail verification** (Jul 2026) — checksums on error_log and signal_audit_log, need periodic verification script — Medium confidence — Structural

### Not Recommended
19. **Reserve decisions** — improves WR but loses total PnL (DRVE-B4) — High confidence — Capital-affecting
20. **V2 ABSTAIN gate as primary filter** — superseded by V7 isotonic calibration filter, keep as secondary safety net only — High confidence — Capital-affecting
21. **Mean reversion 0.5x reduction** — V7 inverts this to 3.0x BOOST; original DQRAE recommendation was based on pre-V7 risk shares — High confidence — Capital-affecting

## Output Format
```json
{
  "recommendations": [
    {
      "action": "...",
      "evidence": "...",
      "confidence": "high|medium|low",
      "impact": "capital|structural|informational",
      "preconditions": ["..."],
      "falsified_by": "..."
    }
  ],
  "priorities": ["immediate", "near_term", "medium_term", "not_recommended"]
}
```
