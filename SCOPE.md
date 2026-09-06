# Scope: Package Boundary / Modular Monolith Foundation v1

## Goal

Execute the mandatory post-Gate structural checkpoint accepted by
[ADR-0024](docs/decisions/ADR-0024-package-boundary-modular-monolith-v1.md):
establish bounded package ownership and make dependency direction mechanically
testable, before broad independent Producer/Consumer expansion.

This is a structural checkpoint, not a new product phase and not a numbered
roadmap phase. It changes package ownership and import direction only. It does
not change DataGateway semantics, Producer–Consumer conformity semantics, any
frozen contract, schema, DDL or fixture.

## Cycle state

```text
baseline                 = 9c938a57daee9e37dfc98c56e88721d42e6391af
audit                    = COMPLETE
implementation candidate = 900c128ddf063b9e4ea393596fb37aa3c0692ba8
branch                   = implementation/package-boundary-foundation-v1
PR                       = #25 MERGED
merge commit             = 7d531fcd8eb46b3d562de93ccae9c2352f2706fa
scope state              = COMPLETE
```

**Package Boundary / Modular Monolith Foundation v1 is `COMPLETE`.** The
implementation candidate, independent review, exact-head CI and merge to
authoritative `origin/main` are closed. The next governed checkpoint is
**Legacy Capability Harvest Audit v1**; broad independent expansion still waits
for that audit.

## Baseline: completed predecessor cycle

Recorded here as the baseline this scope builds on, not as work in this scope.

**Phase A — Controlled Bidirectional Convergence is COMPLETE** at baseline
`9c938a5`:

- Contract Freeze Gate: **PASSED**;
- Conformity Implementation Gate: **PASSED**;
- Human vertical / Golden Bybit BTCUSDT E2E: **PASS** for UTC day
  `2024-01-15`, with an exact Golden match through `DataGateway.scan()`;
- Adversarial Acceptance A1–A9: **PASS**;
- Candle Ordering Compatibility: **PASS**;
- Final Conformity Gate Review: **APPROVE** with `BLOCKERS: NONE` and
  `IMPORTANT: NONE`;
- temporary Producer/Consumer lockstep: **REMOVED**.

The exact Human E2E observations and durable identities remain recorded in
[`docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md`](docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md).

## Audit outcome and current ownership

The structural audit is **COMPLETE**. It used evidence from the finished Golden
vertical rather than assumed bounded contexts, as ADR-0024 section 5 requires.
The resulting ownership is:

```text
quant_platform.access
  owns Gateway / read catalog / Access models

quant_platform.data
  owns shared canonical primitives + Producer

quant_platform.data.parquet
  remains deliberate shared physical seam

quant_platform.source_adapters
  remains source-specific
```

Concretely:

- `quant_platform.access` owns `DataGateway`, `DataScan`,
  `DataScanOpenMetadata`, `ScanState`, the read `Catalog`, and the Access
  request/result/locator models (`DataRequest`, `DataSlice`,
  `DataSliceMetadata`, `CatalogDataset`, `CatalogPartition`,
  `LifecyclePolicy`) with their fingerprint and interval helpers;
- `quant_platform.data` retains the shared canonical identity, time, record,
  hash and error primitives, and continues to host the Producer modules
  (materializer, manifests, coverage, publication, publication catalog and
  publication eligibility);
- `quant_platform.data.parquet` is a **deliberate shared physical seam**: it is
  the canonical physical reader/locator used by both the Access read path and
  the Producer write path, and is owned as `physical`, not as Producer
  orchestration;
- `quant_platform.source_adapters` remains source-specific and is not
  restructured by this scope.

## Dependency rules

```text
shared !→ source-specific
producer !→ access
access !→ producer orchestration/publication
```

