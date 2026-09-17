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
    compute_coverage_evidence_id,
    compute_quality_evidence_id,
    evaluate_invalid_revision_eligibility,
    evaluate_missing_support_eligibility,
    write_convergence_provenance,
)
from quant_platform.data.repair import _reevaluate_authority, _AlreadySatisfiedSignal, _StaleConflictSignal  # noqa: E402


class _FakeProvenanceCursor:
    """Stubs only ``SELECT ... FROM catalog.repair_convergence`` -- the one
    query ``_reevaluate_authority`` issues -- so the pure re-evaluation logic
    can be exercised without a real database connection.

    ``self_for`` is the set of partition_ids that should look like this exact
    repair_intent_id/candidate_id's own durable convergence row; every other
    partition_id reports no provenance row at all.
    """

    def __init__(self, self_for: frozenset[str] = frozenset(), *, intent_id: str = "", candidate_id: str = ""):
        self.self_for = self_for
        self.intent_id = intent_id
        self.candidate_id = candidate_id
        self._result = None

    def execute(self, statement, params=None):
        assert "catalog.repair_convergence" in statement
        partition_id = params[0]
        self._result = (self.intent_id, self.candidate_id) if partition_id in self.self_for else None

    def fetchone(self):
        return self._result


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

    def test_J2_candidate_content_sha_must_be_hex_not_merely_the_right_length(self):
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        # 64 characters, but 'g' is not a hex digit -- length alone must not pass.
        with self.assertRaises(RepairRefusal):
            candidate(repair_intent_id=intent.intent_id, revision=1, content_sha256="g" * 64)

    def test_J3_predecessor_ref_content_sha_must_be_hex(self):
        with self.assertRaises(RepairRefusal):
            PredecessorRef(revision=1, content_sha256="z" * 64, state="invalid")

    def test_J4_predecessor_ref_accepts_uppercase_hex_normalized_to_lowercase(self):
        ref = PredecessorRef(revision=1, content_sha256="A" * 64, state="invalid")
        self.assertEqual(ref.content_sha256, "a" * 64)


