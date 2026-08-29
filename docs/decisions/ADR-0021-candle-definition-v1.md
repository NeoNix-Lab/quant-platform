# ADR-0021 — CandleDefinition v1 semantic contract

**Status:** PROPOSED — pending independent review

**Date:** 2026-08-29

## Context

ADR-0005 makes candles first-class derived representations and ADR-0017
requires a reproducible `CandleDefinition` with mutable `PARTIAL` and
immutable `CLOSED` states. The platform now needs a precise contract before a
historical candle runtime is implemented. The definition must remain separate
from consumer selectors, canonical source dataset identity and physical
materialization details, while allowing historical and future incremental/live
construction to converge.

## Decision proposed

Adopt the normative candidate in
[CandleDefinition v1 Contract](../contracts/CANDLE_DEFINITION.md) for
independent review, with these central decisions:

1. A definition identity is a SHA-256 of canonical UTF-8 JSON containing only
   versioned semantic source, duration, alignment, boundary, aggregation,
   empty-bucket, lifecycle, late-event, availability, numerical and output
   schema parameters. Venue, instrument, query interval, dataset identity,
   catalog identifiers, paths and runtime identifiers are excluded.
2. V1 consumes deterministic `trades@1` / `trade-v1` events, uses positive
   integer nanosecond durations, UTC Unix-epoch alignment, and half-open
   buckets labelled by `bucket_start`.
3. OHLCV and trade count use deterministic source order and exact decimal
   arithmetic; binary float and implicit rounding are forbidden.
4. Fully covered zero-trade buckets are omitted, not filled or treated as
   coverage gaps.
5. Query intervals select full aligned buckets whose intervals intersect the
   query; required bucket support is tracked separately from the consumer
   interval and returned records. Review remediation changes selection from
   `bucket_start` filtering to bucket intersection so Consumer API coverage
   projects over every requested interval, including non-aligned intervals.
6. `PARTIAL` is mutable and unsealed. `CLOSED` requires source finalization,
   complete support and stable source revision/content evidence, and is
   immutable. Corrections require a revised source and rebuilt result, never
   silent in-place mutation.
7. Historical data without observed receive/finalization time does not receive
   a fabricated timestamp. The `causal_floor` for final closed values is
   `bucket_end`; `observed_available_at` is nullable/unknown unless evidenced
   and is never implied to equal the causal floor.
8. On-demand and materialized CLOSED results are equivalent only when their
   definition, source revision/evidence, support/coverage and canonical
   records are equivalent; physical placement is not semantic identity.

The proposed closed-record schema is `schemas/candle-v1.json`. State and
provenance remain in an envelope/materialization metadata, not in the row.
The canonical five-minute definition payload, serialization and hash are
protected by `fixtures/candle-definition-v1/golden-5m.json` and its focused
contract test.

## Consequences

- Two independent implementations have a deterministic target for the first
  historical candle slice.
- A later runtime can support incremental/live construction without weakening
  closed-result immutability or temporal availability rules.
- Empty-bucket and non-aligned-query behavior are explicit, including required
  source support beyond a non-aligned query end when a selected bucket needs it.
- Candle v1 remains only the first representation slice; it does not redefine
  the future Representation system.
- The existing DataGateway, frozen `trade-v1`, catalog and producer contracts
  remain unchanged.

## Review gate

This ADR must remain `PROPOSED` until an independent reviewer confirms the
contract and its semantic fixtures/schema. A separate implementation mandate
may begin only after that review; this ADR does not authorize candle runtime
code by itself.
