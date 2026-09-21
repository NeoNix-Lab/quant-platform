# ADR-0042 — Live-ingest checkpoint / recovery v1

**Status:** ACCEPTED
**Date:** 2026-09-21

## Context

K10 follows A11 because checkpoint/recovery must bind the real durable state the live-ingest runtime actually creates. K03 observability and K08 restore evidence are already prerequisites in the capability DAG. ADR-0040 freezes the A11 continuity/deduplication semantics that K10 must preserve.

The provider does not expose an arbitrary durable replay cursor for public trades, so K10 must not equate a transport message/frame position with canonical recovery authority.

## Decision

### 1. Checkpoint meaning

A K10 checkpoint represents the **last canonical progress point already durably published**, not the last WebSocket message merely observed or parsed.

Its implementation-local shape must bind enough evidence to reject cross-dataset/cross-semantics misuse, including where applicable:

- canonical `DatasetIdentity`;
- source/live-semantics identity/version;
- last durable canonical `TradeKeyV1`;
- last observed source `seq` as diagnostic/source evidence, not as a gap-free cursor;
- durable publication/catalog identity or generation the checkpoint relies on;
- current governed coverage/live-segment identity;
- monotonic checkpoint generation/version.

Exact field/class/file names remain local until A11 runtime state exists.

### 2. Publication-before-checkpoint invariant

The authoritative ordering is:

```text
canonical publication becomes durable
        BEFORE
checkpoint may advance past that publication
```

A state in which the checkpoint claims progress beyond durable canonical publication is forbidden.

### 3. Crash states

The v1 recovery model intentionally permits replay and deduplication:

- crash before canonicalization/publication -> old checkpoint remains; replay is expected;
- crash after canonicalization but before durable publication -> old checkpoint remains; replay is expected;
- crash after durable publication but before checkpoint advance -> old checkpoint remains; replay may include already-published trades and must be removed by ADR-0040 idempotent duplicate semantics;
- crash after checkpoint advance -> restart resumes from that durable progress point.

This yields at-least-once recovery with idempotent canonical effect, not transport-level exactly-once delivery.

### 4. Monotonicity and binding

A valid checkpoint update cannot:

- regress the durable canonical trade key/progress;
- reference an older/incompatible publication generation silently;
- change `DatasetIdentity` or source/live-semantic identity in place;
- bypass an unresolved coverage interruption.

A changed identity/semantic domain starts a distinct recovery domain rather than silently reusing an old checkpoint.

### 5. Restart procedure

A restart must:

1. load and validate the durable checkpoint against the canonical publication/catalog state it binds;
2. reconnect and establish a valid Bybit live session/subscription;
3. buffer new WebSocket evidence while attempting the bounded recent-public-trades reconciliation authorized by ADR-0040;
4. canonicalize/order/deduplicate the recoverable overlap;
5. resume governed live publication only after continuity is proven or an explicit non-complete gap boundary has been recorded.

If the last durable key is outside the provider's bounded reconciliation window, K10 must not guess a resume position or fabricate completeness. The unresolved interval follows ADR-0040's explicit-gap semantics and the separate open long-gap remediation proposition.

### 6. Invalid or missing checkpoint

A malformed, contradictory or identity-mismatched checkpoint fails closed. The daemon must not infer a checkpoint from wall-clock time or provider sequence by guess.

A separately governed recovery tool may reconstruct checkpoint state from already-durable canonical publication only when the derivation is deterministic and proven; such reconstruction is not an implicit daemon fallback.

### 7. Minimum proof matrix

K10 implementation evidence must cover at least:

- failure before canonicalization -> replay;
- failure before durable publication -> replay;
- failure after durable publication but before checkpoint advance -> replay + dedup, no second canonical trade;
- failure after checkpoint -> monotonic resume;
- bounded reconnect where last durable key is still recoverable -> continuity re-established;
- reconnect where the anchor is no longer recoverable -> explicit non-complete gap, never fake complete;
- duplicate REST/WS observation -> one canonical event;
- same canonical key with conflicting payload -> fail closed;
- invalid/corrupt checkpoint -> refusal/explicit recovery state.

## Excluded

This ADR does not define or authorize:

- arbitrary-duration provider replay;
- B06 live-consumer cursor semantics;
- generic checkpoint/job frameworks;
- HA/distributed consensus;
- K09 deletion;
- a mechanism for filling long live gaps when current provider evidence cannot prove them.

## Consequences

- K10 semantic authority is frozen before implementation without inventing implementation-local state shape that only A11 can reveal.
- K10 can be implemented after A11 exists, using the minimum checkpoint state required to enforce these invariants.
- The first vertical promises no silent loss/duplication and explicit gap state; it does not promise zero data loss after an arbitrarily long provider/network outage unsupported by replay evidence.
