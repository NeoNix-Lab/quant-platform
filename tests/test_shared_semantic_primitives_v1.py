#!/usr/bin/env python3
"""Executable tests for the first conformity runtime slice.

These tests cover only the shared semantic primitives authorized by the
Conformity Implementation Gate plan: RecordTimeBounds,
CanonicalContentHashV1, and ordering compatibility.  They do not implement
or exercise a Parquet writer, bounded scan, certification, or a catalog
bridge.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.gateway import DataGateway  # noqa: E402
from quant_platform import ordering as ordering_module  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CanonicalContentHashV1,
    CatalogDataset,
    CatalogPartition,
    CoverageInterval,
    DataIntegrityError,
    DataRequest,
    DatasetIdentity,
    InvalidRequest,
    Instant,
    NaturalPartitionIdentity,
    RecordTimeBounds,
    TradeRecord,
    UnsupportedDatasetKind,
    UnsupportedSchema,
    canonical_content_hash_v1,
)
from quant_platform.ordering import (  # noqa: E402
    DuplicateOrderingProviderError,
    OrderingProvider,
    TRADES_CANONICAL_TOTAL_ORDER_V1,
    ordering_policy_satisfies,
    provider_for,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    bybit_trade_v1_ordering_key,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET = CatalogDataset(IDENTITY, "dataset-id", "canonical/trades/bybit/BTCUSDT/trade-v1", "m" * 64, 1, "s" * 64)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def trade(
    exchange_ts: str,
    trade_id: str | None,
    *,
    receive_ts: str | None = None,
    price: str = "100.00",
    size: str = "0.5",
    aggressor_side: str = "buy",
    sequence: str | None = None,
) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(exchange_ts),
        price=price,
        size=size,
        aggressor_side=aggressor_side,
        receive_ts=None if receive_ts is None else instant(receive_ts),
        trade_id=trade_id,
        sequence=sequence,
    )


class FakeCatalog:
    def __init__(self, partitions: list[CatalogPartition], dataset: CatalogDataset = DATASET):
        self.partitions = partitions
        self.dataset = dataset

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        return self.dataset

    def select_partitions(self, dataset, start, end, states):
        return [
            partition
            for partition in self.partitions
            if partition.state in states
            and partition.coverage is not None
            and partition.ts_end > start
            and partition.ts_start < end
        ]


def covered_partition(
    key: str = "dt=2024-01-01",
    *,
    identity: DatasetIdentity = IDENTITY,
    dataset: CatalogDataset = DATASET,
) -> CatalogPartition:
    return CatalogPartition(
        NaturalPartitionIdentity(identity, key, 1),
        f"catalog-{key}",
        "storage-root",
        "C:/storage",
        dataset.rel_root,
        f"{key}/part-000.parquet",
        instant("2024-01-01T00:00:00Z"),
        instant("2024-01-01T01:00:00Z"),
        1,
        "c" * 64,
        "d" * 64,
        "valid",
        "fixture",
        "test",
    )


def gateway(records: list[TradeRecord]) -> DataGateway:
    return DataGateway(
        FakeCatalog([covered_partition()]),
        reader=lambda _path, _start, _end: list(records),
        path_resolver=lambda _root, _dataset_root, _rel_path: "ignored.parquet",
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )


def request() -> DataRequest:
    return DataRequest(
        IDENTITY,
        "2024-01-01T00:00:00Z",
        "2024-01-01T01:00:00Z",
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


class RecordTimeBoundsTests(unittest.TestCase):
    def test_first_before_last_is_accepted(self):
        bounds = RecordTimeBounds(instant("2024-01-01T00:00:00Z"), instant("2024-01-01T00:00:01Z"))
        self.assertEqual(bounds.stable_dict(), {"first": "2024-01-01T00:00:00Z", "last": "2024-01-01T00:00:01Z"})

    def test_equal_first_and_last_is_accepted(self):
        timestamp = instant("2024-01-01T00:00:00Z")
        self.assertEqual(RecordTimeBounds(timestamp, timestamp).first, timestamp)

    def test_first_after_last_is_rejected(self):
        with self.assertRaisesRegex(InvalidRequest, "first must not be after last"):
            RecordTimeBounds(instant("2024-01-01T00:00:01Z"), instant("2024-01-01T00:00:00Z"))

    def test_gateway_empty_result_has_no_record_bounds(self):
        result = gateway([]).read(request())
        self.assertIsNone(result.metadata.returned_record_bounds)

    def test_gateway_one_record_has_equal_record_bounds(self):
        record = trade("2024-01-01T00:00:00.123456789Z", "1")
        bounds = gateway([record]).read(request()).metadata.returned_record_bounds
        self.assertIsInstance(bounds, RecordTimeBounds)
        self.assertEqual(bounds.first, record.exchange_ts)
        self.assertEqual(bounds.last, record.exchange_ts)

    def test_gateway_multi_record_bounds_are_first_and_last(self):
        records = [
            trade("2024-01-01T00:00:00.100Z", "1"),
            trade("2024-01-01T00:00:00.200Z", "2"),
        ]
        bounds = gateway(records).read(request()).metadata.returned_record_bounds
        self.assertIsInstance(bounds, RecordTimeBounds)
        self.assertEqual(bounds.first, records[0].exchange_ts)
        self.assertEqual(bounds.last, records[1].exchange_ts)

    def test_returned_bounds_are_not_coverage_interval(self):
        bounds = gateway([trade("2024-01-01T00:00:00Z", "1")]).read(request()).metadata.returned_record_bounds
        self.assertNotIsInstance(bounds, CoverageInterval)

    def test_result_identity_is_deterministic_with_record_bounds(self):
        records = [trade("2024-01-01T00:00:00Z", "1")]
        first = gateway(records).read(request())
        second = gateway(records).read(request())
        self.assertEqual(first.metadata.result_identity, second.metadata.result_identity)
        self.assertEqual(first.metadata.returned_record_bounds.stable_dict(), {"first": "2024-01-01T00:00:00Z", "last": "2024-01-01T00:00:00Z"})


class CanonicalContentHashV1Tests(unittest.TestCase):
    def test_zero_records_pinned_vector(self):
        self.assertEqual(
            canonical_content_hash_v1([]),
            "canonical-content-hash-v1:sha256:20b78ae91c5f15ca42130f801467190a7bd21634dab69788fc2eda2bd04f2105",
        )

    def test_one_trade_with_null_optional_fields_pinned_vector(self):
        self.assertEqual(
            CanonicalContentHashV1.compute([trade("2024-01-01T00:00:00.1Z", "10")]),
            "canonical-content-hash-v1:sha256:bd82c2ac5ce1d71fc7d2209a588dc6f18f86f3951d4bc2bea40d46113416b615",
        )

    def test_one_trade_with_all_optional_fields_pinned_vector(self):
        self.assertEqual(
            canonical_content_hash_v1([
                trade(
                    "2024-01-01T00:00:00.2Z",
                    "2",
                    receive_ts="2024-01-01T00:00:00.3Z",
                    price="100.10",
                    size="0.25",
                    aggressor_side="sell",
                    sequence="42",
                )
            ]),
            "canonical-content-hash-v1:sha256:9fd7ce70a30916a0e01066bc6cb9b542e1ecb2e7d6e3b8e964992f12093510e8",
        )

    def test_two_ordered_trades_pinned_vector(self):
        records = [trade("2024-01-01T00:00:00.1Z", "10"), trade("2024-01-01T00:00:00.2Z", "2", receive_ts="2024-01-01T00:00:00.3Z", price="100.10", size="0.25", aggressor_side="sell", sequence="42")]
        self.assertEqual(
            canonical_content_hash_v1(records),
            "canonical-content-hash-v1:sha256:cf6030a42516de7c0b9188895cbd7a72e405651feb4431168aecb03f118f8ed0",
        )

    def test_reversed_records_change_the_order_sensitive_hash(self):
        records = [
            trade("2024-01-01T00:00:00.1Z", "10"),
            trade(
                "2024-01-01T00:00:00.2Z",
                "2",
                receive_ts="2024-01-01T00:00:00.3Z",
                price="100.10",
                size="0.25",
                aggressor_side="sell",
                sequence="42",
            ),
        ]
        self.assertEqual(
            canonical_content_hash_v1(list(reversed(records))),
            "canonical-content-hash-v1:sha256:167734b762b7fe4b7cc79740179d0522f8865970809726bcb1caf017dfda1148",
        )
        self.assertNotEqual(canonical_content_hash_v1(records), canonical_content_hash_v1(list(reversed(records))))

    def test_one_changed_field_changes_the_hash(self):
        original = trade("2024-01-01T00:00:00.1Z", "10")
        changed = trade("2024-01-01T00:00:00.1Z", "10", price="100.01")
        self.assertNotEqual(canonical_content_hash_v1([original]), canonical_content_hash_v1([changed]))

    def test_equivalent_instant_spellings_hash_identically(self):
        short = trade("2024-01-01T00:00:00.1Z", "10")
        padded = trade("2024-01-01T00:00:00.100000000Z", "10")
        self.assertEqual(canonical_content_hash_v1([short]), canonical_content_hash_v1([padded]))

    def test_irrelevant_physical_metadata_is_not_an_input(self):
        record = trade("2024-01-01T00:00:00.1Z", "10")
        self.assertEqual(canonical_content_hash_v1([record]), canonical_content_hash_v1(iter([record])))


class OrderingCompatibilityTests(unittest.TestCase):
    def test_core_modules_do_not_own_bybit_key_implementation(self):
        shared_root = ROOT / "src" / "quant_platform"
        paths = [shared_root / "ordering.py", *sorted((shared_root / "data").rglob("*.py"))]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or "", *(alias.name for alias in node.names)]
                else:
                    continue
                self.assertFalse(
                    any("source_adapters" in name for name in imported),
                    f"shared module imports a source adapter: {path}",
                )

    def test_compatibility_mechanism_is_generic_and_provider_supplied(self):
        provider = BYBIT_ORDERING_PROVIDER
        self.assertTrue(provider.satisfies(TRADES_CANONICAL_TOTAL_ORDER_V1))
        self.assertTrue(ordering_module.ordering_policy_satisfies(
            provider.identity,
            TRADES_CANONICAL_TOTAL_ORDER_V1,
            (provider,),
        ))

    def test_policy_identities_are_exact(self):
        self.assertEqual(BYBIT_TRADE_V1_ORDERING_POLICY, "bybit-trade-v1-exchange-ts-trade-id-v1")
        self.assertEqual(TRADES_CANONICAL_TOTAL_ORDER_V1, "trades@1-canonical-total-order-v1")

    def test_bybit_policy_satisfies_abstract_trade_order(self):
        self.assertTrue(ordering_policy_satisfies(
            BYBIT_TRADE_V1_ORDERING_POLICY,
            TRADES_CANONICAL_TOTAL_ORDER_V1,
            [BYBIT_ORDERING_PROVIDER],
        ))

    def test_unsupported_concrete_policy_does_not_satisfy_requirement(self):
        self.assertFalse(ordering_policy_satisfies(
            "random-policy-v1",
            TRADES_CANONICAL_TOTAL_ORDER_V1,
            [BYBIT_ORDERING_PROVIDER],
        ))
        self.assertFalse(ordering_policy_satisfies(
            BYBIT_TRADE_V1_ORDERING_POLICY,
            "unknown-abstract-v1",
            [BYBIT_ORDERING_PROVIDER],
        ))

    def test_provider_resolution_returns_none_for_zero_matches(self):
        self.assertIsNone(provider_for("missing-policy-v1", (BYBIT_ORDERING_PROVIDER,)))

    def test_provider_resolution_returns_the_unique_match(self):
        self.assertIs(provider_for(BYBIT_TRADE_V1_ORDERING_POLICY, (BYBIT_ORDERING_PROVIDER,)), BYBIT_ORDERING_PROVIDER)

    def test_provider_resolution_rejects_duplicate_identities_regardless_of_tuple_order(self):
        first = OrderingProvider(
            identity="duplicate-policy-v1",
            satisfies_requirements=frozenset(),
            key=lambda _record: (1,),
            applies_to=lambda _identity: True,
        )
        second = OrderingProvider(
            identity="duplicate-policy-v1",
            satisfies_requirements=frozenset(),
            key=lambda _record: (2,),
            applies_to=lambda _identity: True,
        )
        for supplied in ((first, second), (second, first)):
            with self.subTest(supplied=supplied), self.assertRaises(DuplicateOrderingProviderError):
                provider_for("duplicate-policy-v1", supplied)

    def test_missing_ordering_policy_is_rejected_explicitly(self):
        for missing in (None, ""):
            with self.subTest(missing=missing), self.assertRaisesRegex(InvalidRequest, "ordering_policy"):
                DataRequest(
                    IDENTITY,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T01:00:00Z",
                    ordering_policy=missing,
                )

    def test_gateway_does_not_discover_a_provider_from_the_reader(self):
        reader = lambda _path, _start, _end: []
        reader.ordering_provider = BYBIT_ORDERING_PROVIDER
        gateway_without_injection = DataGateway(
            FakeCatalog([covered_partition()]),
            reader=reader,
            path_resolver=lambda _root, _dataset_root, _rel_path: "ignored.parquet",
        )
        with self.assertRaises(UnsupportedSchema):
            gateway_without_injection.read(request())

    def test_provider_applicability_is_checked_by_generic_gateway(self):
        other_identity = DatasetIdentity("canonical", "trades", "kraken", "BTCUSDT", "trade-v1")
        other_dataset = CatalogDataset(
            other_identity,
            "kraken-dataset",
            "canonical/trades/kraken/BTCUSDT/trade-v1",
            "m" * 64,
            1,
            "s" * 64,
        )
        gateway_for_other_venue = DataGateway(
            FakeCatalog([covered_partition(identity=other_identity, dataset=other_dataset)], dataset=other_dataset),
            reader=lambda _path, _start, _end: [],
            path_resolver=lambda _root, _dataset_root, _rel_path: "ignored.parquet",
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )
        with self.assertRaises(UnsupportedDatasetKind):
            gateway_for_other_venue.read(DataRequest(
                other_identity,
                "2024-01-01T00:00:00Z",
                "2024-01-01T01:00:00Z",
                ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            ))

    def test_shared_gateway_accepts_a_second_venue_provider(self):
        identity = DatasetIdentity("canonical", "trades", "kraken", "BTCUSDT", "trade-v1")
        dataset = CatalogDataset(
            identity,
            "kraken-dataset",
            "canonical/trades/kraken/BTCUSDT/trade-v1",
            "m" * 64,
            1,
            "s" * 64,
        )

        def testvenue_key(record: TradeRecord) -> tuple[Instant, str]:
            return record.exchange_ts, record.sequence or ""

        testvenue_provider = OrderingProvider(
            identity="kraken-trade-v1-exchange-ts-sequence-v1",
            satisfies_requirements=frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
            key=testvenue_key,
            applies_to=lambda candidate: (
                candidate.venue == "kraken"
                and candidate.dataset_kind == "trades"
                and candidate.record_schema_id == "trade-v1"
            ),
        )
        records = [
            TradeRecord("kraken", "BTCUSDT", instant("2024-01-01T00:00:00Z"), "1", "1", "buy", sequence="1"),
            TradeRecord("kraken", "BTCUSDT", instant("2024-01-01T00:00:00Z"), "1", "1", "buy", sequence="2"),
        ]
        gateway_for_other_venue = DataGateway(
            FakeCatalog([covered_partition(identity=identity, dataset=dataset)], dataset=dataset),
            reader=lambda _path, _start, _end: list(records),
            path_resolver=lambda _root, _dataset_root, _rel_path: "ignored.parquet",
            ordering_providers=(testvenue_provider,),
        )
        result = gateway_for_other_venue.read(DataRequest(
            identity,
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            ordering_policy=testvenue_provider.identity,
        ))
        self.assertEqual([record.sequence for record in result], ["1", "2"])
    def test_key_uses_exchange_time_before_trade_id(self):
        earlier = trade("2024-01-01T00:00:00.100Z", "z")
        later = trade("2024-01-01T00:00:00.200Z", "a")
        self.assertEqual(sorted([later, earlier], key=bybit_trade_v1_ordering_key), [earlier, later])

    def test_trade_id_is_opaque_lexicographic_string(self):
        records = [trade("2024-01-01T00:00:00Z", identifier) for identifier in ("9", "100", "20")]
        self.assertEqual([record.trade_id for record in sorted(records, key=bybit_trade_v1_ordering_key)], ["100", "20", "9"])

    def test_missing_and_empty_trade_id_are_rejected(self):
        for identifier in (None, ""):
            with self.subTest(identifier=identifier), self.assertRaises(DataIntegrityError):
                bybit_trade_v1_ordering_key(trade("2024-01-01T00:00:00Z", identifier))

    def test_sequence_and_receive_time_do_not_affect_key(self):
        base = trade("2024-01-01T00:00:00Z", "1")
        variant = trade("2024-01-01T00:00:00Z", "1", receive_ts="2024-01-01T00:00:01Z", sequence="999")
        self.assertEqual(bybit_trade_v1_ordering_key(base), bybit_trade_v1_ordering_key(variant))


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
