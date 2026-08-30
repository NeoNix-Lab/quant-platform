# DataGateway Contract v1

**Status:** Contract v1 — narrow catalog-backed implementation present

**Related:** [Producer–Consumer Conformity Contract](PRODUCER_CONSUMER_CONFORMITY.md)
freezes, additively and without changing any rule below: the bounded-read
property the current `read()` implementation does not yet satisfy (§5), a
dedicated `RecordTimeBounds` type distinct from `CoverageInterval` for
`returned_record_bounds` (§6), the producer ordering obligations this
contract's determinism currently depends on (§7), the compatibility mapping
between this contract's `ordering_policy` identity and CandleDefinition's
source-ordering identity (§8), the Bybit first-vertical eligibility profile
that this contract's `trade_id`-dependent ordering requires (§10), and an
additive `CanonicalContentHashV1` field alongside the unchanged
`result_identity` (§11–§12).

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

- resolve a logical dataset identity through the PostgreSQL catalog, using
  manifests as durable consistency evidence;
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

## 3. Logical request and stable identity

The canonical v1 `DatasetIdentity` is the natural identity already defined by
the schemas and `tools/semantic_validator.py`:

```text
(layer, dataset_kind, venue, instrument, record_schema_id)
  + (feature_set_slug, feature_set_version)  # only when layer == features
```

It is a logical tuple, not a path and not a PostgreSQL UUID. The feature-set
pair is part of the identity only for a feature dataset; raw and canonical
datasets do not gain speculative fields. `rel_root` is a deterministic,
path-safe rendering of this identity, not a second identity.

The minimal logical request is named `DataRequest`. Its conceptual fields are:

```text
DataRequest
  dataset_selector: canonical natural DatasetIdentity
  schema_requirement: exact record_schema_id, or an explicitly compatible policy
  interval: start and end UTC instants
  partition_policy: named state and partial-coverage policy
  ordering_policy: ordering policy and version
  projection: optional future field projection
```

The exact programming-language representation remains an implementation
choice. A request must not contain an absolute path, storage-root mount path,
partition `rel_path`, or an unvalidated filesystem selector. Venue, instrument,
data kind and schema are expressed through the canonical natural selector, not
as a second speculative identity model.

`datasets.dataset_id` and `partitions.partition_id` are PostgreSQL-generated
UUIDs. They are runtime/catalog locators and are not stable across a catalog
rebuild or reinsert. A catalog UUID may be accepted as a lookup convenience or
returned for diagnostics only after it is resolved and checked against the
natural identity. It must not affect stable request or result identity.

The stable natural partition identity is:

```text
NaturalPartitionIdentity = DatasetIdentity + (partition_key, revision)
```

This is distinct from the catalog `partition_id` UUID. Physical relocation of
the same partition preserves the natural partition identity.

Selectors must not silently widen. Conflicting identity constraints fail with
`InvalidRequest` or `CatalogConflict`, rather than choosing a nearby dataset.

### Stable request identity

`request_identity` is a canonical hash/fingerprint of the normalized logical
request. Its inputs are exactly the applicable:

- canonical natural dataset selector;
- exact schema requirement or compatible-schema policy;
- normalized UTC `[start, end)` interval;
- named partition/read-state policy;
- strict or future partial-coverage policy;
- ordering-policy version;
- projection, only once projection is supported by this contract version.

It excludes `catalog_dataset_id`, catalog partition UUIDs, `storage_root_id`,
`rel_path`, absolute filesystem paths and volatile runtime metadata. A natural
selector and a catalog-UUID-plus-matching-constraints selector normalize to the
same natural selector and therefore the same `request_identity`; a UUID-only
selector must first resolve to the natural identity and cannot be the durable
identity by itself.

## 4. Time and ordering semantics

All logical timestamps are UTC instants with nanosecond-capable precision. The
request interval is `[start, end)`: `start` is included and `end` is excluded.
`start == end` is a valid empty request; `start > end` is invalid.

The primary record time for `trade-v1` is `exchange_ts`. `receive_ts` is
optional observed metadata and is never fabricated from exchange time, file
names, import time or row order. Timestamp comparison uses parsed temporal
values, not timestamp strings.

Results are deterministic. For the first canonical Bybit `trade-v1` slice the
ordering is exactly `(exchange_ts, trade_id)`. The importer supplies
`trade_id`, and this source has no usable sequence. This is a deterministic
artifact/replay ordering rule; it does not claim to reconstruct real exchange
execution order when the source has no native sequence.

For a future dataset kind, usable native sequence/trade identity must fully
order equal-event-time records, or that kind must define a stable canonical
tie-break primitive before DataGateway supports it. The primitive must survive
physical relocation, catalog rebuild and rereading identical immutable content;
it must not depend on storage root, relative path, catalog UUID or unspecified
reader iteration order. Universal ordinal file-row position is not frozen here.

A request may span any number of partitions and may begin or end in the middle
of a partition. Partition boundaries are selection aids, not observable data
boundaries. A venue-specific sequence is compared numerically, not
lexicographically.

