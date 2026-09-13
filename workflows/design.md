---
description: Design workflow — create system designs, architecture patterns, data models, and configuration schemas for trading systems
---

# Design Workflow

## When To Use
- Designing data models and database schemas
- Creating configuration schemas (JSON configs, calibration matrices)
- Designing API interfaces between system components
- Creating architectural patterns for new subsystems
- Designing feature engineering pipelines

## Design Principles (Ponytail System)

1. **Zero speculative abstractions** — No interfaces with 1 implementation, no factories for 1 product
2. **Deletion over addition** — Fewest files, agents, moving parts possible
3. **Root cause, not symptom** — Fix the shared component once
4. **Document intentional shortcuts** — `# ponytail: [constraint], [upgrade path]`
5. **Safety is never lazy** — Never simplify away input validation, error handling, security

## Design Steps

### 1. Data Model Design
- What entities exist? (trades, signals, decisions, hypotheses, regimes)
- What are their relationships? (1:1, 1:N, M:N)
- What are their lifecycle states?
- What constraints exist? (unique, not null, foreign keys)

**VLTHR DB Schema Example:**
```
shadow_decisions (V2 shadow engine)
  - id, timestamp, symbol, live_dqs, shadow_kelly_mult
  - shadow_decision, shadow_*_vetoed, actual_pnl, actual_outcome
  - trade_id (FK to paper_trades)

decision_events (DQRAE)
  - id, node_id, timestamp, tier (CAPITAL/STRUCTURAL/INFORMATIONAL)
  - magnitude, confidence, action (EXECUTE/ESCALATE/DEFER/VETO)
  - feedback_delta, latency_ms, dual_auth_flag
  - trade_id (FK to paper_trades)
```

### 2. Configuration Schema Design
- What parameters are configurable vs hardcoded?
- What are the valid ranges for each parameter?
- What are the defaults?
- How is the config versioned?

**VLTHR Config Example:**
```json
{
  "abstain_gate": {
    "threshold": 0.52,
    "per_symbol": {"BTCUSDT": {"abstain": true, "accuracy": 0.472}, ...},
    "per_regime": {"sideways": {"abstain": false, "accuracy": 0.537}, ...},
    "per_symbol_regime": {"BTCUSDT|sideways": {"abstain": true, "n": 42, "accuracy": 0.476}, ...}
  }
}
```

### 3. Interface Design
- What data flows between components?
- What format? (DataFrame, JSON, dict, parquet)
- What cadence? (real-time, 15m, hourly, daily)
- What error handling? (retry, skip, abort)

### 4. Feature Engineering Design
- What raw data is available?
- What transformations are needed? (log returns, z-scores, rolling stats)
- What lookback windows? (96, 200, 500 bars)
- What forward horizons for evaluation? (4, 16, 48 bars)
- **CRITICAL:** Are there any look-ahead bias risks? (multi-TF merge, future data)

### 5. Scoring Model Design
- What inputs go into the score?
- What weights? (must sum to 1.0 or 100)
- What transformations? (sigmoid, isotonic, Platt)
- What thresholds? (track, execute, good, excellent)
- What vetoes? (hard overrides regardless of score)

**VLTHR V7 DQS Design (GOLD STANDARD):**
```
DQS = (ctx_raw * 1.00 + bonuses) * combined_mult - penalties
```
Tech and struct domains are ZEROED OUT (IC was negative for both).
Only context domain (IC=+0.0046) contributes to DQS.
Vetoes: DQS 85+ (negative Kelly), crowd LS ratio > 3.0, DOGEUSDT disabled
Entry filter: calibrated_win_prob > 0.48 (isotonic calibration)

**V2 DQS formula (DEPRECATED — do not use):**
```
DQS = (tech_raw*0.35 + struct_raw*0.25 + ctx_raw*0.25 + crs_raw*0.15 + bonuses) 
      * combined_mult - penalties
```

### 6. Calibration Design
- What is being calibrated? (confidence, risk weight, gate threshold)
- What data is used? (UCB layers, live shadow, backtest)
- What method? (isotonic regression, Platt scaling, Wilson LB, Kelly)
- What schedule? (weekly, monthly, on-demand)
- What gates must pass? (G8 calibration error < 0.10)

### 7. Validation Design
- What tests validate the design?
- What gates must pass?
- What stress tests are needed?
- What shadow mode validation is required?

## VLTHR Design Patterns

### Probe-Test-Promote Pattern
```
Probe (discover) -> Test (validate IS/OOS) -> Promote (if Wilson > 0.05)
                                                    -> Demote (if abs(IC) < 0.02)
```

### Calibrate-Veto-Size Pattern
```
Calibrate (DQRAE risk weights) -> Veto (DQS 85+, ABSTAIN gate) -> Size (Kelly multipliers)
```

### Shadow-Live-Parity Pattern
```
Shadow (run new system in parallel) -> Compare (live-shadow parity ±15%) -> Deploy (if G9 passes)
```

### Institutional Hardening Patterns (Jul 2026)
```
Gate-At-Every-Transition: Portfolio gates enforced at BOTH signal generation AND reprice promotion
True-Equity-Drawdown: floating_pnl + wallet_balance (not just DB equity which excludes margin)
Quarantine-Bad-Data: validate_ohlcv failure → skip write (not write with issues flag)
Checksum-Audit-Trail: SHA-256 hash on critical log inserts for tamper detection
Flash-Wick-Guard: 10% spike filter on price movement before SL/TP checks
Auto-Tune-From-Outcomes: closed-trade exit reasons feed back into TP multiplier adjustment
Single-Writer-Ownership: one component owns paper_account writes (dashboard backend)
Auto-Expiring-Circuit-Breaker: errors auto-resolve after time window (1h) to prevent permanent halt
```

## Anti-Patterns
- Over-engineering: interfaces for 1 implementation, config layers for static values
- Under-engineering: no error handling, no validation, no rollback path
- Premature optimization: optimizing before the design is validated
- Speculative generality: building for hypothetical future requirements
