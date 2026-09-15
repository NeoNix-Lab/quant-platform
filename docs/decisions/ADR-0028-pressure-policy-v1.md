# ADR-0028 - PressurePolicyDefinition v1

**Status:** ACCEPTED

**Date:** 2026-09-15

## Context

K04 established observational capacity measurement under
`quant_platform.operations`: a caller-supplied storage root yields either one
complete immutable `CapacityObservation` or explicit `CapacityUnavailable`.
K04 deliberately performs no pressure classification, polling, remediation or
storage mutation.

K05 resolves the first operational pressure-policy slice after K04. Live
acquisition, source protection and relocation need a deterministic pressure
seam, but pressure must remain policy over explicit evidence. It must not
become a telemetry collector, scheduler, storage mover, backup system or
deletion authority.

Credited compatibility evidence:

- `src/quant_platform/operations/capacity.py` provides K04 capacity facts and
  unavailable results without pressure state or mutation.
- `tests/test_operations_capacity_v1.py` proves K04 does not invent partial or
  sentinel capacity values and does not assume `total == used + available`.
- `db/init/001_catalog.sql::storage_roots` owns storage-root identity and tier
  topology, not pressure thresholds.
- `docs/architecture/STORAGE_LIFECYCLE.md` already forbids pressure from
  silently deleting unique RAW/source data.

## Decision

Adopt `PressurePolicyDefinition v1` as the canonical K05 pressure-policy
contract.

K05 is deterministic policy evaluation, not observation. It consumes:

- one immutable/versioned `PressurePolicyDefinition`;
- one explicit UTC-aware evaluation instant `as_of`;
- one fresh successful K04 `CapacityObservation`;
- optional caller-supplied write-rate evidence when the active policy requires
  time-to-full participation.

It returns either one `PressureDecision` or one explicit
`PressureDecisionUnavailable`. It does not discover storage roots, poll
filesystems, collect telemetry histories, move data, delete data, pause
services directly, schedule work or perform remediation.

### Policy definition and identity

The v1 policy contains:

- semantic version `1`;
- maximum accepted capacity-observation age;
- `pressure_available_bytes`, `critical_available_bytes` and
  `exhausted_available_bytes`;
- optional time-to-full participation;
- when enabled, maximum accepted rate-observation age plus
  `pressure_time_to_full` and `critical_time_to_full`.

Byte thresholds must satisfy:

```text
0 <= exhausted_available_bytes
   <= critical_available_bytes
   <= pressure_available_bytes
```

When time-to-full participates:

```text
0 < critical_time_to_full <= pressure_time_to_full
```

Freshness durations are non-negative. Malformed, missing or non-monotonic
policy definitions are refused explicitly.

Policy identity is a deterministic SHA-256 digest over canonical JSON
containing the semantic version, threshold, freshness and rate-participation
payload. The identity excludes host/process/runtime IDs, physical mount paths,
current observation values and monitoring/exporter implementation details.

### Evaluation instant and freshness

Every evaluation receives one explicit UTC-aware `as_of`. The evaluator does
not read a host clock.

For evidence observed at `observed_at`:

```text
age = as_of - observed_at
```

Evidence is fresh when `age <= configured_max_age`; equality is accepted.
Evidence is stale when `age > configured_max_age`. Future-dated evidence is
unavailable. Invalid or missing required evidence never yields `NORMAL`.

The `as_of` instant is part of successful and unavailable decision evidence and
decision identity. Re-evaluating the same observations at a later `as_of` may
therefore change the result.

### Capacity and write-rate evidence

A fresh successful K04 `CapacityObservation` is mandatory. K05 uses
OS-reported `available_bytes` directly and does not recompute capacity facts or
impose `total == used + available`.

When time-to-full is disabled, capacity-only classification is complete and no
rate evidence is required.

When time-to-full is enabled and the root is not already byte-exhausted, the
caller must supply one immutable write-rate observation with:

- finite `bytes_per_second >= 0`;
- explicit measurement window;
- UTC observation time;
- stable caller evidence identity, plus K05 canonical evidence identity.

