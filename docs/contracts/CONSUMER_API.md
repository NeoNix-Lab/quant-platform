# Consumer API Boundary Contract v1

**Status:** Contract v1 — semantic boundary frozen; transport/runtime not implemented

**Decision:** [ADR-0020 — Consumer API semantic boundary](../decisions/ADR-0020-consumer-api-boundary.md)

**Scope:** the canonical application boundary for App UI, TUI, CLI, notebooks,
automation and other external consumers.

**Related:** [PRODUCER_CONSUMER_CONFORMITY.md](PRODUCER_CONSUMER_CONFORMITY.md)
§16 clarifies, without changing any rule below, how a future bounded/streaming
DataGateway read path composes with this contract's synchronous-read and
future job semantics, and how `RecordTimeBounds` composes into
`returned_temporal_bounds`.

This contract freezes the meaning and ownership of the consumer boundary. It
does not implement a web framework, endpoint, client, job runtime or
representation engine.

## 1. Roadmap position

The canonical consumer dependency track in [ROADMAP.md](../product/ROADMAP.md)
places the product capabilities in dependency order:

```text
DataGateway
  -> Representation foundation
  -> Feature foundation
  -> Research / Outcome
  -> Validation / Labeling
  -> Strategy / Decision
  -> Execution / Replay
  -> Experiment persistence
  -> Canonical API and job runtime
  -> Clients
```

The Data Plane producer track runs in parallel and meets this track through
the published Data Plane contracts and the DataGateway boundary. It is not a
second consumer boundary and does not change the ordering above.

A narrow catalog-backed DataGateway read slice is implemented; the DataGateway
capability itself remains `PARTIAL` in the capability map, and broader access
remains open. The representation and later domain capabilities remain ahead of
API runtime implementation. This document is therefore a boundary-preparation
milestone: it does not reorder the roadmap or claim that phase 9 runtime work
has started.

The existing representation decisions are intentionally respected:

- `CandleDefinition` owns reproducible candle semantics;
- `PARTIAL` and `CLOSED` candles remain distinct;
- future market-data contract extensions require explicit versioning;
- unsupported representation fields are not invented here.

## 2. Purpose and exclusive application boundary

The canonical API is the only supported application boundary for:

- App UI;
- TUI;
- CLI;
- future notebooks and automation;
- other external consumers.

The dependency direction is:

```text
Consumers
    |
    v
Canonical API (semantic contract)
    |
    v
Application services / orchestration
    |
    +--> Representations / Feature Engine
    +--> Research / Outcome / Validation
    +--> Strategy / Execution / Experiments
    |
    v
Platform capabilities
    |
    v
DataGateway
    |
    v
Canonical data plane
```

The API is a semantic platform boundary, not a transport-shaped alias for
`DataGateway`. An internal application service may use `DataGateway`, but the
public contract must not be `POST /data/read` or an equivalent operation that
requires callers to know gateway request fields, catalog selection, partition
policy or physical storage.

The conceptual capability families are organized so the platform can grow
without introducing a second client boundary:

| Capability family | Consumer-facing role in this contract |
|---|---|
| Market data | Query a semantic representation over an explicit interval |
| Features | Request versioned feature definitions/sets when implemented |
| Research and outcomes | Submit or inspect declarative studies and outcomes |
| Labels and validation | Request reproducible labeling/validation operations |
| Strategies and execution | Submit decision, replay and execution intents when implemented |
| Experiments | Inspect reproducible studies, runs and artifacts when implemented |
| Jobs | Represent long-running operations across the capability families |

Only the boundary principles and the first `trades` reference slice are
contracted here. The other families are extension points, not implemented
capabilities.

## 3. Three distinct concepts

### 3.1 Consumer query / application request

A consumer request expresses intent. The v1 conceptual market-data request
is:

```text
ConsumerMarketDataQuery v1
  selector
    venue: semantic venue identifier
    instrument: venue-native instrument identifier
  interval
    start: UTC instant
    end: UTC instant
    semantics: [start, end)
  representation
    kind: versioned representation kind
    version: representation semantic version
    definition: representation-owned parameters, only when supported
  options
    optional, capability-owned and versioned
```

The v1 selector is deliberately minimal: `venue` and `instrument`. It
identifies what the consumer means, not a physical dataset, file name, path,
catalog UUID or execution instruction. `start == end` is a valid empty
interval; `start > end` is invalid. The interval is UTC and uses half-open
semantics wherever the requested capability returns a finite historical slice.

Future selector dimensions such as market type, venue sets or cross-venue
selection require an explicit versioned selector extension. Their semantics
are not frozen by this contract.

