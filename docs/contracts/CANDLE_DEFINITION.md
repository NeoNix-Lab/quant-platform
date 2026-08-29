# CandleDefinition v1 Contract

**Status:** Contract v1 — semantic contract frozen; historical candle runtime not implemented

**Decision:** [ADR-0021 — CandleDefinition v1 semantic contract](../decisions/ADR-0021-candle-definition-v1.md)

**Scope:** fixed-duration, UTC epoch-aligned candles built from canonical
`trades@1` / `trade-v1` records

This is a representation contract candidate. It defines semantics before a
runtime exists. It does not implement an aggregator, live collector, API
endpoint, catalog migration or a general Representation framework.

## 1. Authority and v1 boundary

Candles are first-class derived representations, not features or UI objects.
`CandleDefinition` owns the reproducible meaning of one candle series. This v1
contract freezes only fixed-duration time candles over canonical trades. It does
not freeze footprint, book, volume-bar, tick-bar, L1/L2/L3 or calendar/session
representations. Those require their own explicit versioned contracts.

The first implementation slice is:

```text
canonical trades@1 / trade-v1
        -> CandleDefinition v1
        -> fixed-duration UTC candles
        -> CLOSED candle records
```

The source must already be a semantically valid canonical trade result. The
candle layer does not become a second DataGateway, source deduplicator or
physical-file reader.

## 2. Four distinct identity concepts

These concepts must not be collapsed:

| Concept | Owns | Does not own |
|---|---|---|
| `ConsumerMarketDataQuery` | semantic selector (`venue`, `instrument`), requested `[start,end)` interval and requested representation | candle parameters, catalog UUIDs, paths or materialization choice |
| `CandleDefinition` | the versioned meaning of the candle representation and its parameters | venue, instrument, query interval, source dataset UUID, partition locator or runtime state |
| `DatasetIdentity` | a logical canonical or derived dataset, including its venue/instrument and data-plane schema identity | candle aggregation meaning or physical path as identity |
| materialization identity | one derived result/materialization tied to a definition, source snapshot/revision and support interval | storage root, relative path, compression, catalog UUID or process-local cache key |

`venue` and `instrument` therefore remain in the consumer semantic selector and
in the resolved source `DatasetIdentity`; they are not duplicated in
`CandleDefinition`. The same definition can be applied to different selected
instruments when their source satisfies the `trades@1` dependency.

The conceptual semantic materialization identity is:

```text
CandleMaterializationIdentity =
  (CandleDefinition identity,
   source snapshot/result identity,
   ordered source partition identities and content revisions,
   required bucket-support interval(s),
   output record schema identity,
   canonical result/content identity)
```

The source snapshot/result identity and the ordered partition/content tuple are
alternative evidence paths only when the snapshot identity itself is durable;
at least one complete immutable source-evidence path is required. This tuple
is not the `CandleDefinition` identity and contains no physical locator.
Recomputing the same semantic result from the same source evidence does not
create a new semantic materialization merely because the file, compression,
catalog row or process changed.

## 3. CandleDefinition semantic identity

### 3.1 Exact identity fields

The semantic identity of `CandleDefinition v1` is the canonical serialization
of exactly these fields:

```text
representation_kind
definition_version
source
duration_ns
alignment
boundary
aggregation
empty_bucket_policy
closure
late_event_policy
availability
output_record_schema
numerical_semantics
```

No field is inferred from a filename, UI default, source path, venue,
instrument, query interval, catalog identifier, partition locator, process,
cache, materialization or implementation language. V1 has no optional semantic
fields and no aliases with different spellings or meanings.

The canonical payload for a five-minute definition is structurally:

```json
{"aggregation":{"close":"last_by_source_order","high":"max_exact_decimal_price","low":"min_exact_decimal_price","open":"first_by_source_order","trade_count":"count_source_trades","volume":"sum_exact_decimal_size"},"alignment":{"kind":"utc_epoch","offset_ns":"0"},"availability":{"causal_floor":"bucket_end","observed_available_at":"nullable_observed_source_finalization_time","partial_consumption":"forbidden"},"boundary":{"bucket":"[start,end)","query_selection":"bucket_intersects_[query_start,query_end)","timestamp_coordinate":"bucket_start"},"closure":{"closed_state":"CLOSED","evidence":"source_finalization_required","partial_state":"PARTIAL"},"definition_version":1,"duration_ns":"300000000000","empty_bucket_policy":"omit","late_event_policy":"revisioned_rebuild","numerical_semantics":"exact_decimal_no_rounding_v1","output_record_schema":"candle-v1","representation_kind":"candle","source":{"event_time_field":"exchange_ts","ordering_policy":"trades@1-canonical-total-order-v1","record_schema":"trade-v1","representation":"trades@1"}}
```

