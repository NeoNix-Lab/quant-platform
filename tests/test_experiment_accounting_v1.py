#!/usr/bin/env python3
"""I03 trial accounting, comparable population and comparison proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_experiment_persistence_v1 import FakeConnection  # noqa: E402

from quant_platform.experiments import (  # noqa: E402
    ArtifactContentIdentity,
    ArtifactIdentity,
    ArtifactRegistration,
    ComparableTrialPopulation,
    ComparisonEntryStatus,
    ComparisonProtocolIdentity,
    ExperimentAccountingError,
    ExperimentRepository,
    IdentityReference,
    MetricDefinition,
    MetricDirection,
    MetricResult,
    MetricStatus,
    MetricValueKind,
    RunIdentity,
    RunState,
    RunSpecIdentity,
    StudyIdentity,
    TrialAttemptResult,
    TrialIdentity,
    compare_trial_attempts,
    record_trial_attempt,
)


def ref(kind: str, identity: str) -> IdentityReference:
    return IdentityReference(kind, identity)


def study(*dimensions: str) -> StudyIdentity:
    return StudyIdentity(
        study_definition_version="study-definition-v1",
        research_identity=ref("hypothesis-spec", "absorption-v1"),
        evaluation_objective_identity=ref("metric-set", "wave5-metric-set-v1"),
        comparison_protocol_identity=ComparisonProtocolIdentity(
            ref("comparison-protocol", "i03-comparison-v1"),
            dimensions,
        ),
    )


def alternative_study(*dimensions: str) -> StudyIdentity:
    return StudyIdentity(
        study_definition_version="study-definition-v1",
        research_identity=ref("hypothesis-spec", "breakout-v1"),
        evaluation_objective_identity=ref("metric-set", "wave5-metric-set-v1"),
        comparison_protocol_identity=ComparisonProtocolIdentity(
            ref("comparison-protocol", "i03-comparison-v1"),
            dimensions,
        ),
    )


def trial(study_identity: StudyIdentity, **assignments) -> TrialIdentity:
    return TrialIdentity(study_identity, assignments)


def run_spec(
    trial_identity: TrialIdentity,
    *,
    code: str = "git:abc123",
    seed: int = 1,
) -> RunSpecIdentity:
    return RunSpecIdentity(
        trial_identity=trial_identity,
        code_identity=ref("code", code),
        data_identities=(ref("dataset-snapshot", "btc-jan"),),
        feature_identities=(ref("feature-artifact", "features-v1"),),
        research_label_identities=(ref("label-semantics", "triple-barrier-v1"),),
        validation_identity=ref("validation", "walk-forward-v1"),
        strategy_policy_execution_identities=(ref("strategy", "strategy-v1"),),
        environment_identity=ref("environment", "python-3.13-lock-v1"),
        run_configuration={"seed": seed},
    )


def run(
    study_identity: StudyIdentity,
    execution_id: str,
    *,
    lookback: int,
    threshold: str,
    seed: int = 1,
) -> RunIdentity:
    return RunIdentity(
        run_spec(
            trial(study_identity, lookback=lookback, threshold=threshold),
            seed=seed,
        ),
        execution_id,
    )


def artifact(run_identity: RunIdentity, role: str = "metrics", content: str = "sha256:" + "a" * 64) -> ArtifactIdentity:
    return ArtifactIdentity(
        run_identity,
        role,
        ArtifactContentIdentity("metrics", "metrics-v1", content),
    )


class ExperimentAccountingV1Tests(unittest.TestCase):
    def test_population_identity_is_closed_deterministic_and_study_scoped(self):
        base_study = study("lookback", "threshold")
        metric = MetricDefinition("oos_sharpe", 1, MetricDirection.MAXIMIZE)
        first = run(base_study, "exec-b", lookback=20, threshold="z2")
        second = run(base_study, "exec-a", lookback=30, threshold="z2")

        population = ComparableTrialPopulation(
            "wave5-i03-panel",
            1,
            base_study,
            (first, second),
            (metric,),
        )
        reordered = ComparableTrialPopulation(
            "wave5-i03-panel",
            1,
            base_study,
            (second, first),
            (metric,),
        )

        self.assertEqual(population.identity, reordered.identity)
        self.assertEqual(
            tuple(sorted((_run.run_spec_identity.fingerprint, _run.execution_id) for _run in (first, second))),
            tuple((_run.run_spec_identity.fingerprint, _run.execution_id) for _run in population.run_identities),
        )
        with self.assertRaisesRegex(ExperimentAccountingError, "different StudyIdentity"):
            ComparableTrialPopulation(
                "cross-study",
                1,
                base_study,
                (run(alternative_study("lookback", "threshold"), "exec-x", lookback=20, threshold="z2"),),
                (metric,),
            )
        with self.assertRaisesRegex(ExperimentAccountingError, "distinct"):
            ComparableTrialPopulation("duplicate", 1, base_study, (first, first), (metric,))

    def test_comparison_ranks_deterministically_and_keeps_non_evaluable_members(self):
        base_study = study("lookback", "threshold")
        metric = MetricDefinition("oos_sharpe", 1, MetricDirection.MAXIMIZE)
        winner = run(base_study, "exec-a", lookback=20, threshold="z2")
        tied_loser = run(base_study, "exec-b", lookback=30, threshold="z2")
        failed = run(base_study, "exec-c", lookback=40, threshold="z2")
        missing = run(base_study, "exec-d", lookback=50, threshold="z2")
        population = ComparableTrialPopulation(
            "wave5-i03-panel",
            1,
            base_study,
            (missing, failed, tied_loser, winner),
            (metric,),
        )

        comparison = compare_trial_attempts(
            population,
            (
                TrialAttemptResult(winner, RunState.SUCCEEDED, (MetricResult(metric, "1.2"),)),
                TrialAttemptResult(tied_loser, RunState.SUCCEEDED, (MetricResult(metric, "1.2"),)),
                TrialAttemptResult(failed, RunState.FAILED, failure_details={"reason": "training_failed"}),
            ),
            metric,
        )

        self.assertEqual(winner.stable_dict(), comparison.selected_entry.run_identity.stable_dict())
        by_execution = {entry.run_identity.execution_id: entry for entry in comparison.entries}
        self.assertEqual(1, by_execution["exec-a"].rank)
        self.assertEqual(2, by_execution["exec-b"].rank)
        self.assertEqual(ComparisonEntryStatus.NON_EVALUABLE, by_execution["exec-c"].status)
        self.assertEqual("run_state:FAILED", by_execution["exec-c"].non_evaluable_reason)
        self.assertEqual("missing_attempt", by_execution["exec-d"].non_evaluable_reason)
        self.assertEqual(comparison.identity, compare_trial_attempts(population, tuple(reversed((
            TrialAttemptResult(winner, RunState.SUCCEEDED, (MetricResult(metric, "1.20"),)),
            TrialAttemptResult(tied_loser, RunState.SUCCEEDED, (MetricResult(metric, "1.200"),)),
            TrialAttemptResult(failed, RunState.FAILED, failure_details={"reason": "training_failed"}),
        ))), metric).identity)

    def test_duplicate_attempt_results_are_idempotent_but_conflicts_fail_closed(self):
        base_study = study("lookback", "threshold")
        metric = MetricDefinition("loss", 1, MetricDirection.MINIMIZE)
        candidate = run(base_study, "exec-a", lookback=20, threshold="z2")
        population = ComparableTrialPopulation("loss-panel", 1, base_study, (candidate,), (metric,))
        attempt = TrialAttemptResult(candidate, RunState.SUCCEEDED, (MetricResult(metric, "0.5"),))

        comparison = compare_trial_attempts(population, (attempt, attempt), metric)

        self.assertEqual(1, comparison.selected_entry.rank)
        with self.assertRaisesRegex(ExperimentAccountingError, "conflicting duplicate"):
            compare_trial_attempts(
                population,
                (
                    attempt,
                    TrialAttemptResult(candidate, RunState.SUCCEEDED, (MetricResult(metric, "0.4"),)),
                ),
                metric,
            )

    def test_metric_distribution_is_recordable_but_not_a_scalar_comparator(self):
        base_study = study("lookback", "threshold")
        distribution = MetricDefinition(
            "fold_sharpes",
            1,
            MetricDirection.MAXIMIZE,
            value_kind=MetricValueKind.DISTRIBUTION,
        )
        candidate = run(base_study, "exec-a", lookback=20, threshold="z2")
        population = ComparableTrialPopulation("fold-panel", 1, base_study, (candidate,), (distribution,))
        attempt = TrialAttemptResult(
            candidate,
            RunState.SUCCEEDED,
            (MetricResult(distribution, ("1.0", "1.2", "0.9")),),
        )

        self.assertEqual(("1", "1.2", "0.9"), attempt.metrics[0].value)
        with self.assertRaisesRegex(ExperimentAccountingError, "must be scalar"):
            compare_trial_attempts(population, (attempt,), distribution)

    def test_record_attempt_uses_experiment_repository_for_resume_and_conflict(self):
        base_study = study("lookback", "threshold")
        metric = MetricDefinition("oos_sharpe", 1, MetricDirection.MAXIMIZE)
        identity = run(base_study, "exec-artifacts", lookback=20, threshold="z2")
        metrics_artifact = artifact(identity, "metrics")
        registration = ArtifactRegistration(metrics_artifact, locator={"uri": "memory://metrics.json"})
        repository = ExperimentRepository(FakeConnection())
        attempt = TrialAttemptResult(
            identity,
            RunState.SUCCEEDED,
            (MetricResult(metric, "1.1"),),
            artifact_identities=(metrics_artifact,),
        )

        self.assertEqual(RunState.SUCCEEDED, record_trial_attempt(repository, attempt, artifact_registrations=(registration,)).state)
        self.assertEqual(RunState.SUCCEEDED, record_trial_attempt(repository, attempt, artifact_registrations=(registration,)).state)
        self.assertEqual(("metrics",), tuple(item.artifact_role for item in repository.list_artifacts(identity)))

        conflicting = ArtifactRegistration(
            artifact(identity, "metrics", "sha256:" + "b" * 64),
            locator={"uri": "memory://different.json"},
        )
        with self.assertRaises(Exception):
            record_trial_attempt(repository, attempt, artifact_registrations=(conflicting,))


if __name__ == "__main__":
    unittest.main()
