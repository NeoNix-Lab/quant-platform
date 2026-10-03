# ADR-0056 — Golden proof and test-double boundary v1

**Status:** ACCEPTED
**Date:** 2026-10-03

## Context

The installable `quant_platform.application` package contains several Golden
proof compositions. They are legitimate Application-owned orchestration under
ADR-0024 and ADR-0050: a thin `tools/` entrypoint delegates to a composition
that wires existing domain capabilities. That owner decision does not make
every helper inside those proof modules a reusable consumer API.

The public dependency contract already excludes `application.golden_*`,
`wave6_golden`, fixture-bound proof helpers, and repository-relative fixtures.
This audit distinguishes the retained proof seams from the three explicit
in-memory doubles and two policy-shaped helpers so a later refactor is narrow
and does not turn into a blanket move.

## Decision

### Classification inventory

| File and object | Classification | Evidence | Recommended action |
| --- | --- | --- | --- |
| `application/golden_conformity.py`: `GoldenExpectation`, `GoldenCandleExpectation`, `observe_scan`, `observe_scan_with_candles`, mismatch/format helpers | Production proof orchestration worth keeping in `src` | `application/conformity.py` composes these for the human Golden vertical; the candle path streams the existing scan once rather than re-opening it | Keep in Application; retain the fixture-bound/non-public docstring and public-contract exclusion. |
| `application/golden_conformity.py`: `_TelemetryCollector`, `_CandleScanBridge` | Internal helpers requiring no action | Both are private and exist only to bound one proof traversal | Keep private; do not promote or relocate independently. |
| `tests/golden_conformity_support.py`: trade-only expectation/observer support | Test fixture/support | Package-boundary tests already classify this module as `tests`; integration tests import it from `tests`, not `src` | Keep in `tests`; do not re-export it from the package. |
| `application/golden_replay.py`: `run_golden_replay_proof`, `GoldenReplayProof`, gateway/spec composition | Production proof orchestration worth keeping in `src` | `tools/golden_replay_e2e.py` delegates to it for a real canonical dataset and two independent replay runs | Keep in Application and non-public. |
| `application/golden_replay.py`: `MinimalBreakoutFeatureProvider` and `NoOpExecutionPolicy` | Internal proof helpers requiring no action now | Each is used only by `minimal_breakout_strategy()`/the Golden runner; neither has a consumer contract | In a narrow follow-up, make them private and remove their `application.__init__` re-exports; do not move them to tests while the retained proof needs them. |
| `application/golden_replay.py`: `AlwaysOpenSessionPolicy` | Public reusable policy/stub needing a real stable home | It expresses a meaningful continuous-crypto session rule, but exists only as a Golden subclass and is not exported from the module's `__all__` | Separate Strategy/ADR-0045 follow-up: decide a canonical continuous-session policy. Do not promote the Golden class by accident. |
| `application/golden_supervised.py`: `run_wave5_golden_supervised_proof`, `Wave5GoldenSupervisedProof` and private projection builders | Production proof orchestration worth keeping in `src` | `tools/golden_supervised_e2e.py` composes the bounded fixture, I04/I05 projection, and two deterministic training runs | Keep in Application and non-public. It remains unusable from an ordinary installed distribution when the default repository fixture is absent. |
| `application/wave6_golden.py`: `run_wave6_golden_e2e_proof`, `Wave6GoldenE2EProof` | Production proof orchestration worth keeping in `src` | `tools/wave6_golden_e2e.py` delegates to the bounded B06/D04/K07/K09 composition and its deterministic evidence | Keep in Application and non-public. |
| `application/wave6_golden.py`: `_FakeCatalog`, `_FakeBatchReader`, `_FakeRelocationCatalog` | Test fixture/support requiring a dedicated testing namespace | They implement only enough catalog/reader/relocation behavior for the Wave 6 bounded proof and are used solely by its private wiring | Follow-up slice: move the three doubles together to `quant_platform.testing.wave6_fakes` (or `tests/support` if the operator proof moves with them), keep their names private, and leave the retained runner as a thin composition. |

### Public API and typing impact

None of the inventory above is a public Python dependency API. The current
`quant_platform.application.__init__` re-exports of Golden functions and
helpers are convenience imports inside an installable package, not a
compatibility promise; `PUBLIC_PYTHON_API.md` controls that boundary.

The follow-up that narrows those re-exports must update operator tools to
import their one proof runner directly (or use a deliberately private
Application entrypoint). It must not remove a runner before the corresponding
tool has an equivalent path. Since the package ships `py.typed`, this prevents
type discovery from being mistaken for supported consumer ownership.

Moving the Wave 6 fakes may change import paths only for private proof code.
It must not make them public, package a generic fake DataGateway, or alter the
real `Catalog`, reader, or relocation protocols. The existing Golden proof
tests are the behavior-preserving regression evidence for that move.

### Follow-up slices

1. **Private Application export narrowing:** remove Golden helper re-exports
   from `application.__init__`; retain only the runner paths required by each
   operator tool, and add import-path regression coverage.
2. **Wave 6 testing namespace:** relocate `_FakeCatalog`, `_FakeBatchReader`,
   and `_FakeRelocationCatalog` together; prove the Wave 6 deterministic
   runner still exercises B06/D04/K07/K09 without exposing a generic fake.
3. **Continuous-session policy design:** decide whether the 24/7 semantics of
   `AlwaysOpenSessionPolicy` belong in Strategy as a canonical policy. This is
   a policy decision, not a rename of the Golden class.

## Consequences

- Golden proof runners stay under the Application owner, preserving the thin
  CLI-to-composition seam required by the existing ADRs.
- The only explicit source test doubles are contained to one future, atomic
  testing-namespace slice rather than copied or moved piecemeal.
- No fixture or Golden helper becomes public merely because it is importable
  from source or re-exported by `quant_platform.application`.

## Out of scope

- moving code, splitting modules, or removing existing re-exports in this
  design issue;
- generic fake gateway infrastructure or a new testing framework;
- mypy cleanup, Omega bridge implementation, or any replay/runtime redesign.

## Related

Parent tracking: #232. Closes issue #244.
