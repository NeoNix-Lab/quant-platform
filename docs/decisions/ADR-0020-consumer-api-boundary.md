# ADR-0020 — Consumer API semantic boundary

**Status:** PROPOSED — pending independent re-review

**Date:** 2026-08-28

## Context

ADR-0002 establishes API-first application architecture.
[ADR-0019](ADR-0019-datagateway-boundary.md) and the frozen
[DataGateway Contract v1](../contracts/DATA_GATEWAY.md) establish the logical
data access boundary below the quantitative layers. This decision sits above
that boundary and does not supersede, replace or reinterpret it.
The platform now needs one consumer-facing contract that can serve App UI,
TUI, CLI and future automation without turning the first Bybit `trade-v1`
read into the general market-data model or exposing physical data-plane
mechanics.

The roadmap still places representation, feature, research, validation,
strategy, execution and experiment foundations before API/job runtime. This
decision freezes the boundary principles and semantic query shape only; it
does not advance runtime implementation or reorder the roadmap.

## Decision

1. App UI, TUI, CLI and future external consumers use one canonical semantic
   API boundary. They do not invoke DataGateway, storage, domain engines or
   physical data sources directly.
2. Consumer market-data requests express a minimal semantic selector
   (`venue`, `instrument`), an explicit UTC `[start, end)` interval and a
   versioned `RepresentationDefinition`. Future selector dimensions require
   an explicit versioned extension.
3. `RepresentationDefinition`, consumer query/request identity and internal
   `DatasetIdentity` remain separate concepts. A service may resolve one to
   another internally, but DatasetIdentity is not the ordinary consumer query
   model.
4. The API is organized around semantic platform capability families and
   application services. It is not a public DataGateway read wrapper.
5. The `trades@1` request backed by canonical Bybit `trade-v1` is a reference
   vertical slice only. Representation materialization may later be computed,
   cached, materialized or reconstructed without changing the consumer
   boundary when semantics and provenance remain stable.
6. API results preserve representation/application-level coverage, temporal,
   deterministic-ordering and provenance semantics. Stable API errors preserve
   distinctions such as source-not-found, no-coverage, schema incompatibility,
   unsupported-representation and integrity failure without exposing raw
   implementation exceptions or physical paths. Detailed DataGateway causes
   remain internal diagnostics.
7. Cheap bounded reads may be synchronous. Long-running computation and
   materialization use a future asynchronous Job contract; this ADR does not
   implement that runtime.
8. A semantic change to a frozen API or representation contract requires an
   explicit new API or representation version. An ADR may document or
   authorize the evolution but cannot mutate frozen meaning in place. No legacy
   API compatibility is promised.

## Consequences

- All clients can share semantic request composition and receive the same
  domain guarantees.
- DataGateway and data-plane implementation choices remain replaceable behind
  application services.
- Future candle, footprint, book, feature, research, execution and experiment
  capabilities can be added without creating parallel client boundaries.
- Exact transport, serialization, pagination, streaming, snapshot and job
  runtime details remain open until their implementation milestones.
- The current `trade-v1` implementation is evidence for the first slice, not
  evidence that all market-data representations are available.

See [Consumer API Boundary Contract v1](../contracts/CONSUMER_API.md) for the
normative conceptual request, identity, error, coverage and client rules.
