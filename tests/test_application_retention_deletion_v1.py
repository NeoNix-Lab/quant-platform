from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quant_platform.application.retention_deletion import (
    InMemoryRetentionDeletionAuditStore,
    RetentionDeletionApplicationError,
    RetentionDeletionCrashPoint,
    RetentionDeletionInterrupted,
    execute_retention_deletion,
    recover_retention_deletion_result,
)
from quant_platform.data.models import DatasetIdentity, Instant
from quant_platform.operations.retention import (
    DeletionCandidateV1,
    PreservationClass,
    ProtectionEvidenceRef,
    RelocationEvidenceRef,
    RetentionDeletionDecision,
    RetentionPolicyDefinitionV1,
    VerifiedRestoreProofRef,
    evaluate_retention_deletion,
)


PAYLOAD = b"k09 retention candidate\n"
HASH = hashlib.sha256(PAYLOAD).hexdigest()


def identity() -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")


def candidate(**overrides) -> DeletionCandidateV1:
    params = {
        "dataset_identity": identity(),
        "catalog_partition_id": "11111111-1111-4111-8111-111111111111",
        "partition_key": "dt=2026-01-01",
        "revision": 1,
        "storage_root_id": "hot",
        "rel_path": "dt=2026-01-01/part-000.parquet",
        "content_sha256": HASH,
        "byte_size": len(PAYLOAD),
        "lifecycle_state": "closed",
        "producer": "test-producer",
        "code_ref": "abc123",
        "finalized_at": Instant.parse("2026-01-01T00:00:00Z"),
        "preservation_class": PreservationClass.CANONICAL_RESTORABLE,
    }
    params.update(overrides)
    return DeletionCandidateV1(**params)


def restore_proof() -> VerifiedRestoreProofRef:
    return VerifiedRestoreProofRef(
        recovery_set_identity="recovery-set-v1:sha256:" + "b" * 64,
        isolated_restore_proof_identity="isolated-restore-proof-v1:sha256:" + "c" * 64,
        dataset_identity=identity(),
        partition_key="dt=2026-01-01",
        revision=1,
        restored_content_sha256=HASH,
        restored_size_bytes=len(PAYLOAD),
        restored_coverage_or_support="2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
        storage_boundary_independent=True,
    )


def decision(**overrides):
    params = {
        "policy": RetentionPolicyDefinitionV1(),
        "candidate": candidate(),
        "decision_time": "2026-04-15T00:00:00Z",
        "decision_actor_or_authority": "k09-test",
        "restore_proof": restore_proof(),
        "protection_evidence": ProtectionEvidenceRef(),
        "relocation_evidence": RelocationEvidenceRef(),
    }
    params.update(overrides)
    return evaluate_retention_deletion(**params)


def write_candidate(root: Path) -> Path:
    path = root / "dt=2026-01-01" / "part-000.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


class RetentionDeletionApplicationTests(unittest.TestCase):
    def test_permitted_deletion_persists_decision_then_deletes_exact_file_and_records_result(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_candidate(root)
            store = InMemoryRetentionDeletionAuditStore()
            permitted = decision()

            result = execute_retention_deletion(
                decision=permitted,
                storage_root=root,
                audit_store=store,
                deleted_at="2026-04-15T00:00:01Z",
                tombstone_or_catalog_update_ref="audit-row-1",
            )

            self.assertFalse(path.exists())
            self.assertEqual(result.decision, RetentionDeletionDecision.PERMITTED)
            self.assertTrue(result.deleted)
            self.assertIn(permitted.decision_identity, store.decisions)
            self.assertIn(permitted.decision_identity, store.results)

    def test_refused_decision_is_audited_and_never_deletes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_candidate(root)
            store = InMemoryRetentionDeletionAuditStore()
            refused = decision(restore_proof=None)

            result = execute_retention_deletion(
                decision=refused,
                storage_root=root,
                audit_store=store,
                deleted_at="2026-04-15T00:00:01Z",
                tombstone_or_catalog_update_ref="unused",
            )

            self.assertTrue(path.exists())
            self.assertFalse(result.deleted)
            self.assertEqual(result.decision, RetentionDeletionDecision.REFUSED)
            self.assertIn(refused.decision_identity, store.decisions)
            self.assertIn(refused.decision_identity, store.results)

    def test_crash_after_decision_persisted_leaves_file_retained(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_candidate(root)
            store = InMemoryRetentionDeletionAuditStore()
            permitted = decision()

            with self.assertRaises(RetentionDeletionInterrupted):
                execute_retention_deletion(
                    decision=permitted,
                    storage_root=root,
                    audit_store=store,
                    deleted_at="2026-04-15T00:00:01Z",
                    tombstone_or_catalog_update_ref="audit-row-1",
                    crash_after=RetentionDeletionCrashPoint.AFTER_DECISION_PERSISTED,
                )

            self.assertTrue(path.exists())
            self.assertIn(permitted.decision_identity, store.decisions)
            self.assertNotIn(permitted.decision_identity, store.results)

    def test_crash_after_bytes_deleted_can_recover_result_against_same_decision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_candidate(root)
            store = InMemoryRetentionDeletionAuditStore()
            permitted = decision()

            with self.assertRaises(RetentionDeletionInterrupted):
                execute_retention_deletion(
                    decision=permitted,
                    storage_root=root,
                    audit_store=store,
                    deleted_at="2026-04-15T00:00:01Z",
                    tombstone_or_catalog_update_ref="audit-row-1",
                    crash_after=RetentionDeletionCrashPoint.AFTER_BYTES_DELETED,
                )

            self.assertFalse(path.exists())
            self.assertNotIn(permitted.decision_identity, store.results)
            result = recover_retention_deletion_result(
                decision=permitted,
                storage_root=root,
                audit_store=store,
                deleted_at="2026-04-15T00:00:02Z",
                tombstone_or_catalog_update_ref="audit-row-1",
            )
            self.assertTrue(result.deleted)
            self.assertIn(permitted.decision_identity, store.results)

    def test_application_refuses_to_delete_when_file_identity_changed_after_decision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_candidate(root)
            path.write_bytes(b"tampered")
            store = InMemoryRetentionDeletionAuditStore()

            with self.assertRaises(RetentionDeletionApplicationError):
                execute_retention_deletion(
                    decision=decision(),
                    storage_root=root,
                    audit_store=store,
                    deleted_at="2026-04-15T00:00:01Z",
                    tombstone_or_catalog_update_ref="audit-row-1",
                )
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
