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

## Amendment 1 (issue #423) — disposal of one physical copy of a raw source archive

**Date:** 2026-10-17
**Authority:** the maintainer directed, on 2026-10-17, that a raw-source disposal process must
exist before the real-day L2 golden is re-run and chose an amendment to this ADR as the vehicle
(over a K09-gate-only route and over a quarantine tool). The rule below is the narrowest one
that honours that direction without contradicting §1; it is proposed for review in the PR that
carries this section. **Amends:** §3 and §5 by adding one candidate kind and one pure policy;
nothing else. **Does not amend:** the preservation classes and their retention periods, K09's
refusal of `UNIQUE_SOURCE` and `PROTECTED_EVIDENCE`, the gate of §2 for catalog-admitted
artifacts, the audit shape of §4, `K05`-`K08`, `ADR-0032`, `ADR-0048`, the catalog DDL, any
manifest or any other ADR.

### Why and bounded evidence

- Pull request #422 (issue #415) added a `cleanup_source` flag to the L2 day ingest that deletes
  the provider-native `ob200` archive after a day is installed. Its adversarial review found
  that this is not covered by this ADR: §1 makes a provider-native archive `UNIQUE_SOURCE`
  (permanent, "Always refused", no override), and the *Decision* paragraph on non-authoritative
  detritus names staging and scratch files, not source evidence.
- This ADR as written is silent on one case: K09 v1 evaluates *catalog-admitted* finalized
  artifacts. A raw source archive is not a catalog partition, so v1 neither authorizes nor
  forbids removing **one physical copy** of it while another independent, verified copy exists.
  `STORAGE_LIFECYCLE.md` §7 asks for exactly that second copy ("an independently recoverable
  second physical copy"), and its §10 forbids silent deletion ("explicit, policy-based,
  auditable, and provenance-aware").
- ADR-0070 Amendment 1 item 1 and Amendment 2 keep events excluded from a canonical day (head
  gap, discontinuities, the out-of-day tail of an archive file) *only* in the retained source
  archive, and the tail of file `D` is the only copy of its events until `A14f` closes the file
  seam. Any disposal rule must therefore not reduce the number of independent copies of such an
  archive to zero.
- Reproduced on the head of #422: (a) with the installed `part-001.parquet` overwritten by
  `b"CORRUPT"` and its manifests intact, a re-ingest with cleanup reported
  `already_published=True` and deleted the source, because the already-published check
  compares manifest JSON, never installed bytes; (b) an `unlink` failure after a successful
  publish raised out of the ingest and lost its result. The requirements below answer both.
- No evidence exists about the provider's retention of old archive files (ADR-0070 Appendix C
  probed availability only). That is why this amendment does **not** treat an archive as
  reacquirable.

### Decision

1. **Vehicle and scope.** An amendment, because it is a narrower, later decision that does not
   contradict the original: the *logical* source evidence stays `UNIQUE_SOURCE` and keeps its
   permanent retention; what this amendment authorizes is removing **one physical copy** when at
   least one other independent verified physical copy of the same bytes remains. It never
   authorizes removing the last copy.
2. **Candidate kind.** A *source copy* is one physical file of a provider-native source archive,
   identified by `(storage_root_id, rel_path, content_sha256, byte_size)` plus the provider's
   archive name. It is described to the audit shape of §4 as a `DeletionCandidateV1` with
   `preservation_class = UNIQUE_SOURCE` and no `catalog_partition_id`, `partition_key` or
   `revision`. Its dataset identity is the `raw` dataset identity of the source (for L2,
   `L2_RAW_IDENTITY`).
3. **Pure policy, separate from K09's.** A new pure decision in `quant_platform.operations`,
   next to (not inside) `retention.py`, evaluates *copy disposal* under its own
   `policy_definition_identity`. K09's `evaluate` is unchanged and keeps refusing `UNIQUE_SOURCE`
   candidates; nothing flips a class to a deletable one. The decision identity is derived as §3
   requires, so it keeps the existing `retention-deletion-decision-v1:sha256:<64 hex>` form and
   fits `deletion_audit_records` unchanged (the candidate and the decision are `jsonb`).
