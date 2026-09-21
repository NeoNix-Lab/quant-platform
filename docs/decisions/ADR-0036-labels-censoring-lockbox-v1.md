# ADR-0036 — Outcome-derived labels, censoring and lockbox v1

**Status:** ACCEPTED  
**Date:** 2026-09-20

## Context

F03 already owns the canonical `OutcomeSpec` / `Outcome` boundary, including
explicit forward horizons, `COMPLETE | CENSORED_END_OF_DATA |
INSUFFICIENT_COVERAGE`, causal availability and exact canonical fraction-text
numeric values. ADR-0008 requires Outcomes to precede Labels and distinguishes
outcome-derived labels from policy-derived labels.

F06 is frozen and complete under ADR-0031. It owns walk-forward candidate
admissibility, causal availability, warm-up/support, exact dependency-overlap
purge, embargo and deterministic classification precedence. ADR-0031
intentionally leaves target/outcome support, label/censoring policy and the
lockbox/hidden-evaluation boundary to F07, while providing
`DependencyCutoffRole.FOLD_COMPLETION` for a future target dependency.

DG-E therefore has one bounded blocking proposition on the Validation ->
Strategy / Supervised-ML path: freeze F07 semantics without reopening F03 or
F06 and without importing downstream Strategy/Execution assumptions.

Issue #95 is the governance-only decision-gate scope for this ADR. F08 DSR/PBO
is an independent DG-E branch and is not part of this decision.

## Decision

Adopt `F07 labels/censoring/lockbox v1` as the canonical Validation semantic
foundation described below.

### 1. F07 v1 is outcome-derived only

F07 v1 supports **outcome-derived labels only**.

Policy-derived labels remain a valid future family under ADR-0008, but they
require explicit `StrategySpec` / `ExecutionSpec` authority. They are deferred
until those semantics exist and are not approximated inside F07.

F07 does not redefine F03. Outcome horizon, state, realized value, path
measurement, censoring and causal availability remain owned by F03.

### 2. LabelDefinition v1

A canonical outcome-derived label definition declares, at minimum:

- a governed `label_key`;
- an explicit positive semantic version;
- exactly one canonical source `OutcomeSpec` identity;
- one v1 transformation kind and its identity-bearing parameters;
- an explicit output value/class schema;
- the v1 censoring policy `REQUIRE_COMPLETE`.

All output-affecting semantics above participate in deterministic definition
identity. Descriptive text, runtime location, host/process information and
execution timestamps do not.

The identity domain is:

```text
label-definition-v1:sha256:<digest>
```

### 3. Source eligibility and censoring

F03 source state is preserved, never guessed or collapsed:

```text
Outcome COMPLETE
    -> eligible for deterministic label transformation

Outcome CENSORED_END_OF_DATA
    -> no label value; source-censored state remains explicit

Outcome INSUFFICIENT_COVERAGE
    -> no label value; source-insufficient state remains explicit
```

A censored or insufficient Outcome is never coerced to zero, negative,
`no-touch`, `false`, a neutral class or any other fabricated target.

Only a `COMPLETE` Outcome carrying its canonical F03 `realized_value` may
produce a label value.

### 4. Minimum v1 transformations

F07 v1 freezes only two deterministic transformation families.

#### Exact continuous identity

`IDENTITY_VALUE` preserves the source Outcome's canonical exact rational
`realized_value` unchanged as the label value. No float conversion or Decimal
rounding context may alter it.

#### Exact ordered-threshold categorical mapping

`ORDERED_THRESHOLDS` declares:

- a strictly increasing ordered tuple of canonical exact-rational thresholds;
- exactly `N + 1` governed class values for `N` thresholds.

For thresholds `t0 < t1 < ... < tN-1`, interval semantics are:

```text
x < t0                  -> class 0
t0 <= x < t1            -> class 1
...
tN-1 <= x               -> class N
```

Equality therefore belongs to the upper interval. Threshold comparison is
exact rational comparison over the F03 value; no epsilon/tolerance or float
conversion is allowed.

No generic expression language, callback/plugin label engine, retrospective
percentile transform or triple-barrier implementation is introduced by F07
v1. A future path-dependent label that needs semantics not present in F03
must first obtain its own canonical Outcome authority rather than hiding new
Outcome semantics inside Label transformation.

### 5. Label-result identity binds source evidence

A label result is traceable to both its `LabelDefinition` and the exact source
Outcome evidence.

Its deterministic identity must bind at least:

- label-definition identity;
- source Outcome identity;
- source Outcome state;
- source horizon start/end;
- source causal availability;
- canonical source realized value when present;
- resulting label state/value when present.

The result identity domain is:

```text
label-result-v1:sha256:<digest>
```

This explicit evidence binding is required because an opaque source Outcome
identity alone must not let two different realized source results collapse to
one label-result identity.

### 6. Label causal availability

For the pure deterministic v1 transformations above:

```text
label.causal_available_at = source_outcome.causal_available_at
```

A label can never become available before the Outcome evidence that produces
it. Label/lockbox values are evaluation targets and may never be fed backward
as features or decision inputs at an earlier decision instant.

### 7. Canonical target support preserves the Outcome boundary

