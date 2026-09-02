"""Narrow PostgreSQL write boundary for S13 seal and evidence recording.

This adapter is intentionally separate from :class:`Catalog`, whose contract
is read/resolution-only for DataGateway.  It never performs S14 lifecycle
publication: all partition writes made here remain ``closed``.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from .publication import QualityReport, SealedCatalogPartition
from .models import DatasetIdentity, Instant, NaturalPartitionIdentity


class CatalogPublicationConflict(RuntimeError):
    """Raised when an existing catalog row conflicts with sealed evidence."""


class CatalogPublicationWriter:
    """Persist S13 Phase 1 closed rows and revision-local quality evidence.

    Phase 1 owns the atomic revision admission boundary.  It never advances a
    row to an eligibility state and never copies quality evidence between
    revisions.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def seal_partition(
        self,
        *,
        dataset: Mapping[str, Any],
        partition: Mapping[str, Any],
        coverage_start: Any,
        coverage_end: Any,
        storage_root_id: str,
    ) -> SealedCatalogPartition:
        identity = _identity(dataset)
        with self.connection.cursor() as cursor:
            dataset_id = self._resolve_dataset(cursor, dataset, identity)
            cursor.execute(
                """
                SELECT partition_id::text, state, revision, storage_root_id,
                       rel_path, ts_start, ts_end, row_count, byte_size,
                       content_sha256, manifest_sha256, created_at, closed_at,
                       first_sequence, last_sequence, producer, code_ref
                  FROM catalog.partitions
                 WHERE dataset_id = %s
                   AND partition_key = %s
                 ORDER BY revision
                 FOR UPDATE
                """,
                (dataset_id, partition["partition_key"]),
            )
            topology = cursor.fetchall()
            current = _validate_catalog_topology(topology)
            target_revision = int(partition["revision"])
            existing = next((row for row in topology if int(row[2]) == target_revision), None)

            if existing is not None:
                if existing[1] != "closed":
                    raise CatalogPublicationConflict(
                        f"S13 seal refuses existing target state {existing[1]!r}"
                    )
                if current[0] != existing[0] or not _same_phase_one_evidence(
                    existing, partition, storage_root_id
                ):
                    raise CatalogPublicationConflict(
                        "existing closed target conflicts with authoritative Phase-1 evidence"
                    )
                expected_start = coverage_start.to_datetime()
                expected_end = coverage_end.to_datetime()
                if existing[5] != expected_start or existing[6] != expected_end:
                    cursor.execute(
                        """
                        UPDATE catalog.partitions
                           SET ts_start = %s, ts_end = %s
                         WHERE partition_id = %s
                           AND state = 'closed'
                        RETURNING partition_id::text
                        """,
                        (expected_start, expected_end, existing[0]),
                    )
                    updated = cursor.fetchone()
                    if updated is None or str(updated[0]) != str(existing[0]):
                        raise CatalogPublicationConflict(
                            "same-revision coverage restatement did not update the closed target"
                        )
                    cursor.execute(
                        """
                        SELECT partition_id::text, dataset_id::text, partition_key,
                               state, revision, storage_root_id, rel_path,
                               ts_start, ts_end, row_count, byte_size,
                               content_sha256, manifest_sha256, created_at, closed_at,
                               first_sequence, last_sequence, producer, code_ref
                          FROM catalog.partitions
                         WHERE partition_id = %s
                         FOR UPDATE
                        """,
                        (existing[0],),
                    )
                    restated = cursor.fetchone()
                    if not _same_restatement_row(
                        restated,
                        partition_id=existing[0],
                        dataset_id=dataset_id,
                        partition=partition,
                        storage_root_id=storage_root_id,
                        coverage_start=expected_start,
                        coverage_end=expected_end,
                    ):
                        raise CatalogPublicationConflict(
                            "same-revision coverage restatement verification failed"
                        )
                return _sealed_partition(
                    existing[0], dataset_id, identity, partition,
                    coverage_start, coverage_end,
                )

            if current is None:
                if target_revision != 1:
                    raise CatalogPublicationConflict(
                        "first admitted revision must be 1"
                    )
            elif target_revision != int(current[2]) + 1:
                raise CatalogPublicationConflict(
                    f"target revision {target_revision} is not the contiguous successor "
                    f"of live revision {current[2]}"
                )

            if current is not None:
                cursor.execute(
                    """
                    UPDATE catalog.partitions
                       SET state = 'superseded'
                     WHERE partition_id = %s
                       AND state <> 'superseded'
                    RETURNING partition_id::text
                    """,
                    (current[0],),
                )
                superseded = cursor.fetchone()
                if superseded is None or str(superseded[0]) != str(current[0]):
                    raise CatalogPublicationConflict(
                        "live predecessor could not be superseded atomically"
                    )

            # Named parameters, not positional slicing: the authoritative
            # PartitionManifest.created_at (never DB now()) must reach
            # catalog.partitions.created_at so historical/backfill seals with
            # closed_at in the past satisfy closed_after_created.
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
                    "partition_key": partition["partition_key"],
                    "revision": target_revision,
                    "storage_root_id": storage_root_id,
                    "rel_path": partition["rel_path"],
                    "ts_start": coverage_start.to_datetime(),
                    "ts_end": coverage_end.to_datetime(),
                    "row_count": partition["row_count"],
                    "byte_size": partition["file_size_bytes"],
                    "content_sha256": partition["sha256"],
                    "manifest_sha256": _manifest_sha(partition),
                    "created_at": partition["created_at"],
                    "closed_at": partition["closed_at"],
                    "first_sequence": partition.get("first_sequence"),
                    "last_sequence": partition.get("last_sequence"),
                    "producer": partition["producer"],
                    "code_ref": partition["code_ref"],
                },
            )
            partition_id = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT partition_id::text, state, revision, storage_root_id,
                       rel_path, ts_start, ts_end, row_count, byte_size,
                       content_sha256, manifest_sha256, created_at, closed_at,
                       first_sequence, last_sequence, producer, code_ref
                  FROM catalog.partitions
                 WHERE dataset_id = %s
                   AND partition_key = %s
                 ORDER BY revision
                 FOR UPDATE
                """,
                (dataset_id, partition["partition_key"]),
            )
            admitted_topology = cursor.fetchall()
            admitted_current = _validate_catalog_topology(admitted_topology)
            if (
                admitted_current is None
                or int(admitted_current[2]) != target_revision
                or str(admitted_current[0]) != str(partition_id)
                or admitted_current[1] != "closed"
                or current is not None and str(partition_id) == str(current[0])
                or current is not None and any(
                    str(row[0]) == str(current[0]) and row[1] != "superseded"
                    for row in admitted_topology
                )
            ):
                raise CatalogPublicationConflict(
                    "successor admission verification failed"
                )

            return _sealed_partition(
                partition_id, dataset_id, identity, partition,
                coverage_start, coverage_end,
            )

    def record_quality_report(
        self,
        *,
        partition_id: str,
        check_suite: str,
        status: str,
        metrics: Mapping[str, Any],
        violations: list[Mapping[str, Any]],
        code_ref: str,
    ) -> QualityReport:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM catalog.partitions WHERE partition_id = %s FOR UPDATE",
                (partition_id,),
            )
            state = cursor.fetchone()
            if state is None:
                raise CatalogPublicationConflict("quality report target partition does not exist")
            if state[0] != "closed":
                raise CatalogPublicationConflict("S13 evidence recording requires a closed partition")
            metrics_json = _canonical_json(metrics)
            violations_json = _canonical_json(violations)
            cursor.execute(
                """
                SELECT report_id::text, status, metrics, violations, code_ref
                  FROM catalog.quality_reports
                 WHERE partition_id = %s AND check_suite = %s
                 ORDER BY ran_at DESC, report_id::text DESC
                """,
                (partition_id, check_suite),
            )
            for row in cursor.fetchall():
                if (
                    row[1] == status
                    and _canonical_json(row[2]) == metrics_json
                    and _canonical_json(row[3]) == violations_json
                    and row[4] == code_ref
                ):
                    return QualityReport(str(row[0]), partition_id, check_suite, status, dict(metrics), list(violations), code_ref)
            try:
                from psycopg.types.json import Jsonb
            except ImportError as exc:  # pragma: no cover - dependency boundary
                raise RuntimeError("psycopg is required for catalog publication writes") from exc
            cursor.execute(
                """
                INSERT INTO catalog.quality_reports
                    (partition_id, check_suite, status, metrics, violations, code_ref)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING report_id::text
                """,
                (partition_id, check_suite, status, Jsonb(dict(metrics)), Jsonb(list(violations)), code_ref),
            )
            report_id = cursor.fetchone()[0]
            return QualityReport(str(report_id), partition_id, check_suite, status, dict(metrics), list(violations), code_ref)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    @staticmethod
    def _resolve_dataset(cursor: Any, document: Mapping[str, Any], identity: tuple[Any, ...]) -> str:
        cursor.execute(
            """
            SELECT dataset_id::text, rel_root, manifest_sha256
              FROM catalog.datasets
             WHERE layer = %s AND kind = %s AND venue = %s
               AND instrument = %s AND schema_id = %s
               AND feature_set_def_id IS NULL
             FOR UPDATE
            """,
            identity,
        )
        row = cursor.fetchone()
        if row is not None:
            if row[1] != document["rel_root"] or row[2].strip() != _manifest_sha(document):
                raise CatalogPublicationConflict("existing dataset conflicts with durable dataset manifest")
            return str(row[0])
        cursor.execute(
            """
            INSERT INTO catalog.datasets
                (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING dataset_id::text
            """,
            (*identity[:4], document["rel_root"], identity[4], _manifest_sha(document)),
        )
        return str(cursor.fetchone()[0])


def _identity(document: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        document["layer"],
        document["dataset_kind"],
        document["venue"],
        document["instrument"],
        document["record_schema_id"],
    )


def _validate_catalog_topology(rows: list[tuple[Any, ...]]) -> tuple[Any, ...] | None:
    """Validate one complete locked partition family and return its live row."""

    if not rows:
        return None
    live = [row for row in rows if row[1] != "superseded"]
    if len(live) != 1:
        raise CatalogPublicationConflict(
            "partition topology must have exactly one live row"
        )
    current = live[0]
    current_revision = int(current[2])
    if any(int(row[2]) >= current_revision for row in rows if row[1] == "superseded"):
        raise CatalogPublicationConflict(
            "partition topology contains superseded history at or above the live revision"
        )
    return current


def _same_phase_one_evidence(
    existing: tuple[Any, ...],
    partition: Mapping[str, Any],
    storage_root_id: str,
) -> bool:
    """Compare all Phase-1 evidence represented by the catalog row."""

    return (
        existing[3] == storage_root_id
    ) and (
        existing[4] == partition["rel_path"]
        and int(existing[7]) == int(partition["row_count"])
        and int(existing[8]) == int(partition["file_size_bytes"])
        and _text(existing[9]) == partition["sha256"]
        and _text(existing[10]) == _manifest_sha(partition)
        and _timestamp(existing[11]) == _timestamp(partition["created_at"])
        and _timestamp(existing[12]) == _timestamp(partition["closed_at"])
        and _number(existing[13]) == _number(partition.get("first_sequence"))
        and _number(existing[14]) == _number(partition.get("last_sequence"))
        and existing[15] == partition["producer"]
        and existing[16] == partition["code_ref"]
    )


def _same_restatement_row(
    row: tuple[Any, ...] | None,
    *,
    partition_id: Any,
    dataset_id: Any,
    partition: Mapping[str, Any],
    storage_root_id: str,
    coverage_start: Any,
    coverage_end: Any,
) -> bool:
    """Verify the exact row after an in-place coverage-only update."""

    if row is None:
        return False
    return (
        str(row[0]) == str(partition_id)
        and str(row[1]) == str(dataset_id)
        and row[2] == partition["partition_key"]
        and row[3] == "closed"
        and int(row[4]) == int(partition["revision"])
        and row[5] == storage_root_id
        and row[6] == partition["rel_path"]
        and row[7] == coverage_start
        and row[8] == coverage_end
        and int(row[9]) == int(partition["row_count"])
        and int(row[10]) == int(partition["file_size_bytes"])
        and _text(row[11]) == partition["sha256"]
        and _text(row[12]) == _manifest_sha(partition)
        and _timestamp(row[13]) == _timestamp(partition["created_at"])
        and _timestamp(row[14]) == _timestamp(partition["closed_at"])
        and _number(row[15]) == _number(partition.get("first_sequence"))
        and _number(row[16]) == _number(partition.get("last_sequence"))
        and row[17] == partition["producer"]
        and row[18] == partition["code_ref"]
    )


def _sealed_partition(
    partition_id: Any,
    dataset_id: Any,
    identity: tuple[Any, ...],
    partition: Mapping[str, Any],
    coverage_start: Any,
    coverage_end: Any,
) -> SealedCatalogPartition:
    return SealedCatalogPartition(
        partition_id=str(partition_id),
        dataset_id=str(dataset_id),
        natural_identity=NaturalPartitionIdentity(
            DatasetIdentity(*identity), partition["partition_key"], partition["revision"]
        ),
        state="closed",
        ts_start=coverage_start,
        ts_end=coverage_end,
        row_count=int(partition["row_count"]),
        byte_size=int(partition["file_size_bytes"]),
        content_sha256=partition["sha256"],
        manifest_sha256=_manifest_sha(partition),
        producer=partition["producer"],
        code_ref=partition["code_ref"],
    )


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _number(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _timestamp(value: Any) -> Any:
    if isinstance(value, str):
        return Instant.parse(value).to_datetime()
    if isinstance(value, Instant):
        return value.to_datetime()
    return value


def _manifest_sha(document: Mapping[str, Any]) -> str:
    value = document.get("_manifest_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise CatalogPublicationConflict("durable manifest SHA is missing from catalog write input")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = ["CatalogPublicationConflict", "CatalogPublicationWriter"]
