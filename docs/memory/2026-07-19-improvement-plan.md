# VLTHR Improvement Plan — Jul 19, 2026

**Status:** DRAFT — pending human approval before any changes
**Phase:** Post-Phase 2 audit
**Trigger:** Live production data review (25 closed trades, Jul 15-19)

---

## Executive Summary

Three critical gaps found in the live paper trading system:

1. **Risk sizing is broken.** Every trade takes $6 risk (0.06%) instead of the intended $60-200 (0.6-2.0%). A notional cap overrides the risk percentage config.
2. **BTC is bleeding.** 22 trades, 15 expired (duplication bug), 7 closed with 43% win rate and -$19.10 PnL. The trend_following strategy with tight ATR-based stops is not working for BTC in current conditions.
3. **No feedback loop.** The pipeline logs gate rejections and trade outcomes but nothing reads them back. Every 5-minute cycle reasons from scratch. The Pipeline Brain exists but is disabled.

---

## Gap 1: Risk Sizing — `cap_position_size` Overrides Risk_pct (CRITICAL)

### Root Cause

`portfolio_config.py:289` — `cap_position_size()` caps unleveraged notional to `CAPITAL_PER_SYMBOL = $2,000`:

```python
def cap_position_size(position_size_1x, account_balance):
    return min(position_size_1x, CAPITAL_PER_SYMBOL, account_balance * SYMBOL_ALLOC_CAP)
```

The orchestrator (`portfolio_orchestrator.py:1108-1112`) computes dollar risk from `risk_pct * kelly_mult`, derives a quantity, then immediately caps the notional to $2,000. This destroys the risk sizing.

### Evidence

Traced for BTC at entry $64,322 with 0.3% SL distance ($193):

| DQS | Tier Risk | Kelly | Intended Risk | Pre-cap Notional | Post-cap Notional | Actual Risk |
|-----|-----------|-------|---------------|-------------------|-------------------|-------------|
| 55  | 2.0%      | 0.3x  | $60 (0.6%)    | $19,996           | $2,000            | $6 (0.06%)  |
| 65  | 3.0%      | 0.7x  | $210 (2.1%)   | $69,988           | $2,000            | $6 (0.06%)  |
| 75  | 5.0%      | 1.5x  | $750 (7.5%)   | $249,956          | $2,000            | $6 (0.06%)  |

**Every trade across every symbol shows ~$2,000 notional**, confirming the cap is always binding. Actual risk per trade is $6-20 depending on SL distance, which is 0.06-0.20% of the $10k account.

### Secondary Issue: `PER_SYMBOL_PARAMS["risk_pct"]` Is Dead Code

`PER_SYMBOL_PARAMS["BTCUSDT"]["risk_pct"] = 0.05` is never read by the orchestrator. Position sizing uses `RISK_TIERS` (tier-based by DQS), not per-symbol risk_pct. This config value is misleading.

### Fix

**Option A (minimal):** Raise `CAPITAL_PER_SYMBOL` to match intended risk. For 2% risk with 0.3% SL distance on a $10k account, notional needs to be ~$66,667. Set `CAPITAL_PER_SYMBOL = 10000.0` and let `SYMBOL_ALLOC_CAP = 0.25` ($2,500) be the real cap. But $2,500 notional with 0.3% SL = $7.50 risk, still too small.

**Option B (correct):** Cap dollar risk, not notional. Replace the notional cap with a risk-based cap:

```python
def cap_dollar_risk(dollar_risk, account_balance, max_risk_pct=2.0):
    return min(dollar_risk, account_balance * max_risk_pct / 100)
```

Then size quantity from the capped dollar risk. This decouples position size from SL distance.

**Option C (recommended):** Remove `cap_position_size` from the sizing path entirely. Let `risk_pct * kelly_mult` determine dollar risk, then derive quantity. Use the portfolio-level risk budget gate (Gate 4c) and per-symbol margin cap ($1,667 in backend) as the real constraints. The per-symbol margin cap already prevents over-concentration.

### Impact at 2% Risk

With 2% risk per trade on $10k = $200 risk:
- Current 25 closed trades at $6 avg risk = ~$150 total risk deployed
- At $200 risk per trade = $5,000 total risk deployed (33x increase)
- Win rate 48%, avg win $19.65, avg loss -$15.84: expected value per trade at 2% risk = $200 * (0.48 * 1.24 - 0.52 * 1.00) = $200 * 0.075 = $15/trade
- Over 25 trades: ~$375 vs current $47.92

