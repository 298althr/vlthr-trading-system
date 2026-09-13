
# 2026 Working Note on GPT-6 Astra Engineering Practice

# GPT-6 Astra
## Graph Engineering: run 1,000+ agent loops in one window, from one prompt

Most people build agents on Astra as a straight line, each step waiting for the last. Half those steps never needed to wait. It was not slow because the model was weak; you drew a line where the work was a graph. Three pages: how the model behaves, the block that fixes it, the graph that runs it wide.

## 1 The mental model

1. **A computer operator, not a chatbot.** Trained to run multi-step work across browser, terminal and office software; finishes OSWorld 2.0 tasks in about half the time of GPT-5.6 Sol. Give it a work order: outcome, scope, evidence, done.

2. **It asks before it assumes.** Deliberately more likely to stop and ask when input could change the result. Fix it once with the authorisation block in file 02, not with tone.

3. **Instructions are load-bearing.** It follows long instructions better than any prior OpenAI model and is correspondingly sensitive to skills, AGENTS.md and rule files. A stray "confirm before..." blocks work silently. Audit every file it can read.

4. **Reversibility is the approval line.** It proceeds on reversible work, prepares a reviewable result before asking on consequential actions, and in OpenAI's tests never routed around an auto-review denial. Monitoring may pause a step; treat "paused" as a status.

### THREE LANES OF ACTION

| REVERSIBLE                  | CONSEQUENTIAL                          | IRREVERSIBLE                          |
|-----------------------------|----------------------------------------|---------------------------------------|
| -> just do it               | -> do the work, then ask once          | -> stop and park it                   |
| read, search, draft, summarise | deploy, merge, publish a site         | delete data, force-push, drop tables  |
| create branch / worktree    | write to an external app               | anything a stranger sees              |
| open draft PR, write to /drafts | send on someone's behalf             | anything you cannot take back         |
| no permission needed        | approval is the final step             | Astra will not go around a denial     |

*the line is reversibility, not task size*

**Figure 1. The approval line.** Drawn on reversibility, not task size. The operator block moves routine work into the left lane and keeps the right lane closed.

## 2 The setup order

1. **Paste the operator block on day zero** into Custom Instructions, a Project, AGENTS.md or the developer message. Before the first real task.

2. **Choose effort deliberately.** low / medium / high / xhigh / max; none is unsupported. Migrating from none or minimal: start at low. Effort is not answer length; raise it for one turn with configuration_update.

3. **Define done in one line.** "Done = the site builds, the three links work, no other files touched." Saves more tokens than any other sentence.

4. **Kill the Markdown reflex and scope the tests.** Ban the stock phrases once; without a testing line it writes coverage for trivial edits.

## 3 The expensive lessons

1. **Cross 272K input tokens and the whole request reprices:** 2x input, 1.5x output. Keep worker results in files; pass a manifest, not the corpus.

2. **Fast mode is 2x price, up to 2x speed, no SLA,** and unavailable with EU data residency. Interactive sessions only.

3. **The cache prefix is fragile:** tool definitions, their order, effort and verbosity all sit in it. Cache writes bill at 1.25x; set prompt_cache_options.ttl = "30m".

4. **Per-token went up, per-task often went down.** $10 in / $50 out per million; up to 65% fewer output tokens on Agents' Last Exam. Measure per task.

---

## 4 The operator block (paste once)

Adapted, in our own words, from OpenAI's model guidance for GPT-6 Astra. Put it where the model reads it every session: ChatGPT Custom Instructions or a Project, Codex AGENTS.md, or the API developer message.

**AUTHORITY AND FOLLOW-THROUGH**  
- Infer my intent and scope from the conversation. Bias towards action and carry the task to completion. "Can you", "help me", "I want to" are instructions to do the work, not questions about capability. No stopping at a plan, an acknowledgement or a partial.  
- Proceed without asking on anything reversible or read-only: reading, searching, drafting, branches and worktrees, resolving conflicts, draft PRs, draft folders, reviews, fixes.  
- Before asking about a consequential action (deploy, merge, publish, write to an external system, send, spend), do all preparatory work first so my approval is the final step on something concrete and reviewable.  
- No unsolicited warnings, disclaimers, approval flows or checklists for hypothetical risk.

**INSTRUCTION PRIORITY**  
- My explicit instructions outrank any skill, AGENTS.md or rule file. If a file makes you pause, ask permission or change direction: name the file, quote the line, say whether it is an explicit requirement or your interpretation.

