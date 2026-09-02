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
from dataclasses import replace
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


def write_evidence(
    root: Path,
    dataset_path: Path,
    identity: DatasetIdentity,
    revision: int,
    *,
    coverage_start: str = "2024-01-15T00:00:00Z",
    coverage_end: str = "2024-01-16T00:00:00Z",
    coverage_id: str | None = None,
    assertion_id: str | None = None,
    coverage_path: Path | None = None,
) -> SealedPartitionEvidence:
    artifact = root / "dt=2024-01-15" / f"part-{revision:03d}.parquet"
    partition_path = root / f"partition-{revision}.json"
    coverage_path = coverage_path or root / f"coverage-{revision}.json"
    materialization = materialize_bybit_trade_v1(
        artifact,
        [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")],
        dataset_identity=identity,
    )
    partition = emit_partition_manifest(
        partition_path, materialization, dataset_identity=identity, dataset_root=root,
        partition_key="dt=2024-01-15", revision=revision,
        rel_path=f"dt=2024-01-15/part-{revision:03d}.parquet",
        created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
        producer=f"integration-materializer-{revision}", code_ref=f"integration-producer-{revision}",
    )
    emit_coverage_manifest(
        coverage_path, dataset_identity=identity, source_dataset_identity=identity,
        coverage_id=coverage_id or f"integration-coverage-{revision}", supersedes=None,
        created_at="2026-09-01T10:00:02Z",
        acquisition={
            "basis": "source_extract", "intent_start": "2024-01-15T00:00:00Z",
            "intent_end": "2024-01-16T00:00:00Z",
            "source_semantics": "bybit-public-trades-sqlite-v1",
            "mapping": "bybit-sqlite-day-extract-v1",
        },
        assertions=[{
            "assertion_id": assertion_id or f"integration-assertion-{revision}",
            "start": coverage_start, "end": coverage_end,
            "status": "complete",
            "partitions": [{"partition_key": "dt=2024-01-15", "revision": revision}],
            "evidence": [{"kind": "deterministic_source_extract", "detail": "integration source"}],
        }], producer=f"integration-source-{revision}", code_ref=f"integration-source-{revision}",
        partition_manifests=[partition.document],
    )
    return SealedPartitionEvidence(
        dataset_path, partition_path, (coverage_path,), artifact, "hot",
    )


class FailAfterCoverageUpdateConnection:
    """Inject a real PostgreSQL error after the restatement UPDATE executes."""

    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return FailAfterCoverageUpdateCursor(self.connection.cursor())

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()


class FailAfterCoverageUpdateCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, statement, params=None):
        self.cursor.execute(statement, params)
        if "UPDATE catalog.partitions" in statement and "SET ts_start" in statement:
            self.cursor.execute("SELECT 1 / 0")

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


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
        dataset_path = root / "dataset.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        evidence = {
            revision: write_evidence(root, dataset_path, identity, revision)
            for revision in (1, 2, 3, 4, 5)
        }

        connection = psycopg.connect(dsn)
        owns_fixture = False
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
            run1 = runtime.run(evidence[1])
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, state, partition_id::text FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (run1.sealed_partition.dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchall() == [(1, "closed", run1.sealed_partition.partition_id)]
                cursor.execute(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'catalog' AND indexname = 'partitions_one_live'"
                )
                assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]

            original_partition_bytes = evidence[1].partition_manifest_path.read_bytes()
            conflicting_partition = json.loads(original_partition_bytes)
            conflicting_partition["producer"] = "conflicting-producer"
            evidence[1].partition_manifest_path.write_text(json.dumps(conflicting_partition))
            try:
                runtime.run(evidence[1])
            except CatalogPublicationConflict:
                pass
            else:
                raise AssertionError("conflicting sealed evidence was accepted")
            evidence[1].partition_manifest_path.write_bytes(original_partition_bytes)

            restated_coverage_path = root / "coverage-1-restated.json"
            restated_evidence = write_evidence(
                root, dataset_path, identity, 1,
                coverage_start="2024-01-15T00:00:00Z",
                coverage_end="2024-01-15T12:00:00Z",
                coverage_id="integration-coverage-1-restated",
                assertion_id="integration-assertion-1-restated",
                coverage_path=restated_coverage_path,
            )
            restated_evidence = replace(
                restated_evidence,
                partition_manifest_path=evidence[1].partition_manifest_path,
            )

            for lifecycle_state in ("valid", "degraded"):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE catalog.partitions SET state = %s WHERE partition_id = %s",
                        (lifecycle_state, run1.sealed_partition.partition_id),
                    )
                connection.commit()
                try:
                    runtime.seal(restated_evidence)
                except CatalogPublicationConflict:
                    pass
                else:
                    raise AssertionError(f"{lifecycle_state} target accepted changed coverage")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT state, ts_start, ts_end FROM catalog.partitions WHERE partition_id = %s",
                        (run1.sealed_partition.partition_id,),
                    )
                    row = cursor.fetchone()
                    assert row == (
                        lifecycle_state,
                        Instant.parse("2024-01-15T00:00:00Z").to_datetime(),
                        Instant.parse("2024-01-16T00:00:00Z").to_datetime(),
                    )
                    cursor.execute(
                        "UPDATE catalog.partitions SET state = 'closed' WHERE partition_id = %s",
                        (run1.sealed_partition.partition_id,),
                    )
                connection.commit()

            rollback_runtime = PublicationCertification(
                CatalogPublicationWriter(FailAfterCoverageUpdateConnection(connection)),
                BybitTradeV1CertificationProfile("integration-rollback-certifier"),
            )
            try:
                rollback_runtime.seal(restated_evidence)
            except psycopg.errors.DivisionByZero:
                pass
            else:
                raise AssertionError("real post-update rollback failure did not propagate")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state, ts_start, ts_end FROM catalog.partitions WHERE partition_id = %s",
                    (run1.sealed_partition.partition_id,),
                )
                assert cursor.fetchone() == (
                    "closed",
                    Instant.parse("2024-01-15T00:00:00Z").to_datetime(),
                    Instant.parse("2024-01-16T00:00:00Z").to_datetime(),
                )

            restated_run = runtime.run(restated_evidence)
            assert restated_run.sealed_partition.partition_id == run1.sealed_partition.partition_id
            assert restated_run.sealed_partition.natural_identity.revision == 1
            assert restated_run.sealed_partition.state == "closed"
            assert restated_run.sealed_partition.ts_end == Instant.parse("2024-01-15T12:00:00Z")
            assert restated_run.quality_report.report_id != run1.quality_report.report_id
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT partition_id::text, status, metrics, violations, code_ref FROM catalog.quality_reports WHERE partition_id = %s ORDER BY ran_at, report_id::text",
                    (run1.sealed_partition.partition_id,),
                )
                reports = cursor.fetchall()
                assert len(reports) == 2
                assert reports[0][0] == reports[1][0] == run1.sealed_partition.partition_id
                assert reports[0][1] == reports[1][1] == "pass"
                assert reports[0][2]["coverage_manifest_id"] == "integration-coverage-1"
                assert reports[1][2]["coverage_manifest_id"] == "integration-coverage-1-restated"
                assert reports[0][3] == reports[1][3] == []
                assert reports[0][4] == reports[1][4] == "integration-certifier"

            run2 = runtime.run(evidence[2])
            retry2 = runtime.run(evidence[2])
            assert retry2.sealed_partition.partition_id == run2.sealed_partition.partition_id
            assert retry2.quality_report.report_id == run2.quality_report.report_id
            assert run1.sealed_partition.partition_id != run2.sealed_partition.partition_id
            assert run2.certification.metrics["natural_partition_identity"]["revision"] == 2
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, state, partition_id::text FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (run1.sealed_partition.dataset_id, "dt=2024-01-15"),
                )
                topology = cursor.fetchall()
                assert [(row[0], row[1]) for row in topology] == [(1, "superseded"), (2, "closed")]
                assert sum(row[1] != "superseded" for row in topology) == 1
                cursor.execute(
                    "SELECT partition_id::text, code_ref, metrics #>> '{natural_partition_identity,revision}' FROM catalog.quality_reports WHERE partition_id IN (%s, %s)",
                    (run1.sealed_partition.partition_id, run2.sealed_partition.partition_id),
                )
                assert {
                    row[0]: (row[1], row[2]) for row in cursor.fetchall()
                } == {
                    run1.sealed_partition.partition_id: ("integration-certifier", "1"),
                    run2.sealed_partition.partition_id: ("integration-certifier", "2"),
                }
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s",
                    (run1.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == 2
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s",
                    (run2.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == 1

            before_gap = topology
            try:
                runtime.run(evidence[4])
            except CatalogPublicationConflict:
                pass
            else:
                raise AssertionError("revision gap was accepted")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, state, partition_id::text FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (run1.sealed_partition.dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchall() == before_gap

            rollback_runtime = PublicationCertification(
                CatalogPublicationWriter(connection),
                BybitTradeV1CertificationProfile("integration-rollback-certifier"),
            )
            try:
                rollback_runtime.seal(replace(
                    evidence[3], storage_root_id="missing-storage-root",
                ))
            except psycopg.errors.ForeignKeyViolation:
                pass
            else:
                raise AssertionError("invalid storage root did not fail successor insertion")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, state, partition_id::text FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (run1.sealed_partition.dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchall() == before_gap

            class FailingPhaseThreeWriter(CatalogPublicationWriter):
                def record_quality_report(self, **kwargs):
                    raise RuntimeError("simulated Phase 3 failure")

            failing_runtime = PublicationCertification(
                FailingPhaseThreeWriter(connection),
                BybitTradeV1CertificationProfile("failing-certifier"),
            )
            try:
                failing_runtime.run(evidence[3])
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
                    "SELECT revision, state, partition_id::text FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (dataset_id, "dt=2024-01-15"),
                )
                after_phase_three_failure = cursor.fetchall()
                assert [(row[0], row[1]) for row in after_phase_three_failure] == [
                    (1, "superseded"), (2, "superseded"), (3, "closed")
                ]
                assert sum(row[1] != "superseded" for row in after_phase_three_failure) == 1
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s",
                    (after_phase_three_failure[-1][2],),
                )
                assert cursor.fetchone()[0] == 0
                try:
                    cursor.execute(
                        """
                        INSERT INTO catalog.quality_reports
                            (partition_id, dataset_id, check_suite, status)
                        VALUES (%s, %s, 'xor-test', 'pass')
                        """,
                        (run1.sealed_partition.partition_id, dataset_id),
                    )
                except psycopg.errors.CheckViolation:
                    connection.rollback()
                else:
                    raise AssertionError("quality_reports XOR constraint did not reject both targets")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s",
                    (run1.sealed_partition.partition_id,),
                )
                assert cursor.fetchone()[0] == 2
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
                    "SELECT revision, state FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchall()[-1] == (3, "closed")
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id = %s AND code_ref = 'failing-certifier'",
                    (after_phase_three_failure[-1][2],),
                )
                assert cursor.fetchone()[0] == 0

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO catalog.partitions (
                        dataset_id, partition_key, revision, storage_root_id,
                        rel_path, ts_start, ts_end, row_count, byte_size,
                        content_sha256, state, manifest_sha256, created_at,
                        closed_at, producer, code_ref
                    ) VALUES (%s, %s, 4, 'hot', %s, %s, %s, 0, 0, %s,
                              'superseded', %s, %s, %s, 'topology-test', 'topology-test')
                    """,
                    (
                        dataset_id, "dt=2024-01-15", "dt=2024-01-15/part-004.parquet",
                        Instant.parse(start).to_datetime(), Instant.parse(end).to_datetime(),
                        "e" * 64, "f" * 64,
                        Instant.parse("2026-09-01T10:00:00Z").to_datetime(),
                        Instant.parse("2026-09-01T10:00:01Z").to_datetime(),
                    ),
                )
            connection.commit()
            try:
                runtime.run(evidence[5])
            except CatalogPublicationConflict:
                pass
            else:
                raise AssertionError("inconsistent higher superseded topology was accepted")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, state FROM catalog.partitions WHERE dataset_id = %s AND partition_key = %s ORDER BY revision",
                    (dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchall() == [(1, "superseded"), (2, "superseded"), (3, "closed"), (4, "superseded")]
            print("PASS PostgreSQL 17 S13 revision admission and same-revision coverage restatement")
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
