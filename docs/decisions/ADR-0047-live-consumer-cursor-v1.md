# ADR-0047 — Live-consumer cursor v1 (B06)

**Status:** ACCEPTED
**Date:** 2026-09-28

## Context

`SCOPE.md` ("Wave 6 — Live Consumer Data Plane & Storage Lifecycle v1") Active
Path step 1 requires a design gate that resolves `DG-B`'s still-open
live-consumer cursor proposition before any implementation slice may build
`DataGateway.live_stream()` (issue #193, blocking issue #194 and,
transitively, #195).

`A11` (`ADR-0040`) and `B03` are frozen and implemented: canonical trade
acquisition, deduplication and bounded reconnect reconciliation exist, and
`DataGateway.scan()` (`B02`) already provides a bounded-memory, ordered,
finite historical read over the same canonical `trade-v1` storage. `K10`
(`ADR-0042`) already defines `LiveCheckpointV1`, the durable recovery unit
the *producer* side uses to resume acquisition after a restart. Nothing in
the repository today lets an in-process *consumer* read a live, ordered,
resumable tail of that same canonical storage — `scan()` is bounded and
finite by design, and `LiveCheckpointV1` is A11's own internal recovery
state, not a consumer-facing contract.

Issue #193's own decision questionnaire (posted to the issue as a comment)
separates what existing authority already answers ("auto-answered") from
what this ADR must still freeze ("open decision"). This ADR closes exactly
the ten open decisions that questionnaire identifies; it does not revisit
the auto-answered items, and it does not reopen issue #110's
`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` disposition.

## Decision

### 1. `live_stream()` is a synchronous pull iterator, not async, callback, or broker

`DataGateway.scan()` is already a synchronous Python iterator over batches
(`for batch in scan: ...`); nothing else in the read/access path is async.
The only `asyncio` usage in the repository is confined to `A11`'s WebSocket
acquisition layer (`application/bybit_live.py`), which durably publishes
canonical partitions *before* any consumer ever reads them (`ADR-0043`'s
publication-before-checkpoint invariant). `live_stream()` therefore follows
`scan()`'s own precedent exactly:

```python
def live_stream(self, request: LiveStreamRequest, *, cursor: LiveStreamCursorV1 | None = None) -> LiveStream: ...
```

`LiveStream` implements the standard iterator protocol and yields
`LiveStreamEvent` values (defined below) one at a time, pulled at the
caller's own pace. There is no push/subscribe model, no broker, and no new
async framework.

### 2. Backpressure is structural, not policy: pull-based tailing of durable storage needs none

Because `live_stream()` re-derives its position from already-published,
durable canonical storage on every pull — exactly as `scan()` re-derives its
batches from catalog-selected partitions — a slow consumer simply reads
older already-published records more slowly. It never blocks acquisition
(A11 keeps publishing independently) and the gateway never buffers an
unbounded in-memory queue on the consumer's behalf. This closes open
decision 21 (slow-consumer/backpressure policy) without inventing a drop,
block, or fail-closed policy: v1 has no separate backpressure state because
the pull model makes one unnecessary.

### 3. The cursor is a caller-owned, opaque value; the gateway persists nothing new

`live_stream()` accepts an optional `cursor` argument and every
`LiveStreamEvent` carries the caller's next resume cursor. The gateway
itself never persists a cursor to disk or catalog — exactly as `scan()`
never persists `request.start`/`request.end`. This closes open decision 19
(who owns durable consumer offsets): **the caller does**, matching issue
#193's acceptance constraint that B06 must not require new physical storage
or catalog schema beyond `A11`/`K10`. A future `application`-layer composer
may choose to persist a cursor durably (for example by reusing the existing
`operations.checkpoint.CheckpointStore` mechanism, unmodified), but that
choice is outside `DataGateway`'s own contract and outside this ADR.

### 4. Cursor payload: mirror `LiveCheckpointV1`'s proven shape, narrowed to consumer needs

Closing open decision 1 (exact cursor payload), `LiveStreamCursorV1` is:

```python
@dataclass(frozen=True, slots=True)
class LiveStreamCursorV1:
    schema_version: str  # "live-stream-cursor-v1"
    dataset_identity: DatasetIdentity
    ordering_policy: str
    coverage_segment_id: str
    last_canonical_exchange_ts: Instant | None
    last_canonical_trade_id: str | None
    last_observed_sequence: str | None  # diagnostic only, never authoritative (ADR-0040)
```

This deliberately reuses `LiveCheckpointV1`'s own field vocabulary
(`dataset_identity`, the `(exchange_ts, trade_id)` canonical order key,
`last_observed_sequence` as non-authoritative diagnostic evidence,
`coverage_segment_id` as the governed live-segment identity) rather than
inventing a competing identity model. `last_canonical_exchange_ts`/
`last_canonical_trade_id` are `None` only for the initial cursor before any
record has been delivered (start-of-stream). `catalog_dataset_id`,
`partition_key`, `revision` and `partition_manifest_sha256` are
deliberately **not** carried: those are `K10`'s producer-side publication
generation identifiers, not semantic consumer-read state, and carrying them
would let physical publication detail leak into cursor identity — forbidden
by open decision 8's auto-answered constraint.

### 5. Resume is exclusive-of-anchor; duplication is A11's job, not B06's

Closing open decisions 2 and 3: `live_stream(request, cursor=c)` resumes
strictly **after** `c.last_canonical_order_key` — it never redelivers the
anchor record. Upstream deduplication of at-least-once provider evidence is
already `A11`/`ADR-0040`'s job (canonical publication is already
deduplicated before B06 ever reads it); `live_stream()` does not re-run a
dedup pass. Combined with exclusive-anchor resume, this makes "no
duplicate, no lost already-delivered record" directly testable: resuming
twice from the same cursor against unchanged canonical storage yields the
same subsequent sequence, deterministically.

