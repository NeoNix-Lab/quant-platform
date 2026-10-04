# ADR-0062 - Durable job runtime v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

`C03` is the accepted synchronous Application-service boundary: it resolves a
semantic request and translates its completed outcome. It does not retain an
unbounded request while work runs. `I02` persists attributable Experiment facts
(`Study`, `Trial`, `Run`, and `Artifact`) transactionally, but its repository
explicitly does not infer liveness, select retry policy, or schedule jobs. In
particular, an I02 `RUNNING` record is not liveness evidence.

The platform needs server-side long-running work that survives a process
restart without silently repeating a canonical economic or experiment effect.
ADR-0057 assigns canonical data, accepted artifacts, recovery evidence, and
server-side runtime evidence to the server. It also forbids treating the current
unauthenticated/non-TLS J02 override as a deck handoff channel. No remote worker,
security, placement, or transport decision is needed to define the durable local
Job contract.

## Decision

### 1. A Job is a durable Application-composed operation record

`quant_platform.application` will own J03 composition: admission, durable job
lifecycle coordination, dispatch to an already-owned capability, and stable
outcome translation. It does not acquire the domain semantics of the selected
operation. A future implementation keeps the Job record and its lifecycle
storage distinct from `experiments.ExperimentRepository`; that repository
continues to own only I02 Experiment facts.

An operation handler declares its operation kind, immutable implementation
identity, admitted semantic request, admitted input identities, and its
effect-safety class before admission. The Application layer persists that
immutable admission record before it may dispatch the handler. The server is the
authoritative runtime for this v1 contract. A deck-side execution or a
server/deck handoff remains a G3 placement decision, not a Job-runtime exception.

### 2. Identity and idempotent admission

`job_id` is the deterministic semantic fingerprint of the canonical, versioned
admission record:

- operation kind and contract version;
- handler/implementation identity;
- admitted semantic request identity and canonical parameters; and
- every declared input identity needed to determine the operation's effect.

Timestamps, process IDs, queue positions, paths, worker names, and attempt
counts are not identity inputs. Re-submitting the same admission record returns
the existing Job. Submitting the same `job_id` with different immutable fields
fails closed; it never mutates or replaces the admitted work.

A JobIdentity is never a RunIdentity. One Job may have no Experiment `Run`, or
may reference one or more separately registered `RunIdentity` values and
immutable artifact/result identities. Those references are outcomes of a
handler, not substitutes for job identity, lifecycle, or attempt history.

### 3. Durable lifecycle and restart recovery

The durable states are:

```text
ADMITTED -> QUEUED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED
                         |
                         +-> RECOVERY_REQUIRED
CANCELLATION_REQUESTED ---+
```

`SUCCEEDED`, `FAILED`, and `CANCELLED` are terminal. Terminal records are
immutable except for adding bounded diagnostic or evidence references; a result
identity is never replaced. `RECOVERY_REQUIRED` is deliberately non-terminal:
it records that a prior process stopped while work might have been active and
requires an explicit recovery decision.

The allowed transitions are only `ADMITTED -> QUEUED|CANCELLED`,
`QUEUED -> RUNNING|CANCELLED`, `RUNNING -> SUCCEEDED|FAILED|
CANCELLATION_REQUESTED|RECOVERY_REQUIRED`,
`CANCELLATION_REQUESTED -> CANCELLED|FAILED|RECOVERY_REQUIRED`, and
`RECOVERY_REQUIRED -> QUEUED|FAILED|CANCELLED` after an explicit effect-safe
recovery decision. No terminal state has an execution transition.

After restart, the runtime reads durable Job records. It does not make
`RUNNING` liveness evidence and it does not silently resume, redispatch, or
declare success for a former `RUNNING` attempt. Each such record becomes
`RECOVERY_REQUIRED` with its last durable attempt evidence. An operator or future
authorized API may then fail it, cancel it, or request a retry/resume under
section 4. Missing, conflicting, or unreadable lifecycle/result evidence also
fails closed to `RECOVERY_REQUIRED` or `FAILED`, never to `SUCCEEDED`.

