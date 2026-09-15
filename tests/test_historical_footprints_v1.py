#!/usr/bin/env python3
"""D06 historical FINAL footprint runtime proof for FootprintDefinition v1."""

from __future__ import annotations

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
from quant_platform.features.imbalance import PriceLevelInput  # noqa: E402
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    FootprintAggressorSideError,
    FootprintBucket,
    FootprintCoverageError,
    FootprintDefinitionV1,
    FootprintFinalizationError,
    FootprintGridError,
    FootprintInputError,
    FootprintLevel,
    FootprintOrderingError,
    FootprintProvenanceError,
    HistoricalFootprintSourceEvidence,
    aggregate_historical_footprints,
    build_historical_footprint_result,
    required_footprint_bucket_support,
    validate_footprint_bucket,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
IMPLEMENTATION = "quant_platform.representation.footprints:d06-v1"


def instant(value: str) -> Instant:
    return Instant.parse(value)


def interval(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(instant(start), instant(end))


def trade(
    ts: str,
    price: str,
    size: str,
    trade_id: str,
    *,
    side: str = "buy",
    venue: str = "bybit",
    instrument: str = "BTCUSDT",
) -> TradeRecord:
    return TradeRecord(
        venue=venue,
        instrument=instrument,
        exchange_ts=instant(ts),
        price=price,
        size=size,
        aggressor_side=side,
        trade_id=trade_id,
    )


def evidence(
    support: tuple[CoverageInterval, ...],
    *,
    identity: DatasetIdentity = IDENTITY,
    finalized: tuple[CoverageInterval, ...] | None = None,
    finalization_evidence: tuple[str, ...] = ("valid immutable source partitions",),
    source_ordering_policy: str = TRADES_CANONICAL_TOTAL_ORDER_V1,
    natural_partitions: tuple[NaturalPartitionIdentity, ...] | None = None,
    manifest_hashes: tuple[str, ...] = ("dataset-manifest-sha", "partition-manifest-sha"),
    content_hashes: tuple[str, ...] = ("canonical-content-sha",),
    source_result_identity: str | None = "source-result:sha256:abc",
    record_schema_id: str = "trade-v1",
    source_record_schema: str = "trade-v1",
    source_representation: str = "trades@1",
) -> dict:
    return {
        "dataset_identity": identity,
        "record_schema_id": record_schema_id,
        "schema_version": 1,
        "schema_hash": "schema-sha",
        "source_ordering_policy": source_ordering_policy,
        "eligible_source_coverage": support,
        "finalized_source_intervals": support if finalized is None else finalized,
        "finalization_evidence": finalization_evidence,
        "natural_partitions": (
            natural_partitions
            if natural_partitions is not None
            else (NaturalPartitionIdentity(identity, "dt=2024-01-01/hour=10", 1),)
        ),
        "manifest_hashes": manifest_hashes,
        "content_hashes": content_hashes,
        "source_request_identity": "source-request:sha256:def",
        "source_result_identity": source_result_identity,
        "concrete_ordering_policy": "bybit-trade-v1-exchange-ts-trade-id-v1",
        "source_record_schema": source_record_schema,
        "source_representation": source_representation,
    }


def source_evidence(support: tuple[CoverageInterval, ...], **overrides):
    return HistoricalFootprintSourceEvidence(**evidence(support, **overrides))


class HistoricalFootprintRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = FootprintDefinitionV1.from_duration_and_tick("5m", "0.10")

    def test_definition_identity_is_deterministic_and_excludes_runtime_binding(self):
        equivalent = FootprintDefinitionV1.from_duration_and_tick("300s", "0.1")
        duration_changed = FootprintDefinitionV1.from_duration_and_tick("1m", "0.1")
        tick_changed = FootprintDefinitionV1.from_duration_and_tick("5m", "0.01")

        self.assertEqual(self.definition.canonical_payload, equivalent.canonical_payload)
        self.assertEqual(self.definition.definition_identity, equivalent.definition_identity)
        self.assertNotEqual(self.definition.definition_identity, duration_changed.definition_identity)
        self.assertNotEqual(self.definition.definition_identity, tick_changed.definition_identity)

        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
        bybit = source_evidence(support)
        kraken_identity = DatasetIdentity("canonical", "trades", "kraken", "BTCUSDT", "trade-v1")
        kraken = source_evidence(support, identity=kraken_identity)
        self.assertNotEqual(bybit.stable_dict(), kraken.stable_dict())
        self.assertEqual(self.definition.definition_identity, equivalent.definition_identity)

    def test_definition_refuses_non_positive_or_non_exact_tick_size_and_duration(self):
        for tick_size in ("0", "-0.1", "1e-2", 1):
            with self.subTest(tick_size=tick_size):
                with self.assertRaises(FootprintInputError):
                    FootprintDefinitionV1.from_duration_and_tick("5m", tick_size)  # type: ignore[arg-type]
        with self.assertRaises(FootprintInputError):
            FootprintDefinitionV1.from_duration_and_tick("0s", "0.1")

    def test_query_intersection_selects_full_utc_epoch_aligned_bucket_support(self):
        support = required_footprint_bucket_support(
            self.definition,
            "2024-01-01T10:02:00Z",
            "2024-01-01T10:07:00Z",
        )
        self.assertEqual(
            [{"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T10:10:00Z"}],
            [item.stable_dict() for item in support],
        )

    def test_exact_tick_grid_accepts_on_grid_and_refuses_float_tolerance_lookalike(self):
        buckets = aggregate_historical_footprints(
            (trade("2024-01-01T10:00:00Z", "0.30", "1", "1"),),
            FootprintDefinitionV1.from_duration_and_tick("5m", "0.1"),
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
        )
        self.assertEqual(3, buckets[0].levels[0].level_index)

        with self.assertRaises(FootprintGridError):
            aggregate_historical_footprints(
                (trade("2024-01-01T10:00:00Z", "0.30000000000000004", "1", "2"),),
                FootprintDefinitionV1.from_duration_and_tick("5m", "0.1"),
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
            )

    def test_same_level_aggregation_buy_only_sell_only_sparse_and_ordered(self):
        buckets = aggregate_historical_footprints(
            (
                trade("2024-01-01T10:00:00Z", "100.00", "1.25", "1", side="buy"),
                trade("2024-01-01T10:00:01Z", "100.00", "0.75", "2", side="buy"),
                trade("2024-01-01T10:00:02Z", "100.20", "3.5", "3", side="sell"),
            ),
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
        )

        self.assertEqual(1, len(buckets))
        self.assertEqual(
            [
                {"level_index": 1000, "price": "100", "buy_volume": "2", "sell_volume": "0"},
                {"level_index": 1002, "price": "100.2", "buy_volume": "0", "sell_volume": "3.5"},
            ],
            [level.stable_dict() for level in buckets[0].levels],
        )
        self.assertEqual([1000, 1002], [level.level_index for level in buckets[0].levels])

        e05_inputs = tuple(
            PriceLevelInput("2024-01-01T10:00:00Z", level.level_index, float(level.buy_volume), float(level.sell_volume))
            for level in buckets[0].levels
        )
        self.assertEqual([1000, 1002], [level.level_index for level in e05_inputs])

    def test_unknown_missing_and_unsupported_aggressor_side_fail_closed(self):
        for side in ("unknown", "", "maker"):
            with self.subTest(side=side):
                with self.assertRaises(FootprintAggressorSideError):
                    aggregate_historical_footprints(
                        (trade("2024-01-01T10:00:00Z", "100.00", "1", "1", side=side),),
                        self.definition,
                        "2024-01-01T10:00:00Z",
                        "2024-01-01T10:05:00Z",
                    )

        with self.assertRaises(FootprintAggressorSideError):
            aggregate_historical_footprints(
                (trade("2024-01-01T10:00:00Z", "100.00", "1", "1", side=None),),  # type: ignore[arg-type]
                self.definition,
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
            )

    def test_malformed_or_negative_source_volume_refuses(self):
        for size in ("0", "-1", "1e-3"):
            with self.subTest(size=size):
                with self.assertRaises(FootprintInputError):
                    aggregate_historical_footprints(
                        (trade("2024-01-01T10:00:00Z", "100.00", size, "1"),),
                        self.definition,
                        "2024-01-01T10:00:00Z",
                        "2024-01-01T10:05:00Z",
                    )

    def test_complete_zero_trade_support_yields_empty_final_bucket_not_missing_support(self):
        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
        result = build_historical_footprint_result(
            (),
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:05:00Z",
            source_evidence=source_evidence(support),
            implementation_identity=IMPLEMENTATION,
        )
        self.assertEqual(1, result.bucket_count)
        self.assertEqual(0, result.row_count)
        self.assertEqual((), result.buckets[0].levels)
        self.assertTrue(result.coverage.complete)
        self.assertEqual(support, result.required_bucket_support)
        self.assertIsNone(result.returned_record_bounds)

    def test_insufficient_support_or_finalization_refuses_final_claim(self):
        with self.assertRaises(FootprintCoverageError):
            build_historical_footprint_result(
                (),
                self.definition,
                "2024-01-01T10:02:00Z",
                "2024-01-01T10:07:00Z",
                source_evidence=source_evidence(
                    (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
                ),
                implementation_identity=IMPLEMENTATION,
            )
        with self.assertRaises(FootprintFinalizationError):
            build_historical_footprint_result(
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

    def test_conflicting_source_identity_or_provenance_refuses(self):
        support = (interval("2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"),)
        with self.assertRaises(FootprintProvenanceError):
            build_historical_footprint_result(
                (trade("2024-01-01T10:00:00Z", "100.00", "1", "1", venue="kraken"),),
                self.definition,
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                source_evidence=source_evidence(support),
                implementation_identity=IMPLEMENTATION,
            )
        with self.assertRaises(FootprintOrderingError):
            build_historical_footprint_result(
                (),
                self.definition,
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                source_evidence=source_evidence(support, source_ordering_policy="file-row-order"),
                implementation_identity=IMPLEMENTATION,
            )
        with self.assertRaises(FootprintProvenanceError):
            build_historical_footprint_result(
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

    def test_duplicate_supplied_levels_and_inconsistent_level_price_refuse(self):
        with self.assertRaises(FootprintInputError):
            FootprintBucket(
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                (
                    FootprintLevel(1000, "100", "1", "0"),
                    FootprintLevel(1000, "100", "0", "1"),
                ),
            )
        with self.assertRaises(FootprintInputError):
            FootprintBucket(
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                (
                    FootprintLevel(1001, "100.1", "1", "0"),
                    FootprintLevel(1000, "100", "0", "1"),
                ),
            )
        with self.assertRaises(FootprintGridError):
            validate_footprint_bucket(
                self.definition,
                FootprintBucket(
                    "2024-01-01T10:00:00Z",
                    "2024-01-01T10:05:00Z",
                    (FootprintLevel(1000, "100.1", "1", "0"),),
                ),
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
