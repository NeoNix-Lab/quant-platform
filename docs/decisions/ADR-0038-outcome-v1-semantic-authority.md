# ADR-0038 — F03 Outcome v1 semantic authority

**Status:** ACCEPTED
**Date:** 2026-09-21

## Context

F03 (`src/quant_platform/research/outcomes.py`) implements `OutcomeSpec` and
`evaluate_outcome`: the canonical forward evaluation horizon declaration and
non-anticipating measurement runtime. PR #90 fixed the original six
adversarial review findings before merge, and PR #101 (commit
`27cbed46f6921e3950f9f60731c828c11a151ba1`) corrected lazy horizon
consumption so a completed outcome stops walking the market series at
`horizon_end` instead of draining an unbounded iterator.

The module's own docstring, however, still marked every output-affecting
semantic choice as "frozen for this PR only -- not governance-authoritative,"
while ADR-0036 (`Outcome-derived labels, censoring and lockbox v1`) already
treats F03 horizon, state, realized value, path measurement, censoring and
causal availability as canonical source semantics that F07 binds to
directly. That is drift between implementation and governance: downstream
authority already relies on semantics that had no accepted authority of
their own.

A second, independent post-#86 adversarial pass against PR #101 additionally
found two runtime defects in how `evaluate_outcome` told genuine stream
termination apart from invalid input and end-of-data:

- `next(iterator, None)` made a real `None` element in `market_series`
  indistinguishable from iterator exhaustion, letting malformed input silently
  become a valid domain state instead of failing closed.
- Plain Python iterator exhaustion before `horizon_end` was treated as proof
  the authoritative market data source had ended (`CENSORED_END_OF_DATA`).
  An arbitrary finite iterable running dry proves only that the caller
  stopped supplying evidence, not that the underlying dataset is complete.

This ADR closes both gaps in one pass: it materializes the accepted F03 v1
semantic authority (Finding C) and freezes the corrected exhaustion/censoring
contract those semantics must reflect (Findings A and B), so the runtime and
its governing authority agree.

## Decision

Adopt the following as the accepted F03 `OutcomeSpec`/`Outcome` v1 semantic
authority. It describes exactly the runtime already implemented in
`src/quant_platform/research/outcomes.py` and proven by
`tests/test_research_outcomes_v1.py`, corrected only where Findings A and B
below require it.

### 1. OutcomeSpec identity

`OutcomeSpec` semantic identity is exactly the tuple `(outcome_key,
semantic_version, horizon_duration, sampling_period, metric_kind,
price_reference)`. No other field (including free-text notes, if ever added)
participates. Two equivalent duration spellings (e.g. `"1m"` and `"60s"`)
collapse to one canonical identity; a duration horizon and a bar-count
horizon of equal nanosecond length never collide.

### 2. Horizon normalization and fixed-grid alignment

`horizon_duration` accepts either a governed unit-suffixed duration spelling
(identical unit vocabulary to `CandleDefinitionV1.duration_ns`) or a positive
Python `int` bar count. `sampling_period` is always a duration spelling. A
duration horizon must be an exact multiple of `sampling_period`, validated at
construction. `horizon_duration_ns` is computed purely from `spec` (a
duration horizon's own length, or a bar-count horizon's count multiplied by
`sampling_period_ns`), never from supplied market data. Both horizon shapes
resolve to the same fixed grid: `sampling_period`-spaced steps forward from
the event, so `evaluate_outcome` walks either shape identically.

`horizon_start` is always the triggering event's own `event_time`.
`horizon_end` is always `event_time + horizon_duration_ns`, computed purely
from `spec` and `event`, independent of the supplied market data.

### 3. Exact boundary and no-overshoot

The first supplied `MarketObservation` must be the anchor: its `instant`
must equal `event.event_time` exactly, or the result is
`INSUFFICIENT_COVERAGE`. From the anchor, each expected grid instant up to
and including `horizon_end` must be matched exactly by the next observation.
A grid instant with no exact match -- whether the series skips past it or
runs dry before reaching it -- is never bridged by reading a later or
approximate price. Evaluation never overshoots past the requested horizon to
the next available observation.

### 4. Outcome state meaning (Finding B correction)

