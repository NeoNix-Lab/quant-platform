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


def artifact(role: str, content_hash: str = HASH_A, size_bytes: int = 100) -> ArtifactProtectionIdentity:
    return ArtifactProtectionIdentity(role=role, content_hash_sha256=content_hash, size_bytes=size_bytes)


def unit(
    *,
    artifacts,
    protected_support: str = "2024-01-15",
    extract_fingerprint_sha256: str | None = None,
) -> ProtectionUnitIdentity:
    return ProtectionUnitIdentity(
        source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1",
        reconstruction_contract_id="trade-v1",
        protected_support=protected_support,
        artifacts=artifacts,
        extract_fingerprint_sha256=extract_fingerprint_sha256,
    )


def matched_evidence(role: str, content_hash: str = HASH_A, size_bytes: int = 100) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(
        role=role,
        outcome=ArtifactReadOutcome.READ,
        observed_content_hash_sha256=content_hash,
        observed_size_bytes=size_bytes,
    )


def absent_evidence(role: str) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(role=role, outcome=ArtifactReadOutcome.ABSENT)


def unreadable_evidence(role: str) -> ArtifactVerificationEvidence:
    return ArtifactVerificationEvidence(role=role, outcome=ArtifactReadOutcome.UNREADABLE)


def pressure_decision(state: PressureState) -> PressureDecision:
    return PressureDecision(
        storage_root_id="hot",
        policy_definition_identity="pressure-policy-definition-v1:sha256:" + "0" * 64,
        as_of=VERIFIED_AT.to_datetime(),
        capacity_evidence={"available_bytes": 1},
        rate_evidence=None,
        time_to_full=TimeToFullEstimate(TimeToFullKind.NOT_APPLICABLE),
        state=state,
        restrictions=restrictions_for_state(state),
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
    # (structurally guaranteed: no such fields exist to vary; re-asserted here)
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
        )
        self.assertNotEqual(ProtectionState.PROTECTED, result.state)
        self.assertEqual(ProtectionState.LOST, result.state)

    # 9 & 10. fingerprint/canonical-output survival never substitutes for
    # the required source artifact itself; an absent required artifact is LOST.
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
            protection_obligation_unmet=True,
            obligation_detail="second copy overdue",
        )
        self.assertEqual(ProtectionState.LOST, still_lost.state)

    # 17. multi-failure precedence: LOST > CORRUPT > UNAVAILABLE > AT_RISK > PROTECTED
    def test_lost_wins_over_corrupt(self):
        u = unit(artifacts=(artifact("primary", HASH_A, 100), artifact("wal", HASH_B, 10)))
        result = assess_protection(
            u,
            (absent_evidence("primary"), matched_evidence("wal", HASH_C, 10)),
            verified_at=VERIFIED_AT,
            verifier_identity="k06-test-verifier",
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

    def test_missing_ambiguous_or_unexpected_evidence_is_refused(self):
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
        with self.assertRaises(ProtectionError):
            assess_protection(
                u,
                (
                    matched_evidence("primary", HASH_A, 100),
                    matched_evidence("primary", HASH_A, 100),  # duplicate role
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
            )
            self.assertFalse(result.delete_authorized)
        with self.assertRaises(TypeError):
            ProtectionAssessment(  # type: ignore[call-arg]
                protection_identity="x",
                source_semantics_id="x",
                mapping_id="x",
                reconstruction_contract_id="x",
                protected_support="x",
                verified_at=VERIFIED_AT,
                verifier_identity="x",
                artifact_results=(),
                state=ProtectionState.PROTECTED,
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


class ProtectionWriteAuthorizationV1Tests(unittest.TestCase):
    # 13. CRITICAL pressure + bounded safe protection write proven -> may proceed
    def test_critical_pressure_with_proven_safety_permits_write(self):
        result = authorize_protection_write(
            pressure=pressure_decision(PressureState.CRITICAL),
            is_safety_relevant=True,
            fits_evidenced_capacity=True,
        )
        self.assertEqual(ProtectionWriteDecision.PERMITTED, result.decision)
        self.assertFalse(result.delete_authorized)

    # 14. CRITICAL pressure + safety unproven -> deferred/refused + AT_RISK
    def test_critical_pressure_without_proof_is_deferred(self):
        unproven_safety = authorize_protection_write(
            pressure=pressure_decision(PressureState.CRITICAL),
            is_safety_relevant=False,
            fits_evidenced_capacity=True,
        )
        self.assertEqual(ProtectionWriteDecision.DEFERRED, unproven_safety.decision)
        self.assertEqual(ProtectionState.AT_RISK, unproven_safety.resulting_obligation_state)

        unproven_capacity = authorize_protection_write(
            pressure=pressure_decision(PressureState.CRITICAL),
            is_safety_relevant=True,
            fits_evidenced_capacity=False,
        )
        self.assertEqual(ProtectionWriteDecision.DEFERRED, unproven_capacity.decision)

    # 15. EXHAUSTED -> new write refused (even with proof), read-only verification unaffected
    def test_exhausted_pressure_always_refuses_new_writes(self):
        result = authorize_protection_write(
            pressure=pressure_decision(PressureState.EXHAUSTED),
            is_safety_relevant=True,
            fits_evidenced_capacity=True,
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

    def test_normal_and_pressure_states_permit_the_write(self):
        for state in (PressureState.NORMAL, PressureState.PRESSURE):
            result = authorize_protection_write(
                pressure=pressure_decision(state),
                is_safety_relevant=False,
                fits_evidenced_capacity=False,
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
            pressure=unavailable,
            is_safety_relevant=True,
            fits_evidenced_capacity=True,
        )
        self.assertNotEqual(ProtectionWriteDecision.PERMITTED, result.decision)
        self.assertEqual(ProtectionWriteDecision.DEFERRED, result.decision)
        self.assertIsNone(result.pressure_state)

    def test_authorize_protection_write_rejects_malformed_inputs(self):
        with self.assertRaises(ProtectionError):
            authorize_protection_write(
                pressure=object(),  # type: ignore[arg-type]
                is_safety_relevant=True,
                fits_evidenced_capacity=True,
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
