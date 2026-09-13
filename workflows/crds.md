---
description: CRDS — Confidence & Calibration Hierarchy for VLTHR (source, evidence, feature, hypothesis, decision, execution, system confidence layers)
---

# CRDS Workflow: Confidence & Calibration Hierarchy

## When To Use
- Calibrating confidence at any layer of the VLTHR pipeline
- Building or updating the crowd sentiment veto (ls_ratio > 3.0)
- Adding a new confidence layer (feature, hypothesis, source, execution)
- Replacing placeholder priors with real calibration data

## Canonical Naming
- **CRDS** = Confidence & Calibration Hierarchy (VLTHR pipeline) — this workflow
- **CReDS** = Competitive Reaction Decision System (cross-venture) — separate workflow

## Confidence Layers (Staged Reveal)

Each layer is earned by passing the gate below it. Do NOT build all layers upfront.

### Layer 4 — Decision Confidence (BUILD NOW)
- **Measured:** calibration curve (predicted win prob vs realized), Brier score
- **Output:** `calibrated_confidence = recalibrate(raw_confidence)` via isotonic/Platt
- **Gate:** calibration error < 0.10 (G8)
- **Schedule:** weekly refit, >= 50 new resolved decisions

### Layer 2 — Feature Confidence (earned by Phase 5 exit gate)
- **Adds:** rolling IC, Wilson lower bound, IC half-life into confidence aggregation
- **Gate:** FDR-controlled walk-forward IC significance

### Layer 3 — Hypothesis Confidence (earned by Phase 6 exit gate, G1)
- **Adds:** Bayesian posteriors feed per-regime confidence
- **Gate:** direction accuracy > 52%

### Layer 0 — Source Reliability (earned by Phase 4 exit gate)
- **Adds:** uptime/error/gap metrics per data source
- **Gate:** real L/S + liquidation data backfilled

### Layer 5 — Execution Confidence (earned by G9)
- **Adds:** slippage distribution, fill probability adjustments
- **Gate:** live-shadow parity within ±15%

### Layer 6 — System Confidence / DQRAE (earned by G1 + G5 + G9)
- **Adds:** product of all layers feeds capital allocation
- **Gate:** all prior gates pass

## Crowd Sentiment Calibration (CRDS-B1 through B5)

1. **B1 — Crowd Sentiment:** Fit logistic regression on `crowd_ls_ratio`, `rsi_14` vs forward return sign
   - `crowd_ls_ratio` is contrarian (coef -0.061)
   - `rsi_14` is strongest feature (+0.118)
   - Output: `results/crds_b1_crowd_calibration.json`

2. **B2 — Liquidation Cascade:** Fit on liquidation events vs forward returns
   - Sparse data — no veto thresholds if < 5000 clean bars
   - Output: `results/crds_b2_cascade_calibration.json`

3. **B4 — Combined CRS:** Merge crowd + cascade + technical features
   - IS AUC 0.538, OOS AUC 0.520
   - CRS weight in DQS: 0.15
   - Output: `results/crds_b4_combined_crs.json`

4. **B5 — Veto Drawdown Reduction:** Find veto threshold that maximizes Sharpe
   - `ls_ratio > 3.0` is the winner: +$854 PnL, Sharpe 4.45 -> 4.66
   - Output: `results/crds_b5_veto_drawdown.json`

## DQS Integration

**V3 Adaptive Scorer (V7 Gold Standard):**
```
DQS = (ctx_raw * 1.00 + bonuses) * combined_mult - penalties
```
Tech and struct domains are ZEROED OUT (IC was negative for both).
Only context domain (IC=+0.0046) contributes to DQS.

**Previous V2 formula (deprecated):**
```
DQS = (tech_raw*0.35 + struct_raw*0.25 + ctx_raw*0.25 + crs_raw*0.15 + bonuses) * combined_mult - penalties
```

**Crowd veto still active:** `ls_ratio > 3.0` veto applied in V7 pipeline.

## Files
- `Backtest-Engine/backtest_crds_crowd.py`
- `Backtest-Engine/backtest_crds_cascade.py`
- `Backtest-Engine/backtest_crds_combined.py`
- `Backtest-Engine/backtest_crds_veto.py`
- `Backtest-Engine/results/crds_b*.json`
- `Backtest-Engine/institutional_validation_v7.py` — V7 validation with crowd veto
