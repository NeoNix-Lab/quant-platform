# DataGateway Contract v1

**Status:** Contract v1 — implementation not present

**Scope:** historical, catalog-backed access to canonical market datasets

## 1. Purpose and boundary

`DataGateway` is the logical market-data access boundary between the canonical
data plane and every higher quantitative layer. A caller asks for a logical
dataset and a semantic slice. The gateway discovers the catalog record,
selects eligible partitions, resolves their storage internally, reads the
canonical records, and returns data together with the identity and provenance
needed to reproduce the result.

The gateway is not an alternate data model. It reuses the existing dataset,
partition, manifest, schema and catalog contracts. The first implementation is
therefore expected to resolve one canonical `trade-v1` dataset, but the
contract is not hard-coded to six files, one venue, or one physical storage
root.

### Responsibilities

- resolve a logical dataset identity through the PostgreSQL catalog and
  manifests;
- select all relevant partitions for a requested interval and state policy;
- apply UTC, half-open temporal slicing and deterministic ordering;
- perform schema-aware reads without silently changing record meaning;
- resolve storage roots and relative paths internally and safely;
- return canonical dataset, partition and provenance metadata;
- distinguish absent coverage, valid emptiness, invalid state and corruption;
- preserve historical semantics across physical relocation.

### Non-responsibilities

DataGateway does not compute candles, features, labels, research events,
signals, strategies, execution results, portfolio state, ML/RL inputs, or UI
payloads. It does not expose physical storage policy to callers, choose a
cache/distribution policy, or accept arbitrary filesystem paths. Physical
Parquet/file access, catalog SQL and path construction are implementation
details behind this boundary.

## 2. Evidence and vocabulary

The canonical sources reviewed for this contract are:

- `schemas/dataset-manifest-v1.json` for natural dataset identity, relative
  root and lineage;
- `schemas/partition-manifest-v1.json` for partition coverage, hashes,
  lifecycle and producer/code identity;
- `schemas/trade-v1.json` for exact decimal fields, exchange/receive time,
  optional native identifiers and sequence;
- `db/init/001_catalog.sql` for `datasets`, `partitions`, `storage_roots`,
  `schema_registry`, lineage and path-safety constraints;
- `tools/semantic_validator.py` for derived identity/path validation;
- `tools/import_bybit_trades.py` for the canonical Bybit `trade-v1` import
  semantics;
- `docs/contracts/CORE_CONTRACTS.md` and
  `docs/integration/MARKET_PLATFORM_INTEGRATION.md` for shared identity and
  integration principles.

Legacy `ml_core` access patterns in `src/core/market_data_store.py`,
`src/core/dataset_adaptation.py`, `src/core/supervised.py`,
`src/core/core_app.py` and historical TUI/application code are migration
evidence only. Their path-based loaders, dataframe assumptions or runtime
facades are not this contract.

## 3. Logical request

The minimal logical request is named `DataRequest`. Its conceptual fields are:

```text
DataRequest
  dataset_selector: natural DatasetIdentity or an explicitly named catalog identity
  venue: logical venue constraint when not already fixed by the selector
  instrument: logical instrument constraint when not already fixed by the selector
  market_type: market semantic, when applicable
  data_kind: trades, l2, footprint, or another canonical kind
  record_schema_id: required schema identity, or an explicitly compatible schema policy
  interval: start and end UTC instants
  ordering: declared deterministic ordering policy
  fields: optional validated projection
  partition_states: eligibility policy, defaulting to readable canonical states
```

The exact programming-language representation remains an implementation
choice. A request must not contain an absolute path, storage-root mount path,
partition `rel_path`, or an unvalidated filesystem selector. A catalog UUID may
be accepted as an internal selector only when it is resolved and checked
against the requested logical identity; it is not a replacement for natural
identity in persisted provenance.

Selectors must not silently widen. Conflicting identity constraints fail with
`InvalidRequest` or `CatalogConflict`, rather than choosing a nearby dataset.

