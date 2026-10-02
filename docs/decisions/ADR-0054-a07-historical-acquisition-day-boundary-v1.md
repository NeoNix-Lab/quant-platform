# ADR-0054 - A07 historical acquisition day-boundary v1

**Status:** ACCEPTED
**Date:** 2026-10-02

## Context

Design gate issue #252 was raised from the Omega consumer path
(`NeoNix-Lab/omega#15`, U5). Omega needs multi-week historical datasets for
walk-forward and DSR/PBO validation, while A07 is currently credited only as a
narrow Bybit BTCUSDT first vertical over one UTC day. The working question was
whether A07 v1 should grow a native multi-day/date-range acquisition path, or
whether callers should explicitly orchestrate one day at a time.

The existing accepted authority is deliberately day-shaped:

- ADR-0023 and the Producer-Consumer Conformity contract prove the first
  Bybit BTCUSDT vertical on the `2024-01-15` UTC day.
- A04 declared coverage and S13/S14 publication work on explicit half-open
  coverage intervals and natural partitions.
- ADR-0033 repair works over explicit defects and candidates; it does not own
  acquisition, scheduling, source selection, or a generic self-healing loop.
- ADR-0044 keeps long gaps explicit unless an attributable repair source/path
  proves the missing support and integrates through A10 semantics.

Native date-range acquisition would therefore not be a harmless convenience
wrapper if it tried to claim one aggregate range as complete without preserving
the existing per-partition evidence and repair boundaries.

## Decision

**A07 v1 remains a one-UTC-day, Bybit BTCUSDT linear historical acquisition
profile.** Multi-day consumers must build date ranges by an explicit
caller-owned day-by-day loop, where each UTC day is acquired, canonicalized,
covered, certified, published and later read as its own evidenced partition.

This is the accepted v1 answer, not a claim that A07 can never widen. A future
native date-range acquisition path may be proposed only as a separate design
gate and implementation slice. That future slice must prove that it preserves
the already-frozen guarantees instead of bypassing them:

- one explicit half-open declared coverage interval per natural partition;
- S13 certification and S14 publication evidence per admitted partition;
- no silent aggregation of source failures across days;
- no weakening of ADR-0033's repair/cutover model;
- no claim that elapsed time, missing sequence evidence, or an empty provider
  response proves completeness without attributable source evidence.

The caller-owned loop may still offer a higher-level user experience: it can
enumerate days, invoke the A07 path repeatedly, collect per-day outcomes, and
surface a range-level summary. That orchestration is not A07's own semantic
contract unless and until a future ADR promotes it.

## Consequences

- `CAPABILITY_DAG.md` and `CAPABILITY_MAP.md` must describe A07 as the
  one-UTC-day BTCUSDT-linear v1 profile, with multi-day acquisition performed
  by explicit caller orchestration.
- Omega and other consumers needing multi-week datasets should loop
  day-by-day, then consume the resulting eligible canonical partitions through
  `DataGateway.scan()` / `DataGateway.read()`.
- This issue does not implement native date-range acquisition and does not add
  a scheduler or job runtime.
- ADR-0033 remains unchanged: repair stays explicit-defect-driven and does
  not become the source-selection or multi-day acquisition owner.
- A future widening of A07 should be tracked as a new issue and must include
  executable proof for the range orchestration semantics it claims.

## Related

Parent tracking: #232. Closes issue #252. Found via
`NeoNix-Lab/omega#15` (U5).
