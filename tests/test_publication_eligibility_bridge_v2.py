"""S14 v2 topology tests without a real PostgreSQL or Human E2E run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest  # noqa: E402
from quant_platform.data.publication_eligibility_catalog import (  # noqa: E402
    PublicationEligibilityCatalog,
    PublicationEligibilityRefusal,
)
from test_publication_eligibility_bridge_v1 import (  # noqa: E402
    OrderedConnection,
    OrderedCursor,
    _partition,
    _report,
)


TRANSFORM = "canonicalize-trades-v1"
RAW = DatasetIdentity("raw", "trades", "genericvenue", "BTC-USD", "trade-v1")
CANONICAL = DatasetIdentity("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1")


def _persist_json(path: Path, value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _target(partition: dict, partition_hash: str, dataset_id: str = "child"):
    return (
        "partition-id", dataset_id, partition["partition_key"], partition["revision"],
        "closed", "hot", partition["rel_path"],
        Instant.parse("2024-01-15T00:00:00Z").to_datetime(),
        Instant.parse("2024-01-16T00:00:00Z").to_datetime(),
        partition["row_count"], partition["file_size_bytes"], partition["sha256"],
        partition_hash,
        Instant.parse(partition["created_at"]).to_datetime(),
        Instant.parse(partition["closed_at"]).to_datetime(),
        None, None, partition["producer"], partition["code_ref"],
    )


def _durable_evidence(root: Path, *, dataset_derived: bool = False):
    parents = [RAW.stable_dict()] if dataset_derived else None
    dataset = emit_dataset_manifest(
        root / "dataset.json",
        dataset_identity=CANONICAL,
        created_at="2026-09-04T10:00:00Z",
        schema_version="dataset-manifest-v2",
        origin="dataset_derived" if dataset_derived else "source_acquired",
        derived_from=parents,
        transform=TRANSFORM,
    )
    partition = _partition(CANONICAL)
    partition_hash = _persist_json(root / "partition.json", partition)
    coverage = emit_coverage_manifest(
        root / "coverage.json",
        dataset_identity=CANONICAL,
        source_dataset_identity=CANONICAL,
        coverage_id="coverage-1",
        supersedes=None,
        created_at="2026-09-04T10:00:02Z",
        acquisition={
            "basis": "source_extract",
            "intent_start": "2024-01-15T00:00:00Z",
            "intent_end": "2024-01-16T00:00:00Z",
            "source_semantics": "generic-source-v1",
            "mapping": "generic-mapping-v1",
        },
        assertions=[{
            "assertion_id": "assertion-1",
            "start": "2024-01-15T00:00:00Z",
            "end": "2024-01-16T00:00:00Z",
            "status": "complete",
            "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
            "evidence": [{"kind": "deterministic_source_extract", "detail": "generic"}],
        }],
        producer="generic-source",
        code_ref="source-commit",
        partition_manifests=[partition],
    )
    return dataset, partition, partition_hash, coverage


def _publish_connection(dataset, partition, partition_hash, coverage, *, dataset_derived=False):
    child = (
        "child", "canonical", "trades", "genericvenue", "BTC-USD", "trade-v1", None,
        dataset.document["rel_root"], dataset.manifest_sha256,
    )
    datasets = {("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1"): child}
    if dataset_derived:
        parent = (
            "parent", "raw", "trades", "genericvenue", "BTC-USD", "trade-v1", None,
            RAW.stable_dict()["layer"] + "/trades/genericvenue/BTC-USD/trade-v1", "p" * 64,
        )
        datasets[("raw", "trades", "genericvenue", "BTC-USD", "trade-v1")] = parent
    report, _ = _report(
        CANONICAL,
        dataset_hash=dataset.manifest_sha256,
        partition_hash=partition_hash,
        coverage_hashes=[coverage.manifest_sha256],
    )
    return OrderedConnection(datasets, [_target(partition, partition_hash)], [report])


class PublicationEligibilityV2Tests(unittest.TestCase):
    def test_source_acquired_publishes_with_exactly_zero_lineage(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            dataset, partition, partition_hash, coverage = _durable_evidence(root)
            connection = _publish_connection(dataset, partition, partition_hash, coverage)
            result = PublicationEligibilityCatalog(connection).publish(
                dataset=dataset.document,
                dataset_sha256=dataset.manifest_sha256,
                partition=partition,
                partition_sha256=partition_hash,
                coverage_start=Instant.parse("2024-01-15T00:00:00Z"),
                coverage_end=Instant.parse("2024-01-16T00:00:00Z"),
                coverage_ids=["coverage-1"],
                assertion_ids=["assertion-1"],
                coverage_sha256=[coverage.manifest_sha256],
                storage_root_id="hot",
                expected_profile="generic-profile",
                expected_check_suite="generic-suite",
            )
        self.assertEqual(result.state, "valid")
        self.assertEqual(connection.lineage, set())
        self.assertNotIn("INSERT INTO catalog.dataset_lineage", connection.statements)

    def test_dataset_derived_writes_declared_lineage_and_phase5_rechecks_it(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            dataset, partition, partition_hash, coverage = _durable_evidence(root, dataset_derived=True)
            connection = _publish_connection(dataset, partition, partition_hash, coverage, dataset_derived=True)
            result = PublicationEligibilityCatalog(connection).publish(
                dataset=dataset.document,
                dataset_sha256=dataset.manifest_sha256,
                partition=partition,
                partition_sha256=partition_hash,
                coverage_start=Instant.parse("2024-01-15T00:00:00Z"),
                coverage_end=Instant.parse("2024-01-16T00:00:00Z"),
                coverage_ids=["coverage-1"],
                assertion_ids=["assertion-1"],
                coverage_sha256=[coverage.manifest_sha256],
                storage_root_id="hot",
                expected_profile="generic-profile",
                expected_check_suite="generic-suite",
            )
        self.assertEqual(result.state, "valid")
        self.assertEqual(connection.lineage, {("parent", TRANSFORM)})

    def test_source_acquired_preexisting_edge_refuses_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            dataset, partition, partition_hash, coverage = _durable_evidence(root)
            connection = _publish_connection(dataset, partition, partition_hash, coverage)
            connection.lineage.add(("stale-parent", TRANSFORM))
            with self.assertRaises(PublicationEligibilityRefusal):
                self._publish(connection, dataset, partition, partition_hash, coverage)
        self.assertEqual(connection.lineage, {("stale-parent", TRANSFORM)})

    def test_source_acquired_concurrent_edge_visible_at_lineage_read_refuses(self):
        class InjectingCursor(OrderedCursor):
            def execute(self, statement, params=None):
                if "SELECT parent_id::text" in " ".join(statement.split()):
                    self.connection.lineage.add(("concurrent-parent", TRANSFORM))
                super().execute(statement, params)

        class InjectingConnection(OrderedConnection):
            def cursor(self):
                return InjectingCursor(self)

        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            dataset, partition, partition_hash, coverage = _durable_evidence(root)
            base = _publish_connection(dataset, partition, partition_hash, coverage)
            connection = InjectingConnection(base.datasets, base.topology, base.reports)
            with self.assertRaises(PublicationEligibilityRefusal):
                self._publish(connection, dataset, partition, partition_hash, coverage)
        self.assertEqual(connection.lineage, {("concurrent-parent", TRANSFORM)})

    def test_invalid_v2_parent_topologies_refuse(self):
        cursor = OrderedConnection({}, [], []).cursor()
        source = {
            "schema_version": "dataset-manifest-v2",
            **CANONICAL.stable_dict(),
            "origin": "source_acquired",
            "derived_from": [],
            "transform": TRANSFORM,
        }
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityCatalog._resolve_parents(cursor, source, CANONICAL)

        unsupported = {**source, "schema_version": "dataset-manifest-v9", "derived_from": None}
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityCatalog._resolve_parents(cursor, unsupported, CANONICAL)

        features = DatasetIdentity(
            "features", "trade_microstructure", "genericvenue", "BTC-USD", "trade-v1",
            "microstructure", 1,
        )
        feature_source = {
            "schema_version": "dataset-manifest-v2",
            **features.stable_dict(),
            "origin": "source_acquired",
            "transform": "derive-features-v1",
        }
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityCatalog._resolve_parents(cursor, feature_source, features)

    def test_missing_catalog_parent_refuses(self):
        class MissingParentCursor(OrderedCursor):
            def execute(self, statement, params=None):
                sql = " ".join(statement.split())
                if "FROM catalog.datasets" in sql and "FOR UPDATE" not in sql:
                    self.result = []
                    return
                super().execute(statement, params)

        cursor = MissingParentCursor(OrderedConnection({}, [], []))
        document = {
            "schema_version": "dataset-manifest-v2",
            **CANONICAL.stable_dict(),
            "origin": "dataset_derived",
            "derived_from": [RAW.stable_dict()],
            "transform": TRANSFORM,
        }
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityCatalog._resolve_parents(cursor, document, CANONICAL)

    def test_generic_manifest_and_s14_code_has_no_provider_branch(self):
        generic_paths = (
            ROOT / "src" / "quant_platform" / "data" / "manifests.py",
            ROOT / "src" / "quant_platform" / "data" / "publication_eligibility_catalog.py",
        )
        forbidden = ("if venue", "if sqlite", "if legacy_source")
        for path in generic_paths:
            source = path.read_text(encoding="utf-8")
            for branch in forbidden:
                with self.subTest(path=path, branch=branch):
                    self.assertNotIn(branch, source)

    @staticmethod
    def _publish(connection, dataset, partition, partition_hash, coverage):
        return PublicationEligibilityCatalog(connection).publish(
            dataset=dataset.document,
            dataset_sha256=dataset.manifest_sha256,
            partition=partition,
            partition_sha256=partition_hash,
            coverage_start=Instant.parse("2024-01-15T00:00:00Z"),
            coverage_end=Instant.parse("2024-01-16T00:00:00Z"),
            coverage_ids=["coverage-1"],
            assertion_ids=["assertion-1"],
            coverage_sha256=[coverage.manifest_sha256],
            storage_root_id="hot",
            expected_profile="generic-profile",
            expected_check_suite="generic-suite",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
