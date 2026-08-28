# Market Data Ingest Architecture

**Status:** Architecture v1 — documentation foundation
**Owner:** Data Plane / producer side

This document elaborates the producer side of the existing Data Plane. It does
not create a new top-level layer, replace the DataGateway boundary, or define
new record schemas.

## 1. Architectural position

The authoritative platform flow remains:

```text
MARKET DATA SOURCES
        ↓
DATA PLANE
        ↓
DataGateway
        ↓
Representations / Features / Research / Execution / API clients
```

Market Data Ingest is a producer-side capability inside the Data Plane:

```text
DATA PLANE
├── Market Data Ingest
│   ├── Source / Venue Adapters
│   ├── Historical Acquisition
│   ├── Backfill / Repair
│   ├── Live Collection
│   ├── Canonicalization
│   ├── Quality / Reconciliation
│   └── Partition Publication
├── Storage Lifecycle
├── Schemas and Manifests
├── Catalog and Storage Roots
├── Lineage
└── Quality Metadata
```

The existing definitions remain authoritative: [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md),
[CORE_CONTRACTS.md](../contracts/CORE_CONTRACTS.md), the versioned schemas, and
the accepted ADRs.

## 2. Producer flow

```text
External Source
      ↓
Source / Venue Adapter
      ↓
Historical / Backfill / Live Acquisition
      ↓
Source-native preservation where justified
      ↓
Canonicalization
      ↓
Quality / Reconciliation
      ↓
Partition Materialization
      ↓
Sealing and manifest creation
      ↓
Catalog registration / reconciliation
      ↓
Storage placement and lifecycle
      ↓
Eligible canonical Data Plane
      ↓
DataGateway
```

The producer owns the work required to create a materialized, provenance-
bearing, quality-classified partition. DataGateway begins at logical discovery
and reading of eligible Data Plane artifacts.

## 3. Source and venue adapters

`SourceAdapter` / `VenueAdapter` is a semantic boundary, not a prescribed
Python class hierarchy or package layout. It isolates venue-specific facts from
the canonical Data Plane.

An adapter may own:

- authentication and protocol/API interaction;
- archive access, REST pagination, and live subscriptions;
- venue and instrument mapping;
- exchange timestamp interpretation and precision;
- native trade, sequence, and order identifiers;
- aggressor-side interpretation;
- snapshot and incremental/event semantics;
- source-specific validation, errors, and recovery primitives;
- source provenance and capability declarations.

An adapter does not own dataset identity, the catalog model, feature
computation, research, strategy, execution, or client behavior. Missing source
facts remain missing; the adapter must not manufacture receive timestamps,
sequences, trade IDs, order IDs, or ordering guarantees.

## 4. Acquisition modes

### Historical acquisition

Finite retrieval from archives, dumps, provider APIs, or preserved legacy
sources. It may produce a new historical partition or provide source material
for canonicalization.

### Backfill / repair

Finite acquisition intended to repair gaps, replace degraded coverage, or
reconcile a known source defect. Existing revision and supersession mechanisms
provide mechanical support, but do not decide source precedence, overlap
resolution, or deduplication.

### Live collection

Persistent acquisition from WebSocket, streaming, or native provider feeds. It
requires operational lifecycle, checkpoint, reconnect, rate-limit, health, and
gap handling. A live collector is not required to use the same implementation
as a finite job.

All three modes should reuse semantic mappings and canonicalization rules when
they represent the same market fact. Transport and recovery behavior may
differ.

## 5. Market-data levels

The level names retain their market-data meaning from [ADR-0004](../decisions/ADR-0004-market-data-levels.md):

| Level | Meaning | Ingest position |
|---|---|---|
| Trades | Executed market trades | First canonical producer slice |
| L1 | Top-of-book | Future versioned contract and acquisition |
| L2 | Aggregated depth | Future snapshot/incremental contract |
| L3 / MBO | Native order-level events | Future order-lifecycle acquisition |
| Metadata | Venue/instrument/session facts | Source-specific supporting data |

Delta, imbalance, absorption, spoofing, labels, and signals are not levels of
market data. They belong to Feature or Research layers.

## 6. Capability model

Every source capability should be described across these dimensions without
assuming that a venue supports all combinations:

| Dimension | Examples |
|---|---|
| Venue | Native venue identity |
| Instrument / market | Native symbol, market/category, instrument metadata |
| Mode | Historical, backfill, live |
| Level | Trades, L1, L2, L3/MBO, metadata |
| Coverage | Time range, precision, gaps, overlaps |
| Source form | Archive, REST, WebSocket, snapshot, incremental |
| Native identity | Trade ID, sequence, order ID where supplied |
| Observation | Real receive timestamp availability |
| Recovery | Reconnect, replay, snapshot reset, backfill support |
| Quality | Valid, degraded, invalid, with evidence and reason |
| Certification | Sealed, catalog-registered, default-eligible or not |

