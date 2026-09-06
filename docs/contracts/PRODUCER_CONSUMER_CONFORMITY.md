# Producer–Consumer Conformity Contract v1

**Status:** Contract v1 accepted — Contract Freeze Gate PASSED; normative
semantics are frozen. Conformity Implementation Gate: IN PROGRESS / PARTIAL.
Runtime progress is tracked by `SCOPE.md`, `ROADMAP.md` and `CAPABILITY_MAP.md`;
this contract remains the semantic authority, not the implementation ledger.

**Decision:** [ADR-0023 — Producer–Consumer Conformity Gate v1](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)

**Scope:** the seam where the Data Plane producer track's output becomes the
DataGateway consumer track's input, for the first canonical Bybit `trade-v1`
vertical.

This contract composes existing frozen contracts — `trade-v1`,
`dataset-manifest-v1`, `partition-manifest-v1`, `coverage-manifest-v1`, the
DataGateway Contract v1, the Consumer API Boundary Contract v1 and
CandleDefinition v1 — rather than replacing any of them. Where it introduces
new semantics not covered by an existing contract, it says so explicitly and
marks whether the introduction is additive or requires a version change. It
does not implement a Parquet writer, a DataGateway streaming runtime, a
manifest/catalog publication bridge, a catalog migration, a producer
certifier, or a golden ingest run.

## 1. Purpose

[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)
freezes nine audit findings (B1–B9) as gate-blocking. This contract supplies
the normative content that resolves each of them, precisely enough that a
later implementation mandate does not need to invent semantics. Section
numbers below match the findings and the exit criteria in ADR-0023.

## 2. Two-stage gate model

Two distinct gates exist. Confusing them is the structural defect a prior
draft of this contract had; this section exists so that never recurs.

```text
CONTRACT FREEZE GATE                       CONFORMITY IMPLEMENTATION GATE
purpose: authorize implementation          purpose: prove the seam in
                                            executable reality

exit: this ADR + this contract are         exit: the conformity slices
independently reviewed and accepted;       (writer, manifest/coverage
no unresolved shared semantic blocker      emission, certifier, bridge,
(documentation-level only)                 bounded DataGateway read) are
                                            implemented and pass the required
                                            tests, including the golden
                                            vertical end-to-end proof
                                            (runtime-level)

unlocks: implementing the conformity       unlocks: producer vertical
slices against this frozen contract        expansion AND consumer vertical
                                            expansion AND Candle runtime,
                                            per ROADMAP.md
```

**Invariant GM1.** No statement in this document or in ADR-0023 may say both
"no conformity runtime before gate PASS" and "gate PASS requires conformity
runtime" about the same gate. Every occurrence of an undifferentiated "the
gate" in this document refers to one of these two named gates, stated
explicitly.

**Invariant GM2.** Passing the Contract Freeze Gate does **not** by itself
reopen producer or consumer vertical expansion, and does **not** certify that
any future runtime implementation will be correct. It only authorizes
starting the implementation work described in §23.

**Invariant GM3.** Passing the Conformity Implementation Gate requires the
Contract Freeze Gate to already have passed (implementation cannot start
before it is authorized), but the reverse is never true: the Contract Freeze
Gate's own exit criteria (ADR-0023 §6) never depend on runtime existing. This
is what breaks the circularity — each gate's exit criteria are a strict
precondition of the *other* gate's unlock, never of its own.

## 3. The conformity boundary

```text
Source / archive
        |
        v
canonical records                          (trade-v1, frozen)
        |
        v
canonical physical materialization         (S9 — new: physical Parquet contract)
        |
        v
Bybit first-vertical eligibility profile   (S10 — new: trade_id non-null, unique ordering key)
        |
        v
DatasetManifest / PartitionManifest        (frozen)
CoverageManifest                           (frozen, ADR-0022)
        |
        v
certification                              (S13 — new: state=valid evidence rule)
        |
        v
catalog publication                        (S14 — new: manifest+coverage -> catalog mapping)
        |
        v
eligible DataGateway source                (frozen selection: writing/closed/valid/degraded/invalid/superseded)
        |
        v
bounded finite historical read             (S5-S8 — new: bounded-read property, RecordTimeBounds, ordering)
        |
        v
Representations / application services     (frozen: CandleDefinition v1, Consumer API v1)
```

Every box above already has an owner in an accepted ADR or contract except
the boxes marked "new". This contract freezes those and states how they
compose with what already exists. It does not introduce a `PublishedPartition`
manifest or any other new durable artifact; `DatasetManifest`, `PartitionManifest`
and `CoverageManifest` remain the complete durable record set. Certification
evidence's persistence target is answered in §13.3 using an existing table.

## 4. Non-duplication

The following remain sole authorities and are not restated in full here:

- [DATA_GATEWAY.md](DATA_GATEWAY.md) for request/result identity, coverage
  policy, lifecycle states and the error vocabulary;
- [CANDLE_DEFINITION.md](CANDLE_DEFINITION.md) for candle semantics;
- [DECLARED_COVERAGE.md](DECLARED_COVERAGE.md) for coverage manifests and the
  catalog reconstruction fold;
- [CONSUMER_API.md](CONSUMER_API.md) for the consumer-facing envelope;
- [MARKET_DATA_INGEST_CONTRACTS.md](MARKET_DATA_INGEST_CONTRACTS.md) for
  producer obligations not specific to this gate.

This contract only adds what those documents leave open at the seam between
them, and only for the Bybit `trade-v1` vertical.

## 5. Bounded historical read contract (resolves B4)

### 5.1 The property, not the transport

**Invariant BR1.** A finite historical `DataGateway` read of `N` requested
rows MUST be consumable by a caller using memory bounded independently of
`N`. This binds the *consumption pattern* the contract exposes, not a
specific transport. HTTP streaming, gRPC, Arrow Flight, WebSocket and network
pagination are all compatible implementations; none is frozen here, and none
is required for the first slice to satisfy BR1 (a purely in-process
generator/iterator satisfies it without any network transport at all).

**Current-state note.** `DataGateway.read()` in `src/quant_platform/access/gateway.py`
does not satisfy BR1 today: it extends one Python `list` across every
partition and calls one global `records.sort(...)` before returning. This is
existing behavior this contract records as non-conforming, not behavior it
freezes as correct. Building this is Conformity Implementation Gate work
(ADR-0023 §7), not authorized here.

### 5.2 Finite read lifecycle

```text
OPEN
  stable request/source/coverage metadata is available before any row is read
        |
        v
READING
  ordered logical batches are emitted incrementally; each batch is fully
  ordered and each batch's records precede every later batch's records under
  the ordering contract (§7)
        |
        v
COMPLETED                              or  ABORTED
  final row_count                          no completed result identity is
  final RecordTimeBounds (§6)              produced
  final result_identity (DATA_GATEWAY.md
    §5, unchanged)
  completion evidence
```

**Invariant BR2 — no false completion.** A caller that stops consuming before
`COMPLETED` MUST NOT be able to construct or be handed a value that represents
itself as a complete result. Partial consumption is either explicitly
`ABORTED` (caller-visible, no result identity) or still `READING` (caller has
not asked for completion). There is no third state where partial batches are
silently presented as final.

**Invariant BR3 — metadata availability.** `OPEN` metadata (dataset identity,
schema identity, natural partition set, `eligible_coverage`, `coverage_gaps`,
`coverage_complete`, `request_identity`) is exactly the subset of
`DataSliceMetadata` (DATA_GATEWAY.md §5) that does not depend on
`returned_record_bounds` or `row_count`. Those two remain unavailable until
`COMPLETED`, because they are defined over the full returned result.

