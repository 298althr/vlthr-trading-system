---
description: Problem-solving framework — root cause analysis, hypothesis-driven debugging, systematic investigation, and solution design
---

# Problem-Solving Workflow

## When To Use
- Investigating bugs, discrepancies, or unexpected behavior
- Diagnosing performance issues in trading systems
- Resolving metric mismatches between different calculation methods
- Any systematic investigation requiring structured approach

## Framework

### 1. Define the Problem
- State the observed behavior precisely
- State the expected behavior precisely
- Quantify the gap (e.g., "73.1% vs 48.9% direction accuracy")
- Identify when the problem was first observed

### 2. Form Hypotheses
- List all possible causes (brainstorm, don't filter yet)
- For each cause, predict what you'd observe if it were true
- Rank by likelihood and ease of verification

### 3. Test Hypotheses (Cheapest First)
- Start with the easiest-to-verify hypothesis
- Design a test that would falsify the hypothesis
- Run the test
- Record the result (confirmed or falsified)

### 4. Root Cause Analysis
- When a hypothesis is confirmed, trace back to root cause
- Ask "why" 5 times (5 Whys technique)
- Distinguish proximate cause (what triggered it) from root cause (why it was possible)

### 5. Design Solution
- Fix the root cause, not the symptom
- Minimal change — don't refactor unrelated code
- Consider side effects of the fix
- Add a regression test to prevent recurrence

### 6. Verify Solution
- Run the original failing test case
- Run regression tests
- Check that no new issues are introduced
- Document the fix

## VLTHR Example: Direction Accuracy Discrepancy

**Problem:** Phase 8 reports 73.1% direction accuracy, full replay reports 48.9%

**Hypotheses tested:**
1. Different ground truth (1h fwd return vs UCB optimal_side) -> Partial explanation
2. Different sampling cadence (4h vs 15m) -> Partial explanation
3. Label leakage in ctx_log_ret_4h -> **ROOT CAUSE** (correlation 0.42 with forward returns)

**Root cause:** 4h bar timestamps represent OPEN, but merge_asof backward assigns 4h close data (known 4h in the future) to current 15m bars.

**Fix:** Shift 4h timestamps +4h before merge_asof. Post-fix correlation: ~0.00.

**Verification:** `verify_leakage.py` confirms correlation < 0.05 for all symbols.

## Anti-Patterns
- Jumping to solutions before understanding the problem
- Fixing symptoms instead of root causes
- Not testing hypotheses before implementing fixes
- Over-engineering solutions (use single-line fix when sufficient)
- Not adding regression tests after fixes
