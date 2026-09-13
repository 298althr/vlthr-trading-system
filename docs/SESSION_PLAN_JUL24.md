# VLTHR Session Plan: Bug Fixes and Backtest Revalidation

**Date**: 2026-07-24 15:30 UTC (updated 20:20 UTC)
**Author**: Cascade (AI coding agent)
**Status**: COMPLETED
**Purpose**: Comprehensive plan for fixing critical bugs, revalidating backtest parameters, and returning to production-ready state.

### Completion Summary

All 7 bug fixes applied. Full 1-year backtest (Jul 2025 to Jul 2026) run with corrected parameters. **All 6 validation gates PASS.**

| Metric | Before | After |
|--------|--------|-------|
| PF | 2.21 | 2.86 |
| WR | 32.8% | 42.9% |
| Sharpe | 45.39 | 63.98 |
| TP hit rate | 29.9% (FAIL) | 41.4% (PASS) |
| SHORT trades | 16 | 249 |

See Section 10 of `BYBIT_PERFORMANCE_REVIEW_JUL24.md` for full results.

---

## 1. Scope

Fix all critical bugs identified in the Structural and Methodological Gap Analysis (Section 8A of BYBIT_PERFORMANCE_REVIEW_JUL24.md). Re-run backtests with corrected parameters. Return to production only after all fixes are validated.

### What This Session Covers
- 6 critical bug fixes (V1, V3, D13/R2, R1, E2, S2)
- 2 medium-priority fixes (S3, V2)
- Full backtest revalidation with corrected parameters
- Production readiness assessment

### What This Session Does NOT Cover
- Walk-forward optimization (V4) - deferred to future session
- Live validation pipeline (V5) - deferred to future session
- Portfolio diversification (P1, P5) - architectural change, needs separate planning
- Volatility targeting (P2) - architectural change, needs separate planning
- Stress testing (R5) - needs separate session

---

## 2. Pre-Requisites

### 2.1 Shut Down Docker Services
All Docker services must be stopped to free CPU and memory for backtesting.

```bash
docker compose down
```

Verify all containers stopped:
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

Expected: no VLTHR containers running. Postgres data preserved in `vlthr_pgdata` volume.

### 2.2 Frozen Parameters (Do Not Touch Without Human Approval)
- Kelly V7 bands
- MR boost 3x
- Calibration gate 0.40
- Crowding reduction 50%
- DQ gate 60
- Max active trades 8
- Scan interval 300s
- Max leverage 4x
- Max portfolio risk 12%
- SL_TIME_DECAY_RATE 0.15 (this is the correct live value; the bug is that backtest uses 0.25)
- REGIME_STRATEGY_MIN_DQS TF/MIXED=65

### 2.3 Parameter Changes Requiring Backtest Validation
- daily_loss_halt_pct: 2.0 to 3.0 (R1 fix)
- tp_mult: 4.0 to 3.0 for all symbols (S3 fix, tentative pending backtest)
- V2 daily bias filter logic change (S2 fix)

---

## 3. Task List

### Phase 1: Critical Bug Fixes (No Parameter Changes)

#### Task 1: Fix V1 - Backtest SL_TIME_DECAY_RATE Mismatch
- **Bug**: `backtest/backtest_runner.py:1048` hardcodes `0.25` for SL time decay. Live system uses `SL_TIME_DECAY_RATE = 0.15` from `portfolio_config.py:293`.
- **Root cause**: Backtest was written before the parameter was centralized. When TD was changed from 0.25 to 0.15 in config, the backtest was not updated.
- **Fix**: Import `SL_TIME_DECAY_RATE` from `portfolio_config` in backtest runner. Replace hardcoded `0.25` with the imported constant.
- **Files**: `backtest/backtest_runner.py`
- **Test**: Run backtest, verify TD value in logs matches 0.15.
- **Risk**: None. This makes backtest match live. The backtest may show different (likely worse) results, which is the point.

