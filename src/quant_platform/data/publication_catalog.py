"""Narrow PostgreSQL write boundary for S13 seal and evidence recording.

This adapter is intentionally separate from :class:`Catalog`, whose contract
is read/resolution-only for DataGateway.  It never performs S14 lifecycle
publication: all partition writes made here remain ``closed``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .publication import QualityReport, SealedCatalogPartition
from .models import DatasetIdentity, NaturalPartitionIdentity


class CatalogPublicationConflict(RuntimeError):
    """Raised when an existing catalog row conflicts with sealed evidence."""


class CatalogPublicationWriter:
    """Persist only the S13 closed row and its quality report."""

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
                SELECT partition_id::text, state, storage_root_id, rel_path,
                       ts_start, ts_end, row_count, byte_size, content_sha256,
                       manifest_sha256, producer, code_ref
                  FROM catalog.partitions
                 WHERE dataset_id = %s
                   AND partition_key = %s
                   AND revision = %s
                 FOR UPDATE
                """,
                (dataset_id, partition["partition_key"], partition["revision"]),
            )
            existing = cursor.fetchone()
            values = (
                storage_root_id,
                partition["rel_path"],
                coverage_start.to_datetime(),
                coverage_end.to_datetime(),
                partition["row_count"],
                partition["file_size_bytes"],
                partition["sha256"],
                _manifest_sha(partition),
                partition.get("first_sequence"),
                partition.get("last_sequence"),
                partition["producer"],
                partition["code_ref"],
            )
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO catalog.partitions (
                        dataset_id, partition_key, revision, storage_root_id,
                        rel_path, ts_start, ts_end, row_count, byte_size,
                        content_sha256, state, manifest_sha256, closed_at,
                        first_sequence, last_sequence, producer, code_ref
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'closed', %s, %s, %s, %s, %s, %s
                    )
                    RETURNING partition_id::text
                    """,
                    (
                        dataset_id,
                        partition["partition_key"],
                        partition["revision"],
                        *values[:7],
                        values[7],
                        partition["closed_at"],
                        *values[8:],
                    ),
                )
                partition_id = cursor.fetchone()[0]
            else:
                partition_id = existing[0]
                if existing[1] != "closed":
                    raise CatalogPublicationConflict(
                        f"S13 seal refuses existing partition state {existing[1]!r}"
                    )
                expected = (
                    storage_root_id,
                    partition["rel_path"],
                    partition["row_count"],
                    partition["file_size_bytes"],
                    partition["sha256"],
                    _manifest_sha(partition),
                    partition["producer"],
                    partition["code_ref"],
                )
                actual = (
                    existing[2], existing[3], existing[6], existing[7],
                    str(existing[8]).strip(), str(existing[9]).strip(),
                    existing[10], existing[11],
                )
                if actual != expected:
                    raise CatalogPublicationConflict("existing closed partition conflicts with sealed evidence")
                if existing[4] != coverage_start.to_datetime() or existing[5] != coverage_end.to_datetime():
                    raise CatalogPublicationConflict("existing closed partition coverage conflicts with folded coverage")

            natural_identity = NaturalPartitionIdentity(
                DatasetIdentity(*identity),
                partition["partition_key"],
                partition["revision"],
            )
            return SealedCatalogPartition(
                partition_id=str(partition_id),
                dataset_id=str(dataset_id),
                natural_identity=natural_identity,
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


def _manifest_sha(document: Mapping[str, Any]) -> str:
    value = document.get("_manifest_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise CatalogPublicationConflict("durable manifest SHA is missing from catalog write input")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = ["CatalogPublicationConflict", "CatalogPublicationWriter"]
