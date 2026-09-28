#!/usr/bin/env python3
"""I05 supervised training/evaluation and Experiment artifact proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_learning_supervised_input_v1 import (  # noqa: E402
    FEATURE_ID_A,
    candidate,
    feature,
    fold,
    label,
    policy as selection_policy,
)

from quant_platform.experiments import (  # noqa: E402
    ComparisonProtocolIdentity,
    ExperimentRepository,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    RunState,
    StudyIdentity,
    TrialIdentity,
)
from quant_platform.learning import (  # noqa: E402
    ProjectionSide,
    SupervisedTrainingError,
    SupervisedTrainingPolicy,
    align_probability_columns,
    build_supervised_projection,
    record_supervised_training_run,
    train_evaluate_supervised_baseline,
)
from quant_platform.validation import Embargo  # noqa: E402


def ref(kind: str, identity: str) -> IdentityReference:
    return IdentityReference(kind, identity)


def study() -> StudyIdentity:
    return StudyIdentity(
        study_definition_version="study-definition-v1",
        research_identity=ref("hypothesis-spec", "wave5-supervised-v1"),
        evaluation_objective_identity=ref("metric-set", "i05-classification-v1"),
        comparison_protocol_identity=ComparisonProtocolIdentity(
            ref("comparison-protocol", "i05-baseline-comparison-v1"),
            ("model_family",),
        ),
    )


def run_identity(projection_identity: str, execution_id: str = "exec-i05") -> RunIdentity:
    trial = TrialIdentity(study(), {"model_family": "centroid_classifier_v1"})
    spec = RunSpecIdentity(
        trial_identity=trial,
        code_identity=ref("code", "test-code-ref"),
        data_identities=(ref("supervised-projection", projection_identity),),
        feature_identities=(ref("feature-definition", FEATURE_ID_A),),
        research_label_identities=(ref("label-semantics", "identity-value-v1"),),
        validation_identity=ref("walk-forward-fold", "fold-0"),
        environment_identity=ref("environment", "stdlib-only-python"),
        run_configuration={"model_family": "centroid_classifier_v1"},
    )
    return RunIdentity(spec, execution_id)


def projection(*, include_test_outlier: bool = False):
    train_a = candidate(
        "sample-a",
        "2026-01-01T00:10:00Z",
        features=(
            feature(
                FEATURE_ID_A,
                observation_identity="train-a",
                support=("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"),
                value="0",
            ),
        ),
        target=label(
            "a",
            horizon=("2026-01-01T00:10:00Z", "2026-01-01T00:30:00Z"),
            causal_available_at="2026-01-01T00:30:00Z",
            value="0",
        ),
    )
    train_b = candidate(
        "sample-b",
        "2026-01-01T00:20:00Z",
        features=(
            feature(
                FEATURE_ID_A,
                observation_identity="train-b",
                support=("2026-01-01T00:00:00Z", "2026-01-01T00:20:00Z"),
                value="2",
            ),
        ),
        target=label(
            "b",
            horizon=("2026-01-01T00:20:00Z", "2026-01-01T00:40:00Z"),
            causal_available_at="2026-01-01T00:40:00Z",
            value="1",
        ),
    )
    test_a = candidate(
        "sample-c",
        "2026-01-01T01:10:00Z",
        features=(
            feature(
                FEATURE_ID_A,
                observation_identity="test-a",
                support=("2026-01-01T01:00:00Z", "2026-01-01T01:10:00Z"),
                value="100" if include_test_outlier else "0",
                causal_available_at="2026-01-01T01:10:00Z",
                observed_available_at="2026-01-01T01:10:00Z",
            ),
        ),
        target=label(
            "c",
            horizon=("2026-01-01T01:10:00Z", "2026-01-01T01:30:00Z"),
            causal_available_at="2026-01-01T01:30:00Z",
            value="0",
        ),
    )
    test_b = candidate(
        "sample-d",
        "2026-01-01T01:20:00Z",
        features=(
            feature(
                FEATURE_ID_A,
                observation_identity="test-b",
                support=("2026-01-01T01:00:00Z", "2026-01-01T01:20:00Z"),
                value="2",
                causal_available_at="2026-01-01T01:20:00Z",
                observed_available_at="2026-01-01T01:20:00Z",
            ),
        ),
        target=label(
            "d",
            horizon=("2026-01-01T01:20:00Z", "2026-01-01T01:40:00Z"),
            causal_available_at="2026-01-01T01:40:00Z",
            value="1",
        ),
    )
    return build_supervised_projection(
        fold=fold(),
        embargo=Embargo(0),
        selection_policy=selection_policy(),
        candidates=(test_b, train_b, test_a, train_a),
    )


class SupervisedTrainingV1Tests(unittest.TestCase):
    def test_fold_normalizer_fits_only_train_samples_and_keeps_transform_pure(self):
        proj = projection(include_test_outlier=True)
        run = run_identity(proj.identity)
        result = train_evaluate_supervised_baseline(
            projection=proj,
            run_identity=run,
            policy=SupervisedTrainingPolicy(
                "i05.centroid",
                1,
                "test-code-ref",
                declared_class_labels=("0", "1", "2"),
            ),
        )

        self.assertEqual(("1",), result.normalizer.means)
        self.assertEqual(("1",), result.normalizer.scales)
        before = result.normalizer.stable_dict()
        self.assertEqual((("99",), ("1",)), result.normalizer.transform_matrix((("100",), ("2",))))
        self.assertEqual(before, result.normalizer.stable_dict())
        self.assertEqual(("sample-a", "sample-b"), result.normalizer.fitted_sample_ids)

    def test_training_outputs_and_artifact_identities_are_stable(self):
        proj = projection()
        run = run_identity(proj.identity)
        policy = SupervisedTrainingPolicy(
            "i05.centroid",
            1,
            "test-code-ref",
            declared_class_labels=("0", "1", "2"),
        )

        first = train_evaluate_supervised_baseline(projection=proj, run_identity=run, policy=policy)
        second = train_evaluate_supervised_baseline(projection=proj, run_identity=run, policy=policy)

        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertEqual("1", first.metrics.accuracy)
        self.assertEqual(("0", "1", "2"), first.model.class_labels)
        for prediction in first.predictions:
            self.assertEqual(("0", "1", "2"), tuple(prediction.probabilities))
            self.assertEqual("0", prediction.probabilities["2"])
        roles = tuple(registration.identity.artifact_role for registration in first.artifact_registrations)
        self.assertEqual(("normalizer", "model", "predictions", "metrics"), roles)

    def test_artifacts_register_through_experiment_repository_idempotently(self):
        proj = projection()
        run = run_identity(proj.identity)
        result = train_evaluate_supervised_baseline(
            projection=proj,
            run_identity=run,
            policy=SupervisedTrainingPolicy("i05.centroid", 1, "test-code-ref"),
        )
        repository = ExperimentRepository(FakeConnection())

        self.assertEqual(RunState.SUCCEEDED, record_supervised_training_run(repository, result).state)
        self.assertEqual(RunState.SUCCEEDED, record_supervised_training_run(repository, result).state)
        artifacts = repository.list_artifacts(run)
        self.assertEqual(("metrics", "model", "normalizer", "predictions"), tuple(item.artifact_role for item in artifacts))
        self.assertEqual(RunState.SUCCEEDED, result.attempt_result.state)
        self.assertEqual(("accuracy",), tuple(metric.metric_definition.metric_key for metric in result.attempt_result.metrics))

    def test_probability_alignment_fills_missing_classes_and_refuses_drift(self):
        aligned = align_probability_columns(({"1": "1"},), ("0", "1", "2"))

        self.assertEqual(({"0": "0", "1": "1", "2": "0"},), aligned)
        with self.assertRaisesRegex(SupervisedTrainingError, "unknown labels"):
            align_probability_columns(({"unexpected": "1"},), ("0", "1"))

    def test_training_requires_train_and_test_sides(self):
        proj = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=selection_policy(),
            candidates=(
                candidate(
                    "sample-a",
                    "2026-01-01T00:10:00Z",
                    target=label(
                        "a",
                        horizon=("2026-01-01T00:10:00Z", "2026-01-01T00:30:00Z"),
                        causal_available_at="2026-01-01T00:30:00Z",
                        value="0",
                    ),
                ),
            ),
        )

        self.assertEqual((ProjectionSide.TRAIN,), tuple(sample.side for sample in proj.samples))
        with self.assertRaisesRegex(SupervisedTrainingError, "TEST sample"):
            train_evaluate_supervised_baseline(
                projection=proj,
                run_identity=run_identity(proj.identity),
                policy=SupervisedTrainingPolicy("i05.centroid", 1, "test-code-ref"),
            )


if __name__ == "__main__":
    unittest.main()
