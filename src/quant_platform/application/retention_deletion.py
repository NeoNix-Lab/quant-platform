"""Application composer for K09 retention/deletion authority v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol

from ..data.models import Instant
from ..operations.retention import (
    RetentionDeletionDecision,
    RetentionDeletionDecisionV1,
    RetentionDeletionError,
)


class RetentionDeletionCrashPoint(StrEnum):
    AFTER_DECISION_PERSISTED = "after_decision_persisted"
    AFTER_BYTES_DELETED = "after_bytes_deleted"


class RetentionDeletionInterrupted(RuntimeError):
    """Test seam used to prove the action order is restart-safe."""


class RetentionDeletionApplicationError(RuntimeError):
    """K09 application action could not safely complete."""


class RetentionDeletionAuditStore(Protocol):
    def record_decision(self, decision: RetentionDeletionDecisionV1) -> None:
        ...

    def record_result(self, result: "RetentionDeletionApplicationResult") -> None:
        ...


@dataclass(frozen=True, slots=True)
class RetentionDeletionApplicationResult:
    deletion_decision_id: str
    decision: RetentionDeletionDecision
    deleted: bool
    deleted_at: Instant | None
    deleted_paths: tuple[str, ...]
    tombstone_or_catalog_update_ref: str | None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "deletion_decision_id": self.deletion_decision_id,
            "decision": self.decision.value,
            "deleted": self.deleted,
            "deleted_at": None if self.deleted_at is None else self.deleted_at.isoformat(),
            "deleted_paths": list(self.deleted_paths),
            "tombstone_or_catalog_update_ref": self.tombstone_or_catalog_update_ref,
        }


class InMemoryRetentionDeletionAuditStore:
    """Small deterministic audit store for application tests."""

    def __init__(self) -> None:
        self.decisions: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}

    def record_decision(self, decision: RetentionDeletionDecisionV1) -> None:
        self.decisions[decision.decision_identity] = decision.stable_dict()

    def record_result(self, result: RetentionDeletionApplicationResult) -> None:
        self.results[result.deletion_decision_id] = result.stable_dict()


class PostgresRetentionDeletionAuditStore:
    """Concrete K09 audit store bound to one PostgreSQL connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def record_decision(self, decision: RetentionDeletionDecisionV1) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.deletion_audit_records (
                    deletion_decision_id, decision, candidate, decision_document,
                    application_result, deleted_at, updated_at
                )
                VALUES (
                    %(deletion_decision_id)s, %(decision)s,
                    %(candidate)s::jsonb, %(decision_document)s::jsonb,
                    NULL, NULL, now()
                )
                ON CONFLICT (deletion_decision_id) DO UPDATE SET
                    decision = EXCLUDED.decision,
                    candidate = EXCLUDED.candidate,
                    decision_document = EXCLUDED.decision_document,
                    updated_at = now()
                """,
                {
                    "deletion_decision_id": decision.decision_identity,
                    "decision": decision.decision.value,
                    "candidate": json.dumps(decision.candidate.stable_dict(), sort_keys=True),
                    "decision_document": json.dumps(decision.stable_dict(), sort_keys=True),
                },
            )

    def record_result(self, result: RetentionDeletionApplicationResult) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE catalog.deletion_audit_records
                   SET application_result = %(application_result)s::jsonb,
                       deleted_at = %(deleted_at)s,
                       updated_at = now()
                 WHERE deletion_decision_id = %(deletion_decision_id)s
                """,
                {
                    "deletion_decision_id": result.deletion_decision_id,
                    "application_result": json.dumps(result.stable_dict(), sort_keys=True),
                    "deleted_at": None if result.deleted_at is None else result.deleted_at.to_datetime(),
                },
            )
            if cursor.rowcount != 1:
                raise RetentionDeletionApplicationError("deletion decision audit row was not persisted")


