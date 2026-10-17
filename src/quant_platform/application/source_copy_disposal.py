"""Application service: dispose of one physical copy of a raw source archive.

ADR-0049 Amendment 1 (issue #423), Decisions 4-8.  One copy of a provider-native
source archive may be removed only while another independent copy, in a different
storage root, was freshly re-hashed equal, every canonical partition fed by the
archive is ``valid`` with its installed bytes re-hashed equal to the catalog, and a
K06 assessment computed over the surviving copy alone is ``PROTECTED``.

Guarded order (ADR-0049 section 5): observe -> evaluate the pure decision ->
persist the decision audit **before** touching bytes -> make the evidence durable ->
re-verify -> delete exactly the named path -> persist the result.  A refusal or a
failure never deletes; the ingest is never involved.

The ``audit_store`` must make ``record_decision`` durable before it returns (the operator
tool opens its PostgreSQL connection with autocommit for exactly this reason): the
delete that follows relies on it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from ..data.models import DatasetIdentity, Instant
from ..data.parquet import resolve_partition_path
from ..operations.protection import (
    AcceptedReconstructionContract,
    ArtifactProtectionIdentity,
    ArtifactReadOutcome,
    ArtifactVerificationEvidence,
    ProtectionUnitIdentity,
    assess_protection,
)
from ..operations.retention import (
    DeletionCandidateV1,
    PreservationClass,
    RelocationEvidenceRef,
    RetentionDeletionDecision,
)
from ..operations.source_copy_disposal import (
    SOURCE_COPY_DISPOSAL_DECISION_KIND,
    CandidateObservationV1,
    CanonicalPartitionVerificationV1,
    SourceCopyDisposalDecisionV1,
    SourceCopyDisposalPolicyV1,
    SurvivingCopyEvidenceV1,
    evaluate_source_copy_disposal,
)
from .retention_deletion import (
    RetentionDeletionAuditStore,
    RetentionDeletionCrashPoint,
    RetentionDeletionInterrupted,
)

SOURCE_ARCHIVE_ROLE = "source_archive"
SOURCE_ARCHIVE_SEMANTICS_ID = "bybit-linear-ob200-archive-v1"
SOURCE_ARCHIVE_MAPPING_ID = "l2-book-event-v1"
SOURCE_ARCHIVE_RECONSTRUCTION_CONTRACT_ID = "adr-0070-amendment-2-day-ingest"
SOURCE_ARCHIVE_ACCEPTING_AUTHORITY = "ADR-0049-Amendment-1"
SOURCE_ARCHIVE_ACCEPTED_AT = "2026-10-17T00:00:00Z"
_CHUNK = 1024 * 1024


class SourceCopyDisposalApplicationError(RuntimeError):
    """The disposal request is malformed or cannot be evaluated safely."""


class SourceCopyDisposalOutcome(StrEnum):
    REMOVED = "removed"
    ALREADY_ABSENT = "already_absent"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceCopyLocation:
    """One physical file: a storage root directory and a path relative to it."""

    storage_root_id: str
    storage_root: Path
    rel_path: str

    @property
    def path(self) -> Path:
        root = Path(self.storage_root).resolve()
        path = (root / self.rel_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SourceCopyDisposalApplicationError("copy path escapes its storage root") from exc
        return path


@dataclass(frozen=True, slots=True)
class CanonicalPartitionRef:
    """A canonical partition fed by the archive, as recorded in the catalog."""

    partition_key: str
    revision: int
    catalog_partition_id: str
    catalog_state: str
    catalog_content_sha256: str
    catalog_byte_size: int
    artifact_path: Path

    @property
    def coverage_manifest_path(self) -> Path:
        return Path(self.artifact_path).parent / "coverage-manifest.json"


@dataclass(frozen=True, slots=True)
class SourceCopyDisposalResult:
    """Outcome of one disposal request.  Duck-compatible with the K09 audit stores."""

    outcome: SourceCopyDisposalOutcome
    decision_identity: str | None
    decision: RetentionDeletionDecision | None
    refusal_reasons: tuple[str, ...]
    deleted: bool
    deleted_at: Instant | None
    deleted_paths: tuple[str, ...]
    failure_reason: str | None = None
    detail: str | None = None

    @property
    def deletion_decision_id(self) -> str | None:
        return self.decision_identity

    def stable_dict(self) -> dict[str, Any]:
        return {
            "deletion_decision_id": self.decision_identity,
            "decision": None if self.decision is None else self.decision.value,
            "outcome": self.outcome.value,
            "refusal_reasons": list(self.refusal_reasons),
            "deleted": self.deleted,
            "deleted_at": None if self.deleted_at is None else self.deleted_at.isoformat(),
            "deleted_paths": list(self.deleted_paths),
            "failure_reason": self.failure_reason,
            "detail": self.detail,
            "tombstone_or_catalog_update_ref": None,
        }


def l2_raw_dataset_identity(instrument: str, venue: str = "bybit") -> DatasetIdentity:
    """The ``raw`` dataset identity a Bybit ``ob200`` source archive belongs to."""

    return DatasetIdentity("raw", "l2", venue, instrument, "l2-book-event-v1")


def l2_canonical_dataset_identity(instrument: str, venue: str = "bybit") -> DatasetIdentity:
    """The canonical L2 dataset identity whose partitions an archive feeds."""

    return DatasetIdentity("canonical", "l2", venue, instrument, "l2-book-event-v1")


def dispose_source_copy(
    *,
    dataset_identity: DatasetIdentity,
    candidate: SourceCopyLocation,
    surviving: SourceCopyLocation,
    candidate_sha256: str,
    candidate_byte_size: int,
    canonical_partitions: Sequence[CanonicalPartitionRef],
    authority: str,
    decision_time: Instant | str,
    producer: str,
    code_ref: str,
    finalized_at: Instant | str,
    audit_store: RetentionDeletionAuditStore,
    policy: SourceCopyDisposalPolicyV1 | None = None,
    relocation_evidence: RelocationEvidenceRef | None = None,
    clock: Callable[[], Instant] | None = None,
    flush: Callable[[Path], None] | None = None,
    crash_after: RetentionDeletionCrashPoint | str | None = None,
) -> SourceCopyDisposalResult:
    """Evaluate and, only when permitted, remove exactly one source copy."""

    policy = policy or SourceCopyDisposalPolicyV1()
    clock = clock or _system_clock
    flush = flush or _flush_file
    crash_point = None if crash_after is None else RetentionDeletionCrashPoint(crash_after)
    decision_time = Instant.parse(decision_time)
    partitions = tuple(canonical_partitions)
    candidate_path = candidate.path
    surviving_path = surviving.path

    if not candidate_path.exists():
        return SourceCopyDisposalResult(
            outcome=SourceCopyDisposalOutcome.ALREADY_ABSENT,
            decision_identity=None,
            decision=None,
            refusal_reasons=(),
            deleted=False,
            deleted_at=None,
            deleted_paths=(),
            detail="the candidate copy is not on disk; nothing was touched",
        )

    candidate_record = DeletionCandidateV1(
        dataset_identity=dataset_identity,
        storage_root_id=candidate.storage_root_id,
        rel_path=candidate.rel_path,
        content_sha256=candidate_sha256,
        byte_size=candidate_byte_size,
        lifecycle_state="source_acquired",
        producer=producer,
        code_ref=code_ref,
        finalized_at=Instant.parse(finalized_at),
        preservation_class=PreservationClass.UNIQUE_SOURCE,
    )

    observed_candidate = _observe_file(candidate_path)
    candidate_observation = (
        None
        if observed_candidate is None
        else CandidateObservationV1(observed_candidate[0], observed_candidate[1], clock())
    )

    observed_surviving = _observe_file(surviving_path)
    surviving_evidence = None
    if observed_surviving is not None:
        surviving_evidence = SurvivingCopyEvidenceV1(
            storage_root_id=surviving.storage_root_id,
            rel_path=surviving.rel_path,
            content_sha256=observed_surviving[0],
            byte_size=observed_surviving[1],
            verified_at=clock(),
            is_same_file_as_candidate=_same_file(candidate_path, surviving_path),
        )

    verifications = tuple(_verify_partition(item, candidate_sha256, clock) for item in partitions)
    assessment = _assess_surviving_copy(
        candidate_sha256, candidate_byte_size, surviving, surviving_path, observed_surviving,
        authority, clock(), producer,
    )

    decision = evaluate_source_copy_disposal(
        policy=policy,
        candidate=candidate_record,
        decision_time=decision_time,
        decision_actor_or_authority=authority,
        candidate_observation=candidate_observation,
        surviving_copy=surviving_evidence,
        canonical_verifications=verifications,
        protection_assessment=assessment,
        relocation_evidence=relocation_evidence,
    )

    audit_store.record_decision(decision)
    _maybe_interrupt(crash_point, RetentionDeletionCrashPoint.AFTER_DECISION_PERSISTED)

    if decision.decision != RetentionDeletionDecision.PERMITTED:
        return _record(
            audit_store,
            SourceCopyDisposalResult(
                outcome=SourceCopyDisposalOutcome.REFUSED,
                decision_identity=decision.decision_identity,
                decision=decision.decision,
                refusal_reasons=tuple(reason.value for reason in decision.refusal_reasons),
                deleted=False,
                deleted_at=None,
                deleted_paths=(),
            ),
        )

    # Durability, then re-verification, both after the audit and before the delete.
    to_flush = [surviving_path, *(Path(item.artifact_path) for item in partitions)]
    try:
        for path in to_flush:
            flush(path)
    except OSError as exc:
        return _failed(audit_store, decision, f"flush_failed:{type(exc).__name__}", "evidence could not be made durable")

    stale = _reverify(candidate_path, candidate_sha256, candidate_byte_size, surviving_path, partitions)
    if stale is not None:
        return _failed(audit_store, decision, stale, "evidence changed after the decision; nothing was deleted")

    try:
        candidate_path.unlink()
    except OSError as exc:
        return _failed(audit_store, decision, f"unlink_failed:{type(exc).__name__}", str(exc))
    _maybe_interrupt(crash_point, RetentionDeletionCrashPoint.AFTER_BYTES_DELETED)

    return _record(
        audit_store,
        SourceCopyDisposalResult(
            outcome=SourceCopyDisposalOutcome.REMOVED,
            decision_identity=decision.decision_identity,
            decision=decision.decision,
            refusal_reasons=(),
            deleted=True,
            deleted_at=clock(),
            deleted_paths=(str(candidate_path),),
        ),
    )


def recover_source_copy_disposal_result(
    *,
    decision_document: Mapping[str, Any],
    candidate: SourceCopyLocation,
    audit_store: RetentionDeletionAuditStore,
    deleted_at: Instant | str,
    surviving: SourceCopyLocation | None = None,
) -> SourceCopyDisposalResult:
    """Record the result after a crash removed the exact bytes of a permitted decision.

    ``decision_document`` is the persisted audit document of the decision (the removed
    bytes can no longer be observed, so the decision cannot be re-evaluated).  Nothing is
    removed here; the result is recorded against the *existing* decision identity.
    """

    if decision_document.get("decision_kind") != SOURCE_COPY_DISPOSAL_DECISION_KIND:
        raise SourceCopyDisposalApplicationError("the document is not a source-copy-disposal decision")
    if decision_document.get("decision") != RetentionDeletionDecision.PERMITTED.value:
        raise SourceCopyDisposalApplicationError("only a permitted decision can be recovered as removed")
    decision_id = decision_document.get("deletion_decision_id")
    named = decision_document.get("candidate") or {}
    if not decision_id or (named.get("storage_root_id"), named.get("rel_path")) != (
        candidate.storage_root_id,
        candidate.rel_path.replace("\\", "/"),
    ):
        raise SourceCopyDisposalApplicationError("the decision does not name this candidate")
    candidate_path = candidate.path
    if candidate_path.exists():
        raise SourceCopyDisposalApplicationError("candidate still exists; run the disposal instead of recovery")
    detail = "result recorded after recovery against the existing decision"
    if surviving is not None:
        intact = _observe_file(surviving.path) == (named.get("content_sha256"), named.get("byte_size"))
        detail += f"; surviving copy {'verified' if intact else 'NOT verified'} at recovery"
    return _record(
        audit_store,
        SourceCopyDisposalResult(
            outcome=SourceCopyDisposalOutcome.REMOVED,
            decision_identity=str(decision_id),
            decision=RetentionDeletionDecision.PERMITTED,
            refusal_reasons=(),
            deleted=True,
            deleted_at=Instant.parse(deleted_at),
            deleted_paths=(str(candidate_path),),
            detail=detail,
        ),
    )


def read_catalog_canonical_partitions(
    connection: Any,
    dataset_identity: DatasetIdentity,
    partition_keys: Sequence[str],
    *,
    path_resolver: Callable[[str, str, str], Any] = resolve_partition_path,
) -> tuple[CanonicalPartitionRef, ...]:
    """Read the named partitions (latest revision each) of one canonical dataset."""

    keys = tuple(partition_keys)
    if not keys:
        raise SourceCopyDisposalApplicationError("at least one canonical partition must be named")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (p.partition_key)
                   p.partition_id::text, p.partition_key, p.revision, p.state,
                   p.content_sha256, p.byte_size, p.rel_path, sr.abs_path, d.rel_root
              FROM catalog.partitions p
              JOIN catalog.datasets d ON d.dataset_id = p.dataset_id
              JOIN catalog.storage_roots sr ON sr.storage_root_id = p.storage_root_id
             WHERE d.layer = %s AND d.kind = %s AND d.venue = %s
               AND d.instrument = %s AND d.schema_id = %s
               AND p.partition_key = ANY(%s)
             ORDER BY p.partition_key, p.revision DESC
            """,
            (
                dataset_identity.layer, dataset_identity.dataset_kind, dataset_identity.venue,
                dataset_identity.instrument, dataset_identity.record_schema_id, list(keys),
            ),
        )
        rows = cursor.fetchall()
    found = {row[1]: row for row in rows}
    missing = sorted(set(keys) - set(found))
    if missing:
        raise SourceCopyDisposalApplicationError("partition(s) not in the catalog: " + ", ".join(missing))
    refs = []
    for key in keys:
        partition_id, _, revision, state, content_sha256, byte_size, rel_path, abs_path, rel_root = found[key]
        if content_sha256 is None:
            raise SourceCopyDisposalApplicationError(f"partition {key} has no sealed content hash")
        refs.append(
            CanonicalPartitionRef(
                partition_key=key,
                revision=int(revision),
                catalog_partition_id=str(partition_id),
                catalog_state=str(state),
                catalog_content_sha256=str(content_sha256),
                catalog_byte_size=int(byte_size),
                artifact_path=Path(path_resolver(abs_path, rel_root, rel_path)),
            )
        )
    return tuple(refs)


