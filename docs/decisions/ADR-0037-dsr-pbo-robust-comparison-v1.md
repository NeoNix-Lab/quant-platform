# ADR-0037 — DSR/PBO robust-comparison semantics v1

**Status:** ACCEPTED  
**Date:** 2026-09-20

## Context

F08 is the remaining open branch of DG-E after F07 was frozen by ADR-0036.
Its declared prerequisites F04 (event studies/sweeps) and F05 (deterministic
walk-forward schedule) are complete, but neither defines the statistical
meaning of Deflated Sharpe Ratio (DSR) or Probability of Backtest Overfitting
(PBO).

The Legacy Adoption Ledger classifies H14 `DSR / PBO estimators` as `REVIEW`,
not ADOPT/ADAPT, because exact estimator definitions, return-series meaning,
trial population, comparable-fold semantics and reference vectors were not
resolved. The legacy function names are therefore evidence only and cannot be
promoted to canonical semantics by name.

Primary mathematical references are:

- Bailey & López de Prado, *The Deflated Sharpe Ratio: Correcting for Selection
  Bias, Backtest Overfitting and Non-Normality* (Journal of Portfolio
  Management, 2014);
- López de Prado & Porcu, *The Deflated Sharpe Ratio: A Unified Framework for
  Search-Adjusted Performance Inference* (2026), which makes explicit that DSR
  is a family of search-adjusted procedures and distinguishes the original
  location-only form (`DSR-L`) from later location-scale/full-search variants;
- Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest
  Overfitting* (Journal of Computational Finance, 2015), which defines PBO via
  Combinatorially Symmetric Cross-Validation (CSCV).

Issue #97 is the bounded governance-only decision scope for this ADR. No F08
runtime is implemented by this decision.

## Decision

Adopt the following bounded `F08 DSR/PBO v1` semantic contract.

### 1. Canonical owner and architectural boundary

The canonical runtime owner for the F08 estimator foundation is Validation.
A future implementation is expected under `quant_platform.validation`
(e.g. a narrow `robustness` module; exact local naming remains implementation
local).

Validation keeps its existing owner dependency rule: it may depend only on
Validation and Shared runtime modules. F08 must not import Research,
Experiment, Strategy, Execution, Data Access or Application runtime types.

Cross-owner identities (trial, population, upstream result identity) are
consumed as opaque evidence through Validation-owned immutable projections.
F08 does not create a competing Study/Trial/Run identity or persistence model.

### 2. ComparableTrialPanel v1

Both DSR and PBO operate on one explicitly declared comparable trial panel.
The semantic panel contains:

- one canonical population/evidence identity;
- an ordered set of at least one canonical opaque trial identity;
- one common ordered observation universe/support;
- for each trial, one same-length ordered series of finite, dimensionless,
  same-frequency **excess returns**;
- one declared return-semantics identity;
- for DSR, explicit effective-trial-count evidence.

The supplied series are already the economic return quantity to be evaluated.
F08 does **not** reconstruct prices, PnL, costs, fees, slippage, risk-free
returns, annualization or execution semantics. If a risk-free benchmark is
needed, subtraction happens upstream and the supplied series is the resulting
excess-return series.

All trials must share exactly the same observation universe and semantics.
The following fail closed:

- NaN/Inf/non-finite returns;
- unequal lengths/support;
- silent timestamp alignment;
- selective row dropping;
- silent trial dropping;
- performance-based filtering of the panel after observing results.

Once the panel is supplied to F08, every declared comparable trial remains in
the comparison. Upstream trial-accounting semantics may explain why an attempt
is not part of a comparable panel; F08 never invents or hides such exclusions.

### 3. Canonical Sharpe statistic v1

For one finite excess-return series `r_1 ... r_T` with `T >= 2`:

```text
mean = (1/T) * sum(r_i)
s^2  = sum((r_i - mean)^2) / (T - 1)
SR   = mean / sqrt(s^2)
```

This is a **per-observation, non-annualized Sharpe**. F08 never infers a
frequency or multiplies by an annualization factor.

If sample variance is zero, or fewer than two observations are present, the
Sharpe is non-evaluable. Non-evaluable required Sharpe values propagate to an
explicit non-evaluable DSR/PBO result; they are never mapped to `0`, infinity,
or silently removed.

### 4. Deterministic higher-moment convention

DSR uses deterministic standardized central moments from the selected trial's
full supplied return series. For `T >= 4`, let:

```text
d_i = r_i - mean
m_k = (1/T) * sum(d_i^k)

gamma3 = m_3 / m_2^(3/2)
gamma4 = m_4 / m_2^2
```

