#!/usr/bin/env python3
"""Behavioral tests for Consumer Golden Conformity acceptance support."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from golden_conformity_support import (  # noqa: E402
    GoldenExpectation,
    format_observation,
    golden_field_mismatches,
    load_golden_expectation,
    observe_scan,
)
from quant_platform.data.gateway import DataGateway, ScanState  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    DataRequest,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET = CatalogDataset(
    IDENTITY,
    "dataset-a",
    "canonical/trades/bybit/BTCUSDT/trade-v1",
    "a" * 64,
    1,
    "b" * 64,
)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def request() -> DataRequest:
    return DataRequest(
        IDENTITY,
        "2024-01-15T00:00:00Z",
        "2024-01-16T00:00:00Z",
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


def record(ts: str, trade_id: str, side: str = "buy") -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price="100.0",
        size="0.1",
        aggressor_side=side,
        trade_id=trade_id,
    )


PARTITION = CatalogPartition(
    NaturalPartitionIdentity(IDENTITY, "dt=2024-01-15", 1),
    "partition-a",
    "hot",
    "/catalog-root",
    "canonical/trades/bybit/BTCUSDT/trade-v1",
    "dt=2024-01-15/part-000.parquet",
    instant("2024-01-15T00:00:00Z"),
    instant("2024-01-16T00:00:00Z"),
    0,
    "c" * 64,
    "d" * 64,
    "valid",
    "fixture",
    "test",
)


class Catalog:
    def resolve_dataset(self, identity):
        self.assert_identity = identity
        return DATASET

    def select_partitions(self, dataset, start, end, states):
        return [PARTITION]


class Reader:
    def __init__(self, batches):
        self.batches = batches

    def __call__(self, path, start, end, batch_size):
        yield from self.batches


def make_scan(batches):
    return DataGateway(
        Catalog(),
        batch_reader=Reader(batches),
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    ).scan(request(), batch_size=2)


class GoldenConformitySupportV1Tests(unittest.TestCase):
    def test_frozen_fixture_loads_as_data_only_expectation(self):
        expected = load_golden_expectation()
        self.assertEqual(expected.venue, "bybit")
        self.assertEqual(expected.instrument, "BTCUSDT")
        self.assertEqual(expected.row_count, 1_105_145)
        self.assertEqual(expected.buy, 553_875)
        self.assertEqual(expected.sell, 551_270)
        self.assertEqual(expected.interval_start, "2024-01-15T00:00:00Z")
        self.assertEqual(expected.interval_end, "2024-01-16T00:00:00Z")

    def test_loader_rejects_business_or_unknown_fixture_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                '{"venue":"bybit","instrument":"BTCUSDT","interval":{"start":"2024-01-15T00:00:00Z","end":"2024-01-16T00:00:00Z"},"row_count":1,"buy":1,"sell":0,"first_exchange_ts":"2024-01-15T00:00:00.492Z","last_exchange_ts":"2024-01-15T23:59:59.931Z","publication_state":"pass"}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_golden_expectation(path)

    def test_observer_consumes_incrementally_and_exposes_completion_metadata(self):
        scan = make_scan(
            [
                (record("2024-01-15T00:00:00.492Z", "1"), record("2024-01-15T12:00:00Z", "2", "sell")),
                (record("2024-01-15T23:59:59.931Z", "3"),),
            ]
        )
        self.assertEqual(scan.state, ScanState.OPEN)
        self.assertIsNone(scan.completed_metadata)
        observation = observe_scan(scan)
        self.assertEqual(observation.initial_state, ScanState.OPEN)
        self.assertEqual(observation.state_after_first_batch, ScanState.READING)
        self.assertEqual(observation.final_state, ScanState.COMPLETED)
        self.assertEqual(observation.row_count, 3)
        self.assertEqual(observation.buy, 2)
        self.assertEqual(observation.sell, 1)
        self.assertEqual(observation.other_aggressor_side, 0)
        self.assertEqual(observation.first_exchange_ts, instant("2024-01-15T00:00:00.492Z"))
        self.assertEqual(observation.last_exchange_ts, instant("2024-01-15T23:59:59.931Z"))
        self.assertEqual(observation.batch_count, 2)
        self.assertEqual(observation.max_batch_size, 2)
        self.assertIs(observation.completed_metadata, scan.completed_metadata)

    def test_zero_rows_complete_scan_has_no_record_bounds(self):
        empty = observe_scan(make_scan([]))
        self.assertEqual(empty.final_state, ScanState.COMPLETED)
        self.assertEqual(empty.row_count, 0)
        self.assertIsNone(empty.completed_metadata.returned_record_bounds)

    def test_observer_rejects_partially_consumed_scan_without_consuming_tail(self):
        scan = make_scan(
            [
                (
                    record("2024-01-15T00:00:00Z", "1"),
                ),
                (
                    record("2024-01-15T00:01:00Z", "2"),
                ),
            ]
        )
        self.assertEqual(scan.state, ScanState.OPEN)
        self.assertEqual(next(scan)[0].trade_id, "1")
        self.assertEqual(scan.state, ScanState.READING)
        samples = []
        with self.assertRaisesRegex(ValueError, "initially OPEN"):
            observe_scan(scan, memory_sampler=lambda: samples.append(1) or 100)
        self.assertEqual(samples, [])
        self.assertEqual(next(scan)[0].trade_id, "2")

    def test_observer_rejects_already_completed_scan(self):
        scan = make_scan([])
        self.assertEqual(list(scan), [])
        self.assertEqual(scan.state, ScanState.COMPLETED)
        metadata = scan.completed_metadata
        with self.assertRaisesRegex(ValueError, "initially OPEN"):
            observe_scan(scan)
        self.assertIs(scan.completed_metadata, metadata)

    def test_observer_rejects_already_aborted_scan(self):
        scan = make_scan([(record("2024-01-15T00:00:00Z", "1"),)])
        scan.close()
        self.assertEqual(scan.state, ScanState.ABORTED)
        with self.assertRaisesRegex(ValueError, "initially OPEN"):
            observe_scan(scan)
        self.assertIsNone(scan.completed_metadata)

    def test_memory_sampler_keeps_compact_telemetry(self):
        samples = iter([100, 105, 103, 110])
        observation = observe_scan(
            make_scan(
                [
                    (record("2024-01-15T00:00:00Z", "1"),),
                    (record("2024-01-15T00:01:00Z", "2", "sell"),),
                ]
            ),
            memory_sampler=lambda: next(samples),
        )
        self.assertIsNotNone(observation.boundedness)
        assert observation.boundedness is not None
        self.assertEqual(observation.boundedness.baseline_sample, 100)
        self.assertEqual(observation.boundedness.peak_sample, 110)
        self.assertEqual(observation.boundedness.peak_delta, 10)
        self.assertEqual(observation.boundedness.sample_count, 4)

    def test_unexpected_observation_is_reported_as_field_mismatches(self):
        observation = observe_scan(
            make_scan([(record("2024-01-15T00:00:00Z", "1", "buy"),)])
        )
        expected = GoldenExpectation(
            "bybit",
            "BTCUSDT",
            "2024-01-15T00:00:00Z",
            "2024-01-16T00:00:00Z",
            2,
            1,
            1,
            "2024-01-15T00:00:00Z",
            "2024-01-15T00:01:00Z",
        )
        mismatches = golden_field_mismatches(expected, observation)
        self.assertIn("row_count", " ".join(mismatches))
        self.assertIn("sell", " ".join(mismatches))

    def test_report_contains_observation_sections_without_gate_pass(self):
        expected = load_golden_expectation()
        observation = observe_scan(make_scan([]))
        report = format_observation(observation, expected)
        for section in (
            "GOLDEN EXPECTATION",
            "SCAN OBSERVATION",
            "LIFECYCLE",
            "COVERAGE METADATA",
            "PROVENANCE",
            "BOUNDEDNESS TELEMETRY",
        ):
            self.assertIn(section, report)
        self.assertNotIn("CONFORMITY GATE PASS", report)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
