# ADR-0049 - Retention / deletion authority v1

**Status:** ACCEPTED
**Date:** 2026-09-29

## Context

`K09` is the remaining `DG-H` operational-safety decision after `K06`
source protection, `K08` backup/restore and `K07` storage relocation.
`STORAGE_LIFECYCLE.md` deliberately left retention periods and deletion
authority open: pressure may signal that capacity is scarce, and relocation
may move bytes between roots, but neither capability may decide that the
platform no longer retains data.

The deletion authority must be narrower than the capabilities it composes:

- `K05` pressure is an upper-bound restriction and never grants deletion.
- `K06` protection proves whether bounded source/reconstruction evidence is
  known, complete and recoverable enough for its own claim; it never grants
  deletion.
- `K08` proves an identity-bound recovery set can be restored into an isolated
  target and read back. Backup existence without an isolated restore proof is
  not deletion authority.
- `K07` cleanup only removes a byte-identical non-authoritative copy after a
  successful relocation switch. That is copy cleanup, not K09 retention.

`K09` therefore needs a fail-closed, auditable policy whose implementation can
stay pure inside `quant_platform.operations` while any catalog/filesystem
mutation is composed at `quant_platform.application`.

## Decision

Adopt `RetentionDeletionAuthority v1` as the canonical K09 policy.

K09 v1 authorizes only governed deletion of catalog-admitted, finalized data
plane artifacts for which the candidate can be described by stable logical and
physical identity:

- `DatasetIdentity`;
- partition key and revision, where applicable;
- catalog partition id, where applicable;
- storage root id and relative path(s);
- content hash and byte size;
- lifecycle/provenance evidence;
- explicit preservation class.

Temporary staging files, incomplete writes, transient process scratch files and
other non-authoritative runtime detritus are outside this ADR. Removing those is
ordinary local cleanup only if another accepted capability owns it. K09 must not
be used as a back door to delete data whose identity cannot be audited.

### 1. Preservation priority classes

K09 v1 freezes these preservation classes, following
`STORAGE_LIFECYCLE.md` section 6:

| Class | Meaning | K09 v1 retention period | Deletion disposition |
|---|---|---:|---|
| `UNIQUE_SOURCE` | Unique RAW/source evidence, provider-native archive, migration source, or any input that cannot be reconstructed from another accepted source. | Permanent | Always refused. |
| `PROTECTED_EVIDENCE` | Any artifact that is itself K06-protected evidence, or any sole-recoverable member of a reconstruction/recovery claim. | Permanent | Always refused. |
| `CANONICAL_RESTORABLE` | Finalized canonical partition/publication bytes whose exact content identity is covered by a verified K08 isolated-restore proof independent of the candidate storage boundary. | Minimum 90 days after finalization. | May be permitted only after every precondition below passes. |
| `DERIVED_RESTORABLE` | Derived, recomputable or cache-like finalized artifact whose exact content identity is covered by a verified restore/reconstruction proof accepted by the candidate's owning contract. | Minimum 30 days after finalization or supersession, whichever is later. | May be permitted only after every precondition below passes. |

Deployments may choose longer retention windows. They may not shorten these
v1 minima without a new ADR or versioned policy.

`UNIQUE_SOURCE` and `PROTECTED_EVIDENCE` do not become eligible by age,
pressure, operator request, available backup bytes, or a successful relocation.
K09 v1 has no override path for them.

### 2. Required precondition gate

A K09 deletion decision can be `PERMITTED` only when all of these predicates
are true for the exact deletion candidate:

1. The candidate is in an eligible preservation class:
   `CANONICAL_RESTORABLE` or `DERIVED_RESTORABLE`.
2. The candidate is finalized, not `writing`, not incomplete staging, not an
   open live segment, and not an unsealed partial artifact.
3. The candidate's minimum retention period has elapsed from the later of
   finalization time and supersession time.
4. A verified restore proof exists for the data in question:
   - for canonical market data, this is a K08 `RecoverySetV1` whose isolated
     restore proof covers the same logical identity, partition/revision,
     content hash and declared coverage;
   - the proof target must be isolated from the candidate storage boundary;
   - the proof must be for the candidate's current content identity, not merely
     for an older dataset family or path.
5. The candidate is not a required member of the restore proof being cited.
   A backup that depends on the bytes being deleted is not independent
   recovery.
6. The candidate is not K06-protected evidence and is not sole-recoverable
   evidence for any accepted reconstruction, backup, restore, checkpoint or
   source-acquired publication claim known to the caller.
7. No non-terminal K07 relocation exists for the candidate:
   `PLANNED`, `STAGED`, `VERIFIED` or `SWITCHED` all refuse deletion.
   `CLEANED_UP` is terminal evidence and does not by itself block K09.