The v1 reference representation is:

```text
representation = { kind: trades, version: 1 }
```

The consumer-facing shorthand `representation = trades` denotes this
versioned reference. It is not a `dataset_kind` or a request for a specific
materialized dataset.

It requests canonical trade records for the selected venue and instrument.
The consumer does not select `trade-v1`, a partition, a schema file or a
storage location. The application service resolves those details against the
available platform capabilities.

The request may contain a projection or other option only after the relevant
capability has defined its semantics. Unsupported options fail explicitly;
they are not silently ignored or passed through to a lower layer.

### 3.2 Representation definition

`RepresentationDefinition` is the semantic description of the form in which
the consumer wants data. It is separate from both the query and the source
dataset.

Examples of the extensible boundary are:

```text
trades@1
candle(<versioned candle definition>)
footprint(<versioned footprint definition>)
book(<versioned book definition>)
```

Only `trades@1` is a reference representation in this contract. Candle,
footprint and book examples show where future definitions fit; they do not
freeze fields, aggregation rules, L1/L2/L3 semantics, materialization policy
or schemas for capabilities that are not yet implemented.

A representation definition has a stable semantic identity composed of its
kind, version and canonical definition parameters once that representation's
own contract is frozen. Its implementation may be computed on demand,
served from a materialized artifact, read from cache or reconstructed from an
approved canonical source. That choice is not part of consumer semantics.

### 3.3 Dataset identity

`DatasetIdentity` identifies available canonical or derived materialized data
inside the platform. The frozen [DataGateway Contract v1](DATA_GATEWAY.md)
defines the natural identity as:

```text
(layer, dataset_kind, venue, instrument, record_schema_id)
  + (feature_set_slug, feature_set_version)  # only for feature datasets
```

It is not the primary consumer query model. The application service may map a
semantic consumer query to one or more dataset identities, or to a
representation computation that uses no directly materialized representation.

`NaturalPartitionIdentity` is likewise an internal source/provenance concept:

```text
DatasetIdentity + (partition_key, revision)
```

The platform, not the consumer, decides whether a representation is backed by
a materialized dataset, computed from canonical records, cached or served by
another approved implementation.

## 4. Reference request and internal flow

The first reference slice is intentionally narrow but does not define the
complete market-data model:

```text
semantic consumer query
  selector: venue=Bybit, instrument=BTCUSDT
  interval: [start, end)
  representation: trades@1
          |
          v
canonical API boundary
          |
          v
market-data application service
  resolves the representation and semantic selector
          |
          v
internal DataRequest
  canonical natural DatasetIdentity
  schema/time/read policy chosen by the service
          |
          v
DataGateway
  catalog -> eligible partitions -> canonical records
          |
          v
canonical Bybit trade-v1 data
```

For the current reference implementation, the internal resolution is
compatible with the canonical natural identity
`(canonical, trades, bybit, BTCUSDT, trade-v1)`. This is an implementation
mapping, not a requirement that clients send that identity. A later
`candle(5m)`, footprint or book capability must be addable without changing
the consumer's fundamental query boundary.

## 5. Results, coverage and provenance

The API result for a finite market-data query must preserve the meaning needed
to interpret and reproduce the result without exposing physical mechanics.
The exact row/columnar wire representation remains open until transport and
return-shape work is scoped.

The stable result envelope is conceptually:

```text
ConsumerMarketDataResult
  request
    normalized semantic query
    request_identity
  representation
    kind, version and canonical definition identity
  data
    logical records/batches in the representation's defined order
  interval
    requested_interval: [start, end)
    returned_temporal_bounds: first/last record time under the requested
      representation's defined temporal semantics, or null when empty
  coverage
    covered_intervals
    gaps
    complete: bool
  provenance
    source semantic identity and schema/version information
    stable source references where needed for reproducibility
    source/DataGateway coverage diagnostics, when useful
    implementation identity when computation or normalization occurred
  statistics
    row_count where meaningful
```

`returned_temporal_bounds` reports the first and last returned record time
under the requested representation's own defined temporal semantics. Every
representation contract must define the temporal coordinate needed to
interpret those bounds. This contract does not define candle, footprint, book
or L1/L2/L3 timestamp semantics; each representation owns them. For a
successful empty result the bounds are null.

Coverage at the consumer boundary is representation/application-level
coverage. `covered_intervals` describes where the requested representation is
available under that representation's semantics; `gaps` describes the
requested portions that are not covered; `complete` is a boolean that is true
if and only if `gaps` is empty over the requested interval. A derived
representation may have different coverage from its source because of its own
definition, warmup, late-event or partial-result rules. Coverage is not a
universal exposure of DataGateway `eligible_coverage`.