### 6. Minimal event vocabulary: three event types, no more

Closing open decisions 5 and 18, `LiveStreamEvent` is a union of exactly
three types — deliberately not a larger taxonomy, per the questionnaire's
own stop condition against a generic pub/sub vocabulary:

```python
@dataclass(frozen=True, slots=True)
class LiveTradeEvent:
    record: TradeRecord
    cursor: LiveStreamCursorV1


class LiveSessionState(str, Enum):
    HEARTBEAT = "heartbeat"
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"


@dataclass(frozen=True, slots=True)
class LiveSessionEvent:
    state: LiveSessionState
    cursor: LiveStreamCursorV1
    evidence: str | None  # operational/diagnostic only, e.g. provider session id


@dataclass(frozen=True, slots=True)
class LiveGapEvent:
    cursor: LiveStreamCursorV1
    reason: str
```

- `LiveTradeEvent` is the only authoritative data-delivery event.
- `LiveSessionEvent` covers heartbeat/ack/pong, disconnect and reconnect
  uniformly (closing open decision 22: yes, heartbeat/session-health
  evidence is surfaced, exactly as diagnostic/operational information, never
  as a completeness or continuity claim — matching `ADR-0040`'s own
  treatment of ack/heartbeat/pong). A `RECONNECTED` event with no
  accompanying `LiveGapEvent` means continuity was re-established inside the
  bounded recent-public-trades window (`ADR-0040` §"Consequences"); a
  `RECONNECTED` event immediately followed by a `LiveGapEvent` means it was
  not, and the interruption is explicit.
- `LiveGapEvent` is the explicit, descriptive-only signal required when the
  last durable key cannot be found. It is evidence, never a completeness
  claim, and it must never be synthesized into a zero-trade interval,
  interpolated row, or sentinel (closing open decisions 12, 14, 24 exactly
  as already auto-answered, now given a concrete carrier type).

No terminal/error event type is defined in v1: a fatal, non-recoverable
error propagates as a raised exception from the iterator, exactly as
`DataScan` already does for `DataIntegrityError`/`SchemaMismatch`; graceful
end-of-stream (if the caller bounds the request) ends iteration normally.

### 7. Single logical consumer per `live_stream()` call; no fan-out in v1

Closing open decision 20: `live_stream()` returns one `LiveStream` bound to
one logical caller. Multiple independent calls may each open their own
`LiveStream` (each pulling independently from durable canonical storage, at
no extra coordination cost — see decision 2), but `DataGateway` does not
provide shared-offset consumer-group semantics, named consumers, or
fan-out delivery guarantees in v1. A future multi-consumer coordination
capability, if ever needed, is out of scope for `ADR-0047` and would need
its own design gate.

### 8. Live-stream result identity extends `B03`'s model, not a new one

Closing open decision 10: a `LiveStream`'s stable identity is
`sha256(dataset_identity, ordering_policy, coverage_segment_id)`, following
the same canonical-fingerprint pattern `B03`/`DataScan` already use for
historical result identity. `coverage_segment_id` is the discriminator that
changes across an explicit gap (decision 13's later governed segment), so
two `LiveStream` identities differing only in `coverage_segment_id` are
provably evidence of a governed interruption, not a silent identity clash.
Physical locators (paths, catalog row ids) remain diagnostic-only, exactly
as `B03` already requires for historical reads.

## Consequences

- `B06`'s v1 contract is now specific enough for issue #194 to implement
  without further semantic decisions: given a cursor, `live_stream()`
  resumes strictly after it; duplication is prevented by construction
  (exclusive anchor + already-deduplicated upstream data), not by a new
  dedup pass; disconnect/reconnect/heartbeat surface uniformly as
  `LiveSessionEvent`; an unprovable gap surfaces as `LiveGapEvent` and never
  as fabricated continuity; and no new physical storage or catalog schema is
  required anywhere in this design.
- `D04` (issue #195) can consume `LiveTradeEvent`/`LiveGapEvent` directly: it
  must never seal a `CLOSED` candle across an interval a `LiveGapEvent`
  covers (closing questionnaire items 27-29), because a live gap is not
  proof of a zero-trade interval.
- No generic pub/sub framework, message broker, or new repair engine was
  introduced; `A10` remains the only repair capability, and issue #110's
  disposition is unchanged.
- `quant_platform.access`'s package boundary is unaffected: `LiveStreamCursorV1`
  and the three event types are plain value objects alongside
  `DataRequest`/`DataSlice` in `quant_platform.access.models`;
  `live_stream()` lives on `DataGateway` in `quant_platform.access.gateway`,
  the same owner as `scan()`. No new `OWNERS`/`ALLOWED` entry is needed.