`gamma4` is raw/Pearson kurtosis (`3` for a Normal distribution), not excess
kurtosis. These formulas are identity-bearing F08 v1 semantics; library-specific
"bias corrected" skew/kurtosis defaults must not substitute for them.

If `T < 4` or `m_2 == 0`, DSR is non-evaluable.

### 5. DSR v1 is DSR-L

F08 v1 freezes **DSR-L**, the original location-only search-adjusted
implementation. The 2026 unified framework's location-scale (`DSR-LS`) and
full-search-distribution forms are distinct future variants and are not aliases
for v1.

#### 5.1 Selected trial

The selected trial is the deterministic maximum full-panel canonical Sharpe.
If multiple trials have exactly equal Sharpe, the lexicographically smallest
canonical trial identity wins. The selected trial must therefore be a member
of the declared panel and selection cannot be supplied as an unverified
external winner.

#### 5.2 Cross-trial dispersion

For nominal panel size `N > 1`, let `SR_j` be every trial's full-panel Sharpe:

```text
sigma_SR^2 = sum((SR_j - mean(SR))^2) / (N - 1)
```

For `N == 1`, `sigma_SR = 0`.

Every trial Sharpe must be evaluable. No trial may disappear from the
cross-sectional dispersion calculation.

#### 5.3 Effective trial count is explicit evidence

DSR-L requires an explicit finite `K_eff` supplied as effective-trial-count
evidence. F08 v1 **does not estimate** trial independence.

Rules:

```text
1 <= K_eff <= N
K_eff == 1       -> search-adjusted benchmark SR0 := 0
1 < K_eff < 2    -> DSR v1 is NON_EVALUABLE
K_eff >= 2       -> apply the location approximation below
```

The result identity binds `K_eff` and the opaque evidence/method identity that
justifies it. Supplying nominal `N` as `K_eff` is therefore an explicit caller
assumption, never an inference made by F08.

Effective-trial estimation algorithms (clustering, spectral/eigenvalue methods,
or other estimators) are outside F08 v1 and require separate authority if later
adopted.

#### 5.4 Search-adjusted benchmark

For `K_eff >= 2`:

```text
gamma_EM = 0.5772156649015329

z_max =
    (1 - gamma_EM) * Phi^-1(1 - 1/K_eff)
  + gamma_EM       * Phi^-1(1 - 1/(K_eff * e))

SR0 = sigma_SR * z_max
```

`Phi` is the standard Normal CDF and `Phi^-1` its inverse.

This is the Bailey/López de Prado expected-maximum location approximation under
the search-adjusted null. The null location is zero; the observed
cross-sectional trial Sharpe dispersion supplies the scale.

#### 5.5 DSR probability

Let `SR_hat` be the selected trial's canonical Sharpe, `T` its observation
count, and `gamma3/gamma4` the moments above:

```text
A = 1
    - gamma3 * SR_hat
    + ((gamma4 - 1) / 4) * SR_hat^2

z_DSR = ((SR_hat - SR0) * sqrt(T - 1)) / sqrt(A)
DSR   = Phi(z_DSR)
```

`A` must be finite and strictly positive. Otherwise the result is
non-evaluable.

The DSR value is a search-adjusted inferential probability under this frozen
approximation; it is not a forecast, a deployment recommendation, or proof of
strategy robustness.

#### 5.6 Sampling assumption

F08 v1 records the sampling model as `IID_V1`. No serial-correlation correction
is silently applied. A serial-correlation-adjusted DSR requires a future
versioned decision; callers must not describe v1 as having corrected for serial
dependence.

### 6. PBO v1 is full CSCV

PBO v1 implements the canonical full CSCV rank procedure, not ordinary
walk-forward validation and not the simplified/bounded-combination legacy H14
implementation.

Input:

- the complete comparable `T x N` return panel;
- `N >= 2` trials;
- explicit even block count `S >= 4`;
- `T` exactly divisible by `S`;
- each IS/OOS half must contain at least two observations.

#### 6.1 CSCV partition construction

Preserve observation order and partition the `T` rows into exactly `S` equal,
contiguous blocks.

Enumerate every combination of `S/2` blocks as the in-sample half. The exact
complement is out-of-sample. Therefore:

```text
split_count = C(S, S/2)
```

No random subsampling, combination cap, rolling-window substitution or hidden
fold selection is permitted.

F05 is credited for deterministic temporal primitives but F05 walk-forward
folds are **not** reinterpreted as CSCV partitions.