The one-line form above is illustrative of the bytes, not a permission to
omit fields. Object keys are sorted recursively, UTF-8 is used, whitespace is
omitted, and the payload is serialized using RFC 8785 JSON Canonicalization
Scheme rules. `duration_ns` and `offset_ns` are strings specifically to avoid
JSON number precision loss. The definition identity is:

```text
candle-definition-v1:sha256:<lowercase SHA-256 of canonical UTF-8 payload>
```

The hash is a semantic identity, not a materialization or runtime identity.
Changing any listed field requires a new representation/definition version;
accepted v1 meaning is never silently reinterpreted.

### 3.2 Duration canonicalization

The semantic duration is a positive integer number of nanoseconds, serialized
as a decimal digit string with no leading zeroes (`"1"`, not `"01"`). It is
not a floating-point value and cannot use calendar arithmetic.

A surface parser may accept only these explicitly defined forms:

- an unsigned integer followed by one of `ns`, `us`, `ms`, `s`, `m`, `h`, `d`;
- `HH:MM:SS` with an optional fractional second of one to nine digits.

The unit multipliers are fixed SI durations; `d` means exactly 86,400 seconds,
not a local or calendar day. The colon form has `0 <= MM < 60` and
`0 <= SS < 60`. All forms are converted to the one canonical `duration_ns`
field before identity is calculated. Thus `5m`, `300s` and `00:05:00`, when
accepted by the parser, all become `"300000000000"`. Any other spelling is
rejected rather than assigned an accidental meaning. V1 does not support
months, calendar periods, sessions or exchange-local durations.

### 3.3 Source representation dependency

The v1 source object is exactly the semantic dependency on canonical
`trades@1` backed by `trade-v1`:

- event time is `trade-v1.exchange_ts`;
- price is `trade-v1.price`;
- size is `trade-v1.size`;
- source ordering is the deterministic total order guaranteed by the
  `trades@1` result under the named `trades@1-canonical-total-order-v1`
  policy;
- no filesystem/batch/reader iteration order is valid.

For the current Bybit reference slice, that deterministic order is
`(exchange_ts, trade_id)` using parsed timestamp order and the source's
canonical **opaque-string lexicographic** trade-id comparator. A trade ID is
not parsed as a number: values such as `"100"`, `"20"`, `"9"` retain the
source ordering `"100"`, `"20"`, `"9"`. A venue sequence, when it is the
declared source tie-breaker in a future source policy, is compared by its
numeric value, never lexicographically as its serialized string. A source
result without a deterministic total order for all records in a bucket is not
a valid v1 candle input and must fail explicitly. The candle definition does
not invent a tie-breaker.

`receive_ts` is not required for the historical source. If it is absent in
`trade-v1`, it remains absent; it is never derived from `exchange_ts`, a file
name, import time or row position.

## 4. Alignment, boundaries and temporal coordinates

V1 alignment is UTC Unix-epoch alignment only. The reference is
`1970-01-01T00:00:00Z`; the offset is fixed to `0` nanoseconds. Local time,
timezone offsets and DST never participate. Offset alignment is not a v1
feature and a non-zero offset is rejected.

For an event timestamp `t` and duration `D`, the bucket start is calculated
using mathematical floor division:

```text
k = floor((t - unix_epoch) / D)
bucket_start = unix_epoch + k * D
bucket_end   = bucket_start + D
```

The calculation is defined over integer nanoseconds, including for timestamps
before 1970; truncation toward zero is not equivalent and is not permitted.
Each bucket is half-open:

```text
[bucket_start, bucket_end)
```

A trade exactly at `bucket_start` belongs to that bucket. A trade exactly at
`bucket_end` belongs to the next bucket. The candle's canonical temporal
coordinate and record time are `bucket_start`; `bucket_end` is also present in
the record so the full interval is explicit. Neither coordinate is an
availability timestamp.

## 5. Deterministic OHLCV and count semantics

For each non-empty bucket, source trades are consumed in the deterministic
source order described in section 3.3:

