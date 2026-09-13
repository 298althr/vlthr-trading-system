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
- "Reduce mean_reversion risk to 0.5x — 35% risk share but only 10% PnL — High confidence — Capital-affecting"
- "Do NOT deploy live capital — 48.9% direction accuracy (below chance) — High confidence — Capital-affecting"

### 5. Prioritize
1. Capital protection (vetoes, gates, ABSTAIN)
2. Edge improvement (features, strategies, data sources)
3. Risk optimization (position sizing, drawdown reduction)
4. System health (calibration, monitoring, bug fixes)

### 6. Specify Preconditions
- What gates must pass before this recommendation is actionable?
- What data is needed to validate this recommendation?
- What would falsify this recommendation?

## VLTHR Current Recommendations

### Immediate (Capital Protection)
1. **ABSTAIN gate active** — 31/42 cells blocked, only SOLUSDT + sideways/crisis pass
2. **DQS 85+ veto** — negative Kelly, do not trade
3. **Crowd LS ratio > 3.0 veto** — improves Sharpe by 10%
4. **Mean reversion 0.5x risk reduction** — inefficient risk share

### Near-Term (Edge Improvement)
5. **Investigate contrarian ensemble** — top features (risk_drawdown_96, trend_ret_96, regime_trend_dir) all negative IC, may work as ensemble
6. **Backfill real L/S ratio data** — replace proxy, improve CRDS calibration
7. **Add orderbook/aggTrade data** — 9 features currently NaN, may unlock structure domain edge

### Medium-Term (System Health)
8. **Replace placeholder priors** — DQRAE thresholds borrowed from clinical AI, need >= 500 resolved decisions
9. **Re-run V2 backtest with leakage fix** — ctx_log_ret_4h fix may change V2 backtest results
10. **Bear market testing** — system untested in sustained downtrend

### Not Recommended
11. **Reserve decisions** — improves WR but loses total PnL (DRVE-B4)
12. **Live capital deployment** — until G1 (direction > 52%) passes

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