## 5. Result, coverage and provenance

The logical result is named `DataSlice` and has two separately inspectable
parts: records/data and metadata/provenance. The return representation is not
yet frozen to pandas. The implementation should support typed logical rows or
columnar batches behind an iterator/batch abstraction suitable for large
server-local history, remote transport later, and incremental reads.

Conceptually, metadata is grouped as follows:

```text
DataSlice.metadata
  LOGICAL / STABLE
    dataset_identity            canonical natural DatasetIdentity
    record_schema_id             exact record schema identity
    schema_version/hash          registered schema version and body hash
    natural_partitions           ordered NaturalPartitionIdentity values
    manifest_hashes              dataset/partition manifest identities
    content_hashes               immutable partition content identities
    request_identity             normalized logical request fingerprint
    result_identity              stable result fingerprint

  RUNTIME / DIAGNOSTIC LOCATORS
    catalog_dataset_id           catalog UUID, never a stable identity input
    catalog_partition_ids        catalog UUIDs, never stable identity inputs
    storage_root_id              current physical root
    rel_paths / resolved_paths   current physical locations

  COVERAGE
    requested_interval            original [start, end)
    eligible_coverage             union of eligible declared coverage intersected with request
    coverage_gaps                 requested portions without eligible coverage
    coverage_complete             true iff coverage_gaps is empty
    returned_record_bounds         first/last returned event timestamps, or null when empty

  RESULT STATISTICS
    row_count                     returned record count
    ordering_policy/version       applied deterministic ordering
```

`eligible_coverage` is the union of state-eligible canonical partition coverage
intersected with the request. `coverage_gaps` is the portion of the requested
interval not covered by that union. Coverage comes from authoritative catalog /
manifest coverage metadata, not from first/last returned event timestamps.
For gap and overlap analysis, declared coverage intervals use the same
half-open convention as requests: adjacency where `A.end == B.start` is
covered without overlap.
Thus a quiet interval inside a declared covered partition can be a successful
zero-record result, while missing declared coverage is a gap. A zero-row
partition has no event bounds and does not by itself fabricate coverage.

Producer and `code_ref` may be returned as source metadata. A code/runtime
identity belongs in the result only when computation or normalization actually
occurred; the gateway must not imply a transformation it did not perform.

### Stable / durable provenance

```text
natural dataset identity
  -> record schema identity/version/hash
  -> natural partition identities (partition_key + revision)
  -> manifest hashes and content hashes
  -> normalized request identity
  -> result/snapshot identity
```

Natural identity, schema identity, natural partition identities, manifest
hashes, content hashes and normalized request identity must persist with any
durable derived artifact or experiment that claims to be based on a gateway
result. Catalog UUIDs and physical locations are useful operational metadata
but are not semantic identity inputs.

`result_identity` is a canonical hash of these stable inputs:

1. `request_identity`;
2. the ordered natural partition identities;
3. manifest and content identities;
4. exact schema identity/version/hash;
5. ordering-policy version;
6. returned-result semantics: `eligible_coverage`, `coverage_gaps`,
   `coverage_complete`, `returned_record_bounds` and `row_count`.

The coverage fields and `row_count` participate explicitly so a full-coverage
empty result is distinguished from an unavailable result and result semantics
cannot be silently changed. Volatile runtime metadata, catalog UUIDs,
storage roots, relative paths and resolved absolute paths are excluded. A
catalog rebuild with identical canonical manifests/content, or hot-to-cold /
deep-cold relocation, therefore cannot change stable `request_identity` or
`result_identity`.

### Runtime / diagnostic locators

Catalog dataset/partition UUIDs, current storage root and current relative or
resolved paths may be returned for diagnostics, tracing and operational repair.
They are explicitly not part of stable provenance or identity hashes.

The ephemeral `DataSlice` itself need not be stored unless its consumer needs
replayable materialization; a durable snapshot/reference must then retain the
same chain.

### DatasetSnapshot alternatives

Three designs were considered:

- **A — immutable partition-set reference:** a snapshot records the logical
  dataset, normalized request and exact ordered partition/content identities;
  later reads resolve exactly those identities.
- **B — independently materialized snapshot artifact:** a snapshot owns a new
  data artifact and
  records its parents; this is strongest for long-lived sharing but duplicates
  data and belongs to materialization/artifact design.

For v1, the gateway contract requires the provenance fields needed to implement
A, but does not freeze a public `DatasetSnapshot` API. Catalog-query replay
alone is insufficient for durable reproducibility. Whether a snapshot becomes
public, and whether it uses A or B, remains an open decision.

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

## 7. Catalog authority, partition lifecycle and coverage

PostgreSQL is the normal runtime resolver/index for DataGateway reads. A
`DataRequest` does not choose among catalog querying, manifest scanning and
directory scanning based on convenience. Manifest files remain durable
identity, provenance, rebuild and consistency evidence; the catalog remains
rebuildable from those canonical manifests, but manifests are not rescanned as
an alternate discovery path for every ordinary read.

