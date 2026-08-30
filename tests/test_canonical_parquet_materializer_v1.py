#!/usr/bin/env python3
"""Behavioral tests for Canonical Parquet Materializer v1."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import (  # noqa: E402
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    InvalidRequest,
    TradeRecord,
    canonical_content_hash_v1,
)
from quant_platform.data.materializer import (  # noqa: E402
    materialize_trade_v1,
    physical_artifact_sha256,
)
from quant_platform.data.parquet import _COLUMNS, read_trade_v1  # noqa: E402
from quant_platform.ordering import OrderingProvider  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1EligibilityError,
    materialize_bybit_trade_v1,
)

IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
START = Instant.parse("2024-01-15T00:00:00Z")
END = Instant.parse("2024-01-16T00:00:00Z")


def trade(
    exchange_ts: str,
    trade_id: str | None,
    *,
    receive_ts: str | None = None,
    price: str = "100.00",
    size: str = "0.5000",
    aggressor_side: str = "buy",
    sequence: str | None = None,
    venue: str = "bybit",
    instrument: str = "BTCUSDT",
) -> TradeRecord:
    return TradeRecord(
        venue=venue,
        instrument=instrument,
        exchange_ts=Instant.parse(exchange_ts),
        price=price,
        size=size,
        aggressor_side=aggressor_side,
        receive_ts=None if receive_ts is None else Instant.parse(receive_ts),
        trade_id=trade_id,
        sequence=sequence,
    )


class CanonicalParquetMaterializerV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_round_trip_is_canonical_and_physically_ordered(self):
        records = [
            trade(
                "2024-01-15T00:00:00.123456789Z",
                "2",
                receive_ts="2024-01-15T00:00:00.223456789Z",
                price="100.00",
                size="0.5000",
                sequence="10",
            ),
            trade(
                "2024-01-15T00:00:00.123456789Z",
                "10",
                receive_ts="2024-01-15T00:00:00.223456788Z",
                price="99.90",
                size="1.25",
                aggressor_side="sell",
                sequence="9",
            ),
            trade("2024-01-15T00:00:00.023456789Z", "9"),
        ]
        expected = [records[2], records[1], records[0]]
        path = self.root / "dt=2024-01-15" / "part-000.parquet"

        artifact = materialize_bybit_trade_v1(
            path,
            records,
            dataset_identity=IDENTITY,
            row_group_size=1,
        )

        self.assertEqual(read_trade_v1(path, START, END), expected)
        self.assertEqual(artifact.row_count, len(expected))
        self.assertEqual(artifact.first_exchange_ts, expected[0].exchange_ts)
        self.assertEqual(artifact.last_exchange_ts, expected[-1].exchange_ts)
        self.assertEqual(
            artifact.canonical_content_hash_v1,
            canonical_content_hash_v1(expected),
        )
        self.assertEqual(artifact.sha256, physical_artifact_sha256(path))
        self.assertEqual(artifact.content_sha256, artifact.sha256)
        self.assertEqual(artifact.physical_artifact_hash, artifact.sha256)
        self.assertEqual(artifact.file_size_bytes, path.stat().st_size)

        parquet = pq.ParquetFile(path)
        self.assertEqual(parquet.metadata.num_row_groups, 3)
        row_group_ids = [
            parquet.read_row_group(index, columns=["trade_id"])["trade_id"][0].as_py()
            for index in range(parquet.metadata.num_row_groups)
        ]
        self.assertEqual(row_group_ids, ["9", "10", "2"])

    def test_schema_is_exact_and_preserves_nanosecond_exchange_time(self):
        path = self.root / "schema.parquet"
        materialize_bybit_trade_v1(
            path,
            [trade("2024-01-15T00:00:00.123456789Z", "1")],
            dataset_identity=IDENTITY,
        )
        schema = pq.read_schema(path)
        self.assertEqual(schema.names, list(_COLUMNS))
        exchange_type = schema.field("exchange_ts").type
        self.assertTrue(pa.types.is_timestamp(exchange_type))
        self.assertEqual(exchange_type.unit, "ns")
        self.assertEqual(exchange_type.tz, "UTC")
        self.assertTrue(pa.types.is_string(schema.field("receive_ts").type))
        for field_name in ("venue", "instrument", "exchange_ts", "price", "size", "aggressor_side"):
            self.assertFalse(schema.field(field_name).nullable)
        for field_name in ("receive_ts", "trade_id", "sequence"):
            self.assertTrue(schema.field(field_name).nullable)

    def test_optional_nullness_and_decimal_spelling_survive_round_trip(self):
        original = trade(
            "2024-01-15T00:00:00.123456789Z",
            "1",
            price="100.00",
            size="0.5000",
        )
        path = self.root / "nulls.parquet"
        materialize_bybit_trade_v1(path, [original], dataset_identity=IDENTITY)
        returned = read_trade_v1(path, START, END)
        self.assertEqual(returned, [original])
        self.assertIsNone(returned[0].receive_ts)
        self.assertIsNone(returned[0].sequence)
        self.assertEqual(returned[0].price, "100.00")
        self.assertEqual(returned[0].size, "0.5000")

    def test_physical_layout_variation_changes_physical_hash_not_semantic_hash(self):
        records = [
            trade("2024-01-15T00:00:00.1Z", "1"),
            trade("2024-01-15T00:00:00.2Z", "2", aggressor_side="sell"),
            trade("2024-01-15T00:00:00.3Z", "3"),
        ]
        plain = materialize_bybit_trade_v1(
            self.root / "plain.parquet",
            records,
            dataset_identity=IDENTITY,
            compression=None,
            row_group_size=1,
        )
        compressed = materialize_bybit_trade_v1(
            self.root / "compressed.parquet",
            records,
            dataset_identity=IDENTITY,
            compression="gzip",
            row_group_size=3,
        )
        self.assertEqual(
            plain.canonical_content_hash_v1,
            compressed.canonical_content_hash_v1,
        )
        self.assertNotEqual(plain.sha256, compressed.sha256)
        self.assertNotEqual(
            (self.root / "plain.parquet").read_bytes(),
            (self.root / "compressed.parquet").read_bytes(),
        )

    def test_physical_hash_is_exact_file_sha256(self):
        path = self.root / "hash.parquet"
        artifact = materialize_bybit_trade_v1(
            path,
            [trade("2024-01-15T00:00:00Z", "1")],
            dataset_identity=IDENTITY,
        )
        self.assertEqual(artifact.sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_zero_row_partition_materializes_with_null_observed_bounds(self):
        path = self.root / "empty.parquet"
        artifact = materialize_bybit_trade_v1(path, [], dataset_identity=IDENTITY)
        self.assertEqual(artifact.row_count, 0)
        self.assertIsNone(artifact.first_exchange_ts)
        self.assertIsNone(artifact.last_exchange_ts)
        self.assertEqual(read_trade_v1(path, START, END), [])
        self.assertEqual(
            artifact.canonical_content_hash_v1,
            "canonical-content-hash-v1:sha256:20b78ae91c5f15ca42130f801467190a7bd21634dab69788fc2eda2bd04f2105",
        )

    def test_null_trade_id_is_schema_valid_but_bybit_profile_ineligible(self):
        path = self.root / "null-trade-id.parquet"
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "trade_id must be non-null"):
            materialize_bybit_trade_v1(
                path,
                [trade("2024-01-15T00:00:00Z", None)],
                dataset_identity=IDENTITY,
            )
        self.assertFalse(path.exists())

    def test_duplicate_bybit_ordering_key_is_profile_ineligible(self):
        path = self.root / "duplicate.parquet"
        duplicate = trade("2024-01-15T00:00:00Z", "1")
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "duplicate"):
            materialize_bybit_trade_v1(
                path,
                [duplicate, duplicate],
                dataset_identity=IDENTITY,
            )
        self.assertFalse(path.exists())

    def test_record_dataset_identity_mismatch_is_rejected_before_write(self):
        path = self.root / "wrong-identity.parquet"
        with self.assertRaisesRegex(DataIntegrityError, "does not match"):
            materialize_bybit_trade_v1(
                path,
                [trade("2024-01-15T00:00:00Z", "1", instrument="ETHUSDT")],
                dataset_identity=IDENTITY,
            )
        self.assertFalse(path.exists())

    def test_noncanonical_decimal_is_rejected_before_write(self):
        path = self.root / "bad-decimal.parquet"
        with self.assertRaisesRegex(DataIntegrityError, "canonical positive decimal string"):
            materialize_bybit_trade_v1(
                path,
                [trade("2024-01-15T00:00:00Z", "1", price="1e2")],
                dataset_identity=IDENTITY,
            )
        self.assertFalse(path.exists())

    def test_generic_materializer_requires_provider_claiming_total_order(self):
        provider = OrderingProvider(
            identity="not-a-total-order-v1",
            satisfies_requirements=frozenset(),
            key=lambda record: (record.exchange_ts,),
            applies_to=lambda identity: identity == IDENTITY,
        )
        with self.assertRaisesRegex(InvalidRequest, "canonical total order"):
            materialize_trade_v1(
                self.root / "bad-provider.parquet",
                [trade("2024-01-15T00:00:00Z", "1")],
                dataset_identity=IDENTITY,
                ordering_provider=provider,
            )

    def test_generic_materializer_rejects_duplicate_provider_keys(self):
        provider = OrderingProvider(
            identity="constant-key-total-order-v1",
            satisfies_requirements=frozenset({"trades@1-canonical-total-order-v1"}),
            key=lambda _record: (1,),
            applies_to=lambda identity: identity == IDENTITY,
        )
        with self.assertRaisesRegex(DataIntegrityError, "duplicate ordering key"):
            materialize_trade_v1(
                self.root / "duplicate-provider.parquet",
                [
                    trade("2024-01-15T00:00:00Z", "1"),
                    trade("2024-01-15T00:00:01Z", "2"),
                ],
                dataset_identity=IDENTITY,
                ordering_provider=provider,
            )


if __name__ == "__main__":
    unittest.main()
