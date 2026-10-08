from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_learning_supervised_training_v1 import projection, run_identity  # noqa: E402

from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.training_consumer import (  # noqa: E402
    MAX_SYNC_TRAINING_TEST_SAMPLES,
    MAX_SYNC_TRAINING_WORK,
    SupervisedTrainEvaluateRequest,
    SupervisedTrainingConsumerService,
    execute_supervised_train_evaluate,
)
from quant_platform.experiments import ExperimentRepository, IdentityReference, RunState  # noqa: E402
from quant_platform.learning import (  # noqa: E402
    ProjectionSide,
    SupervisedTrainingPolicy,
    train_evaluate_supervised_baseline,
)


def policy() -> SupervisedTrainingPolicy:
    return SupervisedTrainingPolicy("i05.centroid", 1, "test-code-ref")


def request_for(projection_value, *, execution_id: str = "consumer-execution") -> SupervisedTrainEvaluateRequest:
    return SupervisedTrainEvaluateRequest(
        projection=projection_value,
        run_identity=run_identity(projection_value.identity, execution_id=execution_id),
        policy=policy(),
    )


def expanded_projection(*, train_count: int, test_count: int):
    """Clone accepted samples only to exercise the consumer budget boundary."""
    base = projection()
    train_templates = base.samples_for_side(ProjectionSide.TRAIN)
    test_templates = base.samples_for_side(ProjectionSide.TEST)
    samples = tuple(
        replace(train_templates[index % len(train_templates)], sample_id=f"train-{index}")
        for index in range(train_count)
    ) + tuple(
        replace(test_templates[index % len(test_templates)], sample_id=f"test-{index}")
        for index in range(test_count)
    )
    return replace(base, samples=samples)


class TrainingConsumerV1Tests(unittest.TestCase):
    def test_bounded_request_preserves_the_existing_training_result_and_identity(self) -> None:
        request = request_for(projection())

        result = execute_supervised_train_evaluate(request)
        direct = train_evaluate_supervised_baseline(
            projection=request.projection,
            run_identity=request.run_identity,
            policy=request.policy,
        )

        self.assertEqual(direct.stable_dict(), result.stable_dict())
        self.assertEqual(request.request_identity, request_for(projection()).request_identity)

    def test_service_owned_repository_registers_idempotently(self) -> None:
        request = request_for(projection())
        repository = ExperimentRepository(FakeConnection())
        service = SupervisedTrainingConsumerService(repository)

        first = service.execute(request)
        second = service.execute(request)

        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertEqual(RunState.SUCCEEDED, repository.get_run(request.run_identity).state)

    def test_request_carries_no_repository_or_runtime_locator(self) -> None:
        request = request_for(projection())
        self.assertEqual(("projection", "run_identity", "policy"), SupervisedTrainEvaluateRequest.__slots__)
        with self.assertRaises(TypeError):
            SupervisedTrainEvaluateRequest(  # type: ignore[call-arg]
                projection=request.projection,
                run_identity=request.run_identity,
                policy=request.policy,
                repository=ExperimentRepository(FakeConnection()),
            )

    def test_provenance_mismatches_are_refused_before_training_or_registration(self) -> None:
        request = request_for(projection())
        projection_mismatch = replace(
            request,
            run_identity=replace(
                request.run_identity,
                run_spec_identity=replace(
                    request.run_identity.run_spec_identity,
                    data_identities=(
                        IdentityReference(
                            "supervised-projection",
                            "supervised-projection-v1:sha256:" + "0" * 64,
                        ),
                    ),
                ),
            ),
        )
        policy_mismatch = replace(
            request,
            policy=SupervisedTrainingPolicy("i05.centroid", 1, "different-code-ref"),
        )
        service = SupervisedTrainingConsumerService(ExperimentRepository(FakeConnection()))

        for mismatched_request in (projection_mismatch, policy_mismatch):
            with self.subTest(request_identity=mismatched_request.request_identity):
                with patch("quant_platform.application.training_consumer.train_evaluate_supervised_baseline") as trainer, patch(
                    "quant_platform.application.training_consumer.record_supervised_training_run"
                ) as registrar:
                    with self.assertRaises(ConsumerApiError) as error:
                        service.execute(mismatched_request)
                self.assertIs(ConsumerErrorCode.INVALID_REQUEST, error.exception.code)
                self.assertEqual(mismatched_request.request_identity, error.exception.request_identity)
                trainer.assert_not_called()
                registrar.assert_not_called()

    def test_over_budget_requests_are_refused_before_training_or_registration(self) -> None:
        over_test_samples = request_for(expanded_projection(train_count=2, test_count=MAX_SYNC_TRAINING_TEST_SAMPLES + 1))
        over_work = request_for(expanded_projection(train_count=15_001, test_count=5_000), execution_id="over-work")
        repository = ExperimentRepository(FakeConnection())
        service = SupervisedTrainingConsumerService(repository)

        for request in (over_test_samples, over_work):
            with self.subTest(samples=len(request.projection.samples)):
                with patch("quant_platform.application.training_consumer.train_evaluate_supervised_baseline") as trainer, patch(
                    "quant_platform.application.training_consumer.record_supervised_training_run"
                ) as registrar:
                    with self.assertRaises(ConsumerApiError) as error:
                        service.execute(request)
                self.assertIs(ConsumerErrorCode.INVALID_REQUEST, error.exception.code)
                self.assertEqual("training_sync_budget_exceeded", error.exception.context["reason"])
                self.assertEqual(request.request_identity, error.exception.request_identity)
                trainer.assert_not_called()
                registrar.assert_not_called()

    def test_exact_work_boundary_executes_and_result_stays_below_wire_bound(self) -> None:
        feature_count = len(projection().feature_schema)
        total_samples = MAX_SYNC_TRAINING_WORK // feature_count
        test_count = min(MAX_SYNC_TRAINING_TEST_SAMPLES, total_samples // 2)
        projection_value = expanded_projection(
            train_count=total_samples - test_count,
            test_count=test_count,
        )
        request = request_for(projection_value, execution_id="boundary-work")
        self.assertEqual(MAX_SYNC_TRAINING_WORK, len(projection_value.samples) * len(projection_value.feature_schema))

        result = execute_supervised_train_evaluate(request)

        self.assertEqual(test_count, len(result.predictions))
        self.assertLess(
            len(json.dumps(result.stable_dict(), separators=(",", ":")).encode("utf-8")),
            16 * 1024 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
