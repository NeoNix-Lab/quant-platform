#!/usr/bin/env python3
"""K08 backup/restore v1: capture, export, isolated restore and read-back proof.

CREDITs S13 seal semantics (``tests/test_publication_certification_v1.py``),
DataGateway semantics (``tests/test_data_gateway.py``) and K06 protection
identity discipline (``tests/test_operations_protection_v1.py``) rather than
duplicating them.  This file proves only the K08-specific propositions:
deterministic recovery-set capture from an already-sealed publication, pure
backup export, isolated-target restore refusal/success, restored-catalog
resolution, and equivalent historical DataGateway reads from the restored
target.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.backup_restore import (  # noqa: E402
    capture_recovery_set,
    export_recovery_set,
    restore_recovery_set,
)
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import CatalogDataset, CatalogPartition, DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant, NaturalPartitionIdentity, TradeRecord  # noqa: E402
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest, emit_partition_manifest  # noqa: E402
from quant_platform.data.publication import SealedCatalogPartition, SealedPartitionEvidence  # noqa: E402
from quant_platform.operations.recovery import RecoveryError  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    materialize_bybit_trade_v1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"


def trade(timestamp: str, trade_id: str) -> TradeRecord:
    return TradeRecord(IDENTITY.venue, IDENTITY.instrument, Instant.parse(timestamp), "100.00", "0.5000", "buy", None, trade_id, None)


class FakeCatalogWriter:
    """Mirrors test_publication_certification_v1.FakeCatalogWriter, K08-trimmed."""

    def __init__(self):
        self.partitions: list[SealedCatalogPartition] = []
        self.commits = 0
        self.rollbacks = 0

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id):
        identity = DatasetIdentity(
            dataset["layer"], dataset["dataset_kind"], dataset["venue"], dataset["instrument"], dataset["record_schema_id"],
        )
        existing = next(
            (item for item in self.partitions
             if item.natural_identity.partition_key == partition["partition_key"]
             and item.natural_identity.revision == partition["revision"]),
            None,
        )
        if existing is not None:
            return existing
        live = [item for item in self.partitions if item.state != "superseded"]
        if not live and partition["revision"] != 1:
            raise RuntimeError("first admitted revision must be 1")
        admitted = SealedCatalogPartition(
            f"partition-uuid-{len(self.partitions) + 1}", "dataset-uuid-1",
            NaturalPartitionIdentity(identity, partition["partition_key"], partition["revision"]),
            "closed", coverage_start, coverage_end,
            partition["row_count"], partition["file_size_bytes"],
            partition["sha256"], partition["_manifest_sha256"],
            partition["producer"], partition["code_ref"],
        )
        self.partitions.append(admitted)
        return admitted

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCatalog:
    """Mirrors test_data_gateway.FakeCatalog: a Catalog-duck-typed resolver."""

    def __init__(self, dataset: CatalogDataset, partitions: list[CatalogPartition]):
        self.dataset = dataset
        self.partitions = partitions
        self.last_candidate_count = 0

    def resolve_dataset(self, identity):
        return self.dataset

    def select_partitions(self, dataset, start, end, states):
        selected = [
            item for item in self.partitions
            if item.state in states and item.coverage is not None
            and item.ts_end > start and item.ts_start < end
        ]
        self.last_candidate_count = len(selected)
        return selected


def historical_request() -> DataRequest:
    return DataRequest(
        IDENTITY, START, END,
        lifecycle_policy=LifecyclePolicy.VALID_AND_CLOSED,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


class BackupRestoreV1Tests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "primary"
        self.root.mkdir()
        # Physical artifact layout mirrors a real dataset root: <root>/<rel_root>/<partition_key>/...
        self.dataset_root = self.root / REL_ROOT
        self.data_path = self.dataset_root / "dt=2024-01-15" / "part-000.parquet"
        self.dataset_path = self.root / "dataset.json"
        self.partition_path = self.root / "partition.json"
        self.coverage_path = self.root / "coverage.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def sealed_evidence(self, records=None, *, revision=1, writer=None):
        records = records if records is not None else [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")]
        materialization = materialize_bybit_trade_v1(self.data_path, records, dataset_identity=IDENTITY)
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=IDENTITY, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=IDENTITY, dataset_root=self.dataset_root,
            partition_key="dt=2024-01-15", revision=revision, rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="test-materializer", code_ref="producer-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
            coverage_id=f"coverage-{revision}", supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": START, "intent_end": END,
                "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": f"assertion-{revision}", "start": START, "end": END, "status": "complete",
                "partitions": [{"partition_key": "dt=2024-01-15", "revision": revision}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": "sqlite extract complete"}],
            }], producer="test-source", code_ref="source-ref",
            partition_manifests=[json.loads(self.partition_path.read_text())],
        )
        evidence = SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,), self.data_path, "hot",
        )
        writer = writer or FakeCatalogWriter()
        partition_document = json.loads(self.partition_path.read_text())
        partition_document["_manifest_sha256"] = hashlib.sha256(self.partition_path.read_bytes()).hexdigest()
        sealed = writer.seal_partition(
            dataset=json.loads(self.dataset_path.read_text()),
            partition=partition_document,
            coverage_start=Instant.parse(START), coverage_end=Instant.parse(END), storage_root_id="hot",
        )
        return evidence, sealed

    def test_capture_is_deterministic_for_the_same_finalized_publication(self):
        evidence, sealed = self.sealed_evidence()
        first = capture_recovery_set(evidence, sealed)
        second = capture_recovery_set(evidence, sealed)
        self.assertEqual(first.recovery_identity, second.recovery_identity)
        self.assertEqual(first.natural_identity, sealed.natural_identity)

    def test_capture_refuses_non_closed_publication(self):
        evidence, sealed = self.sealed_evidence()
        writing = replace(sealed, state="writing")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, writing)

    def test_capture_refuses_manifest_that_does_not_match_the_sealed_generation(self):
        evidence, sealed = self.sealed_evidence()
        # Tamper the durable partition manifest after sealing: this is exactly
        # a mixed/incompatible generation -- the file no longer matches what
        # was actually admitted to the catalog.
        self.partition_path.write_text(self.partition_path.read_text() + " ")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_refuses_tampered_physical_artifact(self):
        evidence, sealed = self.sealed_evidence()
        self.data_path.write_bytes(self.data_path.read_bytes() + b"corrupt")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_export_is_pure_copy_and_does_not_mutate_primary(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        original_bytes = self.data_path.read_bytes()
        original_manifest = self.partition_path.read_bytes()
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root)
        self.assertEqual(self.data_path.read_bytes(), original_bytes)
        self.assertEqual(self.partition_path.read_bytes(), original_manifest)
        self.assertTrue(export.artifact_path.is_file())
        self.assertEqual(export.artifact_path.read_bytes(), original_bytes)
        self.assertNotEqual(export.artifact_path, evidence.artifact_path)

    def test_export_refuses_when_primary_evidence_changed_since_capture(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        self.data_path.write_bytes(self.data_path.read_bytes() + b"drift")
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root)

    def _export(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root)
        return evidence, sealed, export

    def test_restore_refuses_non_empty_target(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        target.mkdir()
        (target / "stray.txt").write_text("pre-existing")
        with self.assertRaises(RecoveryError):
            restore_recovery_set(
                export, target, forbidden_roots=[self.root, export.destination_root],
                catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
            )

    def test_restore_refuses_target_aliasing_primary_or_backup(self):
        evidence, sealed, export = self._export()
        for alias in (self.root, export.destination_root, self.root / "dt=2024-01-15"):
            with self.subTest(alias=alias):
                with self.assertRaises(RecoveryError):
                    restore_recovery_set(
                        export, alias, forbidden_roots=[self.root, export.destination_root],
                        catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
                    )

    def test_restore_refuses_missing_backup_member(self):
        evidence, sealed, export = self._export()
        export.artifact_path.unlink()
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            restore_recovery_set(
                export, target, forbidden_roots=[self.root, export.destination_root],
                catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
            )

    def test_restore_refuses_corrupt_backup_member(self):
        evidence, sealed, export = self._export()
        export.artifact_path.write_bytes(export.artifact_path.read_bytes() + b"tamper")
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            restore_recovery_set(
                export, target, forbidden_roots=[self.root, export.destination_root],
                catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
            )

    def test_isolated_restore_reproduces_identities_coverage_and_digests(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        restored = restore_recovery_set(
            export, target, forbidden_roots=[self.root, export.destination_root],
            catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
        )
        recovery_set = export.recovery_set
        self.assertEqual(restored.sealed.natural_identity, recovery_set.natural_identity)
        self.assertEqual(restored.sealed.natural_identity, sealed.natural_identity)
        self.assertEqual(restored.sealed.content_sha256, recovery_set.physical_content_sha256)
        self.assertEqual(restored.sealed.manifest_sha256, recovery_set.partition_manifest_sha256)
        self.assertEqual(restored.sealed.ts_start, recovery_set.declared_coverage.start)
        self.assertEqual(restored.sealed.ts_end, recovery_set.declared_coverage.end)
        self.assertEqual(restored.artifact_path.read_bytes(), self.data_path.read_bytes())

    def test_restored_catalog_resolves_and_historical_gateway_reads_equivalent_records(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        restored = restore_recovery_set(
            export, target, forbidden_roots=[self.root, export.destination_root],
            catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
        )

        primary_dataset = CatalogDataset(IDENTITY, sealed.dataset_id, REL_ROOT, "b" * 64, 1, "a" * 64)
        primary_partition = CatalogPartition(
            sealed.natural_identity, sealed.partition_id, "hot", str(self.root),
            REL_ROOT, "dt=2024-01-15/part-000.parquet",
            sealed.ts_start, sealed.ts_end, sealed.row_count, sealed.content_sha256, sealed.manifest_sha256,
            "closed", sealed.producer, sealed.code_ref,
        )
        primary_gateway = DataGateway(FakeCatalog(primary_dataset, [primary_partition]), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        primary_result = primary_gateway.read(historical_request())

        restored_dataset = CatalogDataset(IDENTITY, restored.sealed.dataset_id, restored.rel_root, "b" * 64, 1, "a" * 64)
        restored_partition = CatalogPartition(
            restored.sealed.natural_identity, restored.sealed.partition_id, "restored", str(target),
            restored.rel_root, restored.rel_path,
            restored.sealed.ts_start, restored.sealed.ts_end, restored.sealed.row_count,
            restored.sealed.content_sha256, restored.sealed.manifest_sha256,
            "closed", restored.sealed.producer, restored.sealed.code_ref,
        )
        restored_gateway = DataGateway(FakeCatalog(restored_dataset, [restored_partition]), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        restored_result = restored_gateway.read(historical_request())

        self.assertEqual(len(restored_result.records), 2)
        self.assertEqual([r.trade_id for r in primary_result.records], [r.trade_id for r in restored_result.records])
        self.assertEqual([r.exchange_ts for r in primary_result.records], [r.exchange_ts for r in restored_result.records])
        self.assertEqual(primary_result.metadata.result_identity, restored_result.metadata.result_identity)
        self.assertEqual(primary_result.metadata.dataset_identity, restored_result.metadata.dataset_identity)
        # The restored read used a distinct storage locator (its own
        # isolated target and "restored" storage_root_id), not the primary's.
        self.assertNotEqual(primary_partition.storage_root, restored_partition.storage_root)
        self.assertNotEqual(primary_result.metadata.storage_root_ids, restored_result.metadata.storage_root_ids)

    def test_second_revision_restored_alone_is_refused_by_unchanged_catalog_contiguity(self):
        # v1 non-goal documented on restore_recovery_set: a lone successor
        # revision cannot be admitted into a brand-new empty catalog target,
        # exactly because CertificationCatalogWriter's own contiguous-admission
        # invariant (CREDITed, unmodified) requires revision 1 to be admitted
        # first.
        writer = FakeCatalogWriter()
        self.sealed_evidence(revision=1, writer=writer)
        evidence2, sealed2 = self.sealed_evidence(revision=2, writer=writer)
        recovery_set = capture_recovery_set(evidence2, sealed2)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence2, backup_root)
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RuntimeError):
            restore_recovery_set(
                export, target, forbidden_roots=[self.root, export.destination_root],
                catalog_writer=FakeCatalogWriter(), storage_root_id="restored",
            )


if __name__ == "__main__":
    unittest.main()