These rules are **mechanically enforced**, not documentary. The architecture
proof lives in [`tests/test_package_boundaries_v1.py`](tests/test_package_boundaries_v1.py)
and runs in the standard local suite and CI. It resolves absolute and relative
imports, aliases, re-exports and package initialization; it refuses star and
dynamic imports rather than leaving them untracked; it requires every runtime
module to carry an explicit ownership decision; and it asserts that the shared
façade does not eagerly load Access or Producer runtimes.

## In scope

- extract the Access package and its models from the generic `data` package;
- narrow the `quant_platform.data` façade so shared consumers do not eagerly
  load Access or Producer runtimes;
- migrate call sites explicitly to their owner modules, with no compatibility
  shim reintroducing a catch-all;
- add the executable architecture proof for ownership and dependency
  direction;
- record the resolved structural choices in the existing authorities
  (ADR-0024 and `docs/architecture/OPEN_DECISIONS.md`).

## Out of scope

- changing `trade-v1`, dataset/partition/coverage contracts, CandleDefinition,
  DataGateway, Consumer API or any accepted ADR semantics;
- weakening or replacing frozen invariants;
- changing identity, hash, ordering, lifecycle or error semantics of any moved
  class or function;
- changing schemas, DDL, fixtures or the frozen S13/S14 publication path;
- restructuring `quant_platform.source_adapters`;
- deciding executable host placement, independently installable packages,
  service/deployment split or process topology;
- creating a parallel canonical architecture, a new numbered roadmap phase or a
  generic framework;
- broad independent Producer/Consumer expansion, which does not begin until
  this scope is `COMPLETE`.

## Next scopes

```text
Package Boundary / Modular Monolith Foundation v1   (COMPLETE)
        ↓
Legacy Capability Harvest Audit v1                  (NEXT)
        ↓
Broad independent Producer / Consumer expansion
```

Legacy code remains evidence only and is classified capability-by-capability as
ADOPT / ADAPT / REVIEW / REJECT against current canonical semantics.

## Unimplemented post-Gate capabilities

Unchanged by this scope and still not implemented:

- full Candle runtime — no longer blocked by the Conformity Gate;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and live collection;
- multi-venue runtime;
- L1, L2, L3/MBO runtime;
- unrelated consumer or producer vertical expansion;
- broader paper/live operational mechanics;
- the Producer and Consumer/Product roadmaps preserved in
  [`docs/product/ROADMAP.md`](docs/product/ROADMAP.md).

**Server Access & Runtime Identity Hardening v1** remains a separate,
non-blocking operational follow-up tracked in
[`docs/architecture/OPEN_DECISIONS.md`](docs/architecture/OPEN_DECISIONS.md).

Maintenance, documentation and tests may proceed when they do not weaken a
frozen contract or bypass the current scope boundary.

## Completion criteria

- `quant_platform.access` owns Gateway, read catalog and Access models;
  `quant_platform.data` owns shared canonical primitives and Producer;
  `quant_platform.data.parquet` remains the shared physical seam;
  `quant_platform.source_adapters` remains source-specific;
- the three dependency rules above are enforced by executable architecture
  tests covering alias, re-export and dynamic-import forms;
- the shared façade does not eagerly load Access or Producer runtimes and no
  compatibility shim reintroduces the catch-all;
- moved classes and functions preserve identity, hash, ordering, lifecycle and
  error semantics; S13/S14 and the Bybit vertical are unchanged;
- the resolved structural choices are recorded in ADR-0024 and
  `docs/architecture/OPEN_DECISIONS.md`, and choices this slice did not
  exercise remain explicitly open;
- `python tools/run_tests.py`, `python tools/check_markdown_links.py` and
  `git diff --check` pass;
- no schemas, DDL, fixtures or frozen normative semantics are changed;
- independent review, CI and merge to authoritative `origin/main` are closed —
  only then is this scope `COMPLETE`.

All completion criteria were satisfied before PR #25 merged as
`7d531fcd8eb46b3d562de93ccae9c2352f2706fa`.
