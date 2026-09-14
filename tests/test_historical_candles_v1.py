#!/usr/bin/env python3
"""D03 historical CLOSED candle runtime proof for CandleDefinition v1."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import (  # noqa: E402
    CoverageInterval,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    CandleCoverageError,
    CandleDefinitionV1,
    CandleFinalizationError,
    CandleOrderingError,
    CandleProvenanceError,
    HistoricalCandleSourceEvidence,
    aggregate_historical_candles,
    build_historical_candle_result,
    required_bucket_support,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
IMPLEMENTATION = "quant_platform.representation.candles:d03-v1"


def instant(value: str) -> Instant:
    return Instant.parse(value)


def interval(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(instant(start), instant(end))


def trade(ts: str, price: str, size: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price=price,
        size=size,
        aggressor_side="buy",
        trade_id=trade_id,
    )


def evidence(
    support: tuple[CoverageInterval, ...],
    *,
    finalized: tuple[CoverageInterval, ...] | None = None,
    finalization_evidence: tuple[str, ...] = ("valid immutable source partitions",),
    source_ordering_policy: str = TRADES_CANONICAL_TOTAL_ORDER_V1,
    natural_partitions: tuple[NaturalPartitionIdentity, ...] | None = None,
    manifest_hashes: tuple[str, ...] = ("dataset-manifest-sha", "partition-manifest-sha"),
    content_hashes: tuple[str, ...] = ("canonical-content-sha",),
    source_result_identity: str | None = "source-result:sha256:abc",
) -> dict:
    return {
        "dataset_identity": IDENTITY,
        "record_schema_id": "trade-v1",
        "schema_version": 1,
        "schema_hash": "schema-sha",
        "source_ordering_policy": source_ordering_policy,
        "eligible_source_coverage": support,
        "finalized_source_intervals": support if finalized is None else finalized,
        "finalization_evidence": finalization_evidence,
        "natural_partitions": (
            natural_partitions
            if natural_partitions is not None
            else (NaturalPartitionIdentity(IDENTITY, "dt=2024-01-01/hour=00", 1),)
        ),
        "manifest_hashes": manifest_hashes,
        "content_hashes": content_hashes,
        "source_request_identity": "source-request:sha256:def",
        "source_result_identity": source_result_identity,
        "concrete_ordering_policy": "bybit-trade-v1-exchange-ts-trade-id-v1",
    }


def source_evidence(support: tuple[CoverageInterval, ...], **overrides):
    return HistoricalCandleSourceEvidence(**evidence(support, **overrides))


class HistoricalCandleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = CandleDefinitionV1.from_duration("5m")

    def assert_candle_record_shape(self, records):
        expected = {
            "bucket_start",
            "bucket_end",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
        }
        for record in records:
            self.assertEqual(expected, set(record.stable_dict()))

    def test_definition_identity_matches_frozen_golden_duration_forms(self):
        with (ROOT / "fixtures" / "candle-definition-v1" / "golden-5m.json").open(encoding="utf-8") as handle:
            golden = json.load(handle)
        for spelling in ("5m", "300s", "00:05:00", 300_000_000_000):
            with self.subTest(spelling=spelling):
                definition = CandleDefinitionV1.from_duration(spelling)
                self.assertEqual("300000000000", str(definition.duration_ns))
                self.assertEqual(golden["canonical_payload"], definition.canonical_payload)
                self.assertEqual(golden["expected_definition_identity"], definition.definition_identity)

    def test_query_intersection_selects_full_required_bucket_support(self):
        support = required_bucket_support(
            self.definition,
            "2024-01-01T10:02:00Z",
            "2024-01-01T10:07:00Z",
        )
        self.assertEqual(
            [{"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T10:10:00Z"}],
            [item.stable_dict() for item in support],
        )

    def test_kernel_preserves_source_order_for_open_close_and_uses_exact_decimals(self):
        records = aggregate_historical_candles(
            (
                trade("2024-01-01T10:00:00Z", "100.5000", "0.100", "20"),
                trade("2024-01-01T10:00:00Z", "99.25", "0.0200", "100"),
                trade("2024-01-01T10:04:59.999999999Z", "101.000", "1.000", "9"),
            ),
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
        )
        self.assert_candle_record_shape(records)
        self.assertEqual(
            {
                "bucket_start": "2024-01-01T10:00:00Z",
                "bucket_end": "2024-01-01T10:05:00Z",
                "open": "100.5",
                "high": "101",
                "low": "99.25",
                "close": "101",
                "volume": "1.12",
                "trade_count": "3",
            },
            records[0].stable_dict(),
        )

    def test_half_open_boundaries_and_empty_bucket_omission(self):
        records = aggregate_historical_candles(
            (
                trade("2024-01-01T10:00:00Z", "10.0", "1.0", "1"),
                trade("2024-01-01T10:10:00Z", "12.0", "2.0", "2"),
            ),
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:15:00Z",
        )
        self.assert_candle_record_shape(records)
        self.assertEqual(
            ["2024-01-01T10:00:00Z", "2024-01-01T10:10:00Z"],
            [record.bucket_start for record in records],
        )

    def test_result_builder_preserves_provenance_and_projected_coverage(self):
        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:10:00Z"),)
        supplied_evidence = source_evidence(support)
        result = build_historical_candle_result(
            (
                trade("2024-01-01T10:00:00Z", "10", "1", "1"),
                trade("2024-01-01T10:05:00Z", "12", "2", "2"),
            ),
            self.definition,
            "2024-01-01T10:02:00Z",
            "2024-01-01T10:07:00Z",
            source_evidence=supplied_evidence,
            implementation_identity=IMPLEMENTATION,
        )
        self.assert_candle_record_shape(result.records)
        self.assertEqual("CLOSED", result.state)
        self.assertEqual(self.definition.definition_identity, result.definition_identity)
        self.assertEqual(supplied_evidence, result.source_evidence)
        self.assertEqual(support, result.required_bucket_support)
        self.assertTrue(result.coverage.complete)
        self.assertEqual(
            [{"start": "2024-01-01T10:02:00Z", "end": "2024-01-01T10:07:00Z"}],
            [item.stable_dict() for item in result.coverage.covered_intervals],
        )
        self.assertTrue(result.result_identity.startswith("historical-candle-result-v1:sha256:"))

    def test_same_inputs_yield_identical_records_and_result_identity(self):
        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
        trades = (trade("2024-01-01T10:01:00Z", "10.00", "1.50", "1"),)
        supplied_evidence = source_evidence(support)
        left = build_historical_candle_result(
            trades,
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
            source_evidence=supplied_evidence,
            implementation_identity=IMPLEMENTATION,
        )
        right = build_historical_candle_result(
            trades,
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
            source_evidence=supplied_evidence,
            implementation_identity=IMPLEMENTATION,
        )
        self.assertEqual(left.records, right.records)
        self.assertEqual(left.result_identity, right.result_identity)

    def test_zero_length_query_has_empty_support_and_successful_empty_result(self):
        result = build_historical_candle_result(
            (),
            self.definition,
            "2024-01-01T10:02:00Z",
            "2024-01-01T10:02:00Z",
            source_evidence=source_evidence(()),
            implementation_identity=IMPLEMENTATION,
        )
        self.assertEqual((), result.records)
        self.assertEqual((), result.required_bucket_support)
        self.assertTrue(result.coverage.complete)
        self.assertIsNone(result.returned_record_bounds)

    def test_incomplete_support_or_finalization_fails_explicitly(self):
        with self.assertRaises(CandleCoverageError):
            build_historical_candle_result(
                (),
                self.definition,
                "2024-01-01T10:02:00Z",
                "2024-01-01T10:07:00Z",
                source_evidence=source_evidence(
                    (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
                ),
                implementation_identity=IMPLEMENTATION,
            )
        with self.assertRaises(CandleFinalizationError):
            build_historical_candle_result(
                (),
                self.definition,
                "2024-01-01T10:02:00Z",
                "2024-01-01T10:07:00Z",
                source_evidence=source_evidence(
                    (interval("2024-01-01T10:00:00Z", "2024-01-01T10:10:00Z"),),
                    finalized=(interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),),
                ),
                implementation_identity=IMPLEMENTATION,
            )

    def test_missing_ordering_or_reproducibility_evidence_fails_explicitly(self):
        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
        with self.assertRaises(CandleOrderingError):
            build_historical_candle_result(
                (),
                self.definition,
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                source_evidence=source_evidence(support, source_ordering_policy="file-row-order"),
                implementation_identity=IMPLEMENTATION,
            )
        with self.assertRaises(CandleProvenanceError):
            build_historical_candle_result(
                (),
                self.definition,
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                source_evidence=source_evidence(
                    support,
                    natural_partitions=(),
                    manifest_hashes=(),
                    content_hashes=(),
                    source_result_identity=None,
                ),
                implementation_identity=IMPLEMENTATION,
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