# --------------------------------------------------------------------------- internals


def _verify_partition(
    ref: CanonicalPartitionRef, archive_sha256: str, clock: Callable[[], Instant]
) -> CanonicalPartitionVerificationV1:
    observed = _observe_file(Path(ref.artifact_path))
    return CanonicalPartitionVerificationV1(
        partition_key=ref.partition_key,
        revision=ref.revision,
        catalog_partition_id=ref.catalog_partition_id,
        catalog_state=ref.catalog_state,
        catalog_content_sha256=ref.catalog_content_sha256,
        catalog_byte_size=ref.catalog_byte_size,
        observed_content_sha256=None if observed is None else observed[0],
        observed_byte_size=None if observed is None else observed[1],
        binds_source_archive=_binds_archive(ref.coverage_manifest_path, archive_sha256),
        verified_at=clock(),
    )


def _assess_surviving_copy(
    candidate_sha256: str,
    candidate_byte_size: int,
    surviving: SourceCopyLocation,
    surviving_path: Path,
    observed: tuple[str, int] | None,
    authority: str,
    verified_at: Instant,
    verifier: str,
):
    """K06 assessment computed over the surviving copy alone (Decision 4.3)."""

    unit = ProtectionUnitIdentity(
        source_semantics_id=SOURCE_ARCHIVE_SEMANTICS_ID,
        mapping_id=SOURCE_ARCHIVE_MAPPING_ID,
        reconstruction_contract_id=SOURCE_ARCHIVE_RECONSTRUCTION_CONTRACT_ID,
        protected_support=f"source-archive:{candidate_sha256}",
        artifacts=(ArtifactProtectionIdentity(SOURCE_ARCHIVE_ROLE, candidate_sha256, candidate_byte_size),),
        accepted_contract=AcceptedReconstructionContract(
            source_semantics_id=SOURCE_ARCHIVE_SEMANTICS_ID,
            mapping_id=SOURCE_ARCHIVE_MAPPING_ID,
            reconstruction_contract_id=SOURCE_ARCHIVE_RECONSTRUCTION_CONTRACT_ID,
            required_roles=(SOURCE_ARCHIVE_ROLE,),
            accepting_authority_id=SOURCE_ARCHIVE_ACCEPTING_AUTHORITY,
            accepted_at=Instant.parse(SOURCE_ARCHIVE_ACCEPTED_AT),
        ),
    )
    if observed is None:
        evidence = ArtifactVerificationEvidence(
            SOURCE_ARCHIVE_ROLE, surviving.storage_root_id,
            ArtifactReadOutcome.ABSENT if not surviving_path.exists() else ArtifactReadOutcome.UNREADABLE,
        )
    else:
        evidence = ArtifactVerificationEvidence(
            SOURCE_ARCHIVE_ROLE, surviving.storage_root_id, ArtifactReadOutcome.READ,
            observed_content_hash_sha256=observed[0], observed_size_bytes=observed[1],
        )
    return assess_protection(unit, (evidence,), verified_at=verified_at, verifier_identity=verifier or authority)