The application service may retain source/DataGateway coverage diagnostics in
provenance. Those diagnostics are explanatory source evidence, not the stable
consumer coverage abstraction. For the current `trades@1` reference, the
service preserves the frozen DataGateway v1 strict behavior when determining
whether a semantic result can be produced:

```text
full representation coverage + zero records -> success
                                               complete = true
                                               returned_temporal_bounds = null
no representation coverage                  -> no_coverage
representation gap under strict policy      -> no_coverage
```

The API may translate source outcomes into stable API error codes, but it must
preserve the distinction between a complete empty result and unavailable or
incomplete representation coverage. It must also preserve deterministic
ordering and the representation's temporal availability semantics. It must not
fabricate, interpolate or silently truncate uncovered data.

Provenance exposed at this boundary is semantic and diagnostic: source venue,
instrument, representation/schema versions, coverage and stable source
references when required for reproducibility. Consumers must not need to
construct or persist catalog locators or physical paths to use the result.

## 6. Identity visibility matrix

| Identity or locator | Consumer query | Stable API result/provenance | Internal/runtime policy |
|---|---|---|---|
| `request_identity` | Not supplied as a locator; generated from normalized request | Visible and stable | Must exclude runtime/catalog/path data |
| `representation_identity` | Supplied semantically as kind/version/definition | Visible when the representation is defined | Changes when representation semantics change |
| `result_identity` | Not required to request data | May be returned when deterministic result identity is defined | Derived from stable request/source/result semantics |
| Natural `DatasetIdentity` | Not primary; never required for ordinary consumer requests | May appear as provenance/diagnostic source reference | Owned by DataGateway/data plane; not a client selection API |
| `NaturalPartitionIdentity` | Never a consumer selection instruction | May appear in reproducibility provenance where needed | Internal source identity; not a physical locator |
| Catalog dataset/partition UUID | Never | Not part of stable API identity; diagnostic exposure is not frozen here | Runtime/catalog locator only |
| `storage_root_id`, `rel_path`, absolute path | Never | Never part of stable API semantics | DataGateway-only physical resolution |

Catalog UUIDs, storage roots, relative paths and resolved filesystem paths must
not affect request identity, result identity or semantic compatibility.

## 7. Error boundary and translation policy

The API exposes stable, structured errors rather than raw Python exceptions,
SQL errors, filesystem messages or paths. Each error includes a safe logical
context and, where applicable, the request identity; it does not expose an
absolute path or internal stack trace as caller semantics.

The minimum market-data error vocabulary is:

| API error code | Meaning and required distinction |
|---|---|
| `invalid_request` | Malformed interval, selector, unsupported option shape or contradictory request |
| `unsupported_representation` | The requested representation kind/version is not implemented or available |
| `source_not_found` | No approved source/dataset can satisfy the semantic request |
| `no_coverage` | The requested representation has no covered interval or has an incomplete gap under its policy |
| `schema_incompatible` | Available source/representation schema cannot satisfy the requested semantics |
| `integrity_failure` | The platform cannot safely produce a trustworthy result |

`full representation coverage + zero records` is a successful empty result,
not `no_coverage`. The API may add capability-specific errors later, but it
may not collapse these distinctions into a generic not-found or empty response.

Internal causes such as an unreadable partition state, catalog/manifests
conflict or corrupt content remain diagnosable through safe internal details,
tracing and operational logs. They are not frozen as consumer API error codes;
the application service maps them to `source_not_found` or
`integrity_failure` according to whether the consumer can act on source
absence or only on platform failure.

## 8. Synchronous reads and asynchronous jobs

The API contract distinguishes operation class from transport:

- A cheap, bounded, deterministic read may complete synchronously and return
  its result directly.
- A long-running computation, materialization, research run, backtest,
  training operation or other operation whose duration/resource use is not
  bounded for an interactive request uses asynchronous Job semantics.

The future job contract must provide a stable job identity, lifecycle state,
result/error reference, and cancellation/retry semantics where the operation
supports them. Progress is reported only when it has defined meaning. A
client must not emulate a job by holding an unbounded synchronous request
open.

This document does not implement job storage, scheduling, workers,
cancellation or progress reporting. It also does not require every future
market-data representation to be asynchronous; the application service owns
the boundedness decision under the capability's contract.

### 8.1 Non-loopback J02 security

