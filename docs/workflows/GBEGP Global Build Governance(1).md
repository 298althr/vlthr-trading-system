# Global Build & Environment Governance Protocol (GBEGP)
### Companion to PIVP — governs everything before and during a phase; PIVP governs after.

**Scope:** Stack-agnostic. Applies whether the agent is building a web app, mobile app, backend service, or trading system, on bare metal or Docker Desktop.

---

## Phase 0 — Session Bootstrap
### Runs at the start of every new conversation with the agent, before a single line of code is written.

**0.1 — Environment Detection**
Agent states explicitly: bare metal or Docker Desktop, OS, runtime/language version, package manager. Do not assume — check for existing config (Dockerfile, package.json, requirements.txt, etc.) and report what's found.

**0.2 — Dev/Prod Separation (mandatory first action on a new project)**
Before anything else, the agent creates or confirms:
```
/development/     ← all active build work happens here
/production/      ← only receives promoted, validated code
.env.example       ← committed, no real values, documents every required key
.env.development   ← gitignored, real dev values
.env.production     ← gitignored, real prod values
```
If this structure already exists, the agent confirms it and does not restructure it without asking. If it doesn't exist, it is created before Phase 1 of any implementation plan begins.

**0.3 — Context Ingestion**
Agent reads (in this order, out loud, briefly): the implementation plan/spec doc, the current issue log (from the last PIVP run, if one exists), any handoff packet (see Section 4). Agent produces a 3–5 line summary of "where this project currently stands" and gets a thumbs-up before proceeding. This replaces re-reading the entire codebase from scratch every session — cheaper on tokens, and it's the actual point of the summary: proof the agent understood, not a formality.

**0.4 — Scope Confirmation**
Agent restates, in plain language, what it understands it's building this session — and nothing more. This is the checkpoint against feature creep before code exists (full detail in Section 3).

---

## 0.5 — Large File / Log Reading Strategy

Applies any time a single doc (progress log, handover packet, spec) exceeds roughly 20–30KB or has clearly accumulated over many sessions.

1. **Index first.** Extract headers/section markers only (e.g. `grep -n "^#"`) to build a line-numbered table of contents before reading any body text.
2. **Read the tail first for rolling logs.** Recent entries are almost always what's needed; older ones matter only if the current task references them.
3. **Targeted read by section/line range**, never the full file, unless the task genuinely requires the complete history.
4. **Compact on a schedule, not on demand.** Once a log crosses its threshold, fold older entries into a single digest/changelog summary. Keep the full raw log as an archive, but day-to-day reading works off the digest — this is what keeps step 1 cheap indefinitely instead of the index itself eventually becoming bulky.

---

## 1. Coding & Writing Standards (the "how to write" rules)

These apply regardless of frontend, backend, or language:

- **YAGNI by default.** Do not write code for a feature that isn't in the current phase's spec, even if it seems like it'll be needed "eventually." Speculative code is the single biggest source of dead code later.
- **No premature abstraction.** Don't build a generic/configurable system for one use case. Abstract on the second or third real repetition, not the first anticipated one.
- **Naming and modularity follow the project's established conventions** (already set in the spec/design system) — the agent does not introduce a competing pattern mid-project.
- **One responsibility per function/module.** If a function needs "and" to describe what it does, split it.
- **Comments explain *why*, not *what*.** Code should be readable enough that a comment restating it is redundant.
- **Token/context economy:**
  - Keep files small enough to be read in one pass — split before a file becomes a scroll-heavy grab-bag.
  - Maintain a running short-form state summary (not a full transcript) that gets updated at the end of each phase, so the agent (or a new one) can re-orient in a few lines instead of re-reading everything.
  - Don't paste entire files into working memory when a targeted diff or function will do.

---

## 2. Feature Scope Discipline (Anti-Bloat / Anti-Hallucination)

This is the layer that protects a non-technical vision-holder from an agent (or the excitement of a build session) quietly turning one dashboard into ten tabs.

**2.1 — Idea → Spec Conversion**
When a new idea/feature is raised, the agent does not start coding. It first restates the idea as a short structured spec: what problem it solves, who uses it, and — critically — **what existing feature it might overlap with.** This gets a go-ahead before any code is written.

**2.2 — Synthesis Check**
Before adding any new UI surface (tab, panel, screen, endpoint), the agent must ask: *can this live inside an existing surface instead of becoming a new one?* Rule of thumb: if a dashboard is trending past 5–6 top-level tabs/sections, that's a signal to consolidate related ones rather than keep adding — group by user goal, not by feature origin.

**2.3 — Boundary Rule**
The agent proposes; the human approves scope. The agent never silently expands scope mid-build because it "seemed like a good addition." Vision comes from the human; structuring and flagging overlap/bloat is the agent's job.

---

## 3. Dev → Production Promotion Workflow

1. All active work happens in `/development` against `.env.development`.
2. A phase is only eligible for promotion once it has passed PIVP's exit gate (no open Blocker/Critical).
3. Promotion is a deliberate copy/PR step, never automatic:
   - Copy validated code from `/development` to `/production`.
   - Confirm `.env.production` has every key documented in `.env.example` — nothing dev-only leaks in (API mocks, debug flags, local URLs).
   - Push `/production` to its own branch → PR into `main` on GitHub. Development branches never merge straight to `main` without this step.
4. Any environment-specific behavior is driven entirely by which `.env` file is loaded — never by hardcoded `if (dev)` branches scattered through the code.

---

## 4. Agent Handoff Protocol (when one agent/session takes over from another)

The incoming agent runs **Phase 0 in full** and additionally requires a handoff packet containing:

| Field | Content |
|---|---|
| Current phase | Where the plan currently stands |
| Last PIVP result | Gate status and any deferred issues |
| Decisions already made | So the new agent doesn't relitigate settled architecture |
| Do-not-touch list | Anything intentionally left alone (e.g. a working integration not to be "improved") |
| Open questions | Anything the prior agent flagged but didn't resolve |

**Rule:** the incoming agent does not override a prior decision it disagrees with — it flags it for the human to decide, the same way it would flag a new-feature scope question. Past work is not automatically correct, but it isn't fair game for silent rewriting either.

---

## 5. Full Lifecycle Loop (how GBEGP and PIVP fit together)

```
New session
   → Phase 0 Bootstrap (env check, dev/prod confirm, context ingestion, scope confirm)
   → Build the phase (Sections 1–2 rules apply throughout)
   → PIVP (dead code sweep → debug → review → validate → verify → issue log)
   → Gate PASS?
        NO  → fix Blockers/Criticals, re-run PIVP
        YES → Section 3 promotion checklist → push to production branch
   → Next phase or handoff (Section 4 if a new agent is taking over)
```

---

## Invocation

Start of a new session or new project:
> "Run GBEGP Phase 0 before we start."

Mid-project, new agent taking over:
> "This is a handoff. Run GBEGP Phase 0 and read the handoff packet before touching anything."

After a phase is built:
> "Run PIVP, then check GBEGP Section 3 for promotion eligibility."
