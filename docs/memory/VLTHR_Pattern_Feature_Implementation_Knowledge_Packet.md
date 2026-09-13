# Pattern Recognition & Feature Engineering — Implementation Knowledge Packet
## For: VLTHR Development Agents (Phases 1–4, Pattern/Feature Spec v1.0)

This document exists to answer one question before you write a single pattern function: **what does the actual evidence say about how professional traders and quant researchers use each technique, and how do you encode that correctly in Python without it breaking in live conditions?** Every pattern in the spec traces back to a real methodology (Bulkowski's statistical chart-pattern research, Wyckoff, VSA, or Smart Money Concepts). Each has a different evidence quality, a different intended use, and a different failure mode when translated into code carelessly. Treat this as the reference to check before implementing each category, not a one-time read.

---

## 1. The Single Most Important Rule: Evidence Tiers

Not all 30 patterns carry the same weight of evidence. Treat them accordingly — this determines how much confluence/confirmation you require before a pattern's `detected` flag is allowed to influence a trade, and it should directly inform the IC threshold discussion in Phase 5.

| Tier | Category | Evidence basis | How to treat it in code |
|---|---|---|---|
| **1 — Statistically documented** | Wedges, triangles, channels, H&S, double top/bottom | Thomas Bulkowski's *Encyclopedia of Chart Patterns* is the most extensive statistical study of pattern reliability published, built from tens of thousands of measured patterns. His data quantifies specific failure rates per pattern — e.g. inverse head and shoulders around an 11% failure rate, and confirms that breakouts without volume confirmation fail at close to double the rate of confirmed ones. | These patterns can carry meaningful standalone weight in `confluence_score`, but only when implemented with the same conditions Bulkowski measured: clear pivot structure, volume confirmation on breakout, and a defined measured-move target. Do not treat a "detected" wedge as equally reliable to a confirmed one — track both. |
| **2 — Mechanistically grounded, weaker standalone stats** | Candlestick patterns | This is the one category where the academic literature is genuinely split. Multiple stock-market studies find some bullish reversal candles profitable after transaction costs, particularly in Taiwanese and Chinese markets, while other studies (Horton 2009; Marshall, Young & Cahan 2008; Marshall, Young & Rose 2007) find no profitability in U.S. and Japanese markets. Even studies that do find profitability report it is small — one independent backtest found average PnL under 1% over a 20-day holding period for the best-performing candles, and bearish patterns broadly failed to show any edge. | **Do not let candlestick patterns act alone.** Weight them as confirmation/context inputs only — never as a standalone entry trigger. This is the category most likely to fail your IC filter (|IC| ≥ 0.02) on its own; expect several candlestick fields to get cut in Phase 5, and don't be surprised or fight it. |
| **3 — Framework-level, requires full-sequence confirmation** | Wyckoff phases, VSA (effort vs. result, no-demand/no-supply) | Wyckoff and VSA are not single-bar signals — they are entire multi-bar sequences whose value comes from the sequence, not any one event in isolation. A Spring only means something in the context of a prior trading range with declining volume through Phase B; a single "high volume, narrow spread, close near high" bar in isolation is just one candle. Professional practitioners are explicit that a test is only valid when read against what came before it in the same structure. | Implement these as **stateful, multi-bar sequence detectors**, not point-in-time flags. A Wyckoff phase classification should depend on the accumulated evidence across the whole trading range (volume trend across tests, narrowing range, count of secondary tests), matching the confidence-threshold approach already scoped (Phase C gets 3x weight, Phase B gets 0.5x — this is directionally correct and should stay). |
| **4 — Popular but empirically thin** | Market structure / Smart Money Concepts (BOS, CHoCH, order blocks, FVG) | SMC/ICT terminology (Break of Structure, Change of Character) is, per multiple independent reviews, essentially a relabeling of classical swing-high/swing-low price action and Wyckoff's spring/upthrust concept under new vocabulary — "the structural logic is identical; only the vocabulary differs." Critics specifically flag the lack of concrete, published statistical evidence behind SMC claims, in contrast to Bulkowski's data-backed pattern catalog. The few available backtests are informal (single-asset, short window) rather than peer-reviewed. | Treat BOS/CHoCH as **structural context** (what regime is price currently in?) rather than as a novel edge-bearing signal. This is exactly why the spec's plan to feed these into regime-style classification (rather than a standalone entry signal) is the right call — it's using the concept for what it's actually good at (describing structure) rather than what it's marketed as (predicting institutional footprints). |

**Practical instruction for implementation:** when you're deciding how much weight a new pattern field should get before IC analysis runs, default to its tier above. Tier 1 patterns can be trusted somewhat ahead of data; Tier 2–4 patterns should be assumed near-zero-weight until the IC analysis proves otherwise. This isn't pessimism — it's exactly the same discipline the spec already applies with its |IC| ≥ 0.02 admission threshold, just made explicit per category so you don't waste implementation time being surprised when candlestick or SMC fields get cut.

---

## 2. What "Exactly As Intended" Means Per Category

### 2.1 Trend lines, triangles, wedges, channels (Bulkowski-tier)
**How professionals actually use them:** the pattern itself is not the signal — the *breakout with volume confirmation* is. Bulkowski's own methodology treats the pattern as a setup and the breakout as the trigger, and his statistics are explicitly broken out by breakout direction because a pattern's failure rate is meaningless without knowing which way it broke. The standard professional target-setting method is the **measured move**: take the pattern's height (widest point) and project that distance from the breakout point in the breakout direction — this is a floor target, not a ceiling; strong trends exceed it.

**Implementation implications:**
- Never fire a signal on pattern detection alone. `wedge_detected = True` is a *setup* field, not an entry field. The entry field should be `wedge_breakout_confirmed` (close beyond the boundary + volume ≥ a defined multiple of recent average).
- Compute and store the measured-move target as its own field (`wedge_measured_move_target`) — this becomes a natural feature for take-profit logic and Phase 4 conditional features, and gives you a way to check pattern *quality* against outcome (did price exceed, meet, or fail to reach the target?).
- The R-value threshold used in the spec (0.9 in the referenced `zeta-zetra` implementation, 0.8 in your S/R and channel logic) is doing real work — it's the objective proxy for "how clean is this pattern," which correlates with Bulkowski's finding that tight, well-formed bases outperform messy ones. Do not loosen these thresholds to get more detections; that directly trades away the quality signal for pattern count.

### 2.2 Support / resistance clustering
**How professionals actually use it:** S/R is a *level*, not a single price — professional technicians reason in zones (a tolerance band), not exact prices, precisely because multiple participants' orders cluster near but not exactly at the same price. The multi-touch requirement (3+ touches) exists because a level only "means" something once multiple independent tests have respected it; a single touch is not evidence of a level.

**Implementation implications:**
- The tolerance-based clustering approach in the spec (cluster pivots within 0.5 ATR) is the correct approach, matching independent open-source implementations of the same technique.
- Recency matters more than professionals usually state explicitly, but institutional practice implicitly weights it: a level tested last week is more actionable than one tested 6 months ago whose surrounding structure has changed. Add a recency decay to `sr_levels` strength (e.g., weight touches by inverse age in bars) — this is a cheap addition that should meaningfully improve IC without adding a new pattern category.

### 2.3 Candlestick patterns
**How professionals actually use them (correctly, when they work at all):** the professional discipline that shows up in every study that *did* find profitability is **conditioning on prior trend and market state** — the studies that found no edge tested patterns in isolation; the ones that found a modest edge conditioned on trend direction, overbought/oversold state, or added volume filtering. One direct comparison found that filtering bearish candlestick patterns by a stochastic indicator still failed to produce edge — meaning simple indicator-conditioning doesn't rescue every candle type, only some.

**Implementation implications:**
- Never register a candlestick pattern's `detected` flag without also computing the preceding trend context (was there a genuine prior trend for this to be a "reversal" of?). A hammer after three sideways bars is not the same signal as a hammer after a five-bar decline; encode `candle_prior_trend_bars` and `candle_prior_trend_strength` alongside every candlestick detection so the IC analysis in Phase 5 can actually test the conditional hypothesis, not just the unconditional one.
- Expect these to be your weakest-performing feature category on IC. Budget time accordingly — don't over-invest in exotic candlestick variants (e.g. all 44+ patterns from the referenced `price-action-lib`); implement the handful with the best documented conditional evidence (hammer/shooting star, engulfing, piercing/dark cloud) well, rather than a large number shallowly.

### 2.4 Wyckoff phases and VSA (effort vs. result)
**How professionals actually use them:** this is the category where "use it exactly as intended" matters most, because the entire method is sequence-dependent. The core VSA law is **effort vs. result**: volume (effort) should match price movement (result); when it doesn't — high volume with a narrow spread, or a wide spread with disappointing follow-through — that divergence is the signal, not any single price level. Wyckoff's Spring/Upthrust only qualifies as such in the context of a prior trading range with the specific volume signature Wyckoff practitioners describe: wide swings and high volume early in the range, progressively diminishing volume on tests as supply/demand gets absorbed, and a final low-volume test that fails to make meaningful further progress.

**Implementation implications — this is the part most likely to break if rushed:**
- **Do not implement Wyckoff phase detection as a single-bar classifier.** It must track state across the whole trading range: (a) an established range with defined support/resistance, (b) a declining-volume trend across successive tests within that range, (c) a test (Spring/UTAD) that pierces the range boundary and reverses within a small number of bars, and (d) confirmation via a follow-through test (LPS/LPSY) on visibly lower volume than the Spring itself. Encode this as a small state machine per symbol, not a stateless per-bar function — this is the single biggest implementation risk in the whole spec, more so than any lookahead-bias risk, because a stateless approximation will produce something that looks like Wyckoff phase detection but doesn't actually encode the sequence dependency that gives the method its (claimed) edge.
- For VSA specifically: implement `effort_result_divergence` as a continuous feature (a ratio or z-scored discrepancy between normalized volume and normalized spread) rather than a binary flag — this preserves the graded nature of "how much divergence" for the IC analysis, rather than collapsing it into a yes/no that discards information.
- Confidence-weight by structural position exactly as scoped: a test late in a mature range (post multiple prior tests with declining volume) is qualitatively different evidence than the same volume/spread pattern appearing with no prior range context. The spec's Phase C 3x weight / Phase B 0.5x weight decision is consistent with this — keep it, and extend the same logic to VSA signals (a "no demand" bar means much more inside an established Wyckoff Phase B than as an isolated event).

### 2.5 Market structure / Smart Money Concepts (BOS, CHoCH)
**How professionals actually use them — and the honest caveat:** independent reviews of SMC/ICT are explicit that this is largely a rebranding of classical swing high/low price action with new terminology, and that the "smart money footprint" framing lacks the kind of statistical backing Bulkowski's work provides for classical patterns. Where it is useful is as a **structural description of the current trend state** — BOS = trend continuation confirmed, CHoCH = trend structure broken, an early warning that the prevailing directional bias may be ending. That's a legitimate, if unglamorous, use: state description, not alpha generation.

**Implementation implications:**
- Frame `BoS`/`CHoCH` fields explicitly as *state* features (what is the current swing-structure trend?), not as standalone entry triggers. This is precisely the raw material Phase 2 of the regime-aware rearchitecture needs — a `CHoCH` detection is a legitimate, cheap-to-compute regime-transition candidate feature to feed the HMM-based regime classifier we discussed for session/regime work, rather than a signal in its own right.
- Because the swing-detection underneath BOS/CHoCH is just pivot high/low tracking (the same primitive used for S/R and trend lines), implement it as one shared pivot-detection utility, not a separate parallel implementation — this also directly satisfies risk R6 in the spec (pattern co-occurrence/redundancy) by construction, since BOS/CHoCH will now be visibly derived from the same underlying pivots as your S/R and trend-line patterns instead of silently correlating with them.

---

## 3. Cross-Cutting Implementation Rules (Apply To Every Pattern/Feature)

These are the rules that make the difference between "looks right in backtest" and "works live" — most of this maps directly onto risks R1, R3, R5, R6 already flagged in the spec, restated here as concrete coding discipline.

### 3.1 Look-ahead bias — the single most dangerous bug class
Every pattern function must receive **only** `df.iloc[:idx+1]` — never the full frame. The specific danger with pivot detection (fractal method, e.g. `left=3, right=3`) is that a pivot is only *confirmed* 3 bars after it visually forms; if your pattern function is allowed to see those 3 future bars during backtest but can't see them live, your backtest win rate will be systematically inflated in a way that silently vanishes in live trading. This is precisely the R1 risk already flagged — treat the unit test requirement (shift the DataFrame, verify outputs for confirmed bars are stable) as non-negotiable, not optional coverage.

### 3.2 Volume/breakout confirmation is not optional
Across every tier above — Bulkowski, VSA, and even SMC — the same finding recurs: **the raw pattern boundary crossing is not the signal; confirmed follow-through is.** Naive breakout detectors that fire on any close beyond a boundary get flushed by exactly this failure mode; professional breakout systems classify a raw boundary break as PENDING and only confirm TRUE BREAK status after a decision window (a small number of bars) with volume and momentum alignment, treating anything that reverts inside the structure within that window as a FAKEOUT. Implement this as a two-stage state machine for every breakout-style pattern (wedge, triangle, channel, trend-line break, Wyckoff Spring/UTAD): `raw_break_detected` → `confirmed_break` only after volume + no-reversion checks pass within N bars.

### 3.3 NaN handling
Every pattern/feature function must return `0.0` or an explicit neutral value for insufficient warmup data — never `NaN`. This is R5 in the spec and it's correct; a NaN silently propagating into a downstream weighted score (DQS or any composite) can zero out or corrupt an entire score rather than just neutrally contributing nothing. Add the post-computation NaN check as an automated pipeline assertion, not a manual review step.

### 3.4 Redundancy control (pattern co-occurrence)
Because trend-line, S/R, and structure (BOS/CHoCH) patterns are all built from the same pivot primitive, several of your 30 patterns will be highly correlated by construction — this is R6. Build the co-occurrence matrix as a first-class diagnostic output as soon as Phase 1-3 patterns exist (don't wait until Phase 4 is "done" to look at it), and be willing to drop or merge patterns showing >80% co-occurrence before they ever reach the IC-analysis gate in Phase 5 — a redundant pattern doesn't just waste compute, it silently gives correlated evidence extra vote-weight in any confluence score.

### 3.5 Latency (R4)
Vectorize with numpy/pandas wherever a pattern computes over a rolling window (pivot detection, linear regression on pivots, clustering) rather than looping bar-by-bar in Python. Cache the shared pivot-detection pass once per scan and pass the result into every pattern/feature function that needs it, rather than recomputing pivots inside each of the 30 pattern functions independently — this is both a performance requirement (R4's 50ms/symbol budget) and a redundancy-control measure (3.4), since a single shared pivot cache guarantees every downstream pattern is reasoning from the identical structural read of the chart.

### 3.6 Symbol-specific failure (R7)
Patterns validated on BTC's liquidity/volatility profile will not transfer uniformly to lower-liquidity symbols (XRP, SOL) — this is expected, not a bug to chase. Wire the per-symbol consistency gate (Gate 5) to actually disable individual pattern fields per-symbol via config, exactly as scoped, rather than treating "works differently per symbol" as something to fix at the pattern-logic level.

---

## 4. Quick-Reference Checklist Per Pattern Function

Before marking any pattern or feature function "done," confirm all of the following:

- [ ] Receives only historical data up to and including current bar (`iloc[:idx+1]`) — no future leakage
- [ ] Returns `0.0`/neutral, never `NaN`, when insufficient bars for warmup
- [ ] Uses the shared pivot-detection cache rather than recomputing pivots independently
- [ ] Distinguishes raw detection from confirmed signal (volume/follow-through gate) if it's a breakout-style pattern
- [ ] Outputs a continuous strength/confidence value, not just a boolean, wherever the underlying evidence is graded (VSA divergence, Wyckoff phase confidence, r-value quality)
- [ ] Tagged with its evidence tier (§1) so downstream weighting and IC-review expectations are calibrated correctly
- [ ] Unit tested against a shifted DataFrame to confirm no look-ahead bias
- [ ] Logged as `pat_`/`feat_`-prefixed column only, per the do-not-touch boundary — no modification to DQS, `v2_filters.py`, or `portfolio_config.py` in Phases 1–4

---

## Key Sources
- Thomas Bulkowski, *Encyclopedia of Chart Patterns* — statistical pattern reliability, failure rates, measured-move methodology
- Wyckoff Analytics; TradingCenter.org — Wyckoff phase structure, Spring/UTAD mechanics, volume-through-range sequencing
- Tom Williams, *Master the Markets*; VSA methodology sources — effort vs. result, no-demand/no-supply bar logic
- Academic candlestick profitability literature (Taiwan/Chinese/SSE50 market studies; Marshall, Young & Cahan 2008; Horton 2009) — mixed and context-dependent findings on candlestick predictive power
- Independent SMC/ICT reviews (AlgoStorm, FundLabz, Strike.money) — evidentiary status of Smart Money Concepts relative to classical price action
- TradingView breakout-classifier implementations (BREAKRUNE, WillyAlgoTrader) — two-stage raw-break/confirmed-break architecture as industry practice
- Open-source references already cataloged in spec v1.0 §6 (zeta-zetra, PatternPy, pymarket-structure, price-action-lib, marketflow, srl-python-indicators)
