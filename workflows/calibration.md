---
description: Calibration workflow — decision calibration curves, DQRAE risk weight refitting, calibration gate management, placeholder replacement
---

# Calibration Workflow

## When To Use
- Refitting decision calibration curves (predicted vs realized win probability)
- Rebuilding DQRAE risk weights
- Managing calibration gates (pipeline + backend)
- Replacing placeholder priors with real calibration data
- Rebuilding the calibration matrix from backtest trades

## Calibration Hierarchy (Staged Reveal)

Only build layers that have earned their gate. Do NOT build all 7 layers upfront.

| Layer | Earned By | Adds | Status |
|---|---|---|---|
| 4 — Decision Confidence | Default | Isotonic/Platt recalibration | Build now |
| 2 — Feature Confidence | Phase 5 exit gate | Rolling IC, Wilson LB | Pending |
| 3 — Hypothesis Confidence | Phase 6 exit gate (G1) | Bayesian posteriors | Pending |
| 0 — Source Reliability | Phase 4 exit gate | Uptime/error/gap metrics | Pending |
| 5 — Execution Confidence | G9 (live-shadow parity) | Slippage distribution | Pending |
| 6 — System Confidence | G1 + G5 + G9 | Product of all layers | Pending |

## Calibration Schedule

| Component | Frequency | Trigger |
|---|---|---|
| Decision calibration curve | Weekly | >= 50 new resolved decisions |
| DQRAE risk weights | Monthly | >= 500 resolved decisions per tier |
| Calibration matrix | On new backtest | New V2 backtest CSV available |

## Calibration Matrix Build

1. **Load V2 backtest CSV** (4h IS period trades)
2. **Classify trades:** `net_pnl > 0` = WIN (NOT `exit_type == TP_HIT`)
3. **Group by (symbol, DQS_bucket, strategy, session)**
4. **Compute Wilson lower bound** per cell
5. **Enforce monotonicity** on global fallback only (NOT per-cell)
6. **Upsert to DB** using `ON CONFLICT` (NOT DELETE+INSERT — race condition)
7. **Update calibration.json** with new gate thresholds

## V7 Calibration Configuration (GOLD STANDARD — DO NOT CHANGE)

**Entry filter:** `calibrated_win_prob > 0.48`
**Isotonic calibration:** IS 75% split
**Walk-forward split:** 50% (standard institutional)

**V3 Adaptive Scorer weights:**
- tech: 0.00 (IC was -0.0098, actively misleading)
- struct: 0.00 (IC was -0.005, negative)
- ctx: 1.00 (IC was +0.0046, only positive domain)

**Kelly V7 DQS bands:**
- DQS 85+: 0x (VETO — negative Kelly)
- DQS 80-85: 2.5x
- DQS 75-80: 1.5x
- DQS 70-75: 1.0x
- DQS 65-70: 0.7x
- DQS <65: 0.3x
- MR boost: 3.0x (fixes strategy concentration)
- Symbol allocation cap: 25%, PnL cap: 30%

**Pipeline (calibration.json):**
- BTC/SOL/XRP: gate threshold 0.20
- ETH/BNB/DOGE: gate threshold 0.15

**Backend (server.cjs):**
- BTC/SOL/XRP: gate threshold 0.20
- ETH/BNB/DOGE: gate threshold 0.15

## TP/SL Auto-Tuning (Jul 2026 Hardening)

`tune_tp_from_history(conn)` runs at pipeline startup to adjust TP multipliers based on closed-trade outcomes:
- **Trigger:** ≥20 closed trades with 0 TP hits and >5 TIME_EXIT winners
- **Action:** Tightens TP multipliers by 20% (e.g., 9.0→7.2, 7.5→6.0)
- **Floor:** `sl_mult * MIN_RR_FLOOR` (maintains minimum R:R of 2.0)
- **Scope:** Adjusts `RISK_TIERS` dict in-place at runtime (does not modify config file)
- **Known issue:** 0 TP hits in first 10 closed trades — all winners exited via TIME_EXIT, suggesting TP targets (6-9x ATR) are too far for 12h hold window

## Immutable Audit Trail (Jul 2026 Hardening)

- SHA-256 checksum (16-char hex) added to `error_log` and `signal_audit_log` INSERT statements
- Checksum computed from message content + timestamp before insert
- Enables post-hoc tamper detection: recompute hash and compare to stored checksum
- DB migration: `ALTER TABLE error_log ADD COLUMN IF NOT EXISTS checksum VARCHAR(16)` and same for `signal_audit_log`

## Placeholder Registry

| Placeholder | Source | Current | Replacement Trigger | Status |
|---|---|---|---|---|
| AUTOEXEC_CONFIDENCE_FLOOR | Clinical AI | 0.90 | >= 500 CAPITAL-tier decisions | Active |
| DUAL_AUTH_BAND | Clinical AI | [0.70, 0.90) | >= 500 CAPITAL-tier decisions | Active |
| VETO_BELOW | Clinical AI | 0.70 | >= 500 CAPITAL-tier decisions | Active |
| FP/FN cost ratio | Insurance | 0.5/0.5 | Real feedback_delta computation | Active |
| LS ratio veto threshold | Phase 4 backfill | > 3.0 | DONE — backfilled 392 partitions | Replaced |
| Kelly multipliers | V7 Backtest | 2.5x/0.7x/0.3x | Live shadow validation | Pending G9 |
| Regime thresholds | Hand-set | Fixed percentiles | Empirical UCB distribution | Pending |

## V7 Institutional Validation Score

| Pillar | Score | Max |
|--------|-------|-----|
| Edge Existence | 15 | 15 |
| Statistical Significance | 15 | 15 |
| Robustness | 11 | 15 |
| Risk Analysis | 11 | 15 |
| Cost Sensitivity | 15 | 15 |
| Calibration | 8 | 10 |
| Concentration | 8 | 10 |
| Operational Readiness | 5 | 5 |
| **TOTAL** | **88** | **100** |

**Calibration pillar gaps (8/10):**
- Brier score 0.247 (target <0.20 for 5/5, currently 3/5)
- Feedback delta |FD|=0.0034 (5/5, maxed)

## Files
- `Backtest-Engine/build_cal_matrix.py` — calibration matrix builder
- `Backtest-Engine/institutional_validation_v7.py` — V7 institutional validation
- `paper_trade_unzipped/vlthr-signal-dashboard/engine/calibration.json` — gate config
- `paper_trade_unzipped/vlthr-signal-dashboard/backend/server.cjs` — backend gate
- `Backtest-Engine/results/` — all calibration JSON outputs
- `Backtest-Engine/results/institutional_validation_v7.json` — V7 scorecard
- `engine/portfolio_config.py` — `tune_tp_from_history()`, RISK_TIERS with auto-tuning
- `engine/safety_layer.py` — checksum on `error_log` inserts
- `engine/portfolio_orchestrator.py` — checksum on `signal_audit_log` inserts
- `engine/db_migration.py` — checksum column migrations
