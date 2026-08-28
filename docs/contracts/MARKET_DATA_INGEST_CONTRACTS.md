# Market Data Ingest Contracts

**Status:** Contract foundation v1 — producer-side semantics; no new record schema

This document defines obligations at the producer boundary. It reuses the
existing contracts rather than creating a second identity, provenance, quality,
catalog, or partition model.

## 1. Authorities and non-duplication

The following remain authoritative:

- [CORE_CONTRACTS.md](CORE_CONTRACTS.md) for common identity and domain terms;
- [DATA_GATEWAY.md](DATA_GATEWAY.md) for consumer request, coverage, and read
  eligibility;
- [trade-v1.json](../../schemas/trade-v1.json) for frozen trade semantics;
- [dataset-manifest-v1.json](../../schemas/dataset-manifest-v1.json) for dataset
  identity and lineage declaration;
- [partition-manifest-v1.json](../../schemas/partition-manifest-v1.json) for
  partition metadata, lifecycle, and observed event bounds;
- [ADR-0004](../decisions/ADR-0004-market-data-levels.md) for L1/L2/L3 terms;
- [ADR-0018](../decisions/ADR-0018-frozen-market-data-contract-evolution.md)
  for future contract versions;
- `db/init/001_catalog.sql` as the current concrete runtime catalog/storage-root
  implementation baseline that must be reused; it is not, by itself, an
  immutable freeze of every current DDL detail.

No section below changes those authorities.

## 2. Producer obligation

Market Data Ingest must produce a partition that is:

- represented by the existing natural DatasetIdentity and partition identity;
- compatible with its versioned record schema;
- traceable through manifest, content, lineage, producer, and code provenance;
- classified with existing lifecycle vocabulary;
- reconciled against known source and temporal evidence;
- accompanied by quality and provenance evidence; DataGateway, not the
  producer, applies its own read-eligibility policy.

The producer must fail visibly on contradictory identity, invalid schema,
unresolved source conflict, or fabricated source fact.

## 3. Source adapter obligations

An adapter must expose or preserve, where the source supplies them:

- authentication and protocol boundaries;
- source pagination or subscription behavior;
- native venue/instrument identifiers;
- exchange timestamp and precision;
- trade/aggressor semantics;
- native trade, sequence, and order identifiers;
- snapshot versus incremental meaning;
- source-specific validation and failure conditions;
- recovery and backfill capabilities;
- source provenance.

The adapter may translate source representation into an internal mapping, but
must not silently infer unavailable values. It must not own feature, research,
strategy, execution, or client semantics.

## 4. Acquisition invariants

Historical, backfill, and live modes may use different transports and runtime
components, but they share these invariants:

- the same market fact has compatible canonical meaning;
- source timestamp precision is preserved;
- source-native identity is preserved where available;
- missing receive time, sequence, trade ID, order ID, or ordering guarantee is
  represented as missing;
- the requested acquisition scope is explicit;
- source coverage, gaps, overlaps, and limitations are recorded;
- retry and restart do not silently create a second semantic dataset.

Historical and backfill are finite operations. Live is persistent acquisition;
its reconnect, checkpoint, rate-limit, and health behavior is operational and
does not define a new consumer API.

## 5. Canonicalization obligations

Canonicalization must:

- target an explicit versioned record contract;
- preserve exact decimal and timestamp semantics;
- apply source mappings only when supported by source evidence;
- preserve nullable source fields rather than fabricate replacements;
- reject or quarantine invalid records according to an explicit policy;
- be deterministic for the same source input and semantic configuration;
- record the semantic transformation and execution code identity through the
  existing lineage and partition provenance model.

The frozen `trade-v1` contract remains unchanged. L1, L2, and L3/MBO contracts
require the ADR-0018 evolution process.

## 6. Observed event bounds versus declared coverage

These are different concepts.

### Observed event bounds

`partition-manifest-v1` fields `first_exchange_ts` and `last_exchange_ts` are
the timestamps of actual first and last records. When `row_count == 0`, both
are null. They must never be populated with interval boundaries.

### Declared coverage

Declared coverage is the responsibility interval used to determine whether a
DataGateway request is covered, including an interval containing no events.
It must be deterministic, half-open where required by DataGateway semantics,
and independent from observed event bounds.

The exact population rule for `catalog.partitions.ts_start` and `ts_end` is not
fully frozen in current documentation. The common `dt=YYYY-MM-DD` to UTC-day
interval mapping must not be assumed universally until the partition-key
contract confirms it. This is an open decision, not a reason to modify the
frozen partition manifest.

