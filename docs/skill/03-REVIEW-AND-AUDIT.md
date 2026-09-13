# System Review & Audit Criteria

When reviewing plans, architectures, or diffs, tag and aggressively cut over-engineering. Do not review for style, only for bloat.

For every piece of bloat found, output exactly one line in this format:
`[File]:L[LineNumber]: <tag> <what to cut>. <replacement strategy>.`

You MUST use one of these specific tags:
- `delete:` Dead code, unused flexibility, speculative features, or wrappers that only delegate. Replacement: nothing.
- `stdlib:` A hand-rolled function that the standard library already provides. Name the stdlib function to use instead.
- `native:` A dependency or custom code doing what the browser, OS, or Database already does natively. Name the native feature.
- `yagni:` An abstraction (interface, base class, config object) with only one concrete implementation or caller. Replacement: inline it.
- `shrink:` Verbose logic that can be written in significantly fewer lines. Provide the shorter form.

End every review with a net metric: `net: -<N> lines, -<M> deps possible.`
If the code is already maximally minimal, output exactly: `Lean already. Ship.`
Do not apply fixes during an audit unless explicitly instructed.
