"""Orchestration of the narrow catalog-backed DataGateway v1 slice."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .catalog import Catalog
from .models import (
    CatalogDataset,
    CatalogPartition,
    CatalogConflict,
    CoverageInterval,
    DataIntegrityError,
    DataRequest,
    DataSlice,
    DataSliceMetadata,
    DatasetIdentity,
    Instant,
    LifecyclePolicy,
    NoCoverage,
    SchemaMismatch,
    UnsupportedDatasetKind,
    UnsupportedSchema,
    gaps_for,
    intersect,
    merge_intervals,
    result_fingerprint,
)
from .parquet import read_trade_v1, resolve_partition_path


class DataGateway:
    """Read canonical Bybit ``trade-v1`` data through the PostgreSQL catalog."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        reader: Callable[[str, Instant, Instant], list[Any]] = read_trade_v1,
        path_resolver: Callable[[str, str, str], Any] = resolve_partition_path,
    ):
        self.catalog = catalog
        self._reader = reader
        self._path_resolver = path_resolver

    def read(self, request: DataRequest) -> DataSlice:
        self._validate_support(request)
        dataset = self.catalog.resolve_dataset(request.dataset_selector)
        if dataset.identity.record_schema_id != request.schema_requirement:
            raise SchemaMismatch(
                "requested schema does not match the catalog dataset",
                context={
                    "requested_schema": request.schema_requirement,
                    "stored_schema": dataset.identity.record_schema_id,
                },
            )
        partitions = self.catalog.select_partitions(
            dataset,
            request.start,
            request.end,
            request.lifecycle_policy.states,
        )
        self._validate_partition_set(dataset, partitions)
        request_interval = CoverageInterval(request.start, request.end)
        eligible = tuple(
            clipped
            for partition in partitions
            if (coverage := partition.coverage) is not None
            if (clipped := intersect(coverage, request_interval)) is not None
        )
        eligible_union = merge_intervals(eligible)
        gaps = gaps_for(request_interval, eligible_union)
        if gaps:
            raise NoCoverage(
                "requested interval is not fully covered by eligible partitions",
                context={
                    "requested_interval": request_interval.stable_dict(),
                    "coverage_gaps": [gap.stable_dict() for gap in gaps],
                },
            )

        records: list[Any] = []
        for partition in partitions:
            path = self._path_resolver(
                partition.storage_root,
                partition.dataset_rel_root,
                partition.rel_path,
            )
            records.extend(self._reader(str(path), request.start, request.end))
        self._validate_records(dataset.identity, records)
        if any(record.trade_id is None for record in records):
            raise DataIntegrityError("Bybit trade-v1 ordering requires trade_id")
        records.sort(key=lambda record: (record.exchange_ts.epoch_ns, record.trade_id))
        if len({(record.exchange_ts.epoch_ns, record.trade_id) for record in records}) != len(records):
            raise DataIntegrityError("duplicate Bybit trade-v1 ordering key")

        returned_bounds = None
        if records:
            returned_bounds = CoverageInterval(records[0].exchange_ts, records[-1].exchange_ts)
        metadata = self._metadata(
            request=request,
            dataset=dataset,
            partitions=partitions,
            eligible_coverage=eligible_union,
            coverage_gaps=gaps,
            returned_bounds=returned_bounds,
            row_count=len(records),
        )
        return DataSlice(records=tuple(records), metadata=metadata)

    @staticmethod
    def _validate_support(request: DataRequest) -> None:
        identity = request.dataset_selector
        if identity.dataset_kind != "trades" or identity.layer != "canonical" or identity.venue != "bybit":
            raise UnsupportedDatasetKind(
                "DataGateway v1 supports canonical Bybit trades only",
                context={"dataset_identity": identity.stable_dict()},
            )
        if identity.record_schema_id != "trade-v1" or request.schema_requirement != "trade-v1":
            raise UnsupportedSchema(
                "DataGateway v1 supports trade-v1 only",
                context={"requested_schema": request.schema_requirement},
            )
        if request.ordering_policy != "bybit-trade-v1-exchange-ts-trade-id-v1":
            raise UnsupportedSchema("unsupported ordering policy for trade-v1")

    @staticmethod
    def _validate_partition_set(dataset: CatalogDataset, partitions: list[CatalogPartition]) -> None:
        seen: dict[object, CatalogPartition] = {}
        with_coverage: list[CatalogPartition] = []
        for partition in partitions:
            if partition.dataset_rel_root != dataset.rel_root:
                raise CatalogConflict("partition points at a different dataset rel_root")
            key = partition.natural_identity
            if key in seen:
                raise CatalogConflict(
                    "duplicate natural partition identity in catalog",
                    context={"partition": key.stable_dict()},
                )
            seen[key] = partition
            if partition.coverage is not None:
                with_coverage.append(partition)
            if not partition.content_sha256 or not partition.manifest_sha256:
                raise DataIntegrityError(
                    "eligible partition is missing cataloged content or manifest identity",
                    context={"partition": key.stable_dict()},
                )

        ordered = sorted(with_coverage, key=lambda p: p.coverage.start.epoch_ns)  # type: ignore[union-attr]
        for previous, current in zip(ordered, ordered[1:]):
            previous_end = previous.coverage.end  # type: ignore[union-attr]
            current_start = current.coverage.start  # type: ignore[union-attr]
            if current_start < previous_end:
                raise CatalogConflict(
                    "eligible partitions have unexplained temporal overlap",
                    context={
                        "left": previous.natural_identity.stable_dict(),
                        "right": current.natural_identity.stable_dict(),
                    },
                )

    @staticmethod
    def _validate_records(identity: DatasetIdentity, records: list[Any]) -> None:
        for record in records:
            if record.venue != identity.venue or record.instrument != identity.instrument:
                raise DataIntegrityError(
                    "Parquet record identity does not match catalog dataset",
                    context={"dataset_identity": identity.stable_dict()},
                )

    @staticmethod
    def _metadata(
        *,
        request: DataRequest,
        dataset: CatalogDataset,
        partitions: list[CatalogPartition],
        eligible_coverage: tuple[CoverageInterval, ...],
        coverage_gaps: tuple[CoverageInterval, ...],
        returned_bounds: CoverageInterval | None,
        row_count: int,
    ) -> DataSliceMetadata:
        natural_partitions = tuple(partition.natural_identity for partition in partitions)
        manifest_hashes = (dataset.manifest_sha256,) + tuple(
            partition.manifest_sha256 for partition in partitions if partition.manifest_sha256 is not None
        )
        content_hashes = tuple(
            partition.content_sha256 for partition in partitions if partition.content_sha256 is not None
        )
        stable = {
            "request_identity": request.request_identity,
            "natural_partitions": [item.stable_dict() for item in natural_partitions],
            "manifest_hashes": list(manifest_hashes),
            "content_hashes": list(content_hashes),
            "record_schema_id": dataset.identity.record_schema_id,
            "schema_version": dataset.schema_version,
            "schema_hash": dataset.schema_hash,
            "ordering_policy": request.ordering_policy,
            "lifecycle_policy": request.lifecycle_policy.value,
            "coverage_policy": request.coverage_policy,
            "eligible_coverage": [item.stable_dict() for item in eligible_coverage],
            "coverage_gaps": [item.stable_dict() for item in coverage_gaps],
            "coverage_complete": not coverage_gaps,
            "returned_record_bounds": returned_bounds.stable_dict() if returned_bounds else None,
            "row_count": row_count,
        }
        return DataSliceMetadata(
            dataset_identity=dataset.identity,
            record_schema_id=dataset.identity.record_schema_id,
            schema_version=dataset.schema_version,
            schema_hash=dataset.schema_hash,
            natural_partitions=natural_partitions,
            manifest_hashes=manifest_hashes,
            content_hashes=content_hashes,
            request_identity=request.request_identity,
            result_identity=result_fingerprint(stable),
            requested_interval=CoverageInterval(request.start, request.end),
            eligible_coverage=eligible_coverage,
            coverage_gaps=coverage_gaps,
            coverage_complete=not coverage_gaps,
            returned_record_bounds=returned_bounds,
            row_count=row_count,
            ordering_policy=request.ordering_policy,
            lifecycle_policy=request.lifecycle_policy,
            coverage_policy=request.coverage_policy,
            catalog_dataset_id=dataset.catalog_dataset_id,
            catalog_partition_ids=tuple(partition.catalog_partition_id for partition in partitions),
            storage_root_ids=tuple(partition.storage_root_id for partition in partitions),
            rel_paths=tuple(partition.rel_path for partition in partitions),
        )


__all__ = ["DataGateway"]