#### Task 2: Fix V3 - Backtest Runner Must Use Live Params
- **Bug**: Performance review states "Backtest runner reverted to baseline after optimization." The backtest does not import or use `REGIME_STRATEGY_MIN_DQS` or `SL_TIME_DECAY_RATE` from portfolio_config.
- **Root cause**: Optimization was done by editing backtest code directly, then reverted. Config-based params were never wired into the backtest.
- **Fix**: Import `REGIME_STRATEGY_MIN_DQS` from `portfolio_config` in backtest runner. Apply the TF/MIXED DQS >= 65 filter in the backtest scan logic.
- **Files**: `backtest/backtest_runner.py`
- **Test**: Run backtest, verify TF/MIXED signals with DQS < 65 are rejected.
- **Risk**: None. This makes backtest match live behavior.

#### Task 3: Fix D13/R2 - Symbol Disable Deadlock
- **Bug**: `_check_and_disable_symbols()` at `portfolio_orchestrator.py:211-260` evaluates the last 15 closed trades. When a symbol is auto-re-enabled after 12 hours, those same 15 losing trades are still the most recent (no new trades were placed while disabled). The rules immediately re-disable.
- **Root cause**: No mechanism to distinguish "freshly re-enabled, give it a chance" from "never disabled, still losing."
- **Fix**: When evaluating disable rules, only consider trades closed AFTER the last `disabled_at` timestamp. If a symbol was re-enabled and has no new trades since re-enabling, skip the disable check (grace period).
- **Files**: `pipeline/engine/portfolio_orchestrator.py`
- **Test**: Unit test: symbol with 15 losing trades, disabled, re-enabled after 12h, no new trades. Verify it is NOT re-disabled.
- **Risk**: Low. A genuinely bad symbol will accumulate new losing trades after re-enabling and get disabled again. The grace period only prevents the immediate re-disable deadlock.

#### Task 4: Fix E2 - Bybit SL Placement Failure on Fast Moves
- **Bug**: `portfolio_orchestrator.py:832-839` places market order with SL/TP in a single API call. When price moves past SL before the 5-min cycle executes, Bybit rejects the order ("StopLoss should lower than base_price").
- **Root cause**: Bybit validates SL/TP against current price at order submission time. If price has already breached SL, the order is rejected.
- **Fix**: Split into two API calls:
  1. Place market order without SL/TP.
  2. If market order succeeds, call `set_trading_stop()` to set SL/TP on the position.
  The `set_trading_stop` method already exists at `bybit_executor.py:184`.
- **Files**: `pipeline/engine/portfolio_orchestrator.py`
- **Test**: Verify code path: market order first, then set_trading_stop. Log both results.
- **Risk**: Low. If `set_trading_stop` fails, the position is open without SL. Add a retry and alert for this case.

### Phase 2: Parameter Changes (Require Backtest Validation)

#### Task 5: Fix R1 - Daily Loss Breaker Threshold
- **Bug**: `PORTFOLIO["daily_loss_halt_pct"] = 2.0` at `portfolio_config.py:307`. With 33% WR, a single SL at 5% risk can trip the breaker. Causes 732 rejections in 2 days.
- **Fix**: Change to 3.0.
- **Files**: `pipeline/engine/portfolio_config.py`
- **Old value**: 2.0
- **New value**: 3.0
- **Rationale**: 33% WR strategy expects frequent SL hits. -2% is too tight. -3% allows 2-3 SL hits before halting, which is more compatible with the strategy's win rate.
- **Backtest requirement**: Run backtest with 3.0 and compare PF, max drawdown, and trade count to baseline.