- `open` is the price of the first source-ordered trade;
- `high` is the exact-decimal maximum of all trade prices;
- `low` is the exact-decimal minimum of all trade prices;
- `close` is the price of the last source-ordered trade;
- `volume` is the exact-decimal sum of all trade sizes;
- `trade_count` is the count of source trade records in the bucket.

`aggressor_side` does not change OHLCV in v1. No VWAP, delta, buy/sell
volume, imbalance, intensity or other derived feature is silently added.

Price comparison and size addition use arbitrary-precision exact decimal
semantics. Canonical aggregation must not parse, compare, sum or round these
values through binary floating point. V1 has no implicit scale, tick-size
rounding, clamping or overflow fallback; an implementation that cannot retain
the exact value must fail rather than round it.

Source decimal strings retain their source representation under `trade-v1`.
Output decimal strings use one canonical numeric spelling: no exponent, no
leading zeroes, and no trailing fractional zeroes; zero would be `"0"`.
All v1 OHLC and volume values are strictly positive, so zero is not valid for
those fields. `trade_count` is a canonical non-zero digit string because a
non-empty candle always has at least one source trade and counts must not be
exposed to JSON/float precision loss.

## 6. Empty buckets

An otherwise fully source-covered bucket with zero trades produces **no candle
record**. V1 does not emit null OHLC, zero OHLCV, or carry-forward values.

This omission is not a coverage gap. Coverage is established from declared
source support and finalization evidence, not from returned row count or
observed event bounds. Therefore:

```text
full source support + zero trades -> no row, covered bucket
missing source support             -> uncovered bucket / no CLOSED result
```

An empty result over a fully supported requested bucket set is a successful,
complete empty result with null returned record bounds at the consumer
envelope. Empty candle buckets do not appear in `candle-v1` because there is no
reproducible OHLC record to serialize.

## 7. Query interval, bucket support and returned records

The consumer query interval remains `[query_start, query_end)`. For a candle
representation, a candidate bucket is selected when its full interval
intersects the requested interval:

```text
if query_start == query_end:
    candidate_buckets = empty
    required_bucket_support = empty
    returned records = empty
    representation coverage is complete over the empty requested interval
else:
    candidate bucket iff:
        bucket_start < query_end
        AND
        bucket_end > query_start
```

`query_start > query_end` is invalid under the frozen Consumer API contract.
The equality case is an explicit exception to the overlap predicate above;
it is not evaluated as an ordinary non-empty interval.

Only complete candidate buckets are constructed. Only non-empty candidate
buckets are returned. The returned candle is never truncated to the query
edge; its full `[bucket_start,bucket_end)` remains explicit.

For example, with `D = 5 minutes` and request `[10:02, 10:07)`:

```text
constructed buckets: [10:00, 10:05), [10:05, 10:10)
returned coordinates: 10:00 and 10:05, for non-empty buckets
source-support interval: [10:00, 10:10)
```

Both boundary buckets are selected because they intersect the request. The
first selected bucket requires support before `query_start` and the last can
require support after `query_end`; these are explicit representation-support
rules, not hidden lookahead or feature warmup. Any non-empty query therefore
has at least one candidate aligned bucket. A zero-length query has no
candidates, including both `[10:02,10:02)` and `[10:00,10:00)`; each has empty
required support, empty returned records and complete representation coverage
over its empty requested interval.

`required_bucket_support` is the union of complete `[bucket_start,bucket_end)`
intervals for all candidate buckets. It is the interval(s) the source must
cover to construct the requested representation. It is distinct from both the
consumer query interval and the returned record coordinates.

The same rule applies to on-demand and materialized reads. A materialization
may store a larger support range, but the consumer still receives only
candidate records whose bucket intervals intersect the query interval.

## 8. Coverage and precise terminology

Representation coverage is not identical to DataGateway/source coverage:

```text
declared source coverage
        -> required bucket support
        -> finalizable candle buckets
        -> returned non-empty candle records
```

For v1 historical construction, every required bucket-support interval must
be fully covered by eligible source coverage, with no internal gap, and must
have source finalization evidence. The source read uses the existing strict
DataGateway coverage semantics; an uncovered support interval is `NoCoverage`
and must not yield a partial series or a falsely complete result.

Full source support plus zero events is still complete coverage. Observed
`first_exchange_ts`/`last_exchange_ts` bounds are not declared support and
cannot be substituted for it. Incomplete first/last source support does not
become a partial candle; the bucket is not `CLOSED`.

