# ADR-0031 - Availability/purge/embargo v1

**Status:** ACCEPTED

**Date:** 2026-09-16

## Context

ADR-0006 makes temporal availability a domain contract: no decision may use
information that was not available by decision time. F05 already defines
deterministic expanding half-open walk-forward train/test folds
(`quant_platform.validation.walk_forward`). E02 (ADR-0026) already defines
FeatureDefinition support/reference semantics, the causal availability floor,
`AVAILABLE | FINAL_ONLY` input maturity, `PROVISIONAL -> FINAL` observation
lifecycle, explicit non-observation, and the prohibition on fabricated
availability/finalization timestamps. E02 explicitly leaves fold purge,
embargo, validation warm-up and lockbox policy downstream to F06/F07.

DG-E (`docs/architecture/OPEN_DECISIONS.md`) requires the Validation/labeling
branch to resolve the temporal availability rule used by
Feature/Research/Validation and purge/embargo/warm-up semantics at fold
boundaries before the Strategy/ML path can depend on it. This ADR resolves
that for F06 only; label/outcome horizon, censoring and lockbox semantics
remain F07.

An independent adversarial review of the frozen F06 semantics (recorded in
the governing issue) returned no blocker. It surfaced two findings that this
ADR incorporates: a deterministic precedence is required when a candidate
fails more than one invariant simultaneously, and decision-time dependency
availability (bound to the candidate instant `d`) must be kept explicitly
distinct from fold-level sample-completion availability (bound to
`test_start`), which matters once F07 target/outcome support is defined.

Credited evidence:

- F05 defines deterministic expanding train/test folds; this ADR does not
  redefine fold construction.
- E02/ADR-0026 defines FeatureDefinition input maturity, lifecycle and
  temporal evidence semantics; this ADR does not redefine them, and the
  runtime foundation does not import Feature-owned types to reuse them.
- ADR-0006 requires the availability invariant this ADR makes executable at
  the fold-candidate level.
- `tests/test_package_boundaries_v1.py` fixes `quant_platform.validation`'s
  allowed dependencies to `{validation, shared}`; it may not depend on
  `quant_platform.features`.

## Decision

Adopt `F06 availability/purge/embargo v1` as the canonical semantic
foundation for admitting or rejecting one walk-forward fold candidate.

### Fold membership

For one F05 fold, `train = [train_start, test_start)` and
`test = [test_start, test_end)`. A candidate belongs to train or test by its
canonical decision/reference instant `d`, not by the start of its historical
support. A candidate with `d < train_start` or `d >= test_end` is outside the
fold and is explicitly rejected as out-of-fold; it is never silently coerced
into train/test or misreported as purge/embargo.

### Availability is qualified by the correct cutoff

For a decision-time dependency:

```text
effective_available_at = max(
    semantic_causal_availability_floor,
    observed_available_at when known,
)
```

usable by candidate `d` only when `effective_available_at <= d`. An observed
timestamp may delay admissibility but never makes information available
earlier than the semantic floor; unknown observed availability is never
fabricated, so an absent observed timestamp leaves the semantic floor as the
only cutoff.

Two cutoffs are kept distinct: decision-time feature/observable dependencies
must be admissible by candidate instant `d`; fold-level sample-completion
dependencies (future F07 targets/outcomes) may instead need to be proven
complete/available by `test_start`. Availability by `test_start` never
excuses a dependency that was unavailable at `d`. Before F07 exists, F06 v1
only evaluates decision-time dependencies anchored to `d`, but the runtime
foundation represents the cutoff role explicitly so F07 can plug in a
fold-completion dependency later without changing this rule.

### PROVISIONAL/FINAL preserves contemporaneous meaning

F06 consumes maturity/lifecycle semantics; it does not redefine them. Under
an `AVAILABLE` requirement, a `PROVISIONAL` observation may be used only if
that exact contemporaneous observation was available by `d`; a later `FINAL`
value is never retroactively substituted as though it existed at `d`. Under
`FINAL_ONLY`, finality must be proven by `d` (an evidenced finalization
timestamp at or before `d`); otherwise the dependency is `UNAVAILABLE`.

### Warm-up/support

Pre-fold support may be legitimate causal context: a candidate inside a fold
may depend on valid causal support beginning before that fold, and a test
candidate may use earlier training-period observations as causal history.
Warm-up observations do not become train/test samples merely because they
were consumed. F06 does not require support to begin at a fold boundary.
Insufficient declared support/history remains E02-style non-observation and
is never padded, synthesized, zero-filled or inferred from row proximity.

### Purge is dependency-overlap removal

For held-out test interval `H = [test_start, test_end)`, a nominal training
candidate is `PURGED` when any declared/evidenced dependency support
intersects `H`, using exact half-open semantics: `support_end == test_start`
does not overlap from that boundary alone; `support_start == test_start`
overlaps `H`. Purge is driven by explicit dependency support, never
dataframe row distance, event spacing, partitions or a fixed `purge_rows`.

### F07 plugs into F06 without being defined here

F06 owns the generic no-overlap/admissibility rule over declared candidate
dependencies. When F07 later defines target/outcome/label support, that
support becomes another dependency F06 evaluates the same way; F06 does not
choose target horizon, label window, censoring, lockbox behavior or outcome
semantics.

### Embargo

F06 v1 embargo is a non-negative wall-clock duration `E` immediately before
the test boundary: `[test_start - E, test_start)`. A nominal training
candidate with `d` in that interval is `EMBARGOED`. `E == 0` is an empty
embargo; `E` may equal or exceed the nominal train duration, in which case
every nominal training candidate is embargoed as a deterministic consequence
of the same rule, not a special case. Only `E < 0` is invalid. Embargo
changes sample membership only: embargoed timestamps may still serve as
causal support for later admissible candidates when legally available.

