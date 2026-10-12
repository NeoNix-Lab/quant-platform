"""Single J03 worker lifecycle and host (#335, P04b): the hermetic contract.

The lifecycle runs against SQLite for the store and recording fakes for the
advisory lock and the API's commit record. The real PostgreSQL lock, the
two-process proof and the least-privilege checks are in
tests/integration_runtime_stores_postgres.py.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.application import durable_jobs  # noqa: E402
from quant_platform.application.durable_jobs import (  # noqa: E402
    DurableJobStore,
    EffectSafety,
    JobAdmission,
    JobState,
)
from quant_platform.application.job_dispatch import (  # noqa: E402
    DATA_LOCAL_JOB_REGISTRY,
    JobHandler,
    JobHandlerRegistry,
    JobResult,
)
from quant_platform.application.job_worker_runtime import (  # noqa: E402
    WORKER_ADVISORY_LOCK_KEY,
    JobWorker,
    WorkerCommitMismatch,
    WorkerLockHeld,
    deployment_commit,
)
import job_worker  # noqa: E402  (the host under test)

KIND = "catalog-maintenance-v1"
IDENTITY = "implementation:sha256:aaa"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def admission(request: str = "request:1") -> JobAdmission:
    return JobAdmission(
        operation_kind=KIND, contract_version="v1", implementation_identity=IDENTITY,
        request_identity=request, canonical_parameters={}, input_identities=(),
        effect_safety=EffectSafety.PURE_REPLAYABLE,
    )


class Lock:
    def __init__(self, journal, *, available=True):
        self.journal, self.available = journal, available
        self.held = False

    def acquire(self):
        self.journal.append("lock.acquire")
        self.held = self.available
        return self.available

    def release(self):
        self.journal.append("lock.release")
        self.held = False


class Record:
    def __init__(self, journal, value):
        self.journal, self.value = journal, value

    def recorded(self):
        self.journal.append("commit.read")
        return self.value


class JobWorkerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.journal: list[str] = []
        # The worker loop runs on its own thread in the stop test.
        self.store = DurableJobStore(sqlite3.connect(":memory:", check_same_thread=False))
        self.stop = threading.Event()

    def worker(self, *, registry=None, available=True, recorded=COMMIT, commit=COMMIT, **options):
        lock = Lock(self.journal, available=available)
        return JobWorker(
            store=self.store, registry=registry or DATA_LOCAL_JOB_REGISTRY, lock=lock,
            commit_record=Record(self.journal, recorded), commit=commit, stop=self.stop,
            poll_interval=0.01, **options,
        ), lock

    def running_job(self, request="request:running"):
        job = self.store.admit(admission(request))
        self.store.transition(job.job_id, JobState.QUEUED)
        self.store.claim(job.job_id)
        return job

    def registry(self, execute):
        return JobHandlerRegistry((
            JobHandler(KIND, "v1", IDENTITY, EffectSafety.PURE_REPLAYABLE, execute),
        ))

    # -- start-up order and refusals (AC1-AC3) --------------------------------

    def test_start_order_is_lock_then_commit_then_one_recovery(self) -> None:
        job = self.running_job()
        worker, _ = self.worker()
        with mock.patch.object(
            self.store, "recover_after_restart", wraps=self.store.recover_after_restart,
        ) as recover:
            self.journal.append("start")
            recovered = worker.start()
            self.assertEqual(1, recover.call_count)
        self.assertEqual(["start", "lock.acquire", "commit.read"], self.journal)
        self.assertEqual((job.job_id,), recovered)
        self.assertEqual(JobState.RECOVERY_REQUIRED, self.store.get(job.job_id).state)

    def test_a_held_lock_refuses_before_reading_or_touching_anything(self) -> None:
        job = self.running_job()
        worker, lock = self.worker(available=False)
        with mock.patch.object(self.store, "recover_after_restart") as recover:
            with self.assertRaises(WorkerLockHeld):
                worker.start()
            recover.assert_not_called()
        self.assertEqual(["lock.acquire"], self.journal)  # no commit read either
        self.assertEqual(JobState.RUNNING, self.store.get(job.job_id).state)
        self.assertEqual(1, len(self.store.attempts(job.job_id)))
        self.assertFalse(lock.held)

    def test_a_commit_that_differs_or_is_unrecorded_refuses_and_releases_the_lock(self) -> None:
        for label, recorded in {"differs": "f" * 40, "unrecorded": None}.items():
            with self.subTest(label):
                self.journal.clear()
                job = self.running_job(f"request:{label}")
                worker, lock = self.worker(recorded=recorded)
                with mock.patch.object(self.store, "recover_after_restart") as recover:
                    with self.assertRaises(WorkerCommitMismatch):
                        worker.start()
                    recover.assert_not_called()
                self.assertEqual(
                    ["lock.acquire", "commit.read", "lock.release"], self.journal,
                )
                self.assertFalse(lock.held)
                self.assertEqual(JobState.RUNNING, self.store.get(job.job_id).state)

    def test_recovery_runs_once_and_the_recovered_job_is_not_redispatched(self) -> None:
        calls = []

        def execute(_admission):
            calls.append(1)
            return JobResult(("result:sha256:1",))

        job = self.running_job()
        worker, _ = self.worker(registry=self.registry(execute))
        worker.start()
        with self.assertRaises(RuntimeError):
            worker.start()  # a worker starts once; recovery is not repeated
        threading.Timer(0.15, self.stop.set).start()
        self.assertEqual(0, worker.run())
        self.assertEqual([], calls)
        self.assertEqual(JobState.RECOVERY_REQUIRED, self.store.get(job.job_id).state)
        self.assertEqual(1, len(self.store.attempts(job.job_id)))

    def test_run_requires_a_started_worker(self) -> None:
        worker, _ = self.worker()
        with self.assertRaises(RuntimeError):
            worker.run()
        refused, _ = self.worker(available=False)
        with self.assertRaises(WorkerLockHeld):
            refused.start()
        with self.assertRaises(RuntimeError):
            refused.run()

    # -- loop and stop (AC4) --------------------------------------------------

    def test_the_loop_dispatches_queued_jobs_then_idles_until_stopped(self) -> None:
        def execute(_admission):
            return JobResult(("result:sha256:1",))

        worker, lock = self.worker(registry=self.registry(execute))
        jobs = [self.store.admit(admission(f"request:{i}")) for i in range(3)]
        for job in jobs:
            self.store.transition(job.job_id, JobState.QUEUED)
        threading.Timer(0.3, self.stop.set).start()
        self.assertEqual(3, worker.serve())
        self.assertEqual({JobState.SUCCEEDED}, {self.store.get(j.job_id).state for j in jobs})
        self.assertFalse(lock.held)  # serve() always releases

    def test_a_stop_request_lets_the_in_flight_handler_finish_and_be_recorded(self) -> None:
        entered = threading.Event()
        proceed = threading.Event()
        calls = []

        def execute(_admission):
            calls.append("start")
            entered.set()
            proceed.wait(5)
            calls.append("finish")
            return JobResult(("result:sha256:1",))

        worker, lock = self.worker(registry=self.registry(execute))
        first = self.store.admit(admission("request:first"))
        second = self.store.admit(admission("request:second"))
        for job in (first, second):
            self.store.transition(job.job_id, JobState.QUEUED)

        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("dispatched", worker.serve()))
        thread.start()
        self.assertTrue(entered.wait(5))
        self.stop.set()            # SIGTERM arrives while the handler runs
        self.assertTrue(thread.is_alive())  # ...and does not abort it
        proceed.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["start", "finish"], calls)  # finished, never interrupted
        self.assertEqual(1, result["dispatched"])
        states = sorted(self.store.get(j.job_id).state.value for j in (first, second))
        self.assertEqual(["QUEUED", "SUCCEEDED"], states)  # the next one was not started
        self.assertFalse(lock.held)

    def test_a_crash_inside_run_still_releases_the_lock(self) -> None:
        def execute(_admission):
            raise KeyboardInterrupt

        worker, lock = self.worker(registry=self.registry(execute))
        job = self.store.admit(admission())
        self.store.transition(job.job_id, JobState.QUEUED)
        with self.assertRaises(KeyboardInterrupt):
            worker.serve()
        self.assertFalse(lock.held)
        self.assertEqual(JobState.RUNNING, self.store.get(job.job_id).state)

    def test_close_is_idempotent(self) -> None:
        worker, _ = self.worker()
        worker.start()
        worker.close()
        worker.close()
        self.assertEqual(1, self.journal.count("lock.release"))

    def test_constructor_validates_its_collaborators(self) -> None:
        lock = Lock(self.journal)
        record = Record(self.journal, COMMIT)
        base = dict(
            store=self.store, registry=DATA_LOCAL_JOB_REGISTRY, lock=lock,
            commit_record=record, commit=COMMIT, stop=self.stop,
        )
        for override in (
            {"store": object()}, {"registry": ()}, {"stop": False},
            {"poll_interval": 0}, {"poll_interval": True}, {"commit": ""},
            {"commit": "has space"}, {"commit": "x" * 129},
        ):
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    JobWorker(**{**base, **override})


class WorkerConstantsAndCommitTests(unittest.TestCase):
    def test_the_lock_key_is_fixed_and_apart_from_the_store_transaction_locks(self) -> None:
        self.assertEqual(33110, WORKER_ADVISORY_LOCK_KEY)
        self.assertNotIn(WORKER_ADVISORY_LOCK_KEY, {33101, 33102, 33103})
        # The store keys really are the three above (advisory keys share a space).
        source = Path(durable_jobs.__file__).read_text(encoding="utf-8")
        self.assertIn("RuntimeStoreConnection(connection, 33101)", source)

    def test_deployment_commit_prefers_the_environment_then_git(self) -> None:
        with mock.patch.dict(os.environ, {"QP_GIT_COMMIT": COMMIT}):
            self.assertEqual(COMMIT, deployment_commit(ROOT))
        with mock.patch.dict(os.environ, {"QP_GIT_COMMIT": ""}):
            commit = deployment_commit(ROOT)
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
        with mock.patch.dict(os.environ, {"QP_GIT_COMMIT": "two words"}):
            with self.assertRaises(ValueError):
                deployment_commit(ROOT)

    def test_deployment_commit_without_git_or_environment_is_an_error(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"QP_GIT_COMMIT": ""}):
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=128, stdout="")
                with self.assertRaisesRegex(RuntimeError, "QP_GIT_COMMIT"):
                    deployment_commit(Path(empty))


class WorkerHostTests(unittest.TestCase):
    def test_sigterm_and_sigint_request_a_stop_and_a_second_one_changes_nothing(self) -> None:
        stop = threading.Event()
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in previous.items()])
        job_worker._install_signal_handlers(stop)
        self.assertFalse(stop.is_set())
        signal.raise_signal(signal.SIGTERM)
        self.assertTrue(stop.is_set())
        signal.raise_signal(signal.SIGTERM)  # must not raise or exit
        stop.clear()
        signal.raise_signal(signal.SIGINT)   # no KeyboardInterrupt mid-effect
        self.assertTrue(stop.is_set())

    def test_the_host_exposes_distinct_non_zero_refusal_codes(self) -> None:
        self.assertEqual(2, job_worker.EXIT_LOCK_HELD)
        self.assertEqual(3, job_worker.EXIT_COMMIT_MISMATCH)

    def test_a_missing_dsn_is_a_fatal_error_that_does_not_echo_configuration(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with mock.patch.dict(os.environ, {"QP_RUNTIME_DSN": "", "QP_GIT_COMMIT": COMMIT}):
                code = job_worker.main(["--dsn", ""])
        self.assertEqual(1, code)
        self.assertIn("FATAL: ValueError", err.getvalue())

    def test_the_shipped_registry_the_host_runs_is_the_empty_closed_one(self) -> None:
        self.assertIs(DATA_LOCAL_JOB_REGISTRY, job_worker.DATA_LOCAL_JOB_REGISTRY)
        self.assertEqual(0, len(DATA_LOCAL_JOB_REGISTRY))


if __name__ == "__main__":
    unittest.main()
