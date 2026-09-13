# The Ponytail System Philosophy
*The best system is the one never built. Lazy means efficient, not careless.*

Before designing, planning, orchestrating, or coding, stop at the first rung of this ladder that holds true. The ladder runs *after* you fully understand the problem space.

1. **YAGNI (You Aren't Gonna Need It):** Does this component, agent, or feature need to exist at all? Speculative need = skip it.
2. **Existing Implementation:** Is this pattern, helper, or logic already in the system? Reuse it. Look before you build.
3. **Standard Library / Native Platform:** Does the language stdlib or native platform feature (e.g., DB constraints, OS features, HTML elements) cover it? Use it.
4. **Existing Dependency:** Does an already-installed, trusted dependency solve it? Use it. Avoid adding new ones for things a few lines can do.
5. **The One-Liner:** Can it be solved with a single line of logic or a basic control flow? Do it.
6. **The Minimum Viable System:** Only if 1-5 fail, write the absolute minimum code/architecture that works.
