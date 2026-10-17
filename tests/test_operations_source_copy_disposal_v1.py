"""Pure tests for the ADR-0049 Amendment 1 source-copy-disposal decision (#425)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant  # noqa: E402
from quant_platform.operations.protection import (  # noqa: E402
    AcceptedReconstructionContract,
    ArtifactProtectionIdentity,
    ArtifactReadOutcome,
    ArtifactVerificationEvidence,
    ProtectionUnitIdentity,
    assess_protection,
)
from quant_platform.operations.relocation import RelocationPhase  # noqa: E402
from quant_platform.operations.retention import (  # noqa: E402
    DeletionCandidateV1,
    PreservationClass,
    RelocationEvidenceRef,
    RetentionDeletionDecision,
    RetentionPolicyDefinitionV1,
    evaluate_retention_deletion,
)
from quant_platform.operations.source_copy_disposal import (  # noqa: E402
    CandidateObservationV1,
    CanonicalPartitionVerificationV1,
    SourceCopyDisposalError,
    SourceCopyDisposalPolicyV1,
    SourceCopyDisposalRefusalReason as R,
    SurvivingCopyEvidenceV1,
    evaluate_source_copy_disposal,
)

PAYLOAD = b"ob200 archive bytes\n"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)
OTHER_SHA = hashlib.sha256(b"something else").hexdigest()
NOW = Instant.parse("2026-10-17T12:00:00Z")
ROLE = "source_archive"


def raw_identity() -> DatasetIdentity:
    return DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")


def candidate(**overrides) -> DeletionCandidateV1:
    params = dict(
        dataset_identity=raw_identity(), storage_root_id="hot", rel_path="archive/2025-12-01_BTCUSDT_ob200.data.zip",
        content_sha256=SHA, byte_size=SIZE, lifecycle_state="source_acquired", producer="p", code_ref="c",
        finalized_at=Instant.parse("2026-10-01T00:00:00Z"), preservation_class=PreservationClass.UNIQUE_SOURCE,
    )
    params.update(overrides)
    return DeletionCandidateV1(**params)


def observation(**overrides) -> CandidateObservationV1:
    params = dict(content_sha256=SHA, byte_size=SIZE, verified_at=NOW)
    params.update(overrides)
    return CandidateObservationV1(**params)


def surviving(**overrides) -> SurvivingCopyEvidenceV1:
    params = dict(storage_root_id="cold", rel_path="backup/2025-12-01_BTCUSDT_ob200.data.zip",
                  content_sha256=SHA, byte_size=SIZE, verified_at=NOW)
    params.update(overrides)
    return SurvivingCopyEvidenceV1(**params)


def verification(**overrides) -> CanonicalPartitionVerificationV1:
    params = dict(
        partition_key="dt=2025-12-01/seg=001", revision=1, catalog_partition_id="11111111-1111-4111-8111-111111111111",
        catalog_state="valid", catalog_content_sha256=OTHER_SHA, catalog_byte_size=10,
        observed_content_sha256=OTHER_SHA, observed_byte_size=10, binds_source_archive=True, verified_at=NOW,
    )
    params.update(overrides)
    return CanonicalPartitionVerificationV1(**params)


def assessment(*, scopes=(("cold", ArtifactReadOutcome.READ, SHA, SIZE),), declared=(SHA, SIZE), at=NOW):
    unit = ProtectionUnitIdentity(
        source_semantics_id="s", mapping_id="m", reconstruction_contract_id="r", protected_support="support",
        artifacts=(ArtifactProtectionIdentity(ROLE, declared[0], declared[1]),),
        accepted_contract=AcceptedReconstructionContract(
            "s", "m", "r", (ROLE,), "ADR-0049-Amendment-1", Instant.parse("2026-10-17T00:00:00Z")),
    )
    evidence = []
    for scope, outcome, sha, size in scopes:
        if outcome == ArtifactReadOutcome.READ:
            evidence.append(ArtifactVerificationEvidence(ROLE, scope, outcome, sha, size))
        else:
            evidence.append(ArtifactVerificationEvidence(ROLE, scope, outcome))
    return assess_protection(unit, tuple(evidence), verified_at=at, verifier_identity="v")


def decide(**overrides):
    params = dict(
        policy=SourceCopyDisposalPolicyV1(), candidate=candidate(), decision_time=NOW,
        decision_actor_or_authority="maintainer", candidate_observation=observation(),
        surviving_copy=surviving(), canonical_verifications=(verification(),),
        protection_assessment=assessment(), relocation_evidence=None,
    )
    params.update(overrides)
    return evaluate_source_copy_disposal(**params)


class PermittedTests(unittest.TestCase):
    def test_all_evidence_present_permits(self):
        decision = decide()
        self.assertEqual(decision.decision, RetentionDeletionDecision.PERMITTED)
        self.assertEqual(decision.refusal_reasons, ())

    def test_identity_keeps_k09_form_and_differs_from_a_k09_decision(self):
        decision = decide()
        self.assertRegex(decision.decision_identity, r"^retention-deletion-decision-v1:sha256:[0-9a-f]{64}$")
        self.assertEqual(decision.stable_dict()["deletion_decision_id"], decision.decision_identity)
        self.assertEqual(decision.stable_dict()["decision_kind"], "source_copy_disposal")
        k09 = evaluate_retention_deletion(
            policy=RetentionPolicyDefinitionV1(), candidate=candidate(), decision_time=NOW,
            decision_actor_or_authority="maintainer",
        )
        self.assertNotEqual(decision.decision_identity, k09.decision_identity)
        self.assertNotEqual(decision.policy.definition_identity, RetentionPolicyDefinitionV1().definition_identity)

    def test_k09_evaluate_still_refuses_the_same_unique_source_candidate(self):
        k09 = evaluate_retention_deletion(
            policy=RetentionPolicyDefinitionV1(), candidate=candidate(), decision_time=NOW,
            decision_actor_or_authority="maintainer",
        )
        self.assertEqual(k09.decision, RetentionDeletionDecision.REFUSED)

    def test_identity_is_deterministic_and_input_sensitive(self):
        self.assertEqual(decide().decision_identity, decide().decision_identity)
        self.assertNotEqual(decide().decision_identity, decide(decision_actor_or_authority="other").decision_identity)
        self.assertNotEqual(
            decide().decision_identity,
            decide(policy=SourceCopyDisposalPolicyV1(max_evidence_age_seconds=60)).decision_identity,
        )


class RefusalTests(unittest.TestCase):
    def assertRefused(self, decision, *reasons):
        self.assertEqual(decision.decision, RetentionDeletionDecision.REFUSED)
        for reason in reasons:
            self.assertIn(reason, decision.refusal_reasons)

    def test_candidate_must_be_a_unique_source_copy_without_partition_fields(self):
        self.assertRefused(decide(candidate=candidate(preservation_class=PreservationClass.CANONICAL_RESTORABLE)),
                           R.CANDIDATE_NOT_SOURCE_COPY)
        self.assertRefused(decide(candidate=candidate(partition_key="dt=2025-12-01")), R.CANDIDATE_NOT_SOURCE_COPY)

    def test_candidate_observation_is_required_and_must_match(self):
        self.assertRefused(decide(candidate_observation=None), R.MISSING_CANDIDATE_OBSERVATION)
        self.assertRefused(decide(candidate_observation=observation(content_sha256=OTHER_SHA)),
                           R.CANDIDATE_IDENTITY_MISMATCH)
        self.assertRefused(decide(candidate_observation=observation(byte_size=SIZE + 1)), R.CANDIDATE_IDENTITY_MISMATCH)

    def test_a_surviving_copy_is_required(self):
        self.assertRefused(decide(surviving_copy=None), R.MISSING_SURVIVING_COPY)

    def test_surviving_copy_must_be_in_another_root_and_not_the_candidate(self):
        self.assertRefused(decide(surviving_copy=surviving(storage_root_id="hot", rel_path="other/path.zip")),
                           R.SURVIVING_COPY_SAME_ROOT)
        same = decide(surviving_copy=surviving(storage_root_id="hot", rel_path=candidate().rel_path))
        self.assertRefused(same, R.SURVIVING_COPY_IS_CANDIDATE, R.SURVIVING_COPY_SAME_ROOT)
        self.assertRefused(decide(surviving_copy=surviving(is_same_file_as_candidate=True)),
                           R.SURVIVING_COPY_IS_CANDIDATE)

    def test_surviving_copy_must_carry_the_same_bytes(self):
        self.assertRefused(decide(surviving_copy=surviving(content_sha256=OTHER_SHA)), R.SURVIVING_COPY_IDENTITY_MISMATCH)
        self.assertRefused(decide(surviving_copy=surviving(byte_size=SIZE + 5)), R.SURVIVING_COPY_IDENTITY_MISMATCH)

    def test_canonical_verification_is_required_and_every_partition_must_hold(self):
        self.assertRefused(decide(canonical_verifications=()), R.MISSING_CANONICAL_VERIFICATION)
        self.assertRefused(decide(canonical_verifications=(verification(catalog_state="closed"),)),
                           R.CANONICAL_PARTITION_NOT_VALID)
        self.assertRefused(decide(canonical_verifications=(verification(observed_content_sha256=SHA),)),
                           R.CANONICAL_BYTES_MISMATCH)
        self.assertRefused(decide(canonical_verifications=(verification(observed_byte_size=11),)),
                           R.CANONICAL_BYTES_MISMATCH)
        self.assertRefused(decide(canonical_verifications=(
            verification(observed_content_sha256=None, observed_byte_size=None),)), R.CANONICAL_BYTES_MISMATCH)
        self.assertRefused(decide(canonical_verifications=(verification(binds_source_archive=False),)),
                           R.CANONICAL_NOT_BOUND_TO_ARCHIVE)

    def test_one_bad_partition_among_good_ones_refuses(self):
        good = verification()
        bad = verification(partition_key="dt=2025-12-01/seg=002", catalog_state="invalid")
        self.assertRefused(decide(canonical_verifications=(good, bad)), R.CANONICAL_PARTITION_NOT_VALID)

    def test_protection_assessment_is_required_and_must_be_protected(self):
        self.assertRefused(decide(protection_assessment=None), R.MISSING_PROTECTION_ASSESSMENT)
        absent = assessment(scopes=(("cold", ArtifactReadOutcome.ABSENT, None, None),))
        self.assertRefused(decide(protection_assessment=absent), R.PROTECTION_NOT_PROTECTED,
                           R.PROTECTION_SURVIVING_COPY_NOT_READ)
        wrong = assessment(scopes=(("cold", ArtifactReadOutcome.READ, OTHER_SHA, SIZE),))
        self.assertRefused(decide(protection_assessment=wrong), R.PROTECTION_NOT_PROTECTED)

    def test_protection_must_be_over_the_surviving_copy_alone(self):
        with_candidate = assessment(scopes=(
            ("cold", ArtifactReadOutcome.READ, SHA, SIZE), ("hot", ArtifactReadOutcome.READ, SHA, SIZE)))
        self.assertRefused(decide(protection_assessment=with_candidate), R.PROTECTION_INCLUDES_CANDIDATE_COPY)

    def test_protection_must_be_about_this_archive_and_read_the_surviving_scope(self):
        self.assertRefused(decide(protection_assessment=assessment(declared=(OTHER_SHA, SIZE),
                                  scopes=(("cold", ArtifactReadOutcome.READ, OTHER_SHA, SIZE),))),
                           R.PROTECTION_UNIT_MISMATCH)
        elsewhere = assessment(scopes=(("mirror", ArtifactReadOutcome.READ, SHA, SIZE),))
        self.assertRefused(decide(protection_assessment=elsewhere), R.PROTECTION_SURVIVING_COPY_NOT_READ)

    def test_stale_or_distant_evidence_refuses_on_either_side(self):
        old = Instant.parse("2026-10-17T10:00:00Z")  # two hours before: beyond the 1 h window
        late = Instant.parse("2026-10-17T14:00:00Z")
        for stamp in (old, late):
            self.assertRefused(decide(surviving_copy=surviving(verified_at=stamp)), R.EVIDENCE_STALE)
            self.assertRefused(decide(candidate_observation=observation(verified_at=stamp)), R.EVIDENCE_STALE)
            self.assertRefused(decide(canonical_verifications=(verification(verified_at=stamp),)), R.EVIDENCE_STALE)
            self.assertRefused(decide(protection_assessment=assessment(at=stamp)), R.EVIDENCE_STALE)
        edge = Instant.parse("2026-10-17T11:00:00Z")  # exactly one hour: still fresh
        self.assertEqual(decide(surviving_copy=surviving(verified_at=edge)).decision,
                         RetentionDeletionDecision.PERMITTED)

    def test_relocation_in_flight_refuses_a_terminal_phase_does_not(self):
        in_flight = RelocationEvidenceRef(relocation_id="relocation:sha256:" + "a" * 64,
                                          relocation_phase=RelocationPhase.STAGED)
        self.assertRefused(decide(relocation_evidence=in_flight), R.RELOCATION_IN_FLIGHT)
        done = RelocationEvidenceRef(relocation_id="relocation:sha256:" + "a" * 64,
                                     relocation_phase=RelocationPhase.CLEANED_UP)
        self.assertEqual(decide(relocation_evidence=done).decision, RetentionDeletionDecision.PERMITTED)

    def test_authority_is_required_and_has_no_default(self):
        with self.assertRaises(SourceCopyDisposalError):
            decide(decision_actor_or_authority="  ")
        with self.assertRaises(TypeError):
            evaluate_source_copy_disposal(policy=SourceCopyDisposalPolicyV1(), candidate=candidate(), decision_time=NOW)

    def test_malformed_evidence_is_rejected_not_interpreted(self):
        with self.assertRaises(SourceCopyDisposalError):
            surviving(content_sha256="not-a-hash")
        with self.assertRaises(SourceCopyDisposalError):
            verification(observed_content_sha256=SHA, observed_byte_size=None)
        with self.assertRaises(SourceCopyDisposalError):
            SourceCopyDisposalPolicyV1(max_evidence_age_seconds=0)


if __name__ == "__main__":
    unittest.main()
