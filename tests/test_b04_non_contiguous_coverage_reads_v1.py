#!/usr/bin/env python3
"""B04 non-contiguous coverage read semantics."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.gateway import DataGateway, ScanState  # noqa: E402
from quant_platform.access.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    CoveragePolicy,
    DataRequest,
)
from quant_platform.data.models import (  # noqa: E402
    CatalogConflict,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    NoCoverage,
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
    catalog_dataset_id="dataset-b04",
    rel_root=REL_ROOT,
    manifest_sha256="a" * 64,
    schema_version=1,
    schema_hash="b" * 64,
)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def request(
    start: str = "2024-01-01T00:00:00Z",
    end: str = "2024-01-01T03:00:00Z",
    *,
    coverage_policy: CoveragePolicy | str = CoveragePolicy.STRICT,
) -> DataRequest:
    return DataRequest(
        IDENTITY,
        start,
        end,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
        coverage_policy=coverage_policy,
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
    suffix: str,
    state: str = "valid",
) -> CatalogPartition:
    return CatalogPartition(
        natural_identity=NaturalPartitionIdentity(IDENTITY, key, 1),
        catalog_partition_id=f"partition-{suffix}",
        storage_root_id="hot",
        storage_root="/catalog-root",
        dataset_rel_root=REL_ROOT,
        rel_path=f"{key}/part-{suffix}.parquet",
        ts_start=instant(start),
        ts_end=instant(end),
        row_count=0,
        content_sha256=suffix * 64,
        manifest_sha256=suffix.upper() * 64,
        state=state,
        producer="fixture",
        code_ref="test",
    )


ISLAND_00_01 = partition(
    "dt=2024-01-01/hour=00",
    "2024-01-01T00:00:00Z",
    "2024-01-01T01:00:00Z",
    suffix="c",
)
ISLAND_01_02 = partition(
    "dt=2024-01-01/hour=01",
    "2024-01-01T01:00:00Z",
    "2024-01-01T02:00:00Z",
    suffix="d",
)
ISLAND_02_03 = partition(
    "dt=2024-01-01/hour=02",
    "2024-01-01T02:00:00Z",
    "2024-01-01T03:00:00Z",
    suffix="e",
)
OVERLAP_00_0130 = partition(
    "dt=2024-01-01/overlap=00",
    "2024-01-01T00:30:00Z",
    "2024-01-01T01:30:00Z",
    suffix="f",
)


class FakeCatalog:
    def __init__(self, partitions: list[CatalogPartition]):
        self.partitions = partitions

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        if identity != IDENTITY:
            raise AssertionError(identity)
        return DATASET

    def select_partitions(self, _dataset, start, end, states):
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

    def __call__(self, path: str, _start: Instant, _end: Instant, batch_size: int):
        self.calls.append((path, batch_size))
        yield from self.batches_by_path.get(path, [])


def gateway(
    partitions: list[CatalogPartition],
    batches_by_path: dict[str, list[tuple[TradeRecord, ...]]] | None = None,
) -> tuple[DataGateway, FakeBatchReader]:
    reader = FakeBatchReader(batches_by_path or {})
    instance = DataGateway(
        FakeCatalog(partitions),
        batch_reader=reader,
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )
    return instance, reader


def stable_intervals(metadata) -> tuple[dict[str, str], ...]:
    return tuple(item.stable_dict() for item in metadata.eligible_coverage)


def stable_gaps(metadata) -> tuple[dict[str, str], ...]:
    return tuple(item.stable_dict() for item in metadata.coverage_gaps)


class CoveragePolicyRequestTests(unittest.TestCase):
    def test_default_request_policy_is_strict_and_identity_distinguishes_allow_partial(self):
        strict = request()
        partial = request(coverage_policy=CoveragePolicy.ALLOW_PARTIAL)

        self.assertEqual(CoveragePolicy.STRICT, strict.coverage_policy)
        self.assertEqual("strict", strict.stable_dict()["coverage_policy"])
        self.assertEqual("allow-partial", partial.stable_dict()["coverage_policy"])
        self.assertNotEqual(strict.request_identity, partial.request_identity)
        self.assertEqual(
            CoveragePolicy.ALLOW_PARTIAL,
            request(coverage_policy="ALLOW_PARTIAL").coverage_policy,
        )

    def test_full_contiguous_coverage_succeeds_under_both_policies(self):
        batches = {
            ISLAND_00_01.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
            ISLAND_01_02.rel_path: [(trade("2024-01-01T01:10:00Z", "20"),)],
            ISLAND_02_03.rel_path: [(trade("2024-01-01T02:10:00Z", "30"),)],
        }

        strict_result = gateway([ISLAND_02_03, ISLAND_00_01, ISLAND_01_02], batches)[0].read(request())
        partial_result = gateway([ISLAND_02_03, ISLAND_00_01, ISLAND_01_02], batches)[0].read(
            request(coverage_policy=CoveragePolicy.ALLOW_PARTIAL)
        )

        self.assertEqual(["10", "20", "30"], [record.trade_id for record in strict_result.records])
        self.assertEqual(
            [record.trade_id for record in strict_result.records],
            [record.trade_id for record in partial_result.records],
        )
        self.assertTrue(strict_result.metadata.coverage_complete)
        self.assertTrue(partial_result.metadata.coverage_complete)
        self.assertEqual((), strict_result.metadata.coverage_gaps)
        self.assertEqual(stable_intervals(strict_result.metadata), stable_intervals(partial_result.metadata))

    def test_internal_gap_is_strict_no_coverage_and_allow_partial_success(self):
        batches = {
            ISLAND_00_01.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)],
            ISLAND_02_03.rel_path: [(trade("2024-01-01T02:10:00Z", "20"),)],
        }

        strict_gateway, _ = gateway([ISLAND_00_01, ISLAND_02_03], batches)
        with self.assertRaises(NoCoverage):
            strict_gateway.read(request())

        partial_gateway, reader = gateway([ISLAND_00_01, ISLAND_02_03], batches)
        result = partial_gateway.read(request(coverage_policy=CoveragePolicy.ALLOW_PARTIAL))

        self.assertEqual(["10", "20"], [record.trade_id for record in result.records])
        self.assertEqual([(ISLAND_00_01.rel_path, 65536), (ISLAND_02_03.rel_path, 65536)], reader.calls)
        self.assertFalse(result.metadata.coverage_complete)
        self.assertEqual(
            ({"start": "2024-01-01T01:00:00Z", "end": "2024-01-01T02:00:00Z"},),
            stable_gaps(result.metadata),
        )

    def test_leading_trailing_and_combined_gaps_are_exact(self):
        cases = [
            (
                "leading",
                request("2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z", coverage_policy=CoveragePolicy.ALLOW_PARTIAL),
                ({"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},),
            ),
            (
                "trailing",
                request("2024-01-01T01:00:00Z", "2024-01-01T03:00:00Z", coverage_policy=CoveragePolicy.ALLOW_PARTIAL),
                ({"start": "2024-01-01T02:00:00Z", "end": "2024-01-01T03:00:00Z"},),
            ),
            (
                "both",
                request("2024-01-01T00:00:00Z", "2024-01-01T03:00:00Z", coverage_policy=CoveragePolicy.ALLOW_PARTIAL),
                (
                    {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},
                    {"start": "2024-01-01T02:00:00Z", "end": "2024-01-01T03:00:00Z"},
                ),
            ),
        ]
        for name, item, expected in cases:
            with self.subTest(name=name):
                result = gateway([ISLAND_01_02])[0].read(item)
                self.assertEqual(expected, stable_gaps(result.metadata))
                self.assertFalse(result.metadata.coverage_complete)

    def test_covered_zero_event_interval_succeeds_but_zero_support_fails(self):
        covered = gateway([ISLAND_00_01])[0].read(
            request(
                "2024-01-01T00:00:00Z",
                "2024-01-01T01:00:00Z",
                coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
            )
        )
        self.assertEqual(0, covered.metadata.row_count)
        self.assertEqual((), covered.records)
        self.assertTrue(covered.metadata.coverage_complete)
        self.assertIsNone(covered.metadata.returned_record_bounds)

        with self.assertRaises(NoCoverage):
            gateway([ISLAND_00_01])[0].read(
                request(
                    "2024-01-01T03:00:00Z",
                    "2024-01-01T04:00:00Z",
                    coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
                )
            )

    def test_half_open_adjacent_coverage_merges_without_gap(self):
        result = gateway([ISLAND_01_02, ISLAND_00_01])[0].read(
            request(
                "2024-01-01T00:00:00Z",
                "2024-01-01T02:00:00Z",
                coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
            )
        )

        self.assertEqual((), result.metadata.coverage_gaps)
        self.assertEqual(
            ({"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T02:00:00Z"},),
            stable_intervals(result.metadata),
        )

    def test_overlap_conflict_fails_under_both_policies(self):
        for policy in (CoveragePolicy.STRICT, CoveragePolicy.ALLOW_PARTIAL):
            with self.subTest(policy=policy.value):
                with self.assertRaises(CatalogConflict):
                    gateway([ISLAND_00_01, OVERLAP_00_0130])[0].read(
                        request(
                            "2024-01-01T00:30:00Z",
                            "2024-01-01T01:00:00Z",
                            coverage_policy=policy,
                        )
                    )

    def test_same_rows_with_different_gap_shape_have_distinct_result_identity(self):
        batches = {ISLAND_01_02.rel_path: [(trade("2024-01-01T01:30:00Z", "same"),)]}
        full = gateway([ISLAND_01_02], batches)[0].read(
            request(
                "2024-01-01T01:00:00Z",
                "2024-01-01T02:00:00Z",
                coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
            )
        )
        partial = gateway([ISLAND_01_02], batches)[0].read(
            request(
                "2024-01-01T00:00:00Z",
                "2024-01-01T03:00:00Z",
                coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
            )
        )

        self.assertEqual(["same"], [record.trade_id for record in full.records])
        self.assertEqual(["same"], [record.trade_id for record in partial.records])
        self.assertNotEqual(full.metadata.result_identity, partial.metadata.result_identity)

    def test_aborted_partial_scan_has_no_final_result_metadata(self):
        batches = {ISLAND_00_01.rel_path: [(trade("2024-01-01T00:10:00Z", "10"),)]}
        scan = gateway([ISLAND_00_01, ISLAND_02_03], batches)[0].scan(
            request(coverage_policy=CoveragePolicy.ALLOW_PARTIAL),
            batch_size=1,
        )

        next(scan)
        scan.close()

        self.assertEqual(ScanState.ABORTED, scan.state)
        self.assertIsNone(scan.completed_metadata)


if __name__ == "__main__":
    unittest.main()
