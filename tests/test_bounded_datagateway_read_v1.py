#!/usr/bin/env python3
"""Behavioral tests for the bounded finite DataGateway read path."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.gateway import DataGateway, ScanState  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    DataIntegrityError,
    DataRequest,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
DATASET = CatalogDataset(
    identity=IDENTITY,
    catalog_dataset_id="dataset-a",
    rel_root=REL_ROOT,
    manifest_sha256="a" * 64,
    schema_version=1,
    schema_hash="b" * 64,
)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def request(start: str = "2024-01-01T00:00:00Z", end: str = "2024-01-01T02:00:00Z") -> DataRequest:
    return DataRequest(
        IDENTITY,
        start,
        end,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


def trade(ts: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price="100.0",
        size="0.1",
        aggressor_side="buy",
        trade_id=trade_id,
    )


def partition(
    key: str,
    start: str,
    end: str,
    *,
    partition_id: str,
    rel_path: str | None = None,
) -> CatalogPartition:
    return CatalogPartition(
        natural_identity=NaturalPartitionIdentity(IDENTITY, key, 1),
        catalog_partition_id=partition_id,
        storage_root_id="hot",
        storage_root="/catalog-root",
        dataset_rel_root=REL_ROOT,
        rel_path=rel_path or f"{key}/part-000.parquet",
        ts_start=instant(start),
        ts_end=instant(end),
        row_count=0,
        content_sha256=("c" + partition_id[-1]) * 32,
        manifest_sha256=("d" + partition_id[-1]) * 32,
        state="valid",
        producer="fixture",
        code_ref="test",
    )


LEFT = partition(
    "dt=2024-01-01/hour=00",
    "2024-01-01T00:00:00Z",
    "2024-01-01T01:00:00Z",
    partition_id="partition-a",
)
RIGHT = partition(
    "dt=2024-01-01/hour=01",
    "2024-01-01T01:00:00Z",
    "2024-01-01T02:00:00Z",
    partition_id="partition-b",
)


class FakeCatalog:
    def __init__(self, partitions: list[CatalogPartition]):
        self.partitions = partitions

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        if identity != IDENTITY:
            raise AssertionError(identity)
        return DATASET

    def select_partitions(self, dataset, start, end, states):
        # Deliberately preserve fixture insertion order. DataGateway itself
        # must impose OR3's deterministic coverage ordering.
        return [
            item
            for item in self.partitions
            if item.state in states
            and item.coverage is not None
            and item.ts_end > start
            and item.ts_start < end
        ]


class FakeBatchReader:
    def __init__(self, batches_by_path: dict[str, list[tuple[TradeRecord, ...]]]):
        self.batches_by_path = batches_by_path
        self.calls: list[tuple[str, int]] = []
        self.yields: list[tuple[str, int]] = []

    def __call__(self, path: str, start: Instant, end: Instant, batch_size: int):
        self.calls.append((path, batch_size))
        for index, batch in enumerate(self.batches_by_path.get(path, [])):
            self.yields.append((path, index))
            yield batch


def gateway(
    partitions: list[CatalogPartition],
    batches_by_path: dict[str, list[tuple[TradeRecord, ...]]],
) -> tuple[DataGateway, FakeBatchReader]:
    reader = FakeBatchReader(batches_by_path)
    instance = DataGateway(
        FakeCatalog(partitions),
        batch_reader=reader,
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )
    return instance, reader


class BoundedDataGatewayReadV1Tests(unittest.TestCase):
    def test_scan_lifecycle_exposes_open_metadata_then_final_metadata_only_on_exhaustion(self):
        instance, reader = gateway(
            [LEFT, RIGHT],
            {
                LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
                RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "20"),)],
            },
        )
        scan = instance.scan(request(), batch_size=1)

        self.assertEqual(scan.state, ScanState.OPEN)
        self.assertIsNone(scan.completed_metadata)
        self.assertFalse(hasattr(scan.open_metadata, "result_identity"))
        self.assertFalse(hasattr(scan.open_metadata, "row_count"))
        self.assertFalse(hasattr(scan.open_metadata, "returned_record_bounds"))
        self.assertEqual(reader.calls, [])
        self.assertEqual(reader.yields, [])

        first_batch = next(scan)
        self.assertEqual([row.trade_id for row in first_batch], ["10"])
        self.assertEqual(scan.state, ScanState.READING)
        self.assertIsNone(scan.completed_metadata)
        self.assertEqual(reader.yields, [(LEFT.rel_path, 0)])

        remaining = list(scan)
        self.assertEqual([[row.trade_id for row in batch] for batch in remaining], [["20"]])
        self.assertEqual(scan.state, ScanState.COMPLETED)
        metadata = scan.completed_metadata
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.row_count, 2)
        self.assertEqual(metadata.returned_record_bounds.first, instant("2024-01-01T00:10:00Z"))
        self.assertEqual(metadata.returned_record_bounds.last, instant("2024-01-01T01:10:00Z"))
        self.assertTrue(metadata.result_identity)
        self.assertEqual(reader.calls, [(LEFT.rel_path, 1), (RIGHT.rel_path, 1)])

    def test_partial_consumption_can_be_aborted_without_completed_provenance(self):
        instance, _ = gateway(
            [LEFT],
            {LEFT.rel_path: [
                (trade("2024-01-01T00:10:00Z", "10"),),
                (trade("2024-01-01T00:20:00Z", "20"),),
            ]},
        )
        scan = instance.scan(request(end="2024-01-01T01:00:00Z"), batch_size=1)
        next(scan)
        self.assertEqual(scan.state, ScanState.READING)
        scan.close()
        self.assertEqual(scan.state, ScanState.ABORTED)
        self.assertIsNone(scan.completed_metadata)
        with self.assertRaises(StopIteration):
            next(scan)

    def test_source_batches_are_consumed_lazily_not_drained_at_open_or_first_yield(self):
        instance, reader = gateway(
            [LEFT],
            {LEFT.rel_path: [
                (trade("2024-01-01T00:10:00Z", "10"),),
                (trade("2024-01-01T00:20:00Z", "20"),),
                (trade("2024-01-01T00:30:00Z", "30"),),
            ]},
        )
        scan = instance.scan(request(end="2024-01-01T01:00:00Z"), batch_size=1)
        self.assertEqual(reader.yields, [])
        next(scan)
        self.assertEqual(reader.yields, [(LEFT.rel_path, 0)])
        next(scan)
        self.assertEqual(reader.yields, [(LEFT.rel_path, 0), (LEFT.rel_path, 1)])
        scan.close()

    def test_scan_rejects_misordered_physical_rows_instead_of_repairing_them(self):
        instance, _ = gateway(
            [LEFT],
            {LEFT.rel_path: [(
                trade("2024-01-01T00:10:00Z", "20"),
                trade("2024-01-01T00:10:00Z", "10"),
            )]},
        )
        scan = instance.scan(request(end="2024-01-01T01:00:00Z"))
        with self.assertRaisesRegex(DataIntegrityError, "required physical order"):
            next(scan)
        self.assertEqual(scan.state, ScanState.ABORTED)
        self.assertIsNone(scan.completed_metadata)

    def test_scan_rejects_duplicate_canonical_ordering_key_incrementally(self):
        instance, _ = gateway(
            [LEFT],
            {LEFT.rel_path: [(
                trade("2024-01-01T00:10:00Z", "10"),
                trade("2024-01-01T00:10:00Z", "10"),
            )]},
        )
        scan = instance.scan(request(end="2024-01-01T01:00:00Z"))
        with self.assertRaisesRegex(DataIntegrityError, "duplicate canonical ordering key"):
            next(scan)
        self.assertEqual(scan.state, ScanState.ABORTED)

    def test_partitions_are_processed_by_declared_coverage_not_catalog_insertion_order(self):
        instance, reader = gateway(
            [RIGHT, LEFT],
            {
                LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
                RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "20"),)],
            },
        )
        scan = instance.scan(request(), batch_size=4)
        ids = [row.trade_id for batch in scan for row in batch]
        self.assertEqual(ids, ["10", "20"])
        self.assertEqual([path for path, _ in reader.calls], [LEFT.rel_path, RIGHT.rel_path])
        metadata = scan.completed_metadata
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(
            [item.partition_key for item in metadata.natural_partitions],
            [LEFT.natural_identity.partition_key, RIGHT.natural_identity.partition_key],
        )

    def test_forward_partition_boundary_violation_fails_closed(self):
        instance, _ = gateway(
            [LEFT, RIGHT],
            {
                LEFT.rel_path: [(trade("2024-01-01T01:00:00Z", "10"),)],
                RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "20"),)],
            },
        )
        scan = instance.scan(request())
        with self.assertRaisesRegex(DataIntegrityError, "crosses forward"):
            next(scan)
        self.assertEqual(scan.state, ScanState.ABORTED)

    def test_backward_partition_boundary_violation_fails_closed(self):
        instance, _ = gateway(
            [LEFT, RIGHT],
            {
                LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
                RIGHT.rel_path: [(trade("2024-01-01T00:59:59Z", "20"),)],
            },
        )
        scan = instance.scan(request())
        self.assertEqual(next(scan)[0].trade_id, "10")
        with self.assertRaisesRegex(DataIntegrityError, "crosses backward"):
            next(scan)
        self.assertEqual(scan.state, ScanState.ABORTED)

    def test_fully_covered_zero_record_scan_completes_with_null_record_bounds(self):
        instance, _ = gateway([LEFT], {LEFT.rel_path: []})
        scan = instance.scan(request(end="2024-01-01T01:00:00Z"))
        self.assertEqual(list(scan), [])
        self.assertEqual(scan.state, ScanState.COMPLETED)
        metadata = scan.completed_metadata
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.row_count, 0)
        self.assertIsNone(metadata.returned_record_bounds)
        self.assertTrue(metadata.coverage_complete)

    def test_read_is_the_materializing_convenience_over_the_bounded_scan(self):
        batches = {
            LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
            RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "20"),)],
        }
        scan_gateway, _ = gateway([LEFT, RIGHT], batches)
        scan = scan_gateway.scan(request())
        scanned_records = tuple(row for batch in scan for row in batch)
        scan_metadata = scan.completed_metadata

        read_gateway, _ = gateway([LEFT, RIGHT], batches)
        materialized = read_gateway.read(request())

        self.assertEqual(materialized.records, scanned_records)
        self.assertIsNotNone(scan_metadata)
        assert scan_metadata is not None
        self.assertEqual(materialized.metadata.result_identity, scan_metadata.result_identity)
        self.assertEqual(materialized.metadata.row_count, scan_metadata.row_count)

    def test_batch_size_must_be_positive_integer(self):
        instance, _ = gateway([LEFT], {LEFT.rel_path: []})
        for bad in (0, -1, True):
            with self.subTest(batch_size=bad):
                with self.assertRaises(ValueError):
                    instance.scan(request(end="2024-01-01T01:00:00Z"), batch_size=bad)

    def test_streaming_implementation_has_no_full_file_table_or_global_result_sort(self):
        parquet_source = (ROOT / "src" / "quant_platform" / "data" / "parquet.py").read_text(encoding="utf-8")
        gateway_source = (ROOT / "src" / "quant_platform" / "data" / "gateway.py").read_text(encoding="utf-8")
        self.assertIn("scanner.to_batches()", parquet_source)
        self.assertNotIn("scanner.to_table()", parquet_source)
        self.assertNotIn("records.sort(", gateway_source)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
