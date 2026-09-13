# Content & Communication Protocol (CCP)
### Fourth companion to GBEGP, RODP, and PIVP — governs writing style, how code gets written in sequence, and how handoffs get read.

---

## 1. Front-End / UI Copy Rules

Copy is precise and direct. If it can be cut without losing meaning, cut it.

- **No em dashes.** Use a period, comma, or restructure the sentence.
- **No AI-slop phrasing.** Banned by default: "seamless," "robust," "unlock," "elevate," "leverage" (as a verb), "dive in," "game-changing," "cutting-edge," "empower," "in today's fast-paced world," "supercharge."
- **No sci-fi/tech jargon dressing.** Say the plain action, not a futuristic-sounding metaphor for it. "Manage your tasks," not "orchestrate your workflow."
- **No overexplaining.** State the fact or action once. Do not restate it in different words for emphasis.
- **Lead with the action.** A button says what it does ("Save changes"), not what journey it starts ("Get Started on Your Journey").
- **One idea per sentence.** If a UI string needs "and" to describe two things, it's two strings or two states, not one.

---

## 2. Documentation & Chat Writing Rules

Same rules apply to progress reports, handoff docs, and any agent-to-human writing — not just UI copy.

- Active voice, concrete nouns and verbs.
- State the conclusion first, then only the supporting detail actually needed.
- No narrating the process ("I then went through and checked each file..."). State the finding, not the journey.
- Padding sentences that restate something already said are cut on sight.

---

## 3. Code-Writing Workflow: Build in Units

Code is never generated as one giant pass across a whole file or page. Sequence:

1. **Define the interface first** — name, inputs, outputs, one function/component at a time — and confirm it matches the spec before writing the body.
2. **Write the unit** (one function, one component).
3. **Validate that unit** before moving to the next (this is where PIVP's debug/review steps apply at the unit level, not just at phase-end).
4. **Integrate** the unit into its module.
5. **Module-level check** once all units in that module are in and validated.
6. Repeat for the next module.

This is deliberate, not just careful — smaller units are cheaper to hold in context, faster to debug, and map directly onto RODP's one-concern-per-file rule. A page is built component by component, not as a full tree dumped in one shot.

---

## 4. Writing a Handover (writer's side)

Assume the reader — human or the next agent — has **zero standing context** beyond what's in this document plus whatever it links to.

Order, front-loaded:

1. **Status / gate first** — what state is this in right now (mirrors PIVP's PASS/BLOCKED gate).
2. **Decisions made** — what was decided and why, briefly.
3. **Open items** — what's unresolved, what's deferred and why.
4. **Evidence / links last** — source files, logs, test output, for anyone who needs to go deeper.

No process narration. No re-explaining things already covered in a linked doc — reference it instead of restating it.

---

## 5. Reading a Handover (reader's side — for the agent taking over)

When an incoming agent reads a handoff or progress report (per GBEGP Section 4), it reads in this fixed order:

1. Current status/gate
2. Open issues
3. Decisions already made
4. Do-not-touch list
5. Only then, if something is still unclear, go into the linked source files

**Rule:** don't re-derive a conclusion the document already states. Trust logged decisions unless something concretely contradicts them — and if it does, flag it rather than silently overriding it, same as GBEGP's handoff rule.

---

## Style Cheat Sheet

| Don't | Do |
|---|---|
| Em dashes | Period or comma |
| "Seamless," "robust," "unlock," "elevate," "leverage," "dive in" | The plain verb for what's actually happening |
| Restating a point for emphasis | Say it once |
| Metaphor dressing ("orchestrate your workflow") | Plain action ("manage tasks") |
| Narrating the process | Stating the finding |
| Front-loading caveats and setup | Front-loading the conclusion |

---

## Invocation

Writing any UI copy, doc, or handoff:
> "Apply CCP before finalizing this text."

Starting a new function/component:
> "Follow CCP Section 3 — one unit at a time, validate before the next."

Reading a handoff as a new/returning agent:
> "Read this per CCP Section 5 before touching anything."