4. **Eligibility (all required; any missing, stale, malformed or contradictory item is
   `REFUSED`).**
   1. *Independent copy.* The caller supplies evidence of a second physical copy with the same
      `(content_sha256, byte_size)` in a **different storage root**, whose bytes were freshly
      re-hashed by the application layer immediately before the decision (a prior record never
      licenses skipping the re-hash, as in ADR-0048 §5). The copy is not the candidate, is not a
      path alias of it and is not part of a relocation in flight (§2 item 7).
   2. *Canonical result verified.* For every canonical partition recorded as fed by this
      archive, the catalog state is `valid` and the installed bytes were re-read and re-hashed
      equal to the catalog `content_sha256` at decision time, through the read the consumer
      uses. Manifest text is never sufficient evidence.
   3. *Protection posture.* A `K06` `ProtectionAssessment` for the source unit, computed as if
      the candidate copy were already gone (that is, over the remaining copy only), is
      `PROTECTED`. `AT_RISK`, `UNAVAILABLE`, `CORRUPT` and `LOST` refuse.
   4. *Provenance.* The decision names an accountable authority and an explicit decision time
      (§3); neither has a default.
   5. *No retention minimum* applies, because no evidence is destroyed: every byte, including
      the excluded and out-of-day events, remains in the surviving copy. The minima of §1 remain
      for catalog-admitted candidates.
5. **Seam rule.** Because item 4.1 leaves a full independent copy, no seam-specific condition
   is needed: the out-of-day tail of file `D` is still held in the surviving copy for `A14f`.
   This is also why disposal of the last copy is excluded (item 7).
6. **Guarded order, as §5.** Evaluate the pure decision from one coherent snapshot of candidate
   and evidence; persist the `PERMITTED` audit **before** touching bytes; delete only the exact
   path/hash named by the decision; persist the application result; leave refused candidates
   untouched. A crash after the audit and before removal leaves the copy in place and
   re-evaluable; a crash after removal and before the result is recovered by detecting the exact
   content missing and recording the result against the existing decision. Before the delete the
   application layer flushes what the verification depends on (the surviving copy and the
   installed canonical bytes) to durable storage; evidence that cannot be made durable refuses.
7. **Not decided here (explicit).** Removing the **last** physical copy of a provider-native
   archive stays refused under §1. A "reacquirable source" disposition would need evidence about
   provider retention that does not exist, an accountable assertion in the sense of ADR-0032's
   attributable-evidence principle, and a superseding ADR rather than an amendment. The
   provider-native trade archive (`.csv.gz`) would follow the same rule, but the tool named by
   #415 (`tools/sync_symbol.py`) does not exist in any branch, so no trade-side disposal is
   specified or implemented.
8. **Implementation seam (decision only).** Ingest is and stays non-destructive: no delete
   flag on `ingest_bybit_l2_day` or on the golden harness, which reads the archive twice and
   never disposes. Disposal is one `quant_platform.application` service composing the pure
   policy, the K09 audit store, the catalog read and the filesystem, plus one operator tool in
   `tools/` that reaches repository code only through `quant_platform.application.*`. The tool
   takes the authority, decision time, candidate, surviving copy and `code_ref` as required
   arguments (no constants) and reports the real outcome per candidate (`removed`,
   `already_absent`, or `refused` with reasons). It does not guess a second copy: the caller
   names it.

### Superseded text and precise scope

No sentence of this ADR is superseded. The following clarifications record how existing text is
to be read from now on.

| Where | Earlier sentence | Reading after this amendment |
|---|---|---|
| §1, `UNIQUE_SOURCE` row | "Permanent" retention, deletion "Always refused". | Unchanged for the logical source evidence and for the last physical copy. Decision 1 adds only removal of **one** physical copy while another independent verified copy remains, evaluated by a separate pure policy (Decision 3). |
| *Decision* preamble, detritus paragraph | "Temporary staging files ... are outside this ADR." | Unchanged. A raw source copy is not detritus and is never treated as such. |
| Consequences, fourth bullet | "K09 cannot delete unique RAW/source evidence ... under v1." | Unchanged for K09's `evaluate`. Copy disposal is not a K09 deletion of the evidence; it removes a redundant copy under Decision 4. |

### Evidence status and implementation handoff

- No measurement of provider retention, of copy-verification cost or of disposal throughput was
  taken. The reproductions above come from the head of #422 and are the only executable
  evidence cited.
- The implementation issue must prove, with a mutation check, that the delete cannot precede
  (a) the fresh re-hash of the surviving copy, (b) the re-hash of the installed canonical bytes,
  (c) the persisted `PERMITTED` audit; and must cover: corrupted installed canonical bytes
  refuse; a corrupted or missing surviving copy refuses; the candidate named as its own copy
  refuses; a same-root copy refuses; an unlink failure leaves the audit intact and reports an
  explicit failed result without raising away a published day; a crash between audit and
  removal and between removal and result.
- #415 is closed as superseded and PR #422 is closed unmerged; its tests for the
  non-destructive default and the CLI exit codes may be reused, its `cleanup_source` flag may
  not.
- The real-day golden is re-run only after that implementation is merged, as the maintainer
  requires, and it keeps reading the archive without disposing of it.
- `quant_platform.operations` keeps its `{"operations", "shared"}` allowance; the catalog DDL,
  manifests, identities and the other ADRs are untouched. Governance status is reconciled
  separately; this amendment edits no planning file.
