#!/usr/bin/env python3
"""Focused tests for the additive D03 candle leg of Golden Conformity.

These cover the Application-owned bridge that streams one ``DataGateway``
scan into the accepted D03 historical candle seam
(``quant_platform.application.golden_conformity.observe_scan_with_candles``)
without a second data-access path or a redundant full-day copy, plus the
backward-compatible extension of the Golden fixture schema.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.gateway import (  # noqa: E402
    DataGateway,
    ScanState,
)
from quant_platform.access.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    DataRequest,
    LifecyclePolicy,
)
from quant_platform.application.golden_conformity import (  # noqa: E402
    GoldenCandleExpectation,
    GoldenExpectation,
    build_candle_source_evidence,
    candle_field_mismatches,
    format_observation,
    observe_scan_with_candles,
)
from quant_platform.data.models import (  # noqa: E402
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform.representation.candles import CandleDefinitionV1  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)


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


def request(start: str = "2024-01-15T00:00:00Z", end: str = "2024-01-15T00:02:00Z") -> DataRequest:
    return DataRequest(
        IDENTITY,
        start,
        end,
        lifecycle_policy=LifecyclePolicy.VALID_ONLY,
        coverage_policy="strict",
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


def trade(ts: str, price: str, size: str, side: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price=price,
        size=size,
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
    instant("2024-01-15T00:02:00Z"),
    0,
    "c" * 64,
    "d" * 64,
    "valid",
    "fixture",
    "test",
)


class Catalog:
    def resolve_dataset(self, identity):
        return DATASET

    def select_partitions(self, dataset, start, end, states):
        return [PARTITION]


class Reader:
    def __init__(self, batches):
        self.batches = batches
        self.call_count = 0

    def __call__(self, path, start, end, batch_size):
        self.call_count += 1
        yield from self.batches


TWO_MINUTE_TRADES = (
    (
        trade("2024-01-15T00:00:00.100Z", "100.00", "1.0", "buy", "1"),
        trade("2024-01-15T00:00:30Z", "101.00", "1.0", "sell", "2"),
    ),
    (
        trade("2024-01-15T00:01:00Z", "102.00", "2.0", "buy", "3"),
        trade("2024-01-15T00:01:45Z", "103.00", "1.0", "buy", "4"),
    ),
)


def make_gateway(batches):
    reader = Reader(batches)
    gateway = DataGateway(
        Catalog(),
        batch_reader=reader,
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )
    return gateway, reader


def golden_candle_payload(**overrides):
    payload = {
        "duration": "1m",
        "definition_identity": "candle-definition-v1:sha256:" + "0" * 64,
        "candle_count": 0,
        "first_candle": {},
        "last_candle": {},
        "result_identity": "historical-candle-result-v1:sha256:" + "0" * 64,
    }
    payload.update(overrides)
    return payload


class BuildCandleSourceEvidenceTests(unittest.TestCase):
    def test_evidence_is_derived_from_open_metadata_only(self):
        gateway, reader = make_gateway(TWO_MINUTE_TRADES)
        scan = gateway.scan(request(), batch_size=2)
        evidence = build_candle_source_evidence(scan.open_metadata)

        self.assertEqual(0, reader.call_count)
        self.assertEqual(IDENTITY, evidence.dataset_identity)
        self.assertEqual(TRADES_CANONICAL_TOTAL_ORDER_V1, evidence.source_ordering_policy)
        self.assertEqual(BYBIT_TRADE_V1_ORDERING_POLICY, evidence.concrete_ordering_policy)
        self.assertEqual(scan.open_metadata.eligible_coverage, evidence.eligible_source_coverage)
        self.assertEqual(scan.open_metadata.eligible_coverage, evidence.finalized_source_intervals)
        self.assertTrue(evidence.finalization_evidence)
        self.assertTrue(evidence.natural_partitions)
        self.assertTrue(evidence.manifest_hashes)
        self.assertTrue(evidence.content_hashes)


class ObserveScanWithCandlesTests(unittest.TestCase):
    def test_single_scan_produces_matching_trade_and_candle_observation(self):
        gateway, reader = make_gateway(TWO_MINUTE_TRADES)
        scan = gateway.scan(request(), batch_size=2)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())

        observation, result = observe_scan_with_candles(scan, expectation)

        self.assertEqual(1, reader.call_count, "exactly one physical read of the day")
        self.assertEqual(ScanState.COMPLETED, scan.state)
        self.assertIs(scan.completed_metadata, observation.completed_metadata)

        # Trade-level tallies match what observe_scan would have reported.
        self.assertEqual(4, observation.row_count)
        self.assertEqual(3, observation.buy)
        self.assertEqual(1, observation.sell)
        self.assertEqual("bybit", observation.observed_venue)
        self.assertEqual("BTCUSDT", observation.observed_instrument)

        # D03 candle result over the same trades.
        self.assertEqual(2, result.row_count)
        first, last = result.records[0], result.records[-1]
        self.assertEqual("2024-01-15T00:00:00Z", first.bucket_start)
        self.assertEqual("100", first.open)
        self.assertEqual("101", first.high)
        self.assertEqual("100", first.low)
        self.assertEqual("101", first.close)
        self.assertEqual("2", first.volume)
        self.assertEqual("2", first.trade_count)
        self.assertEqual("2024-01-15T00:01:00Z", last.bucket_start)
        self.assertEqual("103", last.close)
        self.assertEqual(
            CandleDefinitionV1.from_duration("1m").definition_identity,
            result.definition_identity,
        )

    def test_rejects_a_scan_that_is_not_initially_open(self):
        gateway, _reader = make_gateway(TWO_MINUTE_TRADES)
        scan = gateway.scan(request(), batch_size=2)
        next(scan)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())
        with self.assertRaisesRegex(ValueError, "initially OPEN"):
            observe_scan_with_candles(scan, expectation)

    def test_result_identity_is_deterministic_across_independent_scans(self):
        gateway_a, _ = make_gateway(TWO_MINUTE_TRADES)
        gateway_b, _ = make_gateway(TWO_MINUTE_TRADES)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())

        _observation_a, result_a = observe_scan_with_candles(gateway_a.scan(request(), batch_size=2), expectation)
        _observation_b, result_b = observe_scan_with_candles(gateway_b.scan(request(), batch_size=1), expectation)

        self.assertEqual(result_a.result_identity, result_b.result_identity)
        self.assertEqual(result_a.records, result_b.records)


class CandleFieldMismatchesTests(unittest.TestCase):
    def _result(self):
        gateway, _ = make_gateway(TWO_MINUTE_TRADES)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())
        _observation, result = observe_scan_with_candles(gateway.scan(request(), batch_size=2), expectation)
        return result

    def test_matching_frozen_expectation_reports_no_mismatch(self):
        result = self._result()
        frozen = GoldenCandleExpectation.from_mapping(
            golden_candle_payload(
                definition_identity=result.definition_identity,
                candle_count=result.row_count,
                first_candle=result.records[0].stable_dict(),
                last_candle=result.records[-1].stable_dict(),
                result_identity=result.result_identity,
            )
        )
        self.assertEqual((), candle_field_mismatches(frozen, result))

    def test_wrong_candle_count_is_reported_without_normalizing(self):
        result = self._result()
        wrong = GoldenCandleExpectation.from_mapping(
            golden_candle_payload(
                definition_identity=result.definition_identity,
                candle_count=result.row_count + 1,
                first_candle=result.records[0].stable_dict(),
                last_candle=result.records[-1].stable_dict(),
                result_identity=result.result_identity,
            )
        )
        mismatches = candle_field_mismatches(wrong, result)
        self.assertEqual(1, len(mismatches))
        self.assertIn("candle_count", mismatches[0])

    def test_wrong_first_candle_payload_is_reported(self):
        result = self._result()
        drifted_first = dict(result.records[0].stable_dict())
        drifted_first["open"] = "999.00"
        wrong = GoldenCandleExpectation.from_mapping(
            golden_candle_payload(
                definition_identity=result.definition_identity,
                candle_count=result.row_count,
                first_candle=drifted_first,
                last_candle=result.records[-1].stable_dict(),
                result_identity=result.result_identity,
            )
        )
        mismatches = candle_field_mismatches(wrong, result)
        self.assertTrue(any(item.startswith("first_candle") for item in mismatches))

    def test_wrong_result_identity_is_reported(self):
        result = self._result()
        wrong = GoldenCandleExpectation.from_mapping(
            golden_candle_payload(
                definition_identity=result.definition_identity,
                candle_count=result.row_count,
                first_candle=result.records[0].stable_dict(),
                last_candle=result.records[-1].stable_dict(),
                result_identity="historical-candle-result-v1:sha256:" + "f" * 64,
            )
        )
        mismatches = candle_field_mismatches(wrong, result)
        self.assertTrue(any(item.startswith("candle_result_identity") for item in mismatches))


class GoldenExpectationCandleSchemaTests(unittest.TestCase):
    BASE_FIXTURE = {
        "venue": "bybit",
        "instrument": "BTCUSDT",
        "interval": {"start": "2024-01-15T00:00:00Z", "end": "2024-01-16T00:00:00Z"},
        "row_count": 1,
        "buy": 1,
        "sell": 0,
        "first_exchange_ts": "2024-01-15T00:00:00.492Z",
        "last_exchange_ts": "2024-01-15T00:00:00.492Z",
    }

    def test_fixture_without_candle_section_parses_with_candle_none(self):
        expectation = GoldenExpectation.from_mapping(dict(self.BASE_FIXTURE))
        self.assertIsNone(expectation.candle)

    def test_fixture_with_candle_section_parses_it(self):
        payload = dict(self.BASE_FIXTURE)
        payload["candle"] = golden_candle_payload()
        expectation = GoldenExpectation.from_mapping(payload)
        self.assertIsInstance(expectation.candle, GoldenCandleExpectation)
        self.assertEqual("1m", expectation.candle.duration)

    def test_candle_section_rejects_zero_count_with_declared_first_candle(self):
        with self.assertRaises(ValueError):
            GoldenCandleExpectation.from_mapping(
                golden_candle_payload(candle_count=0, first_candle={"open": "1"}, last_candle={})
            )

    def test_candle_section_rejects_unknown_fields(self):
        payload = golden_candle_payload()
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            GoldenCandleExpectation.from_mapping(payload)


class FormatObservationCandleRenderingTests(unittest.TestCase):
    def test_rendering_without_candle_is_unchanged_shape(self):
        gateway, _ = make_gateway(TWO_MINUTE_TRADES)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())
        observation, _result = observe_scan_with_candles(gateway.scan(request(), batch_size=2), expectation)
        rendered = format_observation(observation)
        self.assertNotIn("CANDLE RESULT", rendered)

    def test_rendering_with_candle_includes_compact_summary(self):
        gateway, _ = make_gateway(TWO_MINUTE_TRADES)
        expectation = GoldenCandleExpectation.from_mapping(golden_candle_payload())
        observation, result = observe_scan_with_candles(gateway.scan(request(), batch_size=2), expectation)
        rendered = format_observation(observation, candle=result)
        self.assertIn("CANDLE RESULT", rendered)
        self.assertIn(result.result_identity, rendered)
        self.assertIn(result.definition_identity, rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