def execute_retention_deletion(
    *,
    decision: RetentionDeletionDecisionV1,
    storage_root: str | Path,
    audit_store: RetentionDeletionAuditStore,
    deleted_at: Instant | str,
    tombstone_or_catalog_update_ref: str,
    crash_after: RetentionDeletionCrashPoint | str | None = None,
) -> RetentionDeletionApplicationResult:
    """Persist a K09 decision and, only when permitted, delete its exact bytes."""

    if not isinstance(decision, RetentionDeletionDecisionV1):
        raise RetentionDeletionApplicationError("decision must be RetentionDeletionDecisionV1")
    deleted_at_instant = Instant.parse(deleted_at)
    crash_point = None if crash_after is None else RetentionDeletionCrashPoint(crash_after)
    audit_store.record_decision(decision)
    _maybe_interrupt(crash_point, RetentionDeletionCrashPoint.AFTER_DECISION_PERSISTED)

    if decision.decision != RetentionDeletionDecision.PERMITTED:
        result = RetentionDeletionApplicationResult(
            deletion_decision_id=decision.decision_identity,
            decision=decision.decision,
            deleted=False,
            deleted_at=None,
            deleted_paths=(),
            tombstone_or_catalog_update_ref=None,
        )
        audit_store.record_result(result)
        return result

    candidate_path = _candidate_path(storage_root, decision)
    _verify_candidate_file(candidate_path, decision)
    candidate_path.unlink()
    _maybe_interrupt(crash_point, RetentionDeletionCrashPoint.AFTER_BYTES_DELETED)
    result = RetentionDeletionApplicationResult(
        deletion_decision_id=decision.decision_identity,
        decision=decision.decision,
        deleted=True,
        deleted_at=deleted_at_instant,
        deleted_paths=(str(candidate_path),),
        tombstone_or_catalog_update_ref=_non_empty_text(tombstone_or_catalog_update_ref, "tombstone_or_catalog_update_ref"),
    )
    audit_store.record_result(result)
    return result


def recover_retention_deletion_result(
    *,
    decision: RetentionDeletionDecisionV1,
    storage_root: str | Path,
    audit_store: RetentionDeletionAuditStore,
    deleted_at: Instant | str,
    tombstone_or_catalog_update_ref: str,
) -> RetentionDeletionApplicationResult:
    """Record the result after a prior crash deleted the exact candidate bytes."""

    if decision.decision != RetentionDeletionDecision.PERMITTED:
        raise RetentionDeletionApplicationError("only permitted deletions can be recovered as deleted")
    candidate_path = _candidate_path(storage_root, decision)
    if candidate_path.exists():
        raise RetentionDeletionApplicationError("candidate still exists; execute deletion instead of recovery")
    result = RetentionDeletionApplicationResult(
        deletion_decision_id=decision.decision_identity,
        decision=decision.decision,
        deleted=True,
        deleted_at=Instant.parse(deleted_at),
        deleted_paths=(str(candidate_path),),
        tombstone_or_catalog_update_ref=_non_empty_text(tombstone_or_catalog_update_ref, "tombstone_or_catalog_update_ref"),
    )
    audit_store.record_result(result)
    return result


def _candidate_path(storage_root: str | Path, decision: RetentionDeletionDecisionV1) -> Path:
    root = Path(storage_root)
    path = (root / decision.candidate.rel_path).resolve()
    root_resolved = root.resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise RetentionDeletionApplicationError("candidate path escapes storage_root") from exc
    return path


def _verify_candidate_file(path: Path, decision: RetentionDeletionDecisionV1) -> None:
    if not path.is_file():
        raise RetentionDeletionApplicationError("candidate file is absent")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != decision.candidate.content_sha256 or size != decision.candidate.byte_size:
        raise RetentionDeletionApplicationError("candidate file identity does not match the permitted decision")


def _maybe_interrupt(crash_point: RetentionDeletionCrashPoint | None, point: RetentionDeletionCrashPoint) -> None:
    if crash_point == point:
        raise RetentionDeletionInterrupted(point.value)


def _non_empty_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetentionDeletionError(f"{field} must be a non-empty string")
    return value.strip()


__all__ = [
    "InMemoryRetentionDeletionAuditStore",
    "PostgresRetentionDeletionAuditStore",
    "RetentionDeletionApplicationError",
    "RetentionDeletionApplicationResult",
    "RetentionDeletionAuditStore",
    "RetentionDeletionCrashPoint",
    "RetentionDeletionInterrupted",
    "execute_retention_deletion",
    "recover_retention_deletion_result",
]
