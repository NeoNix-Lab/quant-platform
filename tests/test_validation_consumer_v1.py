from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.validation_consumer import (  # noqa: E402
    MAX_SYNC_PBO_BLOCK_COUNT,
    MAX_SYNC_PBO_WORK,
    ValidationBuildFoldsRequest,
    ValidationClassifyCandidateRequest,
    ValidationEvaluateDsrRequest,
    ValidationEvaluatePboRequest,
    execute_validation_build_folds,
    execute_validation_classify_candidate,
    execute_validation_evaluate_dsr,
    execute_validation_evaluate_pbo,
)
from quant_platform.validation import (  # noqa: E402
    CandidateClassification,
    ComparableTrialPanel,
    EffectiveTrialCountEvidence,
    Embargo,
    EvaluationStatus,
    ValidationCandidate,
    WalkForwardScheduleSpec,
    build_walk_forward_folds,
    evaluate_pbo_v1,
)


def schedule() -> WalkForwardScheduleSpec:
    return WalkForwardScheduleSpec("2024-01-01T00:00:00Z", 10, 3, 1)


def panel(*, trial_count: int = 2, observation_count: int = 4) -> ComparableTrialPanel:
    trial_ids = tuple(f"trial-{index}" for index in range(trial_count))
    observation_ids = tuple(f"observation-{index}" for index in range(observation_count))
    return ComparableTrialPanel(
        population_id="population-v1:alpha",
        trial_ids=trial_ids,
        observation_ids=observation_ids,
        returns={
            trial_id: tuple((index % 5 - 2) / 100 for index in range(observation_count))
            for trial_id in trial_ids
        },
        return_semantics_id="returns-v1",
    )


class ValidationConsumerV1Tests(unittest.TestCase):
    def test_each_finite_operation_preserves_its_existing_domain_result(self) -> None:
        folds_request = ValidationBuildFoldsRequest(schedule())
        self.assertEqual(build_walk_forward_folds(schedule()), execute_validation_build_folds(folds_request))
        self.assertEqual(folds_request.request_identity, ValidationBuildFoldsRequest(schedule()).request_identity)

        fold = execute_validation_build_folds(folds_request)[0]
        classification_request = ValidationClassifyCandidateRequest(
            fold, Embargo(0), ValidationCandidate(fold.test.start)
        )
        classification = execute_validation_classify_candidate(classification_request)
        self.assertIs(CandidateClassification.ADMITTED, classification.classification)

        dsr_request = ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1"))
        dsr = execute_validation_evaluate_dsr(dsr_request)
        self.assertIsInstance(dsr.status, EvaluationStatus)
        self.assertEqual(dsr_request.request_identity, ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1")).request_identity)

        pbo_request = ValidationEvaluatePboRequest(panel(), 4)
        pbo = execute_validation_evaluate_pbo(pbo_request)
        self.assertIsInstance(pbo.status, EvaluationStatus)
        self.assertEqual(pbo_request.request_identity, ValidationEvaluatePboRequest(panel(), 4).request_identity)

    def test_invalid_constructor_or_evaluator_precondition_becomes_invalid_request(self) -> None:
        with self.assertRaises(ConsumerApiError) as folds_error:
            execute_validation_build_folds(ValidationBuildFoldsRequest(object()))  # type: ignore[arg-type]
        self.assertIs(ConsumerErrorCode.INVALID_REQUEST, folds_error.exception.code)

        with self.assertRaises(ConsumerApiError) as pbo_error:
            execute_validation_evaluate_pbo(ValidationEvaluatePboRequest(panel(), 3))
        self.assertIs(ConsumerErrorCode.INVALID_REQUEST, pbo_error.exception.code)

    def test_non_evaluable_existing_dsr_result_is_not_converted_to_error(self) -> None:
        non_evaluable = ComparableTrialPanel(
            population_id="population-v1:short",
            trial_ids=("A",),
            observation_ids=("o1", "o2", "o3"),
            returns={"A": (0.01, 0.02, 0.03)},
            return_semantics_id="returns-v1",
        )
        result = execute_validation_evaluate_dsr(
            ValidationEvaluateDsrRequest(non_evaluable, EffectiveTrialCountEvidence(1.0, "keff-v1"))
        )
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("observation_count_below_4", result.reason)

    def test_pbo_at_the_synchronous_limit_preserves_the_domain_result_and_identity(self) -> None:
        request = ValidationEvaluatePboRequest(panel(observation_count=MAX_SYNC_PBO_BLOCK_COUNT), MAX_SYNC_PBO_BLOCK_COUNT)

        result = execute_validation_evaluate_pbo(request)

        self.assertEqual(evaluate_pbo_v1(request.panel, request.block_count), result)
        self.assertLess(
            len(json.dumps(result.canonical_payload(), separators=(",", ":")).encode("utf-8")),
            16 * 1024 * 1024,
        )

    def test_pbo_over_block_or_work_budget_is_refused_before_evaluation(self) -> None:
        oversized_block_request = ValidationEvaluatePboRequest(panel(observation_count=18), 18)
        oversized_work_request = ValidationEvaluatePboRequest(panel(trial_count=100, observation_count=1200), 12)

        for request in (oversized_block_request, ValidationEvaluatePboRequest(panel(observation_count=30), 30), oversized_work_request):
            with self.subTest(block_count=request.block_count, trials=request.panel.trial_count, observations=request.panel.observation_count):
                with patch("quant_platform.application.validation_consumer.evaluate_pbo_v1") as evaluator:
                    with self.assertRaises(ConsumerApiError) as error:
                        execute_validation_evaluate_pbo(request)
                self.assertIs(ConsumerErrorCode.INVALID_REQUEST, error.exception.code)
                self.assertEqual("pbo_sync_budget_exceeded", error.exception.context["reason"])
                self.assertEqual(request.request_identity, error.exception.request_identity)
                evaluator.assert_not_called()

    def test_pbo_budget_constants_cover_the_boundary(self) -> None:
        self.assertEqual(16, MAX_SYNC_PBO_BLOCK_COUNT)
        self.assertEqual(5_000_000, MAX_SYNC_PBO_WORK)


if __name__ == "__main__":
    unittest.main()
