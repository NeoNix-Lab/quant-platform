#!/usr/bin/env python3
"""K08 backup/restore v1: capture, export, isolated restore and read-back proof.

CREDITs S13 seal semantics (``tests/test_publication_certification_v1.py``),
DataGateway semantics (``tests/test_data_gateway.py``) and K06 protection
identity discipline (``tests/test_operations_protection_v1.py``) rather than
duplicating them.  This file proves only the K08-specific propositions:
deterministic recovery-set capture (with semantic linkage validation) from
an already-sealed publication, a durably persisted and independently
reloadable backup export, isolated-target restore refusal/success (including
path-safety and storage-locator binding), verify-before-commit restore
admission, ordered multi-revision restore, K06 evidence binding, and
equivalent historical DataGateway reads from the restored target.
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
    load_recovery_backup_export,
    restore_recovery_set,
)
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import CatalogDataset, CatalogPartition, DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant, NaturalPartitionIdentity, TradeRecord  # noqa: E402
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest, emit_partition_manifest  # noqa: E402
from quant_platform.data.publication import SealedCatalogPartition, SealedPartitionEvidence  # noqa: E402
from quant_platform.operations.protection import (  # noqa: E402
    AcceptedReconstructionContract,
    ArtifactProtectionIdentity,
    ArtifactReadOutcome,
    ArtifactVerificationEvidence,
    ProtectionUnitIdentity,
    assess_protection,
)
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


REVISION_2_COVERAGE_START = "2024-01-16T00:00:00Z"
REVISION_2_COVERAGE_END = "2024-01-17T00:00:00Z"
REVISION_2_RECORDS = [trade("2024-01-16T00:00:01Z", "1"), trade("2024-01-16T00:00:02Z", "2")]


def protected_assessment():
    artifact_id = ArtifactProtectionIdentity(role="extract", content_hash_sha256="e" * 64, size_bytes=10)
    accepted = AcceptedReconstructionContract(
        source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1",
        reconstruction_contract_id="trade-v1",
        required_roles=("extract",),
        accepting_authority_id="adr:k08-test-authority-v1",
        accepted_at=Instant.parse("2024-01-01T00:00:00Z"),
    )
    unit = ProtectionUnitIdentity(
        source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1",
        reconstruction_contract_id="trade-v1",
        protected_support="k08-test-support",
        artifacts=(artifact_id,),
        accepted_contract=accepted,
    )
    evidence = (ArtifactVerificationEvidence(
        role="extract", instance_scope="hot", outcome=ArtifactReadOutcome.READ,
        observed_content_hash_sha256="e" * 64, observed_size_bytes=10,
    ),)
    return assess_protection(
        unit, evidence, verified_at=Instant.parse("2024-01-15T00:00:00Z"),
        verifier_identity="k08-test-verifier",
    )


class FakeCatalogWriter:
    """Mirrors test_publication_certification_v1.FakeCatalogWriter, K08-trimmed.

    Enforces the same contiguous-revision-admission invariant the real
    ``CatalogPublicationWriter`` does (first admitted revision must be 1;
    successors must be contiguous), so tests exercising K08's own
    multi-revision restore logic against this fake are representative of the
    real write contract, not a looser stand-in for it.
    """

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
        if not live:
            if partition["revision"] != 1:
                raise RuntimeError("first admitted revision must be 1")
        elif partition["revision"] != live[0].natural_identity.revision + 1:
            raise RuntimeError("revision is not a contiguous successor")
        if live:
            self.partitions[self.partitions.index(live[0])] = replace(live[0], state="superseded")
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


class RiggedCatalogWriter(FakeCatalogWriter):
    """Returns a deliberately wrong SealedCatalogPartition to prove restore
    verifies the returned admission before committing it."""

    def seal_partition(self, **kwargs):
        sealed = super().seal_partition(**kwargs)
        return replace(sealed, content_sha256="f" * 64)


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

    def sealed_evidence(self, records=None, *, revision=1, writer=None, identity=IDENTITY,
                         coverage_start=START, coverage_end=END, partition_key="dt=2024-01-15"):
        if records is None:
            records = [
                TradeRecord(identity.venue, identity.instrument, Instant.parse("2024-01-15T00:00:01Z"), "100.00", "0.5000", "buy", None, "1", None),
                TradeRecord(identity.venue, identity.instrument, Instant.parse("2024-01-15T00:00:02Z"), "100.00", "0.5000", "buy", None, "2", None),
            ]
        rel_path = f"{partition_key}/part-000.parquet"
        data_path = self.dataset_root / rel_path
        materialization = materialize_bybit_trade_v1(data_path, records, dataset_identity=identity)
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", identity.venue, identity.instrument, "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=identity, dataset_root=self.dataset_root,
            partition_key=partition_key, revision=revision, rel_path=rel_path,
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="test-materializer", code_ref="producer-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=identity, source_dataset_identity=identity,
            coverage_id=f"coverage-{revision}", supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": START, "intent_end": "2024-01-18T00:00:00Z",
                "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": f"assertion-{revision}", "start": coverage_start, "end": coverage_end, "status": "complete",
                "partitions": [{"partition_key": partition_key, "revision": revision}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": "sqlite extract complete"}],
            }], producer="test-source", code_ref="source-ref",
            partition_manifests=[json.loads(self.partition_path.read_text())],
        )
        evidence = SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,), data_path, "hot",
        )
        writer = writer or FakeCatalogWriter()
        partition_document = json.loads(self.partition_path.read_text())
        partition_document["_manifest_sha256"] = hashlib.sha256(self.partition_path.read_bytes()).hexdigest()
        sealed = writer.seal_partition(
            dataset=json.loads(self.dataset_path.read_text()),
            partition=partition_document,
            coverage_start=Instant.parse(coverage_start), coverage_end=Instant.parse(coverage_end), storage_root_id="hot",
        )
        return evidence, sealed

    def _restore(self, export, target, *, writer=None, storage_root_abs_path=None,
                 storage_root_id="restored", forbidden_roots=None, predecessor_exports=()):
        return restore_recovery_set(
            export, target,
            forbidden_roots=forbidden_roots if forbidden_roots is not None else [self.root, export.destination_root],
            catalog_writer=writer or FakeCatalogWriter(),
            storage_root_id=storage_root_id,
            storage_root_abs_path=target if storage_root_abs_path is None else storage_root_abs_path,
            predecessor_exports=predecessor_exports,
        )

    # -- capture -----------------------------------------------------------

    def test_capture_is_deterministic_for_the_same_finalized_publication(self):
        evidence, sealed = self.sealed_evidence()
        first = capture_recovery_set(evidence, sealed)
        second = capture_recovery_set(evidence, sealed)
        self.assertEqual(first.recovery_identity, second.recovery_identity)
        self.assertEqual(first.natural_identity, sealed.natural_identity)
        self.assertIsNone(first.k06_protection_identity)

    def test_capture_accepts_caller_attested_promoted_partition_state(self):
        # The durable S13 seal evidence itself is always 'closed'; a caller
        # backing up an already-promoted live partition attests the current
        # catalog-observed state explicitly, decoupled from that durable
        # manifest content.
        evidence, sealed = self.sealed_evidence()
        for state in ("valid", "degraded"):
            with self.subTest(state=state):
                recovery_set = capture_recovery_set(evidence, sealed, partition_state=state)
                self.assertEqual(recovery_set.partition_state, state)

    def test_capture_refuses_non_finalized_partition_state_override(self):
        evidence, sealed = self.sealed_evidence()
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, partition_state="writing")

    def test_capture_refuses_non_closed_publication(self):
        evidence, sealed = self.sealed_evidence()
        writing = replace(sealed, state="writing")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, writing)

    def test_capture_refuses_manifest_that_does_not_match_the_sealed_generation(self):
        evidence, sealed = self.sealed_evidence()
        self.partition_path.write_text(self.partition_path.read_text() + " ")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_refuses_tampered_physical_artifact(self):
        evidence, sealed = self.sealed_evidence()
        self.data_path.write_bytes(self.data_path.read_bytes() + b"corrupt")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_refuses_foreign_dataset_manifest(self):
        # A dataset.json for a wholly different dataset identity, sharing
        # nothing with the sealed publication except coincidental placement.
        evidence, sealed = self.sealed_evidence()
        foreign = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=foreign, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "ETHUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_refuses_unrelated_coverage_evidence(self):
        # Coverage evidence that never asserts complete coverage for the
        # sealed partition_key/revision at all -- a foreign/unrelated
        # coverage document that happens to share a dataset identity.
        evidence, sealed = self.sealed_evidence()
        unrelated = json.loads(self.coverage_path.read_text())
        unrelated["coverage_id"] = "unrelated-coverage"
        unrelated["assertions"][0]["assertion_id"] = "unrelated-assertion"
        unrelated["assertions"][0]["partitions"] = [{"partition_key": "dt=2099-01-01", "revision": 1}]
        self.coverage_path.write_text(json.dumps(unrelated))
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_refuses_coverage_that_does_not_match_sealed_interval(self):
        # Coverage evidence for the right partition but a narrower interval
        # than what was actually sealed -- mixed/incompatible generation.
        evidence, sealed = self.sealed_evidence()
        narrowed = json.loads(self.coverage_path.read_text())
        narrowed["assertions"][0]["end"] = "2024-01-15T12:00:00Z"
        self.coverage_path.write_text(json.dumps(narrowed))
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)

    def test_capture_binds_protected_k06_evidence_when_supplied(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        self.assertEqual(recovery_set.k06_protection_identity, assessment.protection_identity)

    def test_capture_refuses_non_protected_k06_evidence(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        unprotected = assess_protection(
            assessment.unit,
            (ArtifactVerificationEvidence(role="extract", instance_scope="hot", outcome=ArtifactReadOutcome.ABSENT),),
            verified_at=Instant.parse("2024-01-15T00:00:00Z"), verifier_identity="k08-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_protection=unprotected)

    # -- export / load -------------------------------------------------------

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
        self.assertTrue((export.destination_root / "recovery-manifest.json").is_file())

    def test_export_refuses_when_primary_evidence_changed_since_capture(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        self.data_path.write_bytes(self.data_path.read_bytes() + b"drift")
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root)

    def test_export_persists_k06_evidence_and_refuses_mismatched_document(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=None)
        other_assessment = protected_assessment()
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set, evidence, backup_root,
                k06_protection_document={**other_assessment.stable_dict(), "protection_identity": "wrong"},
            )
        export = export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=assessment.stable_dict())
        self.assertIsNotNone(export.k06_protection_document_path)
        self.assertTrue(export.k06_protection_document_path.is_file())

    def test_load_recovery_backup_export_round_trips_and_reverifies_from_disk(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root)

        loaded = load_recovery_backup_export(export.destination_root)
        self.assertEqual(loaded.recovery_set.recovery_identity, recovery_set.recovery_identity)
        self.assertEqual(loaded.dataset_manifest_path, export.dataset_manifest_path)
        self.assertEqual(loaded.artifact_path.read_bytes(), export.artifact_path.read_bytes())

        # A restore driven purely from the on-disk export (no surviving
        # in-memory RecoverySetV1 needed) succeeds identically.
        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(loaded, target)
        self.assertEqual(restored.sealed.natural_identity, recovery_set.natural_identity)

    def test_load_recovery_backup_export_detects_tampered_member(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root)
        export.artifact_path.write_bytes(export.artifact_path.read_bytes() + b"tamper")
        with self.assertRaises(RecoveryError):
            load_recovery_backup_export(export.destination_root)

    def test_load_recovery_backup_export_refuses_manifest_member_path_traversal(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root)
        manifest_path = export.destination_root / "recovery-manifest.json"
        document = json.loads(manifest_path.read_text())
        for malicious in ("../../escape.json", "/etc/passwd", "a/../../b"):
            with self.subTest(malicious=malicious):
                tampered = json.loads(json.dumps(document))
                tampered["members"]["dataset_manifest"] = malicious
                manifest_path.write_text(json.dumps(tampered))
                with self.assertRaises(RecoveryError):
                    load_recovery_backup_export(export.destination_root)
        manifest_path.write_text(json.dumps(document))

    # -- restore: refusals ---------------------------------------------------

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
            self._restore(export, target)

    def test_restore_refuses_target_aliasing_primary_or_backup(self):
        evidence, sealed, export = self._export()
        for alias in (self.root, export.destination_root, self.root / "dt=2024-01-15"):
            with self.subTest(alias=alias):
                with self.assertRaises(RecoveryError):
                    self._restore(export, alias)

    def test_restore_refuses_storage_root_abs_path_not_matching_target(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        other = Path(self.tempdir.name) / "not-the-target"
        with self.assertRaises(RecoveryError):
            self._restore(export, target, storage_root_abs_path=other)
        # In particular, claiming the primary's own location is refused too.
        with self.assertRaises(RecoveryError):
            self._restore(export, target, storage_root_abs_path=self.root)

    def test_restore_refuses_missing_backup_member(self):
        evidence, sealed, export = self._export()
        export.artifact_path.unlink()
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            self._restore(export, target)

    def test_restore_refuses_corrupt_backup_member(self):
        evidence, sealed, export = self._export()
        export.artifact_path.write_bytes(export.artifact_path.read_bytes() + b"tamper")
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            self._restore(export, target)

    def test_restore_refuses_path_traversal_in_manifest_locators(self):
        evidence, sealed, export = self._export()
        for field, malicious in (
            ("rel_root", "../../escape"),
            ("rel_path", "../../../escape.parquet"),
            ("rel_root", "/etc"),
        ):
            with self.subTest(field=field, malicious=malicious):
                target = Path(self.tempdir.name) / f"restored-{field}-{abs(hash(malicious))}"
                source_path = export.dataset_manifest_path if field == "rel_root" else export.partition_manifest_path
                original = source_path.read_bytes()
                try:
                    document = json.loads(original)
                    document[field] = malicious
                    source_path.write_bytes(json.dumps(document).encode("utf-8"))
                    with self.assertRaises(RecoveryError):
                        self._restore(export, target)
                    # Refused before any member was ever copied into target.
                    self.assertEqual(list(target.iterdir()) if target.exists() else [], [])
                finally:
                    source_path.write_bytes(original)

    def test_restore_refuses_before_commit_when_returned_admission_mismatches(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        writer = RiggedCatalogWriter()
        with self.assertRaises(RecoveryError):
            self._restore(export, target, writer=writer)
        self.assertEqual(writer.commits, 0)
        self.assertEqual(writer.rollbacks, 1)

    # -- restore: success ------------------------------------------------

    def test_isolated_restore_reproduces_identities_coverage_and_digests(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(export, target)
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
        restored = self._restore(export, target)

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
        self.assertNotEqual(primary_partition.storage_root, restored_partition.storage_root)
        self.assertNotEqual(primary_result.metadata.storage_root_ids, restored_result.metadata.storage_root_ids)

    def test_restore_carries_k06_protection_evidence_through(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=assessment.stable_dict())
        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(export, target)
        self.assertIsNotNone(restored.k06_protection_document_path)
        document = json.loads(restored.k06_protection_document_path.read_text())
        self.assertEqual(document["protection_identity"], assessment.protection_identity)

    # -- multi-revision -------------------------------------------------

    def test_lone_successor_revision_is_refused_without_its_predecessor_chain(self):
        writer = FakeCatalogWriter()
        self.sealed_evidence(revision=1, writer=writer)
        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2)
        backup_root = Path(self.tempdir.name) / "backup"
        export2 = export_recovery_set(recovery_set2, evidence2, backup_root)
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaisesRegex(RecoveryError, "predecessor_exports"):
            self._restore(export2, target)

    def test_multi_revision_restore_replays_predecessor_chain_and_reproduces_live_revision(self):
        writer = FakeCatalogWriter()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1)
        backup_root = Path(self.tempdir.name) / "backup"
        export1 = export_recovery_set(recovery_set1, evidence1, backup_root)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2)
        backup_root2 = Path(self.tempdir.name) / "backup2"
        export2 = export_recovery_set(recovery_set2, evidence2, backup_root2)

        target = Path(self.tempdir.name) / "restored"
        restore_writer = FakeCatalogWriter()
        restored = self._restore(export2, target, writer=restore_writer, predecessor_exports=(export1,))

        self.assertEqual(restored.sealed.natural_identity, sealed2.natural_identity)
        self.assertEqual(restored.sealed.natural_identity.revision, 2)
        self.assertEqual(restored.sealed.ts_start, Instant.parse("2024-01-16T00:00:00Z"))
        # The predecessor was admitted (then superseded) in the restore
        # target's own catalog -- proving the contiguous history was
        # actually replayed there, not merely accepted as a bare claim.
        self.assertEqual(len(restore_writer.partitions), 2)
        self.assertEqual(restore_writer.partitions[0].natural_identity.revision, 1)
        self.assertEqual(restore_writer.partitions[0].state, "superseded")
        self.assertEqual(restore_writer.partitions[1].state, "closed")

    def test_predecessor_exports_must_be_contiguous_and_same_family(self):
        writer = FakeCatalogWriter()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1)
        backup_root1 = Path(self.tempdir.name) / "backup1"
        export1 = export_recovery_set(recovery_set1, evidence1, backup_root1)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2)
        backup_root2 = Path(self.tempdir.name) / "backup2"
        export2 = export_recovery_set(recovery_set2, evidence2, backup_root2)

        # Wrong count: revision 2 needs exactly one predecessor, not two.
        target_count = Path(self.tempdir.name) / "restored-wrong-count"
        with self.assertRaises(RecoveryError):
            self._restore(export2, target_count, predecessor_exports=(export1, export1))

        # Wrong family: a "predecessor" for an unrelated partition_key within
        # the same dataset identity (the Bybit materializer only accepts its
        # own fixed venue/instrument, so partition_key is the family axis
        # this fixture can vary independently of dataset identity).
        foreign_writer = FakeCatalogWriter()
        foreign_evidence, foreign_sealed = self.sealed_evidence(
            revision=1, writer=foreign_writer, partition_key="dt=2099-01-01",
        )
        foreign_recovery_set = capture_recovery_set(foreign_evidence, foreign_sealed)
        foreign_backup_root = Path(self.tempdir.name) / "backup-foreign"
        foreign_export = export_recovery_set(foreign_recovery_set, foreign_evidence, foreign_backup_root)

        target_family = Path(self.tempdir.name) / "restored-wrong-family"
        with self.assertRaises(RecoveryError):
            self._restore(export2, target_family, predecessor_exports=(foreign_export,))


if __name__ == "__main__":
    unittest.main()