class EvidenceIdentityBindingTests(unittest.TestCase):
    """compute_quality_evidence_id / compute_coverage_evidence_id are what
    cutover() re-derives from the actual runtime inputs and checks against
    the candidate's bound identity -- proving the same candidate_id cannot be
    retried with different acceptance evidence."""

    def test_J5_quality_evidence_id_is_deterministic_over_report_content(self):
        report = {"status": "pass", "metrics": {"a": 1}, "violations": [], "code_ref": "cert-1"}
        self.assertEqual(compute_quality_evidence_id(report), compute_quality_evidence_id(dict(report)))

    def test_J6_quality_evidence_id_changes_with_status(self):
        base = {"status": "pass", "metrics": {"a": 1}, "violations": [], "code_ref": "cert-1"}
        changed = {**base, "status": "fail"}
        self.assertNotEqual(compute_quality_evidence_id(base), compute_quality_evidence_id(changed))

    def test_J7_quality_evidence_id_requires_all_fields(self):
        with self.assertRaises(RepairRefusal):
            compute_quality_evidence_id({"status": "pass", "metrics": {}, "violations": []})

    def test_J8_coverage_evidence_id_changes_with_any_component(self):
        base = compute_coverage_evidence_id(["c1"], ["a1"], ["e" * 64])
        different_ids = compute_coverage_evidence_id(["c2"], ["a1"], ["e" * 64])
        different_hash = compute_coverage_evidence_id(["c1"], ["a1"], ["f" * 64])
        self.assertNotEqual(base, different_ids)
        self.assertNotEqual(base, different_hash)


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

    def test_N2_parent_traversal_name_is_refused(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        with self.assertRaises(RepairRefusal):
            self.staging.write_evidence(item, "../escape.json", b"payload")

    def test_N3_nested_relative_path_name_is_refused(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        with self.assertRaises(RepairRefusal):
            self.staging.write_evidence(item, "sub/partition.json", b"payload")

    def test_N4_absolute_path_name_is_refused(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        absolute = str(Path(self._tmp.name) / "outside.json")
        with self.assertRaises(RepairRefusal):
            self.staging.write_evidence(item, absolute, b"payload")

    def test_N5_dot_and_dotdot_names_are_refused(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        for name in (".", "..", ""):
            with self.subTest(name=name):
                with self.assertRaises(RepairRefusal):
                    self.staging.write_evidence(item, name, b"payload")

    def test_N6_traversal_attempt_does_not_escape_the_staging_root(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=2)
        outside_marker = Path(self._tmp.name).parent / "should-not-exist.json"
        self.addCleanup(lambda: outside_marker.unlink(missing_ok=True))
        try:
            self.staging.write_evidence(item, "../should-not-exist.json", b"payload")
        except RepairRefusal:
            pass
        self.assertFalse(outside_marker.exists())


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
        cursor = _FakeProvenanceCursor()
        result = _reevaluate_authority(cursor, self.intent, item, topology=())
        self.assertIsNone(result)

    def test_V_missing_support_now_eligible_elsewhere_is_stale(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1)
        topology = (topology_row("p-1", 1, "valid", content_sha256="z" * 64),)
        cursor = _FakeProvenanceCursor()
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, self.intent, item, topology)

    def test_W_missing_support_already_produced_by_this_exact_candidate_is_already_satisfied(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1, content_sha256="a" * 64)
        topology = (topology_row("p-1", 1, "valid", content_sha256="a" * 64),)
        cursor = _FakeProvenanceCursor({"p-1"}, intent_id=self.intent.intent_id, candidate_id=item.candidate_id)
        with self.assertRaises(_AlreadySatisfiedSignal):
            _reevaluate_authority(cursor, self.intent, item, topology)

    def test_W2_content_hash_match_alone_without_provenance_is_stale_not_already_satisfied(self):
        # A coincidental content-hash match between a distinct candidate and
        # the live row must never be treated as this candidate's own
        # convergence absent durable provenance naming it.
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1, content_sha256="a" * 64)
        topology = (topology_row("p-1", 1, "valid", content_sha256="a" * 64),)
        cursor = _FakeProvenanceCursor()  # no provenance row recorded at all
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, self.intent, item, topology)

    def test_X_missing_support_slot_now_occupied_differently_is_stale_not_silently_retargeted(self):
        item = candidate(repair_intent_id=self.intent.intent_id, revision=1)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="z" * 64),)
        cursor = _FakeProvenanceCursor()
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, self.intent, item, topology)

    def test_Y_replacement_predecessor_matches_proceeds(self):
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="1" * 64),)
        cursor = _FakeProvenanceCursor()
        predecessor_row = _reevaluate_authority(cursor, intent, item, topology)
        self.assertEqual(predecessor_row[0], "p-1")

    def test_Z_replacement_predecessor_already_superseded_is_stale(self):
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2, content_sha256="9" * 64)
        topology = (
            topology_row("p-1", 1, "superseded", content_sha256="1" * 64),
            topology_row("p-2", 2, "valid", content_sha256="3" * 64),
        )
        cursor = _FakeProvenanceCursor()
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, intent, item, topology)

    def test_AA_replacement_already_converged_by_this_exact_candidate_is_already_satisfied(self):
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2, content_sha256="c" * 64)
        topology = (
            topology_row("p-1", 1, "superseded", content_sha256="1" * 64),
            topology_row("p-2", 2, "valid", content_sha256="c" * 64),
        )
        cursor = _FakeProvenanceCursor({"p-2"}, intent_id=intent.intent_id, candidate_id=item.candidate_id)
        with self.assertRaises(_AlreadySatisfiedSignal):
            _reevaluate_authority(cursor, intent, item, topology)

    def test_AA2_replacement_slot_occupied_by_a_different_candidates_provenance_is_stale(self):
        # Durable provenance exists for the live row, but it names a
        # DIFFERENT repair_intent_id/candidate_id -- never treat that as
        # "this" candidate's own convergence.
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2, content_sha256="c" * 64)
        topology = (
            topology_row("p-1", 1, "superseded", content_sha256="1" * 64),
            topology_row("p-2", 2, "valid", content_sha256="c" * 64),
        )
        cursor = _FakeProvenanceCursor({"p-2"}, intent_id="some-other-intent", candidate_id="some-other-candidate")
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, intent, item, topology)

    def test_AB_replacement_predecessor_content_changed_is_stale(self):
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (topology_row("p-1", 1, "invalid", content_sha256="2" * 64),)
        cursor = _FakeProvenanceCursor()
        with self.assertRaises(_StaleConflictSignal):
            _reevaluate_authority(cursor, intent, item, topology)

    def test_AC_higher_revision_or_different_content_never_wins_by_ordering_alone(self):
        # Two revisions live simultaneously is topologically impossible under
        # partitions_one_live, but the pure function must still fail closed
        # rather than pick the higher one if handed a malformed snapshot.
        predecessor = PredecessorRef(revision=1, content_sha256="1" * 64, state="invalid")
        trigger = InvalidRevisionTrigger(predecessor=predecessor, assessment_signature="sig-1", assessment_status="fail")
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)
        item = candidate(repair_intent_id=intent.intent_id, revision=2)
        topology = (
            topology_row("p-1", 1, "invalid", content_sha256="1" * 64),
            topology_row("p-2", 2, "closed", content_sha256="3" * 64),
        )
        cursor = _FakeProvenanceCursor()
        with self.assertRaises(RepairRefusal):
            _reevaluate_authority(cursor, intent, item, topology)


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


