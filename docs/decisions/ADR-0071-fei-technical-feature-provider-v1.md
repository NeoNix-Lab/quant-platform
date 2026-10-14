# ADR-0071 - FEI technical feature provider and finite-window support v1 (DG-L)

**Status:** ACCEPTED
**Date:** 2026-10-14

## Context

DG-L registers the FEI (a multi-scale Ichimoku-based composite feature) as a
governed line (#362, `SCOPE.md` on `implement/fei`). The FEI specification was
checked against the accepted contracts and six conflicts were recorded in #363:
index-grouped scales against epoch-aligned candles; recursion over the whole
history against the `current/point/window` support model; floating point
against exact decimals; value sentinels against explicit non-observation;
a tail clamp that makes the last bars depend on array length; and decisions
(posture, urgency, corridor direction) inside a feature.

Authority consumed and not changed: ADR-0003, ADR-0006, ADR-0009, ADR-0013,
ADR-0016, ADR-0021 and `docs/contracts/CANDLE_DEFINITION.md`, ADR-0026
(`FeatureDefinition v1`), ADR-0034 (`FeatureArtifact v1`), ADR-0035 (precedent:
one `FeatureSetDefinition` of independent definitions), ADR-0037, ADR-0051.

Maintainer decisions taken before this ADR (fixed inputs, not re-opened):

1. the FEI is a *feature* like `h01_imbalance`, but composite;
2. recursive quantities use finite-window support, with no new support primitive
   and no ADR-0026 amendment;
3. the provider extension is generic (`E07`), with no plugin framework;
4. the FEI binds one InputContract on the smallest timeframe; larger scales are
   derived from it by an integer multiplier;
5. `E07` is delivered by its own issue before `T01`.

## Decision

### 1. Shape: one `FeatureSetDefinition`, independent `FeatureDefinition`s

The FEI is delivered as one `FeatureSetDefinition` (`slug = fei`, `version = 1`)
whose constituents are independent `FeatureDefinition`s, not as a single
composite `FeatureDefinition`. This follows ADR-0035 section 12.

| Feature key | Atom | Role |
|---|---|---|
| `technical.ichimoku_cloud` | `T01` | single-scale causal cloud; one instance per scale |
| `technical.fei_static` | `T02` | static multi-scale observables |
| `technical.fei_dynamics` | `T03` | finite-memory derivatives |
| `technical.fei_rhythm` | `T04` | twist rhythm, polarity, evidence state |
| `technical.fei_axes` | `T05` | categorical axes and corridor thinness |

All have `semantic_version = 1`. Reasons: each atom has its own parameters,
output contract and tests; the cloud is reusable outside the FEI; and a
threshold change in `T05` must not re-identify the cloud. `T02`-`T05` declare
composite InputContracts over the outputs of their constituents (ADR-0026,
"Input contracts"); composite dependencies are identity-bearing.

### 2. Binding: one base InputContract, derived epoch-aligned scales

The FEI's only external input is one atomic InputContract on the base
timeframe `D`:

```text
contract_key         = candle.closed_ohlc
contract_version     = 1
required_observables = high, low, close
shape                = atomic
```

A `CLOSED` `CandleDefinition v1` representation of duration `D` satisfies it.
No composite or multi-input contract is used for scales and `InputContractV1`
is unchanged. `D` is a Representation parameter and is not duplicated as a
Feature parameter (ADR-0035 section 3 precedent).

A scale is a derived view of the base series with an integer multiplier
`m_s` (`scale_multiplier`), an identity-bearing semantic parameter of every
definition that uses it. The derivation obeys these rules:

1. **Epoch alignment.** Bucket `k` of scale `m` is
   `[k*D*m, (k+1)*D*m)` on the UTC Unix epoch (CandleDefinition section 4).
   Grouping `m` consecutive base candles by position from the start of a
   series, or of a request, is rejected.
2. **Exact aggregation.** `open` = open of the first base candle, `high` =
   exact-decimal maximum, `low` = exact-decimal minimum, `close` = close of the
   last base candle, `volume` = exact-decimal sum, over the base candles of the
   bucket in time order. No binary floating point.
3. **Equality with a native candle.** A derived bucket is admitted only when
   its whole interval is covered by the base representation's coverage and
   finalization evidence; it then equals the `CandleDefinition v1` record of
   duration `D*m` built directly from `trades@1`. A covered base interval with
   no trades contributes nothing, exactly as CandleDefinition section 6 omits
   an empty bucket, and a bucket whose base intervals are all covered but all
   empty produces no derived candle. A bucket that is not fully covered
   produces no derived candle and is an uncovered interval, never a partial
   candle.
4. **Forming bucket.** At a base candle `c`, a scale uses only derived buckets
   with `bucket_end <= c.bucket_end` (`FINAL_ONLY`). The bucket still forming
   at `c` is never consumed, so a slow scale lags by up to `m-1` base candles.
   A `PARTIAL` candle is never consumed (CandleDefinition section 9).
5. **Evaluation grid.** One evaluation coordinate per `CLOSED` base candle.
   The causal floor of an observation is that base candle's `bucket_end`;
   observed availability is not fabricated (ADR-0026).

The aggregation is a Feature-owned pure function over base candles and a
caller-supplied covered-interval set. `quant_platform.application` supplies
that set from the base representation's coverage evidence. The Feature package
gains no dependency on Representation (ADR-0024, ADR-0035 section 1).

### 3. Window continuity

ADR-0026 support is ordinal over logical observations and implies no
continuity. The FEI adds one rule at binding level: a window whose time span
`[first.bucket_start, last.bucket_end)` is not fully covered is a
non-observation (`insufficient_support`). Covered-empty buckets are skipped
ordinally, as a native candle series skips them.

### 4. Boundary

The provider emits observables and categorical axes. These are not feature
outputs and are not registered under this family:

- POSTURE and urgency;
- corridor LONG/SHORT, corridor shift and target;
- any threshold-crossing that is a trading decision, sizing or an order.

The corridor *thinness* observable of `T05` is a feature: it is a function of
the causal cloud thickness at the evaluation coordinate. Decisions built on
these observables belong to research labels or `execution_policy`
(ADR-0009, ADR-0051).

### 5. Finite-window support

For every recursive quantity (EMA, twist trace, evidence history) the
definition declares, as identity-bearing semantic parameters, its window length
`N` in observations of its input sequence and a seed rule. The value at an
evaluation coordinate is a pure function of the inputs inside the window that
ends at that coordinate.

- **Seed.** The recursion is seeded at the window start with the first defined
  input in the window. A state older than the window never influences a value.
- **Warm-up.** `minimum_history = N`. Before `N` inputs are available the
  outcome is a non-observation: `insufficient_support`, or
  `initialization_required` when the window is full but no defined input exists
  to seed it.
- **Window in base terms.** A quantity computed on scale `m` over `N`
  scale-`m` candles needs `N*m` base candles of history. A quantity computed on
  the base grid with a span scaled by `m` declares `N` in base candles. The
  definition states which, and its minimum history in base candles is a named
  output of the definition.
- **Defaults.** A default value is part of the canonical identity once
  resolved. Changing a default requires a new `semantic_version`.

Sizing guidance (not normative): `N >= ln(eps) / ln(1 - alpha)` bounds the
distance between a finite-window EMA and an infinite-memory one. It governs
fidelity to a reference, not identity; section 7 places that measurement in the
conformity proof.

### 6. Non-observation and evidence

No value sentinel is a canonical value: `0`, `NaN`, `inf` and `null` are never
used to mean "undefined" or "unavailable".

- A quotient with a zero denominator is a non-observation
  (`insufficient_support`, "undefined support" in ADR-0026). It is never `0`
  and never `+/-inf`. No new non-observation reason is introduced.
- **All scales required.** `T02` and everything that consumes it require every
  declared scale (`1`, `5`, `30`). If any scale is unavailable the observable is
  a non-observation. Weights are not renormalized and a missing scale is never
  replaced by `0`; this replaces the reference rule
  `ready = finite(M5) or finite(M30)`. The first observation therefore appears
  only after the slowest scale's minimum history.
- **Evidence state.** `T04` publishes `rst` as an explicit categorical output
  with the closed values `REAL`, `SEEDED`, `NONE`; it is information, not
  absence. Numeric rhythm and polarity are non-observations unless `rst = REAL`
  (and the declared polarity denominator is non-zero).
- **Non-finite input.** A non-finite or non-observed input never updates a
  recursion and is never coerced to `0`: where the definition declares carrying
  the previous state, the carried state is the declared behavior, and a window
  with no defined input is `initialization_required`.
- `T05` labels may only be raised by a predicate whose inputs are observations
  with `rst = REAL`; a missing scale never reads as a state.

### 7. Numerics

All feature arithmetic is exact rational arithmetic over exact-decimal inputs
(`fractions.Fraction` or an equivalent exact integer form). Binary floating
point is not used in the value path. Intermediate rounding is forbidden: a
kernel rounds at most once, at publication.

Numeric outputs follow two rules:

1. **Exact outputs.** `ichimoku_cloud` price-dimension outputs (`T`, `K`,
   `SA`, `SB`, `M` and the thickness) are built only from maxima, minima and
   a bounded number of halvings of exact decimals, so each has a finite
   decimal expansion; they use `NumericalEquivalence EXACT`.
2. **Quantized outputs.** Ratios, EMAs, velocities and scores are the exact
   rational result rounded half-to-even once to a decimal `quantum`
   (`NumericalEquivalence QUANTIZED`, parameter `quantum`, identity-bearing,
   family default `0.000000000001`, i.e. `10^-12`, dimension
   `dimensionless`). Two conforming implementations therefore produce
   identical published values.

A feature-on-feature input is the *published* upstream value. A downstream
kernel is a deterministic function of published values and never recomputes or
re-quantizes its upstream.

Finite-window EMA is the definition, not an approximation of an infinite-memory
EMA, so E04 equivalence carries no truncation residual and E04 is unchanged.
A `TOLERANT` bound belongs to the conformity proof (`T06`) when it compares
with a floating-point reference; it is declared in that proof and is not part
of any `FeatureDefinition` identity.

### 8. Parity

Replay and incremental evaluation produce identical published values for the
same declared support.

- A value depends only on the window of its definition (section 5) and on
  covered input; it does not depend on the request range, its start offset or
  the array length.
- A computation that depends on array length is forbidden. In particular the
  clamp `min(n-1, i+phi_s)` is not reproduced: the projected point at
  `i + d*m_s` equals the causal cloud at `i`, so the projected domain adds no
  observation. A feature output is never stamped at a coordinate later than its
  evaluation coordinate (ADR-0006).
- An incremental implementation that keeps state is admissible only if its
  output equals the windowed recomputation exactly.
- Truncating the tail of the series leaves every earlier output byte-identical.

### 9. Calibration

Threshold values (`theta_*`, `eps`, `delta`, `rho`, `pi`, weights) are declared
identity-bearing semantic parameters. Their defaults are labelled calibration
defaults, not invariants of the family. Automatic tuning is not part of any
definition; it is fold-contained (F06/F07) and any predictive claim requires
ADR-0037 evidence.

### 10. Provider boundary delivered by `E07`

The second provider needs the following, and nothing more:

1. a typed evaluator seam: a pure function from a `FeatureDefinition` and its
   bound inputs (base candles with covered intervals, or constituent
   observations) to `FeatureObservation` or `NonObservation`;
2. one explicit, closed mapping from canonical `feature_key` to evaluator,
   constructed in `quant_platform.application`; an unknown key is refused;
3. a deterministic derivation of the expected observation universe, as
   `derive_h01_expected_observation_identities` does for H01, so that E04's
   FINAL-only seal is attributable (ADR-0034).

It introduces no discovery, entry points, dynamic import, third-party
registration, lifecycle hooks, dependency-graph engine or scheduler. It does
not move or change H01, E02 or E04. The replay provider identity of #342
(`feature-provider-v1`) is reused as is.

## Out of scope

Numeric calibration values; the reference implementation as an authority;
FeatureArtifact materialization of the FEI; any code; any threshold
auto-tune; posture, urgency, corridor direction, shift and target.

## Consequences

- `T01`-`T05` have one authority and fixed feature keys; `E07` precedes `T01`.
- No existing identity byte, ADR-0026 text, CandleDefinition v1 or E04 rule
  changes. `candle.closed_ohlc@1` is a new InputContract identity.
- The FEI's first observation appears after the slowest scale's minimum
  history, not earlier.
- Conformity vectors are derived analytically from this ADR's definitions, not
  from a legacy implementation's output. Deviations from the reference
  implementation (finite window against infinite EMA, removed tail clamp,
  required-scales rule) are listed in `T06` with measured size.
- Implementation remains unauthorized until a governance reconciliation
  re-states `T01`-`T06` and `E07`.

## Acceptance evidence

`tests/test_fei_technical_feature_provider_contract_v1.py` pins this ADR's
normative statements. No runtime evidence exists or is claimed.