#### 6.2 Per-split selection and OOS rank

For each CSCV split:

1. compute canonical v1 Sharpe for every trial on IS;
2. compute canonical v1 Sharpe for every trial on OOS;
3. choose the maximum IS Sharpe; exact ties use lexicographically smallest
   canonical trial identity;
4. rank that selected trial's OOS Sharpe among all `N` OOS Sharpes, where
   rank `1` is worst and rank `N` is best;
5. exact OOS Sharpe ties receive their arithmetic average rank;
6. compute:

```text
omega  = rank_OOS / (N + 1)
lambda = ln(omega / (1 - omega))
```

Because `1 <= rank <= N`, `omega` is always strictly inside `(0,1)`.

If any required IS/OOS Sharpe is non-evaluable, the complete PBO evaluation is
non-evaluable. No split or trial is silently dropped.

#### 6.3 PBO definition

The canonical event is strict below-median OOS performance:

```text
PBO = count(lambda < 0) / split_count
```

An exact `lambda == 0` is the exact median and is **not** counted in the strict
below-median event. This tie boundary is frozen to remove the `< 0` vs `<= 0`
implementation ambiguity.

The output preserves at least the total split count, negative-logit count, PBO,
and deterministic per-split evidence sufficient to reproduce the result.

### 7. Result identity and provenance

Future canonical DSR/PBO results must have deterministic versioned identities
that bind all identity-bearing evidence required to reproduce the statistic.
At minimum this includes:

- metric/spec version;
- comparable population identity;
- ordered trial identities;
- return-semantics identity;
- ordered observation/support identity;
- numerical-policy identity;
- DSR: selected trial, `K_eff` evidence, trial dispersion, selected-series
  moments and all formula inputs;
- PBO: `S`, deterministic partition definition, all split selection/rank/logit
  evidence and final counts.

Local host/path/object identity, wall-clock execution time and iteration
accidents are not identity-bearing.

Changing an identity-bearing input must change the result identity.

### 8. Numerical policy v1

DSR/PBO require square roots, logarithms and Normal CDF/inverse-CDF operations;
there is no exact-rational end-to-end claim.

F08 v1 numerical policy is:

- IEEE-754 binary64 inputs/outputs for statistical calculations;
- reject NaN/Inf and invalid domains explicitly;
- formulas, rank rules and operation semantics in this ADR are canonical;
- the concrete math/statistics library is implementation-local provided the
  pinned vectors below match with absolute error `<= 1e-12` for the listed
  scalar values;
- deterministic ordering must not depend on hash/set traversal.

### 9. Pinned DSR-L reference vector

Primitive formula fixture:

```text
SR_hat      = 0.5
T           = 24
gamma3      = 0
gamma4      = 3
sigma_SR    = 0.2
K_eff       = 10

expected SR0 = 0.31491966026915
expected DSR = 0.79866173151637
```

Both expected scalar values must match within absolute error `1e-12`.

Additional invariants:

- `K_eff == 1` implies `SR0 == 0` and DSR reduces to PSR against zero under the
  same selected-series moments;
- `1 < K_eff < 2` is non-evaluable;
- changing `K_eff`, panel Sharpe dispersion or selected-series moments is an
  identity-bearing change.

### 10. Pinned CSCV/PBO reference vector

Use trial identities ordered `A < B < C < D`, `S = 4`, and this 8x4 return
matrix (rows are the common ordered observations):

```text
row     A       B       C       D
1      0.08   -0.08    0.015  -0.005
2      0.12   -0.12    0.025   0.005
3      0.09   -0.09    0.018  -0.004
4      0.11   -0.11    0.022   0.004
5     -0.08    0.08    0.016  -0.006
6     -0.12    0.12    0.024   0.006
7     -0.09    0.09    0.019  -0.003
8     -0.11    0.11    0.021   0.003
```

The contiguous blocks are `(1,2)`, `(3,4)`, `(5,6)`, `(7,8)`.
Enumerating all six two-block IS combinations in lexicographic block-index
order yields:

```text
IS blocks   winner   OOS rank   omega   lambda
(0,1)       A        1          0.2    -1.3862943611198906
(0,2)       C        4          0.8     1.3862943611198908
(0,3)       C        4          0.8     1.3862943611198908
(1,2)       C        4          0.8     1.3862943611198908
(1,3)       C        4          0.8     1.3862943611198908
(2,3)       C        3          0.6     0.4054651081081642
```

Therefore:

```text
split_count          = 6
negative_logit_count = 1
PBO                  = 1/6
```