## 7. Zero-row semantics

Zero rows do not imply no coverage. A declared responsibility interval may be
fully covered while its materialized partition has:

```text
row_count = 0
first_exchange_ts = null
last_exchange_ts = null
```

The declared interval must come from the Data Plane’s coverage metadata, not
synthetic event timestamps. DataGateway must distinguish:

```text
no eligible coverage
        ≠
complete eligible coverage with zero events
```

The implementation of the declared-coverage metadata is open; the frozen
observed-bound fields must remain truthful.

## 8. Sealing and publication

The semantic publication sequence is:

```text
`writing`
      ↓
sealed / `closed`
      ↓
manifest and catalog reconciliation
      ↓
quality and certification evidence
      ↓
`valid` / `degraded` / `invalid`
```

The invariant is that a consumer must not observe a partially materialized
partition. This does not require an impossible transaction spanning a
filesystem and PostgreSQL. It requires crash-safe staging, idempotent retry,
natural-identity-keyed reconciliation, and recovery after interruption.

The producer emits the lifecycle classification and supporting evidence; it
does not evaluate a caller's DataGateway lifecycle policy. DataGateway
independently applies its own read-eligibility policy. `writing` is not
default-eligible. `closed` is sealed but not certified. `valid` is
default-eligible. `degraded` requires explicit policy. `invalid` and
`superseded` are excluded. “Published” should be used only with a stated
meaning such as catalog-registered or default-eligible.

## 9. Manifest-to-catalog handoff

The existing PostgreSQL catalog is the runtime query index. Durable manifests
remain identity, provenance, rebuild, and reconciliation evidence.

The missing producer bridge must reconcile:

```text
canonical artifact
+ dataset manifest
+ partition manifest
        ↓
catalog dataset/partition/lineage metadata
```

It must reuse natural identity, partition revision, storage roots, hashes,
lineage, producer, and `code_ref`. It must not introduce a second catalog or
filesystem-derived consumer identity.

## 10. Quality and reconciliation

Quality evidence may include source integrity, temporal coverage, gaps,
overlaps, duplicate identities, sequence continuity where guaranteed, snapshot
consistency, schema validity, row and byte counts, checksums, and degradation
reason.

Existing `quality_reports`, lifecycle states, manifests, catalog metadata,
lineage, and provenance are the intended model. The exact quality-report to
`valid`/`degraded`/`invalid` transition remains open.

Partition uniqueness, revision, supersession, and catalog constraints provide
mechanical protection. They do not solve source precedence, overlap resolution,
reconnect overlap, repair triggering, or deduplication when source identity is
absent.

## 11. Concurrency and recovery

Current mechanical protections include:

- unique natural dataset identity;
- unique `(dataset, partition_key, revision)`;
- at most one non-superseded revision per partition key;
- lifecycle exclusion of `writing` from normal reads;
- manifest and content hashes;
- catalog rebuild logging.

These are not a complete writer-coordination protocol. Leases, leader
election, distributed locks, and a coordinator service are deliberately not
defined here.

Open recovery cases include:

- simultaneous live and backfill coverage;
- interrupted canonical write or manifest write;
- file present without catalog registration;
- catalog row without file;
- divergent manifest copies;
- partial relocation;
- retry after a process or host failure.

Any future repair may use revision `N+1`, validation, and supersession, but the
algorithm and source precedence remain open.

## 12. Provenance minimum

The producer should preserve the existing stable chain:

```text
natural dataset identity
  → record schema identity
  → natural partition identity and revision
  → manifest/content hashes
  → lineage transformation
  → producer and execution code reference
```

Source and acquisition details must be retained where necessary for
reproducibility, without changing frozen v1 fields or creating a competing
provenance system. If the current v1 manifests cannot represent a source fact,
the representation requires an explicit future contract or migration-specific
artifact rather than an invented value.

## 13. Non-responsibilities

Ingest contracts do not define:

- FeatureDefinition or FeatureSetDefinition;
- candles or derived features;
- hypotheses, events, labels, or validation recipes;
- strategy, execution, PnL, or model training;
- API/client UI behavior;
- live DataGateway transport or cursor semantics;
- L1/L2/L3/MBO JSON schemas;
- Docker topology or storage technology.

## 14. Open producer decisions

- declared coverage population rule;
- quality-to-lifecycle transition;
- publication/reconciliation protocol details;
- live/backfill source precedence and overlap policy;
- deduplication without native identity;
- second-venue deterministic ordering integration;
- source provenance extension;
- checkpoint and recovery semantics;
- exact future L1/L2/L3/MBO contracts.