**This is a projection, not a guarantee.** Run D6 (Risk sizing with leverage and funding costs) to validate before changing live config.

---

## Gap 2: BTC Underperformance (CRITICAL)

### Data

| Metric | BTC | All Symbols |
|--------|-----|-------------|
| Total trades | 22 | 43 |
| Expired | 15 | 16 |
| Closed | 7 | 25 |
| Wins | 3 | 12 |
| Win rate | 43% | 48% |
| Net PnL | -$19.10 | +$47.92 |
| TP hits | 3 | 12 |
| SL hits | 4 | 11 |

### Bug 2a: Signal Duplication (15 Expired Trades on Jul 17)

15 BTC trades were created on Jul 17 with identical SL ($62,731.94) and TP ($63,658.19) prices. All expired. This is a duplication bug — the same signal was repeatedly submitted as PENDING without deduplication against existing pending trades for the same symbol.

**Fix:** Before creating a new PENDING trade, check if a PENDING trade already exists for the same symbol with the same signal_bar_utc. If so, skip. The dedup check at line 1152 only prevents duplicates within the same scan iteration, not across iterations.

### Bug 2b: BTC Trend_Following Strategy Mismatch

BTC is configured as `trend_following` with `sl_mult=2.5, tp_mult=7.5`. But the actual SL distances in live trades range from 0.3% to 0.8%, suggesting very tight ATR readings. The R:R ratio is nominally 3:1, but with such tight stops, BTC's normal noise easily triggers SL before the trend develops.

**4 out of 7 closed BTC trades were SL_HIT.** The SL distances that triggered:
- Trade #10: 0.80% SL distance, hit
- Trade #27: 0.56% SL distance, hit
- Trade #28: 0.69% SL distance, hit
- Trade #35: 0.44% SL distance, hit

Compare to SOL (also trend_following, 5 closed, 4 wins): SL distances are 0.3-0.4%, but SOL's lower price means the ATR-based stops are proportionally wider relative to noise.

**Fix options:**
1. Switch BTC to `mean_reversion` strategy (like ETH, BNB, XRP) — BTC has been range-bound in the $62k-65k zone during the test period
2. Increase `sl_mult` for BTC from 2.5 to 3.5-4.0 to give trends more room
3. Add a minimum SL distance floor (e.g., 0.5% for BTC) to prevent noise-triggered stops
4. Run D5 (realworld backtest) with modified BTC params to validate before changing live

### Bug 2c: BTC Has 22 Trades but Only 7 Closed

The 15 expired trades inflate BTC's trade count and waste pipeline cycles. Even after fixing the duplication bug, BTC generates too many signals that never execute. This suggests the DQS threshold for BTC may be too low, or the V2 filters are not strict enough for BTC's current regime.

---

## Gap 3: No Feedback Loop (MAJOR)

### Current State

- **Pipeline Brain:** `BRAIN_ENABLED = False` in `portfolio_config.py:336`. The River online classifier exists but is not running. No machine learning feedback.
- **Decision logging:** `_log_node_decision()` writes to `signal_node_log` on every gate pass/reject. Nothing reads this table back before making the next decision.
- **Trade outcomes:** Closed trades record `exit_reason` and `net_pnl_usd`. No process analyzes whether gate rejections were correct (i.e., would a rejected signal have been profitable?).
- **Telegram alerts:** Notify on events but do not analyze root cause.

### What's Missing

The system has instrumentation but no reasoning. It accumulates incidents, not insight. Every 5-minute scan starts fresh without asking: "Have I seen this setup before? What happened last time?"

### Fix (per workflow protocols)

1. **Backfill Pipeline Brain:** Run `pipeline_brain.py` predictions against historical Parquet + `signal_state_log` to accumulate the 30+ samples needed for trust criteria. Don't wait for live shadow trades one at a time.
2. **Wire decision log into scoring:** Before `adaptive_scorer.py` finalizes a DQS score, query `signal_node_log` for similar prior setups (same symbol, same strategy, similar DQS range). Let historical outcomes adjust the score by 2-5 points, not override it.
3. **Add rejection analysis:** Weekly job that samples rejected signals, checks what would have happened if they were approved, and reports false-positive rate per gate.
4. **RCA integration:** Wire pipeline Step 12 (post-flight trace) to POST failures to an RCA endpoint instead of just logging a string. This closes the loop between "something failed" and "we understand why."

---

## Gap 4: No Reconciliation Against Exchange Data (MAJOR)