The winner/rank sequence is canonical evidence; PBO is not accepted if an
implementation obtains `1/6` through a different hidden filtering or split
procedure.

## Explicit exclusions

This ADR does not define or authorize:

- F08 runtime implementation or tests;
- F07 runtime implementation;
- DSR-LS or full-search/exact-order DSR variants;
- serial-correlation/HAC/AR corrections;
- effective-trial-count estimation algorithms;
- generic performance-metric callbacks/plugins/DSLs;
- Strategy/Execution/backtest/PnL/cost/fee/slippage semantics;
- trial generation, search or hyperparameter optimization;
- Experiment persistence/accounting redesign;
- DataGateway/file/database access;
- a robustness score, deployment threshold, strategy acceptance decision or
  automatic model-selection policy.

## Consequences

- F08 decision state becomes `FROZEN`; implementation remains `MISSING`.
- H14 is no longer an unresolved semantic proposition: the canonical system
  resolves it by **ADAPTing the useful DSR/PBO intent to ADR-0037**, not by
  adopting the legacy functions.
- DG-E has no remaining open semantic branch: F07 is frozen by ADR-0036 and F08
  by ADR-0037. Their runtime implementation states remain independent.
- F04, F05 and F07 semantics are unchanged and credited.
- The future F08 coding agent receives formulas, domains, failure behavior,
  ownership and pinned vectors without being asked to choose statistical
  semantics during implementation.

## Acceptance evidence

This governance-only freeze is tracked by issue #97. No runtime tests are added
in this decision scope. The pinned vectors in this ADR become the minimum
required implementation evidence for the later F08 runtime issue.

## Amendment 1 (issue #253) — caller-supplied `k_eff` is the permanent v1 answer, not a placeholder

**Date:** 2026-10-03

Design gate issue #253 (materialized from #232 U6; raised externally via
`NeoNix-Lab/omega#15` U6). Section 5.3 already requires `K_eff` as explicit
caller-supplied evidence and already excludes "effective-trial estimation
algorithms" from this ADR's authority. Confirmed directly in
`validation/robustness.py`: `EffectiveTrialCountEvidence` is a pure evidence
container (`k_eff: float`, `evidence_id: str`) with no estimation logic
anywhere in the module, matching the ADR exactly. The question this gate
closes is not "should `k_eff` be caller-supplied" (already decided) but
"is that a temporary v1 gap pending a future estimator, or the intended,
permanent boundary" — because the former reads as an open TODO and the
latter does not.

### Decision

**Caller-supplied `k_eff` is the intended, permanent answer, not a v1
placeholder awaiting a future estimator.** No estimation algorithm is added
to F08, now or by implication of a future "v2."

A concrete effective-trial-count estimation method (clustering, spectral/
eigenvalue methods on the trial correlation matrix, or other approaches from
the DSR/PBO literature this ADR already cites) remains possible in principle,
but is explicitly **not** a natural extension of this ADR the way, say, a new
`ConsumerErrorCode` was a natural extension of ADR-0050: it requires choosing
among competing statistical methodologies with real tradeoffs, each
defensible, none obviously canonical — exactly the kind of choice issue #253
itself flags as needing "its own statistical-methodology review, likely its
own design gate given the literature choice involved." This gate is not
qualified to make that choice responsibly in passing, and does not attempt
to.

**Recommended conservative fallback:** a caller with no principled
independence estimate should supply `k_eff = N` (the nominal trial count) --
assuming full independence across all attempted trials is the search-adjusted
benchmark `SR0` at its *highest* (hardest to beat), not its lowest, because a
larger effective trial count widens the expected-maximum null `z_max` (s.5.4).
Understating `k_eff` relative to the true number of independent attempts
produces an artificially lenient benchmark and overstates significance;
`k_eff = N` is therefore the safe default in the absence of a better estimate,
never the dangerous direction to be wrong in. This mirrors the fallback the
Omega consumer already uses in production.

### Consequences

- No code or contract change: `EffectiveTrialCountEvidence`, `DSRResult` and
  every DSR/PBO formula in this ADR are unchanged.
- `EffectiveTrialCountEvidence`'s own docstring (`validation/robustness.py`)
  is amended to state the recommended conservative fallback, so a caller
  reading the type directly does not have to find this ADR first.
- A future design gate proposing a concrete estimator must address this
  Amendment directly (it would narrow, not just add to, what this ADR
  permanently decided here) and must resolve a specific literature/method
  choice, not merely "whether" to estimate.