## 4. Time and ordering semantics

All logical timestamps are UTC instants with nanosecond-capable precision. The
request interval is `[start, end)`: `start` is included and `end` is excluded.
`start == end` is a valid empty request; `start > end` is invalid.

The primary record time for `trade-v1` is `exchange_ts`. `receive_ts` is
optional observed metadata and is never fabricated from exchange time, file
names, import time or row order. Timestamp comparison uses parsed temporal
values, not timestamp strings.

Results are deterministic. The default ordering is ascending event timestamp,
followed by stable native sequence when present, then native trade identifier
when present, then a documented deterministic tie-break derived from the
source record/partition identity. A venue-specific sequence is compared
numerically, not lexicographically. The implementation must document the
tie-break when a record has neither sequence nor trade ID.

A request may span any number of partitions and may begin or end in the middle
of a partition. Partition boundaries are selection aids, not observable data
boundaries. A gap in catalog coverage is not filled, interpolated or inferred.
The contract distinguishes a valid empty result from `NoCoverage`; policy for
partial coverage must be explicit and must never turn missing data into rows.

## 5. Result and provenance

The logical result is named `DataSlice` and has two separately inspectable
parts: records/data and metadata/provenance. The return representation is not
yet frozen to pandas. The implementation should support typed logical rows or
columnar batches behind an iterator/batch abstraction suitable for large
server-local history, remote transport later, and incremental reads.

Conceptually, metadata contains:

```text
DataSlice.metadata
  dataset_identity             natural logical identity
  catalog_dataset_id            catalog UUID, if resolved from catalog
  record_schema_id              exact record schema identity
  schema_version                registered schema version/hash
  selected_partitions           ordered partition identities
  requested_interval            original [start, end) request
  actual_coverage               observed coverage of returned records
  row_count                     returned count
  ordering                     ordering policy and tie-break version
  source_manifest_hashes        dataset/partition manifest identities
  source_content_hashes         partition content hashes
  request_identity              canonical hash of normalized request
  result_identity               canonical hash of result metadata/input identities
```

Producer and `code_ref` may be returned as source metadata. A code/runtime
identity belongs in the result only when computation or normalization actually
occurred; the gateway must not imply a transformation it did not perform.

The minimum provenance chain is:

```text
natural dataset identity
  -> catalog dataset UUID
  -> record schema identity/version/hash
  -> selected partition identities
  -> manifest hashes and content hashes
  -> normalized request identity
  -> result/snapshot identity
```

Natural identity, schema identity, selected partition IDs, manifest hashes,
content hashes and normalized request identity must persist with any durable
derived artifact or experiment that claims to be based on a gateway result.
The ephemeral `DataSlice` itself need not be stored unless its consumer needs
replayable materialization; a durable snapshot/reference must then retain the
same chain.

### DatasetSnapshot alternatives

Three designs were considered:

- **A — immutable partition-set reference:** a snapshot records the logical
  dataset, normalized request and exact ordered partition/content identities;
  later reads resolve exactly those identities.
- **B — catalog query replay:** a snapshot stores only the request and reruns
  catalog resolution; this is compact but can change when catalog state or
  partition eligibility changes.
- **C — copied/materialized result:** a snapshot owns a new data artifact and
  records its parents; this is strongest for long-lived sharing but duplicates
  data and belongs to materialization/artifact design.

For v1, the gateway contract requires the provenance fields needed to implement
A, but does not freeze a public `DatasetSnapshot` API. B is insufficient as the
sole durable identity; C is outside this access boundary. The snapshot name,
storage model and whether A is exposed directly remain open decisions.

## 6. Physical storage isolation

Upper layers must never depend on `/srv/marketdata`,
`/archive/marketdata-cold`, `/cold/marketdata-deepcold`, Windows paths,
partition `rel_path`, or storage-root mount locations. The gateway resolves
the catalog's storage root plus safe relative paths internally and validates
the canonical path constraints before opening content.