To compose with the frozen Consumer API coverage envelope, project each
candidate bucket interval onto the requested interval. A fully supported and
finalizable bucket contributes its intersection with the request to
`coverage.covered_intervals`; an incomplete candidate contributes that same
intersection to `coverage.gaps`. The union is normalized using the same UTC
half-open rules as the Consumer API. `coverage.complete` is true exactly when
the projected gaps are empty. For `[10:02,10:07)`, complete support for both
candidate buckets reports covered `[10:02,10:07)` even though source support is
`[10:00,10:10)`. If `[10:05,10:10)` lacks source support, the requested gap is
`[10:05,10:07)` and strict v1 returns `NoCoverage`; it does not return the
other bucket as a silently partial series. For `[10:02,10:03)`, the
intersecting `[10:00,10:05)` bucket projects to covered `[10:02,10:03)`;
there is no requirement for an aligned `bucket_start` inside the query. A
fully supported empty bucket contributes coverage even though it contributes
no record.

There is no indicator warmup in CandleDefinition v1. The source interval
needed to complete aligned buckets is called **required bucket support** or
**support extension**, never feature warmup. A support extension can be on
both sides of a non-aligned non-empty query in v1 because full intersecting
buckets are required. For `[10:02,10:07)`, support extends left to `10:00` and
right to `10:10`.

## 9. PARTIAL and CLOSED state model

State belongs to a runtime observation/envelope, not to the canonical
`candle-v1` record. A materialized candle series contains only `CLOSED`
non-empty records plus its coverage/provenance envelope.

### PARTIAL

`PARTIAL` is mutable, incremental and unsealed. It may be updated as source
events arrive or as source completeness changes. It is not a reproducible
historical result, must not be consumed as a closed candle by Features,
Research or Strategy, and is not eligible for canonical closed-series
materialization.

### CLOSED

`CLOSED` is immutable under its source snapshot/revision. A non-empty CLOSED
record is eligible for reproducible historical use and materialization. An
empty finalized bucket is represented by coverage/finalization evidence and
absence of a row, not by a fabricated CLOSED record.

Closure requires all of the following:

1. complete eligible source coverage for the entire bucket support interval;
2. a deterministic source ordering contract;
3. a stable source snapshot/revision and content evidence;
4. explicit source finalization/completeness evidence for that interval.

Wall-clock passage beyond `bucket_end` alone is never closure evidence. For a
historical read, immutable valid source partitions, their declared coverage,
content/manifest identities and strict gap-free selection can provide the
required finalization evidence. For incremental/live operation, the source or
collector must provide a named completeness/watermark/finalization signal.
The exact live collector protocol is out of scope, but it may not be omitted
from a future implementation.

## 10. Late events, corrections and revisions

An event whose `exchange_ts` is inside a bucket but arrives after the bucket's
wall-clock end is still accepted into the mutable `PARTIAL` state if the
bucket has not received closure evidence. Apparent boundary passage does not
seal it.

After a bucket is CLOSED, a newly discovered event, source correction or
changed source partition revision must never silently mutate that CLOSED
record in place. The required behavior is:

1. retain the old CLOSED result as an immutable result of its old source
   snapshot/revision;
2. publish or select a new source revision/content identity;
3. rebuild every affected bucket from the complete revised source support;
4. produce a new CLOSED result/materialization revision with lineage to the
   revised source and, where applicable, the superseded result.

An implementation may mark the old result superseded in a catalog/application
view, but supersession is not mutation and the old bytes/meaning remain
reproducible. Source partition revisions and materialization revisions are
provenance/result concepts; neither is added to CandleDefinition identity.

The candle runtime does not define live source precedence, deduplication or
repair orchestration. It must, however, honor the immutable-closed and
revisioned-rebuild obligations above.

## 11. Temporal availability and lookahead prevention

The relevant times are distinct:

| Time | Meaning |
|---|---|
| trade `exchange_ts` | venue event time used for bucket membership |
| `bucket_start` / `bucket_end` | representation interval boundaries |
| `record_time` | canonical candle coordinate, equal to `bucket_start` |
| `causal_floor` | deterministic minimum logical time before which the final CLOSED result may not be consumed; v1 is `bucket_end` |
| `observed_available_at` | actual observed source/finalization availability time when evidence exists; nullable/unknown otherwise |

For every CLOSED candle, `causal_floor` is exactly `bucket_end`. If
`observed_available_at` is present, it must be no earlier than that floor and
must represent actual observed source/finalization availability. A
Feature/Research/Strategy consumer must not consume final CLOSED values before
the applicable causal floor or observed availability constraint. `CLOSED`
does not imply `observed_available_at == bucket_end`.

