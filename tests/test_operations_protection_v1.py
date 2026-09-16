#!/usr/bin/env python3
"""K06 RAW/source protection v1 proof."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import inspect
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.operations import protection as protection_module  # noqa: E402
from quant_platform.operations.protection import (  # noqa: E402
    AcceptedReconstructionContract,
    ArtifactAssessmentResult,
    ArtifactProtectionIdentity,
    ArtifactReadOutcome,
    ArtifactVerificationEvidence,
    ProtectionAssessment,
    ProtectionError,
    ProtectionState,
    ProtectionUnitIdentity,
    ProtectionWriteAuthorization,
    ProtectionWriteDecision,
    SafetyRelevanceAssertion,
    assess_protection,
    authorize_protection_write,
)
from quant_platform.operations.pressure import (  # noqa: E402
    PressureDecision,
    PressureDecisionUnavailable,
    PressureDecisionUnavailableReason,
    PressureState,
    TimeToFullEstimate,
    TimeToFullKind,
    restrictions_for_state,
)
from quant_platform.source_adapters.bybit_historical import (  # noqa: E402
    BybitHistoricalExtractAccumulator,
    BybitHistoricalSourceError,
    BybitHistoricalTradeRow,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
VERIFIED_AT = Instant.parse("2024-01-16T00:00:00Z")
ACCEPTED_AT = Instant.parse("2024-01-01T00:00:00Z")
ACCEPTING_AUTHORITY = "adr:k06-test-authority-v1"
SOURCE_SEMANTICS_ID = "bybit-public-trades-sqlite-v1"
MAPPING_ID = "bybit-sqlite-day-extract-v1"
RECONSTRUCTION_CONTRACT_ID = "trade-v1"
INSTANCE_HOT = "hot"
INSTANCE_MIRROR = "mirror"
PROTECTION_ID = "protection-unit-identity-v1:sha256:" + "1" * 64


def artifact(role: str, content_hash: str = HASH_A, size_bytes: int = 100) -> ArtifactProtectionIdentity:
    return ArtifactProtectionIdentity(role=role, content_hash_sha256=content_hash, size_bytes=size_bytes)


def contract(
    roles,
    *,
    source_semantics_id: str = SOURCE_SEMANTICS_ID,
    mapping_id: str = MAPPING_ID,
    reconstruction_contract_id: str = RECONSTRUCTION_CONTRACT_ID,
    accepting_authority_id: str = ACCEPTING_AUTHORITY,
    accepted_at: Instant = ACCEPTED_AT,
) -> AcceptedReconstructionContract:
    return AcceptedReconstructionContract(
        source_semantics_id=source_semantics_id,
        mapping_id=mapping_id,
        reconstruction_contract_id=reconstruction_contract_id,
        required_roles=tuple(roles),
        accepting_authority_id=accepting_authority_id,
        accepted_at=accepted_at,
    )


def unit(
    *,
    artifacts,
    protected_support: str = "2024-01-15",
    extract_fingerprint_sha256: str | None = None,
    accepted_contract: AcceptedReconstructionContract | None = None,
) -> ProtectionUnitIdentity:
    return ProtectionUnitIdentity(
        source_semantics_id=SOURCE_SEMANTICS_ID,
        mapping_id=MAPPING_ID,
        reconstruction_contract_id=RECONSTRUCTION_CONTRACT_ID,
        protected_support=protected_support,
        artifacts=artifacts,
        accepted_contract=accepted_contract or contract([a.role for a in artifacts]),
        extract_fingerprint_sha256=extract_fingerprint_sha256,
    )


def matched_evidence(
    role: str, content_hash: str = HASH_A, size_bytes: int = 100, *, instance_scope: str = INSTANCE_HOT
) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(
        role=role,
        instance_scope=instance_scope,
        outcome=ArtifactReadOutcome.READ,
        observed_content_hash_sha256=content_hash,
        observed_size_bytes=size_bytes,
    )


def absent_evidence(role: str, *, instance_scope: str = INSTANCE_HOT) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(role=role, instance_scope=instance_scope, outcome=ArtifactReadOutcome.ABSENT)


def unreadable_evidence(role: str, *, instance_scope: str = INSTANCE_HOT) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(
        role=role, instance_scope=instance_scope, outcome=ArtifactReadOutcome.UNREADABLE
    )


def artifact_result(
    role: str, content_hash: str = HASH_A, size_bytes: int = 100, *, exhaustive: bool = False
) -> ArtifactAssessmentResult:
    return ArtifactAssessmentResult(
        declared=artifact(role, content_hash, size_bytes),
        evidence=(matched_evidence(role, content_hash, size_bytes),),
        local_instances_exhaustively_checked=exhaustive,
    )


def pressure_decision(state: PressureState, *, available_bytes: int = 1_000_000) -> PressureDecision:
    return PressureDecision(
        storage_root_id="hot",
        policy_definition_identity="pressure-policy-definition-v1:sha256:" + "0" * 64,
        as_of=VERIFIED_AT.to_datetime(),
        capacity_evidence={"available_bytes": available_bytes},
        rate_evidence=None,
        time_to_full=TimeToFullEstimate(TimeToFullKind.NOT_APPLICABLE),
        state=state,
        restrictions=restrictions_for_state(state),
    )


def safety(protection_identity: str = PROTECTION_ID) -> SafetyRelevanceAssertion:
    return SafetyRelevanceAssertion(
        protection_identity=protection_identity,
        asserting_authority_id=ACCEPTING_AUTHORITY,
        asserted_at=ACCEPTED_AT,
        rationale="protects unique non-reconstructible source evidence",
    )


class ProtectionUnitIdentityV1Tests(unittest.TestCase):
    # 1. same semantic inputs -> same identity; no path/root/host field exists at all
    def test_identity_has_no_path_root_or_host_fields(self):
        field_names = {f.name for f in fields(ArtifactProtectionIdentity)} | {
            f.name for f in fields(ProtectionUnitIdentity)
        }
        for forbidden in ("path", "root", "host", "mount", "inode", "mtime", "ctime", "device"):
            self.assertFalse(
                any(forbidden in name for name in field_names),
                f"unexpected operational field containing {forbidden!r}: {field_names}",
            )
        one = unit(artifacts=(artifact("primary"),))
        two = unit(artifacts=(artifact("primary"),))
        self.assertEqual(one.protection_identity, two.protection_identity)

    # 3. mtime/inode drift only, exact content -> semantic identity unchanged
    def test_no_operational_metadata_can_perturb_identity(self):
        a = unit(artifacts=(artifact("primary", HASH_A, 100),))
        b = unit(artifacts=(artifact("primary", HASH_A, 100),))
        self.assertEqual(a.protection_identity, b.protection_identity)

    # 4. multi-artifact descriptors in different enumeration order -> same identity
    def test_artifact_enumeration_order_does_not_affect_identity(self):
        primary = artifact("primary", HASH_A, 100)
        wal = artifact("wal", HASH_B, 10)
        forward = unit(artifacts=(primary, wal))
        backward = unit(artifacts=(wal, primary))
        self.assertEqual(forward.protection_identity, backward.protection_identity)
        self.assertEqual(
            [a.role for a in forward.artifacts], [a.role for a in backward.artifacts]
        )

    # 5. same hashes, different role binding -> different identity
    def test_same_hashes_different_role_binding_changes_identity(self):
        straight = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        swapped = unit(artifacts=(artifact("primary", HASH_B, 10), artifact("wal", HASH_A, 100)))
        self.assertNotEqual(straight.protection_identity, swapped.protection_identity)

    # 6. duplicate artifact roles -> explicit refusal
    def test_duplicate_artifact_roles_are_refused(self):
        with self.assertRaises(ProtectionError):
            unit(artifacts=(artifact("primary", HASH_A, 100), artifact("primary", HASH_B, 10)))

    def test_malformed_role_and_hash_are_refused(self):
        with self.assertRaises(ProtectionError):
            artifact("Primary")  # not canonical lowercase role
        with self.assertRaises(ProtectionError):
            artifact("primary", "not-a-hash", 100)
        with self.assertRaises(ProtectionError):
            artifact("primary", HASH_A, -1)

    def test_unit_requires_accepted_contract_bound_to_the_same_triple(self):
        with self.assertRaises(ProtectionError):
            unit(
                artifacts=(artifact("primary"),),
                accepted_contract=contract(["primary"], reconstruction_contract_id="other-contract-v1"),
            )
        with self.assertRaises(ProtectionError):
            unit(
                artifacts=(artifact("primary"),),
                accepted_contract=contract(["primary"], source_semantics_id="unsupported-source"),
            )

    def test_unit_requires_exact_role_completeness_against_the_accepted_contract(self):
        # accepted contract wants "wal" too; unit only declares "primary" -> missing.
        with self.assertRaises(ProtectionError):
            unit(artifacts=(artifact("primary"),), accepted_contract=contract(["primary", "wal"]))
        # accepted contract only wants "primary"; unit declares an extra "manifest" -> unexpected.
        with self.assertRaises(ProtectionError):
            unit(
                artifacts=(artifact("primary"), artifact("manifest", HASH_B, 1)),
                accepted_contract=contract(["primary"]),
            )

    def test_accepted_contract_rejects_malformed_roles(self):
        with self.assertRaises(ProtectionError):
            contract([])
        with self.assertRaises(ProtectionError):
            contract(["primary", "primary"])

    # Review finding: acceptance must be attributed, not an anonymous string tuple.
    def test_accepted_contract_requires_attributed_provenance(self):
        with self.assertRaises(ProtectionError):
            AcceptedReconstructionContract(
                source_semantics_id=SOURCE_SEMANTICS_ID,
                mapping_id=MAPPING_ID,
                reconstruction_contract_id=RECONSTRUCTION_CONTRACT_ID,
                required_roles=("primary",),
                accepting_authority_id="",
                accepted_at=ACCEPTED_AT,
            )
        with self.assertRaises(ProtectionError):
            AcceptedReconstructionContract(
                source_semantics_id=SOURCE_SEMANTICS_ID,
                mapping_id=MAPPING_ID,
                reconstruction_contract_id=RECONSTRUCTION_CONTRACT_ID,
                required_roles=("primary",),
                accepting_authority_id=ACCEPTING_AUTHORITY,
                accepted_at="2024-01-01T00:00:00Z",  # not an Instant
            )

    def test_accepted_contract_provenance_is_surfaced_but_not_identity_bearing(self):
        first = unit(
            artifacts=(artifact("primary"),),
            accepted_contract=contract(["primary"], accepting_authority_id="authority-a"),
        )
        second = unit(
            artifacts=(artifact("primary"),),
            accepted_contract=contract(["primary"], accepting_authority_id="authority-b"),
        )
        # Different attributed authorities do not change the reconstruction claim's identity...
        self.assertEqual(first.protection_identity, second.protection_identity)
        # ...but the attribution itself remains inspectable for audit.
        self.assertEqual("authority-a", first.accepted_contract.accepting_authority_id)
        self.assertEqual("authority-b", second.accepted_contract.accepting_authority_id)


class ProtectionForgeryResistanceV1Tests(unittest.TestCase):
    """Review finding: constructors must recompute, not trust, evaluator state."""

    def test_artifact_assessment_result_state_cannot_be_supplied(self):
        with self.assertRaises(TypeError):
            ArtifactAssessmentResult(  # type: ignore[call-arg]
                declared=artifact("primary"),
                evidence=(matched_evidence("primary"),),
                local_instances_exhaustively_checked=False,
                state=ProtectionState.PROTECTED,
            )

    def test_artifact_assessment_result_recomputes_state_from_evidence(self):
        result = ArtifactAssessmentResult(
            declared=artifact("primary", HASH_A, 100),
            evidence=(matched_evidence("primary", HASH_A, 100),),
            local_instances_exhaustively_checked=False,
        )
        self.assertEqual(ProtectionState.PROTECTED, result.state)

        forged_mismatch = ArtifactAssessmentResult(
            declared=artifact("primary", HASH_A, 100),
            evidence=(matched_evidence("primary", HASH_C, 100),),
            local_instances_exhaustively_checked=False,
        )
        self.assertEqual(ProtectionState.CORRUPT, forged_mismatch.state)

    def test_artifact_assessment_result_rejects_role_mismatched_evidence(self):
        with self.assertRaises(ProtectionError):
            ArtifactAssessmentResult(
                declared=artifact("primary"),
                evidence=(matched_evidence("wal"),),
                local_instances_exhaustively_checked=False,
            )

    def test_protection_assessment_state_cannot_be_supplied(self):
        u = unit(artifacts=(artifact("primary"),))
        with self.assertRaises(TypeError):
            ProtectionAssessment(  # type: ignore[call-arg]
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(artifact_result("primary"),),
                state=ProtectionState.PROTECTED,
            )

    def test_protection_assessment_rejects_empty_artifact_results(self):
        # The literal forgery attempt from the review finding.
        u = unit(artifacts=(artifact("primary"),))
        with self.assertRaises(ProtectionError):
            ProtectionAssessment(
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(),
            )

    def test_protection_assessment_recomputes_state_from_artifact_results(self):
        u = unit(artifacts=(artifact("primary"),))
        lost_result = ArtifactAssessmentResult(
            declared=artifact("primary"),
            evidence=(absent_evidence("primary"),),
            local_instances_exhaustively_checked=True,
        )
        assessment = ProtectionAssessment(
            unit=u,
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            artifact_results=(lost_result,),
        )
        # Cannot be forged as PROTECTED merely by omitting state: it is
        # recomputed from artifact_results regardless of caller intent.
        self.assertEqual(ProtectionState.LOST, assessment.state)

    # Review finding: results must describe the SAME artifacts the unit
    # itself declares -- not merely be internally self-consistent.
    def test_protection_assessment_rejects_results_for_an_unrelated_unit(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        with self.assertRaises(ProtectionError):
            ProtectionAssessment(
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(artifact_result("primary", HASH_A, 100),),  # missing "wal"
            )

    def test_protection_assessment_rejects_mismatched_declared_descriptor(self):
        # The unit declares "primary" bound to HASH_A/100. A caller cannot
        # bind an internally-consistent-but-different artifact ("primary"
        # bound to HASH_C) to this unit's real, unrelated identity.
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        forged_result = artifact_result("primary", HASH_C, 100)  # self-consistent, wrong bytes
        with self.assertRaises(ProtectionError):
            ProtectionAssessment(
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(forged_result,),
            )

    def test_protection_assessment_rejects_duplicate_result_roles(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        with self.assertRaises(ProtectionError):
            ProtectionAssessment(
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(artifact_result("primary", HASH_A, 100), artifact_result("primary", HASH_A, 100)),
            )


class ProtectionAssessmentV1Tests(unittest.TestCase):
    # 11. exact protected replay -> positive reconstruction proof
    def test_all_matched_evidence_is_protected(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        result = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100), matched_evidence("wal", HASH_B, 10)),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.PROTECTED, result.state)
        self.assertFalse(result.delete_authorized)
        self.assertEqual(u.protection_identity, result.protection_identity)

    # 2. same path, changed bytes -> CORRUPT, never PROTECTED
    def test_changed_content_is_corrupt_never_protected(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (matched_evidence("primary", HASH_C, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.CORRUPT, result.state)

    def test_changed_size_is_corrupt(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 999),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.CORRUPT, result.state)

    # 7. incomplete multi-artifact source -> not PROTECTED
    def test_incomplete_multi_artifact_source_is_not_protected(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        result = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100), absent_evidence("wal")),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertNotEqual(ProtectionState.PROTECTED, result.state)
        self.assertEqual(ProtectionState.LOST, result.state)

    # 9 & 10. fingerprint/canonical-output survival never substitutes for
    # the required source artifact itself; an exhaustively-absent required
    # artifact is LOST.
    def test_retained_fingerprint_does_not_grant_protection_when_source_is_absent(self):
        accumulator = BybitHistoricalExtractAccumulator()
        row = BybitHistoricalTradeRow(
            category="linear",
            symbol="BTCUSDT",
            trade_id="1",
            trade_time_ms=0,
            trade_time_utc="2024-01-15T00:00:00Z",
            side="Buy",
            size="1",
            price="1",
        )
        accumulator.observe(row)
        accumulator._mark_source_exhausted()
        extract_evidence = accumulator.evidence("2024-01-15", 0, 1)

        u = unit(
            artifacts=(artifact("primary", HASH_A, 100),),
            extract_fingerprint_sha256=extract_evidence.source_fingerprint_sha256,
        )
        # Neither "canonical output survives" nor "fingerprint retained" is
        # even representable as verification input; only artifact evidence is.
        result = assess_protection(
            u,
            (absent_evidence("primary"),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertEqual(ProtectionState.LOST, result.state)
        self.assertEqual(extract_evidence.source_fingerprint_sha256, u.extract_fingerprint_sha256)

    # 8. truncated A07 extract -> cannot produce completed extract proof (CREDIT A07)
    def test_truncated_extract_cannot_produce_completed_evidence(self):
        accumulator = BybitHistoricalExtractAccumulator()
        row = BybitHistoricalTradeRow(
            category="linear",
            symbol="BTCUSDT",
            trade_id="1",
            trade_time_ms=0,
            trade_time_utc="2024-01-15T00:00:00Z",
            side="Buy",
            size="1",
            price="1",
        )
        accumulator.observe(row)
        with self.assertRaises(BybitHistoricalSourceError):
            accumulator.evidence("2024-01-15", 0, 1)

    def test_unreadable_evidence_is_unavailable(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (unreadable_evidence("primary"),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.UNAVAILABLE, result.state)

    def test_unmet_protection_obligation_is_at_risk_only_when_otherwise_verified(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        at_risk = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            protection_obligation_unmet=True,
            obligation_detail="second copy overdue",
        )
        self.assertEqual(ProtectionState.AT_RISK, at_risk.state)
        self.assertEqual("second copy overdue", at_risk.obligation_detail)

        # An unmet obligation never masks a worse underlying failure.
        still_lost = assess_protection(
            u,
            (absent_evidence("primary"),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
            protection_obligation_unmet=True,
            obligation_detail="second copy overdue",
        )
        self.assertEqual(ProtectionState.LOST, still_lost.state)

    # multi-failure precedence: LOST > CORRUPT > UNAVAILABLE > AT_RISK > PROTECTED
    def test_lost_wins_over_corrupt(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        result = assess_protection(
            u,
            (absent_evidence("primary"), matched_evidence("wal", HASH_C, 10)),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertEqual(ProtectionState.LOST, result.state)

    def test_corrupt_wins_over_unavailable(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        result = assess_protection(
            u,
            (unreadable_evidence("primary"), matched_evidence("wal", HASH_C, 10)),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.CORRUPT, result.state)

    # 12. support D protected while D+1 absent -> no implied D+1 protection
    def test_protection_is_bounded_to_its_own_declared_support(self):
        day_15 = unit(artifacts=(artifact("primary", HASH_A, 100),), protected_support="2024-01-15")
        day_16 = unit(artifacts=(artifact("primary", HASH_A, 100),), protected_support="2024-01-16")
        self.assertNotEqual(day_15.protection_identity, day_16.protection_identity)

        protected_15 = assess_protection(
            day_15,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        lost_16 = assess_protection(
            day_16,
            (absent_evidence("primary"),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertEqual(ProtectionState.PROTECTED, protected_15.state)
        self.assertEqual(ProtectionState.LOST, lost_16.state)

    # 19. repeated identical evidence + identical verified_at -> deterministic identity/result
    def test_repeated_identical_evidence_is_deterministic(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        first = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        second = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(first.assessment_identity, second.assessment_identity)
        self.assertEqual(first.state, second.state)

    def test_missing_or_unexpected_role_evidence_is_refused(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        with self.assertRaises(ProtectionError):
            assess_protection(
                u,
                (matched_evidence("primary", HASH_A, 100),),  # missing "wal"
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
            )
        with self.assertRaises(ProtectionError):
            assess_protection(
                u,
                (
                    matched_evidence("primary", HASH_A, 100),
                    matched_evidence("wal", HASH_B, 10),
                    matched_evidence("manifest", HASH_C, 1),  # undeclared role
                ),
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
            )

    def test_verified_at_must_be_a_canonical_instant(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        with self.assertRaises(ProtectionError):
            assess_protection(
                u,
                (matched_evidence("primary", HASH_A, 100),),
                verified_at="2024-01-16T00:00:00Z",  # not an Instant
                verifier_identity="k06-test-verifier",
            )

    def test_unreadable_and_absent_evidence_cannot_carry_content_identity(self):
        with self.assertRaises(ProtectionError):
            ArtifactVerificationEvidence(
                role="primary",
                instance_scope=INSTANCE_HOT,
                outcome=ArtifactReadOutcome.ABSENT,
                observed_content_hash_sha256=HASH_A,
            )

    # 20. implicit host-clock dependence absent from the evaluator
    def test_evaluator_never_reads_an_implicit_host_clock(self):
        source = inspect.getsource(protection_module)
        self.assertNotIn("datetime.now(", source)
        self.assertNotIn("time.time(", source)
        signature = inspect.signature(assess_protection)
        self.assertIs(inspect.Parameter.empty, signature.parameters["verified_at"].default)

    # 18. attempt to remove last verified source -> no deletion authorization
    def test_delete_is_never_authorized(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        for evidence in (
            matched_evidence("primary", HASH_A, 100),
            absent_evidence("primary"),
            unreadable_evidence("primary"),
        ):
            result = assess_protection(
                u,
                (evidence,),
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                local_instances_exhaustively_checked=True,
            )
            self.assertFalse(result.delete_authorized)
        with self.assertRaises(TypeError):
            ProtectionAssessment(  # type: ignore[call-arg]
                unit=u,
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
                artifact_results=(artifact_result("primary", HASH_A, 100),),
                delete_authorized=True,
            )

    # 17. local K06 protection valid while K08 independent restore remains unproven
    def test_protection_never_references_backup_or_restore_evidence(self):
        for cls in (ProtectionAssessment, ArtifactAssessmentResult, ProtectionUnitIdentity):
            field_names = {f.name for f in fields(cls)}
            for forbidden in ("backup", "restore", "replica", "second_copy"):
                self.assertFalse(
                    any(forbidden in name for name in field_names),
                    f"{cls.__name__} unexpectedly couples to backup/restore: {field_names}",
                )
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        protected = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.PROTECTED, protected.state)


class ProtectionInstanceScopeV1Tests(unittest.TestCase):
    """Review finding: a single ABSENT observation must not overclaim LOST."""

    def test_absent_at_one_instance_with_match_at_another_is_protected(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (
                absent_evidence("primary", instance_scope=INSTANCE_HOT),
                matched_evidence("primary", HASH_A, 100, instance_scope=INSTANCE_MIRROR),
            ),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.PROTECTED, result.state)

    def test_absent_everywhere_without_exhaustive_attestation_is_unavailable_not_lost(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (
                absent_evidence("primary", instance_scope=INSTANCE_HOT),
                absent_evidence("primary", instance_scope=INSTANCE_MIRROR),
            ),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            # local_instances_exhaustively_checked defaults to False.
        )
        self.assertEqual(ProtectionState.UNAVAILABLE, result.state)
        self.assertNotEqual(ProtectionState.LOST, result.state)

    def test_absent_everywhere_with_exhaustive_attestation_is_lost(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (
                absent_evidence("primary", instance_scope=INSTANCE_HOT),
                absent_evidence("primary", instance_scope=INSTANCE_MIRROR),
            ),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertEqual(ProtectionState.LOST, result.state)

    def test_single_unexhausted_absence_is_unavailable_not_lost(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (absent_evidence("primary"),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.UNAVAILABLE, result.state)

    def test_corrupt_reading_beats_exhaustively_confirmed_absence_elsewhere(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        result = assess_protection(
            u,
            (
                matched_evidence("primary", HASH_C, 100, instance_scope=INSTANCE_HOT),  # wrong bytes
                absent_evidence("primary", instance_scope=INSTANCE_MIRROR),
            ),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
            local_instances_exhaustively_checked=True,
        )
        self.assertEqual(ProtectionState.CORRUPT, result.state)

    def test_duplicate_instance_scope_for_same_role_is_refused(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        with self.assertRaises(ProtectionError):
            assess_protection(
                u,
                (
                    absent_evidence("primary", instance_scope=INSTANCE_HOT),
                    matched_evidence("primary", HASH_A, 100, instance_scope=INSTANCE_HOT),
                ),
                verified_at=VERIFIED_AT,
                verifier_identity="k06-test-verifier",
            )


class ProtectionWriteAuthorizationV1Tests(unittest.TestCase):
    PROTECTION_ID = PROTECTION_ID

    # 13. CRITICAL pressure + bounded safe protection write proven -> may proceed
    def test_critical_pressure_with_proven_safety_permits_write(self):
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=100,
            pressure=pressure_decision(PressureState.CRITICAL, available_bytes=1_000),
            safety_assertion=safety(self.PROTECTION_ID),
        )
        self.assertEqual(ProtectionWriteDecision.PERMITTED, result.decision)
        self.assertTrue(result.is_safety_relevant)
        self.assertTrue(result.fits_evidenced_capacity)
        self.assertFalse(result.delete_authorized)

    # 14. CRITICAL pressure + safety unproven -> deferred + AT_RISK
    def test_critical_pressure_without_safety_proof_is_deferred(self):
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=100,
            pressure=pressure_decision(PressureState.CRITICAL, available_bytes=1_000),
            safety_assertion=None,
        )
        self.assertEqual(ProtectionWriteDecision.DEFERRED, result.decision)
        self.assertFalse(result.is_safety_relevant)
        self.assertEqual(ProtectionState.AT_RISK, result.resulting_obligation_state)

    # Review finding: a safety claim cannot be reused for an unrelated action.
    def test_safety_assertion_must_be_bound_to_the_same_protection_identity(self):
        with self.assertRaises(ProtectionError):
            authorize_protection_write(
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=100,
                pressure=pressure_decision(PressureState.CRITICAL, available_bytes=1_000),
                safety_assertion=safety("protection-unit-identity-v1:sha256:" + "9" * 64),
            )

    def test_safety_assertion_requires_attributed_provenance(self):
        with self.assertRaises(ProtectionError):
            SafetyRelevanceAssertion(
                protection_identity=self.PROTECTION_ID,
                asserting_authority_id="",
                asserted_at=ACCEPTED_AT,
                rationale="x",
            )
        with self.assertRaises(ProtectionError):
            SafetyRelevanceAssertion(
                protection_identity=self.PROTECTION_ID,
                asserting_authority_id=ACCEPTING_AUTHORITY,
                asserted_at="2024-01-01T00:00:00Z",  # not an Instant
                rationale="x",
            )

    # Review finding: capacity fit is computed from real pressure evidence,
    # never trusted as a bare caller assertion.
    def test_critical_pressure_with_insufficient_evidenced_capacity_is_deferred(self):
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=1_000,
            pressure=pressure_decision(PressureState.CRITICAL, available_bytes=50),
            safety_assertion=safety(self.PROTECTION_ID),
        )
        self.assertEqual(ProtectionWriteDecision.DEFERRED, result.decision)
        self.assertFalse(result.fits_evidenced_capacity)
        self.assertEqual(ProtectionState.AT_RISK, result.resulting_obligation_state)

    # 15. EXHAUSTED -> new write refused (even with proof), read-only verification unaffected
    def test_exhausted_pressure_always_refuses_new_writes(self):
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=1,
            pressure=pressure_decision(PressureState.EXHAUSTED, available_bytes=1_000_000),
            safety_assertion=safety(self.PROTECTION_ID),
        )
        self.assertEqual(ProtectionWriteDecision.REFUSED, result.decision)
        self.assertEqual(ProtectionState.AT_RISK, result.resulting_obligation_state)

        # assess_protection takes no pressure input at all: read-only
        # verification is structurally unaffected by any pressure state.
        u = unit(artifacts=(artifact("primary", HASH_A, 100),))
        verified = assess_protection(
            u,
            (matched_evidence("primary", HASH_A, 100),),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
        )
        self.assertEqual(ProtectionState.PROTECTED, verified.state)

    def test_normal_and_pressure_states_permit_the_write_regardless_of_capacity(self):
        for state in (PressureState.NORMAL, PressureState.PRESSURE):
            result = authorize_protection_write(
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=1_000,
                pressure=pressure_decision(state, available_bytes=1),  # would not fit
                safety_assertion=None,
            )
            self.assertEqual(ProtectionWriteDecision.PERMITTED, result.decision)

    # 16. K05 unavailable -> never treated as NORMAL
    def test_unavailable_pressure_is_never_treated_as_normal(self):
        unavailable = PressureDecisionUnavailable(
            reason=PressureDecisionUnavailableReason.MISSING_CAPACITY,
            storage_root_id="hot",
            policy_definition_identity=None,
            as_of=None,
            evidence={},
        )
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=100,
            pressure=unavailable,
            safety_assertion=safety(self.PROTECTION_ID),
        )
        self.assertNotEqual(ProtectionWriteDecision.PERMITTED, result.decision)
        self.assertEqual(ProtectionWriteDecision.DEFERRED, result.decision)
        self.assertIsNone(result.pressure_state)
        self.assertIsNone(result.fits_evidenced_capacity)

    # Review finding: the decision must bind protection/action identity, write
    # size, pressure evidence identity and applicable restrictions.
    def test_write_authorization_binds_protection_and_pressure_evidence(self):
        pressure = pressure_decision(PressureState.CRITICAL, available_bytes=1_000)
        result = authorize_protection_write(
            protection_identity=self.PROTECTION_ID,
            write_size_bytes=100,
            pressure=pressure,
            safety_assertion=safety(self.PROTECTION_ID),
        )
        self.assertEqual(self.PROTECTION_ID, result.protection_identity)
        self.assertEqual(100, result.write_size_bytes)
        self.assertEqual(pressure.decision_identity, result.pressure_decision_identity)
        self.assertEqual(pressure.restrictions, result.restrictions)
        self.assertEqual(PressureState.CRITICAL, result.pressure_state)

    # Review finding: no derived field of the authorization can be supplied
    # directly, so a caller cannot construct an arbitrary PERMITTED record.
    def test_write_authorization_derived_fields_cannot_be_supplied(self):
        with self.assertRaises(TypeError):
            ProtectionWriteAuthorization(  # type: ignore[call-arg]
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=1,
                pressure=pressure_decision(PressureState.EXHAUSTED),
                decision=ProtectionWriteDecision.PERMITTED,
            )
        with self.assertRaises(TypeError):
            ProtectionWriteAuthorization(  # type: ignore[call-arg]
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=1,
                pressure=pressure_decision(PressureState.NORMAL),
                pressure_state=PressureState.NORMAL,
            )

    def test_authorize_protection_write_rejects_malformed_inputs(self):
        with self.assertRaises(ProtectionError):
            authorize_protection_write(
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=1,
                pressure=object(),  # type: ignore[arg-type]
                safety_assertion=None,
            )
        with self.assertRaises(ProtectionError):
            authorize_protection_write(
                protection_identity="",
                write_size_bytes=1,
                pressure=pressure_decision(PressureState.NORMAL),
                safety_assertion=None,
            )
        with self.assertRaises(ProtectionError):
            authorize_protection_write(
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=-1,
                pressure=pressure_decision(PressureState.NORMAL),
                safety_assertion=None,
            )
        with self.assertRaises(ProtectionError):
            malformed_pressure = pressure_decision(PressureState.CRITICAL)
            object.__setattr__(malformed_pressure, "capacity_evidence", {})
            authorize_protection_write(
                protection_identity=self.PROTECTION_ID,
                write_size_bytes=1,
                pressure=malformed_pressure,
                safety_assertion=None,
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
