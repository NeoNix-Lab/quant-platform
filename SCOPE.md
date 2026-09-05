# Scope: Producer–Consumer Conformity Implementation Gate v1

## Goal

Complete the executable conformity seam between the Data Plane producer output
and the DataGateway consumer input, using the accepted
`PRODUCER_CONSUMER_CONFORMITY.md` contract as semantic authority. The Contract
Freeze Gate has passed. This cycle remains the controlled convergence period
before the Conformity Implementation Gate; it is not a redesign of the product
roadmap or architecture.

## Current state and roadmap position

The repository remains in **Phase A — Controlled Bidirectional Convergence**:

- Contract Freeze Gate: **PASSED**;
- Conformity Implementation Gate: **IN PROGRESS / PARTIAL**;
- Human vertical / Golden Bybit BTCUSDT E2E: **PASSED** on 2026-09-05;
- broader Producer/Consumer expansion remains blocked until the complete
  Conformity Implementation Gate passes.

Completed conformity slices on authoritative `origin/main` before the Human E2E
branch:

1. Shared Semantic Primitives v1 — **DONE**;
2. Canonical Parquet Materializer v1 — **DONE**;
3. Bounded DataGateway finite read v1 — **DONE**;
4. Manifest + Coverage Emission v1 — **DONE**;
5. Golden Conformity Acceptance Support v1 — **DONE** as reusable gate support;
6. Publication Certification runtime v1 — **DONE** for S13 Phases 1–3;
7. Publication Eligibility Bridge v1 — **DONE** for S14 / S13.5 Phases 4–5.

The Human E2E branch also closes two integration gaps discovered while preparing
the real vertical:

- ADR-0025 + `dataset-manifest-v2` represent a canonical dataset acquired
  directly from a source/archive without fabricating a raw dataset parent;
- the catalog schema-registry bootstrap establishes the frozen repository schema
  representation required by `catalog.datasets.schema_id`.

These remediations are part of the branch under closeout and do not broaden the
first vertical beyond Bybit BTCUSDT `trade-v1`.

## Human Golden E2E acceptance

The first real Human Golden E2E has passed for Bybit BTCUSDT UTC day
`2024-01-15`.

Observed acceptance facts:

```text
rows                 1,105,145
buy                    553,875
sell                   551,270
other side                    0
first exchange_ts     2024-01-15T00:00:00.492Z
last exchange_ts      2024-01-15T23:59:59.931Z
S13                    pass
S14                    valid
coverage_complete      true
coverage_gaps          0
catalog partitions     1
DataGateway batches    17
max batch size         65,536
lifecycle              OPEN → READING → COMPLETED
Golden comparison      exact match
```

Durable identity evidence captured from the successful run:

```text
source fingerprint
  a707835c5ca7743ff8879383ca5127111b23bb041289c4c888acb2f294ac9609

physical artifact SHA256
  a9581327807fe2c3286bab8b2e6a2ce82dbb7d28a1724bc4d47bc66b9199ef6f

CanonicalContentHashV1 payload
  e0a2bc287aef3b95f07d584b5203e3ddd1f8b9807347e609f69618525f41eedf
```

The runtime observation used `DataGateway.scan()` and completed in 17 bounded
batches. Memory telemetry was not sampled during this operator run; therefore
the Golden evidence records bounded batch behaviour, while the BR1
memory-independence property remains established by the bounded DataGateway
implementation and its dedicated tests.

The successful artifacts and catalog rows are acceptance evidence and are not
to be destructively cleaned up or silently overwritten during closeout.

## Remaining critical path

The Human E2E milestone is complete. The remaining path to the Conformity
Implementation Gate review is now:

8. Human vertical / Golden Bybit BTCUSDT E2E — **DONE / PASS**;
9. adversarial acceptance;
10. Candle ordering compatibility acceptance;
11. final Conformity Implementation Gate review.

The Human E2E PASS does **not** by itself promote the complete Conformity
Implementation Gate to PASS.

## In scope

- closeout and review of the first Human/Golden Bybit vertical;
- preservation of the successful acceptance evidence;
- required adversarial acceptance, including zero-event coverage, gaps,
  supersession, overlap, relocation, rebuild, physical-layout variation and
  abort-before-completion;
- CandleDefinition ordering-compatibility acceptance;
- final evidence and review needed for Conformity Implementation Gate PASS;
- governance synchronization that records current status without widening the
  first vertical.

The implementation dependency order and normative requirements remain frozen in
`docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md` §23. Shared/core remains
source-neutral; source-specific components depend toward generic/core, never the
inverse. Quantitative business logic remains outside App UI, TUI and CLI
clients, and upper layers do not bypass the DataGateway boundary.

## Controlled convergence rule

Until the Conformity Implementation Gate passes, broader vertical expansion is
blocked. Producer and Consumer work is allowed only when it directly advances
one of the remaining gate acceptance items or closes reviewed defects in the
first vertical.

## Explicitly blocked until Conformity Implementation Gate PASS

- full Candle runtime;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and live collection;
- multi-venue runtime;
- L1, L2, L3/MBO runtime;
- unrelated consumer or producer vertical expansion;
- broader paper/live operational mechanics.

## Package topology boundary

Broad package restructuring remains **OUT OF SCOPE** for the current Conformity
closeout. Immediately after Conformity Implementation Gate PASS, the next
structural checkpoint remains **Package Boundary / Modular Monolith Foundation
v1**, followed by **Legacy Capability Harvest Audit v1**, before broad
bidirectional expansion.

## Preserved future roadmap

Producer roadmap after the conformity gate remains: capacity monitoring,
storage health/pressure behavior, RAW/source protection, safe placement and
relocation, backup/restore and verification, retention/deletion authority,
backfill/repair, live trades, multi-venue trades, L1, L2, L3/MBO and advanced
recovery/reconciliation.

Consumer/Product roadmap after the conformity gate remains: Candle runtime,
FeatureDefinition/FeatureSetDefinition/FeatureArtifact, Research and Outcomes,
Validation/Labeling, Strategy/DecisionIntent, Execution/Replay/Portfolio,
Experiment orchestration, API/job runtime, supervised ML, strategic/execution
RL, clients, paper/shadow and live operation.

## Out of scope for this governance closeout

- declaring the Conformity Implementation Gate PASS before adversarial,
  Candle-ordering and final gate review complete;
- changing `trade-v1`, partition/coverage contracts, CandleDefinition,
  DataGateway or Consumer API semantics;
- introducing a fake raw parent or parallel source-provenance architecture;
- broad multi-venue/live/runtime expansion;
- destructive cleanup of the successful Human E2E evidence;
- treating documentation closeout as a substitute for branch architecture,
  functional/code review or CI.

## Completion criteria for this closeout

- Human Golden E2E is recorded as **PASS** with exact observed counts, bounds,
  lifecycle and content identities;
- ADR-0025 / dataset-manifest-v2 and schema-registry bootstrap are reflected as
  first-vertical integration remediations, without claiming broader readiness;
- the remaining critical path begins with adversarial acceptance;
- Conformity Implementation Gate remains **IN PROGRESS / PARTIAL**;
- successful E2E artifacts/catalog rows are treated as retained evidence;
- final branch review and CI remain mandatory before merge/release.
