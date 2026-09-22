#!/usr/bin/env python3
"""K08 backup/restore v1: capture, export, isolated restore and read-back proof.

CREDITs S13 seal semantics (``tests/test_publication_certification_v1.py``),
DataGateway semantics (``tests/test_data_gateway.py``) and K06 protection
identity discipline (``tests/test_operations_protection_v1.py``) rather than
duplicating them.  This file proves only the K08-specific propositions:
deterministic recovery-set capture (with semantic linkage validation and
mandatory K06 applicability) from an already-sealed publication, a durably
persisted and independently reloadable self-contained backup (target
revision plus its complete predecessor chain, each with its own bound K06
evidence), isolated-target/catalog restore refusal/success (path-safety,
a single connection-bound restore-catalog object, empty-family
enforcement), full K06 re-verification at restore's own consumption
boundary (not merely at load time), atomic verify-before-commit
multi-revision restore, and equivalent historical DataGateway reads from the
restored target.
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
    PredecessorEvidence,
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
from quant_platform.operations.recovery import K06NotApplicableAssertion, RecoveryError  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    materialize_bybit_trade_v1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"
REVISION_2_COVERAGE_START = "2024-01-16T00:00:00Z"
REVISION_2_COVERAGE_END = "2024-01-17T00:00:00Z"


def trade(timestamp: str, trade_id: str) -> TradeRecord:
    return TradeRecord(IDENTITY.venue, IDENTITY.instrument, Instant.parse(timestamp), "100.00", "0.5000", "buy", None, trade_id, None)


REVISION_2_RECORDS = [trade("2024-01-16T00:00:01Z", "1"), trade("2024-01-16T00:00:02Z", "2")]

# One shared not-applicable assertion (and its persisted document form) used
# across most fixtures: content-identical calls to K06NotApplicableAssertion
# always fingerprint the same, but sharing one instance keeps capture/export
# call sites obviously consistent with each other.
NOT_APPLICABLE = K06NotApplicableAssertion(
    asserting_authority_id="adr:k08-test-authority-v1",
    asserted_at=Instant.parse("2024-01-15T00:00:00Z"),
    rationale="canonical publication is self-contained for this fixture",
)
NOT_APPLICABLE_DOCUMENT = NOT_APPLICABLE.canonical_payload()


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


class FakeRestoreCatalog:
    """A single object bound to one backing store implementing the complete
    ``RestoreCatalog`` protocol (write contract + authoritative inspector),
    mirroring a real Postgres connection's transactional read-your-own-writes
    semantics -- and, because it is one object, mirroring the structural
    guarantee that restore's write and inspection paths cannot be wired to
    two different catalogs.

    ``partitions`` is the working (possibly uncommitted) set that
    ``seal_partition`` reads/writes for its own contiguous-revision-admission
    checks -- the same invariant the real ``CatalogPublicationWriter``
    enforces (first admitted revision must be 1; successors contiguous).
    ``commit()``/``rollback()`` move the durable boundary; a rollback
    discards every admission made since the last commit, exactly like a real
    transaction, so tests can prove atomicity rather than merely assume it.
    """

    def __init__(self, storage_roots: dict[str, str] | None = None, *, seed: list[SealedCatalogPartition] | None = None):
        self._committed: list[SealedCatalogPartition] = list(seed or ())
        self.partitions: list[SealedCatalogPartition] = list(self._committed)
        self.storage_roots = dict(storage_roots or {})
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
        self._committed = list(self.partitions)
        self.commits += 1

    def rollback(self):
        self.partitions = list(self._committed)
        self.rollbacks += 1

    def resolve_storage_root_abs_path(self, storage_root_id):
        try:
            return self.storage_roots[storage_root_id]
        except KeyError:
            raise RuntimeError(f"unknown storage_root_id: {storage_root_id}")

    def family_admission_count(self, dataset_identity, partition_key):
        return sum(
            1 for item in self._committed
            if item.natural_identity.dataset_identity == dataset_identity
            and item.natural_identity.partition_key == partition_key
        )


class RiggedCatalog(FakeRestoreCatalog):
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
        self.data_path = self.dataset_root / "dt=2024-01-15" / "part-001.parquet"

    def tearDown(self):
        self.tempdir.cleanup()

    def sealed_evidence(self, records=None, *, revision=1, writer=None, identity=IDENTITY,
                         coverage_start=START, coverage_end=END, partition_key="dt=2024-01-15",
                         rel_path=None, dataset_root=None):
        if records is None:
            records = [
                TradeRecord(identity.venue, identity.instrument, Instant.parse("2024-01-15T00:00:01Z"), "100.00", "0.5000", "buy", None, "1", None),
                TradeRecord(identity.venue, identity.instrument, Instant.parse("2024-01-15T00:00:02Z"), "100.00", "0.5000", "buy", None, "2", None),
            ]
        # Every call gets its own file names (unique counter) and every
        # revision its own physical filename: neither a second call for a
        # different partition_key/revision nor a real re-materialized
        # revision ever silently overwrites another's bytes.  A caller
        # proving restore's same-location-different-content collision check
        # passes a distinct ``dataset_root`` with a forced matching
        # ``rel_path``, so two independently sealed publications can declare
        # the same rel_path string without one physically overwriting the
        # other's already-sealed bytes on disk.
        self._evidence_counter = getattr(self, "_evidence_counter", 0) + 1
        call_id = self._evidence_counter
        safe_key = "".join(char if char.isalnum() else "-" for char in partition_key).strip("-").lower()
        rel_path = rel_path or f"{partition_key}/part-{revision:03d}.parquet"
        dataset_root = dataset_root or self.dataset_root
        data_path = dataset_root / rel_path
        materialization = materialize_bybit_trade_v1(data_path, records, dataset_identity=identity)
        dataset_path = self.root / f"dataset-{call_id}.json"
        partition_path = self.root / f"partition-{call_id}.json"
        coverage_path = self.root / f"coverage-{call_id}.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", identity.venue, identity.instrument, "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        emit_partition_manifest(
            partition_path, materialization, dataset_identity=identity, dataset_root=dataset_root,
            partition_key=partition_key, revision=revision, rel_path=rel_path,
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="test-materializer", code_ref="producer-ref",
        )
        emit_coverage_manifest(
            coverage_path, dataset_identity=identity, source_dataset_identity=identity,
            coverage_id=f"coverage-{safe_key}-{revision}-{call_id}", supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": START, "intent_end": "2024-01-18T00:00:00Z",
                "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": f"assertion-{safe_key}-{revision}-{call_id}", "start": coverage_start, "end": coverage_end, "status": "complete",
                "partitions": [{"partition_key": partition_key, "revision": revision}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": "sqlite extract complete"}],
            }], producer="test-source", code_ref="source-ref",
            partition_manifests=[json.loads(partition_path.read_text())],
        )
        evidence = SealedPartitionEvidence(
            dataset_path, partition_path, (coverage_path,), data_path, "hot",
        )
        writer = writer or FakeRestoreCatalog()
        partition_document = json.loads(partition_path.read_text())
        partition_document["_manifest_sha256"] = hashlib.sha256(partition_path.read_bytes()).hexdigest()
        sealed = writer.seal_partition(
            dataset=json.loads(dataset_path.read_text()),
            partition=partition_document,
            coverage_start=Instant.parse(coverage_start), coverage_end=Instant.parse(coverage_end), storage_root_id="hot",
        )
        return evidence, sealed

    def _restore(self, export, target, *, catalog=None, storage_root_abs_path=None, storage_root_id="restored", forbidden_roots=None):
        catalog = catalog or FakeRestoreCatalog()
        catalog.storage_roots[storage_root_id] = str(target if storage_root_abs_path is None else storage_root_abs_path)
        return restore_recovery_set(
            export, target,
            forbidden_roots=forbidden_roots if forbidden_roots is not None else [self.root, export.destination_root],
            restore_catalog=catalog,
            storage_root_id=storage_root_id,
        )

    def _export(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(
            recovery_set, evidence, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
        )
        return evidence, sealed, export

    # -- capture -----------------------------------------------------------

    def test_capture_is_deterministic_for_the_same_finalized_publication(self):
        evidence, sealed = self.sealed_evidence()
        first = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        second = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        self.assertEqual(first.recovery_identity, second.recovery_identity)
        self.assertEqual(first.natural_identity, sealed.natural_identity)
        self.assertIsNone(first.k06_protection_identity)

    def test_capture_requires_exactly_one_k06_applicability_declaration(self):
        evidence, sealed = self.sealed_evidence()
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed)
        with self.assertRaises(RecoveryError):
            capture_recovery_set(
                evidence, sealed, k06_protection=protected_assessment(), k06_not_applicable=NOT_APPLICABLE,
            )

    def test_capture_refuses_non_closed_publication(self):
        evidence, sealed = self.sealed_evidence()
        writing = replace(sealed, state="writing")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, writing, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_refuses_manifest_that_does_not_match_the_sealed_generation(self):
        evidence, sealed = self.sealed_evidence()
        evidence.partition_manifest_path.write_text(evidence.partition_manifest_path.read_text() + " ")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_refuses_tampered_physical_artifact(self):
        evidence, sealed = self.sealed_evidence()
        evidence.artifact_path.write_bytes(evidence.artifact_path.read_bytes() + b"corrupt")
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_refuses_foreign_dataset_manifest(self):
        evidence, sealed = self.sealed_evidence()
        foreign = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
        emit_dataset_manifest(
            evidence.dataset_manifest_path, dataset_identity=foreign, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "ETHUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_refuses_unrelated_coverage_evidence(self):
        evidence, sealed = self.sealed_evidence()
        unrelated = json.loads(evidence.coverage_manifest_paths[0].read_text())
        unrelated["coverage_id"] = "unrelated-coverage"
        unrelated["assertions"][0]["assertion_id"] = "unrelated-assertion"
        unrelated["assertions"][0]["partitions"] = [{"partition_key": "dt=2099-01-01", "revision": 1}]
        evidence.coverage_manifest_paths[0].write_text(json.dumps(unrelated))
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_refuses_coverage_that_does_not_match_sealed_interval(self):
        evidence, sealed = self.sealed_evidence()
        narrowed = json.loads(evidence.coverage_manifest_paths[0].read_text())
        narrowed["assertions"][0]["end"] = "2024-01-15T12:00:00Z"
        evidence.coverage_manifest_paths[0].write_text(json.dumps(narrowed))
        with self.assertRaises(RecoveryError):
            capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)

    def test_capture_binds_protected_k06_evidence_when_supplied(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        self.assertEqual(recovery_set.k06_protection_identity, assessment.protection_identity)
        self.assertIsNotNone(recovery_set.k06_assessment_sha256)
        self.assertIsNone(recovery_set.k06_not_applicable_fingerprint)

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
        evidence, sealed, export = self._export()
        original_bytes = self.data_path.read_bytes()
        self.assertEqual(self.data_path.read_bytes(), original_bytes)
        self.assertTrue(export.artifact_path.is_file())
        self.assertEqual(export.artifact_path.read_bytes(), original_bytes)
        self.assertNotEqual(export.artifact_path, evidence.artifact_path)
        self.assertTrue((export.destination_root / "recovery-manifest.json").is_file())

    def test_export_refuses_when_primary_evidence_changed_since_capture(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        self.data_path.write_bytes(self.data_path.read_bytes() + b"drift")
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set, evidence, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
            )

    def test_export_requires_the_document_matching_the_bound_branch(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root)  # neither document
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set, evidence, backup_root,
                k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
                k06_protection_document=protected_assessment().stable_dict(),
            )  # both documents

    def test_export_persists_protected_evidence_and_refuses_mismatched_document(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=None)
        tampered = dict(assessment.stable_dict())
        tampered["verifier_identity"] = "a-different-verifier"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=tampered)
        export = export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=assessment.stable_dict())
        self.assertIsNotNone(export.k06_evidence_path)
        self.assertTrue(export.k06_evidence_path.is_file())

    def test_export_persists_not_applicable_evidence_and_refuses_mismatched_document(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        tampered = dict(NOT_APPLICABLE_DOCUMENT)
        tampered["rationale"] = "a different, unattributed rationale"
        with self.assertRaises(RecoveryError):
            export_recovery_set(recovery_set, evidence, backup_root, k06_not_applicable_document=tampered)
        export = export_recovery_set(
            recovery_set, evidence, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
        )
        self.assertIsNotNone(export.k06_evidence_path)
        # The full attributed assertion -- not merely its fingerprint -- is
        # what is actually persisted and inspectable after process loss.
        persisted = json.loads(export.k06_evidence_path.read_text())
        self.assertEqual(persisted["asserting_authority_id"], NOT_APPLICABLE.asserting_authority_id)
        self.assertEqual(persisted["rationale"], NOT_APPLICABLE.rationale)

    def test_load_recovery_backup_export_round_trips_and_reverifies_from_disk(self):
        evidence, sealed, export = self._export()
        recovery_set = export.recovery_set

        loaded = load_recovery_backup_export(export.destination_root)
        self.assertEqual(loaded.recovery_set.recovery_identity, recovery_set.recovery_identity)
        self.assertEqual(loaded.dataset_manifest_path, export.dataset_manifest_path)
        self.assertEqual(loaded.artifact_path.read_bytes(), export.artifact_path.read_bytes())

        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(loaded, target)
        self.assertEqual(restored.sealed.natural_identity, recovery_set.natural_identity)

    def test_load_recovery_backup_export_detects_tampered_protected_evidence_content(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=assessment.stable_dict())
        # Tamper a field other than protection_identity/state: the bound full
        # assessment digest must still catch it.
        document = json.loads(export.k06_evidence_path.read_text())
        document["verifier_identity"] = "someone-else"
        export.k06_evidence_path.write_text(json.dumps(document))
        with self.assertRaises(RecoveryError):
            load_recovery_backup_export(export.destination_root)

    def test_load_recovery_backup_export_detects_tampered_not_applicable_evidence_content(self):
        evidence, sealed = self.sealed_evidence()
        recovery_set = capture_recovery_set(evidence, sealed, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(
            recovery_set, evidence, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
        )
        document = json.loads(export.k06_evidence_path.read_text())
        document["asserting_authority_id"] = "someone-unattributed"
        export.k06_evidence_path.write_text(json.dumps(document))
        with self.assertRaises(RecoveryError):
            load_recovery_backup_export(export.destination_root)

    def test_load_recovery_backup_export_detects_tampered_member(self):
        evidence, sealed, export = self._export()
        export.artifact_path.write_bytes(export.artifact_path.read_bytes() + b"tamper")
        with self.assertRaises(RecoveryError):
            load_recovery_backup_export(export.destination_root)

    def test_load_recovery_backup_export_refuses_manifest_member_path_traversal(self):
        evidence, sealed, export = self._export()
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

    def test_restore_refuses_when_storage_root_does_not_resolve_to_target(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        other = Path(self.tempdir.name) / "not-the-target"
        with self.assertRaises(RecoveryError):
            self._restore(export, target, storage_root_abs_path=other)
        # In particular, claiming the primary's own location is refused too.
        with self.assertRaises(RecoveryError):
            self._restore(export, target, storage_root_abs_path=self.root)

    def test_restore_refuses_when_storage_root_id_is_unregistered_in_the_restore_catalog(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        catalog = FakeRestoreCatalog()  # no storage_roots registered at all
        with self.assertRaises(RuntimeError):
            restore_recovery_set(
                export, target, forbidden_roots=[self.root, export.destination_root],
                restore_catalog=catalog, storage_root_id="restored",
            )

    def test_restore_refuses_non_empty_restore_catalog_family(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        # A catalog that already has a (committed) row for this exact family,
        # simulating a restore catalog that is not actually isolated for it.
        pre_existing = SealedCatalogPartition(
            "pre-existing-partition", "pre-existing-dataset", sealed.natural_identity,
            "closed", sealed.ts_start, sealed.ts_end, sealed.row_count, sealed.byte_size,
            sealed.content_sha256, sealed.manifest_sha256, sealed.producer, sealed.code_ref,
        )
        catalog = FakeRestoreCatalog(seed=[pre_existing])
        with self.assertRaises(RecoveryError):
            self._restore(export, target, catalog=catalog)

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

    def test_restore_fully_reverifies_k06_evidence_at_its_own_consumption_boundary(self):
        # Tamper the exported K06 document directly and pass the SAME
        # RecoveryBackupExport object straight to restore, bypassing
        # load_recovery_backup_export entirely -- proving restore itself
        # (not merely the loader) fully re-validates the complete document,
        # not just a protection_identity/fingerprint string match.
        evidence, sealed, export = self._export()
        document = json.loads(export.k06_evidence_path.read_text())
        document["rationale"] = "modified immediately before restore"
        export.k06_evidence_path.write_text(json.dumps(document))
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            self._restore(export, target)

    def test_restore_refuses_missing_k06_evidence_file(self):
        evidence, sealed, export = self._export()
        export.k06_evidence_path.unlink()
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
                    self.assertEqual(list(target.iterdir()) if target.exists() else [], [])
                finally:
                    source_path.write_bytes(original)

    def test_restore_refuses_before_commit_when_returned_admission_mismatches(self):
        evidence, sealed, export = self._export()
        target = Path(self.tempdir.name) / "restored"
        rigged = RiggedCatalog()
        with self.assertRaises(RecoveryError):
            self._restore(export, target, catalog=rigged)
        self.assertEqual(rigged.commits, 0)
        self.assertEqual(rigged.rollbacks, 1)
        # Nothing durable: the family remains admittable afterward.
        self.assertEqual(rigged.family_admission_count(sealed.natural_identity.dataset_identity, sealed.natural_identity.partition_key), 0)

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
            REL_ROOT, "dt=2024-01-15/part-001.parquet",
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

    def test_restore_carries_protected_k06_evidence_through(self):
        evidence, sealed = self.sealed_evidence()
        assessment = protected_assessment()
        recovery_set = capture_recovery_set(evidence, sealed, k06_protection=assessment)
        backup_root = Path(self.tempdir.name) / "backup"
        export = export_recovery_set(recovery_set, evidence, backup_root, k06_protection_document=assessment.stable_dict())
        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(export, target)
        self.assertIsNotNone(restored.k06_evidence_path)
        document = json.loads(restored.k06_evidence_path.read_text())
        self.assertEqual(document["protection_identity"], assessment.protection_identity)

    # -- multi-revision: self-contained chain (including predecessor K06
    #    evidence), atomic restore --------------------------------------

    def test_export_refuses_wrong_predecessor_count(self):
        writer = FakeRestoreCatalog()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        export_recovery_set(
            recovery_set1, evidence1, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
        )

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)
        backup_root2 = Path(self.tempdir.name) / "backup2"
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set2, evidence2, backup_root2,
                k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT, predecessors=(),
            )
        predecessor = PredecessorEvidence(recovery_set1, evidence1, k06_document=NOT_APPLICABLE_DOCUMENT)
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set2, evidence2, backup_root2,
                k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
                predecessors=[predecessor, predecessor],
            )

    def test_lone_revision_1_export_has_no_predecessors_and_restores_directly(self):
        evidence, sealed, export = self._export()
        self.assertEqual(export.predecessors, ())
        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(export, target)
        self.assertEqual(restored.sealed.natural_identity.revision, 1)

    def test_multi_revision_export_is_self_contained_and_restores_atomically(self):
        writer = FakeRestoreCatalog()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_not_applicable=NOT_APPLICABLE)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        export2 = export_recovery_set(
            recovery_set2, evidence2, backup_root,
            k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
            predecessors=[PredecessorEvidence(recovery_set1, evidence1, k06_document=NOT_APPLICABLE_DOCUMENT)],
        )
        self.assertEqual(len(export2.predecessors), 1)
        self.assertEqual(export2.predecessors[0].recovery_set.natural_identity.revision, 1)
        self.assertIsNotNone(export2.predecessors[0].k06_evidence_path)

        # The export alone -- no separately supplied side information -- is
        # everything restore needs, including after a reload from disk.
        loaded = load_recovery_backup_export(export2.destination_root)
        self.assertEqual(len(loaded.predecessors), 1)
        self.assertIsNotNone(loaded.predecessors[0].k06_evidence_path)

        target = Path(self.tempdir.name) / "restored"
        restore_catalog = FakeRestoreCatalog()
        restored = self._restore(loaded, target, catalog=restore_catalog)

        self.assertEqual(restored.sealed.natural_identity, sealed2.natural_identity)
        self.assertEqual(restored.sealed.natural_identity.revision, 2)
        self.assertEqual(restored.sealed.ts_start, Instant.parse(REVISION_2_COVERAGE_START))
        # Both revisions were actually admitted (then rev 1 superseded) in
        # the restore catalog's own durable state -- the chain was truly
        # replayed there, not merely accepted as a bare claim -- and both
        # commits happened as a single atomic transaction (one commit call).
        self.assertEqual(restore_catalog.commits, 1)
        self.assertEqual(restore_catalog.rollbacks, 0)
        self.assertEqual(len(restore_catalog.partitions), 2)
        self.assertEqual(restore_catalog.partitions[0].natural_identity.revision, 1)
        self.assertEqual(restore_catalog.partitions[0].state, "superseded")
        self.assertEqual(restore_catalog.partitions[1].state, "closed")

        # Predecessor artifact bytes were genuinely restored to the target,
        # not merely referenced -- a 'closed' catalog row is never left
        # pointing at a physically absent file.
        predecessor_artifact = target / REL_ROOT / "dt=2024-01-15" / "part-001.parquet"
        self.assertTrue(predecessor_artifact.is_file())
        self.assertEqual(predecessor_artifact.read_bytes(), self.data_path.read_bytes())

    def test_protected_predecessor_carries_its_own_k06_evidence_through(self):
        writer = FakeRestoreCatalog()
        predecessor_assessment = protected_assessment()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_protection=predecessor_assessment)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        export2 = export_recovery_set(
            recovery_set2, evidence2, backup_root,
            k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
            predecessors=[
                PredecessorEvidence(recovery_set1, evidence1, k06_document=predecessor_assessment.stable_dict()),
            ],
        )
        loaded = load_recovery_backup_export(export2.destination_root)
        self.assertIsNotNone(loaded.predecessors[0].k06_evidence_path)
        document = json.loads(loaded.predecessors[0].k06_evidence_path.read_text())
        self.assertEqual(document["protection_identity"], predecessor_assessment.protection_identity)

        target = Path(self.tempdir.name) / "restored"
        restored = self._restore(loaded, target)
        self.assertEqual(restored.sealed.natural_identity.revision, 2)

    def test_export_refuses_predecessor_missing_its_own_k06_evidence(self):
        writer = FakeRestoreCatalog()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_not_applicable=NOT_APPLICABLE)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)
        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set2, evidence2, backup_root,
                k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
                predecessors=[PredecessorEvidence(recovery_set1, evidence1, k06_document=None)],
            )

    def test_restore_refuses_two_revisions_claiming_the_same_physical_location(self):
        writer = FakeRestoreCatalog()
        colliding_rel_path = "dt=2024-01-20/part-001.parquet"
        evidence1, sealed1 = self.sealed_evidence(
            revision=1, writer=writer, partition_key="dt=2024-01-20", rel_path=colliding_rel_path,
        )
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_not_applicable=NOT_APPLICABLE)

        # Revision 2 is independently, validly sealed (own materialization
        # under its own temp dataset_root, so it never overwrites revision
        # 1's already-sealed bytes on disk) but its own durable partition
        # manifest declares the SAME rel_path string as revision 1 --
        # simulating a producer bug/attack that reuses a physical location
        # across revisions -- with genuinely different content.
        second_dataset_root = Path(self.tempdir.name) / "primary-rev2" / REL_ROOT
        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer, partition_key="dt=2024-01-20",
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
            rel_path=colliding_rel_path, dataset_root=second_dataset_root,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)
        self.assertNotEqual(recovery_set1.physical_content_sha256, recovery_set2.physical_content_sha256)

        backup_root = Path(self.tempdir.name) / "backup"
        export2 = export_recovery_set(
            recovery_set2, evidence2, backup_root,
            k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
            predecessors=[PredecessorEvidence(recovery_set1, evidence1, k06_document=NOT_APPLICABLE_DOCUMENT)],
        )
        target = Path(self.tempdir.name) / "restored"
        with self.assertRaises(RecoveryError):
            self._restore(export2, target)

    def test_predecessor_chain_must_be_contiguous_and_same_family(self):
        writer = FakeRestoreCatalog()
        evidence1, sealed1 = self.sealed_evidence(revision=1, writer=writer)
        recovery_set1 = capture_recovery_set(evidence1, sealed1, k06_not_applicable=NOT_APPLICABLE)

        evidence2, sealed2 = self.sealed_evidence(
            REVISION_2_RECORDS, revision=2, writer=writer,
            coverage_start=REVISION_2_COVERAGE_START, coverage_end=REVISION_2_COVERAGE_END,
        )
        recovery_set2 = capture_recovery_set(evidence2, sealed2, k06_not_applicable=NOT_APPLICABLE)

        foreign_writer = FakeRestoreCatalog()
        foreign_evidence, foreign_sealed = self.sealed_evidence(
            revision=1, writer=foreign_writer, partition_key="dt=2099-01-01",
        )
        foreign_recovery_set = capture_recovery_set(foreign_evidence, foreign_sealed, k06_not_applicable=NOT_APPLICABLE)

        backup_root = Path(self.tempdir.name) / "backup"
        with self.assertRaises(RecoveryError):
            export_recovery_set(
                recovery_set2, evidence2, backup_root,
                k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
                predecessors=[PredecessorEvidence(foreign_recovery_set, foreign_evidence, k06_document=NOT_APPLICABLE_DOCUMENT)],
            )


if __name__ == "__main__":
    unittest.main()
