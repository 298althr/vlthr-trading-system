# Phase 1: System Orchestration & Planning

As an agent designing systems or orchestrating architecture, you must prevent architectural bloat before a single line of code is written. 

## The Decision Ladder (Planning Phase)
Before approving or proposing any architectural component, evaluate it against this ladder. Do not proceed until you find the lowest rung that satisfies the immediate requirements.

1. **The 'Do Nothing' Check (YAGNI):** 
   - *Question:* Is this component required for the immediate, specified use case, or is it for a speculative "future" need?
   - *Action:* If it is speculative, reject it. We do not build for "later".

2. **The 'Already Solved' Check:**
   - *Question:* Does a pattern, service, or helper already exist in the codebase that can be reused or slightly modified?
   - *Action:* If yes, reuse it. Do not orchestrate a new service or component.

3. **The 'Platform Native' Check:**
   - *Question:* Can the underlying platform (Database, OS, Cloud Provider native feature, Browser API) handle this natively?
   - *Action:* Offload work to the platform. E.g., use DB `LIMIT/OFFSET` instead of app-level pagination; use DB `UNIQUE` constraints instead of app-level validation.

4. **The 'Minimal Viable Architecture' Check:**
   - *Rule:* No speculative abstractions. Do not create interfaces or base classes unless there are *currently* at least two distinct implementations. Do not create factories for a single product type.
   - *Rule:* Single responsibility, but minimal separation. If a script can do it, don't make it a microservice.

## Designing with Intentional Debt (The Ceiling)
If you deliberately choose a simpler, less scalable approach to save time/complexity, you MUST document the "ceiling" (when this approach will break) and the "upgrade path".
- *Format:* `ponytail: [ceiling constraint], [upgrade path]`
- *Example:* `// ponytail: global memory lock limits throughput to 100/sec, switch to Redis lock when distributed`
