#!/usr/bin/env python3
"""Focused PostgreSQL proof for S13 seal and evidence persistence.

Run explicitly against a PostgreSQL database initialized by ``db/init``.  It
does not run as part of the local script runner because it requires external
database infrastructure.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from quant_platform.data import (  # noqa: E402
    CatalogPublicationConflict,
    CatalogPublicationWriter,
    DatasetIdentity,
    PublicationCertification,
    SealedPartitionEvidence,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)
from quant_platform.data import Instant, TradeRecord  # noqa: E402


def trade(timestamp: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        "bybit", "BTCUSDT", Instant.parse(timestamp),
        "100.00", "0.5000", "buy", None, trade_id, None,
    )


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL publication certification integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0

    identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
    start = "2024-01-15T00:00:00Z"
    end = "2024-01-16T00:00:00Z"
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        artifact = root / "dt=2024-01-15" / "part-000.parquet"
        dataset_path = root / "dataset.json"
        partition_path = root / "partition.json"
        coverage_path = root / "coverage.json"
        materialization = materialize_bybit_trade_v1(
            artifact,
            [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")],
            dataset_identity=identity,
        )
        emit_dataset_manifest(
            dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        partition = emit_partition_manifest(
            partition_path, materialization, dataset_identity=identity, dataset_root=root,
            partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="integration-materializer", code_ref="integration-producer",
        )
        emit_coverage_manifest(
            coverage_path, dataset_identity=identity, source_dataset_identity=identity,
            coverage_id="integration-coverage", supersedes=None,
            created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": start, "intent_end": end,
                "source_semantics": "bybit-public-trades-sqlite-v1",
                "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": "integration-assertion", "start": start, "end": end,
                "status": "complete",
                "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": "integration source"}],
            }], producer="integration-source", code_ref="integration-source",
            partition_manifests=[partition.document],
        )

        connection = psycopg.connect(dsn)
        owns_fixture = False
        original_partition_bytes = partition_path.read_bytes()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError("integration database already contains the fixed first-vertical dataset")
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO catalog.schema_registry (schema_id, name, version, json_sha256, body)
                    VALUES ('trade-v1', 'trade', 1, %s, %s::jsonb)
                    ON CONFLICT (schema_id) DO NOTHING
                    """,
                    (hashlib.sha256((ROOT / "schemas" / "trade-v1.json").read_bytes()).hexdigest(),
                     json.dumps(json.loads((ROOT / "schemas" / "trade-v1.json").read_text()))),
                )
            connection.commit()
            runtime = PublicationCertification(
                CatalogPublicationWriter(connection),
                BybitTradeV1CertificationProfile("integration-certifier"),
            )
            owns_fixture = True
            run = runtime.run(SealedPartitionEvidence(
                dataset_path, partition_path, (coverage_path,), artifact, "hot",
            ))
            retry = runtime.run(SealedPartitionEvidence(
                dataset_path, partition_path, (coverage_path,), artifact, "hot",
            ))
            assert retry.sealed_partition.partition_id == run.sealed_partition.partition_id
            assert retry.quality_report.report_id == run.quality_report.report_id
            conflicting_partition = json.loads(original_partition_bytes)
            conflicting_partition["producer"] = "conflicting-producer"
            partition_path.write_text(json.dumps(conflicting_partition))
            try:
                runtime.run(SealedPartitionEvidence(
                    dataset_path, partition_path, (coverage_path,), artifact, "hot",
                ))
            except CatalogPublicationConflict:
                pass
            else:
                raise AssertionError("conflicting sealed evidence was accepted")
            partition_path.write_bytes(original_partition_bytes)

            class FailingPhaseThreeWriter(CatalogPublicationWriter):
                def record_quality_report(self, **kwargs):
                    raise RuntimeError("simulated Phase 3 failure")

            failing_runtime = PublicationCertification(
                FailingPhaseThreeWriter(connection),
                BybitTradeV1CertificationProfile("failing-certifier"),
            )
            try:
                failing_runtime.run(SealedPartitionEvidence(
                    dataset_path, partition_path, (coverage_path,), artifact, "hot",
                ))
            except RuntimeError:
                pass
            else:
                raise AssertionError("simulated Phase 3 failure did not propagate")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT dataset_id::text FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
                )
                dataset_id = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT state FROM catalog.partitions WHERE partition_id = %s",
                    (run.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == "closed"
                cursor.execute(
                    "SELECT status, code_ref FROM catalog.quality_reports WHERE partition_id = %s",
                    (run.sealed_partition.partition_id,),
                )
                assert cursor.fetchone() == ("pass", "integration-certifier")
                try:
                    cursor.execute(
                        """
                        INSERT INTO catalog.quality_reports
                            (partition_id, dataset_id, check_suite, status)
                        VALUES (%s, %s, 'xor-test', 'pass')
                        """,
                        (run.sealed_partition.partition_id, dataset_id),
                    )
                except psycopg.errors.CheckViolation:
                    connection.rollback()
                else:
                    raise AssertionError("quality_reports XOR constraint did not reject both targets")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s",
                    (run.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == 1
                try:
                    cursor.execute(
                        "INSERT INTO catalog.quality_reports (partition_id, check_suite, status) VALUES ('00000000-0000-0000-0000-000000000000', 'fk-test', 'pass')"
                    )
                except psycopg.errors.ForeignKeyViolation:
                    connection.rollback()
                else:
                    raise AssertionError("quality_reports partition_id FK did not reject an unknown partition")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state FROM catalog.partitions WHERE partition_id = %s",
                    (run.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == "closed"
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s AND code_ref = 'failing-certifier'",
                    (run.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == 0
            print("PASS PostgreSQL publication certification integration: closed seal + quality report")
        finally:
            if owns_fixture:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
                    )
                connection.commit()
            else:
                connection.rollback()
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