Paper trading is simulated entirely inside the system. Nothing checks simulated fills against actual Bybit prices. The backtest assumes no slippage, no funding costs, and static crowding clusters. These are the most likely sources of live divergence.

### Fix

1. Record the Bybit ticker price at signal creation time and at trade open/close. Compare to the simulated entry/exit.
2. Track funding rate paid/received during open positions. Subtract from PnL.
3. Estimate slippage as the difference between simulated fill and next-bar open.
4. Run this reconciliation for 50 trades, then feed the average slippage and funding cost back into the backtest to see if the edge survives.

---

## Gap 5: No Redundancy or Backup (MAJOR)

- Single Postgres container, single pipeline container, no replicas, no failover.
- `vlthr_pgdata` named volume has no backup strategy.
- If the Postgres container dies mid-trade, there is no recovery story.

### Fix

1. Add `pg_dump` cron job to the Postgres container, writing to a mounted backup volume.
2. Add health check retry logic to the pipeline container so it restarts cleanly on failure.
3. Document the recovery procedure: restore from dump, restart pipeline, verify trade state consistency.

---

## Gap 6: No Test Suite or CI (MINOR — documented but not enforced)

- `test_risk_logic.py` exists but is not run in any CI pipeline.
- No automated test runs on deploy.
- PIVP Phase 2 (Automated Debug Pass) is manual.

### Fix

1. Add `pytest` step to a deploy script or CI workflow.
2. Add integration test that runs one pipeline iteration against fixture data and verifies trade creation.
3. Gate deploys on test pass.

---

## Priority Order

| Priority | Gap | Effort | Risk if Ignored |
|----------|-----|--------|-----------------|
| 1 | Risk sizing (Gap 1) | Small code change, big impact | Every trade is 33x undersized. Cannot evaluate strategy with broken sizing. |
| 2 | BTC duplication bug (Gap 2a) | Small fix | Wasted cycles, polluted trade log, unreliable metrics. |
| 3 | BTC strategy review (Gap 2b) | Requires D5 backtest | Continued losses on 1 of 5 symbols. |
| 4 | Feedback loop (Gap 3) | Medium effort, high value | System never learns from outcomes. |
| 5 | Reconciliation (Gap 4) | Medium effort | Live results will diverge from backtest with no explanation. |
| 6 | Redundancy (Gap 5) | Small effort | Data loss risk. |
| 7 | Test suite (Gap 6) | Small effort | No regression protection. |

---

## Workflow Protocol Alignment

### GBEGP (Build Governance)
- **Dev/prod separation missing:** No `/development` or `/production` dirs. Risk sizing fix should be tested in a dev environment first.
- **Scope:** This plan covers 7 gaps. Implement one at a time, per GBEGP Section 2.2 (agent proposes, human approves scope).

### PIVP (Validation)
- **Before changing risk sizing:** Run D6 (Risk sizing with leverage and funding costs) to validate the new parameters.
- **Before changing BTC strategy:** Run D5 (realworld backtest) with modified params.
- **After each fix:** Run PIVP gate. Do not advance to next fix until gate passes.

### CCP (Communication)
- No parameter changes without explicit human approval.
- This plan is a proposal, not a directive.

### RODP (Repository Organization)
- This document lives in `/docs/memory/` per RODP Section 6.
- Progress reports should be written per RODP Section 7 after each fix lands.

### Pipeline Gate Audit
- The 12-gate chain is structurally sound. The gaps are in pre-gate (sizing) and post-gate (feedback), not in the gates themselves.
- Gate 4c (Risk Budget Ledger) is checking capacity against an account_balance that is correct ($10,046), but the dollar risk it approves is immediately overridden by `cap_position_size`. The gate passes but the trade is undersized.

---

## Do Not Touch

- `calibration.json` (V7 Gold Standard is frozen)
- DQS scoring weights
- Kelly sizing bands (fix the cap, not the bands)
- Entry filter threshold (0.40, already softened from 0.48)
- DOGEUSDT (remains disabled)
- `data/` directory contents

---

## Next Actions (pending approval)

1. **Approve this plan** — human review required before any code changes.
2. **Fix risk sizing** — implement Option B or C, test in dev, run D6 backtest.
3. **Fix BTC duplication** — add cross-iteration dedup check.
4. **Review BTC strategy** — run D5 backtest with alternative params.
5. **Backfill Pipeline Brain** — accumulate historical predictions.
6. **Add reconciliation** — record exchange prices alongside simulated fills.