def _reverify(
    candidate_path: Path,
    candidate_sha256: str,
    candidate_byte_size: int,
    surviving_path: Path,
    partitions: Sequence[CanonicalPartitionRef],
) -> str | None:
    if _observe_file(candidate_path) != (candidate_sha256, candidate_byte_size):
        return "candidate_changed_after_decision"
    if _observe_file(surviving_path) != (candidate_sha256, candidate_byte_size):
        return "surviving_copy_changed_after_decision"
    for ref in partitions:
        if _observe_file(Path(ref.artifact_path)) != (ref.catalog_content_sha256, ref.catalog_byte_size):
            return f"canonical_bytes_changed_after_decision:{ref.partition_key}"
    return None


def _observe_file(path: Path) -> tuple[str, int] | None:
    digest = hashlib.sha256()
    size = 0
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return None
    return digest.hexdigest(), size


def _binds_archive(coverage_manifest_path: Path, archive_sha256: str) -> bool:
    try:
        document = json.loads(Path(coverage_manifest_path).read_text(encoding="utf-8"))
        marker = f"sha256:{archive_sha256}"
        return any(
            item.get("kind") == "archive_completeness" and marker in str(item.get("detail", ""))
            for assertion in document.get("assertions", [])
            for item in assertion.get("evidence", [])
        )
    except (OSError, ValueError, AttributeError):
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _flush_file(path: Path) -> None:
    with Path(path).open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        fd = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _system_clock() -> Instant:
    return Instant(time.time_ns())


