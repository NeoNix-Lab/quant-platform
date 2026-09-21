# ADR-0039 — Backup / restore v1

**Status:** ACCEPTED
**Date:** 2026-09-21

## Context

K08 is the remaining restore-proof prerequisite on the first live-ingest path. K06 source protection is already frozen and complete under ADR-0032. The live-ingest scope requires proof that already-published canonical state can survive loss/corruption of the primary operational storage without pulling tier relocation (K07), retention/deletion (K09), high availability, or a generic backup platform into scope.

## Decision

### 1. Protected recovery unit

K08 v1 protects finalized published canonical state, not arbitrary filesystem state. A `RecoverySetV1` is the identity-bound set of artifacts/evidence required to reconstruct one accepted canonical publication state, including where applicable:

- published canonical partition artifacts;
- partition/publication manifests;
- declared-coverage evidence;
- catalog state required to resolve those publications;
- K06-protected evidence required to validate/reconstruct the same accepted state.

Temporary files, incomplete staging, caches and volatile runtime memory are not recovery authority.

### 2. Failure model

K08 v1 proves recovery from loss or corruption of the primary operational storage while an independent backup destination remains available. It does not claim whole-site disaster recovery, simultaneous primary+backup loss tolerance, automatic failover or high availability.

The backup destination used as evidence must be independent of the primary storage boundary being tested. Independence is an observed deployment property, not inferred from two directory names on the same storage device.

### 3. Consistent finalized generation

A valid recovery set must represent one internally consistent finalized publication generation. A backup that mixes incompatible artifact/catalog/coverage generations is invalid.

The implementation mechanism (snapshot, copy/export plus manifest, filesystem primitive, etc.) is local provided this invariant is proven.

### 4. Recovery objective

K08 v1 does not freeze an arbitrary wall-clock backup frequency or numeric RTO.

```text
RPO v1 = the most recent successfully finalized RecoverySetV1.
RTO v1 = no numeric SLA; a bounded manual restore is acceptable.
```

Operational frequency may be configured and observed, but changing frequency does not redefine recovery semantics.

### 5. Restore proof

Backup existence is not proof. K08 acceptance requires restoring one recovery set into an empty isolated target that cannot fall back to primary state, then proving the restored state reproduces the required canonical identities and remains readable through the existing historical data path.

At minimum the restore proof must establish, for the selected dataset/publication evidence:

- the same logical `DatasetIdentity`;
- the same published partition identities/revisions;
- the same content digests where identity binds them;
- the same declared coverage;
- a catalog state that resolves the restored publications;
- equivalent historical DataGateway-visible records.

### 6. Mutation boundaries

K08 does not authorize destructive testing against shared/production state. The restore target is isolated. Any real primary-storage destruction/loss simulation requires separate explicit authorization.

## Excluded

This ADR does not define or authorize:

- K07 tier relocation;
- K09 deletion/retention authority;
- whole-host/off-site disaster recovery;
- HA/automatic failover;
- a generic backup framework;
- live cursor/checkpoint semantics (K10);
- provider/live-acquisition semantics (A11).

## Consequences

- K08 semantics are frozen and implementation may proceed with the minimum sufficient backup+isolated-restore proof.
- A11 may rely on K08 only after that proof exists; this ADR alone does not make K08 implementation complete.
- K07 remains independent and K09 remains downstream of successful restore proof.