class GapEliminationAndFreshnessTests(unittest.TestCase):
    """The two coverage-gap-trigger re-authorization checks cutover() runs
    before ever mutating topology: exact elimination by the candidate's own
    declared coverage, and freshness against just-recomputed B04 evidence."""

    def setUp(self):
        from quant_platform.data.repair import _verify_gaps_eliminated, _verify_gap_trigger_still_open
        self._verify_gaps_eliminated = _verify_gaps_eliminated
        self._verify_gap_trigger_still_open = _verify_gap_trigger_still_open
        self.trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP,))

    def test_AJ_full_coverage_eliminates_the_gap(self):
        self._verify_gaps_eliminated((GAP,), REQUIRED.start, REQUIRED.end)

    def test_AK_exact_gap_bounds_eliminate_the_gap(self):
        self._verify_gaps_eliminated((GAP,), GAP.start, GAP.end)

    def test_AL_partial_fill_does_not_eliminate_the_gap(self):
        half = Instant.parse("2024-01-15T09:00:00Z")
        with self.assertRaises(RepairRefusal):
            self._verify_gaps_eliminated((GAP,), GAP.start, half)

    def test_AM_coverage_starting_after_the_gap_does_not_eliminate_it(self):
        with self.assertRaises(RepairRefusal):
            self._verify_gaps_eliminated((GAP,), GAP.end, REQUIRED.end)

    def test_AN_fresh_gaps_identical_to_captured_gaps_proceeds(self):
        self._verify_gap_trigger_still_open(self.trigger, (GAP,))

    def test_AO_fresh_gaps_empty_is_already_satisfied(self):
        with self.assertRaises(_AlreadySatisfiedSignal):
            self._verify_gap_trigger_still_open(self.trigger, ())

    def test_AP_fresh_gaps_different_shape_is_stale(self):
        shifted = CoverageInterval(Instant.parse("2024-01-15T07:00:00Z"), Instant.parse("2024-01-15T13:00:00Z"))
        with self.assertRaises(_StaleConflictSignal):
            self._verify_gap_trigger_still_open(self.trigger, (shifted,))

    def test_AQ_partial_overlap_of_multiple_captured_gaps_is_stale(self):
        second_gap = CoverageInterval(Instant.parse("2024-01-15T14:00:00Z"), Instant.parse("2024-01-15T18:00:00Z"))
        trigger = CoverageGapTrigger(required=REQUIRED, gaps=(GAP, second_gap))
        # Only the first gap is still open; the second was independently filled.
        with self.assertRaises(_StaleConflictSignal):
            self._verify_gap_trigger_still_open(trigger, (GAP,))


if __name__ == "__main__":
    unittest.main()
