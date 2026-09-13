---
description: System factory workflow — create new systems from reusable patterns, templates, and architectural blueprints
---

# System Factory Workflow

## When To Use
- Creating a new trading system or subsystem from scratch
- Generating new probe types, hypothesis templates, or scenario detectors
- Building new calibration pipelines
- Creating new decision intelligence components
- Spinning up new analysis or research frameworks

## Factory Pattern

### 1. Define System Requirements
- What inputs does the system consume?
- What outputs does the system produce?
- What gates or thresholds must it satisfy?
- What is the system's scope (what is explicitly OUT of scope)?

### 2. Select Base Template
Choose the closest existing pattern:

| System Type | Base Template | Key Files |
|---|---|---|
| Probe (feature discovery) | `probe_engine.py` | 10 probe types, IC scoring, Wilson LB |
| Hypothesis (idea testing) | `hypothesis_engine.py` | Bayesian updating, lifecycle states |
| Scenario (regime detection) | `scenario_engine.py` | 8 regimes, robustness scoring |
| Decision (action selection) | `decision_engine.py` | Timing, evidence, execution, exit |
| Calibration | `build_cal_matrix.py` | Wilson LB, monotonicity, upsert |
| Backtest | `backtest_portfolio.py` | Cost model, metrics, IS/OOS split |
| Gate/Filter | `build_abstain_gate.py` | Per-cell accuracy, threshold blocking |

### 3. Generate System from Template
```
1. Copy base template
2. Replace domain-specific logic
3. Update input/output schemas
4. Add new domain rules
5. Wire into orchestrator
6. Add to workflow registry
```

### 4. System Interface Contract
Every new system must define:
```python
class NewSystem:
    def __init__(self, config: dict):
        """Load configuration, calibrate parameters."""
    
    def run(self, data: pd.DataFrame, **kwargs) -> list[Result]:
        """Process data and return results."""
    
    def validate(self, results: list[Result]) -> dict:
        """Check results against acceptance criteria."""
    
    def to_dict(self) -> dict:
        """Serialize system state for persistence."""
    
    @classmethod
    def from_dict(cls, state: dict) -> "NewSystem":
        """Deserialize system state."""
```

### 5. Integration Checklist
- [ ] System produces results in standard format
- [ ] Results can be serialized to JSON
- [ ] System has a runner (batch mode)
- [ ] System integrates with orchestrator (--flag)
- [ ] System has a backtest script
- [ ] System has acceptance gates defined
- [ ] System logs to DB (if stateful)
- [ ] System handles errors gracefully (no crash on bad data)

### 6. Quality Gates for New Systems
- No look-ahead bias in any computation
- No hardcoded constants in capital-affecting paths (use config)
- No placeholder priors without registry entry
- All features verified with leakage check
- IS/OOS validation before deployment

## VLTHR System Catalog

### Production Systems (V7 GOLD STANDARD 88/100)
- `confidence_engine` — DQS scoring, enriched data loading (V7: ctx-only scorer)
- `portfolio_orchestrator` — main pipeline, V2 filters, calibration gate, reprice gates, flash-wick fallback, TP tuning
- `v2_filters` — daily bias, volume, RSI momentum, regime, BTC correlation
- `calibration_lookup` — Wilson LB gate, calibrated probability
- `institutional_validation_v7` — V7 institutional validation (GOLD STANDARD)
- `safety_layer` — pre/post-flight checks, floating-PnL drawdown, auto-expiring circuit breaker, per-symbol freshness, checksum audit trail
- `portfolio_gates` — CorrelationGuard, TradeCountGuard, RiskBudgetLedger, DirectionalGuard (now enforced at both signal generation AND reprice promotion)
- `portfolio_config` — RISK_TIERS, Kelly bands, `tune_tp_from_history()` auto-tuning
- `test_risk_logic` — 31 unittest tests covering risk tiers, SL/TP, Kelly, gates, TP tuning, directional guard

### Shadow Systems (V3 — integrated into V7)
- `probe_engine` — feature discovery, IC validation (informed V7 scorer design)
- `hypothesis_engine` — Bayesian hypothesis testing
- `scenario_engine` — regime detection, robustness
- `decision_engine` — decision intelligence, ABSTAIN gate (secondary to V7 calibration filter)

### Calibration Systems
- `build_ucb_labels` — Layer 1 ground truth
- `build_ucb_microstructure` — Layer 2 features
- `build_ucb_decisions` — Layer 3 decision quality
- `build_cal_matrix` — calibration matrix
- `build_abstain_gate` — direction accuracy gate

### Backtest Systems
- `backtest_portfolio` — full portfolio backtest
- `replay_engine` — bar-by-bar replay
- `stress_test` — scenario stress testing
- `backtest_phase5_probes` — probe IC validation
- `backtest_phase8_decision` — decision PnL replay

### Infrastructure (Jul 2026 Hardening)
- `engine-ci.yml` — GitHub Actions CI, runs `test_risk_logic.py` on engine path changes
- `db_migration.py` — checksum columns on `error_log` and `signal_audit_log`
- `server.cjs` — flash-wick spike filter (10% guard), single writer for paper_account
