# Phase 1 Archive — Validated Configuration

## Status: COMPLETE — All 6 validation gates PASS

## Archived Files
- `portfolio_config.py` — Phase 1 config (Kelly=0 for DQS>=85, domain weights 40/30/30)
- `portfolio_orchestrator.py` — Phase 1 pipeline fixes (V7 veto moved to risk gate, equity fix, scanner decoupled)
- `calibration.json` — DQS calibration with restored domain weights
- `adaptive_scorer.py` — Scorer with 40/30/30 domain weights
- `portfolio_gates.py` — CorrelationGuard with MAX_OPEN=8
- `backtest_runner.py` — Fixed closed-trade logging, SL computation at reprice
- `backtest_results.csv` — 64,179 rows (3 modes x 21,393 signals, Jan-Jun 2026)
- `backtest_results_kelly_adj.csv` — Kelly=0.25 for DQS>=85 A/B test
- `stress_test_results.csv` — Per-symbol/regime/session/tier/month breakdowns

## Phase 1 Results (Control Mode — Kelly=0 for DQS>=85)
- Closed trades: 1,441
- Win rate: 56.4%
- Profit factor: 1.56
- Expectancy: +$5.88/trade
- Max drawdown: 3.55%
- TP hit rate: 45.0%
- Final balance: $18,476.84 (from $10,000)
- All 6 gates: PASS

## Key Parameters
- KELLY_V7_BANDS: dqs_ge_85=0.0, dqs_ge_80=2.5, dqs_ge_75=1.5, dqs_ge_70=1.0, dqs_ge_65=0.7, dqs_lt_65=0.3
- DQS_WEIGHTS: technical=0.40, structure=0.30, context=0.30
- RISK_TIERS tp_mult: fair=3.0, good=4.0, excellent=5.0
- MIN_RR_FLOOR: 1.0
- PORTFOLIO: max_active=8, max_daily=10, max_risk_pct=12.0, max_same_side=5
- CorrelationGuard.MAX_OPEN: 8

## Kelly Adjustment Test (0.25x for DQS>=85)
- DQS>=85 signals have WORSE win rate (46.8% vs 56.4% overall)
- Adding them degrades all metrics: PF 1.56→1.48, DD 3.55%→5.60%, Calmar 48→28
- Conclusion: V7 veto (Kelly=0) is VALIDATED — keep DQS>=85 blocked
