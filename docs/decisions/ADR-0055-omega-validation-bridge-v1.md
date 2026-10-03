# ADR-0055 — Omega validation bridge phase 1 v1

**Status:** ACCEPTED
**Date:** 2026-10-03

## Context

Omega currently centralizes its platform imports in `omega.platform_link`, but
its validation entries still point at implementation modules such as
`quant_platform.validation.robustness` and `.walk_forward`. That conflicts
with the installed-distribution boundary in `PUBLIC_PYTHON_API.md` and leaves
the consumer coupled to names that are not a v1 dependency contract.

The existing Validation runtime already owns the required semantics:

- ADR-0037 freezes DSR-L and full-CSCV PBO over a
  `ComparableTrialPanel`;
- ADR-0031 freezes availability, purge and embargo classification over a
  `WalkForwardFold`, `Embargo` and `ValidationCandidate`.

This decision creates an implementation-ready, deliberately narrow bridge. It
does not migrate replay, make `DataGateway` part of the Omega bridge, or
recreate a statistics implementation in Omega.

## Decision

### 1. Public imports and compatibility

Every phase-1 *Validation* import in Omega must come from
`quant_platform.validation`. It must not import a Validation implementation
submodule, `quant_platform.data.models`, or an internal/proof helper.

The minimum dependency is a future immutable Quant Platform tag containing
this ADR and the public-surface smoke test (commit `390239b` or a descendant).
`quant_platform.__version__ == "0.1.0"` alone is insufficient because the
existing Omega pin predates the public-surface contract. The Omega change must
move both `PLATFORM_PIN` and its `platform` extra to that one exact tag, and
its existing pin-consistency test must remain green. Creating or naming that
release tag is release work, not part of this issue.

Omega's `PLATFORM_SYMBOLS` replacements are:

```python
# all map to "quant_platform.validation"
VALIDATION_BRIDGE_SYMBOLS = (
    "ComparableTrialPanel", "EffectiveTrialCountEvidence", "EvaluationStatus",
    "DSRResult", "PBOResult", "evaluate_dsr_v1", "evaluate_pbo_v1",
    "Instant", "CoverageInterval",
    "WalkForwardFold", "WalkForwardScheduleSpec", "build_walk_forward_folds",
    "Embargo", "DependencyCutoffRole", "DependencyMaturity", "DependencyLifecycle",
    "DependencyEvidence", "ValidationCandidate",
    "CandidateClassification", "CandidateClassificationResult", "classify_candidate",
)
```

`Instant` and `CoverageInterval` are re-export as Validation inputs rather
than opening `quant_platform.data` as another consumer surface. The three
dependency enums are public because a sufficient `DependencyEvidence` cannot
be constructed without them.

### 2. Exact first-use adapters in Omega

Omega owns conversion of its already ordered, same-frequency *excess-return*
series into this panel:

```python
ComparableTrialPanel(
    population_id: str,
    trial_ids: tuple[str, ...],
    observation_ids: tuple[str | int, ...],
    returns: Mapping[str, tuple[float, ...]],
    return_semantics_id: str,
)
```

The mapping must declare exactly the ordered trial IDs, every return must be
finite, and every series must span the identical ordered observation universe.
Omega must not align timestamps, drop rows/trials, annualize, reconstruct PnL,
or subtract costs/risk-free returns inside this adapter. Those are upstream
Omega evidence choices and must be reflected in `population_id` and
`return_semantics_id`.

For DSR, Omega supplies
`EffectiveTrialCountEvidence(k_eff: float, evidence_id: str)` and consumes
the returned `DSRResult`. It must preserve `NON_EVALUABLE` status/reason as a
non-result, not coerce it to zero or a pass. Until a separately governed
method exists, the only permitted fallback is `k_eff = len(panel.trial_ids)`
with an evidence ID that says this is the conservative nominal-count fallback,
as frozen by ADR-0037 Amendment 1.

For PBO, Omega supplies the same panel and an even `block_count >= 4` that
exactly divides the observation count, then consumes `PBOResult`. It must not
replace full CSCV with a sampled subset or reinterpret walk-forward folds as
CSCV blocks.

For temporal classification, Omega creates a
`WalkForwardScheduleSpec`, obtains its `WalkForwardFold` values through
`build_walk_forward_folds`, and invokes:

```python
classify_candidate(
    fold: WalkForwardFold,
    embargo: Embargo(duration_ns: int),
    candidate: ValidationCandidate,
) -> CandidateClassificationResult
```

A sufficient `DependencyEvidence` must carry its actual `CoverageInterval`,
cutoff role, maturity/lifecycle and availability/finality evidence. An absent
or insufficient source is represented as `sufficient=False`; Omega must not
synthesize availability timestamps or replace a non-admitted classification
with an admitted result. `CandidateClassification` and the result reasons are
the only phase-1 classification output.

### 3. Parity and integration tests in Omega

The Omega implementation PR must add tests that:

1. resolve every bridge name through `omega.platform_link` and assert its
   `PLATFORM_SYMBOLS` mapping is exactly `quant_platform.validation`, never an
   implementation submodule;
2. build the same comparable panel through Omega's adapter and directly
   through the documented platform constructors, then assert identical
   `DSRResult.canonical_payload()` and `PBOResult.canonical_payload()`;
3. cover DSR's conservative `k_eff = N` fallback and propagate a
   non-evaluable result unchanged;
4. use the ADR-0037 8x4 CSCV reference panel with `block_count=4`, asserting
   six splits, one negative logit and `pbo == 1 / 6`;
5. construct one availability candidate whose dependency overlaps the held-out
   interval and assert `PURGED`, and one at the embargo boundary and assert
   `EMBARGOED`; and
6. retain Omega's no-direct-platform-import architectural test, permitting
   these imports only through `omega.platform_link`.

The Quant Platform installed-distribution smoke test imports every name in
section 1. It is the upstream regression proof that the bridge cannot silently
fall back to an internal import path.

### 4. Ownership and sequencing

| Work | Owner | Boundary |
| --- | --- | --- |
| Document/re-export the minimal bridge inputs and smoke them from a non-editable install | Quant Platform | This issue |
| Create the immutable tag containing this contract | Quant Platform release process | Follow-up, no tag mutation here |
| Move Omega's pin and `PLATFORM_SYMBOLS`; add adapters and parity tests | Omega | Separate Omega PR |
| DataGateway consumption, replay/backtest migration, live/paper trading | Neither phase-1 change | Explicitly out of scope |

## Consequences

- Omega receives a small, typed, package-level Validation dependency surface
  instead of an implicit implementation-module dependency.
- The bridge preserves Validation's fail-closed outcomes and frozen DSR/PBO
  semantics; it does not invent a competing Omega estimator or availability
  policy.
- An Omega pin update remains intentionally blocked until an immutable release
  tag contains the public contract.

## Out of scope

- changes to the Omega repository;
- a `DataGateway` migration or any D05/replay representation input work;
- trial-count estimation, DSR-LS, serial-correlation corrections, or sampled
  CSCV;
- replay/backtest engine migration and paper/live trading.

## Related

Parent tracking: #232. Closes issue #243. Found via `NeoNix-Lab/omega#15`.