```text
COMPLETE
    every grid instant from the anchor through horizon_end matched exactly

CENSORED_END_OF_DATA
    a grid instant before horizon_end is unmatched
    AND the caller supplied explicit EndOfData evidence at that point

INSUFFICIENT_COVERAGE
    no anchor was established (including an empty series), OR
    a grid instant before horizon_end is unmatched
    AND no explicit EndOfData evidence was supplied there
```

This corrects the runtime's prior behavior, which treated bare Python
iterator exhaustion before `horizon_end` as `CENSORED_END_OF_DATA`. An
arbitrary finite iterable running dry proves only that the caller stopped
supplying evidence; it does not, by itself, prove the authoritative
dataset/source ended. `evaluate_outcome` now requires the caller to supply
`EndOfData` (an immutable, implementation-local marker exported alongside
`MarketObservation`) as the `market_series` element at the point the
authoritative source is known to have ended, in place of the missing
observation. Absent that explicit marker, an exhausted stream before
`horizon_end` is `INSUFFICIENT_COVERAGE`, exactly like an interior gap
(interior-gap detection itself is unchanged: a later `EndOfData` cannot
retroactively excuse a gap that already made the interior path untrustworthy
-- only the first unmatched grid instant governs the outcome).

A non-`COMPLETE` outcome never carries a `realized_value`.

### 5. Iterator exhaustion vs. malformed evidence (Finding A correction)

`evaluate_outcome` detects genuine iterator exhaustion with an
implementation-local sentinel object distinct from every valid domain value,
including `None`. Every consumed `market_series` element that is neither a
`MarketObservation` nor an `EndOfData` marker -- including a literal `None`,
at the first position or any interior position -- raises
`OutcomeEvaluationError`. It is never mistaken for the stream ending or
silently reinterpreted as a domain state.

### 6. Canonical exact-fraction numeric representation

`realized_value` and `PathMetrics` fields are exact canonical fraction
strings (`"<numerator>"` or `"<numerator>/<denominator>"`, lowest terms, no
reducible form, no zero-denominator collapse such as `"0/3"`, no signed zero
`"-0"`), computed with `fractions.Fraction` arithmetic over `Decimal` prices.
No float or rounding error is ever introduced, and every value has exactly
one byte representation.

### 7. Causal-availability rule

`Outcome.causal_available_at` is the later (never earlier) of: `horizon_end`,
`event.causal_available_at`, and every consumed `MarketObservation`'s own
`causal_available_at` (anchor included), plus an explicit `EndOfData`
marker's own `causal_available_at` when supplied. Considering every consumed
evidence source -- not just the boundary observation -- guarantees an
outcome can never become available before any evidence actually used to
compute it. This enforces `causal_available_at >= horizon_end`
unconditionally (ADR-0006), structurally in `Outcome.__post_init__`. An
`EndOfData` marker can only ever push `causal_available_at` forward or leave
it unchanged; it can never pull it earlier than `horizon_end` or any other
consumed evidence, because the evaluator only ever takes a running maximum.

### 8. Metric formulas (`FORWARD_RETURN`, `HIGH_LOW_EXCURSION`, `EXTREMA`)

- `FORWARD_RETURN`: `boundary.price_for(price_reference) /
  anchor.price_for(price_reference) - 1`, exact `Fraction` arithmetic.
- `HIGH_LOW_EXCURSION`: `(max(high over consumed path) - min(low over
  consumed path)) / anchor.price_for(price_reference)`.
- `EXTREMA`: the forward return (relative to the anchor's `price_reference`)
  of the path observation with the largest-magnitude return; ties on `abs()`
  resolve to Python `max`'s first-encountered-wins order (forward
  chronological order of the consumed path).

All three require the full path through `horizon_end` to be established
(`COMPLETE`); an interior gap anywhere in that path -- including within an
excursion/extrema window -- is `INSUFFICIENT_COVERAGE`, never a metric
computed over a partial path.

### 9. PathMetrics MFE/MAE measurement

`PathMetrics.maximum_favorable_excursion` / `maximum_adverse_excursion` are
the max/min of the anchor's own zero return together with every consumed
observation's return relative to the anchor's `price_reference` price. The
anchor's zero return always participates, so a path that only ever moves
against the anchor still reports a favorable excursion of `"0"` (the
event-time price itself was always an available, if trivial, exit point),
and symmetrically for an all-advancing path's adverse excursion.
`PathMetrics` is `None` only when no observation beyond the anchor was ever
consumed.