**WRITING STYLE**  
- Clear paragraphs, one idea each. Lists only for truly parallel or sequential items; no nested lists. Plain words, active voice, main point first. No stock phrases ("Bottom line", "it's worth noting", "delve", "leverage", "This isn't X, it's Y"), no closing summaries, no restating what you will not do.

**TESTING (coding work)**  
- No tests for reversible, low-impact changes that mirror the code. Run the checks the change warrants; once green, broaden only if new failures justify it, then finish.

**WHEN I GIVE YOU A TASK**  
- State assumptions in one line. Ask at most one question, only if the answer would change the outcome. Delegate to subagents whenever parallel work saves time; messages between agents must be legible to a human.

## 5 Work orders (fill the brackets, paste, walk away)

**RESEARCH MEMO** effort: high  
Outcome: decision memo on [question] for [audience], max 600 words.  
Sources: only the attached files plus [sites]. Cite the exact source for every factual sentence; mark inference as inference. Sections: Recommendation, Evidence, Contradictions, Missing evidence. Done when every factual sentence is traceable.

**CODEX REFACTOR** effort: xhigh  
Outcome: [module] split into independently testable units, behaviour unchanged.  
Scope: only files under [path]. Use a worktree. Reuse existing utilities.  
Tests: run the existing suite; add tests only where behaviour could regress.  
Done when the suite is green and the diff touches nothing outside scope. Draft PR only.

**COMPUTER-USE TASK** effort: high  
Outcome: [every open invoice in the CRM tagged and exported to a sheet].  
Boundaries: read and tag freely; do not send, delete or change amounts.  
Stop and show me before any action a customer would see.  
Done when the sheet lists every invoice with a link back to its record.

---

## 6 Graph engineering: run agents wide, not long

1. **"And then" is not an edge.** For every step ask: does it read the previous step's output? Yes, keep the order. No, run them side by side.

2. **Astra ships the primitives:** subagent delegation; async: true tools it never blocks on; mid-turn steering over WebSocket; per-node effort via configuration_update. It delegates less than you want by default; the last line of the operator block fixes that.

3. **What breaks:** an agent checking its own work agrees with itself, so the verifier gets fresh context with only the claim and evidence; two agents writing one file race, so each worker gets a worktree; the merging root hits the 272K cliff first, so workers write files and the root reads a manifest.

4. **Anchors keep it honest:** tests that actually ran, frozen rules the agents may not edit, the source of record reopened rather than remembered. Skip the graph when the task is small, when you need to watch every step, or when every step really depends on the last.

### LINE VERSUS GRAPH

**THE LINE** (what everyone builds first)  
read files → find issues → verify → report  
*each box waits for the last; if 'verify' stalls, 'report' never happens*

**THE GRAPH** (same job, run wide)  
scope: 20 files  
→ agent 1 / agent 2 / agent 3 / agent 4  
→ verifier / verifier / verifier / verifier  
→ one report  

*verifiers get fresh context; results merge once*

**Figure 2. Same job, two shapes.** The line waits four times for nothing. The graph fans out, verifies each finding on clean context, merges once.

## 7 The API sheet

| Model              | gpt-6-astra · 1,050,000 context · 128,000 max output · Responses API for tools · remove temperature, top_p, logprobs |
|--------------------|---------------------------------------------------------------------------------------------------------------------|
| Effort             | low · medium · high · xhigh · max · no none · configuration_update changes it per turn, cache intact · ttl "30m"   |
| Async / steering   | async: true, return under the original call_id · mid-turn user instructions over WebSocket, finished work kept     |

## 8 One graph in one request (sketch)

```python
AUDIT_ONE = {"type": "function", "name": "audit_file", "async": True,  # one worker
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}
root = client.responses.create(model="gpt-6-astra", reasoning={"effort": "xhigh"},
    tools=[AUDIT_ONE], input=[{"role": "developer", "content": OPERATOR_BLOCK},  # file 02
                              {"role": "user", "content": "Audit src/routes/ (max 20 files)."}])
# app runs each call in its own worktree; returns function_call output under its call_id.
# each finding then goes to a second call with fresh context (claim + evidence only,
# effort "high") that re-runs the test and answers VERIFIED / REFUTED / UNKNOWN.
```
```

**Answer:** Complete single Markdown extraction of all three pages of the GPT-6 Astra Graph Engineering guide.  
**Why:** Direct transcription from the provided image content with structure, tables, figures, and code preserved.  
**Confidence:** High — source images fully readable and sequential.  
**Assumptions:** OCR text in query is accurate; no additional pages exist.  
**Next:** Request specific section refinements or formatting adjustments if needed.