Historical `trade-v1` imports generally have no real `receive_ts`. They
therefore cannot establish the wall-clock time at which a live observer could
have known the result. `observed_available_at` is consequently nullable/unknown
unless adequate observed source/finalization evidence exists; the contract
never manufactures it from exchange time, file/import time or row order. For
historical causal replay, `bucket_end` is the deterministic logical causal
floor, not a claim that the data was actually available then. A future replay
may explicitly adopt an exchange-time-only availability assumption, but that
assumption must not be presented as observed live availability or
latency-faithful replay. When actual source finalization/observation evidence
exists, it must remain in provenance and the later real time governs.

`PARTIAL` has no final-candle availability and is not a closed-candle input.
`bucket_start` must never be used as a proxy for final value availability.

## 12. Historical/live equivalence

The core convergence invariant is:

```text
same CandleDefinition
+ semantically equivalent complete source events
+ same source revision/content
= same final CLOSED candle records
```

This equality holds regardless of whether construction is batch historical,
incremental, on demand or sourced from an approved materialized closed series.
Intermediate PARTIAL states and update timing need not be byte-identical. Final
closed records, bucket membership, exact decimals, ordering-dependent open/
close, empty-bucket omission and coverage meaning must be equivalent.

## 13. Materialization equivalence

An on-demand CLOSED result and a catalog-backed materialized result are
semantically equivalent when they have:

- the same CandleDefinition identity;
- the same candidate bucket set and returned query semantics;
- the same source snapshot/revision/content evidence;
- the same required bucket support and coverage result;
- the same canonical `candle-v1` records, including omission of empty buckets;
- compatible availability/finalization evidence.

Materialization is a derived dataset operation, not a new candle meaning.
Physical storage root, relative path, file format, compression, partition
placement, catalog UUID and process/cache identity are not semantic identity.
Materialized output must retain source lineage and provenance. An
implementation identity is recorded to explain how computation occurred, but
does not make otherwise equal semantic records unequal; differing output is a
contract failure exposed by content/result identity, not a new definition.

## 14. Provenance minimum

Every reusable CLOSED result, and every on-demand result that claims
reproducibility, must retain at least:

```text
CandleDefinition identity and version
source representation identity: trades@1 / trade-v1
resolved natural source DatasetIdentity
ordered NaturalPartitionIdentity values and revisions
source manifest/content hashes or equivalent immutable content evidence
source result/snapshot identity when one exists
requested interval and required bucket-support interval(s)
source coverage and finalization evidence
implementation/code identity used for computation
materialization/result identity when persisted
```

Catalog UUIDs, storage roots, relative paths and absolute paths may be
diagnostic runtime metadata but cannot enter semantic identity or be the only
provenance needed to reproduce a result. Missing `receive_ts` remains missing.

## 15. Canonical output record

The proposed `schemas/candle-v1.json` defines one non-empty CLOSED record:

| Field | Meaning |
|---|---|
| `bucket_start` | UTC canonical start of the half-open bucket; record time |
| `bucket_end` | UTC canonical end; exactly `bucket_start + duration_ns` |
| `open` | first source-ordered trade price |
| `high` | exact-decimal maximum trade price |
| `low` | exact-decimal minimum trade price |
| `close` | last source-ordered trade price |
| `volume` | exact-decimal sum of trade sizes |
| `trade_count` | exact count of source trade records, canonical digit string |

All fields are required and no additional fields are accepted by the proposed
record schema. `state`, availability, source provenance and definition
identity belong in the runtime/result envelope, not in each row. A partial
runtime object is not a `candle-v1` materialized record.

## 16. Explicit non-goals and evolution

This contract does not define:

- an aggregator class, package, execution schedule or live collector;
- calendar/month/session alignment;
- volume/tick/dollar bars;
- footprint, book or L1/L2/L3 representations;
- source deduplication, precedence, repair or correction orchestration;
- DataGateway, catalog DDL, producer coverage population or storage layout;
- API transport, UI/TUI/CLI behavior or feature/indicator semantics.

Any semantic change to v1, including changing empty buckets, timestamp
coordinate, source order, numerical rounding, query selection, closure or
availability, requires an explicit new version under ADR-0018. The runtime
mandate must implement this contract as written after independent review; it
must not use implementation defaults to fill gaps in the contract.
