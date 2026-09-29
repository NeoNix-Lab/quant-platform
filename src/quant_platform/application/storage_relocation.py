"""Application composer for K07 storage-tier relocation v1."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Protocol

from ..data.models import DatasetIdentity
from ..operations.relocation import (
    RelocationError,
    RelocationPhase,
    RelocationPlan,
    RelocationRecordV1,
    RelocationVerificationFailed,
    advance_relocation,
    utc_now_instant,
    verify_target_identity,
)


class RelocationCrashPoint(StrEnum):
    AFTER_PLANNED = "after_planned"
    AFTER_TEMP_COPY = "after_temp_copy"
    AFTER_STAGED = "after_staged"
    AFTER_VERIFIED = "after_verified"
    AFTER_SWITCHED = "after_switched"


class RelocationInterrupted(RuntimeError):
    """Test seam used to prove restart safety after each durable phase."""


class RelocationCatalog(Protocol):
    def load_relocation_record(self, relocation_id: str) -> RelocationRecordV1 | None:
        ...

    def save_relocation_record(self, record: RelocationRecordV1) -> None:
        ...

    def current_storage_root_id(self, catalog_partition_id: str) -> str:
        ...

    def switch_partition_storage_root(
        self,
        *,
        catalog_partition_id: str,
        source_storage_root_id: str,
        target_storage_root_id: str,
    ) -> bool:
        ...


class PostgresRelocationCatalog:
    """Concrete K07 catalog adapter bound to one PostgreSQL connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def load_relocation_record(self, relocation_id: str) -> RelocationRecordV1 | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dataset_identity, catalog_partition_id::text, partition_key, revision,
                       source_storage_root_id, target_storage_root_id, dataset_rel_root,
                       rel_path, expected_content_sha256, expected_size_bytes, phase,
                       target_content_sha256, target_size_bytes, refusal_reason, updated_at
                  FROM catalog.relocation_jobs
                 WHERE relocation_id = %s
                """,
                (relocation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        identity_payload = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        return RelocationRecordV1(
            dataset_identity=DatasetIdentity(**identity_payload),
            catalog_partition_id=row[1],
            partition_key=row[2],
            revision=int(row[3]),
            source_storage_root_id=row[4],
            target_storage_root_id=row[5],
            dataset_rel_root=row[6],
            rel_path=row[7],
            expected_content_sha256=row[8],
            expected_size_bytes=int(row[9]),
            phase=RelocationPhase(row[10]),
            target_content_sha256=row[11],
            target_size_bytes=None if row[12] is None else int(row[12]),
            refusal_reason=row[13],
            updated_at=row[14],
        )

    def save_relocation_record(self, record: RelocationRecordV1) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.relocation_jobs (
                    relocation_id, dataset_identity, catalog_partition_id, partition_key,
                    revision, source_storage_root_id, target_storage_root_id,
                    dataset_rel_root, rel_path, expected_content_sha256,
                    expected_size_bytes, phase, target_content_sha256,
                    target_size_bytes, refusal_reason, updated_at
                )
                VALUES (
                    %(relocation_id)s, %(dataset_identity)s::jsonb, %(catalog_partition_id)s,
                    %(partition_key)s, %(revision)s, %(source_storage_root_id)s,
                    %(target_storage_root_id)s, %(dataset_rel_root)s, %(rel_path)s,
                    %(expected_content_sha256)s, %(expected_size_bytes)s, %(phase)s,
                    %(target_content_sha256)s, %(target_size_bytes)s,
                    %(refusal_reason)s, %(updated_at)s
                )
                ON CONFLICT (relocation_id) DO UPDATE SET
                    phase = EXCLUDED.phase,
                    target_content_sha256 = EXCLUDED.target_content_sha256,
                    target_size_bytes = EXCLUDED.target_size_bytes,
                    refusal_reason = EXCLUDED.refusal_reason,
                    updated_at = EXCLUDED.updated_at
                """,
                {
                    "relocation_id": record.relocation_id,
                    "dataset_identity": json.dumps(record.dataset_identity.stable_dict(), sort_keys=True),
                    "catalog_partition_id": record.catalog_partition_id,
                    "partition_key": record.partition_key,
                    "revision": record.revision,
                    "source_storage_root_id": record.source_storage_root_id,
                    "target_storage_root_id": record.target_storage_root_id,
                    "dataset_rel_root": record.dataset_rel_root,
                    "rel_path": record.rel_path,
                    "expected_content_sha256": record.expected_content_sha256,
                    "expected_size_bytes": record.expected_size_bytes,
                    "phase": record.phase.value,
                    "target_content_sha256": record.target_content_sha256,
                    "target_size_bytes": record.target_size_bytes,
                    "refusal_reason": record.refusal_reason,
                    "updated_at": record.updated_at.to_datetime(),
                },
            )

    def current_storage_root_id(self, catalog_partition_id: str) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT storage_root_id FROM catalog.partitions WHERE partition_id = %s",
                (catalog_partition_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RelocationError("catalog partition not found")
        return str(row[0])

    def switch_partition_storage_root(
        self,
        *,
        catalog_partition_id: str,
        source_storage_root_id: str,
        target_storage_root_id: str,
    ) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE catalog.partitions
                   SET storage_root_id = %s, tiered_at = now()
                 WHERE partition_id = %s
                   AND storage_root_id = %s
                """,
                (target_storage_root_id, catalog_partition_id, source_storage_root_id),
            )
            return cursor.rowcount == 1


def relocate_storage_tier(
    *,
    plan: RelocationPlan,
    catalog: RelocationCatalog,
    source_storage_root: str | Path,
    target_storage_root: str | Path,
    crash_after: RelocationCrashPoint | str | None = None,
) -> RelocationRecordV1:
    """Execute or resume one ADR-0048 relocation until CLEANED_UP."""

    crash_point = None if crash_after is None else RelocationCrashPoint(crash_after)
    source_path = _artifact_path(source_storage_root, plan)
    target_path = _artifact_path(target_storage_root, plan)
    record = catalog.load_relocation_record(plan.relocation_id)

    if record is None:
        record = plan.planned_record(updated_at=utc_now_instant())
        catalog.save_relocation_record(advance_relocation(None, record))
        _maybe_interrupt(crash_point, RelocationCrashPoint.AFTER_PLANNED)

    current_root = catalog.current_storage_root_id(plan.catalog_partition_id)
    if current_root == plan.target_storage_root_id and record.phase in {
        RelocationPhase.PLANNED,
        RelocationPhase.STAGED,
        RelocationPhase.VERIFIED,
    }:
        target_hash, target_size = _hash_and_size(target_path)
        if not verify_target_identity(plan.expected_content_sha256, plan.expected_size_bytes, target_hash, target_size):
            raise RelocationVerificationFailed("catalog points at target but target identity does not match")
        switched = record.with_phase(
            RelocationPhase.SWITCHED,
            updated_at=utc_now_instant(),
            target_content_sha256=target_hash,
            target_size_bytes=target_size,
        )
        record = advance_relocation(record, switched)
        catalog.save_relocation_record(record)

    if record.phase in {RelocationPhase.PLANNED, RelocationPhase.STAGED}:
        _stage_target(source_path, target_path, crash_point)
        target_hash, target_size = _hash_and_size(target_path)
        staged = record.with_phase(
            RelocationPhase.STAGED,
            updated_at=utc_now_instant(),
            target_content_sha256=target_hash,
            target_size_bytes=target_size,
        )
        record = advance_relocation(record, staged) if record.phase == RelocationPhase.PLANNED else staged
        catalog.save_relocation_record(record)
        _maybe_interrupt(crash_point, RelocationCrashPoint.AFTER_STAGED)

    if record.phase == RelocationPhase.STAGED:
        target_hash, target_size = _hash_and_size(target_path)
        if not verify_target_identity(plan.expected_content_sha256, plan.expected_size_bytes, target_hash, target_size):
            raise RelocationVerificationFailed("staged target identity does not match source catalog identity")
        verified = record.with_phase(
            RelocationPhase.VERIFIED,
            updated_at=utc_now_instant(),
            target_content_sha256=target_hash,
            target_size_bytes=target_size,
        )
        record = advance_relocation(record, verified)
        catalog.save_relocation_record(record)
        _maybe_interrupt(crash_point, RelocationCrashPoint.AFTER_VERIFIED)

    if record.phase == RelocationPhase.VERIFIED:
        target_hash, target_size = _hash_and_size(target_path)
        if not verify_target_identity(plan.expected_content_sha256, plan.expected_size_bytes, target_hash, target_size):
            restaged = record.with_phase(RelocationPhase.STAGED, updated_at=utc_now_instant())
            record = advance_relocation(record, restaged)
            catalog.save_relocation_record(record)
            raise RelocationVerificationFailed("pre-switch target identity no longer matches source catalog identity")
        current_root = catalog.current_storage_root_id(plan.catalog_partition_id)
        if current_root == plan.source_storage_root_id:
            if not catalog.switch_partition_storage_root(
                catalog_partition_id=plan.catalog_partition_id,
                source_storage_root_id=plan.source_storage_root_id,
                target_storage_root_id=plan.target_storage_root_id,
            ):
                raise RelocationError("catalog storage_root_id switch was not applied")
        elif current_root != plan.target_storage_root_id:
            raise RelocationError("catalog partition moved outside this relocation domain")
        switched = record.with_phase(RelocationPhase.SWITCHED, updated_at=utc_now_instant())
        record = advance_relocation(record, switched)
        catalog.save_relocation_record(record)
        _maybe_interrupt(crash_point, RelocationCrashPoint.AFTER_SWITCHED)

    if record.phase == RelocationPhase.SWITCHED:
        if source_path.exists():
            source_path.unlink()
        cleaned = record.with_phase(RelocationPhase.CLEANED_UP, updated_at=utc_now_instant())
        record = advance_relocation(record, cleaned)
        catalog.save_relocation_record(record)

    return record


def _artifact_path(storage_root: str | Path, plan: RelocationPlan) -> Path:
    root = Path(storage_root)
    path = (root / plan.dataset_rel_root / plan.rel_path).resolve()
    root_resolved = root.resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise RelocationError("resolved artifact path escapes storage_root") from exc
    return path


def _stage_target(source_path: Path, target_path: Path, crash_point: RelocationCrashPoint | None) -> None:
    if not source_path.is_file():
        raise RelocationError("source artifact is not readable at the catalog location")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.name}.k07.tmp")
    shutil.copy2(source_path, temp_path)
    _maybe_interrupt(crash_point, RelocationCrashPoint.AFTER_TEMP_COPY)
    os.replace(temp_path, target_path)


def _hash_and_size(path: Path) -> tuple[str, int]:
    if not path.is_file():
        raise RelocationVerificationFailed("target artifact is absent")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _maybe_interrupt(crash_point: RelocationCrashPoint | None, point: RelocationCrashPoint) -> None:
    if crash_point == point:
        raise RelocationInterrupted(point.value)


__all__ = [
    "PostgresRelocationCatalog",
    "RelocationCatalog",
    "RelocationCrashPoint",
    "RelocationInterrupted",
    "relocate_storage_tier",
]