### 10. Outcome identity

`Outcome.outcome_id` is deterministically derived from `outcome_spec_id`,
`event_id`, `horizon_start` and `horizon_end` only -- never from
`realized_value`, `state` or `path_metrics`. Two evaluations that agree on
spec, event and horizon bounds share one identity regardless of how the
underlying market path was supplied or what it measured. This is the
identity binding ADR-0036 §5 (`Label-result identity binds source evidence`)
already relies on to distinguish two different realized results explicitly
in the *label*-result identity, precisely because the *Outcome* identity
alone does not.

## Corrected: end-of-data evidence contract

This is the one output-affecting behavior change from the runtime state as
merged by PR #101, required by Finding B:

```text
missing required horizon evidence
+ explicit EndOfData evidence supplied at that point
    -> CENSORED_END_OF_DATA

missing required horizon evidence
+ no explicit EndOfData evidence
    -> INSUFFICIENT_COVERAGE
```

`EndOfData` is a narrow, Research-owned, implementation-local evidence seam
(an immutable marker carrying an optional `causal_available_at`), not a
generic coverage/evidence framework, stream abstraction, registry or plugin
system, and not a dependency on DataGateway, files or persistence. It is
supplied as a plain element of the same `market_series` iterable
`evaluate_outcome` already consumes.

## Excluded

This ADR does not authorize or define:

- F04 (`EventStudySpec`/`run_event_study`) or F07 (label/censoring/lockbox)
  redesign; both remain owned by their existing ADRs/modules and are
  credited, not re-proved, here;
- a generic end-of-data/coverage framework applicable beyond F03;
- new Outcome families, path-dependent metrics, barrier/triple-barrier
  semantics, or policy/Strategy/Execution-derived outcomes;
- DataGateway, persistence or source-specific integration inside Research;
- any change to ADR-0006, ADR-0007, ADR-0008 or ADR-0036.

## Consequences

- `src/quant_platform/research/outcomes.py`'s module docstring now points to
  this ADR as the governing authority instead of declaring its design notes
  frozen for a single PR; the docstring text is kept as a synchronized
  summary, not a competing authority.
- `EndOfData` is exported from `quant_platform.research` alongside
  `MarketObservation`.
- `evaluate_outcome` callers that relied on bare iterator/list exhaustion
  producing `CENSORED_END_OF_DATA` now correctly receive
  `INSUFFICIENT_COVERAGE` unless they supply explicit `EndOfData` evidence.
  F04's `run_event_study` does not thread per-event `EndOfData` evidence
  through its shared `market_series` sequence (no call site currently needs
  it), so its own tests that previously exercised bare-exhaustion
  `CENSORED_END_OF_DATA` are corrected to `INSUFFICIENT_COVERAGE` to match;
  `PopulationRecord`/`AggregateMetrics` shape and handling of both states are
  unchanged (§4 above already treats them symmetrically -- no fabricated
  value, counted separately).
- ADR-0036 is unaffected: F03 state/value/support/availability semantics it
  binds to are ratified here exactly as it already assumed, with the
  corrected end-of-data provenance rule folded into what "F03 owns the
  canonical ... `COMPLETE | CENSORED_END_OF_DATA | INSUFFICIENT_COVERAGE`"
  means.

## Acceptance evidence

PR #90's original six findings and PR #101's lazy horizon-consumption
correction are credited, not re-proved. `tests/test_research_outcomes_v1.py`
is extended with the minimum new proof for Findings A and B: a malformed
`None` at the first and an interior position each raise
`OutcomeEvaluationError`; a finite prefix with horizon incomplete and no
`EndOfData` evidence is `INSUFFICIENT_COVERAGE`; the same prefix with
explicit `EndOfData` evidence is `CENSORED_END_OF_DATA`; an `EndOfData`
marker's own `causal_available_at` is proven unable to pull
`Outcome.causal_available_at` earlier than `horizon_end` or other consumed
evidence; an interior gap's `INSUFFICIENT_COVERAGE` state is proven not
overridden by a later `EndOfData`. `tests/test_package_boundaries_v1.py`
package-boundary proof is unchanged and remains PASS.