#### Task 6: Fix S2 - V2 Daily Bias Filter Blocking All SHORTs
- **Bug**: `v2_filters.py:205-214` blocks TF SHORT when daily bias is BULL, and MR SHORT when daily bias is BULL and RSI daily < 70. Backtest shows SHORT PF 7.27 (n=16) but live pipeline generates zero SHORTs.
- **Root cause**: Daily bias is a coarse EMA50 vs close comparison. It does not account for short-term momentum or intraday reversals. In a bull market with a pullback, SHORTs are systematically blocked even when the short-term setup is valid.
- **Fix**: Add a short-term momentum override. If the 4h log return is strongly negative (< -1.5%), allow the SHORT regardless of daily bias. This lets the system catch intraday pullbacks in a bull market.
- **Files**: `pipeline/engine/v2_filters.py`
- **Old logic**: TF SHORT blocked if daily_bias == "BULL". MR SHORT blocked if daily_bias == "BULL" and rsi_daily < 70.
- **New logic**: TF SHORT blocked if daily_bias == "BULL" AND 4h log return >= -0.015. MR SHORT blocked if daily_bias == "BULL" and rsi_daily < 70 AND 4h log return >= -0.015.
- **Rationale**: A strong short-term drop (-1.5% or more on 4h) signals a pullback worth trading as SHORT, even in a bull market. The daily bias filter was too coarse and eliminated all SHORTs.
- **Backtest requirement**: Run backtest with the override and compare SHORT trade count, SHORT PF, and overall PF.

#### Task 7: Fix S3 - TP Distances Too Far (Tentative)
- **Bug**: All symbols have `tp_mult: 4.0`. MFE averages 2.6 ATR (65% of TP at 4+ ATR). TP hit rate 30% (backtest) / 11% (live).
- **Fix**: Reduce `tp_mult` from 4.0 to 3.0 for all symbols.
- **Files**: `pipeline/engine/portfolio_config.py`
- **Old value**: 4.0 (all symbols)
- **New value**: 3.0 (all symbols)
- **Rationale**: MFE of 2.6 ATR means price typically reaches 65% of a 4 ATR TP. Reducing to 3.0 ATR puts TP within typical MFE range, improving TP hit rate.
- **Backtest requirement**: Run backtest with tp_mult=3.0 and compare TP hit rate, PF, and avg win/loss ratio.
- **Status**: TENTATIVE. Only apply if backtest shows PF improvement or neutral with higher TP hit rate.

#### Task 8: Fix V2 - Backtest Dynamic SL Modeling (Medium Priority)
- **Bug**: `backtest/backtest_runner.py` `_compute_dynamic_sl()` method exists (line 1025) but the simpler `regime_backtest.py` used for headline numbers does not model dynamic SL at all.
- **Fix**: Ensure `backtest_runner.py` uses the `_compute_dynamic_sl` method consistently in its exit checking logic. Verify it imports `SL_TIME_DECAY_RATE` from portfolio_config (covered by Task 1).
- **Files**: `backtest/backtest_runner.py`
- **Test**: Verify dynamic SL is applied in backtest exit checks. Compare results with and without dynamic SL.
- **Risk**: Low. This makes backtest more realistic.

### Phase 3: Backtest Revalidation

#### Task 9: Run Full Backtest with Corrected Parameters
- **Command**: Run `backtest/backtest_runner.py` with 1-year data range (Jul 2025 - Jul 2026).
- **Parameters to validate**:
  - SL_TIME_DECAY_RATE = 0.15 (fixed in Task 1)
  - REGIME_STRATEGY_MIN_DQS TF/MIXED = 65 (fixed in Task 2)
  - daily_loss_halt_pct = 3.0 (from Task 5)
  - V2 daily bias filter with SHORT override (from Task 6)
  - tp_mult = 3.0 (from Task 7, if backtest supports it)
- **Baseline for comparison**: Previous backtest results (893 trades, PF 2.21, WR 32.8%, Sharpe 45.39) with TD 0.25 and no TF/MIXED filter.
- **Success criteria**:
  - PF >= 1.8 (allowing for TD 0.15 being more conservative)
  - WR >= 30%
  - SHORT trades > 0 (validating S2 fix)
  - TP hit rate >= 35% (validating S3 fix if applied)
  - Max drawdown <= 15%
  - At least 5/6 validation gates PASS

#### Task 10: Compare Backtest Results to Live Performance
- Compare backtest metrics to live paper trading results (43 trades, WR 39.5%, net PnL -$633.65).
- Identify any remaining divergences between backtest and live.
- Update BYBIT_PERFORMANCE_REVIEW_JUL24.md with new backtest results.

