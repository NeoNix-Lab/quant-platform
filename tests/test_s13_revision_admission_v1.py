#!/usr/bin/env python3
"""Behavioral tests for the S13 contiguous revision-admission boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.publication_catalog import (  # noqa: E402
    CatalogPublicationConflict,
    CatalogPublicationWriter,
)
from quant_platform.data import Instant  # noqa: E402


START = Instant.parse("2024-01-15T00:00:00Z")
END = Instant.parse("2024-01-16T00:00:00Z")
DATASET = {
    "layer": "canonical",
    "dataset_kind": "trades",
    "venue": "bybit",
    "instrument": "BTCUSDT",
    "record_schema_id": "trade-v1",
    "rel_root": "canonical/trades/bybit/BTCUSDT/trade-v1",
    "_manifest_sha256": "d" * 64,
}


def partition(revision: int, token: str = "a") -> dict[str, object]:
    return {
        "partition_key": "dt=2024-01-15",
        "revision": revision,
        "rel_path": f"dt=2024-01-15/part-{revision:03d}.parquet",
        "row_count": 1,
        "file_size_bytes": 100 + revision,
        "sha256": token * 64,
        "_manifest_sha256": (token.upper() * 64),
        "created_at": "2026-09-01T10:00:00Z",
        "closed_at": "2026-09-01T10:00:01Z",
        "first_sequence": None,
        "last_sequence": None,
        "producer": f"producer-{revision}",
        "code_ref": f"producer-ref-{revision}",
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
            self.one = ("dataset-1", DATASET["rel_root"], DATASET["_manifest_sha256"])
        elif "INSERT INTO catalog.datasets" in statement:
            self.one = ("dataset-1",)
        elif "FROM catalog.partitions" in statement and "SELECT state" in statement:
            partition_id = params[0]
            row = next((item for item in self.connection.rows if item[0] == partition_id), None)
            self.one = None if row is None else (row[1],)
        elif "FROM catalog.partitions" in statement:
            self.many = sorted(self.connection.rows, key=lambda item: item[2])
        elif "UPDATE catalog.partitions" in statement:
            partition_id = params[0]
            for index, row in enumerate(self.connection.rows):
                if row[0] == partition_id and row[1] != "superseded":
                    self.connection.rows[index] = (row[0], "superseded", *row[2:])
                    self.one = (partition_id,)
                    break
        elif "INSERT INTO catalog.partitions" in statement:
            if self.connection.fail_successor_insert:
                raise RuntimeError("simulated successor insert failure")
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
            partition_id, check_suite, status, _, _, code_ref = params
            report_id = f"report-{len(self.connection.quality_reports) + 1}"
            self.connection.quality_reports.append(
                (report_id, status, {}, [], code_ref, partition_id, check_suite)
            )
            self.one = (report_id,)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []
        self.quality_reports: list[tuple[object, ...]] = []
        self.statements: list[tuple[str, object]] = []
        self.next_partition_id = 1
        self.fail_successor_insert = False
        self._before_transaction: list[tuple[object, ...]] | None = None

    def cursor(self) -> Cursor:
        if self._before_transaction is None:
            self._before_transaction = deepcopy(self.rows)
        return Cursor(self)

    def commit(self) -> None:
        self._before_transaction = None

    def rollback(self) -> None:
        if self._before_transaction is not None:
            self.rows = self._before_transaction
        self._before_transaction = None

    def seed(self, revision: int, state: str = "closed", token: str | None = None) -> None:
        item = partition(revision, token or chr(96 + revision))
        self.rows.append((
            f"partition-{self.next_partition_id}", state, revision, "hot",
            item["rel_path"], START.to_datetime(), END.to_datetime(), item["row_count"],
            item["file_size_bytes"], item["sha256"], item["_manifest_sha256"],
            db_timestamp(item["created_at"]), db_timestamp(item["closed_at"]),
            None, None, item["producer"], item["code_ref"],
        ))
        self.next_partition_id += 1


class RevisionAdmissionTests(unittest.TestCase):
    def admit(self, connection: Connection, item: dict[str, object]):
        result = CatalogPublicationWriter(connection).seal_partition(
            dataset=DATASET,
            partition=item,
            coverage_start=START,
            coverage_end=END,
            storage_root_id="hot",
        )
        connection.commit()
        return result

    def test_no_history_revision_one_passes(self):
        result = self.admit(Connection(), partition(1))
        self.assertEqual(result.natural_identity.revision, 1)

    def test_no_history_revision_two_is_rejected(self):
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(Connection(), partition(2))

    def test_live_revision_one_revision_two_is_atomic_successor(self):
        connection = Connection()
        first = self.admit(connection, partition(1))
        second = self.admit(connection, partition(2))
        self.assertNotEqual(first.partition_id, second.partition_id)
        self.assertEqual([row[1] for row in connection.rows], ["superseded", "closed"])
        self.assertEqual(sum(row[1] != "superseded" for row in connection.rows), 1)

    def test_successor_gap_is_rejected(self):
        connection = Connection()
        self.admit(connection, partition(1))
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, partition(3))

    def test_live_revision_three_target_five_is_rejected(self):
        connection = Connection()
        connection.seed(1, "superseded")
        connection.seed(2, "superseded")
        connection.seed(3)
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, partition(5))

    def test_superseded_revision_four_above_live_three_is_topology_conflict(self):
        connection = Connection()
        connection.seed(1, "superseded")
        connection.seed(3)
        connection.seed(4, "superseded")
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, partition(5))

    def test_history_without_live_row_is_rejected(self):
        connection = Connection()
        connection.seed(1, "superseded")
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, partition(2))

    def test_live_revision_not_highest_present_is_rejected(self):
        connection = Connection()
        connection.seed(1)
        connection.seed(2, "superseded")
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, partition(3))

    def test_successor_retry_is_idempotent_and_keeps_one_live_row(self):
        connection = Connection()
        first = self.admit(connection, partition(1))
        successor = self.admit(connection, partition(2))
        retry = self.admit(connection, partition(2))
        self.assertEqual(retry.partition_id, successor.partition_id)
        self.assertNotEqual(first.partition_id, successor.partition_id)
        self.assertEqual(len(connection.rows), 2)
        self.assertEqual([row[1] for row in connection.rows], ["superseded", "closed"])

    def test_exact_closed_target_conflicting_evidence_fails_closed(self):
        connection = Connection()
        self.admit(connection, partition(1))
        conflicting = partition(1)
        conflicting["sha256"] = "f" * 64
        with self.assertRaises(CatalogPublicationConflict):
            self.admit(connection, conflicting)

    def test_existing_non_closed_target_is_not_reinterpreted_as_retry(self):
        for state in ("valid", "degraded", "invalid", "superseded"):
            with self.subTest(state=state):
                connection = Connection()
                connection.seed(1, state)
                with self.assertRaises(CatalogPublicationConflict):
                    self.admit(connection, partition(1))

    def test_failed_successor_insert_rolls_back_predecessor_lifecycle(self):
        connection = Connection()
        self.admit(connection, partition(1))
        connection.fail_successor_insert = True
        with self.assertRaises(RuntimeError):
            CatalogPublicationWriter(connection).seal_partition(
                dataset=DATASET, partition=partition(2),
                coverage_start=START, coverage_end=END, storage_root_id="hot",
            )
        connection.rollback()
        self.assertEqual(connection.rows[0][1], "closed")
        self.assertEqual(len(connection.rows), 1)

    def test_quality_evidence_is_revision_local(self):
        connection = Connection()
        first = self.admit(connection, partition(1))
        old_quality = CatalogPublicationWriter(connection).record_quality_report(
            partition_id=first.partition_id, check_suite="suite-v1", status="pass",
            metrics={"revision": 1}, violations=[], code_ref="certifier-v1",
        )
        connection.commit()
        second = self.admit(connection, partition(2))
        self.assertNotEqual(second.partition_id, old_quality.partition_id)
        self.assertEqual(connection.quality_reports[0][5], first.partition_id)
        self.assertNotIn(second.partition_id, [item[5] for item in connection.quality_reports])

    def test_valid_and_degraded_predecessors_can_be_superseded(self):
        for state in ("valid", "degraded"):
            with self.subTest(state=state):
                connection = Connection()
                connection.seed(1, state)
                successor = self.admit(connection, partition(2))
                self.assertEqual(successor.state, "closed")
                self.assertEqual([row[1] for row in connection.rows], ["superseded", "closed"])

    def test_successor_selection_query_has_no_temporal_predicate(self):
        connection = Connection()
        self.admit(connection, partition(1))
        topology_queries = [
            statement for statement, _ in connection.statements
            if "FROM catalog.partitions" in statement and "ORDER BY revision" in statement
        ]
        self.assertTrue(topology_queries)
        for query in topology_queries:
            where = query.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
            self.assertNotIn("ts_start", where)
            self.assertNotIn("ts_end", where)


if __name__ == "__main__":
    unittest.main()
