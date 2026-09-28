# ADR-0048 — Storage tier relocation v1 (K07)

**Status:** ACCEPTED
**Date:** 2026-09-28

## Context

`SCOPE.md` ("Wave 6 — Live Consumer Data Plane & Storage Lifecycle v1")
Active Path step 4 requires a design gate that resolves `DG-H`'s still-open
tier-relocation proposition before any implementation slice may build
crash-safe hot→cold/deepcold relocation (issue #196, blocking issue #197).

`K05` (`ADR-0028`) and `K06` (`ADR-0032`) are frozen and implemented.
`docs/architecture/STORAGE_LIFECYCLE.md` already fixes the invariant this
ADR must satisfy (§4: a crash at any point must leave either the original or
the fully-verified new placement authoritative, never neither) and
explicitly leaves the exact algorithm and thresholds open (§13).

Three existing pieces of evidence ground every decision below, so this ADR
adopts them rather than inventing competing mechanisms:

- `quant_platform.access.models.CatalogPartition` already carries
  `storage_root_id`, `content_sha256` and `manifest_sha256` as plain columns
  (`db/init/001_catalog.sql`'s `partitions` table: `storage_root_id text NOT
  NULL REFERENCES storage_roots ON DELETE RESTRICT`). Relocation is
  therefore a **single foreign-key column update on an existing row**, not a
  new schema concept.
- `quant_platform.application.backup_restore` already implements
  content-identity-verified atomic staging (`_atomic_copy`, `_verify_file`,
  `_sha256_file`, `_atomic_write_json`) for exactly this shape of problem
  (copy, verify by `(sha256, size)`, then commit). K07 reuses this pattern,
  not a new one.
- `quant_platform.operations.checkpoint.advance_checkpoint` already
  establishes the precedent for a pure, catalog-independent state-transition
  validator over a durable record (domain match, monotonic advance,
  idempotent no-op on an identical candidate, explicit regression refusal).
  K07's relocation-phase validator follows the same shape.

Issue #196's own decision questionnaire (posted to the issue as a comment)
separates auto-answered items from those this ADR must still freeze. This
ADR closes every item the questionnaire marks "Decision needed," plus its
one conditional stop condition (item 74); it does not revisit auto-answered
items, and it does not weaken `K06` or reach into `K09`'s deletion authority.

## Decision

### 1. Scope: a generic, root-pair-agnostic algorithm; thresholds stay open

Closing items 11 and 13: this ADR defines the safe relocation algorithm for
**any** `(source_storage_root_id, target_storage_root_id)` pair already
present in the `storage_roots` catalog table, given `source != target`. It
does not select which partitions should move or when — `STORAGE_LIFECYCLE.md`
§13's age/utilization/schedule thresholds remain explicitly open, and a
caller (an operator, or a future scheduled job — out of scope) supplies the
partition and target root. Wave 6's own Golden E2E proof (issue #200)
exercises `hot → cold`; `cold → deepcold` uses the identical mechanism
without further design work, because the algorithm never branches on which
specific roots are involved.

### 2. Eligibility predicate

Closing item 14: a partition is relocation-eligible only when its catalog
`state` column is `"closed"` — the same terminal, immutable lifecycle state
`LifecyclePolicy.VALID_AND_CLOSED` already exposes to readers. A partition
still in `"valid"` (open/actively-written) or `"degraded"` state is refused
(closing item 15's mandate that open partitions must be excluded, made
concrete). This needs no new column: `state` already exists on `partitions`.

### 3. Admission gates: K05 pressure and K06 protection

Closing items 17-19: before staging begins, the pure relocation validator
requires two pieces of caller-supplied evidence:

- **K05** — a current `PressureDecision` for the **target** root. `NORMAL`
  and `PRESSURE` admit relocation (pressure on the source `hot` root is
  typically the very reason to relocate off it, so source pressure never
  blocks relocation); `CRITICAL` or `EXHAUSTED` **on the target** root
  refuses admission — starting a write-heavy staging operation into an
  already-critical root would itself violate K05's role as an upper bound on
  downstream admission.
- **K06** — a current `ProtectionAssessment` for the unit, confirming its
  protection posture is known. Relocation does not require a `K08` backup
  first (relocation is not deletion: exactly one authoritative copy exists
  throughout, matching the pre-relocation posture) but it must not proceed
  against a unit whose protection state is unassessed or refused, so a
  mid-relocation crash never leaves attributable evidence in question.

### 4. Durable relocation record and phase state machine

Closing items 24-27: a new durable `RelocationRecordV1` value, persisted by
the `application`-layer composer (not inside `operations`, preserving
`operations`'s `{"operations", "shared"}` `ALLOWED` set), tracks one
relocation job through five phases:

```text
PLANNED -> STAGED -> VERIFIED -> SWITCHED -> CLEANED_UP
                                           \-> (REFUSED, terminal, from PLANNED or STAGED)
```

- **Job identity**: `relocation_id = sha256(dataset_identity, partition_key,
  revision, source_storage_root_id, target_storage_root_id)` — deterministic
  over the job's own inputs, exactly like `LiveCheckpointV1`'s domain key,
  so re-issuing the same relocation request is always the same job, never a
  new one.
- **Durable storage**: a new `relocation_jobs` catalog table
  (`relocation_id primary key`, `phase`, `source_storage_root_id`,
  `target_storage_root_id`, `target_content_sha256`, `target_size_bytes`,
  `updated_at`), analogous to how `LiveCheckpointV1` durably persists K10
  state — this is a legitimate new schema addition scoped to K07 alone
  (unlike `B06`/`ADR-0047`, nothing in this scope forbids it; crash-safe
  restart is not provable without *some* durable phase record).
- The pure `operations.relocation` module's `advance_relocation(previous,
  candidate)` validates each transition exactly like `advance_checkpoint`
  does: same-domain check, forward-only phase ordering, idempotent no-op on
  an identical candidate, explicit refusal of any transition that skips a
  phase (e.g. `PLANNED → SWITCHED` directly is illegal).

### 5. Content-identity verification

Closing items 37, 38 and 40: verification compares the staged target copy's
freshly computed `(sha256, size)` — via the same primitive shape as
`backup_restore._sha256_file`/`_verify_file` — against the **catalog's
already-recorded** `content_sha256` for that partition. It does not compute
a new identity or a new manifest; it confirms the physical copy matches the
logical identity already on record (closing item 39's auto-answer: no new
semantic dataset/version is created). The durable `VERIFIED` phase record
itself, written only after this comparison succeeds, is the evidence that
verification preceded the switch (closing item 40); a restart that finds a
`VERIFIED` record with a stale/missing/corrupt `target_content_sha256`
treats it as not-verified and restages from scratch (closing item 35 —
fail closed, never trust ambiguous evidence).

### 6. Pure/impure split (closing items 41, 53-55)

- `quant_platform.operations.relocation` (pure, no filesystem/catalog
  access): `plan_relocation(...) -> RelocationPlan` (validates eligibility,
  K05/K06 evidence, and root pair), `advance_relocation(previous, candidate)
  -> RelocationRecordV1` (phase-transition validator), and
  `verify_target_identity(expected_sha256, expected_size, actual_sha256,
  actual_size) -> bool` (pure comparison over caller-supplied evidence).
  Exceptions: `RelocationError` (base), `RelocationDomainMismatch`,
  `RelocationPhaseError` (illegal transition), `RelocationVerificationFailed`,
  `RelocationRefused` (K05/K06 admission gate failed) — mirroring
  `checkpoint.py`'s exact exception-hierarchy shape.
- An `application`-layer composer (exact module name deferred to the K07
  implementation issue, following `live_ingest_server.py`'s own composition
  pattern) performs the actual file copy (`_atomic_copy`-equivalent),
  computes real `(sha256, size)` evidence, persists `RelocationRecordV1` rows,
  and issues the single-row catalog `UPDATE`.

### 7. Atomic catalog switch primitive

Closing items 28 and 29: the switch is
`UPDATE partitions SET storage_root_id = :target WHERE catalog_partition_id
= :id AND storage_root_id = :source` inside one transaction. Postgres's own
row-level transactional guarantee is the entire mechanism — no new locking
or two-phase-commit machinery is needed. A concurrent reader's
`select_partitions` query (already a plain `SELECT ... JOIN storage_roots`)
observes either the pre-switch row or the post-switch row, atomically, by
construction of ordinary read-committed row visibility; it can never observe
a null or half-written `storage_root_id` (closing item 60 — no new
concurrent-reader design is needed beyond what Postgres already guarantees).
The `WHERE storage_root_id = :source` guard makes the switch itself
idempotent-safe against a duplicate retry: a second attempt after a
already-successful switch matches zero rows and is a detectable no-op, not a
silent double-application.

### 8. Restart/resume rules per interrupted phase

Closing items 30-34, resolving each case the questionnaire enumerates
against the phase state machine in decision 4:

| Durable record found at restart | Catalog state | Action |
|---|---|---|
| No record (crash before `PLANNED` committed) | points to source | Original is authoritative; start fresh, equivalent to a first attempt. |
| `PLANNED` or `STAGED` (incomplete/no verified copy) | points to source | Original is authoritative; discard any partial staged copy and restage from scratch. |
| `VERIFIED` | points to source | Original is authoritative; proceed directly to the `SWITCHED` transition — no need to recopy, but re-run decision 5's comparison if the durable evidence is not fully trustworthy (see decision 5). |
| `SWITCHED` (or `VERIFIED` but catalog already shows target — a switch that committed durably but whose record update lagged) | points to target | New placement is authoritative; the source copy is harmless cleanup debt. Resume at cleanup (decision 9). |
| `CLEANED_UP` | points to target | Job already complete; idempotent no-op. |

This table is exhaustive over every case items 30-34 name, including the
"catalog says old but a verified new copy exists" and "catalog says new but
old copy still exists" cases: in both, the **catalog row**, not the durable
relocation record, is ground truth for which placement is authoritative —
the relocation record only ever decides what work remains to converge the
non-authoritative side.

### 9. Source cleanup is K07-scoped, not K09, and defaults to deferred

Closing items 43-45, 48, 49 and 74: source cleanup — removing the
now-redundant pre-switch copy — is part of K07, executed only in the
`SWITCHED → CLEANED_UP` transition, strictly after the catalog switch has
committed (closing item 44's auto-answered "never before" with a concrete
phase gate). This is categorically distinct from `K09`'s retention/deletion
authority: K07 cleanup only ever removes a copy that has already been proven
byte-identical to a still-fully-retained, still-logically-present
authoritative copy at a different physical location — no data the platform
retains is ever reduced by it. `K09` governs removing data the platform no
longer retains anywhere. This framing means item 74's stop condition is not
triggered: K07 cleanup can never become retention/deletion policy as long as
implementation enforces "only delete a copy whose sibling already passed
decision 5's verification and is the current catalog-authoritative one."

Default behavior is to run cleanup immediately after a successful switch
within the same job (v1 keeps the job's five phases as one logical
operation), but a crash leaving `SWITCHED` uncleaned is not an error state —
old copies may persist as cleanup debt indefinitely without threatening
correctness, and a future run can always resume and complete the `CLEANED_UP`
transition. The audit evidence retained after cleanup is the terminal
`RelocationRecordV1` row itself (job identity, both roots, verified content
hash, phase timestamps) — sufficient to answer "what moved, when, and how it
was verified" without needing the deleted bytes.

This ADR explicitly flags, without resolving, that `ADR-0049` (`K09`'s own
design gate, issue #198) must define its own precondition refusing deletion
of a unit with a non-terminal (in-flight) `RelocationRecordV1`; K07 does not
implement or authorize that check itself (closing item 63's forward
reference).

### 10. Concurrency: at most one in-flight job per partition

Closing items 61 and 62: the `application`-layer composer must refuse to
start a new relocation job for a partition that already has a non-terminal
(`PLANNED`/`STAGED`/`VERIFIED`/`SWITCHED`) `RelocationRecordV1` row, keyed by
`(dataset_identity, partition_key, revision)` independent of target root —
a second concurrent request for the same partition is refused, not queued
or run in parallel, mirroring `advance_checkpoint`'s "one recovery domain,
one authoritative progression" discipline rather than inventing a new
lease/lock primitive.

### 11. Testability: hermetic simulated proof for v1

Closing items 64-70: `K07`'s own tests (issue #197) must hermetically
simulate a crash immediately after each phase commit —
`PLANNED`/`STAGED`(partial copy)/`STAGED`(complete, unverified)/`VERIFIED`/
`SWITCHED` — over a temp-directory two-root fixture, and assert restart
converges to exactly the decision-8 table's outcome for each, with
`tests/test_package_boundaries_v1.py` unchanged. A real-filesystem/real-server
proof is not required at this level; that evidence belongs to Wave 6's own
Golden E2E proof (issue #200), which exercises K07 concurrently with a live
`B06`/`D04` consumer — exactly as `K10`'s hermetic proof matrix preceded its
own separate real-server proof (PR #122).

## Consequences

- `K07`'s v1 contract is now specific enough for issue #197 to implement
  without further semantic decisions: eligibility, admission gates, the
  five-phase durable record, the exact switch primitive, every restart case,
  and the cleanup/K09 boundary are all fixed.
- A new `relocation_jobs` catalog table is required (decision 4); this is a
  scoped, K07-specific schema addition and does not affect `B06`/`ADR-0047`'s
  no-new-schema constraint, which was specific to the consumer-cursor seam.
- `quant_platform.operations`'s `ALLOWED` set stays `{"operations", "shared"}`;
  all catalog/filesystem access is composed at `application`, following
  `live_ingest_server.py`'s existing pattern.
- No generic scheduler, HA/distributed consensus, or off-site DR was
  introduced; thresholds for *when* to relocate remain open per
  `STORAGE_LIFECYCLE.md` §13, unaffected by this ADR.
- `K06`'s source-protection guarantees are unweakened: relocation never
  removes a unit's only copy before a verified second copy exists, and
  never removes RAW/source evidence outside the narrow, verified,
  already-superseded-duplicate cleanup this ADR defines.