8. The current K05 pressure decision, when supplied, does not fabricate
   permission. `NORMAL`, `PRESSURE`, `CRITICAL` and `EXHAUSTED` may all be
   recorded as context, but none can satisfy or bypass the restore/protection
   gates above.

If any required evidence is missing, malformed, stale relative to the candidate
content identity, or contradictory, the decision is `REFUSED`. K09 has no
`UNKNOWN_BUT_DELETE_ANYWAY` state.

### 3. Structural implementation shape

The pure operations layer owns only deterministic policy evaluation. The K09
implementation issue must introduce an operations-owned model equivalent to:

```text
RetentionPolicyDefinitionV1
DeletionCandidateV1
VerifiedRestoreProofRef
ProtectionEvidenceRef
RelocationEvidenceRef
RetentionDeletionDecisionV1
```

`RetentionDeletionDecisionV1` must be constructible as `PERMITTED` only when a
`VerifiedRestoreProofRef` is present and cross-bound to the candidate content
identity. A bare boolean such as `has_backup=True` is forbidden.

The decision identity must be deterministic over:

- policy definition identity and version;
- candidate logical identity and content identity;
- preservation class;
- retention clock inputs;
- cited K08 restore proof identity;
- cited K06/protection evidence;
- cited K07 relocation terminal or absence evidence;
- decision time supplied explicitly by the caller.

The pure operations module must not import `access`, `producer`, filesystem,
PostgreSQL, source adapters, schedulers or application services. Actual
catalog lookup, evidence loading, persistence of audit rows, path deletion and
transaction handling belong to the application layer.

### 4. Deletion audit trail

Every deletion evaluation, including refusals, must be representable as an
audit document. The minimum audit shape is:

```text
deletion_decision_id
policy_definition_identity
decision_time
decision_actor_or_authority
decision = PERMITTED | REFUSED
refusal_reasons[]
candidate:
  dataset_identity
  catalog_partition_id?
  partition_key?
  revision?
  storage_root_id
  rel_path
  content_sha256
  byte_size
  lifecycle_state
  producer
  code_ref
preservation_class
retention:
  finalized_at
  superseded_at?
  minimum_retention_days
  eligible_at
restore_evidence:
  recovery_set_identity
  isolated_restore_proof_identity
  restored_content_sha256
  restored_coverage_or_support
protection_evidence:
  k06_assessment_identity?
  protection_state?
  sole_recoverable_refusal_evidence?
relocation_evidence:
  relocation_id?
  relocation_phase?
application_result:
  deleted_at?
  deleted_paths[]
  tombstone_or_catalog_update_ref?
```

The pure decision audit is sufficient to answer "why would this be safe or
refused?" The application result extends it after actual deletion to answer
"what bytes were removed and what catalog/tombstone state records that fact?"

### 5. Catalog and action boundary

K09 v1 may require an application-layer durable audit table during
implementation, but that table is scoped to deletion decisions/results. This
ADR does not introduce a generic compliance subsystem, legal-hold framework,
workflow engine, alerting stack or enterprise audit product.

The application layer must perform the destructive action in a guarded order:

1. evaluate the pure K09 decision from one coherent snapshot of candidate and
   evidence;
2. persist the `PERMITTED` decision audit before deleting bytes;
3. delete only the exact path/content identity named by the decision;
4. verify the catalog/tombstone/audit result records the deletion outcome;
5. leave refused candidates untouched.

If application execution crashes after decision persistence but before byte
removal, the candidate remains retained and a later run may re-evaluate or
resume. If it crashes after byte removal but before result persistence, the
application layer must recover by detecting the missing exact content and
recording the result against the already persisted decision; it must not create
a new unrelated decision to explain an old deletion.

## Consequences

- Issue #199 can implement K09 without further semantic decisions: eligible
  classes, retention periods, restore-proof gate, protected-evidence refusal,
  relocation-in-flight refusal, audit shape and package boundary are fixed.
- `quant_platform.operations` remains pure and keeps its package-boundary
  allowance at `{"operations", "shared"}`.
- K09 cannot be used for emergency pressure reclamation unless the same
  restore/protection/retention gates pass; capacity pressure supplies context,
  not authority.
- K09 cannot delete unique RAW/source evidence, K06-protected evidence or
  sole-recoverable evidence under v1.
- This ADR does not choose backup destination, backup frequency, monitoring
  technology, legal/compliance taxonomy, a scheduler, or an off-site disaster
  recovery model.

## Acceptance evidence

This ADR is the design-gate output for issue #198. Implementation and
executable proof belong to issue #199.
