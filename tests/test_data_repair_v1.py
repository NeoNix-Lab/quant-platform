#!/usr/bin/env python3
"""Behavioral tests for the A10 repair-intent, candidate, staging and
eligibility runtime foundation that do not require a database connection.

The atomic compare-and-cutover transaction itself (predecessor supersession,
candidate admission, and the A16/S14 transaction-scoped seams chained under
one commit) is proven against real PostgreSQL in
``tests/integration_a10_repair_postgres.py``; row locking and multi-statement
transactional semantics cannot be faithfully faked in-process.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pathlib import Path as _Path
import sys

ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.repair import (  # noqa: E402
    ALREADY_SATISFIED,
    CONVERGED,
    CandidateAttempt,
    CandidateStaging,
    CandidateStagingConflict,
    ConvergenceProvenance,
    CoverageGapTrigger,
    CutoverResult,
    FAILED,
    InvalidRevisionTrigger,
    PredecessorRef,
    REPAIR_REQUIRED,
    RepairIntent,
    RepairRefusal,
    STALE_CONFLICT,
    evaluate_invalid_revision_eligibility,
    evaluate_missing_support_eligibility,
    write_convergence_provenance,
)
from quant_platform.data.repair import _reevaluate_authority, _AlreadySatisfiedSignal, _StaleConflictSignal  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REQUIRED = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
GAP = CoverageInterval(Instant.parse("2024-01-15T06:00:00Z"), Instant.parse("2024-01-15T12:00:00Z"))


def _natural(revision: int, partition_key: str = "dt=2024-01-15"):
    from quant_platform.data.models import NaturalPartitionIdentity
    return NaturalPartitionIdentity(IDENTITY, partition_key, revision)


def candidate(
    *,
    repair_intent_id: str,
    revision: int,
    content_sha256: str = "a" * 64,
    partition_manifest_sha256: str = "b" * 64,
    partition_key: str = "dt=2024-01-15",
) -> CandidateAttempt:
    return CandidateAttempt(
        repair_intent_id=repair_intent_id,
        natural_identity=_natural(revision, partition_key),
        content_sha256=content_sha256,
        partition_manifest_sha256=partition_manifest_sha256,
        source_evidence_id="source-evidence-1",
        materialization_id="materialization-1",
        coverage_evidence_id="coverage-evidence-1",
        quality_evidence_id="quality-evidence-1",
        code_ref="repair-commit-1",
    )


def topology_row(
    partition_id: str, revision: int, state: str, content_sha256: str = "a" * 64,
) -> tuple:
    """One 19-column locked partition topology row, matching the shared shape
    used by ``_lock_partition_topology`` in quality_lifecycle.py and
    publication_eligibility_catalog.py."""

    return (
        partition_id, "dataset-1", "dt=2024-01-15", revision, state,
        "hot", f"dt=2024-01-15/part-{revision:03d}.parquet",
        None, None, 1, 100, content_sha256, "c" * 64,
        None, None, None, None, "producer-1", "commit-1",
    )


class RepairIntentIdentityTests(unittest.TestCase):
    def test_A_coverage_gap_trigger_requires_at_least_one_gap(self):
        with self.assertRaises(RepairRefusal):
            CoverageGapTrigger(required=REQUIRED, gaps=())

    def test_B_coverage_gap_must_lie_inside_required_support(self):
        outside = CoverageInterval(Instant.parse("2024-01-16T01:00:00Z"), Instant.parse("2024-01-16T02:00:00Z"))
        with self.assertRaises(RepairRefusal):
            CoverageGapTrigger(required=REQUIRED, gaps=(outside,))

    def test_C_invalid_revision_trigger_requires_invalid_predecessor_state(self):
        predecessor = PredecessorRef(revision=1, content_sha256="a" * 64, state="valid")
        with self.assertRaises(RepairRefusal):
            InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")

    def test_D_invalid_revision_trigger_requires_fail_status(self):
        predecessor = PredecessorRef(revision=1, content_sha256="a" * 64, state="invalid")
        with self.assertRaises(RepairRefusal):
            InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="warn")

    def test_E_two_distinct_partition_keys_yield_distinct_intents(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        first = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        second = RepairIntent(IDENTITY, "dt=2024-01-16", trigger)
        self.assertNotEqual(first.intent_id, second.intent_id)

    def test_F_identical_trigger_evidence_yields_identical_intent_identity(self):
        trigger_a = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        trigger_b = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        first = RepairIntent(IDENTITY, "dt=2024-01-15", trigger_a)
        second = RepairIntent(IDENTITY, "dt=2024-01-15", trigger_b)
        self.assertEqual(first.intent_id, second.intent_id)

    def test_G_different_gap_evidence_yields_distinct_intent_identity(self):
        other_gap = CoverageInterval(Instant.parse("2024-01-15T14:00:00Z"), Instant.parse("2024-01-15T18:00:00Z"))
        first = RepairIntent(IDENTITY, "dt=2024-01-15", CoverageGapTrigger(required=REQUIRED, gaps=(GAP,)))
        second = RepairIntent(IDENTITY, "dt=2024-01-15", CoverageGapTrigger(required=REQUIRED, gaps=(other_gap,)))
        self.assertNotEqual(first.intent_id, second.intent_id)


class CandidateIdentityTests(unittest.TestCase):
    def test_H_identical_evidence_is_the_same_candidate_idempotent_retry(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        first = candidate(repair_intent_id=intent.intent_id, revision=1)
        second = candidate(repair_intent_id=intent.intent_id, revision=1)
        self.assertEqual(first.candidate_id, second.candidate_id)

    def test_I_different_content_hash_is_a_distinct_candidate(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        first = candidate(repair_intent_id=intent.intent_id, revision=1, content_sha256="a" * 64)
        second = candidate(repair_intent_id=intent.intent_id, revision=1, content_sha256="f" * 64)
        self.assertNotEqual(first.candidate_id, second.candidate_id)

    def test_J_candidate_content_sha_must_be_sha256_shaped(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        with self.assertRaises(RepairRefusal):
            candidate(repair_intent_id=intent.intent_id, revision=1, content_sha256="short")


class CandidateStagingIsolationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.staging = CandidateStaging(Path(self._tmp.name))
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        self.intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)

    def test_K_two_distinct_candidates_for_the_same_nominal_revision_get_isolated_directories(self):
        first = candidate(repair_intent_id=self.intent.intent_id, revision=2, content_sha256="a" * 64)
        second = candidate(repair_intent_id=self.intent.intent_id, revision=2, content_sha256="f" * 64)
        first_dir = self.staging.directory_for(first)
        second_dir = self.staging.directory_for(second)
        self.assertNotEqual(first_dir, second_dir)
        self.staging.write_evidence(first, "partition.json", b"attempt-one-bytes")
        self.staging.write_evidence(second, "partition.json", b"attempt-two-bytes")
        self.assertEqual((first_dir / "partition.json").read_bytes(), b"attempt-one-bytes")
        self.assertEqual((second_dir / "partition.json").read_bytes(), b"attempt-two-bytes")

    def test_L_identical_retry_write_is_idempotent(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        path_a = self.staging.write_evidence(item, "partition.json", b"same-bytes")
        path_b = self.staging.write_evidence(item, "partition.json", b"same-bytes")
        self.assertEqual(path_a, path_b)
        self.assertEqual(path_a.read_bytes(), b"same-bytes")

    def test_M_differing_content_for_the_same_candidate_identity_is_refused(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        self.staging.write_evidence(item, "partition.json", b"first-bytes")
        with self.assertRaises(CandidateStagingConflict):
            self.staging.write_evidence(item, "partition.json", b"different-bytes")

    def test_N_occupant_reflects_only_the_requested_candidate(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        self.assertIsNone(self.staging.occupant(item.candidate_id))
        self.staging.directory_for(item)
        self.assertEqual(self.staging.occupant(item.candidate_id), item.candidate_id)


class PureEligibilityTests(unittest.TestCase):
    def test_O_exact_internal_gap_requires_repair(self):
        self.assertEqual(evaluate_missing_support_eligibility(REQUIRED, (GAP,)), REPAIR_REQUIRED)

    def test_P_no_gaps_is_already_satisfied(self):
        self.assertEqual(evaluate_missing_support_eligibility(REQUIRED, ()), ALREADY_SATISFIED)

    def test_Q_fail_and_invalid_requires_repair(self):
        self.assertEqual(evaluate_invalid_revision_eligibility("fail", "invalid"), REPAIR_REQUIRED)

    def test_R_warn_degraded_is_not_an_automatic_trigger(self):
        self.assertEqual(evaluate_invalid_revision_eligibility("warn", "degraded"), ALREADY_SATISFIED)

    def test_S_pass_valid_is_not_a_trigger(self):
        self.assertEqual(evaluate_invalid_revision_eligibility("pass", "valid"), ALREADY_SATISFIED)

    def test_T_fail_status_without_invalid_state_is_not_a_trigger(self):
        # A16 has not yet transitioned the row to invalid; nothing to repair yet.
        self.assertEqual(evaluate_invalid_revision_eligibility("fail", "closed"), ALREADY_SATISFIED)


class ReevaluateAuthorityTests(unittest.TestCase):
    """Exercises the pure topology-reevaluation function directly with
    hand-built locked-topology rows, independent of any DB connection."""

    def setUp(self):
        self.trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        self.intent = RepairIntent(IDENTITY, "dt=2024-01-15", self.trigger)

    def test_U_missing_support_no_predecessor_empty_topology_proceeds(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1)
        result = _reevaluate_authority(self.intent, item, topology=())
        self.assertIsNone(result)

    def test_V_missing_support_now_eligible_elsewhere_is_stale(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1)
        topology = (topology_row("p-1", 1, "valid", content_sha256="z" * 64),)
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(self.intent, item, topology)

    def test_W_missing_support_already_produced_by_this_exact_candidate_is_already_satisfied(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1, content_sha256="a" * 64)
        topology = (topology_row("p-1", 1, "valid", content_sha256="a" * 64),)
        with self.assertRaises(_AlreadySatisfiedSignal):
            _reevaluate_authority(self.intent, item, topology)

    def test_X_missing_support_slot_now_occupied_differently_is_stale_not_silently_retargeted(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="z" * 64),)
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(self.intent, item, topology)

    def test_Y_replacement_predecessor_matches_proceeds(self):
        predecessor = PredecessorRef(revision=1, content_sha256="p" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="p" * 64),)
        predecessor_row = _reevaluate_authority(intent, item, topology)
        self.assertEqual(predecessor_row[0], "p-1")

    def test_Z_replacement_predecessor_already_superseded_is_stale(self):
        predecessor = PredecessorRef(revision=1, content_sha256="p" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2, content_sha256="different" + "d" * 55)
        topology = (
            topology_row("p-1", 1, "superseded", content_sha256="p" * 64),
            topology_row("p-2", 2, "valid", content_sha256="q" * 64),
        )
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(intent, item, topology)

    def test_AA_replacement_already_converged_by_this_exact_candidate_is_already_satisfied(self):
        predecessor = PredecessorRef(revision=1, content_sha256="p" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2, content_sha256="c" * 64)
        topology = (
            topology_row("p-1", 1, "superseded", content_sha256="p" * 64),
            topology_row("p-2", 2, "valid", content_sha256="c" * 64),
        )
        with self.assertRaises(_AlreadySatisfiedSignal):
            _reevaluate_authority(intent, item, topology)

    def test_AB_replacement_predecessor_content_changed_is_stale(self):
        predecessor = PredecessorRef(revision=1, content_sha256="p" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="different-content" + "0" * 47),)
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(intent, item, topology)

    def test_AC_higher_revision_or_different_content_never_wins_by_ordering_alone(self):
        # Two revisions live simultaneously is topologically impossible under
        # partitions_one_live, but the pure function must still fail closed
        # rather than pick the higher one if handed a malformed snapshot.
        predecessor = PredecessorRef(revision=1, content_sha256="p" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (
            topology_row("p-1", 1, "invalid", content_sha256="p" * 64),
            topology_row("p-2", 2, "closed", content_sha256="q" * 64),
        )
        with self.assertRaises(RepairRefusal):
            _reevaluate_authority(intent, item, topology)


class ConvergenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        self.intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        self.item = candidate(repair_intent_id=self.intent.intent_id, revision=1)

    def _provenance(self) -> ConvergenceProvenance:
        return ConvergenceProvenance(
            repair_intent_id=self.intent.intent_id,
            candidate_id=self.item.candidate_id,
            predecessor=None,
            final_partition_id="partition-1",
            final_natural_identity=_natural(1),
            final_partition_manifest_sha256=self.item.partition_manifest_sha256,
            final_content_sha256=self.item.content_sha256,
            coverage_evidence_id=self.item.coverage_evidence_id,
            quality_assessment_signature="sig-final",
            quality_assessment_status="pass",
            publication_state="valid",
        )

    def test_AD_provenance_is_deterministic_given_the_same_fields(self):
        first = self._provenance()
        second = self._provenance()
        self.assertEqual(first.provenance_id, second.provenance_id)

    def test_AE_durable_write_is_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as holder:
            path = Path(holder) / "convergence.json"
            first_hash = write_convergence_provenance(path, self._provenance())
            second_hash = write_convergence_provenance(path, self._provenance())
            self.assertEqual(first_hash, second_hash)

    def test_AF_durable_write_refuses_silent_overwrite_with_different_content(self):
        with tempfile.TemporaryDirectory() as holder:
            path = Path(holder) / "convergence.json"
            write_convergence_provenance(path, self._provenance())
            other = ConvergenceProvenance(
                repair_intent_id=self.intent.intent_id,
                candidate_id=self.item.candidate_id,
                predecessor=None,
                final_partition_id="partition-2",
                final_natural_identity=_natural(1),
                final_partition_manifest_sha256=self.item.partition_manifest_sha256,
                final_content_sha256=self.item.content_sha256,
                coverage_evidence_id=self.item.coverage_evidence_id,
                quality_assessment_signature="sig-different",
                quality_assessment_status="pass",
                publication_state="valid",
            )
            with self.assertRaises(RepairRefusal):
                write_convergence_provenance(path, other)


class CutoverResultInvariantTests(unittest.TestCase):
    def test_AG_converged_outcome_requires_provenance(self):
        with self.assertRaises(RepairRefusal):
            CutoverResult(outcome=CONVERGED, provenance=None)

    def test_AH_non_converged_outcomes_do_not_require_provenance(self):
        for outcome in (STALE_CONFLICT, FAILED, ALREADY_SATISFIED):
            with self.subTest(outcome=outcome):
                result = CutoverResult(outcome=outcome, reason="because")
                self.assertEqual(result.outcome, outcome)

    def test_AI_unsupported_outcome_is_rejected(self):
        with self.assertRaises(RepairRefusal):
            CutoverResult(outcome="NOT_A_REAL_OUTCOME")


if __name__ == "__main__":
    unittest.main()
