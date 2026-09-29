from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant  # noqa: E402
from quant_platform.operations.relocation import RelocationPhase  # noqa: E402
from quant_platform.operations.retention import (
    DeletionCandidateV1,
    PreservationClass,
    ProtectionEvidenceRef,
    RelocationEvidenceRef,
    RetentionDeletionDecision,
    RetentionDeletionError,
    RetentionPolicyDefinitionV1,
    RetentionRefusalReason,
    VerifiedRestoreProofRef,
    evaluate_retention_deletion,
)


HASH = "a" * 64
RECOVERY_ID = "recovery-set-v1:sha256:" + "b" * 64
RESTORE_PROOF_ID = "isolated-restore-proof-v1:sha256:" + "c" * 64
RELOCATION_ID = "relocation-v1:sha256:" + "d" * 64


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
        "byte_size": 12,
        "lifecycle_state": "closed",
        "producer": "test-producer",
        "code_ref": "abc123",
        "finalized_at": Instant.parse("2026-01-01T00:00:00Z"),
        "preservation_class": PreservationClass.CANONICAL_RESTORABLE,
    }
    params.update(overrides)
    return DeletionCandidateV1(**params)


def restore_proof(**overrides) -> VerifiedRestoreProofRef:
    params = {
        "recovery_set_identity": RECOVERY_ID,
        "isolated_restore_proof_identity": RESTORE_PROOF_ID,
        "dataset_identity": identity(),
        "partition_key": "dt=2026-01-01",
        "revision": 1,
        "restored_content_sha256": HASH,
        "restored_size_bytes": 12,
        "restored_coverage_or_support": "2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
        "storage_boundary_independent": True,
        "depends_on_candidate": False,
    }
    params.update(overrides)
    return VerifiedRestoreProofRef(**params)


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


class RetentionDeletionDecisionTests(unittest.TestCase):
    def test_permitted_decision_requires_cross_bound_restore_proof(self):
        result = decision()

        self.assertEqual(result.decision, RetentionDeletionDecision.PERMITTED)
        self.assertEqual(result.refusal_reasons, ())
        self.assertTrue(result.decision_identity.startswith("retention-deletion-decision-v1:sha256:"))
        audit = result.stable_dict()
        self.assertEqual(audit["restore_evidence"]["restored_content_sha256"], HASH)
        self.assertEqual(audit["retention"]["minimum_retention_days"], 90)

    def test_missing_or_stale_restore_proof_refuses(self):
        missing = decision(restore_proof=None)
        self.assertEqual(missing.decision, RetentionDeletionDecision.REFUSED)
        self.assertIn(RetentionRefusalReason.MISSING_VERIFIED_RESTORE, missing.refusal_reasons)

        stale = decision(restore_proof=restore_proof(restored_content_sha256="e" * 64))
        self.assertEqual(stale.decision, RetentionDeletionDecision.REFUSED)
        self.assertIn(RetentionRefusalReason.RESTORE_PROOF_IDENTITY_MISMATCH, stale.refusal_reasons)

        dependent = decision(restore_proof=restore_proof(depends_on_candidate=True))
        self.assertIn(RetentionRefusalReason.RESTORE_PROOF_DEPENDS_ON_CANDIDATE, dependent.refusal_reasons)

    def test_permanent_preservation_classes_are_refused_without_override(self):
        for klass in (PreservationClass.UNIQUE_SOURCE, PreservationClass.PROTECTED_EVIDENCE):
            with self.subTest(klass=klass):
                result = decision(candidate=candidate(preservation_class=klass))
                self.assertEqual(result.decision, RetentionDeletionDecision.REFUSED)
                self.assertIn(RetentionRefusalReason.PERMANENT_PRESERVATION_CLASS, result.refusal_reasons)

    def test_protected_and_sole_recoverable_evidence_are_refused(self):
        result = decision(
            protection_evidence=ProtectionEvidenceRef(
                candidate_is_k06_protected_evidence=True,
                candidate_is_sole_recoverable_evidence=True,
                k06_assessment_identity="protection-assessment-v1:sha256:" + "f" * 64,
                protection_state="PROTECTED",
            )
        )

        self.assertEqual(result.decision, RetentionDeletionDecision.REFUSED)
        self.assertIn(RetentionRefusalReason.K06_PROTECTED_EVIDENCE, result.refusal_reasons)
        self.assertIn(RetentionRefusalReason.SOLE_RECOVERABLE_EVIDENCE, result.refusal_reasons)

    def test_retention_period_and_lifecycle_must_be_satisfied(self):
        too_early = decision(decision_time="2026-01-15T00:00:00Z")
        self.assertEqual(too_early.decision, RetentionDeletionDecision.REFUSED)
        self.assertIn(RetentionRefusalReason.RETENTION_PERIOD_NOT_ELAPSED, too_early.refusal_reasons)

        writing = decision(candidate=candidate(lifecycle_state="writing"))
        self.assertEqual(writing.decision, RetentionDeletionDecision.REFUSED)
        self.assertIn(RetentionRefusalReason.NOT_FINALIZED, writing.refusal_reasons)

    def test_non_terminal_relocation_refuses_deletion(self):
        for phase in (
            RelocationPhase.PLANNED,
            RelocationPhase.STAGED,
            RelocationPhase.VERIFIED,
            RelocationPhase.SWITCHED,
        ):
            with self.subTest(phase=phase):
                result = decision(relocation_evidence=RelocationEvidenceRef(RELOCATION_ID, phase))
                self.assertEqual(result.decision, RetentionDeletionDecision.REFUSED)
                self.assertIn(RetentionRefusalReason.RELOCATION_IN_FLIGHT, result.refusal_reasons)

        cleaned = decision(relocation_evidence=RelocationEvidenceRef(RELOCATION_ID, RelocationPhase.CLEANED_UP))
        self.assertEqual(cleaned.decision, RetentionDeletionDecision.PERMITTED)

    def test_unknown_preservation_class_is_malformed_not_ambiguous(self):
        with self.assertRaises(RetentionDeletionError):
            candidate(preservation_class="MAYBE_DELETE")

    def test_derived_restorable_uses_thirty_day_floor(self):
        derived = decision(
            candidate=candidate(preservation_class=PreservationClass.DERIVED_RESTORABLE),
            decision_time="2026-02-05T00:00:00Z",
        )
        self.assertEqual(derived.decision, RetentionDeletionDecision.PERMITTED)
        self.assertEqual(derived.minimum_retention_days, 30)


if __name__ == "__main__":
    unittest.main()
