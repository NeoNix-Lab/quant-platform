#!/usr/bin/env python3
"""I02 Experiment persistence runtime foundation proof."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.experiments import (  # noqa: E402
    ArtifactContentIdentity,
    ArtifactIdentity,
    ArtifactRegistration,
    ComparisonProtocolIdentity,
    ExperimentPersistenceConflict,
    ExperimentRepository,
    IdentityReference,
    RunIdentity,
    RunState,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)


def ref(kind: str, identity: str) -> IdentityReference:
    return IdentityReference(kind, identity)


def protocol(*dimensions: str) -> ComparisonProtocolIdentity:
    return ComparisonProtocolIdentity(ref("comparison-protocol", "wf-comparison-v1"), dimensions)


def study(*dimensions: str) -> StudyIdentity:
    return StudyIdentity(
        study_definition_version="study-definition-v1",
        research_identity=ref("hypothesis-spec", "absorption-v1"),
        evaluation_objective_identity=ref("metric-set", "profit-factor-v1"),
        comparison_protocol_identity=protocol(*dimensions),
    )


def trial(**assignments) -> TrialIdentity:
    return TrialIdentity(study("lookback", "threshold"), assignments)


def run_spec(
    trial_identity: TrialIdentity | None = None,
    *,
    code: str = "git:abc123",
    seed: int = 1,
) -> RunSpecIdentity:
    return RunSpecIdentity(
        trial_identity=trial_identity or trial(lookback=20, threshold={"z": 2.5}),
        code_identity=ref("code", code),
        data_identities=(ref("dataset-snapshot", "btc-jan"),),
        feature_identities=(ref("feature-artifact", "features-v1"),),
        validation_identity=ref("validation", "walk-forward-v1"),
        strategy_policy_execution_identities=(ref("strategy", "strategy-v1"),),
        run_configuration={"seed": seed},
    )


def run(execution_id: str = "exec-1") -> RunIdentity:
    return RunIdentity(run_spec(), execution_id)


def content(identity: str = "sha256:" + "a" * 64) -> ArtifactContentIdentity:
    return ArtifactContentIdentity("metrics", "metrics-v1", identity)


def artifact(run_identity: RunIdentity, *, role: str = "metrics", content_identity=None) -> ArtifactIdentity:
    return ArtifactIdentity(run_identity, role, content_identity or content())


def unwrap(value):
    return getattr(value, "obj", value)


class FakeConnection:
    def __init__(self):
        self.data = {
            "experiment.studies": {},
            "experiment.trials": {},
            "experiment.run_specs": {},
            "experiment.artifact_contents": {},
            "experiment.runs": {},
            "experiment.artifacts": {},
        }
        self._snapshot = None
        self.fail_artifact_role: str | None = None

    def cursor(self):
        if self._snapshot is None:
            self._snapshot = deepcopy(self.data)
        return FakeCursor(self)

    def commit(self):
        self._snapshot = None

    def rollback(self):
        if self._snapshot is not None:
            self.data = self._snapshot
        self._snapshot = None


class FakeCursor:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def fetchone(self):
        return None if not self.rows else self.rows.pop(0)

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def execute(self, statement: str, params=()):
        sql = " ".join(statement.lower().split())
        if sql.startswith("insert into experiment.studies"):
            return self._insert_payload("experiment.studies", "study_fingerprint", (), params)
        if sql.startswith("insert into experiment.trials"):
            return self._insert_payload("experiment.trials", "trial_fingerprint", ("study_fingerprint",), params)
        if sql.startswith("insert into experiment.run_specs"):
            return self._insert_payload("experiment.run_specs", "run_spec_fingerprint", ("trial_fingerprint",), params)
        if sql.startswith("insert into experiment.artifact_contents"):
            return self._insert_payload(
                "experiment.artifact_contents",
                "artifact_content_fingerprint",
                ("artifact_kind", "artifact_schema_version", "content_identity"),
                params,
            )
        if sql.startswith("select") and "from experiment.studies" in sql:
            return self._select_payload("experiment.studies", "study_fingerprint", (), params)
        if sql.startswith("select") and "from experiment.trials" in sql:
            return self._select_payload("experiment.trials", "trial_fingerprint", ("study_fingerprint",), params)
        if sql.startswith("select") and "from experiment.run_specs" in sql:
            return self._select_payload("experiment.run_specs", "run_spec_fingerprint", ("trial_fingerprint",), params)
        if sql.startswith("select") and "from experiment.artifact_contents" in sql:
            return self._select_payload(
                "experiment.artifact_contents",
                "artifact_content_fingerprint",
                ("artifact_kind", "artifact_schema_version", "content_identity"),
                params,
            )
        if sql.startswith("insert into experiment.runs"):
            key = (params[0], params[1])
            inserted = key not in self.connection.data["experiment.runs"]
            self.connection.data["experiment.runs"].setdefault(
                key,
                {"run_spec_fingerprint": params[0], "execution_id": params[1], "state": "REGISTERED", "failure_details": None},
            )
            self.rows = [(params[1],)] if inserted else []
            return
        if sql.startswith("select run_spec_fingerprint, execution_id, state, failure_details from experiment.runs"):
            table = self.connection.data["experiment.runs"]
            if "and execution_id" in sql:
                row = table.get((params[0], params[1]))
                self.rows = [] if row is None else [(row["run_spec_fingerprint"], row["execution_id"], row["state"], row["failure_details"])]
            else:
                self.rows = [
                    (row["run_spec_fingerprint"], row["execution_id"], row["state"], row["failure_details"])
                    for key, row in sorted(table.items())
                    if key[0] == params[0]
                ]
            return
        if sql.startswith("update experiment.runs"):
            key = (params[4], params[5])
            row = self.connection.data["experiment.runs"].get(key)
            if row is None:
                self.rows = []
                return
            row["state"] = params[0]
            row["failure_details"] = unwrap(params[3])
            self.rows = [(row["run_spec_fingerprint"], row["execution_id"], row["state"], row["failure_details"])]
            return
        if sql.startswith("select artifact_content_fingerprint, locator from experiment.artifacts"):
            row = self.connection.data["experiment.artifacts"].get((params[0], params[1], params[2]))
            self.rows = [] if row is None else [(row["artifact_content_fingerprint"], row["locator"])]
            return
        if sql.startswith("insert into experiment.artifacts"):
            if self.connection.fail_artifact_role == params[2]:
                raise RuntimeError("injected artifact failure")
            self.connection.data["experiment.artifacts"][(params[0], params[1], params[2])] = {
                "run_spec_fingerprint": params[0],
                "execution_id": params[1],
                "artifact_role": params[2],
                "artifact_content_fingerprint": params[3],
                "locator": unwrap(params[4]),
            }
            self.rows = []
            return
        if sql.startswith("select run_spec_fingerprint, execution_id, artifact_role"):
            self.rows = [
                (row["run_spec_fingerprint"], row["execution_id"], row["artifact_role"], row["artifact_content_fingerprint"], row["locator"])
                for key, row in sorted(self.connection.data["experiment.artifacts"].items())
                if key[0] == params[0] and key[1] == params[1]
            ]
            return
        raise AssertionError(statement)

    def _insert_payload(self, table, key_column, extras, params):
        key = params[0]
        if key in self.connection.data[table]:
            self.rows = []
            return
        row = {key_column: key, "payload": unwrap(params[-1])}
        for index, column in enumerate(extras, start=1):
            row[column] = params[index]
        self.connection.data[table][key] = row
        self.rows = [(key,)]

    def _select_payload(self, table, key_column, extras, params):
        row = self.connection.data[table].get(params[0])
        if row is None:
            self.rows = []
            return
        self.rows = [tuple(row[column] for column in extras) + (row["payload"],)]


class ExperimentPersistenceV1Tests(unittest.TestCase):
    def test_additive_ddl_declares_experiment_schema_and_semantic_keys(self):
        ddl = (ROOT / "db/init/003_experiment.sql").read_text(encoding="utf-8")
        for text in (
            "CREATE SCHEMA IF NOT EXISTS experiment AUTHORIZATION experiment_owner",
            "CREATE TABLE studies",
            "CREATE TABLE trials",
            "CREATE TABLE run_specs",
            "CREATE TABLE runs",
            "CREATE TABLE artifact_contents",
            "CREATE TABLE artifacts",
            "PRIMARY KEY (run_spec_fingerprint, execution_id)",
            "UNIQUE (run_spec_fingerprint, execution_id, artifact_role)",
            "state IN ('REGISTERED','RUNNING','SUCCEEDED','FAILED')",
            "GRANT SELECT ON ALL TABLES IN SCHEMA experiment TO experiment_reader",
        ):
            self.assertIn(text, ddl)
        self.assertNotIn("catalog.", ddl)

    def test_study_trial_run_spec_and_content_writes_are_idempotent_and_conflict_refusing(self):
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        base_study = study("lookback", "threshold")
        base_trial = trial(lookback=20, threshold={"z": 2.5})
        base_spec = run_spec(base_trial)
        base_content = content()

        self.assertEqual(base_study.fingerprint, repository.register_study(base_study))
        self.assertEqual(base_study.fingerprint, repository.register_study(base_study))
        self.assertEqual(base_trial.fingerprint, repository.register_trial(base_trial))
        self.assertEqual(base_spec.fingerprint, repository.register_run_spec(base_spec))
        self.assertEqual(base_content.fingerprint, repository.register_artifact_content(base_content))

        connection.data["experiment.studies"][base_study.fingerprint]["payload"] = {"conflict": True}
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.register_study(base_study)

    def test_parent_relationship_conflicts_refuse(self):
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        base_trial = trial(lookback=20, threshold={"z": 2.5})
        base_spec = run_spec(base_trial)
        repository.register_run_spec(base_spec)

        connection.data["experiment.trials"][base_trial.fingerprint]["study_fingerprint"] = "0" * 64
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.register_trial(base_trial)

        connection.data["experiment.trials"][base_trial.fingerprint]["study_fingerprint"] = base_trial.study_identity.fingerprint
        connection.data["experiment.run_specs"][base_spec.fingerprint]["trial_fingerprint"] = "1" * 64
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.register_run_spec(base_spec)

    def test_run_lifecycle_restart_query_and_retry_identity(self):
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        first = run("exec-1")
        retry = RunIdentity(first.run_spec_identity, "exec-2")

        self.assertEqual(RunState.REGISTERED, repository.register_run(first).state)
        self.assertEqual(RunState.RUNNING, repository.transition_run(first, RunState.RUNNING).state)
        self.assertFalse(repository.get_run(first).running_is_liveness_evidence)
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.transition_run(first, RunState.REGISTERED)
        self.assertEqual(RunState.FAILED, repository.transition_run(first, RunState.FAILED, failure_details={"reason": "boom"}).state)
        self.assertEqual(RunState.FAILED, repository.transition_run(first, RunState.FAILED, failure_details={"reason": "boom"}).state)
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.transition_run(first, RunState.RUNNING)

        self.assertEqual(RunState.REGISTERED, repository.register_run(retry).state)
        records = repository.list_runs_for_run_spec(first.run_spec_identity)
        self.assertEqual(("exec-1", "exec-2"), tuple(record.execution_id for record in records))
        self.assertEqual((RunState.FAILED, RunState.REGISTERED), tuple(record.state for record in records))

    def test_artifact_registration_and_atomic_success_transition(self):
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        identity = run("exec-artifacts")
        repository.register_run(identity)
        repository.transition_run(identity, RunState.RUNNING)

        metrics = ArtifactRegistration(
            artifact(identity, role="metrics"),
            locator={"uri": "file:///first-location/metrics.json"},
        )
        model = ArtifactRegistration(
            artifact(identity, role="model", content_identity=content("sha256:" + "b" * 64)),
            locator={"uri": "file:///model.bin"},
        )
        result = repository.succeed_run_with_artifacts(identity, [metrics, model])
        self.assertEqual(RunState.SUCCEEDED, result.state)
        self.assertEqual(("metrics", "model"), tuple(sorted(item.artifact_role for item in repository.list_artifacts(identity))))

        moved_metrics = ArtifactRegistration(
            metrics.identity,
            locator={"uri": "file:///moved/metrics.json"},
        )
        self.assertEqual(RunState.SUCCEEDED, repository.succeed_run_with_artifacts(identity, [moved_metrics]).state)
        artifacts = {item.artifact_role: item for item in repository.list_artifacts(identity)}
        self.assertEqual(metrics.identity.artifact_content_identity.fingerprint, artifacts["metrics"].artifact_content_fingerprint)

        conflicting = ArtifactRegistration(
            artifact(identity, role="metrics", content_identity=content("sha256:" + "c" * 64)),
        )
        with self.assertRaises(ExperimentPersistenceConflict):
            repository.succeed_run_with_artifacts(identity, [conflicting])

    def test_success_with_claimed_artifacts_rolls_back_on_injected_artifact_failure(self):
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        identity = run("exec-rollback")
        repository.register_run(identity)
        repository.transition_run(identity, RunState.RUNNING)
        connection.fail_artifact_role = "bad"

        good = ArtifactRegistration(artifact(identity, role="good"))
        bad = ArtifactRegistration(artifact(identity, role="bad", content_identity=content("sha256:" + "d" * 64)))
        with self.assertRaises(RuntimeError):
            repository.succeed_run_with_artifacts(identity, [good, bad])

        self.assertEqual(RunState.RUNNING, repository.get_run(identity).state)
        self.assertEqual((), repository.list_artifacts(identity))


if __name__ == "__main__":
    unittest.main()