#### Task 11: Update Performance Review Document
- Update `docs/BYBIT_PERFORMANCE_REVIEW_JUL24.md` with:
  - New backtest results
  - Status of each bug fix
  - Updated frozen parameters list
  - Updated recommendations
- Update date/time in document header.

### Phase 4: Production Readiness

#### Task 12: Run PIVP Validation
- Run Post-Implementation Validation Protocol on all changes.
- Verify no look-ahead bias in backtest changes.
- Verify no race conditions in Bybit order placement change.
- Verify all parameter changes are documented with old/new values and rationale.

#### Task 13: Run CITM Code Review
- Run CITM code reviewer on all modified files.
- Verify no severity BLOCKER or CRITICAL findings.

#### Task 14: Docker Rebuild and Restart
- Rebuild all modified services.
- Restart Docker services.
- Run zero-signal-diagnosis if no signals appear within 2 scan cycles.
- Monitor first 3 scan cycles for health.

---

## 4. Execution Order

Tasks must be executed in this order due to dependencies:

1. **Task 3** (Docker shutdown) - frees resources
2. **Task 1** (V1 fix) - backtest SL_TIME_DECAY_RATE
3. **Task 2** (V3 fix) - backtest REGIME_STRATEGY_MIN_DQS
4. **Task 8** (V2 fix) - backtest dynamic SL modeling
5. **Task 3** (D13/R2 fix) - symbol disable deadlock
6. **Task 4** (E2 fix) - Bybit SL placement
7. **Task 5** (R1 fix) - daily loss breaker (parameter change)
8. **Task 6** (S2 fix) - V2 daily bias filter (logic change)
9. **Task 7** (S3 fix) - TP distances (parameter change, tentative)
10. **Task 9** (Backtest) - full revalidation
11. **Task 10** (Compare) - backtest vs live
12. **Task 11** (Update doc) - performance review
13. **Task 12** (PIVP) - validation
14. **Task 13** (CITM) - code review
15. **Task 14** (Restart) - production

---

## 5. Files to be Modified

| File | Tasks | Changes |
|------|-------|---------|
| `backtest/backtest_runner.py` | 1, 2, 8 | Import SL_TIME_DECAY_RATE and REGIME_STRATEGY_MIN_DQS from portfolio_config. Replace hardcoded 0.25. Apply TF/MIXED DQS filter in scan. |
| `pipeline/engine/portfolio_orchestrator.py` | 3, 4 | Fix symbol disable deadlock (grace period after re-enable). Split Bybit order into market + set_trading_stop. |
| `pipeline/engine/portfolio_config.py` | 5, 7 | Change daily_loss_halt_pct to 3.0. Change tp_mult to 3.0 (tentative). |
| `pipeline/engine/v2_filters.py` | 6 | Add 4h log return override for SHORT signals in BULL daily bias. |
| `docs/BYBIT_PERFORMANCE_REVIEW_JUL24.md` | 11 | Update with new backtest results and fix status. |

---

## 6. Success Criteria for Production Restart

All of the following must be true before Docker services are restarted:

1. All 6 critical bug fixes implemented and tested.
2. Backtest run with corrected parameters shows PF >= 1.8.
3. Backtest generates at least some SHORT trades (validating S2 fix).
4. No PIVP blockers.
5. No CITM blockers.
6. All parameter changes documented with old value, new value, rationale, and backtest evidence.
7. Performance review document updated with new results.
8. Human approval received for any parameter changes to frozen list.

---

## 7. Rollback Plan

If backtest results are worse than baseline after all fixes:
1. Revert parameter changes (daily_loss_halt_pct, tp_mult, V2 filter logic).
2. Keep critical bug fixes (V1, V3, D13/R2, E2) as they are correctness fixes, not parameter changes.
3. Run backtest again with only bug fixes, no parameter changes.
4. Document results and decide whether to proceed with partial fixes.

---

*Document created: 2026-07-24 15:30 UTC. Update this timestamp when the document is modified.*