**Invariant BR4 — abort semantics.** `ABORTED` carries whatever partial
diagnostic information the implementation chooses (rows read so far, last
batch's bounds) but that information MUST NOT be labeled `returned_record_bounds`
or `result_identity`, and MUST NOT be accepted as durable provenance by any
consumer. An aborted read has no reproducible result.

### 5.3 Read vs scan

`DataGateway.read()` remains a legitimate bounded convenience for small
slices where full-result buffering is an acceptable implementation choice — it
is not deprecated by this contract. A separate `scan()` (name not frozen; see
§5.1) becomes the canonical interface for the general case, returning an
iterator/generator of ordered batches under the lifecycle in §5.2 rather than
one materialized `DataSlice`. `read()` MAY be implemented in terms of `scan()`
by fully draining it; `scan()` MUST NOT be implemented by first calling a
buffering `read()` and re-chunking the result, since that would only move
where the O(N) buffer lives, not remove it.

This document does not freeze the Python method name, return type or iterator
protocol. It freezes that two distinct capability shapes exist — bounded
convenience and general bounded-memory access — and that the second exists
by the time BR1 is claimed satisfied.

## 6. RecordTimeBounds contract (resolves B6, part 1)

### 6.1 The distinction

```text
CoverageInterval                         RecordTimeBounds
declared/eligible responsibility         observed extent of the rows actually
over an interval, producer- or           returned by one read, consumer-facing
catalog-asserted                         only

exists even when the interval            exists only when at least one row
contains zero events (§ DATA_GATEWAY.md  was returned; null for an empty
"FULL COVERAGE + ZERO RECORDS")          result

a claim about responsibility             an observation about what was handed
                                          back
```

**Invariant RT1.** `RecordTimeBounds` is a distinct conceptual type from
`CoverageInterval`. It MUST NOT reuse `CoverageInterval` (or any coverage type)
as its representation, because doing so implies a responsibility claim
(`Coverage*`) about data that is in fact only an observation about what one
particular read happened to return.

```text
RecordTimeBounds
  first: Instant
  last: Instant
```

**Invariant RT2.** `first <= last`. A single-record result is
`first == last` and is a valid, non-degenerate `RecordTimeBounds`, not treated
as a special or invalid case.

**Invariant RT3.** An empty completed result (`row_count == 0` with
`coverage_complete == true`, i.e. the "full coverage, zero records" case
already frozen by DATA_GATEWAY.md §7) has `RecordTimeBounds = None`. This is
identical in meaning to the existing `returned_record_bounds = null` case; RT3
only renames the type, it does not change when the value is null.

**Invariant RT4.** `RecordTimeBounds` NEVER implies coverage, support,
bucket responsibility or availability. A consumer that has `RecordTimeBounds`
for a result learns only what the earliest and latest *returned* records were
timestamped; it learns nothing about whether the surrounding interval was
observed, complete, or gap-free. That information lives exclusively in
`coverage_complete` / `coverage_gaps` (DataGateway) or `complete` / `gaps`
(Consumer API).

### 6.2 Current-state note

`gateway.py._metadata()` constructs `returned_bounds` as a `CoverageInterval`
today (`CoverageInterval(records[0].exchange_ts, records[-1].exchange_ts)`).
This is exactly the violation RT1 forbids. Renaming the field's *type* to a
dedicated `RecordTimeBounds` (same two fields, `first`/`last` instead of
`start`/`end`, no `CoverageInterval.__post_init__` reuse implying half-open
responsibility semantics) is additive to `DataSliceMetadata` — it does not
change `result_identity` (DATA_GATEWAY.md §5 already lists
`returned_record_bounds` as a semantic input to `result_identity` by value,
not by type) and does not require a DataGateway contract version increase.
The frozen name `returned_record_bounds` (DATA_GATEWAY.md) and
`returned_temporal_bounds` (CONSUMER_API.md) are unaffected; only the internal
representation type changes from `CoverageInterval` to `RecordTimeBounds`.

### 6.3 Compatibility with `returned_temporal_bounds`

CONSUMER_API.md §5 already defines `returned_temporal_bounds` as "the first
and last returned record time under the requested representation's own
defined temporal semantics ... null when empty," with the explicit warning
that "this contract does not define candle, footprint, book or L1/L2/L3
timestamp semantics; each representation owns them." `RecordTimeBounds` is
the DataGateway-level primitive that `returned_temporal_bounds` is built from
for the `trades@1` reference representation; a future representation (candle,
footprint) computes its own `returned_temporal_bounds` from its own record
time coordinate (for candles, `bucket_start` — CANDLE_DEFINITION.md §15) and
is not required to reuse `RecordTimeBounds` verbatim, only to satisfy the same
RT1–RT4 invariants at its own layer.

### 6.4 CandleDefinition must never treat these as source coverage

This restates, and does not weaken, CANDLE_DEFINITION.md §8: "Observed
`first_exchange_ts`/`last_exchange_ts` bounds are not declared support and
cannot be substituted for it." `RecordTimeBounds` (or a source's
`returned_record_bounds`) is exactly as inadmissible as those legacy observed
bounds for establishing bucket support or closure evidence. A candle
implementation that reads `RecordTimeBounds` to decide whether a bucket may
close is a contract violation, not an optimization.

## 7. Ordering contract for bounded reads (resolves B5)

### 7.1 The frozen total order

DATA_GATEWAY.md §4 already freezes the Bybit `trade-v1` deterministic order as
exactly `(exchange_ts, trade_id)` using parsed timestamp order and opaque
lexicographic `trade_id` comparison (CANDLE_DEFINITION.md §3.3 states the
comparator precisely). This contract does not change that order. It freezes
the producer- and read-path obligations needed to deliver rows in that order
under BR1 without a consumer-visible O(N) sort.

### 7.2 Producer obligations

**Invariant OR1 — intra-partition physical order.** Canonical rows within one
partition's physical Parquet artifact MUST be written in the frozen total
order (§7.1). This is a physical materialization obligation on the producer,
not a DataGateway read-time obligation.

**Invariant OR2 — row-group order.** Row groups within one Parquet file MUST
themselves appear in an order consistent with the total order — i.e. every row
in row group `k` orders before every row in row group `k+1` under §7.1. This
is what allows a streaming reader to consume row groups sequentially without
buffering the whole file, and what allows row-group statistics pushdown
(already used opportunistically by `read_trade_v1`'s `_arrow_boundary_safe`
path) to prune safely.

**Invariant OR3 — deterministic partition processing order.** For a request
spanning multiple eligible partitions, partitions MUST be processed in
ascending order of their declared coverage start (`ts_start`). This is already
true of `Catalog.select_partitions`, whose SQL carries
`ORDER BY p.ts_start, p.ts_end, p.partition_key, p.revision` — this contract
freezes that ordering as a semantic requirement, not an incidental
implementation detail that a future query rewrite may drop.

### 7.3 The two-part non-overlap proof

**This is a correction to a prior draft**, which attributed cross-partition
non-overlap to declared-coverage contiguity alone. That is imprecise and is
not restated. The actual proof has two independent legs, each owned by a
different layer:

**Invariant OR4a — intra-partition contiguity (publish-time guarantee).**
DECLARED_COVERAGE.md I9 forbids *one partition's own* attributed coverage from
being non-contiguous or internally overlapping; a partition failing this is
not publishable to `catalog.partitions` at all (§13.4 restates this as a
certification/publication precondition). This guarantees each individual
partition's declared coverage is one clean interval. **It says nothing, by
itself, about whether two different partitions' intervals overlap each
other.**

**Invariant OR4b — inter-partition non-overlap (read-time fail-closed
guarantee).** Two eligible, non-superseded, differently-keyed partitions with
overlapping declared coverage are not forbidden from existing in the catalog
by I9 or by any producer-side invariant in this contract — a producer defect
or a race could in principle create one. What actually prevents a sequential
read from silently mis-ordering such data is that DATA_GATEWAY.md §7 and the
existing `gateway.py._validate_partition_set` implementation treat this case
as `CatalogConflict` and refuse to read, **before** any concatenation happens.
This is an enforcement guarantee, not a static non-existence guarantee.

**Invariant OR4 — sequential concatenation is safe because both legs run
before concatenation starts.** Given OR1–OR3 (intra-partition physical order,
row-group order, ascending processing order) and OR4a+OR4b together — not
OR4a alone — sequentially concatenating partitions in ascending coverage order
yields the correct total order without a k-way merge: OR4a guarantees each
partition internally has no interleaving to resolve, and OR4b guarantees that
by the time the sequential scan begins consuming partition `i+1` after
partition `i`, the read path has already checked (and would have aborted on)
any overlap between them. The k-way-merge-avoidance conclusion therefore rests
on an enforced invariant, not an assumed one.

**Invariant OR5 — boundary key validation.** A read spanning a partition
boundary MUST validate, not merely assume, that no record in partition `i`
has `exchange_ts >= partition i+1's coverage start` and no record in partition
`i+1` has `exchange_ts < partition i's coverage end`. This is the same check
`gateway.py._validate_partition_set` already performs at the catalog-coverage
level (OR4b); this invariant extends it to the physical row level as a
defensive consistency check a streaming reader may perform incrementally
rather than needing the current implementation's global post-hoc
`DataIntegrityError` on duplicate/misordered keys.

**Invariant OR6 — no incidental ordering sources.** Filesystem iteration
order, catalog row insertion order, catalog UUID order and storage-root
enumeration order MUST NOT influence output order. Only OR1–OR4 may.

### 7.4 When a k-way merge becomes necessary

