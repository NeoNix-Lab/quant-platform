#!/usr/bin/env python3
"""Focused executable proof for A16 quality lifecycle v1."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.publication_eligibility_catalog import (  # noqa: E402
    PublicationEligibilityRefusal,
    _eligible_state,
    _select_authoritative_report,
)
from quant_platform.data.quality_lifecycle import (  # noqa: E402
    SUPERSEDES_ASSESSMENT_SIGNATURE,
    QualityLifecycleCatalog,
    QualityLifecycleRefusal,
    SelectedQualityAssessment,
    lifecycle_state_for_status,
    select_current_quality_assessment,
    semantic_assessment_signature,
)
from quant_platform.data.quality_lifecycle import _validate_supersession_graph  # noqa: E402
from test_publication_eligibility_bridge_v1 import _partition, _report  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1")
DATASET = {
    "schema_version": "dataset-manifest-v1",
    **IDENTITY.stable_dict(),
    "rel_root": "canonical/trades/genericvenue/BTC-USD/trade-v1",
    "derived_from": [DatasetIdentity("raw", "trades", "genericvenue", "BTC-USD", "trade-v1").stable_dict()],
    "transform": "canonicalize-trades-v1",
}
DATASET_HASH = "dataset-hash"
PARTITION_HASH = "partition-hash"
COVERAGE_IDS = ["coverage-1"]
ASSERTION_IDS = ["assertion-1"]
COVERAGE_HASHES = ["coverage-hash"]
START = Instant.parse("2024-01-15T00:00:00Z")
END = Instant.parse("2024-01-16T00:00:00Z")


def target_row(partition: dict, *, state: str = "closed", revision: int | None = None, partition_id: str = "partition-id"):
    return (
        partition_id,
        "dataset-id",
        partition["partition_key"],
        partition["revision"] if revision is None else revision,
        state,
        "hot",
        partition["rel_path"],
        START.to_datetime(),
        END.to_datetime(),
        partition["row_count"],
        partition["file_size_bytes"],
        partition["sha256"],
        PARTITION_HASH,
        Instant.parse(partition["created_at"]).to_datetime(),
        Instant.parse(partition["closed_at"]).to_datetime(),
        None,
        None,
        partition["producer"],
        partition["code_ref"],
    )


def report(status="pass", *, metrics_patch=None, categories=None, violations=None, code_ref="certifier-commit"):
    row, metrics = _report(
        IDENTITY,
        status=status,
        dataset_hash=DATASET_HASH,
        partition_hash=PARTITION_HASH,
        coverage_hashes=COVERAGE_HASHES,
        categories=categories,
        violations=violations,
        code_ref=code_ref,
    )
    if metrics_patch:
        metrics = {**metrics, **metrics_patch}
        row = (row[0], row[1], metrics, row[3], row[4], row[5])
    return row


def warn_report(*, metrics_patch=None):
    return report(
        "warn",
        metrics_patch=metrics_patch,
        categories={"source": "warn", "canonical": "pass", "physical": "pass", "manifests": "pass", "coverage": "pass"},
        violations=[{"category": "source", "message": "weak source evidence"}],
    )


def fail_report(*, metrics_patch=None):
    return report(
        "fail",
        metrics_patch=metrics_patch,
        categories={name: "fail" for name in ("source", "canonical", "physical", "manifests", "coverage")},
        violations=[{"category": "physical", "message": "bad artifact"}],
    )


def signature(row) -> str:
    return semantic_assessment_signature("generic-suite", row[1], row[2], row[3], row[4])


def superseding(row, predecessor_signature: str):
    metrics = {**row[2], SUPERSEDES_ASSESSMENT_SIGNATURE: predecessor_signature}
    return (row[0] + "-successor", row[1], metrics, row[3], row[4], row[5])


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.result = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, params=None) -> None:
        sql = " ".join(statement.split())
        self.connection.statements.append(sql)
        self.result = []
        if "FROM catalog.datasets" in sql:
            self.result = [self.connection.dataset]
        elif "FROM catalog.partitions" in sql:
            self.result = sorted(self.connection.topology, key=lambda row: row[3])
        elif "FROM catalog.quality_reports" in sql:
            self.result = list(self.connection.reports)
        elif sql.startswith("UPDATE catalog.partitions"):
            desired, partition_id, prior = params
            for index, row in enumerate(self.connection.topology):
                if row[0] == partition_id and row[4] == prior and row[4] in {"closed", "valid", "degraded", "invalid"}:
                    self.connection.topology[index] = (*row[:4], desired, *row[5:])
                    self.result = [(partition_id,)]
                    return

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class Connection:
    def __init__(self, topology, reports) -> None:
        self.dataset = (
            "dataset-id",
            IDENTITY.layer,
            IDENTITY.dataset_kind,
            IDENTITY.venue,
            IDENTITY.instrument,
            IDENTITY.record_schema_id,
            None,
            DATASET["rel_root"],
            DATASET_HASH,
        )
        self.topology = list(topology)
        self.reports = list(reports)
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self._snapshot = None

    def cursor(self) -> Cursor:
        if self._snapshot is None:
            self._snapshot = deepcopy(self.topology)
        return Cursor(self)

    def commit(self) -> None:
        self.commits += 1
        self._snapshot = None

    def rollback(self) -> None:
        self.rollbacks += 1
        if self._snapshot is not None:
            self.topology = self._snapshot
        self._snapshot = None


class QualityLifecycleTests(unittest.TestCase):
    def select(self, reports, *, partition=None, target=None):
        part = partition or _partition(IDENTITY)
        return select_current_quality_assessment(
            reports,
            expected_profile="generic-profile",
            expected_check_suite="generic-suite",
            identity=IDENTITY,
            partition=part,
            dataset_sha256=DATASET_HASH,
            partition_sha256=PARTITION_HASH,
            coverage_ids=COVERAGE_IDS,
            assertion_ids=ASSERTION_IDS,
            coverage_sha256=COVERAGE_HASHES,
            target=target or target_row(part),
        )

    def apply(self, state: str, reports, *, topology=None):
        part = _partition(IDENTITY)
        connection = Connection(topology or [target_row(part, state=state)], reports)
        result = QualityLifecycleCatalog(connection).apply_partition_lifecycle(
            dataset=DATASET,
            dataset_sha256=DATASET_HASH,
            partition=part,
            partition_sha256=PARTITION_HASH,
            coverage_start=START,
            coverage_end=END,
            coverage_ids=COVERAGE_IDS,
            assertion_ids=ASSERTION_IDS,
            coverage_sha256=COVERAGE_HASHES,
            storage_root_id="hot",
            expected_profile="generic-profile",
            expected_check_suite="generic-suite",
            lifecycle_code_ref="a16-test-code",
        )
        return connection, result

    def test_closed_pass_warn_fail_map_to_valid_degraded_invalid(self):
        for status_report, expected in ((report("pass"), "valid"), (warn_report(), "degraded"), (fail_report(), "invalid")):
            with self.subTest(expected=expected):
                connection, result = self.apply("closed", [status_report])
                self.assertEqual(result.prior_state, "closed")
                self.assertEqual(result.resulting_state, expected)
                self.assertEqual(connection.topology[0][4], expected)

    def test_s14_still_refuses_fail_publication_eligibility(self):
        part = _partition(IDENTITY)
        selected = _select_authoritative_report(
            [fail_report()],
            expected_profile="generic-profile",
            expected_check_suite="generic-suite",
            identity=IDENTITY,
            partition=part,
            dataset_sha256=DATASET_HASH,
            partition_sha256=PARTITION_HASH,
            coverage_ids=COVERAGE_IDS,
            assertion_ids=ASSERTION_IDS,
            coverage_sha256=COVERAGE_HASHES,
            target=target_row(part),
        )
        self.assertEqual(selected.status, "fail")
        with self.assertRaises(PublicationEligibilityRefusal):
            _eligible_state(selected)

    def test_explicit_superseding_reassessment_revises_existing_quality_state(self):
        root = report("pass")
        successor = superseding(fail_report(), signature(root))
        connection, result = self.apply("valid", [root, successor])
        self.assertEqual(result.assessment_status, "fail")
        self.assertEqual(result.resulting_state, "invalid")
        self.assertEqual(connection.topology[0][4], "invalid")

        root_warn = warn_report()
        successor_pass = superseding(report("pass"), signature(root_warn))
        _, result = self.apply("degraded", [root_warn, successor_pass])
        self.assertEqual(result.resulting_state, "valid")

        root_fail = fail_report()
        successor_warn = superseding(warn_report(), signature(root_fail))
        _, result = self.apply("invalid", [root_fail, successor_warn])
        self.assertEqual(result.resulting_state, "degraded")

    def test_equivalent_duplicates_are_idempotent_and_same_state_application_is_idempotent(self):
        first = report("pass")
        duplicate = ("other-report-id", first[1], first[2], first[3], first[4], "2099-01-01T00:00:00Z")
        selected = self.select([first, duplicate])
        self.assertEqual(selected.signature, signature(first))
        connection, result = self.apply("valid", [first, duplicate])
        self.assertFalse(result.mutated)
        self.assertFalse(any(statement.startswith("UPDATE catalog.partitions") for statement in connection.statements))

    def test_two_distinct_unsuperseded_leaves_are_ambiguous(self):
        with self.assertRaises(QualityLifecycleRefusal):
            self.select([report("pass"), report("pass", code_ref="other-certifier")])

    def test_unresolved_supersession_refuses(self):
        with self.assertRaises(QualityLifecycleRefusal):
            self.select([superseding(report("pass"), "missing-signature")])

    def test_self_and_cyclic_supersession_validation_refuses(self):
        assessment_a = SelectedQualityAssessment("a", "pass", "code", "profile", "suite", "a", {}, [])
        with self.assertRaises(QualityLifecycleRefusal):
            _validate_supersession_graph({"a": assessment_a})
        assessment_b = SelectedQualityAssessment("b", "warn", "code", "profile", "suite", "a", {}, [])
        assessment_a_to_b = SelectedQualityAssessment("a", "pass", "code", "profile", "suite", "b", {}, [])
        with self.assertRaises(QualityLifecycleRefusal):
            _validate_supersession_graph({"a": assessment_a_to_b, "b": assessment_b})

    def test_stale_report_is_unavailable_and_does_not_mutate(self):
        stale = report("pass", metrics_patch={"partition_manifest_sha256": "old"})
        part = _partition(IDENTITY)
        connection = Connection([target_row(part, state="closed")], [stale])
        with self.assertRaises(QualityLifecycleRefusal):
            QualityLifecycleCatalog(connection).apply_partition_lifecycle(
                dataset=DATASET, dataset_sha256=DATASET_HASH, partition=part,
                partition_sha256=PARTITION_HASH, coverage_start=START, coverage_end=END,
                coverage_ids=COVERAGE_IDS, assertion_ids=ASSERTION_IDS,
                coverage_sha256=COVERAGE_HASHES, storage_root_id="hot",
                expected_profile="generic-profile", expected_check_suite="generic-suite",
                lifecycle_code_ref="a16-test-code",
            )
        self.assertEqual(connection.topology[0][4], "closed")

    def test_writing_superseded_and_non_live_revisions_refuse(self):
        with self.assertRaises(QualityLifecycleRefusal):
            self.apply("writing", [report("pass")])
        part = _partition(IDENTITY)
        old = target_row(part, state="superseded", revision=1, partition_id="old")
        newer_partition = {**part, "revision": 2}
        current = target_row(newer_partition, state="closed", revision=2, partition_id="new")
        with self.assertRaises(QualityLifecycleRefusal):
            self.apply("superseded", [report("pass")], topology=[old, current])
        non_live = (*target_row(part, state="closed", revision=1, partition_id="old")[:4], "closed", *target_row(part, state="closed")[5:])
        with self.assertRaises(QualityLifecycleRefusal):
            self.apply("closed", [report("pass")], topology=[non_live, current])

    def test_dataset_level_reports_do_not_mutate_partition_lifecycle(self):
        part = _partition(IDENTITY)
        connection = Connection([target_row(part, state="closed")], [])
        with self.assertRaises(QualityLifecycleRefusal):
            QualityLifecycleCatalog(connection).apply_partition_lifecycle(
                dataset=DATASET, dataset_sha256=DATASET_HASH, partition=part,
                partition_sha256=PARTITION_HASH, coverage_start=START, coverage_end=END,
                coverage_ids=COVERAGE_IDS, assertion_ids=ASSERTION_IDS,
                coverage_sha256=COVERAGE_HASHES, storage_root_id="hot",
                expected_profile="generic-profile", expected_check_suite="generic-suite",
                lifecycle_code_ref="a16-test-code",
            )
        self.assertEqual(connection.topology[0][4], "closed")

    def test_lifecycle_result_retains_decision_provenance(self):
        _, result = self.apply("closed", [warn_report()])
        self.assertEqual(result.selected_assessment_scope, {"certification_profile": "generic-profile", "check_suite": "generic-suite"})
        self.assertEqual(result.assessment_status, "warn")
        self.assertEqual(result.bound_evidence["partition_content_sha256"], "0" * 64)
        self.assertEqual(result.lifecycle_code_ref, "a16-test-code")

    def test_status_mapping_is_exact(self):
        self.assertEqual(lifecycle_state_for_status("pass"), "valid")
        self.assertEqual(lifecycle_state_for_status("warn"), "degraded")
        self.assertEqual(lifecycle_state_for_status("fail"), "invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