The current loopback J02 listener remains a local-only deployment. Any future
non-loopback J02 listener is governed by
[ADR-0063](../decisions/ADR-0063-remote-j02-security-v1.md): it must use TLS
1.3 mutual TLS, validate the server endpoint and client certificate, and map the
exact client credential fingerprint to an explicit principal and scope before
decoding a Consumer API request. The only scope defined now is
`j02.market_data.read`; it grants no canonical storage, catalog, checkpoint,
backup, publication, Job, or administrative authority.

Authentication and authorization failure occur outside the Consumer API result
envelope and must not reinterpret a `ConsumerErrorCode`. A remote listener may
not fall back to plaintext or an unauthenticated WebSocket session. This is a
future implementation contract; it does not change current loopback behavior or
add a remote endpoint.

## 9. Versioning and evolution

The following version domains remain distinct:

- **API contract version:** versioned semantic boundary and error/result rules;
- **representation version:** versioned meaning and parameters of a requested
  representation;
- **dataset/schema version:** canonical source and record meaning owned by the
  data plane/DataGateway;
- **runtime identity:** catalog UUIDs, paths and process metadata, never a
  semantic compatibility key.

Semantic change to a frozen API contract requires an explicit new API version.
Semantic change to a frozen representation requires an explicit new
representation version. An ADR may authorize or document the migration,
compatibility plan and version introduction, but an ADR alone cannot mutate
frozen semantics in place. A source materialization can change from computed
to cached or persisted without a consumer contract change if the
representation semantics, provenance and result guarantees are unchanged.

No legacy API compatibility is promised. Existing `ml_core` behavior is
read-only evidence and does not constrain this boundary. Compatibility with
the frozen `trade-v1`, dataset-manifest-v1 and partition-manifest-v1
contracts cannot be obtained by silently changing those contracts; a future
incompatible data meaning requires explicit contract evolution.

Transport versioning, authentication, pagination, streaming/cursors,
snapshot publication, exact serialization and schema-v2 compatibility remain
deferred decisions for their relevant implementation scopes.

## 10. Client invariants

App UI, TUI and CLI are equivalent clients of the same canonical API. Clients
may own presentation, interaction state, local preferences and rendering.

Clients must not own:

- market-data reconstruction or aggregation;
- catalog or physical dataset discovery;
- feature calculations;
- label or outcome semantics;
- strategy logic;
- execution simulation;
- retry logic that changes domain meaning.

The API/application-service layer owns request normalization, capability
resolution, temporal semantics, provenance, deterministic ordering and domain
logic. A client may compose a semantic request and render the result, but it
must not bypass the API to invoke DataGateway, PostgreSQL, Parquet, a
Representation Engine or another platform internal.

## 11. Contract invariants and explicit non-goals

This contract is accepted only if all of the following remain true:

- consumers never access DataGateway directly;
- consumers never resolve PostgreSQL, Parquet, storage roots or paths;
- the API is not defined as a DataGateway HTTP wrapper;
- `trade-v1` is a reference implementation slice, not the general market-data model;
- consumer market-data requests are semantic;
- RepresentationDefinition and DatasetIdentity are separate concepts;
- materialization strategy may change without changing consumer semantics;
- future candle, footprint, book and other representations fit the same boundary;
- runtime UUID/path identities do not leak into stable consumer semantics;
- coverage, determinism and provenance remain meaningful through the API;
- long-running operations have a future asynchronous Job path;
- App, TUI and CLI share one canonical platform API;
- quantitative business logic is not moved into clients.

Explicit non-goals for this milestone are FastAPI, React, TUI, CLI commands,
candle/footprint/book builders, L1/L2/L3 implementation, Feature Engine,
Research Engine, Job Runtime, authentication, deployment, schema migration,
DataGateway generalization and legacy CoreApp/API porting.

## 12. Domain capability seam extensions

ADR-0065 defines the accepted application seam discipline for the future
Strategy, Replay, Validation, and Training Consumer-API capabilities. Requests
carry only existing immutable semantic payloads and content identities; paths,
catalog UUIDs, repository handles, callable import names, and client-selected
locators are never consumer semantics. Application owns normalization,
governed resolution, invocation, and stable error translation; transport and
clients do not reinterpret domain behavior.

The accepted J10 Strategy seam is `compose_decision`; J12 consists only of its
existing finite fold/classification/DSR/PBO operations; and J13 is the existing
supervised evaluation/owned-registration boundary. J11 Replay remains deferred:
`HistoricalReplayRuntime` requires a `feature_provider` callable that has no
accepted semantic, serializable reference. A later ADR must supply that
authority before any Replay consumer request can exist. Long-running work is
admitted through J03, never emulated by a client-held synchronous request.
