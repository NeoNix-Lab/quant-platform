"""Orchestration of the catalog-backed DataGateway v1 read boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .catalog import Catalog
from .models import (
    CatalogDataset,
    CatalogPartition,
    CatalogConflict,
    CoverageInterval,
    RecordTimeBounds,
    DataIntegrityError,
    DataRequest,
    DataSlice,
    DataSliceMetadata,
    DatasetIdentity,
    Instant,
    LifecyclePolicy,
    NaturalPartitionIdentity,
    NoCoverage,
    SchemaMismatch,
    UnsupportedDatasetKind,
    UnsupportedSchema,
    gaps_for,
    intersect,
    merge_intervals,
    result_fingerprint,
)
from ..ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1, provider_for
from .parquet import resolve_partition_path, scan_trade_v1


BatchReader = Callable[[str, Instant, Instant, int], Iterable[tuple[Any, ...]]]
CompletionBuilder = Callable[[RecordTimeBounds | None, int], DataSliceMetadata]


class ScanState(str, Enum):
    OPEN = "open"
    READING = "reading"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class DataScanOpenMetadata:
    """Metadata known before the first canonical row is consumed."""

    dataset_identity: DatasetIdentity
    record_schema_id: str
    schema_version: int
    schema_hash: str
    natural_partitions: tuple[NaturalPartitionIdentity, ...]
    manifest_hashes: tuple[str, ...]
    content_hashes: tuple[str, ...]
    request_identity: str
    requested_interval: CoverageInterval
    eligible_coverage: tuple[CoverageInterval, ...]
    coverage_gaps: tuple[CoverageInterval, ...]
    coverage_complete: bool
    ordering_policy: str
    lifecycle_policy: LifecyclePolicy
    coverage_policy: str
    catalog_dataset_id: str
    catalog_partition_ids: tuple[str, ...]
    storage_root_ids: tuple[str, ...]
    rel_paths: tuple[str, ...]


class DataScan(Iterator[tuple[Any, ...]]):
    """Finite ordered batch scan with explicit completion semantics.

    The object never exposes final ``DataSliceMetadata`` before the source is
    fully drained.  A caller may stop early and leave the scan in ``READING``
    or call :meth:`close` to mark it ``ABORTED``; neither path produces a
    ``result_identity``.
    """

    def __init__(
        self,
        *,
        request: DataRequest,
        dataset: CatalogDataset,
        partitions: tuple[CatalogPartition, ...],
        ordering_provider: OrderingProvider,
        open_metadata: DataScanOpenMetadata,
        batch_reader: BatchReader,
        path_resolver: Callable[[str, str, str], Any],
        completion_builder: CompletionBuilder,
        batch_size: int,
    ) -> None:
        self.request = request
        self.dataset = dataset
        self.partitions = partitions
        self.ordering_provider = ordering_provider
        self.open_metadata = open_metadata
        self._batch_reader = batch_reader
        self._path_resolver = path_resolver
        self._completion_builder = completion_builder
        self.batch_size = batch_size
        self._state = ScanState.OPEN
        self._iterator: Iterator[tuple[Any, ...]] | None = None
        self._completed_metadata: DataSliceMetadata | None = None
        self._row_count = 0
        self._first_record_time: Instant | None = None
        self._last_record_time: Instant | None = None
        self._last_ordering_key: tuple[Any, ...] | None = None

    @property
    def state(self) -> ScanState:
        return self._state

    @property
    def completed_metadata(self) -> DataSliceMetadata | None:
        """Return final metadata only after complete source exhaustion."""

        return self._completed_metadata

    def __iter__(self) -> "DataScan":
        return self

    def __next__(self) -> tuple[Any, ...]:
        if self._state in {ScanState.COMPLETED, ScanState.ABORTED}:
            raise StopIteration
        if self._iterator is None:
            self._state = ScanState.READING
            self._iterator = self._iter_batches()
        try:
            return next(self._iterator)
        except StopIteration:
            self._complete()
            raise
        except Exception:
            self._state = ScanState.ABORTED
            raise

    def close(self) -> None:
        """Abort an unfinished scan without manufacturing final provenance."""

        if self._state == ScanState.COMPLETED:
            return
        iterator = self._iterator
        if iterator is not None:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        self._state = ScanState.ABORTED
        self._completed_metadata = None

    def _iter_batches(self) -> Iterator[tuple[Any, ...]]:
        for index, partition in enumerate(self.partitions):
            path = self._path_resolver(
                partition.storage_root,
                partition.dataset_rel_root,
                partition.rel_path,
            )
            previous_partition = self.partitions[index - 1] if index else None
            next_partition = self.partitions[index + 1] if index + 1 < len(self.partitions) else None
            for raw_batch in self._batch_reader(
                str(path),
                self.request.start,
                self.request.end,
                self.batch_size,
            ):
                batch = tuple(raw_batch)
                if not batch:
                    continue
                self._validate_and_account_batch(
                    partition=partition,
                    previous_partition=previous_partition,
                    next_partition=next_partition,
                    batch=batch,
                )
                yield batch

    def _validate_and_account_batch(
        self,
        *,
        partition: CatalogPartition,
        previous_partition: CatalogPartition | None,
        next_partition: CatalogPartition | None,
        batch: tuple[Any, ...],
    ) -> None:
        previous_end = previous_partition.coverage.end if previous_partition and previous_partition.coverage else None
        next_start = next_partition.coverage.start if next_partition and next_partition.coverage else None

        for record in batch:
            if record.venue != self.dataset.identity.venue or record.instrument != self.dataset.identity.instrument:
                raise DataIntegrityError(
                    "Parquet record identity does not match catalog dataset",
                    context={"dataset_identity": self.dataset.identity.stable_dict()},
                )
            if previous_end is not None and record.exchange_ts < previous_end:
                raise DataIntegrityError(
                    "record crosses backward over the previous partition boundary",
                    context={"partition": partition.natural_identity.stable_dict()},
                )
            if next_start is not None and record.exchange_ts >= next_start:
                raise DataIntegrityError(
                    "record crosses forward over the next partition boundary",
                    context={"partition": partition.natural_identity.stable_dict()},
                )

            ordering_key = self.ordering_provider.key(record)
            if self._last_ordering_key is not None:
                if ordering_key == self._last_ordering_key:
                    raise DataIntegrityError("duplicate canonical ordering key")
                if ordering_key < self._last_ordering_key:
                    raise DataIntegrityError("canonical records are not in required physical order")
            self._last_ordering_key = ordering_key

            record_time = Instant.parse(record.exchange_ts)
            if self._first_record_time is None:
                self._first_record_time = record_time
            self._last_record_time = record_time
            self._row_count += 1

    def _complete(self) -> None:
        if self._state == ScanState.COMPLETED:
            return
        returned_bounds = None
        if self._first_record_time is not None and self._last_record_time is not None:
            returned_bounds = RecordTimeBounds(
                first=self._first_record_time,
                last=self._last_record_time,
            )
        self._completed_metadata = self._completion_builder(returned_bounds, self._row_count)
        self._state = ScanState.COMPLETED


class DataGateway:
    """Read canonical ``trade-v1`` data through the PostgreSQL catalog."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        batch_reader: BatchReader = scan_trade_v1,
        path_resolver: Callable[[str, str, str], Any] = resolve_partition_path,
        ordering_providers: tuple[OrderingProvider, ...] = (),
    ):
        self.catalog = catalog
        self._batch_reader = batch_reader
        self._path_resolver = path_resolver
        self._ordering_providers = tuple(ordering_providers)

    def scan(self, request: DataRequest, *, batch_size: int = 65_536) -> DataScan:
        """Open a finite bounded-memory historical scan of ordered batches."""

        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")

        ordering_provider, dataset, partitions, eligible_union, gaps = self._prepare(request)
        open_metadata = self._open_metadata(
            request=request,
            dataset=dataset,
            partitions=partitions,
            eligible_coverage=eligible_union,
            coverage_gaps=gaps,
        )

        def completion_builder(returned_bounds: RecordTimeBounds | None, row_count: int) -> DataSliceMetadata:
            return self._metadata(
                request=request,
                dataset=dataset,
                partitions=partitions,
                eligible_coverage=eligible_union,
                coverage_gaps=gaps,
                returned_bounds=returned_bounds,
                row_count=row_count,
            )

        return DataScan(
            request=request,
            dataset=dataset,
            partitions=partitions,
            ordering_provider=ordering_provider,
            open_metadata=open_metadata,
            batch_reader=self._batch_reader,
            path_resolver=self._path_resolver,
            completion_builder=completion_builder,
            batch_size=batch_size,
        )

    def read(self, request: DataRequest) -> DataSlice:
        """Materialize a small finite slice by fully draining :meth:`scan`."""

        scan = self.scan(request)
        records: list[Any] = []
        for batch in scan:
            records.extend(batch)
        metadata = scan.completed_metadata
        if metadata is None:  # defensive: full drain must complete the scan
            raise RuntimeError("DataGateway scan ended without completion metadata")
        return DataSlice(records=tuple(records), metadata=metadata)

    def _prepare(
        self,
        request: DataRequest,
    ) -> tuple[
        OrderingProvider,
        CatalogDataset,
        tuple[CatalogPartition, ...],
        tuple[CoverageInterval, ...],
        tuple[CoverageInterval, ...],
    ]:
        ordering_provider = self._validate_support(request)
        dataset = self.catalog.resolve_dataset(request.dataset_selector)
        if dataset.identity.record_schema_id != request.schema_requirement:
            raise SchemaMismatch(
                "requested schema does not match the catalog dataset",
                context={
                    "requested_schema": request.schema_requirement,
                    "stored_schema": dataset.identity.record_schema_id,
                },
            )
        selected = self.catalog.select_partitions(
            dataset,
            request.start,
            request.end,
            request.lifecycle_policy.states,
        )
        partitions = self._ordered_partitions(selected)
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
        return ordering_provider, dataset, partitions, eligible_union, gaps

    @staticmethod
    def _ordered_partitions(partitions: Iterable[CatalogPartition]) -> tuple[CatalogPartition, ...]:
        return tuple(
            sorted(
                partitions,
                key=lambda partition: (
                    partition.ts_start.epoch_ns if partition.ts_start is not None else -1,
                    partition.ts_end.epoch_ns if partition.ts_end is not None else -1,
                    partition.natural_identity.partition_key,
                    partition.natural_identity.revision,
                ),
            )
        )

    def _validate_support(self, request: DataRequest) -> OrderingProvider:
        identity = request.dataset_selector
        if identity.dataset_kind != "trades" or identity.layer != "canonical":
            raise UnsupportedDatasetKind(
                "DataGateway v1 supports canonical trades only",
                context={"dataset_identity": identity.stable_dict()},
            )
        if identity.record_schema_id != "trade-v1" or request.schema_requirement != "trade-v1":
            raise UnsupportedSchema(
                "DataGateway v1 supports trade-v1 only",
                context={"requested_schema": request.schema_requirement},
            )
        ordering_provider = provider_for(
            request.ordering_policy,
            self._ordering_providers,
        )
        if ordering_provider is None or not ordering_provider.satisfies(TRADES_CANONICAL_TOTAL_ORDER_V1):
            raise UnsupportedSchema(
                "requested ordering policy has no compatible provider",
                context={"ordering_policy": request.ordering_policy},
            )
        if not ordering_provider.applies(identity):
            raise UnsupportedDatasetKind(
                "ordering provider is not applicable to the requested dataset",
                context={
                    "ordering_policy": request.ordering_policy,
                    "dataset_identity": identity.stable_dict(),
                },
            )
        return ordering_provider

    @staticmethod
    def _validate_partition_set(dataset: CatalogDataset, partitions: tuple[CatalogPartition, ...]) -> None:
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

        for previous, current in zip(with_coverage, with_coverage[1:]):
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
    def _open_metadata(
        *,
        request: DataRequest,
        dataset: CatalogDataset,
        partitions: tuple[CatalogPartition, ...],
        eligible_coverage: tuple[CoverageInterval, ...],
        coverage_gaps: tuple[CoverageInterval, ...],
    ) -> DataScanOpenMetadata:
        natural_partitions = tuple(partition.natural_identity for partition in partitions)
        manifest_hashes = (dataset.manifest_sha256,) + tuple(
            partition.manifest_sha256 for partition in partitions if partition.manifest_sha256 is not None
        )
        content_hashes = tuple(
            partition.content_sha256 for partition in partitions if partition.content_sha256 is not None
        )
        return DataScanOpenMetadata(
            dataset_identity=dataset.identity,
            record_schema_id=dataset.identity.record_schema_id,
            schema_version=dataset.schema_version,
            schema_hash=dataset.schema_hash,
            natural_partitions=natural_partitions,
            manifest_hashes=manifest_hashes,
            content_hashes=content_hashes,
            request_identity=request.request_identity,
            requested_interval=CoverageInterval(request.start, request.end),
            eligible_coverage=eligible_coverage,
            coverage_gaps=coverage_gaps,
            coverage_complete=not coverage_gaps,
            ordering_policy=request.ordering_policy,
            lifecycle_policy=request.lifecycle_policy,
            coverage_policy=request.coverage_policy,
            catalog_dataset_id=dataset.catalog_dataset_id,
            catalog_partition_ids=tuple(partition.catalog_partition_id for partition in partitions),
            storage_root_ids=tuple(partition.storage_root_id for partition in partitions),
            rel_paths=tuple(partition.rel_path for partition in partitions),
        )

    @staticmethod
    def _metadata(
        *,
        request: DataRequest,
        dataset: CatalogDataset,
        partitions: tuple[CatalogPartition, ...],
        eligible_coverage: tuple[CoverageInterval, ...],
        coverage_gaps: tuple[CoverageInterval, ...],
        returned_bounds: RecordTimeBounds | None,
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


__all__ = ["DataGateway", "DataScan", "DataScanOpenMetadata", "ScanState"]
