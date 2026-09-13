# Repository Organization & Documentation Protocol (RODP)
### Companion to GBEGP (before/during) and PIVP (after) — governs how the repo itself stays legible as it grows.

**Core principle:** length is a *signal*, not the rule. The real rule is **one concern per file.** A short file doing three unrelated things still gets split; a long file doing one thing consistently is fine.

---

## 1. Top-Level Repository Structure

```
/development
/production
/docs
   /architecture   ← system design, spec docs, decision records
   /schema         ← DB/data schemas, API contracts
   /backend        ← service docs, endpoint inventories
   /frontend       ← component inventory, design-system docs
   /memory         ← progress reports, session summaries, handoff packets
/src (or /app)
/styles            ← see Section 3
```

If a project's existing structure doesn't match this, the agent proposes a migration plan rather than restructuring silently — this is a scope-affecting change per GBEGP Section 2.

---

## 2. File Length & Split Thresholds (soft caps, not hard rules)

| File type | Soft cap | Split trigger |
|---|---|---|
| CSS file | ~150–250 lines | New concern (theme, breakpoint, component) — split immediately regardless of length |
| Backend service/controller | ~200–300 lines | Mixed responsibility (routing + business logic + DB access) |
| Frontend component | ~150–200 lines | More than one workflow or more than one visual concern |
| Any file | — | If describing what it does requires "and," split it |

The cap is a prompt to check for mixed concerns, not a hard line to hit before splitting is allowed.

---

## 3. CSS / Design System Breakdown

One CSS file handling light mode, dark mode, mobile, tablet, and desktop simultaneously means every bug ("wrong color in dark mode on mobile") requires scanning the whole file. Split by concern instead:

```
/styles
   /tokens
      colors.css          ← raw values only, no semantics
      typography.css
      spacing.css
   /themes
      light.css           ← semantic tokens mapped for light mode
      dark.css            ← semantic tokens mapped for dark mode
   /breakpoints
      mobile.css
      tablet.css
      desktop.css
   /components
      button.css
      card.css
      nav.css             ← one file per component, no exceptions
   /layout
      grid.css
      containers.css
   index.css               ← imports only, contains no rules itself
```

**Rule:** theme logic, responsive logic, and component logic never share a file. This means a dark-mode-on-mobile bug points you to 2–3 specific files instead of one 2,000-line stylesheet.

---

## 4. Backend Breakdown

- One file = one responsibility: controller, service, repository/data-access, or validator — never combined.
- Standard separation regardless of exact folder names per stack:
  `/controllers` (routing only) → `/services` (business logic) → `/repositories` (data access) → `/validators` (input checks) → `/middleware`.
- If a bug report says "the endpoint returns wrong data," the agent should be able to know which single layer to open first.

---

## 5. Frontend Page/Workflow Breakdown

- A page component composes smaller components — it does not implement multiple workflows inline.
- Rule of thumb: if one page/file handles more than ~2 distinct workflows (e.g. auth *and* data entry *and* reporting all inline), extract each into its own route-level component, hook, or module.
- Goal: when something breaks, you should be able to say "it's the reporting workflow" and go straight to one file, not hunt through a page that does five things at once.

---

## 6. Documentation: What Goes Where

- **`/docs/architecture`** — system design, APSOS/spec-style documents, decision records (why a choice was made, not just what it is).
- **`/docs/schema`** — data models, DB schema, API contracts.
- **`/docs/backend`** — service responsibilities, endpoint inventories.
- **`/docs/frontend`** — component inventory, design-system reference.
- **`/docs/memory`** — progress reports, session summaries, handoff packets (this is what feeds GBEGP Phase 0.3 context ingestion).

---

## 7. Progress Report Triggers

A progress report gets written to `/docs/memory` whenever:
- Any substantial code change lands.
- A new plan or architectural decision is made.
- A mistake is found and corrected (this pairs with the PIVP issue log — the log tracks the issue, the progress report captures the narrative of what happened and why).

**Format (short, not a novel):**
```
Date | Phase
What changed
Why
What's next
Links to relevant files/docs
```

This is what keeps handoffs and session bootstraps cheap — a new agent reads the last few progress reports instead of the whole repo to get oriented.

---

## 8. HTML Documentation Rendering

Docs are *written* in Markdown (git-diff-friendly, easy for agents to edit), but every doc is *read* by you as HTML, styled with your existing design system (midnight blue / electric gold / signal cyan, Bebas Neue / Outfit / JetBrains Mono).

**Rule:** every file in `/docs` gets a companion HTML render, regenerated any time the source `.md` changes — never left stale.

**Setup:** one conversion script, run against any file in `/docs`, wrapping the rendered Markdown in a fixed HTML template that pulls your design-system stylesheet. Output goes to a parallel `/docs/html` mirror (or alongside each `.md`, your call) so the folder structure in Section 6 stays intact.

This script doesn't exist yet as a deliverable — I can build it now (e.g. a small Python or Node script using your existing design tokens) if you want it working today rather than just specified.

---

## Invocation

Starting new work in a repo:
> "Apply RODP before we structure anything."

Writing a new doc:
> "Log this to /docs/[category] per RODP and regenerate the HTML render."

Any substantial change:
> "Drop a progress report per RODP Section 7 before we move on."