F03 evaluates the source path from `horizon_start == event_time` through the
boundary observation exactly at `horizon_end`. That terminal observation is
consumed evidence and therefore belongs to target support.

The semantic target support is consequently closed at the right edge:

```text
[horizon_start, horizon_end]
```

F06 uses canonical half-open `CoverageInterval` semantics. When projecting an
F07 target into that seam, the projection must preserve the consumed terminal
instant exactly. With the current nanosecond `Instant` domain the canonical
projection is:

```text
[horizon_start, horizon_end + 1ns)
```

or an exactly equivalent half-open representation that includes
`horizon_end`. It is forbidden to shrink this to
`[horizon_start, horizon_end)`: if `horizon_end == test_start` or
`lockbox_start`, the target consumed evidence at that held-out boundary and
the development sample is contaminated.

### 8. Training-target integration with F06

For a training/development sample, an F07 target is a fold-completion
dependency, not a decision-time feature dependency.

A usable training target must:

- originate from a `COMPLETE` F03 Outcome;
- carry the exact target support above;
- carry causal availability derived from the source Outcome;
- be represented to the F06 seam with cutoff role `FOLD_COMPLETION`;
- be complete/available by the F06 fold-completion cutoff;
- not overlap the held-out interval under ADR-0031's exact half-open overlap
  rule.

Censored/insufficient source Outcomes do not become labeled training samples.
No rule here weakens F06 decision-time availability: feature/observable
inputs still must have been available by the sample decision/reference
instant `d`.

For test/lockbox samples, the future target is an **evaluation output**, not a
decision-time dependency of the same sample. Requiring its future label to be
known at `d` would itself be a temporal error.

### 9. Lockbox v1

A v1 lockbox is one explicitly declared **terminal contiguous holdout**
interval:

```text
L = [lockbox_start, lockbox_end)
```

It begins after the development folds selected for the same evaluation plan.
Sample membership is determined by the canonical candidate/reference instant
`d`: `d in L` means the sample belongs to the lockbox population.

While the lockbox is unrevealed:

- a development sample may not have `d` inside `L`;
- development dependency/target support may not intersect `L`;
- evidence needed to score lockbox members, including forward target evidence
  extending beyond a member's `d` (and, where necessary, beyond
  `lockbox_end`), is part of hidden evaluation evidence and must not feed the
  development loop;
- lockbox labels, aggregates or diagnostics may not influence feature
  selection, label thresholds, model/hyperparameter selection, strategy or
  decision thresholds, trial selection, stopping decisions or any other
  development choice.

A lockbox evaluation is run only against a candidate/configuration whose
relevant development choices are already frozen for that evaluation.

F07 v1 freezes the **semantic isolation**, not a physical security mechanism.
Separate database credentials, encryption, filesystem ACLs, vaults or remote
services are not required by this ADR and may be added later only when an
operational requirement justifies them.

### 10. Reveal is explicit and semantically irreversible

The lockbox has two semantic visibility states:

```text
UNREVEALED -> REVEALED
```

Reveal is an explicit action/evidence transition; it is never inferred merely
because evaluation bytes exist somewhere.

Once lockbox results have been revealed and observed, that exact population
cannot subsequently be claimed as an **untouched lockbox** for a development
or selection decision influenced by those results. It may remain historical
evaluation evidence, but another independent untouched population is required
for a new lockbox claim.

There is no `REVEALED -> UNREVEALED` transition.

### 11. Package/ownership boundary

F03 remains Research-owned and F07 remains Validation-owned. The existing
package DAG does not permit `quant_platform.validation` to import Research
runtime types directly.

A future F07 implementation must therefore consume a narrow Validation-owned
projection of the exact F03 Outcome evidence needed by this ADR, supplied by
a permitted composition/caller boundary. That projection must preserve the
source identities, state, horizon, causal availability and canonical value;
it must not reinterpret or recompute F03 semantics.

Concrete local type/helper names remain implementation-local.

## Excluded

This ADR does not authorize or define:

- F07 runtime implementation;
- F08 DSR/PBO;
- policy-derived labels;
- StrategySpec, backtesting, execution, PnL, fees or fills;
- new F03 Outcome families or hidden triple-barrier/path semantics;
- model training or feature selection algorithms;
- Experiment Study/Trial/Run accounting or persistence;
- physical lockbox security/ACL infrastructure;
- a generic label/validation DSL, registry, plugin system or framework.

## Consequences

- F07 decision state becomes `FROZEN`; implementation remains `MISSING`.
- The DG-E validation/labeling branch has no remaining semantic blocker for
  the bounded F07 v1 implementation.
- F08 remains an independent `OPEN_BLOCKING` DG-E branch.
- G01 and I04 remain implementation-blocked until F07 runtime is actually
  complete; semantic freeze alone does not satisfy their `Requires F07`
  implementation dependency.
- F03 and ADR-0031/F06 remain unchanged authorities and are credited rather
  than re-proved.

## Acceptance evidence

This governance-only freeze is tracked by issue #95. Existing F03 tests and
ADR-0031/F06 adversarial tests are credited evidence for the source Outcome
and fold-admissibility propositions respectively; this decision scope does
not duplicate those test campaigns or mutate runtime code.
