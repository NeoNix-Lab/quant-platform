"""Caller-owned transaction mode of ExperimentRepository (#332, ADR-0068 s4).

Hermetic contract of the opt-in mode and of the unchanged default. The real
one-PostgreSQL-transaction proof, with fault injection against the K13 service,
lives in tests/integration_runtime_stores_postgres.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.experiments import (  # noqa: E402
    ExperimentPersistenceError,
    ExperimentRepository,
    RunState,
)
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_governed_result_import_v1 import output, run  # noqa: E402


class TransactionalFakeConnection(FakeConnection):
    """An autocommit connection whose ``transaction()`` is the only boundary."""

    autocommit = True

    def __init__(self) -> None:
        super().__init__()
        self.commits = 0
        self.rollbacks = 0
        self.transactions = 0

    def commit(self) -> None:
        self.commits += 1
        super().commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        super().rollback()

    @contextmanager
    def transaction(self):
        before = deepcopy(self.data)
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.data = before
            raise


def succeed(repository: ExperimentRepository, identity):
    return repository.succeed_run_with_artifacts(
        identity, (output(identity).registration,),
    )


class DefaultModeIsUnchangedTests(unittest.TestCase):
    def test_every_public_mutation_still_commits_on_its_own(self) -> None:
        connection = FakeConnection()
        commits = []
        original = connection.commit
        connection.commit = lambda: (commits.append(1), original())[1]
        repository = ExperimentRepository(connection)
        identity = run("default-mode")
        repository.register_run(identity)
        repository.transition_run(identity, RunState.RUNNING)
        succeed(repository, identity)
        self.assertEqual(3, len(commits))
        self.assertEqual(RunState.SUCCEEDED, repository.get_run(identity).state)

    def test_a_failing_mutation_still_rolls_back_on_its_own(self) -> None:
        connection = FakeConnection()
        repository = ExperimentRepository(connection)
        identity = run("default-failure")
        repository.register_run(identity)
        repository.transition_run(identity, RunState.RUNNING)
        connection.fail_artifact_role = "metrics"
        with self.assertRaisesRegex(RuntimeError, "injected artifact failure"):
            succeed(repository, identity)
        # The artifact failure rolled back its own transition: still RUNNING.
        self.assertEqual(RunState.RUNNING, repository.get_run(identity).state)

    def test_default_mode_does_not_require_an_autocommit_connection(self) -> None:
        repository = ExperimentRepository(FakeConnection())  # no autocommit, no transaction()
        self.assertEqual(
            RunState.REGISTERED, repository.register_run(run("plain")).state,
        )


class CallerOwnedModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = TransactionalFakeConnection()
        self.repository = ExperimentRepository(self.connection)

    def test_calls_inside_the_block_never_commit_or_roll_back_by_themselves(self) -> None:
        identity = run("owned")
        with self.repository.caller_owned_transaction():
            self.repository.register_run(identity)
            self.repository.transition_run(identity, RunState.RUNNING)
            succeed(self.repository, identity)
            self.assertEqual((0, 0), (self.connection.commits, self.connection.rollbacks))
        self.assertEqual(1, self.connection.transactions)
        self.assertEqual((0, 0), (self.connection.commits, self.connection.rollbacks))
        self.assertEqual(RunState.SUCCEEDED, self.repository.get_run(identity).state)

    def test_an_exception_leaving_the_block_rolls_back_every_nested_call(self) -> None:
        identity = run("owned-fault")
        with self.assertRaisesRegex(RuntimeError, "fault after the Experiment write"):
            with self.repository.caller_owned_transaction():
                self.repository.register_run(identity)
                self.repository.transition_run(identity, RunState.RUNNING)
                succeed(self.repository, identity)
                raise RuntimeError("fault after the Experiment write")
        self.assertEqual((), self.repository.list_runs_for_run_spec(identity.run_spec_identity))
        self.assertEqual((), self.repository.list_artifacts(identity))
        self.assertEqual({}, self.connection.data["experiment.runs"])

    def test_a_failing_repository_call_propagates_and_rolls_back_the_block(self) -> None:
        identity = run("owned-inner-failure")
        self.connection.fail_artifact_role = "metrics"
        with self.assertRaisesRegex(RuntimeError, "injected artifact failure"):
            with self.repository.caller_owned_transaction():
                self.repository.register_run(identity)
                self.repository.transition_run(identity, RunState.RUNNING)
                succeed(self.repository, identity)
        # The repository did not roll back by itself; the block did, as a whole.
        self.assertEqual(0, self.connection.rollbacks)
        self.assertEqual({}, self.connection.data["experiment.runs"])

    def test_nested_blocks_join_the_outermost_transaction(self) -> None:
        identity = run("owned-nested")
        with self.assertRaises(RuntimeError):
            with self.repository.caller_owned_transaction():
                with self.repository.caller_owned_transaction():
                    self.repository.register_run(identity)
                # Leaving the inner block must not end the outer transaction.
                self.assertEqual(1, self.connection.transactions)
                self.repository.transition_run(identity, RunState.RUNNING)
                raise RuntimeError("outer fault")
        self.assertEqual({}, self.connection.data["experiment.runs"])

    def test_the_default_mode_is_restored_after_the_block_even_on_failure(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.repository.caller_owned_transaction():
                raise RuntimeError("fault")
        before = self.connection.commits
        self.repository.register_run(run("after-block"))
        self.assertEqual(before + 1, self.connection.commits)

    def test_it_requires_an_autocommit_connection_with_transaction(self) -> None:
        for connection in (FakeConnection(), _NonAutocommit()):
            with self.subTest(connection=type(connection).__name__):
                repository = ExperimentRepository(connection)
                with self.assertRaises(ExperimentPersistenceError):
                    with repository.caller_owned_transaction():
                        self.fail("the block must not run on an unsuitable connection")
                # Refused cleanly: the default mode is intact.
                repository.register_run(run("still-default"))


class _NonAutocommit(TransactionalFakeConnection):
    autocommit = False


if __name__ == "__main__":
    unittest.main()