def _record(audit_store: RetentionDeletionAuditStore, result: SourceCopyDisposalResult) -> SourceCopyDisposalResult:
    audit_store.record_result(result)  # type: ignore[arg-type]
    return result


def _failed(
    audit_store: RetentionDeletionAuditStore,
    decision: SourceCopyDisposalDecisionV1,
    reason: str,
    detail: str,
) -> SourceCopyDisposalResult:
    return _record(
        audit_store,
        SourceCopyDisposalResult(
            outcome=SourceCopyDisposalOutcome.FAILED,
            decision_identity=decision.decision_identity,
            decision=decision.decision,
            refusal_reasons=(),
            deleted=False,
            deleted_at=None,
            deleted_paths=(),
            failure_reason=reason,
            detail=detail,
        ),
    )


def _maybe_interrupt(crash_point: RetentionDeletionCrashPoint | None, point: RetentionDeletionCrashPoint) -> None:
    if crash_point == point:
        raise RetentionDeletionInterrupted(point.value)


__all__ = [
    "CanonicalPartitionRef",
    "SourceCopyDisposalApplicationError",
    "SourceCopyDisposalOutcome",
    "SourceCopyDisposalResult",
    "SourceCopyLocation",
    "dispose_source_copy",
    "l2_canonical_dataset_identity",
    "l2_raw_dataset_identity",
    "read_catalog_canonical_partitions",
    "recover_source_copy_disposal_result",
]