OR4's "no k-way merge needed" conclusion depends on OR4b continuing to be
enforced at read time. A k-way merge across partitions becomes necessary only
if a future contract revision *removes* the `CatalogConflict` enforcement (for
example, an `ALLOW_PARTIAL` or multi-source-precedence policy that
DATA_GATEWAY.md §7 explicitly defers: "Future `ALLOW_PARTIAL` semantics
require a later contract revision") and allows overlapping eligible coverage
to be read rather than refused. This contract does not authorize such a
revision; it only records that OR4 would need to be revisited if one is
adopted.

## 8. DataGateway <-> CandleDefinition ordering identity compatibility (resolves B8)

### 8.1 Two identities, two abstraction levels

```text
DataGateway ordering identity:      bybit-trade-v1-exchange-ts-trade-id-v1
  names a CONCRETE tie-break rule for one venue's DataRequest identity:
  "compare parsed exchange_ts, then compare trade_id as an opaque string"

CandleDefinition source ordering:   trades@1-canonical-total-order-v1
  names an ABSTRACT GUARANTEE that CandleDefinition v1 depends on as an input
  contract: "the trades@1 result has a deterministic total order over every
  record in a bucket, by some venue-appropriate rule"
```

**Decision: option B (explicit normative mapping), not option A (merged
identity).** They operate at different abstraction levels and merging them
would force every future venue's DataGateway ordering-policy string to also
become a semantic input to CandleDefinition's frozen identity hash — which
CANDLE_DEFINITION.md §3.1 explicitly forbids adding to (its exact-identity
field list has no venue/source-ordering-implementation slot; only the abstract
dependency belongs there).

### 8.2 The frozen mapping

**Invariant OI1.** `trades@1-canonical-total-order-v1` is *satisfied by*
`bybit-trade-v1-exchange-ts-trade-id-v1` for the Bybit reference vertical,
**and only for `trade-v1` records that pass the Bybit first-vertical
eligibility profile (§10)** — i.e. `trade_id` is non-null and the
`(exchange_ts, trade_id)` key is unique. A `trades@1` result is a valid
CandleDefinition v1 input if and only if both hold: the `DataRequest` that
produced it used an ordering policy documented as satisfying the abstract
guarantee, and every record it returned actually carries the fields that
ordering policy depends on.

**Invariant OI2.** A future second venue's DataGateway ordering-policy
identity (for example `okx-trade-v1-exchange-ts-sequence-v1`) is valid
CandleDefinition v1 input only if it is added to this mapping table with an
explicit statement of why it satisfies the abstract guarantee (per-source tie
-break primitive, survives relocation/rebuild, independent of reader iteration
order — the same properties DATA_GATEWAY.md §4 already requires of a future
ordering primitive) **and** its own eligibility profile analogous to §10.
Adding a venue to this table is additive to this contract; it is not a
CandleDefinition v1 identity change, because `trades@1-canonical-total-order-v1`
names the guarantee, not the venue-specific mechanism.

**Invariant OI3.** A `trades@1` result produced under a `DataRequest` whose
ordering policy is not in this mapping table, or whose records fail the
associated eligibility profile, MUST fail CandleDefinition construction
explicitly (CANDLE_DEFINITION.md §3.3: "A source result without a
deterministic total order for all records in a bucket is not a valid v1
candle input and must fail explicitly"). It must not be silently accepted.

### 8.3 Acceptance test requirement

A future CandleDefinition runtime implementation MUST include a test
demonstrating: given conforming `trades@1` batches read under
`bybit-trade-v1-exchange-ts-trade-id-v1` (whether from a buffering `read()` or
a bounded `scan()`, §5.3), CandleDefinition OPEN/CLOSE semantics
(`open` = first source-ordered trade price, `close` = last source-ordered
trade price, per CANDLE_DEFINITION.md §5) are identical to the same computation
over the same events sorted by the canonical `(exchange_ts, trade_id)` key
directly. This is the acceptance test named as an obligation, not implemented,
by this contract.

## 9. Parquet physical contract for trade-v1 (resolves B1)

### 9.1 Semantic vs tunable

The following are SEMANTIC and frozen by this contract. They must hold for any
producer, any writer implementation, any compression choice.

| Concept | Requirement |
|---|---|
| Columns | Exactly `venue`, `instrument`, `exchange_ts`, `price`, `size`, `aggressor_side`, `receive_ts`, `trade_id`, `sequence` — the same set `src/quant_platform/data/parquet.py` already requires/accepts (`_REQUIRED`, `_OPTIONAL`). No other column. |
| Required vs optional | `venue`, `instrument`, `exchange_ts`, `price`, `size`, `aggressor_side` are required and non-null. `receive_ts`, `trade_id`, `sequence` are nullable at the generic `trade-v1` level and MUST remain absent/null when the source does not supply them — never fabricated (`trade-v1.json`, CORE_CONTRACTS.md §5). §10 freezes a *stricter, source-profile-scoped* requirement on `trade_id` for Bybit publication eligibility specifically; that stricter rule does not change this generic schema row. |
| `exchange_ts` physical representation | A native Arrow/Parquet `timestamp` logical type with an explicit unit (`s`/`ms`/`us`/`ns`) and UTC timezone is preferred, since it enables row-group statistics pushdown (`read_trade_v1`'s existing `_arrow_boundary_safe` path). A string ISO-8601 UTC representation identical to `trade-v1.json`'s canonical timestamp spelling remains an accepted fallback (`_timestamp_values` already reads either). Whichever is chosen, round-tripping through Parquet MUST NOT lose precision below what `trade-v1.json` requires (nanosecond-capable) and MUST NOT silently reinterpret the instant. |
| `receive_ts` physical representation | Same rule as `exchange_ts` when present; string or typed timestamp, never fabricated. |
| `price` / `size` representation | Canonical decimal string, exactly as `trade-v1.json` and `parquet.py._decimal_text` already require (`_POSITIVE_DECIMAL` pattern: no leading zeroes, no trailing fractional zeroes beyond what the source provides, no exponent, no binary float). A Parquet `DECIMAL` logical type is an acceptable alternative physical encoding only if it round-trips to the exact same canonical string on read; a fixed-scale decimal that would round or truncate any observed source value is not conformant. |
| `aggressor_side` | Enum `{"buy", "sell", "unknown"}`, exactly as `parquet.py` already validates. |
| `trade_id` | Opaque string when present; canonical comparator is lexicographic, never parsed as a number (CANDLE_DEFINITION.md §3.3). Generic nullability per this table's "Required vs optional" row; Bybit eligibility non-nullability per §10. |
| `sequence` | Canonical non-negative digit string when present (`_DIGITS` pattern), compared numerically, never lexicographically (DATA_GATEWAY.md §4). |
| Canonical row order | Exactly the ordering contract in §7.2 (OR1–OR2). |
| Unsupported-column behavior | A producer artifact with an extra column outside the frozen set MUST be rejected at read (already true: `read_trade_v1` raises `DataIntegrityError` for unsupported columns) and MUST NOT be written in the first place. |
| Canonical -> Parquet -> canonical equality | For every accepted `trade-v1` canonical record, writing it and reading it back through this physical contract MUST reproduce byte-identical canonical field values (same decimal string spelling, same instant, same enum spelling, same optional-field nullness). This is the physical-layer analogue of CANDLE_DEFINITION.md §13's semantic round-trip requirement. |

### 9.2 Tunable, not frozen here

The following remain producer/performance policy, explicitly not semantic:

- compression algorithm and level;
- target file size and exact row-group size (subject only to the *ordering*
  obligation OR2, not a size obligation);
- writer implementation/library;
- dictionary encoding, page sizing, statistics settings.

A future writer may change any of these without a contract version increase,
as long as §9.1's semantic table and §7.2's ordering invariants hold.

## 10. Bybit BTCUSDT first-vertical eligibility profile (resolves B9)

### 10.1 The conflict this resolves

The accepted Bybit deterministic ordering policy (DATA_GATEWAY.md §4,
`bybit-trade-v1-exchange-ts-trade-id-v1`) is exactly `(exchange_ts, trade_id)`.
This key is unusable if `trade_id` is null. But `trade-v1.json` legitimately
allows `trade_id = null`, because a future or source-generic dataset may lack
a native trade identity, and the frozen generic schema must not be narrowed
to accommodate one venue's ordering policy.

**This contract does not change `trade-v1.json`.** `trade_id = null` remains,
and must remain, generically schema-valid. Instead, this section freezes a
**stricter, source-scoped publication-eligibility profile** that sits between
generic schema validity and Bybit certification:

```text
generic record-schema validity  (trade-v1.json — unchanged, trade_id nullable)
        !=
source/profile publication eligibility  (this section — Bybit-specific, trade_id non-null)
```

A record, or a partition, can be simultaneously **schema-valid** and
**certification-ineligible**. These are not competing definitions of
correctness; they answer different questions ("is this well-formed
`trade-v1`?" vs. "can this specific partition become `state=valid` under the
Bybit conformity vertical?").

### 10.2 The frozen profile

**Invariant EP1.** A Bybit BTCUSDT historical canonical `trade-v1` partition
is eligible for `state=valid` under this conformity gate only if, for every
record in the partition:

```text
trade_id IS NOT NULL
trade_id != ""
```

**Invariant EP2.** The canonical ordering key `(exchange_ts, trade_id)` MUST
be unique across every record in the partition. A duplicate key is a
certification failure (§13.2's Canonical evidence category), not a
data-integrity condition to be silently deduplicated or to be caught only at
DataGateway read time as a defensive backstop (`gateway.py` currently raises
`DataIntegrityError` on a duplicate key — that check remains a legitimate
defense-in-depth backstop, but under this profile the certifier MUST already
have refused publication before a duplicate key could reach it).

**Invariant EP3.** No fallback ordering key is invented for a record that
fails EP1. There is no row-position, receive-time, or synthetic-counter
substitute. A record failing EP1 makes its **entire partition** ineligible for
`state=valid` under this profile; the correct producer response is to not
publish that partition (or to fix the source extraction so `trade_id` is
actually captured), not to invent an ordering surrogate. This is consistent
with MARKET_DATA_INGEST_CONTRACTS.md §3's rule that an adapter "must not
silently infer unavailable values."

**Invariant EP4.** A Bybit `trade-v1` artifact with one or more
`trade_id = null` records:

- **may** still be representable by generic `trade-v1` if the generic schema
  permits it (it does; this is unaffected);
- **MUST NOT** become `state = valid` under this conformity profile;
- **MUST NOT** pass certification (§13) for this vertical;
- **MUST NOT** be published as DataGateway-eligible data for this vertical
  (i.e. it must not reach `catalog.partitions` with a lifecycle state the
  default `VALID_ONLY` read policy selects).

**Invariant EP5.** This profile is scoped to `(venue=bybit, dataset_kind=trades,
record_schema=trade-v1)` for the historical vertical this gate covers. It is
not a retroactive amendment to `trade-v1.json`'s nullability rule for any
other venue or future source, and a future venue with its own native ordering
identity (§8.2 OI2) defines its own eligibility profile rather than inheriting
this one.

### 10.3 Where this is enforced

This profile is a **certification-time** check (§13.2, Canonical evidence
category), not a DataGateway read-time check and not a generic-schema check.
`trade-v1.json` and `tests/test_trade_v1.py` are unaffected. The existing
`gateway.py` `DataIntegrityError` on `record.trade_id is None` (`"Bybit
trade-v1 ordering requires trade_id"`) is consistent with EP1–EP4 as a
defense-in-depth backstop for data that should never have reached `valid` in
the first place, but this profile's primary enforcement point is
certification, before publication, not this read-time backstop.

## 11. CanonicalContentHashV1 specification (resolves B7, algorithm)

### 11.1 Goal

Same ordered canonical logical records → same `CanonicalContentHashV1`,
regardless of Parquet compression, row-group boundaries, writer metadata,
physical page encoding, storage root, catalog UUID, or file path. Two
byte-different Parquet artifacts encoding the same canonical records in the
same canonical order MUST produce the identical digest.

### 11.2 Scope and version

`CanonicalContentHashV1` identifies **one canonical partition's ordered
logical record content** — i.e. it is scoped to one `NaturalPartitionIdentity`
under one accepted revision, computed over exactly the records that partition
contains after certification. It is **not** a dataset-wide or
request-result-wide hash; a multi-partition `DataGateway` result's aggregate
content identity, should one ever be needed, is an explicitly separate future
concept this contract does not define.

### 11.3 Domain separation

Every digest begins from an explicit, versioned domain tag so it can never be
confused with a raw physical SHA-256 or with a hash computed under a future
`CanonicalContentHashV2`:

```text
DOMAIN_TAG = UTF-8 bytes of the literal string
  "quant-platform/canonical-content-hash-v1/trade-v1"
  followed by one 0x00 byte
```

### 11.4 Record scope and field order

For `trade-v1`, hash exactly these logical canonical fields, in exactly this
order, per record:

```text
venue
instrument
exchange_ts
receive_ts
price
size
aggressor_side
trade_id
sequence
```

Excluded, unconditionally: Parquet metadata, row-group metadata, physical
encodings, file names, paths, catalog IDs, storage roots, `content_sha256`
itself, and any runtime/diagnostic locator.

### 11.5 Record order

Records MUST be fed in the frozen canonical total order for the ordering
policy under which the partition was certified — for the first Bybit
vertical, exactly `(exchange_ts parsed instant, trade_id opaque string)`
(§7.1). `CanonicalContentHashV1` is therefore an **ordered**-content identity:
a row permutation of the same record set is not semantically identical and
MUST NOT produce the same digest. This is consistent with, and depends on,
the Bybit eligibility profile (§10): the hash's ordering key requires
`trade_id` to be non-null and the pair to be unique, exactly what EP1/EP2
already require before a partition can be certified.

### 11.6 Field serialization — canonical byte framing

A length-delimited binary framing is used, **not** JSON, so there is no
canonicalization-algorithm, whitespace, or key-ordering ambiguity to freeze:

```text
CanonicalContentHashV1(records) =
  SHA-256(
      DOMAIN_TAG
      || RECORD_COUNT                    -- 8-byte big-endian unsigned integer
      || RECORD_FRAME(records[0])
      || RECORD_FRAME(records[1])
      || ...
      || RECORD_FRAME(records[RECORD_COUNT - 1])
  )

RECORD_FRAME(record) =
  FIELD_FRAME(venue)
  || FIELD_FRAME(instrument)
  || FIELD_FRAME(exchange_ts)
  || FIELD_FRAME(receive_ts)
  || FIELD_FRAME(price)
  || FIELD_FRAME(size)
  || FIELD_FRAME(aggressor_side)
  || FIELD_FRAME(trade_id)
  || FIELD_FRAME(sequence)

FIELD_FRAME(value) =
  if value is None:
      0x00                               -- single null-tag byte; frame ends here
  else:
      0x01                               -- single present-tag byte
      || LENGTH                          -- 8-byte big-endian unsigned integer:
                                          -- byte length of ENCODED
      || ENCODED                         -- UTF-8 bytes, see §11.7
```

The `0x00`/`0x01` tag byte makes null distinct from an empty string at the
framing level, independent of whether any particular field's grammar happens
to forbid empty strings. The 8-byte length prefix makes record boundaries
unambiguous without relying on an assumption that no field value can contain a
delimiter byte.

### 11.7 Canonical field encoding (`ENCODED`, per field)

| Field | Encoding |
|---|---|
| `venue`, `instrument`, `aggressor_side`, `trade_id`, `sequence` | UTF-8 bytes of the exact canonical string already required by `trade-v1.json`/`parquet.py` (`aggressor_side` in `{"buy","sell","unknown"}`; `trade_id` opaque, compared lexicographically, never renormalized; `sequence` the canonical non-negative digit string). No locale-dependent transformation. |
| `price`, `size` | UTF-8 bytes of the canonical decimal string exactly as `trade-v1.json`'s pattern requires (no exponent, no leading zeroes, no non-required trailing fractional zeroes, no binary float). The stored canonical string is hashed directly; it is never round-tripped through a float or through `Decimal` renormalization first. |
| `exchange_ts`, `receive_ts` (when present) | UTF-8 bytes of one canonical UTC representation, computed **from the field's parsed `Instant.epoch_ns`**, not from the source's original string spelling: `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ` — exactly nine fractional digits, zero-padded, literal `Z` suffix. This normalizes away the spelling variation `trade-v1.json` explicitly permits (one to nine fractional digits): `"...00.1Z"` and `"...00.100000000Z"` denote the same instant and MUST hash identically. No float conversion is used to compute this string; it is derived from the integer nanosecond count only (`seconds, nanos = divmod(epoch_ns, 1_000_000_000)`, then fixed-width decimal formatting of `nanos`). |

### 11.8 Empty content

Zero logical records is a well-defined, non-null digest, not a sentinel or an
omitted hash:

```text
CanonicalContentHashV1(empty) = SHA-256(DOMAIN_TAG || 0x0000000000000000)
```

This matters because DECLARED_COVERAGE.md I5 makes a zero-row `complete`
partition a legitimate certified partition (§13.2's Coverage category); such
a partition still gets a real, computable `CanonicalContentHashV1`.

### 11.9 Output encoding and name

```text
canonical_content_hash_v1_value =
  "canonical-content-hash-v1:sha256:" + lowercase_hex(SHA-256(byte_stream))
```

matching the naming convention `CANDLE_DEFINITION.md` §3.1 already uses for
`candle-definition-v1:sha256:<hex>`.

### 11.10 Worked example (canonical byte layout, informative)

For a one-record partition with `venue="bybit"`, `instrument="BTCUSDT"`,
`exchange_ts` = instant with `epoch_ns` such that its canonical string is
`"2024-01-15T00:00:00.492000000Z"`, `receive_ts=None`, `price="42000.5"`,
`size="0.001"`, `aggressor_side="buy"`, `trade_id="123456"`, `sequence=None`:

```text
DOMAIN_TAG
  = b"quant-platform/canonical-content-hash-v1/trade-v1" + b"\x00"

RECORD_COUNT = (8 bytes) 0x0000000000000001

RECORD_FRAME =
  FIELD_FRAME("bybit")                              -- 0x01 + len(5)  + b"bybit"
  FIELD_FRAME("BTCUSDT")                             -- 0x01 + len(7)  + b"BTCUSDT"
  FIELD_FRAME("2024-01-15T00:00:00.492000000Z")      -- 0x01 + len(30) + b"2024-...Z"
  FIELD_FRAME(None)                                  -- 0x00
  FIELD_FRAME("42000.5")                             -- 0x01 + len(7)  + b"42000.5"
  FIELD_FRAME("0.001")                                -- 0x01 + len(5)  + b"0.001"
  FIELD_FRAME("buy")                                  -- 0x01 + len(3)  + b"buy"
  FIELD_FRAME("123456")                               -- 0x01 + len(6)  + b"123456"
  FIELD_FRAME(None)                                   -- 0x00

CanonicalContentHashV1 = "canonical-content-hash-v1:sha256:"
  + lowercase_hex(SHA-256(DOMAIN_TAG || RECORD_COUNT || RECORD_FRAME))
```

This example is illustrative of the exact byte layout, not a substitute for
computing and freezing a golden digest fixture, which is Conformity
Implementation Gate work (a `CanonicalContentHashV1` implementation and its
own fixture test), not authorized here.

## 12. Physical vs semantic vs result identity (resolves B7, decision)

### 12.1 The tension

DATA_GATEWAY.md §5 defines `result_identity` as a hash over, among other
things, "manifest and content identities" — i.e. `content_hashes`, the
physical Parquet artifact's `sha256`. This is frozen, accepted (ADR-0019) and
unchanged by this contract: `result_identity` continues to answer *"is this
exactly the same physical evidence chain"*, which is the correct question for
provenance and reproducibility of a specific `DataSlice`.

CANDLE_DEFINITION.md §13 (materialization equivalence, ADR-0021) requires a
different question to have a stable answer: *"are these two artifacts the same
canonical records"*, independent of "physical storage root, relative path,
file format, compression, partition placement, catalog UUID and process/cache
identity." Once a producer can legitimately regenerate a byte-different
Parquet artifact for identical canonical records (different compression,
different row-group size, a different writer version — all explicitly
tunable per §9.2), `result_identity` as currently defined answers "no, these
differ," even though ADR-0021 requires the semantic answer to be "yes,
equivalent." **This is not a contradiction between the two contracts** — see
§12.3.

### 12.2 The identity matrix

| Identity | Definition | Sensitive to compression/row-group/writer metadata? | Owning contract |
|---|---|---|---|
| `PhysicalArtifactHash` | `sha256` of the exact Parquet bytes (== today's `content_hashes` / `partition-manifest-v1` `content_sha256`, unchanged) | **Yes** — any byte difference changes it | DATA_GATEWAY.md / `partition-manifest-v1.json` (unchanged) |
| `CanonicalContentHashV1` | §11's algorithm over ordered canonical logical record content | **No** — invariant to compression, row-group layout, writer version/metadata by construction (§11.4 excludes them from the hashed input) | This contract, §11 (new, additive) |
| `result_identity` | ADR-0019/DATA_GATEWAY.md §5's existing hash, which includes `content_hashes` (i.e. `PhysicalArtifactHash` values) among its inputs | **Yes**, transitively, under current ADR-0019 — unless and until a future DataGateway contract version changes what `result_identity` is computed from | DATA_GATEWAY.md (unchanged by this contract) |

### 12.3 Behavior under compression/layout variation, stated without contradiction

Two byte-different but semantically equal Parquet artifacts for the same
canonical records, in the same canonical order, produce:

```text
PhysicalArtifactHash      DIFFERENT   (different bytes)
CanonicalContentHashV1    SAME        (§11 excludes exactly what differs)
result_identity           DIFFERENT   (under current ADR-0019; it includes
                                        PhysicalArtifactHash values)
```

**This is not a contradiction.** It is three different, correctly-scoped
questions receiving three internally consistent answers:

- "Is this the exact same physical evidence?" → `PhysicalArtifactHash` /
  `result_identity`: no.
- "Is this the same canonical content?" → `CanonicalContentHashV1`: yes.

**Invariant HX1.** Wherever this or any other frozen document uses the phrase
"semantic identity independent of compression/layout," it means
`CanonicalContentHashV1`, **never** the current ADR-0019 `result_identity`.
`result_identity` remains, by design and unchanged, sensitive to exact
physical evidence. A future implementation or reviewer that finds wording
implying `result_identity` itself becomes compression-invariant has found a
drafting defect against this invariant, not a valid reading of this contract.

### 12.4 Additive, not a rewrite

- `result_identity` keeps identifying exact source artifact provenance,
  unchanged, per §12.1: it answers the reproducibility question DATA_GATEWAY.md
  §5 already documents, and ADR-0019 is not reinterpreted.
- `CanonicalContentHashV1` is a new field alongside the existing stable
  metadata (`DataSliceMetadata` gains a `canonical_content_hash` field),
  computed per §11, independent of the physical artifact. It does not remove,
  rename or change the computation of `result_identity`. No existing
  `result_identity` value changes meaning. **No DataGateway contract version
  increase is required** for this addition; it is exactly the kind of
  additive extension DATA_GATEWAY.md §10 anticipates for a documented future
  `DataRequest`/`DataSliceMetadata` capability.
- CandleDefinition materialization equivalence (ADR-0021 §13) is restated to
  reference `CanonicalContentHashV1` (or an equivalent immutable canonical
  evidence path) as one of its "alternative evidence paths," consistent with
  CANDLE_DEFINITION.md §2's existing text: "The source snapshot/result
  identity and the ordered partition/content tuple are alternative evidence
  paths only when the snapshot identity itself is durable." This is a
  clarification of an already-open alternative-evidence-path question, not a
  reinterpretation of ADR-0021's frozen identity fields.

## 13. Certification / `state=valid` rule (resolves B3)

### 13.1 What `valid` must mean

DATA_GATEWAY.md §8 already states the consequence: "For a `valid` canonical
partition, normal reads may trust the catalog-recorded manifest/content
identities... Ordinary reads do not require a full multi-gigabyte content
rehash." This contract freezes the minimum evidence that must exist *before*
that trust is extended, for the first Bybit BTCUSDT `trade-v1` historical
vertical only. It does not invent a universal source-completeness guarantee
(DECLARED_COVERAGE.md I11 already forbids that at the coverage layer, and
this rule does not weaken it).

### 13.2 Minimum objective evidence by category

| Category | Minimum evidence required before `valid` |
|---|---|
| **Source** | Extraction from the declared source succeeded without a fail-fast abort (`import_bybit_trades.py`'s existing "FAIL-FAST: qualunque anomalia interrompe l'import" discipline); the requested source scope (date/instrument) is recorded; source evidence strength is named per DECLARED_COVERAGE.md I11 (`source_semantics`, versioned). |
| **Canonical** | Every emitted record validates against `trade-v1.json` (schema-valid); the partition satisfies the Bybit first-vertical eligibility profile (§10: `trade_id` non-null on every record, unique `(exchange_ts, trade_id)`); no `receive_ts` or `sequence` is fabricated (CORE_CONTRACTS.md §5); canonicalization is deterministic for the same source input (MARKET_DATA_INGEST_CONTRACTS.md §5). |
| **Physical** | The Parquet file satisfies §9.1's semantic table; it round-trips canonical -> Parquet -> canonical without loss (§9.1's equality requirement); `PhysicalArtifactHash` (byte size and `content_sha256`) and `CanonicalContentHashV1` (§11) are both computed and recorded. |
| **Manifests** | `DatasetManifest`, `PartitionManifest` validate against their frozen schemas; `PartitionManifest.row_count`, `first_exchange_ts`, `last_exchange_ts` are internally consistent (`ZERO_ROW_OBSERVED_BOUNDS` per DECLARED_COVERAGE.md I1 does not fire); a `CoverageManifest` `complete` assertion exists that resolves to this exact partition (DECLARED_COVERAGE.md I13) and does not contradict `transport_interruption`/`sequence_discontinuity` evidence (I7). |
| **Coverage** | The partition's attributed `complete` coverage folds (DECLARED_COVERAGE.md §4) to exactly one contiguous interval (I9); observed bounds lie inside declared coverage (I8). A partition failing this check is **not eligible for `valid`**, independent of source/canonical/physical evidence quality — I9 already makes such a partition unpublishable to `catalog.partitions` at all. |
| **Publication** | The catalog row's `(ts_start, ts_end)` equals the folded declared-coverage interval (§14 below), not observed bounds or a filename-derived guess; the catalog row's `content_sha256`/`manifest_sha256` match the durable manifests byte-for-byte; the durable certification evidence record (§13.3) is committed and referenceable before the lifecycle state is set to `valid`, and that record's `quality_reports.code_ref` is non-null and non-empty (§13.3 CE6) — a `status='pass'` report with no certifier identity does not satisfy this row. |

### 13.3 Durable certification evidence model

**The question this answers:** where does the durable evidence that justified
`state=valid` live?

**Answer: the existing `quality_reports` table, with no schema/DDL change.**
`db/init/001_catalog.sql`'s `quality_reports` already has exactly the shape
needed: `partition_id` (target, XOR with `dataset_id`), `check_suite` (text),
`status` (`pass`/`warn`/`fail`), `metrics` (`jsonb`), `violations` (`jsonb`),
`code_ref` (text), `ran_at`. Because `metrics` and `violations` are `jsonb`,
this contract can freeze an application-level record shape they must carry
without any DDL change — the same relationship `dataset-manifest-v1.json` etc.
already have to the database (an app-defined schema layered over a
schema-flexible column), not a new subsystem.

**Invariant CE1 — status mapping.** `quality_reports.status` and
`catalog.partitions.state` are different vocabularies; this contract freezes
their mapping for this vertical:

```text
quality_reports.status = 'pass'
  AND code_ref IS NOT NULL AND non-empty (CE6)  -> partitions.state MAY become 'valid'
quality_reports.status = 'pass'
  AND code_ref IS NULL or empty (CE6)           -> certification incomplete;
                                                    partitions.state MUST NOT
                                                    become 'valid' or 'degraded'
quality_reports.status = 'warn'
  AND code_ref IS NOT NULL AND non-empty (CE6)
  AND metrics.evidence.source.status = 'warn'
  AND metrics.evidence.canonical.status = 'pass'
  AND metrics.evidence.physical.status = 'pass'
  AND metrics.evidence.manifests.status = 'pass'
  AND metrics.evidence.coverage.status = 'pass'
                                                    -> partitions.state MAY
                                                       become 'degraded'
                                                       (weaker source-scope
                                                       evidence only)
quality_reports.status = 'warn' with any other
  evidence shape or missing certifier identity       -> partitions.state MUST
                                                       NOT become 'degraded'
quality_reports.status = 'fail'                 -> partitions.state MUST NOT
                                                    become 'valid' or
                                                    'degraded'; publication is
                                                    refused (§13.4)
```

`status == 'pass'` is therefore necessary but not sufficient for `valid`;
CE6's non-null, non-empty `code_ref` is an independent, equally mandatory
condition. For `degraded`, `status == 'warn'` is also necessary but not
sufficient: the exact predicate above is frozen for v1. It admits only a
source-scope warning while canonical, physical, manifest and coverage
evidence all pass. A warning caused by any other category, an unknown overall
status, an unexplained warning, or a missing/empty certifier identity is a
refusal condition, never degraded evidence.

**Invariant CE2 — frozen `metrics` shape.** For a certification run against
this vertical, `quality_reports.check_suite` MUST be the literal string
`"producer-consumer-conformity-v1/bybit-trade-v1-first-vertical"`, and
`quality_reports.metrics` MUST contain at least:

```json
{
  "certification_profile": "producer-consumer-conformity-v1/bybit-trade-v1-first-vertical-v1",
  "natural_partition_identity": {
    "dataset_identity": { "layer": "...", "dataset_kind": "...", "venue": "...", "instrument": "...", "record_schema_id": "..." },
    "partition_key": "...",
    "revision": 1
  },
  "dataset_manifest_sha256": "...",
  "partition_manifest_sha256": "...",
  "coverage_manifest_id": "...",
  "coverage_assertion_id": "...",
  "physical_artifact_hash": "...",
  "canonical_content_hash_v1": "canonical-content-hash-v1:sha256:...",
  "evidence": {
    "source": { "...": "..." },
    "canonical": { "...": "..." },
    "physical": { "...": "..." },
    "manifests": { "...": "..." },
    "coverage": { "...": "..." }
  }
}
```

and, when `status != 'pass'`, `quality_reports.violations` MUST enumerate
which §13.2 category failed and why, using the existing free-text/structured
`jsonb` shape — no new column is required for this either.

**Invariant CE3 — redundant natural-identity encoding, and why.**
`quality_reports.partition_id` is a foreign key to `catalog.partitions.partition_id`,
a **catalog-generated UUID**. DATA_GATEWAY.md already states such UUIDs "are
runtime/catalog locators and are not stable across a catalog rebuild or
reinsert." If the catalog is rebuilt (§14.4), a partition may be re-inserted
under a *new* UUID, which would silently orphan a `quality_reports` row keyed
only by the old FK. This is why CE2 requires `natural_partition_identity`,
`physical_artifact_hash` and `canonical_content_hash_v1` to also be recorded
**inside** `metrics`, redundantly with the FK: certification evidence for a
given partition MUST be resolvable by natural identity plus content hashes,
not solely by the transient catalog UUID, so that a rebuild can re-associate
existing evidence with a re-inserted row (or determine that fresh
certification is required) without ambiguity.

**Invariant CE4 — evidence precedes eligibility, always.** `state=valid` (or
`degraded`) MUST NOT become durable in `catalog.partitions` without a
committed `quality_reports` row satisfying CE1–CE3 already existing for that
partition. §13.5 fixes the exact ordering.

**Invariant CE5 — not itself a file-durable artifact.** Unlike
`DatasetManifest`/`PartitionManifest`/`CoverageManifest`, a `quality_reports`
row is catalog-durable (a committed PostgreSQL row) but not
filesystem/Git-durable — it cannot be reconstructed purely from files if the
database is lost. This contract does not close that gap: certification is
treated as a *reproducible operation* (same durable manifest + physical
evidence inputs → same certification outcome), so the correct recovery
behavior after catalog loss is to re-run certification, not to require a
file-durable certification artifact. A future `certification-evidence-v1`
durable artifact, mirroring `coverage-manifest-v1`'s relationship to
observed/declared coverage, is a plausible v2 evolution but is explicitly not
designed here (this contract does not design a new subsystem).

**Invariant CE6 — mandatory certifier identity.** `quality_reports.code_ref`
is the canonical certifier implementation identity for the first Bybit
conformity profile. The column stays generically nullable at the DDL level —
this contract does not add a DDL constraint — but the **certification
profile** is stricter than the table's generic structural shape:

```text
generic quality_reports structural validity    (code_ref MAY be null — DDL)
        !=
Bybit conformity PASS eligibility               (code_ref MUST NOT be null
                                                  or empty — this profile)
```

For the first Bybit historical `trade-v1` certification profile, a report
authorizes `partitions.state == 'valid'` only if, in addition to CE1's
`status == 'pass'` and CE2's evidence shape:

```text
quality_reports.code_ref IS NOT NULL
AND trim(quality_reports.code_ref) != ''
AND code_ref identifies the certifier implementation/code revision, per the
    same producer/code_ref semantics `db/init/001_catalog.sql` already uses
    for `partitions.code_ref` ("l'unico posto in cui vive" the exact
    execution revision that produced a given result)
```

A report with `status = 'pass'` and `code_ref = null`, or `status = 'pass'`
and `code_ref = ''`, **MUST NOT** authorize `state = 'valid'`. Missing
certifier identity means certification is **incomplete**, not weak evidence —
it is a refusal-to-publish condition (§13.4), never a `degraded` condition,
because `degraded` is reserved for weaker *source-scope* evidence, not for an
incomplete certification record about the certification itself.

**Invariant CE7 — certifier identity and producer identity are distinct
roles, never conflated.** `quality_reports.code_ref` identifies *the
certifier implementation* that evaluated §13.2's evidence categories and
produced this report. `catalog.partitions.code_ref` identifies *the
producer* that materialized the partition's physical artifact — a different
role, already required `NOT NULL` at the DDL level
(`db/init/001_catalog.sql`'s `partitions.code_ref text NOT NULL CHECK
(length(trim(code_ref)) > 0)`, documented there as "il commit con cui e'
stata materializzata"). The certifier and the producer MAY be the same code
revision in a simple pipeline, or MAY differ (a certifier upgraded
independently of the producer that wrote an older partition); this contract
does not assume either. Reading one `code_ref` as evidence for the other's
identity is a contract violation. No new field is introduced to hold the
certifier identity — CE6 uses the existing `quality_reports.code_ref` column
exactly as it already exists, precisely because it already serves this role
and duplicating it inside `metrics` would only create a second copy that
could silently drift from the column.

### 13.4 Failure outcomes

- **Any Source, Canonical (including the §10 eligibility profile), Physical,
  or certifier-identity failure (CE6/CE7 — missing or empty
  `quality_reports.code_ref`)** is a **refusal to publish**: the partition is
  not registered in `catalog.partitions` at all, or — if it already exists at
  `closed` — is never advanced past `closed`. A missing certifier identity is
  grouped here, not with Coverage or Manifest failures, because it is not a
  claim about the *data* being deficient at all — it means the certification
  *record itself* is incomplete, which is exactly the same class of defect as
  an artifact that was never actually evaluated.
- **A Coverage evidence failure** (non-contiguous or contradicted attribution)
  is also a **refusal to publish**, per DECLARED_COVERAGE.md I9/§7.1 — the
  existing decision, not a new one. `degraded` is explicitly not a substitute
  (I9.3, "no lifecycle state narrows coverage").
- **A Manifest evidence failure** (schema-invalid or internally
  inconsistent manifest) is a **refusal to publish**; a manifest is required
  evidence, not optional metadata.
- **`degraded`** is reserved for a partition whose Source/Canonical/Physical/
  Manifest/Coverage evidence is all present and internally consistent, but
  whose *source scope or extraction confidence* is weaker than the `valid`
  bar (for example, `source_semantics` naming a lower-strength extraction such
  as a legacy SQLite dump rather than an authoritative venue archive — the
  exact I11 example). It is never used to encode a coverage, eligibility-profile,
  physical, or certifier-identity defect.
- **`invalid`** is reserved for a partition that reached materialization but
  failed certification after the fact (for example, a later-discovered
  physical corruption); this contract does not define the transition
  mechanics for moving an already-`valid` partition to `invalid`, which
  remains open per MARKET_DATA_INGEST_CONTRACTS.md §10.

This contract does not invent a numeric confidence score or a generalized
quality-report-to-lifecycle formula; MARKET_DATA_INGEST_CONTRACTS.md §10
already records that mapping as open beyond this minimum rule.

### 13.5 Certification/publication sequencing (avoids a second circularity)

A naive protocol is circular: "certification requires the bridge to have
published the partition" while "the bridge requires a final certification
result before it may publish `valid`." This contract freezes a five-phase
sequence that avoids that:

```text
Phase 1 — SEAL (no certification dependency)
  the physical artifact is written and hashed; DatasetManifest/PartitionManifest
  are written; the catalog partition row is admitted at state='closed' with
  content_sha256/manifest_sha256/closed_at populated. This already satisfies
  the existing `closed_is_sealed` DDL constraint and requires no certification
  outcome. Revision admission is a contiguous rematerialization ordinal:
  the first admitted revision is 1, and a new revision is admitted only as
  the exact successor N+1 of the one authoritative live revision N. The
  complete `(dataset, partition_key)` topology is inspected under lock;
  historical rows must be superseded and lower than the live revision, and a
  family with history but no live row fails closed. Before admitting N+1,
  superseding N and inserting the new CLOSED row are one atomic Phase-1
  operation. An exact existing CLOSED target with identical authoritative
  Phase-1 evidence is an idempotent retry; conflicting or otherwise
  lifecycle-ineligible targets fail closed. No meaning is assigned to skipped
  revision numbers.
        |
        v
Phase 2 — CERTIFY (reads Phase 1's durable evidence + the coverage manifest)
  the certifier evaluates §13.2's five evidence categories (Source, Canonical,
  Physical, Manifests, Coverage) against the sealed artifact, its manifests,
  and the relevant CoverageManifest assertions; it also computes
  PhysicalArtifactHash confirmation and CanonicalContentHashV1 (§11). This
  phase reads catalog/manifest state but writes nothing yet.
        |
        v
Phase 3 — RECORD EVIDENCE (durable, before any eligibility change)
  a quality_reports row satisfying CE1-CE3, with a non-null non-empty
  code_ref satisfying CE6, is committed. At this point the partition is still
  'closed' in catalog.partitions -- evidence exists before eligibility can
  change, which is what CE4 requires.
        |
        v
Phase 4 — PUBLISH ELIGIBILITY (fail-closed, atomic with or strictly after Phase 3)
  catalog.partitions.state is updated to 'valid' or 'degraded' per CE1's
  mapping -- which for 'valid' requires CE6's code_ref condition, not status
  alone -- or left at 'closed'/never advanced on 'fail' or on missing
  certifier identity. If Phase 3's write and Phase 4's write are not one
  database transaction, Phase 3 MUST commit first and Phase 4 MUST be skipped
  entirely if Phase 3 fails -- there is no ordering in which 'valid' becomes
  visible without committed evidence that itself names its certifier.
        |
        v
Phase 5 — VERIFY
  the bridge re-reads the row it just wrote (or the transaction's own
  post-commit state) and confirms ts_start/ts_end, hashes and state match what
  Phases 1-4 intended, catching a lost update or a concurrent conflicting
  write before considering the publish complete.
```

**Invariant SEQ1.** Certification (Phase 2) never depends on the partition
already being `valid`, and publication of eligibility (Phase 4) never
proceeds without committed evidence (Phase 3) already existing. This is what
makes the sequence non-circular: each phase depends only on the *output* of
an earlier phase, never on its own eventual output.

**Invariant SEQ2.** This is a semantic phase sequence, not a mandated SQL
transaction shape; an implementation may collapse Phases 3–4 into one
transaction (preferred, for atomicity) or run them as separate fail-closed
steps, as long as SEQ1 holds either way.

**Invariant SEQ3 — revision-local Phase-1 admission.** Phase 1 does not carry
quality evidence across revisions. A newly admitted successor receives its
own generated `partition_id` and must proceed through Phase 2 CERTIFY and
Phase 3 RECORD EVIDENCE against that partition's own manifests, physical
artifact and coverage. If Phase 2 or Phase 3 fails after Phase 1 committed,
the predecessor remains `superseded` and the successor remains `closed`; this
slice does not define automatic restoration or recovery.

## 14. Manifest + Coverage -> Catalog mapping (resolves B2)

### 14.1 Inputs and outputs

```text
INPUTS                                    OUTPUTS
DatasetManifest                           catalog.datasets row
PartitionManifest                         catalog.partitions row
CoverageManifest (folded per              catalog.dataset_lineage row(s)
  DECLARED_COVERAGE.md §4)                catalog.quality_reports row (§13.3)
physical artifact (content_sha256)
storage-root context
certification result (§13)
```

Dataset lineage is derived from the `DatasetManifest.derived_from` parent
identities together with `DatasetManifest.transform`, and is persisted in
`catalog.dataset_lineage`. CoverageManifest folding establishes declared
coverage only; it does not establish dataset lineage.

### 14.2 Critical invariant

```text
catalog.partitions.ts_start / ts_end
    ==
the single folded contiguous complete interval from
reconstruct_catalog_coverage() (DECLARED_COVERAGE.md §4)

NEVER:
  first_exchange_ts / last_exchange_ts        (observed bounds, I1)
  a partition_key-derived guess                (partition_key carries no
                                                 interval meaning — errata,
                                                 DECLARED_COVERAGE.md §7)
  a filename-derived guess
```

This restates DECLARED_COVERAGE.md §4 step 6 and MARKET_DATA_INGEST_CONTRACTS.md
§9 exactly; it is not a new rule. It is repeated here because it is the single
invariant a bridge implementation is most likely to get wrong under deadline
pressure, and this contract's job is to make that failure impossible to miss.

### 14.3 Bridge obligations frozen by this contract

**Invariant BC1 — idempotency.** Re-running the bridge over an unchanged
input set (same manifests, same coverage manifests, same certification
result) MUST produce the same catalog state, not a duplicate row or a spurious
lineage entry. This follows from `reconstruct_catalog_coverage()` already
being pure and order-independent (DECLARED_COVERAGE.md §4); the bridge's
obligation is to not introduce non-determinism the fold does not have.

**Invariant BC2 — natural identity keying.** The bridge writes to
`catalog.partitions` keyed by `(dataset, partition_key, revision)`, matching
`partitions_one_live` (one live revision per key), never by a bridge-run
sequence number or wall-clock time.

**Invariant BC3 — revision handling.** A `CoverageManifest` supersession
(DECLARED_COVERAGE.md I10) that changes a partition's folded interval MUST
result in the catalog row being updated to the new folded interval when the
exact natural partition target is already `closed` and every non-coverage
Phase-1 field is identical. That is an in-place update of only `ts_start` and
`ts_end`: the `partition_id`, revision, physical/manifest evidence, producer
identity and `closed` lifecycle remain unchanged. If the partition manifest
itself was revised (`revision N+1`), a new catalog row
under the new revision with the old one's lifecycle state moved to
`superseded` — mirroring the existing partition-manifest supersession
mechanics MARKET_DATA_INGEST_CONTRACTS.md §11 already assumes. Certification
(§13.5) MUST be re-run after either kind of coverage change; a superseded revision's
`quality_reports` evidence is not carried forward as evidence for the new
revision's content.

For a same-revision coverage change, an existing target is updated in place
only when it is `closed` and every non-coverage Phase-1 field is identical.
Any non-`closed` target or changed non-coverage evidence is refused without
mutation. Certification MUST be re-run after the update; existing quality
evidence is never copied or rewritten.

**Invariant BC4 — storage-root mapping.** The bridge resolves
`storage_root_id` from the physical location actually used for the artifact
at publication time; it does not assume a fixed root per dataset (multiple
partitions of one dataset may already occupy different roots per
STORAGE_LIFECYCLE.md §3).

**Invariant BC5 — hashes and producer/`code_ref`.** `content_sha256` and
`manifest_sha256` on the catalog row are copied verbatim from the durable
manifests, never recomputed by the bridge from a fresh read of the artifact
(that would duplicate the certification step, §13) and never left null for a
row the bridge marks eligible.

**Invariant BC6 — lifecycle state is certification output, not bridge input.**
The bridge writes the lifecycle state (`valid`/`degraded`/`invalid`) that
certification (§13) already determined (§13.5 Phase 4); the bridge does not
itself decide certification outcomes.

**Invariant BC7 — rebuildability, precisely defined.** See §14.4.

**Invariant BC8 — failure behavior.** A bridge run that encounters any
reconstruction-fold violation (the stable violation codes in
DECLARED_COVERAGE.md §4, e.g. `COVERAGE_NOT_CONTIGUOUS`,
`PARTITION_NOT_ELIGIBLE`) publishes nothing for the affected natural partition
identity and reports the violation; it does not publish a partial or
best-effort catalog row. This mirrors the fold's own fail-closed behavior
(DECLARED_COVERAGE.md §4 "Violations are returned, never repaired") at the
catalog-write layer.

### 14.4 Catalog rebuild equality (precise semantic definition)

PostgreSQL-generated UUIDs (`datasets.dataset_id`, `partitions.partition_id`,
`quality_reports.report_id`, `rebuild_log.rebuild_id`) are **not** reproducible
identities: DATA_GATEWAY.md already states this. "The rebuilt catalog is
equivalent" therefore **never** means byte-identical or UUID-identical rows.

**Invariant RB1 — semantic rebuild equality.** A catalog rebuilt solely from
authoritative durable evidence (`DatasetManifest`, `PartitionManifest`,
`CoverageManifest`, the physical artifacts, and re-run certification per
CE5) is equivalent to a prior catalog state if and only if it reconstructs the
same values for every field in this table:

| Field class | Rebuild-authoritative (must match) | Runtime/generated (may differ) |
|---|---|---|
| Dataset identity | `DatasetIdentity` natural key (`layer`, `dataset_kind`, `venue`, `instrument`, `record_schema_id`, and `feature_set_slug`/`feature_set_version` where applicable) | `datasets.dataset_id` (UUID) |
| Partition identity | `NaturalPartitionIdentity` = `DatasetIdentity` + `partition_key` + `revision` | `partitions.partition_id` (UUID) |
| Coverage | `ts_start`/`ts_end` (the folded declared-coverage interval, §14.2) | — |
| Lifecycle | the lifecycle classification (`valid`/`degraded`/`invalid`/`superseded`) as derived from re-run certification evidence, per §13.4 | the exact wall-clock moment it was assigned (`closed_at`, `tiered_at`) unless a specific invariant elsewhere makes a timestamp semantic |
| Storage placement | `storage_root_id` correctly reflecting the artifact's *actual current* physical location (consistent with whatever placement exists at rebuild time — not required to match a placement from before an intervening, legitimate relocation per STORAGE_LIFECYCLE.md) | the exact prior `storage_root_id` value if a relocation legitimately happened between the two catalog states |
| `rel_path` | the path *semantics* (safe, rooted correctly under the dataset's `rel_root`, resolves to the same logical artifact) | the literal string only insofar as physical layout is unchanged; a relocation may legitimately change it |
| Physical evidence | `PhysicalArtifactHash` (`content_sha256`) | — |
| Semantic content evidence | `CanonicalContentHashV1`, **if** the implementation design chooses to catalog/persist it (this contract does not mandate a dedicated catalog column for it; §13.3's `quality_reports.metrics.canonical_content_hash_v1` is sufficient) | — |
| Manifest evidence | `manifest_sha256` and the referenced manifest identities | — |
| Provenance | `producer`, `code_ref` | — |
| Lineage | `dataset_lineage.transform` (semantic transformation identity) parent/child edges | `dataset_lineage.recorded_at` |
| Schema | `record_schema_id`, `schema_registry.version`, `schema_registry.json_sha256` | — |
| Certification/quality outcome | the evidence needed to reproduce eligibility per §13.2–§13.4 (i.e. re-running certification against the same durable inputs yields the same `status`/lifecycle) | `quality_reports.report_id` (UUID), `quality_reports.ran_at` |

**Invariant RB2 — DataGateway-stable semantics survive rebuild.**
`request_identity` and `result_identity` (DATA_GATEWAY.md §5) are already
defined to exclude catalog UUIDs, storage roots and paths from their inputs.
RB1 does not weaken that; it is the reason a rebuild that satisfies RB1 also
guarantees `request_identity`/`result_identity` for any given logical request
are unchanged before and after rebuild, even though every UUID in the catalog
changed.

**Invariant RB3 — `rebuild_log` is diagnostic, not semantic.** A
`rebuild_log` row's `rebuild_id`, `started_at`, `finished_at` are operational
metadata; `manifests_seen`, `rows_written` and `discrepancies` are useful
audit output but are not themselves part of RB1's equality definition — they
describe the rebuild *process*, not the resulting catalog *state*.

## 15. Golden conformity acceptance target (resolves the audit's test-target ask)

### 15.1 The vertical

```text
Bybit BTCUSDT trades, 2024-01-15 UTC
declared interval: [2024-01-15T00:00:00Z, 2024-01-16T00:00:00Z)

rows = 1,105,145
buy  = 553,875
sell = 551,270

first observed: 2024-01-15T00:00:00.492Z
last observed:  2024-01-15T23:59:59.931Z
```

**Current evidence.** `tests/integration_bybit_trades_2024_01_15.py` already
proves `rows`, `buy`, `sell`, `first_exchange_ts` and `last_exchange_ts` exactly
match these numbers for the SQLite -> canonical JSONL leg (it requires a
locally supplied SQLite file not versioned in the repository, so it is
excluded from `tools/run_tests.py`'s local suite, consistent with README.md's
existing "separate database/real-data integration tests" carve-out). This
contract does not re-derive these numbers; it adopts the existing proof as the
frozen target for every later leg of the chain. This day's data is also
assumed, for the purpose of this gate, to satisfy the Bybit eligibility
profile (§10) — i.e. every trade in the source SQLite carries a native
`trade_id`; this contract does not itself re-verify that assumption against
the real SQLite file.

### 15.2 Required future proof

The contract's original future-proof chain is retained below. This contract
does not maintain live implementation status; the current state is tracked in
[`CAPABILITY_MAP.md`](../product/CAPABILITY_MAP.md), with execution order and
active scope in [`ROADMAP.md`](../product/ROADMAP.md) and `SCOPE.md`.

```text
SQLite/source
  -> canonical trade-v1
  -> canonical Parquet
  -> Bybit eligibility profile check
  -> manifests
  -> declared coverage
  -> certification
  -> catalog
  -> bounded DataGateway finite read
  -> exact semantic equality against §15.1's numbers
```

**Invariant GV1.** The end-to-end proof MUST demonstrate the full chain
above produces the identical `rows`/`buy`/`sell`/first/last numbers, not just
that each leg independently claims correctness. This is the acceptance test
that makes the Producer–Consumer Conformity Gate objectively testable in the
runtime sense, once implemented, and is required for Conformity
Implementation Gate PASS (ADR-0023 §7).

**Invariant GV2.** The end-to-end proof MUST demonstrate that memory growth
during the bounded-DataGateway-read leg is not O(total rows) — for example, by
observing bounded process memory while streaming all 1,105,145 rows in small
batches, contrasted with the current buffering implementation.

### 15.3 Required future conformity tests beyond the golden day

The following are named as required future test scenarios, required for
Conformity Implementation Gate PASS; none is implemented by this contract:

- zero-event complete coverage (a day with a `complete` assertion and
  `row_count == 0`, DECLARED_COVERAGE.md I5);
- Bybit eligibility profile refusal (a partition with one `trade_id = null`
  record correctly fails certification and is never published, §10 EP4);
- gap/non-contiguous refusal (DECLARED_COVERAGE.md I9, already proved at the
  fold level by `test_coverage_lineage_and_reconstruction.py`; needed again at
  the bridge/catalog level once the bridge exists);
- supersession (DECLARED_COVERAGE.md I10, fold-level proof exists; bridge-level
  proof needed);
- overlap rejection (DATA_GATEWAY.md §7 `CatalogConflict`, already proved at
  `test_data_gateway.py`; needed again against a real bridge-published
  catalog);
- relocation (STORAGE_LIFECYCLE.md §4 invariants, no implementation yet);
- catalog rebuild against RB1's semantic equality definition (§14.4), not
  UUID equality;
- physical-layout variation (two byte-different but semantically identical
  Parquet artifacts for the same day, proving `CanonicalContentHashV1`
  equality and `result_identity` inequality per §12);
- abort before finite-stream completion (§5.2 `ABORTED`, BR2/BR4).

## 16. Consumer API compatibility

No transport is implemented here. This section only clarifies composition.

**Invariant CA1.** Application services above `DataGateway` MUST be able to
consume finite logical batches (§5.2 `READING`) without first constructing one
giant in-memory DTO, once the bounded `scan()` capability (§5.3) exists. This
is required for the Consumer API's own future job/streaming path
(CONSUMER_API.md §8) to be buildable without re-introducing the same O(N)
buffering one layer up.

**Invariant CA2.** Representation-level coverage
(`covered_intervals`/`gaps`/`complete`, CONSUMER_API.md §5) remains distinct
from source/DataGateway coverage (`eligible_coverage`/`coverage_gaps`,
DATA_GATEWAY.md §5), exactly as already frozen. Nothing in this contract
merges them; `RecordTimeBounds` (§6) composes into `returned_temporal_bounds`
per §6.3, and that is the only new composition point.

**Invariant CA3.** The existing sync-vs-job distinction (CONSUMER_API.md §8)
is preserved: pagination and network streaming remain deferred transport
choices, unaffected by the in-process bounded-read property (§5.1) this
contract freezes.

## 17. Historical vs live/replay boundary

This contract defines no live runtime. It records what may be shared and what
must stay separate, so a future live design does not collapse into this one
by accident.

**May share** (per §3's boundary composition): record/batch logical shape,
the ordering vocabulary (§7), `RecordTimeBounds`'s two-field shape (§6),
identity and provenance vocabulary.

**Must remain separate**: cursor semantics, reconnect/checkpoint state, an
open-ended stream lifecycle (as opposed to §5.2's finite `OPEN`/`READING`/
`COMPLETED`/`ABORTED`), live gap recovery, and finite-completed-result identity
(`result_identity` is defined only for a `COMPLETED` finite read; a live
stream has no equivalent single result identity by construction).

## 18. Storage relocation invariants

No relocation runtime is implemented here. This section restates
STORAGE_LIFECYCLE.md §3-§4 as binding on this contract's certification and
bridge sections: relocating a `valid` partition's bytes to a different storage
root MUST NOT change its natural identity, content hash, declared coverage,
`result_identity` composition (DATA_GATEWAY.md §5 already excludes storage
root/paths from `result_identity`), or the certification evidence recorded in
§13 — only `storage_root_id` and physical path metadata change, and those are
diagnostic locators, never semantic identity (DATA_GATEWAY.md §6), consistent
with §14.4's rebuild-equality table. Capacity monitoring remains, as
ROADMAP.md already states, an operational milestone after the golden vertical
and before mass backfill/high-volume live work; this contract does not move
that milestone.

## 19. Frozen contract governance

This contract does not rewrite ADR-0019, ADR-0020, ADR-0021 or ADR-0022 in
place. Every new semantic introduced above is one of:

- an **additive extension** to an existing contract (§6.2's
  `RecordTimeBounds` field, §12.4's `CanonicalContentHashV1` field, §5.3's
  `scan()` capability) — safe to add without a version increase, per the same
  discipline DATA_GATEWAY.md §10 already applies to projection;
- a **restatement** of an already-frozen rule at a new layer (§7.3's OR4a/OR4b
  correctly re-attributing existing coverage non-overlap enforcement; §14.2
  restating DECLARED_COVERAGE.md §4 step 6);
- a **new rule filling a documented open decision** (§13's certification
  rule, filling MARKET_DATA_INGEST_CONTRACTS.md §10's explicitly open
  quality-to-lifecycle mapping; §14.3's bridge invariants, filling
  MARKET_DATA_INGEST_CONTRACTS.md §9's explicitly missing bridge; §10's
  eligibility profile, filling the gap between generic `trade-v1` nullability
  and the Bybit ordering policy's dependency on `trade_id`).

No section of this contract changes the meaning of `trade-v1`,
`dataset-manifest-v1`, `partition-manifest-v1`, `coverage-manifest-v1`,
`candle-v1`, or any accepted ADR's decision text — **including ADR-0021**,
which §11 and §8 both depend on without altering. If a future implementation
mandate discovers that one of these frozen contracts cannot actually support
an invariant above, that is a stop-and-report condition under AGENTS.md ("If
code conflicts with an accepted ADR or contract, stop and report the
conflict"), not license to reinterpret the older contract silently.

## 20. Test plan

This contract's own test obligations are **contract-consistency tests only**:
checks that this document, ADR-0023, and the existing narrow DataGateway v1
implementation agree with each other and do not silently drift. See
[test_producer_consumer_conformity_v1.py](../../tests/test_producer_consumer_conformity_v1.py).
None of those tests proves runtime behavior, and none may be read as doing so
— the test file's own module docstring states this explicitly.

The following adversarial invariants are named as **required future
behavioral/runtime tests**, needed for Conformity Implementation Gate PASS
(ADR-0023 §7), and are **not** implemented by this contract or by the current
test suite:

- single-record `RecordTimeBounds` (RT2);
- aborted finite stream produces no result identity (BR2, BR4);
- Bybit eligibility profile refusal on a null `trade_id` (EP4, §15.3);
- physical/semantic hash distinction under two byte-different, semantically
  identical artifacts (§12, §15.3);
- `CanonicalContentHashV1` reproducibility against a golden fixture (§11.10's
  worked example is illustrative, not a frozen golden digest);
- ordering across a row-group boundary (OR2);
- ordering across a partition boundary (OR4, OR5);
- canonical Parquet semantic round-trip (§9.1's equality row);
- catalog coverage source is the folded coverage manifest, never observed
  bounds (§14.2, already proved at the fold level, needed again at the bridge
  level);
- `valid`-certification preconditions per category (§13.2), each with a
  negative test showing the missing-evidence case refuses publication;
- certification/publication sequencing (§13.5) — evidence commits before
  eligibility, verified under a simulated crash between Phase 3 and Phase 4;
- catalog rebuild against RB1's semantic equality definition, with generated
  UUIDs deliberately different between the two catalog states (§14.4);
- Candle ordering compatibility (§8.3).

## 21. Explicit non-goals

This contract does not define or implement: a Parquet writer; a DataGateway
streaming runtime; a manifest/coverage-to-catalog bridge; a catalog migration;
a producer certifier; a golden ingest run; FastAPI/HTTP/gRPC/Arrow Flight
transport; live collector, cursor or reconnect semantics; L1/L2/L3/MBO
contracts; a generic evidence/workflow/DAG framework; App UI/TUI/CLI behavior;
a `certification-evidence-v1` durable file artifact (§13.3 CE5); or any change
to `trade-v1`, `dataset-manifest-v1`, `partition-manifest-v1`,
`coverage-manifest-v1`, `candle-v1`, `db/init/001_catalog.sql`, or any accepted
ADR.

## 22. Evolution

Adding a venue to the ordering-identity mapping (§8.2 OI2) together with its
own eligibility profile, adding an evidence category to §13.2, or adding a
bridge invariant to §14.3 is additive evolution under this contract's own
governance (§19) and does not require a new contract version. Changing an
existing invariant's meaning — for example, permitting overlapping eligible
coverage (§7.4), relaxing the Bybit eligibility profile (§10), or changing
what `valid` requires (§13.2) — requires an explicit
`producer-consumer-conformity-v2` under the same discipline
[ADR-0018](../decisions/ADR-0018-frozen-market-data-contract-evolution.md)
already applies to the frozen market-data contracts.

## 23. Implementation plan (dependency order)

The Contract Freeze Gate has passed (ADR-0023 §6/§8), so implementation of the
dependency-ordered slices is authorized. Revised from the first candidate to put shared
semantic primitives — including `CanonicalContentHashV1`, which any
certifying/persisting component needs — before anything that consumes them.

```text
CONTRACT FREEZE GATE PASS
        |
        v
1. Shared semantic primitives
   - RecordTimeBounds (S6)
   - CanonicalContentHashV1 implementation + golden fixture (S11)
   - ordering compatibility primitives (S7, S8)
   depends on nothing else in this list; can start immediately
        |
        v
2. Canonical Parquet materializer (S9)
   - physical hash (PhysicalArtifactHash)
   - semantic content hash (CanonicalContentHashV1, from step 1)
   - ordered round-trip
   - Bybit first-vertical eligibility check (S10)
   depends on (1) for CanonicalContentHashV1
        |
        v
3. Manifest + coverage emission
   - DatasetManifest / PartitionManifest against (2)'s artifact
   - CoverageManifest assertions for the same acquisition run
   depends on (2)
        |
        v
4. Certifier (S13)
   - evaluates S13.2's five evidence categories
   - Phases 1-3 of S13.5's sequencing
   depends on (2), (3)
        |
        v
5. Manifest+coverage+certification -> catalog bridge (S14)
   - Phases 4-5 of S13.5's sequencing
   - RB1 semantic rebuild equality
   depends on (4)

IN PARALLEL, SAFE AFTER CONTRACT FREEZE GATE PASS (independent of 2-5):
        |
6. Bounded DataGateway finite read path (S5-S8)
   depends on (1) for RecordTimeBounds and ordering compatibility; does not
   depend on (2)-(5) since it reads already-published 'valid' partitions
   using the existing narrow slice's fixtures
        |
        v
7. Golden E2E conformity vertical (S15)
   depends on (1)-(6) all existing and passing their own tests
        |
        v
8. Relocation / rebuild / empty / gap / abort / physical-layout adversarial
   tests (S15.3, S20)
   depends on (7)
        |
        v
9. Candle ordering compatibility test (S8.3)
   depends on (6)
        |
        v
CONFORMITY IMPLEMENTATION GATE REVIEW (ADR-0023 S7)
        |
        v
PASS -> producer vertical expansion resumes
     -> consumer vertical expansion resumes
     -> Candle runtime may resume per ROADMAP.md
```

No step in this graph depends on the Conformity Implementation Gate itself
having passed — the gate review (last step) depends on steps 1–9, and steps
1–9 depend only on each other and on the Contract Freeze Gate having already
passed. There is no cycle.
