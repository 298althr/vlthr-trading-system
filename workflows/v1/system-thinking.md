---
description: Systems thinking methodology — analyze complex systems through feedback loops, emergence, boundaries, and leverage points
---

# Systems Thinking Workflow

## When To Use
- Analyzing complex systems with interacting components
- Identifying feedback loops (positive/negative) and emergent behaviors
- Finding leverage points for system intervention
- Understanding unintended consequences of changes
- Designing new systems or diagnosing existing ones

## Core Concepts

1. **System Boundary** — Define what's inside vs outside the system
   - What are the stocks (accumulations)?
   - What are the flows (rates of change)?
   - What are the feedback loops?

2. **Feedback Loops**
   - **Reinforcing (positive):** A -> B -> more A (exponential growth/decline)
   - **Balancing (negative):** A -> B -> less A (self-correcting, stable)
   - Identify delays in feedback — delayed feedback causes oscillation

3. **Emergence** — System-level behaviors that don't exist in any individual component
   - The system is not the sum of its parts — it's the product of their interactions
   - Watch for emergent properties that are unexpected

4. **Leverage Points** (from least to most effective)
   - Parameters (numbers, constants)
   - Buffer sizes
   - Stock/flow structures
   - Delays
   - Balancing feedback loop strength
   - Reinforcing feedback loop strength
   - Information flows
   - Rules (incentives, constraints)
   - **Self-organization** (ability to add/change structure)
   - **Goals** (what the system optimizes for)
   - **Paradigm** (what the system IS)

## Steps

1. **Map the system** — Draw stocks, flows, and feedback loops
2. **Identify the boundary** — What's in, what's out, what crosses the boundary
3. **Trace causality** — For each outcome, trace back through causal chains
4. **Find feedback loops** — Which loops dominate? Are there delays?
5. **Identify leverage points** — Where can intervention produce the most change?
6. **Anticipate side effects** — What happens when you push on a leverage point?
7. **Look for emergence** — What system-level behaviors arise from interactions?
8. **Test mental models** — What assumptions are you making? How could they be wrong?

## VLTHR Application
- **System boundary:** 6 Bybit perpetuals, 15m execution, 4h context, Supabase DB
- **Reinforcing loop:** Good backtest -> confidence -> deploy -> live trades -> feedback -> calibrate -> better backtest
- **Balancing loop:** ABSTAIN gate -> fewer trades -> less data -> slower calibration -> gate loosens -> more trades
- **Leverage point:** Direction accuracy (G1 gate) — if solved, unlocks capital deployment
- **Emergent property:** 48.9% direction accuracy emerges from DQS scoring + strategy selection + regime detection interacting together
- **Unintended consequence:** Calibration gate rejects most live signals (too conservative), pipeline trades less than backtest

## Anti-Patterns
- Linear thinking (A causes B) in a nonlinear system
- Focusing on parameters when the goal or paradigm is misaligned
- Ignoring delays in feedback loops
- Treating symptoms instead of root causes
