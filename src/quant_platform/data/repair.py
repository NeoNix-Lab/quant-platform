"""A10 backfill/repair v1: evidence-driven reconciliation over explicit defects.

This module owns only the bounded runtime foundation frozen by the A10
mandate: deterministic repair-intent identity, immutable isolated candidate
attempts, staging isolation sufficient to stop concurrent attempts clobbering
each other, and one dedicated atomic compare-and-cutover transaction that
reuses the existing A16 (:mod:`quality_lifecycle`) and A09/S14
(:mod:`publication_eligibility_catalog`) transaction-scoped seams.

It deliberately does not own acquisition/download, scheduling, A11 live-cursor
semantics, or deletion.  It never imports ``quant_platform.access``: B04
gap/coverage evidence is supplied by the caller as already-frozen
``CoverageInterval`` values from :mod:`quant_platform.data.models`.

Replacement repair cannot reuse the ordinary S13 admission path
(:class:`~quant_platform.data.publication_catalog.CatalogPublicationWriter`)
because that path supersedes the predecessor and commits before quality or
publication evidence is verified.  A10 instead supersedes the predecessor and
admits the candidate as ``closed`` inside its own transaction, then calls the
A16 and S14 transaction-scoped seams against that same connection, and only
commits if both accept the exact final revision -- so a failed candidate never
leaves a half cutover and the predecessor stays authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity
from .quality_lifecycle import QualityLifecycleCatalog, QualityLifecycleRefusal
from .publication_eligibility_catalog import (
    PublicationEligibilityCatalog,
    PublicationEligibilityRefusal,
)


REPAIR_SEMANTICS_VERSION = "quant-platform/a10-repair-v1"

# Explicit outcome vocabulary (frozen contract section 13).
REPAIR_REQUIRED = "REPAIR_REQUIRED"
CANDIDATE_PENDING = "CANDIDATE_PENDING"
CONVERGED = "CONVERGED"
ALREADY_SATISFIED = "ALREADY_SATISFIED"
FAILED = "FAILED"
STALE_CONFLICT = "STALE_CONFLICT"

_OUTCOMES = frozenset({
    REPAIR_REQUIRED, CANDIDATE_PENDING, CONVERGED,
    ALREADY_SATISFIED, FAILED, STALE_CONFLICT,
})

_ELIGIBLE_COVERAGE_STATES = frozenset({"closed", "valid", "degraded"})
_QUALITY_APPLICABLE_STATES = frozenset({"valid", "degraded"})


class RepairRefusal(RuntimeError):
    """Raised when repair evidence or topology cannot authorize an operation."""


# ---------------------------------------------------------------------------
# Trigger evidence (frozen contract sections 1-3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredecessorRef:
    """Immutable identity of the exact predecessor a trigger was captured against."""

    revision: int
    content_sha256: str
    state: str

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise RepairRefusal("predecessor revision must be positive")
        if not isinstance(self.content_sha256, str) or len(self.content_sha256.strip()) != 64:
            raise RepairRefusal("predecessor content_sha256 must be a SHA-256 hex digest")
        if self.state not in {"closed", "valid", "degraded", "invalid"}:
            raise RepairRefusal(f"predecessor state {self.state!r} is not a repair-eligible state")

    def stable_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "content_sha256": self.content_sha256.strip(), "state": self.state}


@dataclass(frozen=True, slots=True)
class CoverageGapTrigger:
    """B04 evidence: one or more exact non-empty gaps inside required support.

    ``predecessor`` is set only when the partition_key already carries a
    non-superseded row assessed ``invalid`` (contributing zero eligible
    coverage); it is ``None`` when no admitted revision exists at all, i.e.
    genuine missing-support backfill.
    """

    required: CoverageInterval
    gaps: tuple[CoverageInterval, ...]
    predecessor: PredecessorRef | None = None

    def __post_init__(self) -> None:
        if not self.gaps:
            raise RepairRefusal("a coverage-gap trigger requires at least one exact gap")
        for gap in self.gaps:
            if gap.start < self.required.start or gap.end > self.required.end:
                raise RepairRefusal("gap evidence must lie inside the required support interval")

    @property
    def kind(self) -> str:
        return "missing_support"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "required": self.required.stable_dict(),
            "gaps": [gap.stable_dict() for gap in self.gaps],
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
        }


@dataclass(frozen=True, slots=True)
class InvalidRevisionTrigger:
    """A16 evidence: current authoritative assessment is ``fail`` / ``invalid``."""

    predecessor: PredecessorRef
    assessment_signature: str
    assessment_status: str

    def __post_init__(self) -> None:
        if self.predecessor.state != "invalid":
            raise RepairRefusal("invalid-revision trigger requires an invalid predecessor state")
        if self.assessment_status != "fail":
            raise RepairRefusal("invalid-revision trigger requires a fail assessment status")
        if not isinstance(self.assessment_signature, str) or not self.assessment_signature.strip():
            raise RepairRefusal("invalid-revision trigger requires a non-empty assessment signature")

    @property
    def kind(self) -> str:
        return "invalid_revision"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "predecessor": self.predecessor.stable_dict(),
            "assessment_signature": self.assessment_signature.strip(),
            "assessment_status": self.assessment_status,
        }


RepairTrigger = CoverageGapTrigger | InvalidRevisionTrigger


# ---------------------------------------------------------------------------
# Repair intent identity (frozen contract sections 1, 3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairIntent:
    """A deterministic, partition-bounded repair obligation.

    Distinct ``DatasetIdentity`` + ``partition_key`` pairs always yield
    distinct intents (section: "multi-partition B04 gap results project into
    independent partition-bounded repair intents").
    """

    dataset_identity: DatasetIdentity
    partition_key: str
    trigger: RepairTrigger
    repair_semantics_version: str = REPAIR_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.partition_key, str) or not self.partition_key.strip():
            raise RepairRefusal("repair intent requires a non-empty partition_key")
        if not isinstance(self.repair_semantics_version, str) or not self.repair_semantics_version.strip():
            raise RepairRefusal("repair intent requires a non-empty repair_semantics_version")

    @property
    def predecessor(self) -> PredecessorRef | None:
        return self.trigger.predecessor

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "trigger": self.trigger.stable_dict(),
            "repair_semantics_version": self.repair_semantics_version,
        }

    @property
    def intent_id(self) -> str:
        return _canonical_hash("a10-repair-intent-v1", self.stable_dict())


# ---------------------------------------------------------------------------
# Candidate attempts (frozen contract sections 5-6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """One immutable, isolated repair candidate attempt.

    Binds every input the acceptance seams (A16/S14) will independently
    re-verify.  Two attempts with identical evidence share one
    ``candidate_id`` (idempotent retry); any differing durable/semantic
    evidence produces a distinct id (frozen contract section 6).
    """

    repair_intent_id: str
    natural_identity: NaturalPartitionIdentity
    content_sha256: str
    partition_manifest_sha256: str
    source_evidence_id: str
    materialization_id: str
    coverage_evidence_id: str
    quality_evidence_id: str
    code_ref: str

    def __post_init__(self) -> None:
        for name in ("content_sha256", "partition_manifest_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value.strip()) != 64:
                raise RepairRefusal(f"candidate {name} must be a SHA-256 hex digest")
        for name in ("repair_intent_id", "source_evidence_id", "materialization_id",
                     "coverage_evidence_id", "quality_evidence_id", "code_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RepairRefusal(f"candidate {name} must be a non-empty string")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "repair_intent_id": self.repair_intent_id,
            "natural_identity": self.natural_identity.stable_dict(),
            "content_sha256": self.content_sha256.strip(),
            "partition_manifest_sha256": self.partition_manifest_sha256.strip(),
            "source_evidence_id": self.source_evidence_id.strip(),
            "materialization_id": self.materialization_id.strip(),
            "coverage_evidence_id": self.coverage_evidence_id.strip(),
            "quality_evidence_id": self.quality_evidence_id.strip(),
            "code_ref": self.code_ref.strip(),
        }

    @property
    def candidate_id(self) -> str:
        return _canonical_hash("a10-candidate-attempt-v1", self.stable_dict())


# ---------------------------------------------------------------------------
# Staging isolation (frozen contract section 5; amendment section 1/3).
#
# Physical isolation is operational metadata only: it grants no authority and
# is never a semantic partition identity.  Distinct candidate identities are
# content-addressed to distinct directories so concurrent attempts cannot
# clobber one another; the same candidate identity may be written repeatedly
# only if the bytes are unchanged (idempotent retry).
# ---------------------------------------------------------------------------


class CandidateStagingConflict(RepairRefusal):
    """Raised when two distinct candidate attempts would share physical storage."""


@dataclass(frozen=True, slots=True)
class CandidateStaging:
    """Allocates one physically isolated, content-addressed staging directory per candidate."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    def directory_for(self, candidate: CandidateAttempt) -> Path:
        """Return (creating if necessary) the isolated directory for one candidate.

        The directory name is the candidate's own content-addressed identity,
        so two distinct candidates -- even ones targeting the same nominal
        final revision -- can never resolve to the same path.
        """

        directory = self.root / _filesystem_safe(candidate.candidate_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_evidence(self, candidate: CandidateAttempt, name: str, payload: bytes) -> Path:
        """Durably write one immutable evidence file for a candidate attempt.

        Writing the same bytes for the same (candidate, name) is idempotent.
        Writing different bytes for an existing (candidate, name) is refused:
        durable candidate evidence, once written, is immutable.
        """

        directory = self.directory_for(candidate)
        target = directory / name
        if target.exists():
            existing = target.read_bytes()
            if existing != payload:
                raise CandidateStagingConflict(
                    f"candidate {candidate.candidate_id} evidence {name!r} is immutable "
                    "and cannot be overwritten with different content"
                )
            return target
        temporary = directory / f".{name}.tmp"
        temporary.write_bytes(payload)
        temporary.replace(target)
        return target

    def occupant(self, candidate_id: str) -> str | None:
        """Return the candidate_id physically occupying its directory, if any.

        Since directories are named by ``candidate_id`` itself, occupancy is
        always exactly the requested id or absent; this exists so callers can
        assert isolation held rather than assume it.
        """

        directory = self.root / _filesystem_safe(candidate_id)
        return candidate_id if directory.exists() else None


# ---------------------------------------------------------------------------
# Convergence provenance (frozen contract section 14; amendment section 8).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConvergenceProvenance:
    """Immutable evidence bound to the exact final revision that became authoritative."""

    repair_intent_id: str
    candidate_id: str
    predecessor: PredecessorRef | None
    final_partition_id: str
    final_natural_identity: NaturalPartitionIdentity
    final_partition_manifest_sha256: str
    final_content_sha256: str
    coverage_evidence_id: str
    quality_assessment_signature: str
    quality_assessment_status: str
    publication_state: str
    repair_semantics_version: str = REPAIR_SEMANTICS_VERSION

    def stable_dict(self) -> dict[str, Any]:
        return {
            "repair_intent_id": self.repair_intent_id,
            "candidate_id": self.candidate_id,
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "final_partition_id": self.final_partition_id,
            "final_natural_identity": self.final_natural_identity.stable_dict(),
            "final_partition_manifest_sha256": self.final_partition_manifest_sha256,
            "final_content_sha256": self.final_content_sha256,
            "coverage_evidence_id": self.coverage_evidence_id,
            "quality_assessment_signature": self.quality_assessment_signature,
            "quality_assessment_status": self.quality_assessment_status,
            "publication_state": self.publication_state,
            "repair_semantics_version": self.repair_semantics_version,
        }

    @property
    def provenance_id(self) -> str:
        return _canonical_hash("a10-convergence-provenance-v1", self.stable_dict())


def write_convergence_provenance(path: Path, provenance: ConvergenceProvenance) -> str:
    """Durably write convergence provenance as an immutable, hash-addressed JSON document.

    Returns the sha256 of the written bytes.  Refuses to silently overwrite an
    existing file with different content, matching the immutability the
    frozen contract requires of convergence evidence.
    """

    payload = json.dumps(provenance.stable_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    target = Path(path)
    if target.exists():
        existing = target.read_bytes()
        if existing != payload:
            raise RepairRefusal(f"convergence provenance at {target} is immutable and already differs")
        return hashlib.sha256(existing).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Cutover result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CutoverResult:
    outcome: str
    reason: str | None = None
    provenance: ConvergenceProvenance | None = None

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise RepairRefusal(f"unsupported repair outcome: {self.outcome!r}")
        if self.outcome == CONVERGED and self.provenance is None:
            raise RepairRefusal("a CONVERGED outcome requires convergence provenance")


class _AlreadySatisfiedSignal(Exception):
    pass


class _StaleConflictSignal(Exception):
    pass


# ---------------------------------------------------------------------------
# The dedicated A10 cutover seam (amendment sections 4-9; frozen contract 9-12).
# ---------------------------------------------------------------------------


class RepairCutoverCatalog:
    """Owns the one atomic compare-and-cutover transaction for A10 repair.

    Lock ordering matches S14's own internal order (dataset locks before
    partition-topology locks) to avoid a lock-order-inversion deadlock against
    concurrent ordinary S14 calls.  A16/S14 verification and mutation are
    delegated to their own transaction-scoped seams against this same cursor,
    so a failed candidate rolls back every mutation this transaction made,
    including the predecessor's uncommitted supersession.
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.quality = QualityLifecycleCatalog(connection)
        self.publication = PublicationEligibilityCatalog(connection)

    def cutover(
        self,
        *,
        repair_intent: RepairIntent,
        candidate: CandidateAttempt,
        dataset: Mapping[str, Any],
        dataset_sha256: str,
        candidate_partition: Mapping[str, Any],
        candidate_quality_report: Mapping[str, Any],
        coverage_start: Instant,
        coverage_end: Instant,
        coverage_ids: Sequence[str],
        assertion_ids: Sequence[str],
        coverage_sha256: Sequence[str],
        storage_root_id: str,
        expected_profile: str,
        expected_check_suite: str,
        lifecycle_code_ref: str | None = None,
    ) -> CutoverResult:
        if candidate.repair_intent_id != repair_intent.intent_id:
            raise RepairRefusal("candidate does not target this repair intent")
        target_natural = NaturalPartitionIdentity(
            repair_intent.dataset_identity, repair_intent.partition_key, int(candidate_partition["revision"]),
        )
        if candidate.natural_identity != target_natural:
            raise RepairRefusal("candidate natural identity does not match candidate_partition")
        if candidate_partition.get("state") != "closed":
            raise RepairRefusal("A10 cutover requires a sealed closed candidate partition manifest")
        if _text(candidate_partition.get("sha256")) != candidate.content_sha256.strip():
            raise RepairRefusal("candidate_partition physical hash differs from candidate identity")

        try:
            with self.connection.cursor() as cursor:
                identity = repair_intent.dataset_identity
                # Step 1: lock relevant dataset(s) -- matches S14's own order.
                dataset_row = QualityLifecycleCatalog._resolve_dataset(cursor, identity, dataset, dataset_sha256)
                # Step 2: lock/re-read the natural partition topology.
                topology = QualityLifecycleCatalog._lock_partition_topology(
                    cursor, dataset_row[0], repair_intent.partition_key,
                )
                # Step 3: re-evaluate the repair trigger/current authority under lock.
                predecessor_row = _reevaluate_authority(repair_intent, candidate, topology)

                # Step 5: if replacement, supersede the predecessor (uncommitted).
                if predecessor_row is not None:
                    cursor.execute(
                        """
                        UPDATE catalog.partitions
                           SET state = 'superseded'
                         WHERE partition_id = %s
                           AND state <> 'superseded'
                        RETURNING partition_id::text
                        """,
                        (predecessor_row[0],),
                    )
                    if cursor.fetchone() is None:
                        raise RepairRefusal("predecessor could not be superseded atomically")

                # Step 6: insert/admit candidate under its FINAL natural identity as closed.
                candidate_partition_id = _insert_candidate(
                    cursor, dataset_row[0], repair_intent.partition_key, candidate_partition,
                    coverage_start, coverage_end,
                )
                # The candidate's quality-report CONTENT (status/metrics/violations/
                # code_ref) is immutable evidence prepared before cutover, but its
                # catalog row can only be written now: catalog.quality_reports is
                # keyed by partition_id, which does not exist before this INSERT.
                _insert_quality_report(cursor, candidate_partition_id, expected_check_suite, candidate_quality_report)

                sealed_partition = dict(candidate_partition)
                sealed_partition["_manifest_sha256"] = candidate.partition_manifest_sha256.strip()

                # Step 7: apply the existing A16 lifecycle semantics (tx-scoped seam).
                quality_result = self.quality._apply_partition_lifecycle_tx(
                    cursor,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    partition=sealed_partition,
                    partition_sha256=candidate.partition_manifest_sha256.strip(),
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    coverage_ids=coverage_ids,
                    assertion_ids=assertion_ids,
                    coverage_sha256=coverage_sha256,
                    storage_root_id=storage_root_id,
                    expected_profile=expected_profile,
                    expected_check_suite=expected_check_suite,
                    lifecycle_code_ref=lifecycle_code_ref or REPAIR_SEMANTICS_VERSION,
                )
                if quality_result.resulting_state not in _QUALITY_APPLICABLE_STATES:
                    raise RepairRefusal(
                        f"candidate quality assessment resulted in {quality_result.resulting_state!r}; "
                        "replacement cannot converge"
                    )

                # Step 8: apply the existing A09/S14 publication semantics (tx-scoped seam).
                publication_result = self.publication._publish_tx(
                    cursor,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    partition=sealed_partition,
                    partition_sha256=candidate.partition_manifest_sha256.strip(),
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    coverage_ids=coverage_ids,
                    assertion_ids=assertion_ids,
                    coverage_sha256=coverage_sha256,
                    storage_root_id=storage_root_id,
                    expected_profile=expected_profile,
                    expected_check_suite=expected_check_suite,
                )
                # Step 9: require the resulting state to be valid/degraded.
                if publication_result.state not in _QUALITY_APPLICABLE_STATES:
                    raise RepairRefusal(
                        f"candidate publication eligibility resulted in {publication_result.state!r}; "
                        "replacement cannot converge"
                    )

                predecessor_ref = repair_intent.predecessor
                provenance = ConvergenceProvenance(
                    repair_intent_id=repair_intent.intent_id,
                    candidate_id=candidate.candidate_id,
                    predecessor=predecessor_ref,
                    final_partition_id=candidate_partition_id,
                    final_natural_identity=target_natural,
                    final_partition_manifest_sha256=candidate.partition_manifest_sha256.strip(),
                    final_content_sha256=candidate.content_sha256.strip(),
                    coverage_evidence_id=candidate.coverage_evidence_id.strip(),
                    quality_assessment_signature=quality_result.assessment_signature,
                    quality_assessment_status=quality_result.assessment_status,
                    publication_state=publication_result.state,
                    repair_semantics_version=repair_intent.repair_semantics_version,
                )
            self.connection.commit()
            return CutoverResult(outcome=CONVERGED, provenance=provenance)
        except _AlreadySatisfiedSignal:
            self.connection.rollback()
            return CutoverResult(outcome=ALREADY_SATISFIED)
        except _StaleConflictSignal as exc:
            self.connection.rollback()
            return CutoverResult(outcome=STALE_CONFLICT, reason=str(exc))
        except (RepairRefusal, QualityLifecycleRefusal, PublicationEligibilityRefusal) as exc:
            self.connection.rollback()
            return CutoverResult(outcome=FAILED, reason=str(exc))
        except Exception:
            self.connection.rollback()
            raise


def _insert_candidate(
    cursor: Any,
    dataset_id: str,
    partition_key: str,
    partition: Mapping[str, Any],
    coverage_start: Instant,
    coverage_end: Instant,
) -> str:
    cursor.execute(
        """
        INSERT INTO catalog.partitions (
            dataset_id, partition_key, revision, storage_root_id,
            rel_path, ts_start, ts_end, row_count, byte_size,
            content_sha256, state, manifest_sha256, created_at,
            closed_at, first_sequence, last_sequence, producer, code_ref
        ) VALUES (
            %(dataset_id)s, %(partition_key)s, %(revision)s, %(storage_root_id)s,
            %(rel_path)s, %(ts_start)s, %(ts_end)s, %(row_count)s, %(byte_size)s,
            %(content_sha256)s, 'closed', %(manifest_sha256)s, %(created_at)s,
            %(closed_at)s, %(first_sequence)s, %(last_sequence)s, %(producer)s, %(code_ref)s
        )
        RETURNING partition_id::text
        """,
        {
            "dataset_id": dataset_id,
            "partition_key": partition_key,
            "revision": int(partition["revision"]),
            "storage_root_id": partition["storage_root_id"],
            "rel_path": partition["rel_path"],
            "ts_start": coverage_start.to_datetime(),
            "ts_end": coverage_end.to_datetime(),
            "row_count": partition["row_count"],
            "byte_size": partition["file_size_bytes"],
            "content_sha256": partition["sha256"],
            "manifest_sha256": _manifest_sha(partition),
            "created_at": _timestamp(partition["created_at"]),
            "closed_at": _timestamp(partition["closed_at"]),
            "first_sequence": partition.get("first_sequence"),
            "last_sequence": partition.get("last_sequence"),
            "producer": partition["producer"],
            "code_ref": partition["code_ref"],
        },
    )
    return str(cursor.fetchone()[0])


def _insert_quality_report(
    cursor: Any,
    partition_id: str,
    check_suite: str,
    report: Mapping[str, Any],
) -> None:
    for name in ("status", "metrics", "violations", "code_ref"):
        if name not in report:
            raise RepairRefusal(f"candidate quality report is missing required field {name!r}")
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise RuntimeError("psycopg is required for A10 catalog writes") from exc
    cursor.execute(
        """
        INSERT INTO catalog.quality_reports
            (partition_id, check_suite, status, metrics, violations, code_ref)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            partition_id, check_suite, report["status"],
            Jsonb(dict(report["metrics"])), Jsonb(list(report["violations"])),
            report["code_ref"],
        ),
    )


def _reevaluate_authority(
    repair_intent: RepairIntent,
    candidate: CandidateAttempt,
    topology: Sequence[Any],
) -> Any | None:
    """Re-check current catalog authority under lock; raise the matching signal.

    Returns the locked predecessor row to supersede (or ``None`` for a
    genuine no-predecessor backfill), or raises :class:`_AlreadySatisfiedSignal`
    / :class:`_StaleConflictSignal` to short-circuit the transaction without
    any mutation.
    """

    live = [row for row in topology if row[4] != "superseded"]
    if len(live) > 1:
        raise RepairRefusal("partition topology does not have exactly one live revision")
    live_row = live[0] if live else None
    target_revision = int(candidate.natural_identity.revision)
    target_content = candidate.content_sha256.strip()

    def _is_already_the_candidate(row: Any) -> bool:
        return (
            int(row[3]) == target_revision
            and _text(row[11]) == target_content
            and row[4] in _QUALITY_APPLICABLE_STATES
        )

    predecessor_ref = repair_intent.predecessor

    if predecessor_ref is None:
        # Missing-support backfill: no admitted/eligible revision expected yet.
        if live_row is None:
            return None
        if _is_already_the_candidate(live_row):
            raise _AlreadySatisfiedSignal()
        if live_row[4] in _ELIGIBLE_COVERAGE_STATES:
            raise _StaleConflictSignal(
                "required support is no longer missing: a live eligible revision already exists"
            )
        # A row now exists where the intent expected an empty slot (e.g. it
        # became invalid after this intent was captured).  That is a
        # different eligibility ground than the one this intent was built
        # against (frozen contract section 3): it must re-evaluate to a
        # freshly derived InvalidRevisionTrigger intent, never silently
        # retarget this one.
        raise _StaleConflictSignal(
            f"a revision now occupies the target slot in state {live_row[4]!r}; "
            "re-evaluate as a newly derived repair intent"
        )

    # Replacement repair: a captured predecessor identity/state must still match.
    if live_row is None:
        raise _StaleConflictSignal("predecessor no longer exists in current topology")
    if (
        int(live_row[3]) == predecessor_ref.revision
        and _text(live_row[11]) == predecessor_ref.content_sha256
        and live_row[4] == predecessor_ref.state
    ):
        return live_row
    if _is_already_the_candidate(live_row):
        raise _AlreadySatisfiedSignal()
    raise _StaleConflictSignal(
        "current authority no longer matches the captured predecessor; repair intent must re-evaluate"
    )


# ---------------------------------------------------------------------------
# Pure eligibility evaluation (frozen contract section 2), independent of any
# DB connection so callers can decide REPAIR_REQUIRED vs no-trigger before
# ever constructing a candidate.
# ---------------------------------------------------------------------------


def evaluate_missing_support_eligibility(
    required: CoverageInterval,
    gaps: Sequence[CoverageInterval],
) -> str:
    """REPAIR_REQUIRED if B04 proves at least one non-empty gap, else ALREADY_SATISFIED."""

    return REPAIR_REQUIRED if gaps else ALREADY_SATISFIED


def evaluate_invalid_revision_eligibility(assessment_status: str, live_state: str) -> str:
    """REPAIR_REQUIRED only for an authoritative fail assessment on an invalid live revision.

    ``warn``/``degraded`` is never an automatic trigger (frozen contract
    section 2); neither is ``pass``/``valid`` merely because other evidence is
    newer or different.
    """

    if assessment_status == "fail" and live_state == "invalid":
        return REPAIR_REQUIRED
    return ALREADY_SATISFIED


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def _filesystem_safe(identity: str) -> str:
    """Project a content-addressed identity string to a portable directory name.

    Identities carry ``:`` separators (e.g. ``a10-candidate-attempt-v1:sha256:<hex>``),
    which is not a legal path character on Windows.  The projection is
    injective for the identities this module produces: only the fixed
    ``<domain>:sha256:<hex>`` shape is ever generated, so replacing the
    separator cannot collide two distinct identities.
    """

    return identity.replace(":", "_")


def _canonical_hash(domain: str, payload: Mapping[str, Any]) -> str:
    document = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(f"{domain}\x00{document}".encode("utf-8")).hexdigest()
    return f"{domain}:sha256:{digest}"


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _timestamp(value: Any) -> Any:
    if isinstance(value, str):
        return Instant.parse(value).to_datetime()
    if isinstance(value, Instant):
        return value.to_datetime()
    return value


def _manifest_sha(document: Mapping[str, Any]) -> str:
    value = document.get("_manifest_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise RepairRefusal("durable candidate manifest SHA is missing from catalog write input")
    return value


__all__ = [
    "REPAIR_SEMANTICS_VERSION",
    "REPAIR_REQUIRED",
    "CANDIDATE_PENDING",
    "CONVERGED",
    "ALREADY_SATISFIED",
    "FAILED",
    "STALE_CONFLICT",
    "RepairRefusal",
    "PredecessorRef",
    "CoverageGapTrigger",
    "InvalidRevisionTrigger",
    "RepairTrigger",
    "RepairIntent",
    "CandidateAttempt",
    "CandidateStaging",
    "CandidateStagingConflict",
    "ConvergenceProvenance",
    "write_convergence_provenance",
    "CutoverResult",
    "RepairCutoverCatalog",
    "evaluate_missing_support_eligibility",
    "evaluate_invalid_revision_eligibility",
]