### 4. Attempts, retry, and effects

An attempt has the stable pair `(job_id, monotonically increasing attempt_no)`.
It is durable before dispatch and records only bounded start/end, state,
failure-code, and evidence-reference fields. Automatic retry is prohibited.

An explicit retry/resume retains the Job identity and creates a new attempt only
after the handler's declared effect-safety rule proves one of the following:

1. no externally visible effect was admitted or performed;
2. the effect is pure/replayable; or
3. the owning capability enforces an idempotency key or can provide durable
   evidence that the earlier effect completed exactly once.

If that proof is unavailable, the Job remains `RECOVERY_REQUIRED` or becomes
`FAILED`; it may not be redispatched. Economic, canonical-publication, and
Experiment effects therefore remain attributable to their owning domain records
and cannot be duplicated merely because a worker or process disappeared.

### 5. Cancellation, result, failure, and observability

A queued job can become `CANCELLED` before dispatch. A running job first records
`CANCELLATION_REQUESTED`; it becomes `CANCELLED` only after its handler
acknowledges that it has stopped and records the required effect evidence. If
that acknowledgement cannot establish whether an effect occurred, cancellation
is not inferred: the Job enters `RECOVERY_REQUIRED` or `FAILED`.

`SUCCEEDED` requires durable immutable result/artifact references plus every
declared domain reference (including a `RunIdentity` where applicable). `FAILED`
and `CANCELLED` carry an explicit stable reason code and may carry a bounded,
sanitised diagnostic/evidence reference. Progress is optional and is persisted
only when its operation-specific meaning is declared; it is not a liveness
signal. The Job record exposes only bounded identity, lifecycle, attempt,
result-reference, reason-code, and diagnostic-reference data. Raw unbounded
worker logs, credentials, paths, or canonical payloads are not Job status fields.

### 6. Deliberate implementation boundary

This decision permits a later bounded, server-local J03 implementation under
`quant_platform.application` and its existing allowed dependency direction to
the capability it composes. It does not permit a new top-level owner,
distributed scheduler, workflow platform, message broker, generic executor, or
remote-execution framework. It also does not define authentication/TLS (G1),
server/deck placement or artifact handoff (G3), Consumer-API seams (G4), or
transport evolution (G5).

## Consequences

- Later J03 work must implement the admission record, lifecycle, attempt
  evidence, and recovery rules above before exposing a long-running operation.
- I02 Run/Artifact persistence remains reusable outcome evidence but is not
  overloaded into a cross-domain Job store.
- A client-facing submission/status API is a later G4/G5 implementation concern;
  it must carry this contract without redefining it.
- No current production runtime, schema, worker, or transport changes here.

## Acceptance evidence

Direct inspection established that `ExperimentRepository` persists I02 durable
facts while excluding worker liveness, retry, and scheduling; its
`RunRecord.running_is_liveness_evidence` is explicitly false. The existing I02
tests prove idempotent registration, durable Run transitions, failed attempt
visibility, and distinct retry `RunIdentity` values. C03's
`application.market_data` remains a completed bounded synchronous seam, and
ADR-0057 supplies the server-authority and no-remote-framework constraints.

`tests/test_durable_job_runtime_contract_v1.py` guards the persisted J03 decision
and its contract/source-boundary cross-references. It is a regression guard for
this design authority, not evidence of a Job runtime implementation.

## Out of scope

- production Job storage, schema migration, workers, scheduling, or an API;
- remote execution, deck handoff, authentication/TLS, or credentials;
- Experiment-domain semantics, live/paper trading, or broker execution;
- changing C03, I02, J02, or ADR-0057 accepted behavior.

## Related

Parent tracking: #281. Closes issue #282.
