---
description: System regeneration workflow — refactor, rebuild, or regenerate existing systems while preserving functionality and improving architecture
---

# System Regeneration Workflow

## When To Use
- Refactoring a system that has accumulated technical debt
- Rebuilding a component after discovering fundamental flaws
- Migrating from V1 to V2 to V3 architecture
- Regenerating systems after paradigm shifts

## Principles

1. **Preserve functionality** — The system must produce the same outputs during regeneration
2. **Shadow mode first** — Run new system alongside old, compare outputs
3. **Incremental replacement** — Replace one component at a time, not all at once
4. **Audit trail** — Every change is logged with before/after state
5. **Rollback path** — Every step has a documented rollback procedure

## Steps

1. **Audit current system**
   - Map all components and their dependencies
   - Identify technical debt, bugs, and design flaws
   - Document what works well (don't break it)

2. **Define target architecture**
   - What should the system look like after regeneration?
   - What constraints must the new system satisfy?
   - What lessons from the old system inform the new design?

3. **Build shadow system**
   - Implement new components alongside old ones
   - Run both in parallel, log all outputs
   - Compare: where do they agree? Where do they diverge?

4. **Validate parity**
   - Live-shadow parity within ±15% (G9 gate)
   - Direction accuracy matches expected values
   - No new bugs or regressions

5. **Replace incrementally**
   - Switch one component at a time
   - After each switch: run full validation suite
   - If any test fails: rollback, investigate, fix, retry

6. **Decommission old system**
   - Only after all components replaced and validated
   - Keep old code as reference (don't delete immediately)
   - Document the migration in handover notes

## VLTHR V1 -> V2 -> V3 Migration Example

**V1 -> V2:**
- Added V2 filters (daily bias, volume, RSI momentum, regime, BTC correlation)
- Calibration matrix rebuilt with V2 backtest data
- Gate thresholds lowered
- Shadow mode: V2 ran alongside V1 until validated

**V2 -> V3:**
- V3 discovery engine (probe, hypothesis, scenario, decision) built in shadow
- V3 orchestrator runs alongside V2 pipeline
- V3 decisions logged as shadow, not executed
- V2 remains live until V3 passes all gates (G1, G5, G8, G9)

## Regeneration Triggers
- Discovery of fundamental flaw (e.g., label leakage)
- Paradigm shift (e.g., from count-based to quality-based risk allocation)
- Accumulated technical debt (e.g., 70+ tmp_*.py files)
- New data sources that change system capabilities
- Gate failure requiring architectural change
- Institutional hardening audit (e.g., Jul 2026: 12 fixes for risk gate bypass, data integrity, audit trail)

## V2 Institutional Hardening (Jul 2026)

**Audit findings and fixes:**
- Risk gate bypass at reprice → gates now enforced at PENDING→OPEN
- False drawdown halts → floating-PnL-aware kill switch with true mark-to-market equity
- Dual-writer race on paper_account → single writer (dashboard backend only)
- Bad data written with issues flag → blocking validation, quarantine on failure
- TLS verification disabled → removed `verify=False` from 6 API call sites
- Flash-wick false triggers → 10% spike filter in both server.cjs and fallback monitor
- Circuit breaker never cleared → auto-expiring after 1h window
- Only BTC freshness checked → per-symbol freshness for all 5 symbols
- Static TP targets → auto-tuning from closed-trade exit reasons
- No audit integrity → SHA-256 checksums on error_log and signal_audit_log
- No risk logic tests → 31 unittest tests covering gates, tiers, Kelly, TP tuning
- No CI → GitHub Actions on engine path changes

## Anti-Patterns
- Big bang rewrite (replace everything at once)
- Not running shadow mode (can't validate before switching)
- Deleting old code before new code is validated
- Not documenting the migration (future engineers won't understand why)