### Deterministic classification precedence

Purge and embargo are independent predicates, but a scalar canonical
classification requires deterministic precedence. For a nominal training
candidate:

```text
1. UNAVAILABLE
2. INSUFFICIENT_SUPPORT / NON_OBSERVATION
3. PURGED
4. EMBARGOED
5. ADMITTED
```

Temporal availability/finality failure wins first; absent/insufficient
declared support wins next; held-out dependency overlap then yields
`PURGED`; pre-test embargo then yields `EMBARGOED`; only a candidate passing
all invariants is `ADMITTED`. No rule widens or shifts the F05 fold itself.

### Test-side admissibility remains causal

Test candidates are evaluated at their own `d`. They may consume earlier
causal history from the training interval or pre-fold warm-up; they may not
consume dependencies whose effective availability is after `d`. A test
candidate is never rejected merely because historical feature support lies
in the training interval.

### Determinism and fail-closed behavior

Given an identical F05 fold, embargo duration, candidate `d`, dependency
support evidence and availability/finality evidence, the same classification
results. Malformed/contradictory intervals, missing required support
identity, future-only availability or unprovable required finality fail
closed. No newest-row/timestamp heuristic or silent fallback is allowed.

## Runtime materialization

The accepted runtime foundation is implemented under
`quant_platform.validation.availability` as immutable typed values:

- `Embargo` — a non-negative wall-clock embargo duration in nanoseconds;
- `DependencyCutoffRole` — `DECISION_TIME` (cutoff `d`) or `FOLD_COMPLETION`
  (cutoff `test_start`), so a future F07 dependency can be represented
  without redefining F07 here;
- `DependencyEvidence` — one declared dependency's temporal support plus a
  Validation-owned mirror of the E02 causal-floor/observed-evidence/
  `PROVISIONAL -> FINAL` shape (`DependencyMaturity`, `DependencyLifecycle`),
  or an explicit `sufficient=False` non-observation with no availability
  fields. The effective availability/finality instant is always
  `max(causal_floor, observed_when_known)`: an observed timestamp earlier
  than the floor is accepted as evidence but has no effect, never rejected,
  since it can only delay admissibility, not advance it. A `FINAL`-lifecycle
  dependency evaluated under an `AVAILABLE` requirement additionally fails
  closed for any candidate before its proven finalization instant unless the
  evidence carries `contemporaneous_version_proven=True`, because absent
  that proof the seam cannot establish that the contemporaneously available
  value is the later final value rather than a retroactively substituted
  one;
- `ValidationCandidate` — one candidate decision/reference instant plus its
  dependency evidence;
- `CandidateClassification` / `CandidateClassificationResult` — the six
  canonical outcomes (`OUT_OF_FOLD`, `UNAVAILABLE`, `INSUFFICIENT_SUPPORT`,
  `PURGED`, `EMBARGOED`, `ADMITTED`) plus structured per-dependency reasons;
- `classify_candidate(fold, embargo, candidate)` — the pure deterministic
  decision function implementing the precedence above over one existing F05
  `WalkForwardFold`.

`quant_platform.validation` continues to depend only on
`quant_platform.data.models` (shared canonical `Instant`/`CoverageInterval`)
and its own `walk_forward` module; it does not import
`quant_platform.features`. `DependencyEvidence` deliberately re-declares the
narrow E02 shape it needs (maturity, lifecycle, causal/observed timestamps)
as Validation-owned values rather than importing Feature-owned runtime types,
per the package-boundary model in ADR-0024 and
`tests/test_package_boundaries_v1.py`.

The implementation introduces no dataset/model/training-framework binding,
no generic validation DSL, no row-count purge, no synthetic
availability/finality timestamps and no F07 target/label/censoring/lockbox
semantics, no F08 DSR/PBO, and no G04 session/cooldown semantics.

## Consequences

- F06 is frozen and complete for the bounded runtime foundation.
- F07 can later supply fold-completion dependency evidence into the same
  `classify_candidate` seam without this ADR being revisited, as long as it
  does not weaken the decision-time rule.
- I04 (supervised input/selection) and G04 (session/cooldown) can depend on
  a stable F06 classification outcome without re-deciding availability,
  purge or embargo semantics themselves.
- E02/ADR-0026 remains the sole owner of FeatureDefinition maturity and
  lifecycle semantics; F06 only mirrors the shape it needs.
- F05 remains the sole owner of fold construction; F06 does not widen or
  shift fold boundaries.

## Acceptance evidence

Executable proof is in `tests/test_validation_availability_v1.py`, covering
nominal fold membership and out-of-fold rejection, decision-time vs
fold-completion availability cutoffs, exact effective-availability and
observed-evidence precedence, contemporaneous provisional/final semantics,
causal warm-up, exact dependency-overlap purge and half-open boundaries,
exact pre-test embargo including equality and `E >= train_duration`
behavior, and deterministic multi-failure precedence (including the three
pairwise conflicts: unavailable-vs-purge/embargo, insufficient-support-vs-
purge, purged-vs-embargoed).

Directly relevant F05 walk-forward regression evidence remains in
`tests/test_validation_walk_forward_v1.py`; directly relevant E02 temporal
evidence remains in `tests/test_feature_definition_v1.py`. Package ownership
remains mechanically enforced by `tests/test_package_boundaries_v1.py`,
extended with `quant_platform.validation.availability` under the existing
`validation` owner.
