#!/usr/bin/env python3
"""F06 availability/purge/embargo classification proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest  # noqa: E402
from quant_platform.validation.availability import (  # noqa: E402
    CandidateClassification,
    DependencyCutoffRole,
    DependencyEvidence,
    DependencyLifecycle,
    DependencyMaturity,
    Embargo,
    ValidationCandidate,
    classify_candidate,
)
from quant_platform.validation.walk_forward import WalkForwardFold  # noqa: E402


TRAIN_START = "2024-01-01T00:00:00Z"
TEST_START = "2024-01-01T00:00:10Z"
TEST_END = "2024-01-01T00:00:13Z"
TRAIN_DURATION_NS = 10_000_000_000


def fold() -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=0,
        train=CoverageInterval(Instant.parse(TRAIN_START), Instant.parse(TEST_START)),
        test=CoverageInterval(Instant.parse(TEST_START), Instant.parse(TEST_END)),
    )


def dep(
    identity: str,
    support: tuple[str, str],
    causal: str,
    *,
    role: DependencyCutoffRole = DependencyCutoffRole.DECISION_TIME,
    maturity: DependencyMaturity = DependencyMaturity.AVAILABLE,
    lifecycle: DependencyLifecycle = DependencyLifecycle.PROVISIONAL,
    observed_available: str | None = None,
    observed_finalized: str | None = None,
) -> DependencyEvidence:
    return DependencyEvidence(
        identity=identity,
        cutoff_role=role,
        support=CoverageInterval(Instant.parse(support[0]), Instant.parse(support[1])),
        required_maturity=maturity,
        lifecycle=lifecycle,
        causal_available_at=Instant.parse(causal),
        observed_available_at=(
            None if observed_available is None else Instant.parse(observed_available)
        ),
        observed_finalized_at=(
            None if observed_finalized is None else Instant.parse(observed_finalized)
        ),
    )


def insufficient_dep(
    identity: str,
    *,
    role: DependencyCutoffRole = DependencyCutoffRole.DECISION_TIME,
    detail: str = "",
) -> DependencyEvidence:
    return DependencyEvidence(identity=identity, cutoff_role=role, sufficient=False, detail=detail)


def candidate(d: str, dependencies: tuple[DependencyEvidence, ...] = ()) -> ValidationCandidate:
    return ValidationCandidate(d=d, dependencies=dependencies)


class ValidationAvailabilityV1Tests(unittest.TestCase):
    # 1. support wholly before boundary -> admitted when otherwise valid
    def test_support_wholly_before_boundary_admits(self):
        d = dep("f", ("2024-01-01T00:00:00Z", "2024-01-01T00:00:05Z"), TRAIN_START)
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.ADMITTED, result.classification)

    # 2. support ending exactly test_start -> not purged from equality alone
    def test_support_ending_exactly_test_start_not_purged(self):
        d = dep("f", ("2024-01-01T00:00:00Z", TEST_START), TRAIN_START)
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:09Z", (d,))
        )
        self.assertEqual(CandidateClassification.ADMITTED, result.classification)

    # 3. support crossing test boundary -> PURGED
    def test_support_crossing_test_boundary_purges(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:08Z", "2024-01-01T00:00:11Z"),
            TRAIN_START,
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:09Z", (d,))
        )
        self.assertEqual(CandidateClassification.PURGED, result.classification)
        self.assertIn("f", result.reasons)

    # 4. support starting exactly test_start -> overlap/purge for nominal train candidate
    def test_support_starting_exactly_test_start_purges(self):
        d = dep("f", (TEST_START, "2024-01-01T00:00:12Z"), TRAIN_START)
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:09Z", (d,))
        )
        self.assertEqual(CandidateClassification.PURGED, result.classification)

    # 5. valid warm-up before fold start -> allowed causal context
    def test_warm_up_support_before_fold_start_is_allowed(self):
        d = dep(
            "f",
            ("2023-12-31T23:59:55Z", "2024-01-01T00:00:02Z"),
            "2023-12-31T23:59:55Z",
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:02Z", (d,))
        )
        self.assertEqual(CandidateClassification.ADMITTED, result.classification)

    # 6. insufficient history -> explicit non-observation, never synthesized
    def test_insufficient_support_is_explicit_non_observation(self):
        d = insufficient_dep("f", detail="not enough history")
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.INSUFFICIENT_SUPPORT, result.classification)
        self.assertIn("not enough history", result.reasons[0])

    # 7. semantic availability after d -> UNAVAILABLE despite earlier support timestamp
    def test_causal_floor_after_d_is_unavailable(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            "2024-01-01T00:00:06Z",
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, result.classification)

    # 8. observed availability after d -> UNAVAILABLE even if causal floor is earlier
    def test_observed_available_after_d_is_unavailable(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
            observed_available="2024-01-01T00:00:06Z",
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, result.classification)

    # 9. contemporaneously available provisional value -> usable only where AVAILABLE permitted
    def test_provisional_value_usable_only_under_available_requirement(self):
        available_dep = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
            maturity=DependencyMaturity.AVAILABLE,
            lifecycle=DependencyLifecycle.PROVISIONAL,
            observed_available="2024-01-01T00:00:01Z",
        )
        admitted = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (available_dep,))
        )
        self.assertEqual(CandidateClassification.ADMITTED, admitted.classification)

        final_only_dep = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
            maturity=DependencyMaturity.FINAL_ONLY,
            lifecycle=DependencyLifecycle.PROVISIONAL,
            observed_available="2024-01-01T00:00:01Z",
        )
        rejected = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (final_only_dep,))
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, rejected.classification)

    # 10. later FINAL cannot be substituted retroactively
    def test_later_final_cannot_be_substituted_retroactively(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
            maturity=DependencyMaturity.FINAL_ONLY,
            lifecycle=DependencyLifecycle.FINAL,
            observed_available="2024-01-01T00:00:01Z",
            observed_finalized="2024-01-01T00:00:06Z",
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, result.classification)

    # 11. FINAL_ONLY not proven by d -> UNAVAILABLE
    def test_final_only_unproven_is_unavailable(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
            maturity=DependencyMaturity.FINAL_ONLY,
            lifecycle=DependencyLifecycle.FINAL,
            observed_available="2024-01-01T00:00:01Z",
            observed_finalized=None,
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, result.classification)

    # 12. embargo equality at test_start - E -> EMBARGOED
    def test_embargo_equality_boundary_is_embargoed(self):
        result = classify_candidate(
            fold(), Embargo(3_000_000_000), candidate("2024-01-01T00:00:07Z")
        )
        self.assertEqual(CandidateClassification.EMBARGOED, result.classification)

    # 13. E == 0 -> empty embargo
    def test_zero_embargo_is_empty(self):
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:09Z")
        )
        self.assertEqual(CandidateClassification.ADMITTED, result.classification)

    # 14. test candidate may use causal train-history support
    def test_test_candidate_may_use_causal_train_history(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:02Z", "2024-01-01T00:00:09Z"),
            TRAIN_START,
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:11Z", (d,))
        )
        self.assertEqual(CandidateClassification.ADMITTED, result.classification)

    # 15. future F07-style target support crossing held-out test -> generic purge
    def test_fold_completion_dependency_crossing_test_is_purged(self):
        d = dep(
            "target",
            ("2024-01-01T00:00:09Z", "2024-01-01T00:00:12Z"),
            TRAIN_START,
            role=DependencyCutoffRole.FOLD_COMPLETION,
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(CandidateClassification.PURGED, result.classification)

    # 16. candidate outside [train_start, test_end) -> explicit out-of-fold rejection
    def test_out_of_fold_candidates_are_explicitly_rejected(self):
        unavailable_dep = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            "2099-01-01T00:00:00Z",
        )
        before = classify_candidate(
            fold(), Embargo(0), candidate("2023-12-31T23:59:00Z", (unavailable_dep,))
        )
        self.assertEqual(CandidateClassification.OUT_OF_FOLD, before.classification)

        after = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:20Z", (unavailable_dep,))
        )
        self.assertEqual(CandidateClassification.OUT_OF_FOLD, after.classification)

        at_test_end = classify_candidate(fold(), Embargo(0), candidate(TEST_END))
        self.assertEqual(CandidateClassification.OUT_OF_FOLD, at_test_end.classification)

    # 17. simultaneously unavailable + purge/embargo predicates -> primary UNAVAILABLE
    def test_unavailable_wins_over_purge_and_embargo(self):
        unavailable = dep(
            "u",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            "2099-01-01T00:00:00Z",
        )
        purging = dep(
            "p",
            ("2024-01-01T00:00:08Z", "2024-01-01T00:00:11Z"),
            TRAIN_START,
        )
        result = classify_candidate(
            fold(),
            Embargo(5_000_000_000),
            candidate("2024-01-01T00:00:06Z", (unavailable, purging)),
        )
        self.assertEqual(CandidateClassification.UNAVAILABLE, result.classification)

    # 18. sufficient availability but insufficient support + purge predicate -> primary INSUFFICIENT_SUPPORT
    def test_insufficient_support_wins_over_purge(self):
        insufficient = insufficient_dep("i")
        purging = dep(
            "p",
            ("2024-01-01T00:00:08Z", "2024-01-01T00:00:11Z"),
            TRAIN_START,
        )
        result = classify_candidate(
            fold(), Embargo(0), candidate("2024-01-01T00:00:05Z", (insufficient, purging))
        )
        self.assertEqual(CandidateClassification.INSUFFICIENT_SUPPORT, result.classification)

    # 19. both purged and embargoed -> primary PURGED
    def test_purged_wins_over_embargoed(self):
        purging = dep(
            "p",
            ("2024-01-01T00:00:09Z", "2024-01-01T00:00:11Z"),
            TRAIN_START,
        )
        result = classify_candidate(
            fold(), Embargo(5_000_000_000), candidate("2024-01-01T00:00:06Z", (purging,))
        )
        self.assertEqual(CandidateClassification.PURGED, result.classification)

    # 20. E >= train_duration -> all nominal train candidates may be embargoed
    def test_embargo_covering_entire_train_duration_embargoes_all(self):
        equal_case = classify_candidate(
            fold(), Embargo(TRAIN_DURATION_NS), candidate(TRAIN_START)
        )
        self.assertEqual(CandidateClassification.EMBARGOED, equal_case.classification)

        near_boundary = classify_candidate(
            fold(), Embargo(TRAIN_DURATION_NS), candidate("2024-01-01T00:00:09Z")
        )
        self.assertEqual(CandidateClassification.EMBARGOED, near_boundary.classification)

        exceeding = classify_candidate(
            fold(), Embargo(TRAIN_DURATION_NS + 5_000_000_000), candidate(TRAIN_START)
        )
        self.assertEqual(CandidateClassification.EMBARGOED, exceeding.classification)

    def test_determinism_across_repeated_classification(self):
        d = dep(
            "f",
            ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
            TRAIN_START,
        )
        first = classify_candidate(
            fold(), Embargo(1_000_000_000), candidate("2024-01-01T00:00:05Z", (d,))
        )
        second = classify_candidate(
            fold(), Embargo(1_000_000_000), candidate("2024-01-01T00:00:05Z", (d,))
        )
        self.assertEqual(first, second)

    def test_negative_embargo_is_rejected(self):
        with self.assertRaises(InvalidRequest):
            Embargo(-1)

    def test_insufficient_dependency_cannot_carry_availability_fields(self):
        with self.assertRaises(InvalidRequest):
            DependencyEvidence(
                identity="f",
                cutoff_role=DependencyCutoffRole.DECISION_TIME,
                sufficient=False,
                causal_available_at=TRAIN_START,
            )

    def test_provisional_dependency_cannot_carry_finalization_evidence(self):
        with self.assertRaises(InvalidRequest):
            dep(
                "f",
                ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
                TRAIN_START,
                lifecycle=DependencyLifecycle.PROVISIONAL,
                observed_finalized="2024-01-01T00:00:02Z",
            )

    def test_observed_available_cannot_precede_causal_floor(self):
        with self.assertRaises(InvalidRequest):
            dep(
                "f",
                ("2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"),
                "2024-01-01T00:00:05Z",
                observed_available="2024-01-01T00:00:01Z",
            )

    def test_classify_candidate_rejects_unvalidated_inputs(self):
        with self.assertRaises(InvalidRequest):
            classify_candidate(object(), Embargo(0), candidate(TRAIN_START))  # type: ignore[arg-type]
        with self.assertRaises(InvalidRequest):
            classify_candidate(fold(), object(), candidate(TRAIN_START))  # type: ignore[arg-type]
        with self.assertRaises(InvalidRequest):
            classify_candidate(fold(), Embargo(0), object())  # type: ignore[arg-type]


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
