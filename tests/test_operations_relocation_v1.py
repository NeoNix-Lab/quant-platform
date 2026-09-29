from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant  # noqa: E402
from quant_platform.operations.pressure import (  # noqa: E402
    PressureDecision,
    PressureDecisionUnavailable,
    PressureDecisionUnavailableReason,
    PressureState,
    TimeToFullEstimate,
    TimeToFullKind,
    restrictions_for_state,
)
from quant_platform.operations.protection import (  # noqa: E402
    AcceptedReconstructionContract,
    ArtifactAssessmentResult,
    ArtifactProtectionIdentity,
    ArtifactReadOutcome,
    ArtifactVerificationEvidence,
    ProtectionAssessment,
    ProtectionObligationEvidence,
    ProtectionState,
    ProtectionUnitIdentity,
)
from quant_platform.operations.relocation import (  # noqa: E402
    RelocationDomainMismatch,
    RelocationPhase,
    RelocationPhaseError,
    RelocationRefused,
    advance_relocation,
    plan_relocation,
    verify_target_identity,
)


NOW = Instant.parse("2026-01-01T00:00:00Z")
HASH = "a" * 64


def _identity() -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")


def _pressure(state: PressureState = PressureState.NORMAL, *, root: str = "cold") -> PressureDecision:
    return PressureDecision(
        storage_root_id=root,
        policy_definition_identity="pressure-policy-definition-v1:sha256:" + "1" * 64,
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        capacity_evidence={"evidence": "test"},
        rate_evidence=None,
        time_to_full=TimeToFullEstimate(TimeToFullKind.NOT_APPLICABLE),
        state=state,
        restrictions=restrictions_for_state(state),
    )


def _protection(*, at_risk: bool = False) -> ProtectionAssessment:
    artifact = ArtifactProtectionIdentity("data", HASH, 12)
    contract = AcceptedReconstructionContract(
        source_semantics_id="bybit-public-trades-v1",
        mapping_id="canonical-trades-v1",
        reconstruction_contract_id="reconstruct-canonical-trades-v1",
        required_roles=("data",),
        accepting_authority_id="adr-0048",
        accepted_at=NOW,
    )
    unit = ProtectionUnitIdentity(
        source_semantics_id=contract.source_semantics_id,
        mapping_id=contract.mapping_id,
        reconstruction_contract_id=contract.reconstruction_contract_id,
        protected_support="BTCUSDT/dt=2026-01-01",
        artifacts=(artifact,),
        accepted_contract=contract,
    )
    evidence = ArtifactVerificationEvidence(
        role="data",
        instance_scope="hot",
        outcome=ArtifactReadOutcome.READ,
        observed_content_hash_sha256=HASH,
        observed_size_bytes=12,
    )
    result = ArtifactAssessmentResult(
        declared=artifact,
        evidence=(evidence,),
        local_instances_exhaustively_checked=True,
    )
    obligation = None
    if at_risk:
        obligation = ProtectionObligationEvidence(
            protection_identity=unit.protection_identity,
            obligation_id="refresh-check",
            requesting_authority_id="k07-test",
            identified_at=NOW,
            detail="still known but carrying a local refresh obligation",
        )
    return ProtectionAssessment(
        unit=unit,
        verified_at=NOW,
        verifier_identity="k07-test",
        artifact_results=(result,),
        protection_obligation=obligation,
    )


def _plan(**overrides):
    params = {
        "dataset_identity": _identity(),
        "catalog_partition_id": "11111111-1111-4111-8111-111111111111",
        "partition_key": "dt=2026-01-01",
        "revision": 1,
        "source_storage_root_id": "hot",
        "target_storage_root_id": "cold",
        "dataset_rel_root": "canonical/trades/bybit/BTCUSDT/trade-v1",
        "rel_path": "dt=2026-01-01/part-000.parquet",
        "content_sha256": HASH,
        "byte_size": 12,
        "partition_state": "valid",
        "target_pressure": _pressure(),
        "protection": _protection(),
    }
    params.update(overrides)
    return plan_relocation(**params)


class StorageRelocationDomainTests(unittest.TestCase):
    def test_plan_admits_valid_closed_degraded_under_normal_or_pressure_and_known_protection(self):
        for state in ("valid", "closed", "degraded"):
            for pressure_state in (PressureState.NORMAL, PressureState.PRESSURE):
                with self.subTest(state=state, pressure=pressure_state):
                    plan = _plan(
                        partition_state=state,
                        target_pressure=_pressure(pressure_state),
                        protection=_protection(at_risk=True),
                    )
                    self.assertEqual(plan.target_storage_root_id, "cold")

    def test_plan_refuses_ineligible_lifecycle_or_pressure_or_protection(self):
        with self.assertRaises(RelocationRefused):
            _plan(partition_state="writing")
        with self.assertRaises(RelocationRefused):
            _plan(target_pressure=_pressure(PressureState.CRITICAL))
        with self.assertRaises(RelocationRefused):
            _plan(
                target_pressure=PressureDecisionUnavailable(
                    reason=PressureDecisionUnavailableReason.MISSING_CAPACITY,
                    storage_root_id="cold",
                    policy_definition_identity=None,
                    as_of=None,
                    evidence={},
                )
            )

    def test_plan_requires_pressure_to_match_target_root(self):
        with self.assertRaises(RelocationDomainMismatch):
            _plan(target_pressure=_pressure(root="deepcold"))

    def test_target_identity_verification_is_exact(self):
        self.assertTrue(verify_target_identity(HASH, 12, HASH, 12))
        self.assertFalse(verify_target_identity(HASH, 12, "b" * 64, 12))
        self.assertFalse(verify_target_identity(HASH, 12, HASH, 13))
        self.assertFalse(verify_target_identity(HASH, 12, "not-a-hash", 12))

    def test_phase_progression_is_ordered_and_domain_bound(self):
        plan = _plan()
        planned = plan.planned_record(updated_at=NOW)
        staged = planned.with_phase(
            RelocationPhase.STAGED,
            updated_at=NOW,
            target_content_sha256=HASH,
            target_size_bytes=12,
        )
        verified = staged.with_phase(RelocationPhase.VERIFIED, updated_at=NOW)
        switched = verified.with_phase(RelocationPhase.SWITCHED, updated_at=NOW)
        cleaned = switched.with_phase(RelocationPhase.CLEANED_UP, updated_at=NOW)

        self.assertIs(advance_relocation(None, planned), planned)
        self.assertIs(advance_relocation(planned, staged), staged)
        self.assertIs(advance_relocation(staged, verified), verified)
        self.assertIs(advance_relocation(verified, switched), switched)
        self.assertIs(advance_relocation(switched, cleaned), cleaned)

        with self.assertRaises(RelocationPhaseError):
            advance_relocation(planned, verified)
        with self.assertRaises(RelocationPhaseError):
            advance_relocation(cleaned, switched)


if __name__ == "__main__":
    unittest.main()
