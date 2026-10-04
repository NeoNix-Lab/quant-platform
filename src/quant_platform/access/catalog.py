"""PostgreSQL catalog resolver for DataGateway v1.

This module only resolves logical datasets and eligible partition locators.  It
never opens a Parquet file and never scans a storage root.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import (
    CatalogDataset,
    CatalogPartition,
)
from ..data.models import (
    CatalogConflict,
    DatasetIdentity,
    DatasetNotFound,
    Instant,
    NaturalPartitionIdentity,
)


class Catalog:
    """Minimal PostgreSQL catalog adapter.

    ``connection`` may be an existing psycopg connection, which keeps
    transaction ownership with the caller.  ``dsn`` is a convenience for a
    gateway-owned connection per operation.
    """

    def __init__(self, connection: Any | None = None, *, dsn: str | None = None):
        if connection is None and dsn is None:
            raise ValueError("connection or dsn is required")
        self._connection = connection
        self._dsn = dsn
        self.last_candidate_count = 0

    def _connect(self):
        if self._connection is not None:
            return self._connection, False
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RuntimeError("psycopg is required for PostgreSQL catalog access") from exc
        return psycopg.connect(self._dsn), True

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        connection, owned = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT d.dataset_id::text, d.layer, d.kind, d.venue,
                           d.instrument, d.feature_set_def_id::text,
                           d.schema_id, d.rel_root, d.manifest_sha256,
                           sr.version, sr.json_sha256
                      FROM catalog.datasets AS d
                      JOIN catalog.schema_registry AS sr
                        ON sr.schema_id = d.schema_id
                     WHERE d.layer = %s
                       AND d.kind = %s
                       AND d.venue = %s
                       AND d.instrument = %s
                       AND d.schema_id = %s
                       AND d.feature_set_def_id IS NULL
                    """,
                    (
                        identity.layer,
                        identity.dataset_kind,
                        identity.venue,
                        identity.instrument,
                        identity.record_schema_id,
                    ),
                )
                rows = cursor.fetchall()
        finally:
            if owned:
                connection.close()
        if not rows:
            raise DatasetNotFound(
                "no catalog dataset matches the natural identity",
                context={"dataset_identity": identity.stable_dict()},
            )
        if len(rows) != 1:
            raise CatalogConflict(
                "catalog returned multiple datasets for one natural identity",
                context={"dataset_identity": identity.stable_dict(), "count": len(rows)},
            )
        row = rows[0]
        resolved = DatasetIdentity(
            layer=row[1],
            dataset_kind=row[2],
            venue=row[3],
            instrument=row[4],
            record_schema_id=row[6],
        )
        if resolved != identity:
            raise CatalogConflict(
                "catalog dataset does not match the requested natural identity",
                context={"requested": identity.stable_dict(), "resolved": resolved.stable_dict()},
            )
        return CatalogDataset(
            identity=resolved,
            catalog_dataset_id=row[0],
            rel_root=row[7],
            manifest_sha256=_text(row[8]),
            schema_version=int(row[9]),
            schema_hash=_text(row[10]),
        )

    def select_partitions(
        self,
        dataset: CatalogDataset,
        start: Instant,
        end: Instant,
        states: tuple[str, ...],
    ) -> list[CatalogPartition]:
        """Return only partitions whose declared coverage intersects the request."""
        if start == end:
            self.last_candidate_count = 0
            return []
        connection, owned = self._connect()
        try:
            with connection.cursor() as cursor:
                # ts_end/ts_start are declared half-open coverage boundaries.
                # The predicates are the partition-pruning boundary: files
                # outside the request never reach the Parquet reader.
                cursor.execute(
                    """
                    SELECT p.partition_id::text, p.partition_key, p.revision,
                           p.storage_root_id, sr.abs_path, d.rel_root,
                           p.rel_path, p.ts_start, p.ts_end, p.row_count,
                           p.content_sha256, p.manifest_sha256, p.state,
                           p.producer, p.code_ref
                      FROM catalog.partitions AS p
                      JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
                      JOIN catalog.storage_roots AS sr
                        ON sr.storage_root_id = p.storage_root_id
                     WHERE p.dataset_id = %s
                       AND p.state = ANY(%s)
                       AND p.ts_end > %s
                       AND p.ts_start < %s
                     ORDER BY p.ts_start, p.ts_end, p.partition_key, p.revision
                    """,
                    (
                        dataset.catalog_dataset_id,
                        list(states),
                        _db_timestamp(start),
                        _db_timestamp(end, round_up=True),
                    ),
                )
                rows = cursor.fetchall()
        finally:
            if owned:
                connection.close()
        self.last_candidate_count = len(rows)
        return [
            CatalogPartition(
                natural_identity=NaturalPartitionIdentity(
                    dataset_identity=dataset.identity,
                    partition_key=row[1],
                    revision=int(row[2]),
                ),
                catalog_partition_id=row[0],
                storage_root_id=row[3],
                storage_root=row[4],
                dataset_rel_root=row[5],
                rel_path=row[6],
                ts_start=_instant_or_none(row[7]),
                ts_end=_instant_or_none(row[8]),
                row_count=int(row[9]),
                content_sha256=_text_or_none(row[10]),
                manifest_sha256=_text_or_none(row[11]),
                state=row[12],
                producer=row[13],
                code_ref=row[14],
            )
            for row in rows
        ]


def _text(value: Any) -> str:
    return str(value).strip()


def _text_or_none(value: Any) -> str | None:
    return None if value is None else _text(value)


def _instant_or_none(value: Any) -> Instant | None:
    return None if value is None else Instant.parse(value)


def _db_timestamp(value: Instant, *, round_up: bool = False) -> datetime:
    epoch_us, remainder = divmod(value.epoch_ns, 1_000)
    if round_up and remainder:
        epoch_us += 1
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=epoch_us)


__all__ = ["Catalog"]
