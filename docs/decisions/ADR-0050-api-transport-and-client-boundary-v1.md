# ADR-0050 — API transport and client boundary v1 (J02)

**Status:** ACCEPTED
**Date:** 2026-09-30

## Context

`SCOPE.md` ("Wave 7 — API & Platform Transport v1") Active Path step 1 requires
a design gate that resolves `J02`'s still-open transport/serialization/
runtime-host/package-boundary decisions before any implementation slice may
build a remote transport or client (issue #217, blocking issues #218-#221).

`C02`/`C03` (Consumer API semantic selector resolution and result/error
translation) are frozen and implemented. `quant_platform.application.market_data`
already owns the complete Consumer API surface: `ConsumerMarketDataQuery`,
`NormalizedMarketDataQuery`, `ConsumerMarketDataResult` (with nested
`ConsumerCoverage`/`ConsumerProvenance`), `ConsumerApiError` and the six frozen
`ConsumerErrorCode` values, via `execute_market_data_query`/
`resolve_market_data_request`.

Issue #217's own decision questionnaire (posted to the issue as a comment)
separates what existing authority already answers ("Autorisolto") from what
this ADR must still freeze ("Da decidere"). This ADR closes every item the
questionnaire marks "Da decidere," plus the two items marked "Autorisolto
parziale"; it does not revisit the fully auto-answered items, and it does not
reopen `J03`, `J07`/`J08`, or any capability `SCOPE.md`'s Out of Scope section
already excludes.

**One correction to `SCOPE.md`'s own framing.** `SCOPE.md`'s Objective
describes J02 needing a "pagination/streaming shape for bounded and unbounded
(live) Consumer API results." On inspection, `ConsumerMarketDataResult.data`
is `tuple[TradeRecord, ...]` — a fully-materialized, finite result. No
live-facing Consumer API exists anywhere in `quant_platform.application`
today; `B06`'s live stream (`DataGateway.live_stream()`) has never been
wrapped into a `C02`/`C03`-equivalent consumer-facing capability. Inventing
live wire semantics now would mean deciding new Application-service semantics
under cover of a transport ADR — exactly what `SCOPE.md`'s own Authority
section forbids ("`J02` carries [the Consumer API], it does not redesign
it"). This ADR therefore scopes J02 v1 to the bounded shape that actually
exists and explicitly defers live wire semantics (see Decision 4).

## Decision

### 1. Protocol: WebSocket, single unified transport

J02 v1 uses **WebSocket** as its only wire protocol, via the `websockets`
library already a pinned dependency (`pyproject.toml`) and already used in
this repository for `A11`'s Bybit live client (`application/bybit_live.py`).
This is the first use of `websockets.serve` (server side) in the repository;
the dependency itself is not new.

This closes questionnaire items 9-10 (single protocol, not a REST/WebSocket
pair): a REST framework (Flask/FastAPI/Starlette/etc.) is not a current
dependency and would be new tooling for a capability `websockets` already
covers. A single connection-oriented protocol also means the wire shape never
needs to change when a future live-facing Consumer API eventually exists
(decision 4) — it is additive new message types on the same protocol, not a
second transport bolted on later.

Each request is one JSON message; each response is one JSON message on the
same connection. The connection may carry multiple sequential request/response
exchanges (v1 does not require reconnecting per request), but v1 makes no
concurrency claim beyond one in-flight request per connection at a time.

### 2. Serialization: JSON, reusing the existing canonical-encoding discipline

Wire payloads are JSON, encoded with the **same canonical rules already used
everywhere in this repository for `stable_dict()`/content-fingerprint
payloads**: `Decimal`-precision values (`price`, `size`, coverage bounds) are
strings, never floats; `Instant` values are ISO-8601 UTC strings via
`.isoformat()`; field order is not semantically significant (receivers must
not rely on it). This is not a new encoding convention — it is the one
`TradeRecord`, `CandleRecord`, `LiveStreamCursorV1` and every other
`stable_dict()` in this codebase already uses, extended to the wire.

This closes questionnaire item 11: JSON, not protobuf/Arrow IPC. Introducing
a binary schema-driven format would require a new code-generation toolchain
this repository has never needed; JSON with the existing canonical-string
discipline already provably preserves exact decimal and temporal precision
(every existing content-hash fingerprint in this repository already depends
on that fact).

### 3. Wire envelope: carries the Consumer API exactly, adds transport metadata only

Request message:

```json
{
  "schema_version": "j02-request-v1",
  "request_id": "<caller-supplied opaque string, echoed back>",
  "query": {
    "venue": "...", "instrument": "...",
    "start": "<ISO-8601>", "end": "<ISO-8601>",
    "representation": {"kind": "...", "version": 1, "definition": {}},
    "options": {}
  }
}
```

`query` maps field-for-field onto `ConsumerMarketDataQuery`. No field renames,
no additions beyond what `ConsumerMarketDataQuery` already carries.

Success response:

```json
{
  "schema_version": "j02-response-v1",
  "request_id": "<echoed>",
  "status": "ok",
  "result": {
    "request": {"...": "NormalizedMarketDataQuery.stable_dict()"},
    "request_identity": "...",
    "representation": {"kind": "...", "version": 1},
    "data": [ "...TradeRecord fields..." ],
    "requested_interval": {"start": "...", "end": "..."},
    "returned_temporal_bounds": {"first": "...", "last": "..."},
    "coverage": {"covered_intervals": [], "gaps": [], "complete": true},
    "provenance": {"...": "ConsumerProvenance fields"},
    "row_count": 0
  }
}
```

Error response:

```json
{
  "schema_version": "j02-response-v1",
  "request_id": "<echoed>",
  "status": "error",
  "error": {
    "code": "<one of the six frozen ConsumerErrorCode values>",
    "message": "...",
    "context": {},
    "request_identity": "..."
  }
}
```

`status`/`schema_version`/`request_id` are the only transport-owned fields.
Every other field is a direct, lossless carry of `ConsumerMarketDataResult`
or `ConsumerApiError`'s own fields — J02 must not add, drop, rename or
reinterpret any of them. This closes questionnaire items 5-8: field-level
semantic fidelity, not best-effort; the six `ConsumerErrorCode` values are
carried verbatim, never mapped onto transport-specific status codes as the
canonical signal (a WebSocket close code or similar may exist as operational
diagnostics but is never authoritative).

### 4. Pagination and live streaming: none needed for v1; explicitly deferred

Closing items 13 and 18-21: `execute_market_data_query` already returns one
fully-materialized `ConsumerMarketDataResult`. J02 v1 therefore needs no
resumable pagination cursor and no live/incremental event framing — there is
no existing bounded-but-paginated or live-capable Consumer API method to
carry one for. A single request yields exactly one response message.

If a response's JSON-encoded `data` array is large enough to warrant wire
chunking for practical message-size reasons, that is transport-level framing
internal to one logical response (multiple WebSocket frames reassembled
before JSON parsing) — it is not an application-visible pagination cursor and
must not be confused with one.

Live wire semantics (streaming `B06`/`D04` results over J02) are explicitly
**out of this ADR and this wave**. They require a Consumer-facing live query
capability equivalent to `C02`/`C03` to exist first — a new Application
service decision, not a transport decision — and are deferred to a future
scope that would extend `quant_platform.application.market_data` (or a
sibling module) before any wire shape for it is designed.

### 5. Runtime host: an `application`-composed process, not a new owner

Closing items 14, 15, 16, 17 and 22: J02's server runs as a bounded,
long-running process launched via `websockets.serve`, composed inside
**`quant_platform.application`** (a new submodule; exact name at the
implementer's discretion, e.g. `application.api_transport_server`) — **not**
a new top-level package/owner. Every other runtime composition in this
repository (`live_ingest_server`, `live_gap_orchestration`, `golden_replay`,
`wave6_golden`) already lives inside `application` for exactly this reason:
composing already-owned capabilities into a running process is `application`'s
job by definition (`ADR-0024`), and J02's server-side message dispatch is
another instance of that, not a new architectural layer. This corrects
`SCOPE.md`'s own speculative framing ("likely `quant_platform.transport`") —
on inspection, minting a new owner is unnecessary.

The server calls `execute_market_data_query` directly; it never opens
`DataGateway`, the catalog, or physical storage itself. It requires no daemon
supervision, systemd unit, or scheduler beyond what `tools/live_ingest_server.py`
already demonstrates is sufficient for a comparable bounded long-running
process — it must not grow into `J03` or a generic job runtime. A thin
`tools/api_server.py` CLI entrypoint (importing only from
`quant_platform.application`, per the existing executable-orchestration seam)
starts it, mirroring `tools/live_ingest_server.py` exactly.

The minimal test/proof entrypoint (item 17) is: start the server bound to an
ephemeral local port, issue one request over a real WebSocket client
connection, and compare the decoded response against a direct in-process
`execute_market_data_query` call on the same input (decision 8).

### 6. Package boundary: no new owner; existing rules extended

Closing item 23: since J02's composition lives inside `application`, no new
`OWNERS`/`ALLOWED` entry is needed beyond registering the new submodule with
owner `application` (already-allowed to reach every domain package it needs).
`application`'s existing `ALLOWED` set already covers this; no edge changes.

### 7. Client boundary: J04 stays in-process; J05/J06 are outside `src/quant_platform` entirely

Closing items 24-30:

- **J04 (CLI)** uses the DAG's permitted bounded in-process `C03` alternative,
  not J02. It is a `tools/`-style script (`tools/cli.py` or similar) importing
  only `quant_platform.application`, following the exact same
  executable-orchestration discipline `tests/test_package_boundaries_v1.py`
  already enforces for every other script under `tools/`. This is simpler
  than requiring J02 to be running just to exercise the most basic client,
  and matches this repository's preference for the smallest sufficient
  mechanism.
- **J05 (TUI)** and **J06 (App UI)** are remote clients: they talk to J02 only
  over the WebSocket wire protocol defined above and have no legitimate reason
  to import `quant_platform` at all. They live in a **new top-level directory
  outside `src/`** (e.g. `clients/tui/`, `clients/app_ui/`), entirely outside
  `ADR-0024`'s modular-monolith boundary and outside
  `tests/test_package_boundaries_v1.py`'s current purview (they are not part
  of the `quant_platform` package). The implementer should add a small,
  focused test asserting neither client directory imports `quant_platform` —
  a "reverse" boundary check mirroring the discipline this repository already
  applies everywhere else, not a new enforcement philosophy.
- No client (`J04`/`J05`/`J06`) may contain quantitative business logic,
  direct storage access, or a competing selector/query-construction path —
  each must construct a `ConsumerMarketDataQuery`-shaped request (in-process
  for J04, wire JSON for J05/J06) and render whatever `ConsumerMarketDataResult`
  or `ConsumerApiError` comes back. This is `CAPABILITY_DAG.md`'s own
  `RESOLVED` disposition for J04/J05/J06, restated as a structural constraint,
  not narrowed or widened here.

### 8. Verification and acceptance

Closing items 31-35:

- **Semantic fidelity test**: run one representative `ConsumerMarketDataQuery`
  both directly (`execute_market_data_query`) and through a real J02
  round-trip (real WebSocket connection to a locally-started server instance);
  assert the decoded wire result's canonical fields equal the direct result's
  `stable_dict()`-equivalent fields exactly. Repeat for at least one of each
  frozen `ConsumerErrorCode` to prove error fidelity, not just the success
  path.
- **Package boundary test**: extend `tests/test_package_boundaries_v1.py`
  with the new `application` submodule (owner `application`, no `ALLOWED`
  change) and, separately, the new reverse check that `clients/tui/` and
  `clients/app_ui/` never import `quant_platform`.
- **Golden proof** (issue #222): a full client-to-service round-trip (at
  least one of J04/J05/J06) against real canonical Bybit BTCUSDT evidence,
  proving the same semantic-fidelity property end-to-end on real data, not
  just a hermetic fixture.
- Markdown/link checks and the repository's standard verification gate
  (`compileall`, `git diff --check`, package boundary tests) must pass.

## Consequences

- `J02`'s v1 contract is now specific enough for issue #218 to implement
  without further semantic decisions: protocol, serialization, wire envelope,
  runtime host, and package placement are all fixed.
- `J04`/`J05`/`J06` (issues #219-#221) can proceed independently: J04 needs
  nothing from J02 and can start immediately; J05/J06 need only J02's wire
  contract, not its implementation, to begin client-side work in parallel
  once the contract above is accepted.
- No new top-level package owner is introduced; `application`'s existing
  package-boundary allowance is unchanged. `J05`/`J06` living outside `src/`
  means they never need a domain-package dependency in the first place —
  structurally, not just by convention.
- Live wire semantics for `B06`/`D04` remain undecided and are explicitly out
  of this wave; a future scope must first decide the live-facing Consumer API
  semantics (an Application-service decision) before any wire shape for it
  can be designed.
- `C02`/`C03`'s frozen Consumer API semantics are unweakened: J02 is a
  lossless carrier, proven by the semantic-fidelity test, not a reinterpretation.
- `J03`, `J07`, `J08`, RL, broker/live execution, second venue, and L1/L2/L3
  remain untouched by this ADR, matching `SCOPE.md`'s Out of Scope section.
