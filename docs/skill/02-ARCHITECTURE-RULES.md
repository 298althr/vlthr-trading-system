# Architecture, Planning, and Decision Rules

- **Zero Speculative Abstractions:** No interfaces with a single implementation. No factories for one product. No configuration layers for values that never change.
- **Deletion over Addition:** The shortest path to done is the right path. Boring over clever. Clever is what someone decodes at 3am. Keep the fewest files, agents, and moving parts possible.
- **Root Cause, not Symptom:** When fixing or orchestrating, fix the shared root component once. Do not patch symptoms across multiple callers.
- **The Ceiling Comment:** If you take a deliberate architectural shortcut, document it inline with `// ponytail:` (or `# ponytail:`). State the known ceiling (e.g., global lock) and the exact upgrade path (e.g., per-user lock). Simple reads as intent, not ignorance.
- **Safety is Never Lazy:** Never simplify away:
  - Input validation at trust boundaries.
  - Error handling that prevents data loss.
  - Security measures and accessibility.
  - The single runnable check (tests/asserts) for non-trivial logic.
