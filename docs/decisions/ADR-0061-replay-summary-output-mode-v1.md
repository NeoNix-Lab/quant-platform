# ADR-0061 — Replay summary output mode v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Decision

Replay output retention is a separate `ReplayOutputConfig`, not a field of
`ReplaySpec`. `FULL_TRACE` remains the default and retains the existing per-tick
decisions, admissions, equity snapshots, orders, and fills. `SUMMARY` retains
no such output collections, while the unchanged runtime still produces the
same final ledger and a deterministic `ReplaySummary` containing aggregate
counts and a digest of the bounded event evidence.

The output setting is deliberately excluded from `ReplaySpec.identity`: it
does not change source input, strategy, decision clock, execution, fills, or
accounting. The summary digest binds the replay spec, ordered emitted evidence,
final ledger, and completed data metadata, allowing a summary run to be
compared with a full-trace run without making verbosity a strategy/replay
semantic input.

## Consequences

- Long replay callers can avoid retaining per-tick trace, order, and fill
  output objects in the result.
- Full-trace behavior and result identity stay compatible by default.
- The ledger remains the existing accounting authority; this decision does not
  redesign its transaction retention model.

## Evidence

The replay test executes one fixed run in both modes, proves equivalent final
ledger values and equal deterministic digests, repeats summary mode for digest
stability, and proves that all detailed result collections are empty in
summary mode.

## Out of scope

Changing replay semantics, decision timing, ledger internals, bar/D05 replay,
durable evidence storage, or a second replay implementation.

## Related

Parent tracking: #232. Closes issue #234.
