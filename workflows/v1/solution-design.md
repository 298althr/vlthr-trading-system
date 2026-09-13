---
description: Solution design workflow — design end-to-end solutions for trading system problems, from requirements to implementation plan
---

# Solution Design Workflow

## When To Use
- Designing a new system component or feature
- Creating an implementation plan for a complex change
- Designing solutions to problems identified by research or debugging
- Planning architecture changes or migrations

## Steps

### 1. Requirements Analysis
- What problem are we solving? (state precisely)
- What are the inputs? (data sources, formats, cadence)
- What are the outputs? (results, metrics, decisions)
- What constraints exist? (gates, thresholds, performance, resources)
- What is explicitly out of scope?

### 2. Architecture Design
- Map components and their interactions
- Identify which existing systems can be reused
- Define interfaces between components
- Choose data flow pattern (batch, streaming, hybrid)
- Consider failure modes and error handling

### 3. Decision Ladder (Ponytail System)
Before writing any new code, run through:
1. **YAGNI** — Does this need to exist at all?
2. **Existing Implementation** — Already in the codebase?
3. **Standard Library** — Can stdlib/platform handle it?
4. **Existing Dependency** — Already-installed package?
5. **One-Liner** — Can it be a single line or basic control flow?
6. **Minimum Viable System** — Only if 1-5 fail, write the minimum

### 4. Interface Contract
Define the interface for each new component:
```python
class NewComponent:
    def __init__(self, config: dict): ...
    def run(self, data: pd.DataFrame, **kwargs) -> list[Result]: ...
    def validate(self, results: list[Result]) -> dict: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, state: dict) -> "NewComponent": ...
```

### 5. Implementation Plan
- Break into phases, each independently testable
- Define acceptance gates for each phase
- Identify dependencies between phases
- Estimate resource requirements (CPU, RAM, disk, time)

### 6. Validation Strategy
- Unit tests (assert-based, stdlib only)
- Integration tests (component interaction)
- Backtest validation (IS/OOS, walk-forward)
- Shadow mode validation (live vs shadow parity)
- Gate checks (G1, G4, G5, G8, G9)

### 7. Risk Assessment
- What could go wrong?
- What are the failure modes?
- What is the rollback plan?
- What placeholders are introduced? (register in placeholder registry)

### 8. Documentation
- Architecture diagram
- Interface contracts
- Implementation plan with phases
- Acceptance gates
- Placeholder registry entries
- `# ponytail: [constraint], [upgrade path]` for intentional shortcuts

## VLTHR Solution Design Example: ABSTAIN Gate

**Problem:** System has 48.9% direction accuracy (below chance). Need to prevent trading in low-accuracy regimes.

**Requirements:**
- Input: replay data with (symbol, regime, side, optimal_side)
- Output: JSON config marking which (symbol, regime) cells to block
- Constraint: threshold 52%, must integrate into V2 + V3 pipelines
- Out of scope: improving direction accuracy (separate effort)

**Architecture:**
- Calibration script (`build_abstain_gate.py`) -> JSON config
- Decision engine: load JSON, check before side determination
- V2 orchestrator: load JSON, check after V2 filters
- V3 orchestrator: inline check in shadow mode

**Decision ladder result:** Step 6 (MVS) — no existing implementation, needs new code but minimal (~100 lines)

**Implementation phases:**
1. Build calibration script (testable independently)
2. Integrate into decision_engine (test with mock gate)
3. Integrate into portfolio_orchestrator (test with real data)
4. Integrate into V3 orchestrator (test in shadow mode)

**Gates:**
- Gate config must have all 42 cells (6 symbols x 7 regimes)
- Numpy bools must be cast to Python bools for JSON serialization
- Regime detection in V2 must use available data (ctx_adx, bull_4h)
