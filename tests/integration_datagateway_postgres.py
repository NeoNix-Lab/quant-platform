#!/usr/bin/env python3
"""PostgreSQL 17 integration test for the catalog -> Parquet gateway path.

The disposable CI database is initialized by the existing integrity workflow.
Set ``DATA_GATEWAY_TEST_DSN`` (or the usual PG* environment) to run locally.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.catalog import Catalog  # noqa: E402
from quant_platform.data.gateway import DataGateway  # noqa: E402
from quant_platform.data.models import DataRequest, DatasetIdentity  # noqa: E402


def write_fixture(root: Path, rel_path: str) -> Path:
    target = root / "canonical/trades/bybit/BTCUSDT/trade-v1" / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "venue": ["bybit", "bybit"],
            "instrument": ["BTCUSDT", "BTCUSDT"],
            "exchange_ts": pa.array(
                [datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                 datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)],
                type=pa.timestamp("ns", tz="UTC"),
            ),
            "price": ["100.0", "101.0"],
            "size": ["0.1", "0.2"],
            "aggressor_side": ["buy", "sell"],
            "receive_ts": [None, None],
            "trade_id": ["20", "10"],
            "sequence": [None, None],
        }
    )
    pq.write_table(table, target)
    return target


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        dsn = None  # psycopg resolves the standard PG* environment variables.
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        rel_path = "dt=2024-01-15/part-000.parquet"
        data_file = write_fixture(root, rel_path)
        digest = hashlib.sha256(data_file.read_bytes()).hexdigest()
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE catalog.storage_roots SET abs_path = %s WHERE storage_root_id = 'hot'", (str(root),))
                cursor.execute(
                    """
                    INSERT INTO catalog.schema_registry (schema_id, name, version, json_sha256, body)
                    VALUES ('trade-v1', 'trade', 1, %s, '{}'::jsonb)
                    ON CONFLICT (schema_id) DO NOTHING
                    """,
                    ("a" * 64,),
                )
                cursor.execute(
                    """
                    INSERT INTO catalog.datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
                    VALUES ('canonical', 'trades', 'bybit', 'BTCUSDT',
                            'canonical/trades/bybit/BTCUSDT/trade-v1', 'trade-v1', %s)
                    RETURNING dataset_id
                    """,
                    ("b" * 64,),
                )
                dataset_id = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO catalog.partitions (
                        dataset_id, partition_key, storage_root_id, rel_path,
                        ts_start, ts_end, row_count, byte_size, content_sha256,
                        manifest_sha256, state, closed_at, producer, code_ref
                    ) VALUES (%s, 'dt=2024-01-15', 'hot', %s,
                              '2024-01-15T00:00:00Z', '2024-01-16T00:00:00Z',
                              2, %s, %s, %s, 'valid', now(), 'integration', 'test')
                    """,
                    (dataset_id, rel_path, data_file.stat().st_size, digest, "c" * 64),
                )
            identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
            result = DataGateway(Catalog(connection=connection)).read(
                DataRequest(identity, "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")
            )
            assert [record.trade_id for record in result] == ["10", "20"]
            assert result.metadata.coverage_complete
            assert result.metadata.row_count == 2
            print("PASS PostgreSQL 17 DataGateway integration: 1 dataset, 1 partition, 2 rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