The canonical partition states are `writing`, `closed`, `valid`, `degraded`,
`invalid` and `superseded`. The default historical read policy is to use
`valid` partitions only. `closed` is sealed but not validated and requires an
explicit named opt-in. `degraded` is usable with reservations and also
requires an explicit named opt-in. `writing` is still being written and is
unreadable; `invalid` is unusable; `superseded` is replaced and remains
excluded. Any relaxed policy must be visible in request semantics and returned
metadata and must affect `request_identity`.

### Strict v1 coverage policy

DataGateway v1 is **STRICT**: the full requested interval must have eligible
coverage. Under this policy `NoCoverage` includes zero eligible coverage and
any incomplete requested coverage or internal gap. The available subset is not
silently returned. Future `ALLOW_PARTIAL` semantics require a later contract
revision.

The distinctions are:

```text
FULL COVERAGE + ZERO RECORDS  -> success; row_count=0,
                                 coverage_complete=true,
                                 returned_record_bounds=null
ZERO COVERAGE                 -> NoCoverage
PARTIAL / INTERNAL GAP        -> NoCoverage under v1 STRICT
```

Structured `NoCoverage` context should identify uncovered ranges where the
implementation can do so. `partial-coverage policy` is part of the request
identity.

The gateway must define and test behavior for:

- a request wholly inside one partition;
- a request ending at a partition boundary;
- a request spanning multiple partitions;
- same natural partition content physically relocated;
- superseded/replacement lineage;
- boundary adjacency under half-open semantics;
- overlapping coverage from differently keyed eligible partitions;
- gaps and missing catalog coverage;
- readable versus writing, closed, valid, degraded, invalid and superseded
  states;
- empty partitions and empty time ranges.

For v1, unexplained temporal overlap between two eligible, non-superseded
partitions with different natural partition identities is `CatalogConflict`.
The gateway must not concatenate silently, deduplicate incidentally, choose by
filesystem order or catalog UUID, or choose the newest partition absent an
explicit future revision/lineage rule. If the same natural partition content
is physically relocated, it is one partition, not an overlap. Half-open
adjacency where `A.end == B.start` is not overlap. Deduplication, if ever
required by a venue contract, must be a named semantic rule with provenance.

## 8. Content trust and schema evolution

For a `valid` canonical partition, normal reads may trust the catalog-recorded
manifest/content identities established by upstream sealing and validation.
Ordinary reads do not require a full multi-gigabyte content rehash. A future
strict/verify mode may independently verify the content hash and raise
`CorruptContent` on mismatch; that mode is not implemented here. The contract
does not claim a cheap file-size check beyond the evidence actually recorded by
the canonical catalog/manifest.

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

## 10. Projection and errors

Projection is a documented future `DataRequest` capability and is not part of
the first runtime slice. When implemented, its contract must specify that
ordering/time keys remain internally available, how projection affects
`request_identity`, and how output/result identity changes. It must not be
silently added to the first implementation.

The minimum error vocabulary is:

- `DatasetNotFound` — no dataset matches the logical selector;
- `NoCoverage` — under v1 STRICT, zero eligible coverage or incomplete
  requested coverage/internal gap; distinct from a full-coverage empty result;
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
3. select eligible `valid` partitions for `[start, end)`;
4. resolve their physical files internally;
5. read canonical trade records;
6. apply the temporal slice and deterministic ordering;
7. return `DataSlice` data plus the provenance metadata above.

This document does not create that implementation, a Python package, a
DataGateway class, a candle/feature path, or a live interface.

## 12. Semantic acceptance tests for the future implementation

The implementation must have independent tests showing that:

- the same logical request works after hot-to-cold physical relocation;
- catalog rebuild/reinsert with identical canonical identities does not alter
  request or result identity despite new catalog UUIDs;
- two equivalent selector syntaxes normalize to the same request identity;
- callers cannot select data by arbitrary absolute path or traversal path;
- `[start, end)` includes/excludes the correct boundary records;
- one-partition, boundary and multi-partition reads are deterministic;
- full valid coverage with zero records succeeds with complete coverage and
  null returned bounds;
- internal coverage gaps fail under STRICT policy;
- default reads exclude `closed` and `degraded` partitions;
- explicit relaxed policy can include `closed`/`degraded` only when deliberately
  requested and recorded in metadata and request identity;
- two eligible differently keyed overlapping partitions raise
  `CatalogConflict`;
- no fabricated/interpolated rows appear in a gap;
- schema identity is preserved and incompatible schemas fail;
- natural partition identities, manifest hashes and content hashes are present
  in stable provenance;
- repeated reads with the same inputs produce the same ordering/result identity;
- catalog UUIDs and runtime physical paths do not affect semantic identities;
- Bybit equal-`exchange_ts` ordering is deterministic by `trade_id`;
- ordinary valid reads do not require rehashing immutable content, while a
  separately implemented strict verification mode detects mismatches;
- duplicate or conflicting catalog/manifests fail loudly.
