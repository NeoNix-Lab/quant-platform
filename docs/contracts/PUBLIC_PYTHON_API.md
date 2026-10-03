# Public Python Dependency Surface v1

**Status:** Public dependency contract v1

**Scope:** the small, supported Python import surface for an installed
`quant-platform` distribution. This is a package-consumer contract, not the
Consumer API/transport contract in [CONSUMER_API.md](CONSUMER_API.md).

## Version and typing

Consumers obtain the installed distribution version from:

```python
import quant_platform

quant_platform.__version__
```

The value is read from installed package metadata; a source checkout that has
not been installed reports `0+unknown` rather than carrying a second version
constant. `quant_platform` ships `py.typed`, so the import paths listed below
are intended to be consumed as typed Python APIs.

## Supported imports

The following package-level imports are the stable v1 surface. Their types and
fail-closed behavior remain governed by their existing contracts; this document
does not widen those contracts.

| Import path | Supported v1 purpose |
| --- | --- |
| `quant_platform.access` | Historical `DataGateway`, `DataRequest`, `DataSlice`, `DataSliceMetadata`, `CoveragePolicy`, and `LifecyclePolicy`. [DATA_GATEWAY.md](DATA_GATEWAY.md) remains the semantic authority. |
| `quant_platform.validation` | Validation temporal inputs (`Instant`, `CoverageInterval`); availability/purge/embargo classification (`CandidateClassification`, `CandidateClassificationResult`, `DependencyCutoffRole`, `DependencyMaturity`, `DependencyLifecycle`, `Embargo`, `DependencyEvidence`, `ValidationCandidate`, `classify_candidate`); walk-forward primitives (`WalkForwardFold`, `WalkForwardScheduleSpec`, `build_walk_forward_folds`); and F08 DSR-L/PBO (`ComparableTrialPanel`, `EffectiveTrialCountEvidence`, `EvaluationStatus`, `DSRResult`, `PBOResult`, `evaluate_dsr_v1`, `evaluate_pbo_v1`). |
| `quant_platform.replay` | Selected deterministic replay primitives: `ReplaySpec`, `ReplayContext`, `ReplayResult`, `HistoricalReplayRuntime`, `ReplayEngine`, and `ReplayError`. |

Consumers must import from those package paths, not from their implementation
submodules. For example:

```python
from quant_platform.access import DataGateway, DataRequest
from quant_platform.validation import Embargo, evaluate_dsr_v1
from quant_platform.replay import HistoricalReplayRuntime, ReplaySpec
```

`DataGateway` remains the finite historical access seam. It does not turn
catalog, storage, physical Parquet paths, or live-stream machinery into a
public dependency contract.

## Explicit exclusions

The following are deliberately not public dependency APIs in v1:

- implementation modules below the supported packages, including
  `quant_platform.access.gateway`, `quant_platform.validation.robustness`, and
  `quant_platform.validation.availability`;
- `quant_platform.application.golden_*` and `quant_platform.application.wave6_golden`;
- repository-relative Golden/proof helpers and their fixtures (for example,
  the Wave 5 fixture loaded by `quant_platform.application.golden_supervised`);
- `tests`, `tools`, schemas, repository-local database/bootstrap artifacts,
  and any private helper prefixed with `_`.

Golden/proof helpers use repository-relative fixture evidence by design and
are therefore non-portable after a normal package installation. They remain
test/proof machinery, not supported library functions. This issue documents
that boundary; it does not move proof code or package fixtures.

[ADR-0056](../decisions/ADR-0056-golden-proof-test-double-boundary-v1.md)
classifies the Golden modules and their private doubles. A name being re-exported
by `quant_platform.application` does not override these exclusions or create a
consumer compatibility promise.

## Compatibility policy

The listed import paths and exported names are the supported v1 dependency
surface. Additive exports require documentation and an installed-package smoke
test. Removing or changing a listed import is a future versioned compatibility
decision. Nothing else is implied public merely because it is importable from a
source checkout.

## Omega validation bridge phase 1

The phase-1 Omega bridge is governed by
[ADR-0055](../decisions/ADR-0055-omega-validation-bridge-v1.md). Its
consumer-facing imports are limited to the documented `quant_platform.validation`
names above. In particular, the temporal input and dependency enum values are
public here because they are necessary to construct a `DependencyEvidence`
honestly; a consumer must not import them from `quant_platform.data.models` or
`quant_platform.validation.availability`.
