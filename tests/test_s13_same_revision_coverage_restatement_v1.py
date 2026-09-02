#!/usr/bin/env python3
"""Focused tests for S13 same-revision declared-coverage restatement."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data import (  # noqa: E402
    CatalogPublicationConflict,
    CatalogPublicationWriter,
    DatasetIdentity,
    Instant,
    PublicationCertification,
    SealedPartitionEvidence,
    TradeRecord,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET = {
    "layer": IDENTITY.layer,
    "dataset_kind": IDENTITY.dataset_kind,
    "venue": IDENTITY.venue,
    "instrument": IDENTITY.instrument,
    "record_schema_id": IDENTITY.record_schema_id,
    "rel_root": "canonical/trades/bybit/BTCUSDT/trade-v1",
    "_manifest_sha256": "d" * 64,
}
START_A = Instant.parse("2024-01-15T00:00:00Z")
END_A = Instant.parse("2024-01-16T00:00:00Z")
START_B = Instant.parse("2024-01-15T00:00:00Z")
END_B = Instant.parse("2024-01-15T12:00:00Z")


def partition(revision: int = 1, token: str = "a") -> dict[str, object]:
    return {
        "partition_key": "dt=2024-01-15",
        "revision": revision,
        "rel_path": "dt=2024-01-15/part-000.parquet",
        "row_count": 2,
        "file_size_bytes": 100,
        "sha256": token * 64,
        "_manifest_sha256": token.upper() * 64,
        "created_at": "2026-09-01T10:00:00Z",
        "closed_at": "2026-09-01T10:00:01Z",
        "first_sequence": None,
        "last_sequence": None,
        "producer": "materializer-v1",
        "code_ref": "producer-ref-v1",
    }


def db_timestamp(value: str) -> datetime:
    return Instant.parse(value).to_datetime()


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.one = None
        self.many: list[tuple[object, ...]] = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, params=None) -> None:
        self.connection.statements.append((statement, params))
        self.one = None
        self.many = []
        if "FROM catalog.datasets" in statement:
            self.one = (
                "dataset-1", self.connection.dataset_rel_root,
                self.connection.dataset_manifest_sha,
            )
        elif "INSERT INTO catalog.datasets" in statement:
            self.one = ("dataset-1",)
        elif "FROM catalog.partitions" in statement and "SELECT state" in statement:
            partition_id = params[0]
            row = next((item for item in self.connection.rows if item[0] == partition_id), None)
            self.one = None if row is None else (row[1],)
        elif "FROM catalog.partitions" in statement and "WHERE partition_id = %s" in statement:
            partition_id = params[0]
            row = next((item for item in self.connection.rows if item[0] == partition_id), None)
            if row is not None:
                self.one = (
                    row[0], "dataset-1", "dt=2024-01-15", row[1], row[2], row[3],
                    row[4], row[5], row[6], row[7], row[8], row[9], row[10],
                    row[11], row[12], row[13], row[14], row[15], row[16],
                )
        elif "FROM catalog.partitions" in statement:
            self.many = sorted(self.connection.rows, key=lambda item: item[2])
        elif "UPDATE catalog.partitions" in statement:
            if "SET ts_start" in statement:
                start, end, partition_id = params
                for index, row in enumerate(self.connection.rows):
                    if row[0] == partition_id and row[1] == "closed":
                        self.connection.rows[index] = (row[0], row[1], row[2], row[3], row[4], start, end, *row[7:])
                        self.one = (partition_id,)
                        break
            else:
                partition_id = params[0]
                for index, row in enumerate(self.connection.rows):
                    if row[0] == partition_id and row[1] != "superseded":
                        self.connection.rows[index] = (row[0], "superseded", *row[2:])
                        self.one = (partition_id,)
                        break
        elif "INSERT INTO catalog.partitions" in statement:
            values = params
            partition_id = f"partition-{self.connection.next_partition_id}"
            self.connection.next_partition_id += 1
            self.connection.rows.append((
                partition_id, "closed", values["revision"], values["storage_root_id"],
                values["rel_path"], values["ts_start"], values["ts_end"],
                values["row_count"], values["byte_size"], values["content_sha256"],
                values["manifest_sha256"], db_timestamp(values["created_at"]),
                db_timestamp(values["closed_at"]), values["first_sequence"],
                values["last_sequence"], values["producer"], values["code_ref"],
            ))
            self.one = (partition_id,)
        elif "FROM catalog.quality_reports" in statement:
            partition_id, check_suite = params
            self.many = [
                item for item in self.connection.quality_reports
                if item[5] == partition_id and item[6] == check_suite
            ]
        elif "INSERT INTO catalog.quality_reports" in statement:
            partition_id, check_suite, status, metrics, violations, code_ref = params
            report_id = f"report-{len(self.connection.quality_reports) + 1}"
            self.connection.quality_reports.append(
                (report_id, status, _unwrap_json(metrics), _unwrap_json(violations),
                 code_ref, partition_id, check_suite)
            )
            self.one = (report_id,)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


def _unwrap_json(value):
    return deepcopy(getattr(value, "obj", getattr(value, "adapted", value)))


class Connection:
    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []
        self.quality_reports: list[tuple[object, ...]] = []
        self.statements: list[tuple[str, object]] = []
        self.next_partition_id = 1
        self.dataset_rel_root = DATASET["rel_root"]
        self.dataset_manifest_sha = DATASET["_manifest_sha256"]
        self._before_transaction = None

    def cursor(self) -> Cursor:
        if self._before_transaction is None:
            self._before_transaction = (deepcopy(self.rows), deepcopy(self.quality_reports), self.next_partition_id)
        return Cursor(self)

    def commit(self) -> None:
        self._before_transaction = None

    def rollback(self) -> None:
        if self._before_transaction is not None:
            self.rows, self.quality_reports, self.next_partition_id = self._before_transaction
        self._before_transaction = None

    def seed(self, state: str = "closed", *, start: Instant = START_A, end: Instant = END_A) -> None:
        item = partition()
        self.rows.append((
            f"partition-{self.next_partition_id}", state, 1, "hot", item["rel_path"],
            start.to_datetime(), end.to_datetime(), item["row_count"], item["file_size_bytes"],
            item["sha256"], item["_manifest_sha256"], db_timestamp(item["created_at"]),
            db_timestamp(item["closed_at"]), None, None, item["producer"], item["code_ref"],
        ))
        self.next_partition_id += 1
        self.commit()


class SameRevisionRestatementTests(unittest.TestCase):
    def admit(self, connection: Connection, *, start=START_A, end=END_A, item=None):
        result = CatalogPublicationWriter(connection).seal_partition(
            dataset=DATASET, partition=item or partition(),
            coverage_start=start, coverage_end=end, storage_root_id="hot",
        )
        connection.commit()
        return result

    def test_same_revision_restatement_updates_only_coverage_in_place(self):
        connection = Connection()
        first = self.admit(connection)
        original = connection.rows[0]
        restated = self.admit(connection, start=START_B, end=END_B)
        self.assertEqual(restated.partition_id, first.partition_id)
        self.assertEqual(restated.natural_identity.revision, 1)
        self.assertEqual(restated.state, "closed")
        self.assertEqual((restated.ts_start, restated.ts_end), (START_B, END_B))
        self.assertEqual(len(connection.rows), 1)
        self.assertEqual(connection.rows[0][:5], original[:5])
        self.assertEqual(connection.rows[0][7:], original[7:])
        self.assertEqual(connection.rows[0][5:7], (START_B.to_datetime(), END_B.to_datetime()))

    def test_restatement_retry_is_idempotent(self):
        connection = Connection()
        first = self.admit(connection)
        restated = self.admit(connection, start=START_B, end=END_B)
        retry = self.admit(connection, start=START_B, end=END_B)
        self.assertEqual(retry.partition_id, restated.partition_id)
        self.assertEqual(first.partition_id, retry.partition_id)
        self.assertEqual(len(connection.rows), 1)
        self.assertEqual(connection.rows[0][1], "closed")

    def test_non_coverage_conflict_does_not_partially_restate(self):
        for field, value, storage_root_id in (
            ("sha256", "f" * 64, "hot"),
            ("_manifest_sha256", "F" * 64, "hot"),
            ("rel_path", "dt=2024-01-15/other.parquet", "hot"),
            ("producer", "different-producer", "hot"),
            ("storage_root_id", None, "cold"),
        ):
            with self.subTest(field=field):
                connection = Connection()
                self.admit(connection)
                original = deepcopy(connection.rows)
                conflicting = partition()
                if field != "storage_root_id":
                    conflicting[field] = value
                with self.assertRaises(CatalogPublicationConflict):
                    CatalogPublicationWriter(connection).seal_partition(
                        dataset=DATASET, partition=conflicting,
                        coverage_start=START_B, coverage_end=END_B,
                        storage_root_id=storage_root_id,
                    )
                connection.rollback()
                self.assertEqual(connection.rows, original)

    def test_valid_target_refuses_changed_coverage(self):
        connection = Connection()
        connection.seed("valid")
        original = deepcopy(connection.rows)
        with self.assertRaises(CatalogPublicationConflict):
            CatalogPublicationWriter(connection).seal_partition(
                dataset=DATASET, partition=partition(),
                coverage_start=START_B, coverage_end=END_B, storage_root_id="hot",
            )
        connection.rollback()
        self.assertEqual(connection.rows, original)

    def test_degraded_target_refuses_changed_coverage(self):
        connection = Connection()
        connection.seed("degraded")
        original = deepcopy(connection.rows)
        with self.assertRaises(CatalogPublicationConflict):
            CatalogPublicationWriter(connection).seal_partition(
                dataset=DATASET, partition=partition(),
                coverage_start=START_B, coverage_end=END_B, storage_root_id="hot",
            )
        connection.rollback()
        self.assertEqual(connection.rows, original)

    def test_quality_report_is_preserved_across_restatement(self):
        connection = Connection()
        first = self.admit(connection)
        writer = CatalogPublicationWriter(connection)
        old = writer.record_quality_report(
            partition_id=first.partition_id, check_suite="suite-v1", status="pass",
            metrics={"coverage": "A"}, violations=[], code_ref="certifier-v1",
        )
        connection.commit()
        self.admit(connection, start=START_B, end=END_B)
        self.assertEqual(len(connection.quality_reports), 1)
        self.assertEqual(connection.quality_reports[0][0], old.report_id)
        self.assertEqual(connection.quality_reports[0][1:5], ("pass", {"coverage": "A"}, [], "certifier-v1"))

    def test_restatement_keeps_dataset_then_topology_lock_order(self):
        connection = Connection()
        self.admit(connection)
        statements = [statement for statement, _ in connection.statements]
        dataset_lock = next(
            index for index, statement in enumerate(statements)
            if "FROM catalog.datasets" in statement and "FOR UPDATE" in statement
        )
        topology_lock = next(
            index for index, statement in enumerate(statements)
            if "FROM catalog.partitions" in statement
            and "ORDER BY revision" in statement
            and "FOR UPDATE" in statement
        )
        self.assertLess(dataset_lock, topology_lock)


class OrchestrationRestatementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.dataset_path = self.root / "dataset.json"
        self.partition_path = self.root / "partition.json"
        self.coverage_path = self.root / "coverage.json"
        self.artifact_path = self.root / "dt=2024-01-15" / "part-000.parquet"
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=IDENTITY,
            created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        materialization = materialize_bybit_trade_v1(
            self.artifact_path,
            [
                TradeRecord("bybit", "BTCUSDT", Instant.parse("2024-01-15T00:00:01Z"), "100.00", "0.5000", "buy", None, "1", None),
                TradeRecord("bybit", "BTCUSDT", Instant.parse("2024-01-15T00:00:02Z"), "100.00", "0.5000", "buy", None, "2", None),
            ],
            dataset_identity=IDENTITY,
        )
        self.partition = emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=IDENTITY,
            dataset_root=self.root, partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="materializer-v1", code_ref="producer-ref-v1",
        )
        self.write_coverage(START_A, END_A, "coverage-a", "assertion-a")

    def tearDown(self):
        self.tempdir.cleanup()

    def write_coverage(self, start: Instant, end: Instant, coverage_id: str, assertion_id: str):
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
            coverage_id=coverage_id, supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": START_A, "intent_end": END_A,
                "source_semantics": "bybit-public-trades-sqlite-v1",
                "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": assertion_id, "start": start, "end": end,
                "status": "complete",
                "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": "test source"}],
            }], producer="source-v1", code_ref="source-ref-v1",
            partition_manifests=[self.partition.document],
        )

    def evidence(self) -> SealedPartitionEvidence:
        return SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,),
            self.artifact_path, "hot",
        )

    def runtime(self, connection: Connection) -> PublicationCertification:
        return PublicationCertification(
            CatalogPublicationWriter(connection),
            BybitTradeV1CertificationProfile("certifier-v1"),
            batch_size=1,
        )

    def restate(self, connection: Connection):
        connection.dataset_manifest_sha = hashlib.sha256(self.dataset_path.read_bytes()).hexdigest()
        first = self.runtime(connection).run(self.evidence())
        self.write_coverage(START_B, END_B, "coverage-b", "assertion-b")
        restated = self.runtime(connection).seal(self.evidence())
        return first, restated

    def test_fresh_phase_three_report_coexists_with_old_coverage_evidence(self):
        connection = Connection()
        first, restated = self.restate(connection)
        result = self.runtime(connection).certify(self.evidence(), restated)
        fresh = self.runtime(connection).record_evidence(restated, result)
        self.assertEqual(first.sealed_partition.partition_id, restated.partition_id)
        self.assertNotEqual(first.quality_report.report_id, fresh.report_id)
        self.assertEqual(len(connection.quality_reports), 2)
        self.assertEqual(connection.quality_reports[0][2]["coverage_manifest_id"], "coverage-a")
        self.assertEqual(connection.quality_reports[1][2]["coverage_manifest_id"], "coverage-b")
        self.assertEqual(connection.quality_reports[0][5], connection.quality_reports[1][5])

    def test_phase_two_failure_after_restatement_does_not_restore_old_coverage(self):
        connection = Connection()
        first, restated = self.restate(connection)
        self.artifact_path.write_bytes(self.artifact_path.read_bytes() + b"tampered")
        result = self.runtime(connection).certify(self.evidence(), restated)
        self.assertEqual(result.status, "fail")
        self.assertEqual(connection.rows[0][5], START_B.to_datetime())
        self.assertEqual(connection.rows[0][6], END_B.to_datetime())
        self.assertEqual(connection.quality_reports[0][0], first.quality_report.report_id)

    def test_phase_three_failure_after_restatement_does_not_restore_old_coverage(self):
        connection = Connection()
        first, restated = self.restate(connection)
        result = self.runtime(connection).certify(self.evidence(), restated)

        class FailingWriter(CatalogPublicationWriter):
            def record_quality_report(self, **kwargs):
                raise RuntimeError("simulated Phase 3 persistence failure")

        failing_runtime = PublicationCertification(
            FailingWriter(connection), BybitTradeV1CertificationProfile("certifier-v1"), batch_size=1,
        )
        with self.assertRaises(RuntimeError):
            failing_runtime.record_evidence(restated, result)
        self.assertEqual(connection.rows[0][5], START_B.to_datetime())
        self.assertEqual(connection.rows[0][6], END_B.to_datetime())
        self.assertEqual(len(connection.quality_reports), 1)
        self.assertEqual(connection.quality_reports[0][0], first.quality_report.report_id)


if __name__ == "__main__":
    unittest.main()
