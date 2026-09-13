# The Safety Invariants (Non-Negotiable)

"Lazy" means efficient and minimal, never careless. You are strictly forbidden from simplifying or removing the following critical system boundaries. 

## 1. Trust Boundaries
You must never skip input validation at a trust boundary (API endpoints, user inputs, external file reads).
- **Rule:** Sanitize, validate type, and bound-check all external inputs before processing.

## 2. Data Integrity
You must never skip error handling that prevents data corruption or data loss.
- **Rule:** Wrap database transactions, file writes, and state mutations in proper try/catch/finally or equivalent rollback blocks.
- **Rule:** Do not swallow exceptions silently (`except pass` or `catch(e) {}`). Log them or throw them up the stack.

## 3. Security
You must never simplify away authentication, authorization, or cryptographic standards.
- **Rule:** Do not hardcode secrets to "save time".
- **Rule:** Do not downgrade hashing algorithms (e.g., from Argon2 to MD5) for "simplicity".

## 4. The Runnable Check
Lazy code without a check is unfinished code.
- **Rule:** Any non-trivial logic you write MUST be accompanied by exactly ONE minimal runnable check (e.g., a simple `assert`-based script or a single test file). 
- **Constraint:** Do not add third-party testing frameworks or their fixtures/plugins (no Jest, PyTest plugins, Mocha, etc.) unless one is already established in the repo. Use the language's built-in test runner and assertions: native `node:test` + `assert`, or Python's stdlib `unittest` / plain `assert` scripts.
