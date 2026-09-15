# Scope: K05 PressurePolicyDefinition v1 Operations Foundation

## Objective

Materialize and implement `K05 - Health/pressure policy` so the repository has
accepted canonical authority and a bounded executable
`PressurePolicyDefinition v1` foundation under `quant_platform.operations`.

This slice owns only Operations-level pressure policy meaning: immutable policy
identity, explicit evaluation instant, capacity/rate freshness, exact pressure
state classification, unavailable decisions, deterministic decision evidence
and restriction-only action semantics.

## Baseline

```text
base main = 32e750fad6276ecd81f6870c1b41d4738176abc8
branch    = agent/issue-53-10
atom      = K05
owner     = Operations
```

At work start, local `origin/main` was verified as exactly
`32e750fad6276ecd81f6870c1b41d4738176abc8`.

## Accepted state after this slice

```text
K01  Provisioning/fixtures/CI       RESOLVED / COMPLETE
K02  Server access/runtime identity OPEN_BLOCKING / MISSING
K03  Observability                  OPEN_BLOCKING / MISSING
K04  Capacity observation           RESOLVED / COMPLETE
K05  Health/pressure policy         FROZEN / COMPLETE
K06  RAW/source protection          OPEN_BLOCKING / MISSING
K07  Tier relocation                OPEN_BLOCKING / MISSING
K08  Backup/restore proof           OPEN_BLOCKING / MISSING
K09  Retention/deletion authority   OPEN_BLOCKING / MISSING
K10  Checkpoint/recovery            OPEN_BLOCKING / MISSING
```

K04 is reconciled to complete based on the existing
`src/quant_platform/operations/capacity.py` runtime and
`tests/test_operations_capacity_v1.py` proof.

K05 is frozen by ADR-0028 and implemented as immutable typed values and a pure
deterministic evaluator in `src/quant_platform/operations/pressure.py`.

## Included

- Create accepted ADR-0028 for PressurePolicyDefinition v1.
- Update directly affected governance so K04/K05 are no longer stale or
  unresolved.
- Add immutable `PressurePolicyDefinition` with deterministic semantic payload
  and content-derived identity.
- Add canonical `NORMAL`, `PRESSURE`, `CRITICAL` and `EXHAUSTED` states.
- Add caller-supplied immutable write-rate evidence for the optional
  time-to-full path.
- Add explicit `PressureDecision` and `PressureDecisionUnavailable` values.
- Add pure deterministic pressure evaluation over K04 capacity evidence,
  optional rate evidence and explicit UTC `as_of`.
- Preserve unavailable behavior for missing, stale, future-dated and malformed
  required evidence.
- Add restriction facts proving K05 is non-authorizing and never grants
  deletion.

## Excluded

- Filesystem polling or capacity observation beyond consuming K04.
- Concrete deployment threshold values.
- Telemetry history, smoothing, forecasting or rate sampling beyond consuming
  caller-supplied evidence.
- Hysteresis/state machines.
- Silent or automatic deletion.
- K06 source protection and reconstruction guarantees.
- K07 relocation mechanics.
- K08 backup topology or restore proof.
- K09 deletion authority.
- K10 checkpoint/recovery.
- A11 source-specific live semantics.
- Monitoring/dashboard/exporter technology.
- Generic admission-control or policy orchestration frameworks.

## Credited evidence

- K04 capacity observation returns complete immutable observations or explicit
  unavailable results and performs no pressure classification or storage
  mutation.
- K04 tests prove unavailable behavior and no `total == used + available`
  assumption.
- `db/init/001_catalog.sql::storage_roots` owns storage-root identity/tier
  topology but not pressure policy.
- `docs/architecture/STORAGE_LIFECYCLE.md` forbids pressure from becoming
  implicit deletion authority.

## Acceptance

DONE means:

1. ADR-0028 is accepted and K05 is removed from unresolved governance.
2. K04 governance state is reconciled to actual merged runtime evidence.
3. `quant_platform.operations` exposes the PressurePolicy v1 runtime model.
4. Policy identity includes threshold, freshness and rate-participation
   semantics while excluding runtime/mount/observation values.
5. Evaluation requires explicit UTC `as_of` and uses no implicit host clock.
6. Freshness boundaries, future-dated evidence and unavailable behavior are
   exact.
7. Optional rate evidence and finite/unbounded time-to-full semantics are exact.
8. State boundaries, equality and byte/time disagreement precedence are exact.
9. Exhausted byte evidence is decided without a time-to-full forecast.
10. Successful and unavailable decisions have deterministic structured
    evidence/identity.
11. K05 restrictions are non-authorizing upper bounds and
    `delete_authorized == false`.
12. No telemetry/scheduler/storage mutation runtime is absorbed into K05.
13. No separate K05 runtime implementation issue is needed for this foundation.

## Verification

Targeted checks for this slice:

```text
python tests/test_operations_pressure_v1.py
python tests/test_operations_capacity_v1.py
python tests/test_package_boundaries_v1.py
python -m compileall -q src tests
python tools/check_markdown_links.py
git diff --check
```

## Downstream state

Unblocked by K05:

```text
K06  RAW/source protection can consume a deterministic pressure restriction seam
K07  Tier relocation can later consume K05 after K06 source protection
```

Still blocked:

```text
A11  Live trades acquisition still requires DG-B live semantics plus K06/K08
K06  Source protection authority and reconstruction guarantees
K07  Crash-safe relocation mechanics
K08  Backup/restore proof
K09  Retention/deletion authority
K10  Checkpoint/recovery after A11/K03/K08
J08  Live product mode operational authorization gates
```