Missing, stale, future-dated, malformed, non-finite or negative required rate
evidence yields `PressureDecisionUnavailable`. K05 does not silently fall back
to capacity-only `NORMAL`.

For a strictly positive rate:

```text
time_to_full = available_bytes / bytes_per_second
```

For an evidenced zero rate, time-to-full is unbounded. It is not represented by
a finite sentinel and it does not satisfy any time-based threshold.

`EXHAUSTED` byte evidence is decided before forecasting. An already exhausted
root does not require division or a finite time-to-full estimate.

### States and restrictions

Canonical state order:

```text
NORMAL < PRESSURE < CRITICAL < EXHAUSTED
```

Classification evaluates the most severe state first:

1. `EXHAUSTED` iff `available_bytes <= exhausted_available_bytes`;
2. otherwise `CRITICAL` iff `available_bytes <= critical_available_bytes` or
   finite `time_to_full <= critical_time_to_full`;
3. otherwise `PRESSURE` iff `available_bytes <= pressure_available_bytes` or
   finite `time_to_full <= pressure_time_to_full`;
4. otherwise `NORMAL`.

Equality enters the more severe state. If byte and time predicates disagree,
the more severe state wins. V1 is stateless and defines no hysteresis/history
state machine.

K05 restrictions are upper bounds only:

- `NORMAL`: no pressure-specific restriction is added to otherwise authorized
  non-destructive work or writes;
- `PRESSURE`: optional, bulk or recomputable work should be refused or
  deferred;
- `CRITICAL`: optional, bulk or recomputable writes are refused; safety
  relevant writes require their owning capability to independently prove the
  bounded write remains safe;
- `EXHAUSTED`: new data-producing writes are refused; diagnostics and
  independently authorized recovery/protection actions may proceed only when
  they do not rely on unavailable write capacity.

For every successful K05 state:

```text
delete_authorized = false
```

K05 never authorizes automatic deletion, retention enforcement, cache eviction
implementation or source removal. Future K09 owns deletion authority after its
own prerequisites.

## Runtime materialization

The accepted runtime foundation is implemented under
`quant_platform.operations` as:

- immutable `PressurePolicyDefinition`;
- canonical `PressureState` values;
- immutable caller-supplied `WriteRateObservation`;
- explicit `PressureDecision` and `PressureDecisionUnavailable`;
- pure `evaluate_pressure(policy, capacity, as_of, rate=None)`;
- deterministic successful and unavailable decision identities;
- typed pressure restrictions with `delete_authorized == false`.

The implementation intentionally introduces no watcher, daemon, scheduler,
rate sampler/history store, exporter, telemetry stack, policy engine, storage
remediation executor, relocation, backup, protection or deletion runtime.

## Consequences

- K04 remains observational and complete.
- K05 is frozen and complete for the bounded v1 runtime foundation.
- A11 can depend on a deterministic pressure seam after K06/K08 and other live
  prerequisites are satisfied.
- K06 source protection and K07 relocation can consume pressure decisions as
  restrictions, but they retain ownership of protection and relocation actions.
- A11 live acquisition remains blocked by DG-B live semantics and K06/K08.
- K06 remains the next DG-H source-protection decision/runtime branch; K05 does
  not define reconstruction guarantees.
- K09 remains the future deletion-authority gate.

## Acceptance evidence

Executable proof is in `tests/test_operations_pressure_v1.py`.

The proof covers deterministic policy identity, malformed policy refusal,
byte-threshold equality and precedence, explicit `as_of` freshness boundaries,
future-dated evidence, repeated deterministic evaluation, capacity unavailable
and malformed-capacity refusal, finite/unbounded time-to-full semantics,
zero-rate behavior, required rate-evidence unavailability, byte/time severity
disagreement, exhausted-byte short-circuiting without a forecast, deterministic
decision identities and `delete_authorized == false`.

Existing regression evidence remains in `tests/test_operations_capacity_v1.py`
and `tests/test_package_boundaries_v1.py`.
