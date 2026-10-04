# ADR-0066 - Transport evolution v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

ADR-0050 freezes J02 v1 as one JSON request and one JSON response carrying the
already-frozen Consumer API exactly. Its Amendment 1 deliberately rejects a
market-data result above 50,000 rows with `RESULT_TOO_LARGE`; it does not add
pagination or chunking to that existing operation. The current server confirms
this shape: `handle_api_transport_message` decodes one request, executes one
Consumer query, and sends one response with the 16 MiB WebSocket ceiling.

ADR-0047 separately defines B06's in-process live pull iterator, its opaque
`LiveStreamCursorV1`, exclusive-anchor resume, and the three live event types.
It is not a Consumer-facing live API or a J02 wire protocol. No accepted
application service currently owns a live Consumer request/result semantic.

## Decision

### 1. J02 v1 remains lossless and unchanged

`j02-request-v1` and `j02-response-v1` remain a single complete exchange.
Their query, result, error vocabulary, `request_id`, request identity, and
`RESULT_TOO_LARGE` refusal are unchanged. J14 is additive: an implementation
must not silently switch a v1 request to frames, add a pagination cursor to a
v1 result, or use transport framing to avoid the application-level row bound.

WebSocket and JSON remain the only transport stack. A J14 envelope declares a
new `message_family` and a family-specific `schema_version`; an endpoint that
does not negotiate that exact family/version rejects it as unsupported instead
of treating it as a J02 v1 message. New families and optional fields are
additive only; a change to a required field or to the meaning of an accepted
family/version requires a new family version.

### 2. Finite large-result framing is byte transport, not pagination

`j14-framed-result-v1` may carry only a complete, already-produced finite
application result whose owning application contract declares a stable logical
result identity. It serializes the complete application result with RFC 8785
JSON Canonicalization Scheme and encodes that serialization as UTF-8 bytes.
`payload_sha256` is the lowercase hexadecimal SHA-256 digest of exactly those
canonical result bytes. It then defines `transfer_id` as the lowercase
hexadecimal SHA-256 digest of the UTF-8 RFC 8785 serialization of this named
preimage object:

```json
{
  "logical_result_identity": "<existing application identity>",
  "message_family": "j14-framed-result-v1",
  "payload_sha256": "<lowercase 64-hex digest>",
  "schema_version": "j14-framed-result-v1"
}
```

The server splits those exact canonical result bytes at deterministic offsets no larger than
the configured frame payload bound (which remains below the negotiated
WebSocket message bound). Every frame carries `transfer_id`, the logical result
identity, `payload_sha256`, zero-based `chunk_index`, immutable `chunk_count`,
and the chunk's lowercase hexadecimal SHA-256. A receiver accepts a result only after it has every
unique index, verifies every chunk and the reassembled payload digest, then
parses the complete application result. It must not expose a partial frame set
as a partial page or interpret individual chunks as domain records.

This framing has no application page size, page cursor, or next-result
semantics. An application-level pagination cursor may exist only when a future
application capability defines its own semantic cursor, identity, coverage, and
ordering rules. J14 neither creates nor translates that cursor.

### 3. Resumption is transfer-local and integrity checked

Where an implementation retains a completed finite payload, a client resumes
only by presenting its `transfer_id` and the next verified `chunk_index`. The
server returns the exact remaining frames for that immutable transfer; it never
re-executes a request under the same transfer id or substitutes newer data.
Retention duration is operational and must be declared by the implementation.
After retention expiry, the server returns a typed transport `transfer_expired`
outcome and the client must issue a new semantic application request. A digest,
index, count, or identity mismatch is a typed transport `integrity_failure` and
the incomplete transfer is unusable.

These transport outcomes do not replace or reinterpret a Consumer API error.
They are attributable to `transfer_id` and never contain paths, catalog UUIDs,
or storage locators. A J14 implementation must bound retained payload bytes and
in-flight transfers; it may refuse admission before execution rather than retain
unbounded state.

### 4. Future live families carry an application event unchanged

No J14 live operation is defined now. A future application owner may introduce
a new `j14-live-<family>-vN` message family only after it has accepted the
corresponding Consumer request, event/result, error, ordering, and resume
semantics. Its family header may add connection-local stream metadata, but the
event payload remains a lossless serialization of the application-owned event;
transport may not infer continuity, deduplicate records, close a gap, or invent
a cursor.

If that future owner exposes ADR-0047 B06 events, it must carry
`LiveStreamCursorV1`, `LiveTradeEvent`, `LiveSessionEvent`, and `LiveGapEvent`
without changing their identity or exclusive-anchor resume rule. A J14 transfer
cursor is never a `LiveStreamCursorV1`, and a B06 cursor is never a finite-frame
resume token. Unknown live families or versions are rejected, not coerced into
generic pub/sub events.

## Consequences

- A later finite-result implementation has exact frame identity, ordering,
  digest, resumability, expiry, and bounded-retention rules without changing
  J02 v1 or pretending frames are pages.
- A later live implementation must first establish its application semantics;
  B06 remains the only authority for the existing cursor/gap vocabulary.
- J14 adds no REST stack, broker, binary protocol, application pagination, or
  second semantic API.

## Out of scope

- J14 implementation, J02 v1 migration, changes to the 50,000-row refusal,
  client work, storage/cache implementation, or J03 runtime;
- a live Consumer API, B06/D04 redesign, application-level pagination, or a
  generic event/RPC framework.

## Acceptance evidence

ADR-0050 and `api_transport_server.py` establish current one-message J02
fidelity and the bounded refusal. ADR-0047 establishes the live cursor and
event authority that J14 must carry without reinterpretation. The test below
guards the additive boundary and the explicit separation of frames, pages, and
live cursors; it is not an implementation or end-to-end transport proof.

## Related

Parent tracking: #281. Closes issue #286.
