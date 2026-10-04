# ADR-0058 — Replay sweep orchestration boundary v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

`HistoricalReplayRuntime.run()` is deterministic for one complete
`ReplaySpec`, but it owns no sweep/worker lifecycle and returns a
`ReplayResult`, not an Experiment `RunIdentity`, metric projection, or durable
attempt record.  I02/I03 already provide race-safe PostgreSQL state
transitions and full-population accounting, but they do not define which
replay result metrics constitute a trial attempt.  `ReplaySpec` also binds no
Decimal-context policy, while Decimal arithmetic may be process-local.

Adding workers directly would therefore require guessing three semantic
contracts: replay-result-to-metric projection, a process-safe data/runtime
factory, and worker ownership of experiment persistence.

## Decision

1. Parallelism may range only over independent, complete replay attempts. A
   stateful replay is never partitioned by day, time window, or temporal shard:
   positions, cooldowns, sessions, ledger state, fills, and outcomes remain
   one `HistoricalReplayRuntime.run()` invocation.
2. This issue introduces no executor yet. A future sweep request must bind,
   per member, a `ReplaySpec`, a `RunIdentity`, a declared metric projection,
   a serializable runtime/data factory, and a Decimal-context policy. None is
   inferable from the current public runtime objects.
3. Workers may return immutable success/failure envelopes only. A single
   coordinator registers every planned member before dispatch and records each
   terminal result through I02/I03 in stable `RunIdentity` order. Failed or
   aborted members remain `FAILED` with provenance; they may not disappear
   from the comparable population or lower an effective trial count by
   omission.
4. A process worker must initialize the declared Decimal context before it
   constructs its runtime. Process workers are preferred only after the
   factory/context contract is executable; thread workers are not authorized
   as an implicit alternative.
5. Future acceptance must prove one-worker and N-worker runs return identical
   per-spec result identities and registry ordering, plus explicit failed and
   aborted attempt records. It must also prove that no temporal-split option
   exists.

## Consequences

- Existing replay semantics and experiment accounting remain unchanged.
- The safe unit of future parallelism is an entire independent attempt, not a
  time slice.
- The implementation is deliberately deferred until its missing identity,
  metric, factory, persistence, and Decimal-context inputs are defined.

## Out of scope

- faster replay internals, summary mode, D05 representation input, and a
  second execution/fill/ledger implementation;
- a scheduler, job service, thread-pool fallback, or executor code.

## Acceptance evidence

Existing `tests/test_replay_v1.py` proves repeatability of a complete replay;
`tests/test_experiment_accounting_v1.py` proves failed attempts stay visible in
closed-population comparison; and `tests/test_experiment_persistence_v1.py`
proves durable run-state transitions.  They do not prove cross-process sweep
equivalence, which remains the required evidence for the follow-up slice.

## Related

Parent tracking: #232. Closes issue #237.
