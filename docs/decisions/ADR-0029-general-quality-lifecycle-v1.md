# ADR-0029 - General Quality Lifecycle v1

**Status:** ACCEPTED

**Date:** 2026-09-16

## Context

A08/S13 records immutable, target-bound quality evidence for sealed partition
revisions. A09/S14 consumes the first accepted vertical and authorizes
publication only when current evidence is `pass` or accepted source-only
`warn`. That first vertical is credited and is not reopened here.

A10 repair and A11 live acquisition need stable quality facts beyond first
publication. In particular, they need deterministic reassessment semantics for
the current live partition revision, explicit supersession, and exact
`pass/warn/fail` lifecycle mapping without letting repair policy define quality
truth.

The existing catalog already has:

- immutable partition revisions with `writing`, `closed`, `valid`,
  `degraded`, `invalid` and `superseded` states;
- `partitions_one_live`, enforcing at most one non-superseded revision per
  natural partition key;
- structured JSON `quality_reports.metrics`, sufficient to carry explicit
  supersession links without a schema migration.

## Decision

Adopt General Quality Lifecycle v1 as the canonical A16 contract.

Quality evidence, quality assessment, lifecycle application, publication/read
eligibility, and repair/live policy remain separate concepts:

```text
quality evidence
      -> quality assessment/disposition
      -> lifecycle state application
      -> publication/read eligibility
      -> repair/live policy
```

Producing a quality report does not mutate a partition. Repair policy does not
define quality truth.

### Target-bound immutable assessment evidence

One partition assessment binds one explicit partition natural identity, durable
target evidence, selected assessment scope, status in `pass | warn | fail`,
metrics/violations, and certifier `code_ref`. A reassessment creates new
evidence; it never rewrites previous evidence.

The selected assessment scope is the required profile/suite contract for the
capability. Reports from unrelated suites or profiles do not compete for the
same lifecycle decision.

### Semantic assessment identity

A16 uses the existing deterministic semantic signature convention over:

- check suite;
- status;
- normalized metrics;
- normalized violations;
- certifier `code_ref`.

Catalog UUIDs, timestamps, insertion order and physical row location are not
semantic assessment identity. Semantically identical repeated evidence has the
same signature and is idempotent for lifecycle meaning.

### Explicit supersession

Timestamp order never implies authority. A superseding reassessment for the
same target evidence and assessment scope must carry:

```text
metrics.supersedes_assessment_signature
```

Omitted or null means root/non-superseding evidence. When present, the value is
the exact prior semantic assessment signature being superseded. The link itself
participates in the successor signature. It may reference only another
applicable assessment for the same target evidence and scope. Unresolved,
foreign, self-referential or cyclic links fail closed. A16 v1 permits one
predecessor per assessment. Multiple distinct successor leaves over the same
predecessor remain ambiguous rather than timestamp-resolved.

Existing accepted S13/S14 reports without this field remain valid root
assessments; no backfill is required.

### Current authoritative assessment

For one partition target and one assessment scope:

1. discard reports whose bound durable target evidence is stale/non-current;
2. collapse semantically identical signatures;
3. validate explicit supersession links among remaining assessments;
4. remove assessments superseded by a valid successor;
5. require exactly one unsuperseded semantic leaf.

Exactly one valid leaf is current. Zero leaves means evidence unavailable.
More than one distinct leaf is ambiguous. Invalid supersession topology fails
closed. Row order, report UUID and timestamp never break ties.

### Lifecycle states

The canonical partition states remain:

```text
writing
closed
valid
degraded
invalid
superseded
```

Quality-derived application is bounded to the current live, sealed,
non-superseded partition revision and maps exactly:

```text
pass -> valid
warn -> degraded
fail -> invalid
```

Allowed source states are:

```text
closed | valid | degraded | invalid
```

Applying the same result again is idempotent. A16 refuses `writing`,
topologically `superseded`, and any revision that is not the current live
revision for its natural partition key. `superseded` is topology-owned and
absorbing; quality reassessment cannot reactivate it.

S14 publication semantics remain unchanged: S14 may authorize only `pass` and
accepted `warn`; `fail` can classify a sealed live partition as `invalid`
through A16, but cannot authorize publication eligibility.

Dataset-level quality reports do not fan out into partition lifecycle
transitions in A16 v1.

## Runtime materialization

The accepted A16 runtime foundation is implemented under
`quant_platform.data.quality_lifecycle` as:

- deterministic semantic assessment signatures;
- current partition report filtering against durable target evidence;
- explicit `supersedes_assessment_signature` graph validation;
- current authoritative assessment selection;
- exact `pass -> valid`, `warn -> degraded`, `fail -> invalid` mapping;
- transactional catalog application for the current live sealed partition
  revision;
- idempotent same-state application;
- explicit refusal for missing, stale, ambiguous, unresolved, cyclic,
  `writing`, `superseded` and non-live evidence/topology;
- immutable lifecycle result provenance with natural identity, selected scope,
  assessment signature/status, prior/resulting state, bound evidence and
  lifecycle code identity.

The S14 publication path reuses the shared current-assessment selector while
preserving its existing fail-publication refusal.

A16 does not introduce a generic quality framework, event-sourcing subsystem,
new lifecycle-event table, repair scheduler, provider plugin abstraction, or
dataset-level fan-out runtime.

## Consequences

- A16 is frozen and complete for the bounded v1 partition lifecycle foundation
  needed before A10/A11 can consume stable quality facts.
- A10 remains downstream and must still define repair triggering,
  precedence, retry and replacement-revision semantics.
- A11 remains downstream of A10 and the live/operational prerequisites; A16
  does not define live cursor, overlap or duplicate behavior.
- B04 non-contiguous coverage read semantics remain unresolved.
- Existing S13/S14 first-vertical behavior remains credited and unchanged.

## Acceptance evidence

Executable proof is in `tests/test_quality_lifecycle_v1.py`.

The proof covers closed `pass/warn/fail` mapping, explicit superseding
reassessment across `valid/degraded/invalid`, duplicate signature
idempotency, ambiguous distinct leaves, unresolved/self/cyclic supersession
refusal, stale evidence refusal, `writing`/`superseded`/non-live refusal,
dataset-level non-application, decision provenance, exact status mapping, and
S14 fail publication refusal.

Focused S14 regressions remain in:

- `tests/test_publication_eligibility_bridge_v1.py`;
- `tests/test_publication_eligibility_bridge_v2.py`.