Moving a partition from hot to cold or deep-cold must preserve its logical
dataset identity, partition identity, manifest identity, content identity and
returned logical records. Conflicting duplicate catalog/manifests or differing
content for one claimed identity fail loudly as `CatalogConflict` or
`CorruptContent`; the gateway does not select a convenient copy silently.

Path traversal, absolute caller paths and arbitrary file loading are rejected.
The gateway is a dataset reader, not a generic file browser.

## 7. Partition lifecycle and coverage

The canonical partition states are `writing`, `closed`, `valid`, `degraded`,
`invalid` and `superseded`. The default historical read policy is to use
`valid` and `closed` partitions according to an explicit implementation
policy, with `writing`, `invalid` and `superseded` excluded unless a future
contract deliberately permits them. `degraded` requires an explicit caller
policy and must remain visible in metadata.

The gateway must define and test behavior for:

- a request wholly inside one partition;
- a request ending at a partition boundary;
- a request spanning multiple partitions;
- overlapping coverage and duplicate manifest copies;
- gaps and missing catalog coverage;
- readable versus writing, closed, valid, degraded, invalid and superseded
  states;
- empty partitions and empty time ranges.

It must not hide overlap, duplicate rows or lifecycle conflicts. Deduplication,
if ever required by a venue contract, must be a named semantic rule with
provenance—not an incidental file-reader behavior.

## 8. Schema evolution

Every result exposes `record_schema_id` and the registered schema identity. A
caller may request an explicitly compatible schema only where compatibility is
defined and verified. The gateway must not coerce `trade-v1` records into an
unfrozen `trade-v2` meaning or silently merge incompatible schemas. An
incompatible schema request fails with `SchemaMismatch`.

## 9. Historical and live boundary

Historical reads and live/replay streams share logical identity, time and
provenance concepts, but they need not share one method or transport API. The
recommended direction is a read concept for finite `DataRequest` slices and a
separate stream/replay concept with explicit cursor, ordering and lifecycle
semantics. Live stream catalog/identity semantics remain open. This contract
does not define WebSocket, HTTP, broker or other transport details.

## 10. Errors

The minimum error vocabulary is:

- `DatasetNotFound` — no dataset matches the logical selector;
- `NoCoverage` — the requested interval has no eligible coverage, distinct
  from a valid empty dataset/result;
- `SchemaMismatch` — requested and stored schema identities are incompatible;
- `InvalidPartitionState` — selected state is not readable under the policy;
- `CatalogConflict` — catalog/manifests disagree or identity is ambiguous;
- `CorruptContent` — content/hash/schema validation fails;
- `InvalidRequest` — malformed interval, selector, projection, path-like
  input, or contradictory options.

Errors must retain enough logical identity and provenance context for diagnosis
without exposing an internal absolute path as the caller's identity.

## 11. First implementation slice (not implemented here)

The first implementation slice is intentionally narrow:

1. query the PostgreSQL catalog;
2. resolve one canonical Bybit `trade-v1` dataset;
3. select eligible `valid`/`closed` partitions for `[start, end)`;
4. resolve their physical files internally;
5. read canonical trade records;
6. apply the temporal slice and deterministic ordering;
7. return `DataSlice` data plus the provenance metadata above.

This document does not create that implementation, a Python package, a
DataGateway class, a candle/feature path, or a live interface.

## 12. Semantic acceptance tests for the future implementation

The implementation must have independent tests showing that:

- the same logical request works after hot-to-cold physical relocation;
- callers cannot select data by arbitrary absolute path or traversal path;
- `[start, end)` includes/excludes the correct boundary records;
- one-partition, boundary and multi-partition reads are deterministic;
- lifecycle states and gaps are reported according to policy;
- no fabricated/interpolated rows appear in a gap;
- schema identity is preserved and incompatible schemas fail;
- partition IDs, manifest hashes and content hashes are present in provenance;
- repeated reads with the same inputs produce the same ordering/result identity;
- duplicate or conflicting catalog/manifests fail loudly.
