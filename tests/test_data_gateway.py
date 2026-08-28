#!/usr/bin/env python3
"""Deterministic semantic tests for the DataGateway v1 implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.catalog import Catalog  # noqa: E402
from quant_platform.data.gateway import DataGateway  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    CatalogConflict,
    DataRequest,
    DatasetIdentity,
    Instant,
    LifecyclePolicy,
    NaturalPartitionIdentity,
    NoCoverage,
    StorageResolutionError,
)
from quant_platform.data.parquet import read_trade_v1, resolve_partition_path  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "Bybit", "BTCUSDT", "trade-v1")
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
SCHEMA_HASH = "a" * 64
DATASET = CatalogDataset(IDENTITY, "dataset-a", REL_ROOT, "b" * 64, 1, SCHEMA_HASH)


def instant(text: str) -> Instant:
    return Instant.parse(text)


def request(start: str, end: str, *, policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY) -> DataRequest:
    return DataRequest(IDENTITY, start, end, lifecycle_policy=policy)


def write_parquet(root: Path, filename: str, times: list[str], *, ids: list[str] | None = None) -> Path:
    target = root / REL_ROOT / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    ids = ids or [str(index + 1) for index in range(len(times))]
    table = pa.table(
        {
            "venue": pa.array(["bybit"] * len(times), type=pa.string()),
            "instrument": pa.array(["BTCUSDT"] * len(times), type=pa.string()),
            "exchange_ts": pa.array(
                [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in times],
                type=pa.timestamp("ns", tz="UTC"),
            ),
            "price": pa.array(["100.0"] * len(times), type=pa.string()),
            "size": pa.array(["0.1"] * len(times), type=pa.string()),
            "aggressor_side": pa.array(["buy"] * len(times), type=pa.string()),
            "receive_ts": pa.array([None] * len(times), type=pa.string()),
            "trade_id": pa.array(ids, type=pa.string()),
            "sequence": pa.array([None] * len(times), type=pa.string()),
        }
    )
    pq.write_table(table, target)
    return target


def partition(
    root: Path,
    start: str,
    end: str,
    key: str,
    *,
    rows: int,
    state: str = "valid",
    partition_id: str = "partition-a",
    revision: int = 1,
    times: list[str] | None = None,
    ids: list[str] | None = None,
    content_hash: str = "c" * 64,
    manifest_hash: str = "d" * 64,
) -> CatalogPartition:
    filename = f"{key}/part-000.parquet"
    write_parquet(root, filename, times or [], ids=ids) if rows or times is not None else write_parquet(root, filename, [])
    return CatalogPartition(
        NaturalPartitionIdentity(IDENTITY, key, revision),
        partition_id,
        "hot",
        str(root),
        REL_ROOT,
        filename,
        instant(start),
        instant(end),
        rows,
        content_hash,
        manifest_hash,
        state,
        "fixture",
        "test",
    )


class FakeCatalog:
    def __init__(self, dataset: CatalogDataset, partitions: list[CatalogPartition]):
        self.dataset = dataset
        self.partitions = partitions
        self.last_candidate_count = 0
        self.selected: list[CatalogPartition] = []

    def resolve_dataset(self, identity):
        self.assert_identity = identity
        return self.dataset

    def select_partitions(self, dataset, start, end, states):
        self.selected = [
            item
            for item in self.partitions
            if item.state in states
            and item.coverage is not None
            and item.ts_end > start
            and item.ts_start < end
        ]
        self.last_candidate_count = len(self.selected)
        return self.selected


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.query, self.params = query, params

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.cursor_obj = Cursor(rows)

    def cursor(self):
        return self.cursor_obj


class DataGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def gateway(self, partitions):
        return DataGateway(FakeCatalog(DATASET, partitions))

    def test_identity_normalization_and_equality(self):
        self.assertEqual(IDENTITY, DatasetIdentity("CANONICAL", "TRADES", "bybit", "BTCUSDT", "TRADE-V1"))
        self.assertNotEqual(IDENTITY, DatasetIdentity("canonical", "trades", "bybit", "btc-usdt", "trade-v1"))

    def test_half_open_boundary_and_parquet_reader(self):
        p = partition(
            self.root, "2024-01-01T00:00:00Z", "2024-01-01T04:00:00Z", "dt=2024-01-01",
            rows=4,
            times=[f"2024-01-01T0{hour}:00:00Z" for hour in range(4)],
        )
        result = self.gateway([p]).read(request("2024-01-01T01:00:00Z", "2024-01-01T03:00:00Z"))
        self.assertEqual([str(row.exchange_ts) for row in result], [
            "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"
        ])
        self.assertEqual(len(read_trade_v1(resolve_partition_path(str(self.root), REL_ROOT, p.rel_path), request("2024-01-01T00:00:00Z", "2024-01-01T04:00:00Z").start, request("2024-01-01T00:00:00Z", "2024-01-01T04:00:00Z").end)), 4)

    def test_partition_pruning_reads_only_temporal_candidates(self):
        early = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=1, times=["2024-01-01T00:30:00Z"])
        late = partition(self.root, "2024-01-02T00:00:00Z", "2024-01-02T01:00:00Z", "dt=2024-01-02", rows=1, times=["2024-01-02T00:30:00Z"], partition_id="partition-b")
        gateway = self.gateway([early, late])
        result = gateway.read(request("2024-01-02T00:00:00Z", "2024-01-02T01:00:00Z"))
        self.assertEqual(len(result.records), 1)
        self.assertEqual(gateway.catalog.last_candidate_count, 1)
        self.assertEqual(gateway.catalog.selected[0].natural_identity.partition_key, "dt=2024-01-02")

    def test_full_coverage_zero_coverage_and_empty_but_covered(self):
        empty = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=0)
        result = self.gateway([empty]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"))
        self.assertEqual(result.records, ())
        self.assertTrue(result.metadata.coverage_complete)
        self.assertIsNone(result.metadata.returned_record_bounds)
        with self.assertRaises(NoCoverage):
            self.gateway([partition(self.root, "2024-01-01T02:00:00Z", "2024-01-01T03:00:00Z", "dt=2024-01-02", rows=0)]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"))

    def test_partial_and_internal_gap(self):
        left = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=1, times=["2024-01-01T00:30:00Z"])
        right = partition(self.root, "2024-01-01T02:00:00Z", "2024-01-01T03:00:00Z", "dt=2024-01-01/hour=02", rows=1, times=["2024-01-01T02:30:00Z"], partition_id="partition-b")
        with self.assertRaises(NoCoverage):
            self.gateway([left, right]).read(request("2024-01-01T00:00:00Z", "2024-01-01T03:00:00Z"))

    def test_adjacency_is_not_overlap_but_true_overlap_is_conflict(self):
        left = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=1, times=["2024-01-01T00:30:00Z"])
        right = partition(self.root, "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z", "dt=2024-01-02", rows=1, times=["2024-01-01T01:30:00Z"], partition_id="partition-b")
        self.assertTrue(self.gateway([left, right]).read(request("2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z")).metadata.coverage_complete)
        overlap = partition(self.root, "2024-01-01T00:30:00Z", "2024-01-01T01:30:00Z", "dt=2024-01-03", rows=1, times=["2024-01-01T00:45:00Z"], partition_id="partition-c")
        with self.assertRaises(CatalogConflict):
            self.gateway([left, overlap]).read(request("2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z"))

    def test_lifecycle_filtering_and_opt_in(self):
        closed = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=1, state="closed", times=["2024-01-01T00:30:00Z"])
        with self.assertRaises(NoCoverage):
            self.gateway([closed]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"))
        result = self.gateway([closed]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", policy=LifecyclePolicy.VALID_AND_CLOSED))
        self.assertEqual(len(result.records), 1)

    def test_deterministic_ordering_and_identity_ignores_uuid_and_path(self):
        p = partition(self.root, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "dt=2024-01-01", rows=2, times=["2024-01-01T00:00:00Z"] * 2, ids=["20", "10"])
        first = self.gateway([p]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"))
        other_root = Path(self.temp.name) / "relocated"
        relocated_path = write_parquet(other_root, p.rel_path, ["2024-01-01T00:00:00Z"] * 2, ids=["20", "10"])
        relocated = CatalogPartition(p.natural_identity, "different-uuid", "cold", str(other_root), REL_ROOT, p.rel_path, p.ts_start, p.ts_end, 2, p.content_sha256, p.manifest_sha256, "valid", p.producer, p.code_ref)
        second = self.gateway([relocated]).read(request("2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"))
        self.assertEqual([r.trade_id for r in first], ["10", "20"])
        self.assertEqual(first.metadata.request_identity, second.metadata.request_identity)
        self.assertEqual(first.metadata.result_identity, second.metadata.result_identity)
        self.assertNotEqual(first.metadata.catalog_partition_ids, second.metadata.catalog_partition_ids)
        self.assertNotEqual(first.metadata.rel_paths, (str(relocated_path),))

    def test_path_safety(self):
        with self.assertRaises(StorageResolutionError):
            resolve_partition_path(str(self.root), REL_ROOT, "../escape.parquet")
        with self.assertRaises(StorageResolutionError):
            resolve_partition_path(str(self.root), REL_ROOT, "dt=2024-01-01/part.txt")

    def test_catalog_resolver_and_sql_pruning(self):
        row = ("dataset-a", "canonical", "trades", "bybit", "BTCUSDT", None, "trade-v1", REL_ROOT, "b" * 64, 1, SCHEMA_HASH)
        connection = Connection([row])
        catalog = Catalog(connection=connection)
        resolved = catalog.resolve_dataset(IDENTITY)
        self.assertEqual(resolved.identity, IDENTITY)
        connection.cursor_obj.rows = [
            ("partition-a", "dt=2024-01-01", 1, "hot", str(self.root), REL_ROOT,
             "dt=2024-01-01/part-000.parquet", datetime(2024, 1, 1, tzinfo=timezone.utc),
             datetime(2024, 1, 2, tzinfo=timezone.utc), 1, "c" * 64, "d" * 64,
             "valid", "fixture", "test")
        ]
        catalog.select_partitions(resolved, instant("2024-01-01T00:00:00Z"), instant("2024-01-02T00:00:00Z"), ("valid",))
        query = connection.cursor_obj.query
        self.assertIn("p.ts_end > %s", query)
        self.assertIn("p.ts_start < %s", query)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
