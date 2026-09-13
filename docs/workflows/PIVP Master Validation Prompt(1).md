# Post-Implementation Validation Protocol (PIVP)
### Master Agent Prompt — Run After Every Implementation Phase

**Trigger:** Paste this after the agent finishes building any phase of any implementation plan, regardless of stack (web app, mobile app, backend service, trading system, embedded, whatever). This is a gate, not a suggestion — the agent does not advance to the next phase until this protocol completes.

---

## Phase 0 — Context Lock (mandatory, before touching anything)

Before validating, the agent must explicitly restate:

1. **Stack** — language, framework, runtime, package manager in use.
2. **Spec reference** — which plan/spec document and section this phase implements.
3. **Acceptance criteria** — the criteria *as written in the plan*, not invented or inferred.
4. **Out-of-scope items** — what this phase deliberately does not cover, so it isn't wrongly flagged as missing.

**Rule:** If the spec, criteria, or scope is ambiguous or missing, STOP and ask. Do not guess the acceptance bar and then grade against the guess.

---

## Phase 1 — Dead Code & Artifact Sweep

- Search for unused imports, unreferenced functions/classes, orphaned files, stale commented-out blocks, unused config keys/env vars, leftover debug prints/console logs.
- Use the correct tool for the actual stack — name it explicitly (e.g. `ts-prune`/`depcheck` for TS/JS, `vulture` for Python, `cargo-udeps` for Rust, Android Lint's unused-resources check for mobile). Do not default to one tool across all stacks.
- Every removal is logged as a `REMOVED` entry (file + line) in the issue log below — nothing is silently deleted.

---

## Phase 2 — Automated Debug Pass

- Run the project's actual build/compile step. No skipping.
- Run the full existing test suite (unit + integration). Do not mock away a failing test to make it pass.
- Run the linter/type-checker at the strictest configured level.
- For each failure: attempt one automated fix, re-run. If still failing after 2 attempts → log as **Blocker**, halt phase advancement. Do not suppress with a broad try/catch, `@ts-ignore`, or type cast just to get green.

---

## Phase 3 — Code Review

Evidence-based only — paste the actual diff/snippet, never "looks fine, moving on."

- Matches the architectural pattern already established for this project (naming conventions, layering, FK/schema decisions already made in the spec — don't reinvent).
- No hardcoded values that belong in config.
- No exposed secrets, credentials, or unauthenticated endpoints.
- Error handling present on every I/O, network, or external call.
- **Domain-specific risk check** — name the bug class most likely for *this* domain and check for it explicitly (e.g. look-ahead bias in a data/trading pipeline, race conditions in concurrent code, XSS/CSRF in a web form, null-handling in mobile UI, off-by-one in pagination). This must be named, not skipped as "N/A."

---

## Phase 4 — Validation (does it meet the spec?)

- Line up the implementation against each acceptance criterion from Phase 0, one at a time.
- Explicit **PASS / FAIL** per criterion — no aggregate "looks good."
- Any criterion that isn't actually testable as implemented → flagged **UNVERIFIABLE**, never silently passed.

---

## Phase 5 — Verification (does it actually run end-to-end?)

- Execute the real feature path — start the server and hit the endpoint, run the actual mobile build, execute the actual script — not just unit tests in isolation.
- Capture real output/logs as evidence.
- **No-self-certification rule:** the agent cannot mark a phase VERIFIED without pasting the actual command output that demonstrates it worked. "Should work" is not verification.

---

## Phase 6 — Issue Logging

Append-only, persistent across phases (same shape as your VLTHR corrections log).

| Field | Description |
|---|---|
| ID | Sequential |
| Phase | Which implementation phase this came from |
| Severity | Blocker / Critical / Major / Minor / Cosmetic |
| Category | Dead Code / Debug / Review / Validation / Verification |
| Description | What's wrong |
| Evidence | Command output, diff, or log line |
| Status | Open / Fixed / Deferred |
| Owner Phase | Which phase is responsible for the fix if deferred |

---

## Exit Gate

- **Blocker or Critical open → phase is NOT complete.** No advancing.
- **Major** issues: must be either fixed or explicitly deferred with a stated reason and owner phase.
- **Minor/Cosmetic**: logged, non-blocking.
- End with a summary block: issue counts by severity, and gate status (`PASS` / `BLOCKED`).

---

## Invocation

Paste this after any phase completes:

> "Run PIVP on the last phase before proceeding. Do not advance until the gate status is PASS."
