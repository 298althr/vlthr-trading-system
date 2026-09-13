---
description: CReDS — Competitive Reaction Decision System for cross-venture competitor reaction scoring (NOT VLTHR pipeline)
---

# CReDS Workflow: Competitive Reaction Decision System

## When To Use
- Evaluating how competition will react to a business decision (pricing, product, marketing, capital)
- Scoring candidate actions by weighted competitive reaction
- Cross-venture strategy decisions (NOT crypto trading — see CRDS for that)

## Canonical Naming
- **CReDS** = Competitive Reaction Decision System (this workflow, cross-venture)
- **CRDS** = Confidence & Calibration Hierarchy (VLTHR crypto pipeline — separate workflow)
- CReDS is OUT OF SCOPE for VLTHR pipeline. Its only defensible VLTHR mapping (order-book crowding / correlated-strategy flow) is covered by the crowd-sentiment veto in CRDS.

## Core Abstractions

| Term | Definition |
|---|---|
| Actor | You / the venture making the decision |
| Competitive Set (CS) | List of competitors relevant to this decision domain |
| Reaction Dimension (RD) | Phase of possible reaction (pricing, product, marketing, capital, legal, talent, speed, retention) |
| Action | Candidate decision expressed as an impact vector |
| Reaction Magnitude | -1 to +1 (unfavorable to favorable) |
| Reaction Probability | 0 to 1 (likelihood of reaction materializing) |
| Reaction Latency | fast / medium / slow |

## Scoring Model

### Per-dimension weighted score
```
WeightedDimScore(c,d) = magnitude(c,d) * probability(c,d) * dimWeight(c,d)
```

### Competitor Reaction Score
```
CoRS(c) = sum_d WeightedDimScore(c,d) / 100    -> range [-1, 1]
```

### Composite Reaction Score
```
CRS = sum_c ( CoRS(c) * setWeight(c) ) / 100    -> range [-100, 100]
```

- CRS > 0: net favorable
- CRS ~ 0: neutral / competitively invisible
- CRS < 0: net unfavorable

### Cascade Veto
```
IF magnitude(c,d) <= -0.8 AND probability(c,d) >= 0.7 AND dimWeight(c,d) >= 20
THEN CRITICAL_REACTION_RISK = True -> hard veto, regardless of CRS
```

## Steps

1. **Define Competitive Set** — List competitors with set weights (sum to 100)
2. **Define Reaction Dimensions** — Per competitor, list dimensions with weights (sum to 100 per competitor)
3. **Score Impact Vector** — For each competitor/dimension pair, assign magnitude (-1 to +1) and probability (0 to 1)
4. **Compute CRS** — Aggregate per the scoring model above
5. **Check Veto** — Any single dimension triggering cascade veto overrides CRS
6. **Assess Latency** — Factor in reaction speed for timing decisions
7. **Decide** — CRS > 0 with no veto = proceed; CRS < 0 = reconsider or stage rollout

## Key Design Rule
Weights are always relative and always renormalize to 100. Adding/removing a competitor or dimension forces explicit tradeoffs.

## Source
`PROPRIETARY-ENGINES/CRDS_SPECIFICATION.md` (rename internally to CReDS to avoid collision with VLTHR CRDS)
