---
description: Master workflow index — navigation hub for all VLTHR workflows, engines, skills, and systems
---

# VLTHR Master Workflow Index

## How To Use
Each workflow is a `.md` file in `.devin/workflows/`. Use the slash command or reference the workflow name to invoke it. Workflows are grouped by category below.

---

## Proprietary Engines

| Workflow | Description | Slash Command |
|---|---|---|
| [ADCOS](adcos.md) | Adaptive Decision Control Operating System — full-pipeline orchestration, 14-stage replay, acceptance gates | `/adcos` |
| [CRDS](crds.md) | Confidence & Calibration Hierarchy — 7-layer confidence calibration, crowd sentiment veto, DQS integration | `/crds` |
| [CReDS](creds.md) | Competitive Reaction Decision System — cross-venture competitor reaction scoring (NOT VLTHR pipeline) | `/creds` |
| [DQRAE](dqrae.md) | Decision Quantification & Risk Allocation Engine — Kelly sizing, risk weights, feedback delta, placeholder registry | `/dqrae` |
| [DRVE](drve.md) | Decision Ripple Verification Engine — 10-layer pairwise verification, tournament backtesting, reserve decisions | `/drve` |

---

## V3 Discovery Engine

| Workflow | Description | Slash Command |
|---|---|---|
| [Probe Engine](probe-engine.md) | Feature discovery — 10 probe types, IC validation, Wilson lower bounds, redundancy | `/probe-engine` |
| [Hypothesis Engine](hypothesis-engine.md) | Bayesian hypothesis testing — 12 templates, lifecycle states, evidence accumulation | `/hypothesis-engine` |
| [Scenario Engine](scenario-engine.md) | Regime detection — 8 regimes, robustness scoring, IS/OOS validation | `/scenario-engine` |
| [Decision Engine](decision-engine.md) | Decision intelligence — timing, evidence, execution, exit, ABSTAIN gate | `/decision-engine` |

---

## Calibration Systems

| Workflow | Description | Slash Command |
|---|---|---|
| [UCB Construction](ucb-construction.md) | Unified Calibration Benchmark — 4 layers (labels, microstructure, decisions, feedback) | `/ucb-construction` |
| [Calibration](calibration.md) | Decision calibration curves, DQRAE risk weights, gate management, placeholder replacement | `/calibration` |
| [ABSTAIN Gate](abstain-gate.md) | Direction accuracy filter — block trading where accuracy < 52% per (symbol, regime) | `/abstain-gate` |

---

## Trading & Backtesting Skills

| Workflow | Description | Slash Command |
|---|---|---|
| [Backtest Strategy](backtest-strategy.md) | Full backtest workflow — data, signals, simulation, metrics, validation | `/backtest-strategy` |
| [Data Management](data-management.md) | Ingestion, validation, multi-TF merge, enrichment, leakage prevention | `/data-management` |
| [Parameter Optimization](parameter-optimization.md) | Grid/random search, walk-forward optimization, overfitting prevention | `/parameter-optimization` |
| [Performance Metrics](performance-metrics.md) | Comprehensive metrics calculation, bootstrap analysis, reporting | `/performance-metrics` |
| [Scenario Testing](scenario-testing.md) | Stress tests — normal, high vol, crisis, 2x cost, inverted bias, decoupler | `/scenario-testing` |
| [Statistical Alpha](statistical-alpha.md) | Bootstrap p-value, deflated Sharpe, factor regression, meta consistency | `/statistical-alpha` |
| [Walk-Forward](walk-forward.md) | Chronological IS/OOS validation, year-by-year, overfitting detection | `/walk-forward` |
| [Debug Backtest](debug-backtest.md) | Diagnose look-ahead bias, overfitting, data snooping, label leakage | `/debug-backtest` |

---

## General Systems

| Workflow | Description | Slash Command |
|---|---|---|
| [Systems Thinking](system-thinking.md) | Feedback loops, emergence, leverage points, system boundaries | `/system-thinking` |
| [Problem Solving](problem-solving.md) | Root cause analysis, hypothesis-driven debugging, 5 Whys | `/problem-solving` |
| [System Regeneration](system-regeneration.md) | Refactor/rebuild systems with shadow mode, incremental replacement | `/system-regeneration` |
| [System Factory](system-factory.md) | Create new systems from reusable patterns, templates, interface contracts | `/system-factory` |
| [Agent Runner](agent-runner.md) | Batch agent execution across symbols, loop mode, result aggregation | `/agent-runner` |
| [Research](research.md) | Systematic research methodology — literature, implement, validate, document | `/research` |
| [Recommendations](recommendations.md) | Evidence-based action items, prioritized by impact and confidence | `/recommendations` |
| [Solution Design](solution-design.md) | End-to-end solution design — requirements, architecture, implementation plan | `/solution-design` |
| [Presentation](presentation.md) | Executive summaries, results dashboards, gate status, stakeholder reports | `/presentation` |
| [Design](design.md) | Data models, config schemas, interfaces, scoring models, calibration design | `/design` |

---

## Quick Reference

### Acceptance Gates
| Gate | Criteria | Status |
|---|---|---|
| G1 | Direction accuracy > 52% | PASS (71.9% V7 filtered) |
| G4 | No label leakage | PASS (fixed) |
| G5 | IS-OOS robustness > 0.80 | PASS (0.8848) |
| G8 | Calibration error < 0.10 | PASS (Brier 0.247, FD -0.017) |
| G9 | Live-shadow parity ±15% | PENDING (post-deployment) |
| GOLD | Institutional score >= 85% | **PASS (88/100 GOLD STANDARD)** |

### System Status
- **Institutional validation:** 88/100 — GOLD STANDARD
- **All phases:** COMPLETE (0-10)
- **All tests:** 47/47 passing
- **Capital:** $10,000, 4x leverage
- **Trading window:** 12 months (Jun 2025 – May 2026)
- **V7 final metrics:** 1,201 trades, $13,401 PnL, 54% WR, 1.80 PF
- **Key docs:** Doc 40 (Strategy), Doc 41 (CTP-V4 Handoff), Doc 42 (Linux Deploy)

### Key Files
- Engine: `paper_trade_unzipped/vlthr-signal-dashboard/engine/`
- Backtest: `Backtest-Engine/`
- Data: `data/bybit/{SYMBOL}/{TF}/*.parquet`
- UCB: `data/ucb/layer*.parquet`
- Results: `Backtest-Engine/results/`
- Specs: `PROPRIETARY-ENGINES/`
- Docs: `docs/algorithm-specs/`, `v3/docs/algorithm-specs/`
- V7 validation: `Backtest-Engine/institutional_validation_v7.py`
- V7 report: `Backtest-Engine/results/institutional_validation_v7.json`

### Ponytail System (Global Rules)
1. YAGNI — does this need to exist?
2. Reuse existing implementation
3. Use stdlib/native before new deps
4. One-liners win
5. Minimum viable system only if 1-4 fail
6. Zero speculative abstractions
7. Deletion over addition
8. Safety is never lazy