`UNKNOWN` and `TBD` are valid capability values. No provider capability is
asserted here without repository evidence or a later provider-specific review.

## 7. Historical/live convergence

Historical and live representations of the same market fact should converge on
the same canonical semantics. For example, both may eventually canonicalize to
`trade-v1` when that frozen contract is sufficient.

The source facts remain different where they are genuinely different:

- historical `receive_ts` is null when no local receive event was observed;
- live `receive_ts` may contain the actual local observation time;
- archive ordering is not a live sequence;
- live reconnect and snapshot recovery are operational concerns.

The broader historical/live strategy rule remains in
[ADR-0013](../decisions/ADR-0013-historical-live-semantics.md). The live
DataGateway stream, cursor, and identity contract remain open.

## 8. Publication boundary

Use precise lifecycle terms:

1. `materialized`: data has been written to a staging or partition artifact;
2. `sealed`: the artifact is durably finalized and its content identity is
   known;
3. `catalog-registered`: manifests and catalog metadata agree;
4. `validated`: quality evidence supports a lifecycle classification;
5. `default-eligible`: the partition is `valid` under the DataGateway contract.

If “published” is used, it means the final state of this producer process for a
canonical partition: sealed content, valid manifests, catalog registration,
provenance, and a lifecycle state visible to the consumer policy. It does not
mean that a filesystem rename and PostgreSQL transaction were one atomic
operation.

`writing` is not consumer-eligible. `closed` is sealed but not necessarily
validated. `valid` is eligible by default. `degraded` requires explicit policy;
`invalid` and `superseded` are excluded. The exact consumer semantics belong to
[DATA_GATEWAY.md](../contracts/DATA_GATEWAY.md).

The manifest-to-catalog bridge is currently a missing producer capability. It
must reuse the PostgreSQL market catalog as the runtime index and durable
manifests as reconstruction and reconciliation evidence.

## 9. Quality and reconciliation

Quality evidence should reuse lifecycle state, `quality_reports`, checksums,
coverage, lineage, provenance, and native sequence evidence where available.
The mapping from a quality result to `valid`, `degraded`, or `invalid` is not
fully frozen and must remain an explicit decision.

Ingest may detect source gaps, overlaps, duplicate identities, sequence breaks,
snapshot inconsistency, and canonical schema violations. It must not silently
choose a source, synthesize missing identity, or conceal an unresolved conflict.

Backfill may eventually produce revision `N+1`, validate it, and supersede
revision `N`; this document does not freeze a universal repair algorithm.

## 10. Storage lifecycle relationship

Storage placement is part of Data Plane operations, not a new data identity
system. The detailed model is in [STORAGE_LIFECYCLE.md](STORAGE_LIFECYCLE.md).

Ingest must account for staging and writing capacity as well as final sealed
artifact size. Tier movement must preserve logical identity, content identity,
manifest consistency, and consumer continuity.

## 11. MBO / L3 positioning

Future L3/MBO acquisition can preserve evidence for book reconstruction, order
lifecycle, add/modify/cancel, resting duration, replenishment, cancellation,
and queue dynamics. It does not contain absorption detectors, manipulation
detectors, signals, labels, trading rules, or PnL.

New L3/MBO contracts must follow [ADR-0018](../decisions/ADR-0018-frozen-market-data-contract-evolution.md).
No MBO schema or reconstruction algorithm is defined here.

## 12. Runtime and deployment

Finite historical/backfill acquisition may use the existing asynchronous job
concept. A persistent live collector has different lifecycle and recovery
semantics. Tiering, backup, and health monitoring may also be finite or
persistent operational workloads.

The deployment topology remains open. This document does not prescribe a
container count, process topology, broker, scheduler, async framework, or
checkpoint database.

## 13. Parallel development

Producer work owns adapters, acquisition, source preservation,
canonicalization, reconciliation, publication, and storage operations.

Consumer work owns DataGateway requests and reads, representations, candles,
features, research, strategy, execution, experiments, API, and clients.

The streams may evolve independently until a change affects shared schemas,
identity, lifecycle eligibility, coverage, lineage, provenance, catalog
structure, or a future live stream contract. Such changes require coordinated
review and versioned migration where applicable.

## 14. Open decisions

- declared coverage population and its relationship to observed event bounds;
- publication/sealing/reconciliation protocol details;
- quality-report to lifecycle-state transition;
- live/backfill overlap, precedence, and repair triggering;
- deduplication when native identity is absent;
- second-venue ordering and tie-break integration;
- exact L1/L2/L3/MBO contracts;
- live DataGateway cursor and stream semantics;
- runtime checkpoint and deployment choices.
Storage-specific decisions are maintained in [STORAGE_LIFECYCLE.md](STORAGE_LIFECYCLE.md)
and [OPEN_DECISIONS.md](OPEN_DECISIONS.md).
