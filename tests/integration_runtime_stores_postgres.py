#!/usr/bin/env python3
"""Real PostgreSQL parity/privilege proof on an explicitly disposable database.

Run after 001/002/003/004/005 bootstrap, with RUNTIME_TEST_ADMIN_DSN,
RUNTIME_TEST_API_DSN, RUNTIME_TEST_WORKER_DSN and RUNTIME_TEST_INGEST_DSN.
No silent skip: missing configuration fails. The database must be named
dgk331_disposable. Existing J03/K12/K13 test methods are reused unchanged.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import json
import os
from pathlib import Path
import io
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputConflict, AdmittedInputError, AdmittedInputState, AdmittedInputStore,
)
from quant_platform.application.durable_jobs import (  # noqa: E402
    DurableJobConflict, DurableJobStore, EffectSafety, JobState,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultEvidenceStore, GovernedResultImportError, GovernedResultImportService,
    GovernedResultRefused, ResultOutputContractV1,
)
from quant_platform.application.job_dispatch import (  # noqa: E402
    DATA_LOCAL_JOB_REGISTRY, JobHandler, JobHandlerRegistry, JobResult,
)
from quant_platform.application.job_worker_runtime import (  # noqa: E402
    WORKER_ADVISORY_LOCK_KEY, CompositeWorkerLock, JobWorker, PostgresAdvisoryLock,
    PostgresCommitRecord, ProcessLifetimeLock, WorkerCommitMismatch, WorkerLockHeld,
    WorkerLockLost, database_fence_identity, process_lock_path, record_deployment_commit,
    recorded_deployment_commit,
)
from quant_platform.experiments import ExperimentRepository, RunState  # noqa: E402
from quant_platform.experiments.accounting import (  # noqa: E402
    TrialAttemptResult, record_trial_attempt,
)
import test_admitted_input_manifest_v1 as k12  # noqa: E402
import test_durable_job_runtime_v1 as j03  # noqa: E402
import test_job_dispatch_v1 as dispatch  # noqa: E402
import test_admitted_input_transport_v1 as admitted_transport  # noqa: E402
import test_job_transport_v1 as job_transport  # noqa: E402
import test_result_import_transport_v1 as result_import  # noqa: E402
import api_server  # noqa: E402  (tools/api_server.py: the API host's commit record)
import test_governed_result_import_v1 as k13  # noqa: E402


def connect(role: str, *, autocommit: bool = True):
    dsn = os.environ[f"RUNTIME_TEST_{role}_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "dgk331_disposable":
        raise RuntimeError("runtime proof requires database dgk331_disposable")
    return psycopg.connect(dsn, autocommit=autocommit)


class DisposableCase:
    def setUp(self):
        with connect("ADMIN") as admin:
            admin.execute(
                "TRUNCATE runtime.durable_jobs, runtime.durable_job_attempts, "
                "runtime.admitted_inputs, runtime.governed_result_bundles, "
                "runtime.governed_result_outputs, runtime.deployment_commit, "
                "experiment.studies CASCADE"
            )

    def connection(self, role="API", *, autocommit=True):
        connection = connect(role, autocommit=autocommit)
        self.addCleanup(connection.close)
        return connection


class PostgresJobs(DisposableCase, j03.DurableJobRuntimeV1Tests):
    def store(self):
        return DurableJobStore(self.connection("WORKER"))

    def test_restart_marks_running_job_recovery_required_without_redispatch(self):
        first = self.store()
        job = first.admit(j03.admission())
        first.transition(job.job_id, JobState.QUEUED)
        first.transition(job.job_id, JobState.RUNNING)
        first.connection.close()
        restarted = self.store()
        recovered = restarted.recover_after_restart()
        self.assertEqual((job.job_id,), tuple(item.job_id for item in recovered))
        self.assertEqual(JobState.RECOVERY_REQUIRED, restarted.get(job.job_id).state)
        self.assertEqual((JobState.RECOVERY_REQUIRED,), tuple(
            item.state for item in restarted.attempts(job.job_id)
        ))

    def test_free_text_evidence_and_terminal_append_survive_reconnect(self):
        first = self.store()
        job = first.admit(j03.admission())
        raw = first.connection.execute(
            "SELECT reason_code, diagnostic_reference FROM runtime.durable_jobs WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual((None, None), raw)
        first.transition(job.job_id, JobState.QUEUED)
        first.transition(job.job_id, JobState.RUNNING)
        reason = 'failed\x00detail' + r'\u0000 "quoted"'
        failed = first.transition(job.job_id, JobState.FAILED, reason_code=reason)
        attempt = first.attempts(job.job_id)[0]
        first.connection.close()
        reopened = self.store()
        self.assertEqual(failed, reopened.get(job.job_id))
        self.assertEqual((attempt,), reopened.attempts(job.job_id))
        diagnostic = 'evidence\x00ref' + r'\x00\u0000'
        appended = reopened.transition(
            job.job_id, JobState.FAILED, reason_code=reason, diagnostic_reference=diagnostic,
        )
        reopened.connection.close()
        final = self.store()
        self.assertEqual(appended, final.get(job.job_id))
        self.assertEqual(appended, final.transition(
            job.job_id, JobState.FAILED, reason_code=reason, diagnostic_reference=diagnostic,
        ))
        self.assertEqual((attempt,), final.attempts(job.job_id))
        raw = final.connection.execute(
            "SELECT reason_code, diagnostic_reference FROM runtime.durable_jobs WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual((reason, diagnostic), tuple(json.loads(value) for value in raw))
        raw_attempt = final.connection.execute(
            "SELECT failure_code, evidence_reference FROM runtime.durable_job_attempts WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual(reason, json.loads(raw_attempt[0]))
        self.assertIsNone(raw_attempt[1])

        second = final.admit(replace(j03.admission(), request_identity="reconnect-attempt"))
        final.transition(second.job_id, JobState.QUEUED)
        final.transition(second.job_id, JobState.RUNNING)
        failed = final.transition(second.job_id, JobState.FAILED,
                                  reason_code=reason, diagnostic_reference=diagnostic)
        attempt = final.attempts(second.job_id)[0]
        final.connection.close()
        after_failure = self.store()
        self.assertEqual(failed, after_failure.get(second.job_id))
        self.assertEqual((attempt,), after_failure.attempts(second.job_id))
        self.assertEqual((reason, diagnostic), (attempt.failure_code, attempt.evidence_reference))


class PostgresDispatch(DisposableCase, dispatch.JobDispatchV1Tests):
    """#334: the backend-neutral dispatch contract on the runtime schema, as the worker role."""

    def store(self):
        return DurableJobStore(self.connection("WORKER"))

    def test_concurrent_claims_of_one_queued_job_have_exactly_one_winner(self):
        seed = self.store()
        job = seed.admit(dispatch.admission())
        seed.transition(job.job_id, JobState.QUEUED)

        def claim(_):
            with connect("WORKER") as connection:
                try:
                    return DurableJobStore(connection).claim(job.job_id).state
                except DurableJobConflict:
                    return "refused"

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(claim, range(6)))
        self.assertEqual(1, outcomes.count(JobState.RUNNING))
        self.assertEqual(5, outcomes.count("refused"))
        self.assertEqual(1, len(seed.attempts(job.job_id)))


class PostgresWorker(DisposableCase, unittest.TestCase):
    """#335 (P04b): the single worker's real lock, commit check and recovery."""

    COMMIT = "a" * 40

    def api_records(self, commit=None):
        record_deployment_commit(self.connection("API"), commit or self.COMMIT)

    def worker_parts(self, *, commit=None, stop=None):
        connection = self.connection("WORKER")
        stop = stop or threading.Event()
        return JobWorker(
            store=DurableJobStore(connection), registry=DATA_LOCAL_JOB_REGISTRY,
            lock=PostgresAdvisoryLock(connection), commit_record=PostgresCommitRecord(connection),
            commit=commit or self.COMMIT, stop=stop, poll_interval=0.05,
        ), connection

    def running_job(self, request="request:worker"):
        store = DurableJobStore(self.connection("WORKER"))
        job = store.admit(replace(j03.admission(), request_identity=request))
        store.transition(job.job_id, JobState.QUEUED)
        store.claim(job.job_id)
        return job, store

    def test_the_api_records_the_commit_and_the_worker_can_only_read_it(self):
        worker = self.connection("WORKER")
        self.assertIsNone(recorded_deployment_commit(worker))
        self.api_records()
        self.assertEqual(self.COMMIT, recorded_deployment_commit(worker))
        self.api_records("b" * 40)  # a restart with another commit replaces the row
        self.assertEqual("b" * 40, recorded_deployment_commit(worker))
        self.assertEqual(1, self.connection("ADMIN").execute(
            "SELECT count(*) FROM runtime.deployment_commit").fetchone()[0])
        for statement in (
            "INSERT INTO runtime.deployment_commit (singleton, git_commit) VALUES (true, 'x')",
            "UPDATE runtime.deployment_commit SET git_commit = 'x'",
            "DELETE FROM runtime.deployment_commit",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self.connection("WORKER").execute(statement)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.connection("INGEST").execute("SELECT * FROM runtime.deployment_commit")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection("API").execute(
                "INSERT INTO runtime.deployment_commit (singleton, git_commit) VALUES (false, 'x')"
            )

    def test_the_api_host_records_its_commit_at_startup(self):
        os.environ["QP_GIT_COMMIT"] = "c" * 40
        self.addCleanup(os.environ.pop, "QP_GIT_COMMIT", None)
        api_server._record_commit(os.environ["RUNTIME_TEST_API_DSN"])
        self.assertEqual("c" * 40, recorded_deployment_commit(self.connection("WORKER")))
        # Configured but unwritable (a login without the privilege) is loud.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            api_server._record_commit(os.environ["RUNTIME_TEST_WORKER_DSN"])

    def test_the_advisory_lock_is_exclusive_session_level_and_dies_with_the_session(self):
        first_connection, second_connection = self.connection("WORKER"), self.connection("WORKER")
        first, second = PostgresAdvisoryLock(first_connection), PostgresAdvisoryLock(second_connection)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        held = self.connection("ADMIN").execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND objid = %s AND pid = %s",
            (WORKER_ADVISORY_LOCK_KEY, first_connection.info.backend_pid),
        ).fetchone()[0]
        self.assertEqual(1, held)
        # Session level: it survives a statement boundary / transaction end.
        first_connection.execute("SELECT 1")
        self.assertFalse(second.acquire())
        first_connection.close()  # the previous worker's session ends
        deadline = time.monotonic() + 10
        while not second.acquire():
            self.assertLess(time.monotonic(), deadline, "lock was not released with its session")
            time.sleep(0.1)

    def test_start_takes_the_lock_checks_the_commit_then_recovers_once(self):
        job, _ = self.running_job()
        self.api_records()
        worker, connection = self.worker_parts()
        self.assertEqual((job.job_id,), worker.start())
        self.assertEqual(JobState.RECOVERY_REQUIRED, DurableJobStore(self.connection("API")).get(job.job_id).state)
        # A second worker is refused while this one holds the lock...
        other, _ = self.worker_parts()
        with self.assertRaises(WorkerLockHeld):
            other.start()
        # ...and is entitled to start once it is released.
        worker.close()
        third, _ = self.worker_parts()
        self.assertEqual((), third.start())
        third.close()

    def test_a_different_or_missing_recorded_commit_refuses_and_releases_the_lock(self):
        job, store = self.running_job()
        for label, recorded in (("differs", "d" * 40), ("missing", None)):
            with self.subTest(label):
                if recorded is not None:
                    self.api_records(recorded)
                worker, _ = self.worker_parts()
                with self.assertRaises(WorkerCommitMismatch):
                    worker.start()
                self.assertEqual(JobState.RUNNING, store.get(job.job_id).state)
                self.assertEqual(1, len(store.attempts(job.job_id)))
                probe = PostgresAdvisoryLock(self.connection("WORKER"))
                self.assertTrue(probe.acquire(), "the lock must not leak on a refusal")
                probe.release()



    # -- review of 52ddf4b: fencing and fail-closed commit record -----------------

    def fenced_worker(self, registry, lock_directory, *, dsn=None):
        """A worker on its own (terminable) connection, with the two-part lock.

        ``dsn`` may spell the server differently from other workers': the lock
        file is named by the server's own identity, so they still contend.
        """
        connection = psycopg.connect(dsn or os.environ["RUNTIME_TEST_WORKER_DSN"], autocommit=True)
        self.addCleanup(connection.close)
        worker = JobWorker(
            store=DurableJobStore(connection), registry=registry,
            lock=CompositeWorkerLock([
                ProcessLifetimeLock(process_lock_path(connection, lock_directory)),
                PostgresAdvisoryLock(connection),
            ]),
            commit_record=PostgresCommitRecord(connection), commit=self.COMMIT,
            stop=threading.Event(), poll_interval=0.05,
        )
        return worker, connection

    def lock_dir(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        return Path(directory)

    def equivalent_dsns(self):
        """Three spellings of the worker login's DSN that name the same server."""
        info = conninfo_to_dict(os.environ["RUNTIME_TEST_WORKER_DSN"])
        user, password = info["user"], info["password"]
        host, port, dbname = info["host"], info["port"], info["dbname"]
        return (
            os.environ["RUNTIME_TEST_WORKER_DSN"],
            f"host={host} hostaddr={host} port={port} dbname={dbname} user={user} password={password}",
            f"postgresql://{user}:{password}@{host}:{port}/{dbname}?connect_timeout=5&application_name=other",
        )

    def wait_until_advisory_lock_is_free(self):
        admin = self.connection("ADMIN")
        deadline = time.monotonic() + 10
        while admin.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND objid = %s",
            (WORKER_ADVISORY_LOCK_KEY,),
        ).fetchone()[0]:
            self.assertLess(time.monotonic(), deadline, "the session lock was not released")
            time.sleep(0.05)

    def test_a_lost_session_under_an_active_handler_cannot_admit_a_second_worker(self):
        self.api_records()
        entered, release = threading.Event(), threading.Event()
        executions = []

        def execute(received):
            executions.append(received.request_identity)
            entered.set()
            release.wait(60)
            return JobResult(("result:sha256:1",))

        registry = JobHandlerRegistry((
            JobHandler(dispatch.KIND, "v1", dispatch.IDENTITY, EffectSafety.PURE_REPLAYABLE, execute),
        ))
        seed = DurableJobStore(self.connection("WORKER"))
        jobs = []
        for request in ("request:fence:first", "request:fence:second"):
            job = seed.admit(dispatch.admission(request=request))
            seed.transition(job.job_id, JobState.QUEUED)
            jobs.append(job)
        lock_directory = self.lock_dir()

        first, first_connection = self.fenced_worker(registry, lock_directory)
        failure = {}

        def serve():
            try:
                first.serve()
            except BaseException as error:  # noqa: BLE001 - captured for the assertions
                failure["error"] = error

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(30), "the handler never started")
        running = [job for job in jobs if seed.get(job.job_id).state is JobState.RUNNING]
        self.assertEqual(1, len(running))

        # The database session of the worker ends while its handler is active.
        self.connection("ADMIN").execute(
            "SELECT pg_terminate_backend(%s)", (first_connection.info.backend_pid,)
        )
        self.wait_until_advisory_lock_is_free()

        # A successor must NOT be admitted: the process is alive and still working.
        second, _ = self.fenced_worker(
            registry, lock_directory, dsn=self.equivalent_dsns()[1],
        )
        with self.assertRaises(WorkerLockHeld):
            second.start()
        self.assertEqual(JobState.RUNNING, seed.get(running[0].job_id).state)
        self.assertEqual(
            [JobState.RUNNING], [a.state for a in seed.attempts(running[0].job_id)],
        )
        other = next(job for job in jobs if job is not running[0])
        self.assertEqual(JobState.QUEUED, seed.get(other.job_id).state)
        self.assertEqual(1, len(executions), "a second handler must not have started")

        # The in-flight handler ends; it cannot record (its session is gone) and the
        # worker stops without starting the other Job; only then may a successor run.
        release.set()
        thread.join(30)
        self.assertFalse(thread.is_alive())
        # Its outcome could not be recorded: that is a lost lock, not a defect.
        self.assertIsInstance(failure.get("error"), WorkerLockLost)
        self.assertEqual(1, len(executions))
        self.assertEqual(JobState.QUEUED, seed.get(other.job_id).state)

        third, _ = self.fenced_worker(
            registry, lock_directory, dsn=self.equivalent_dsns()[2],
        )
        self.assertEqual((running[0].job_id,), third.start())
        self.assertEqual(JobState.RECOVERY_REQUIRED, seed.get(running[0].job_id).state)
        self.assertEqual(JobState.QUEUED, seed.get(other.job_id).state)
        self.assertEqual(1, len(executions))
        third.close()

    def test_an_idle_worker_whose_session_is_terminated_stops_with_lock_lost(self):
        self.api_records()
        worker, connection = self.fenced_worker(DATA_LOCAL_JOB_REGISTRY, self.lock_dir())
        worker.start()
        failure = {}

        def run():
            try:
                worker.run()
            except BaseException as error:  # noqa: BLE001
                failure["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.3)
        self.connection("ADMIN").execute(
            "SELECT pg_terminate_backend(%s)", (connection.info.backend_pid,)
        )
        thread.join(15)
        self.assertFalse(thread.is_alive(), "an unconfirmable lock must stop the worker")
        self.assertIsInstance(failure.get("error"), WorkerLockLost)

    def test_equivalent_dsns_share_one_fence_name_and_directory_follows_the_override(self):
        names, identities = set(), set()
        for dsn in self.equivalent_dsns():
            with psycopg.connect(dsn, autocommit=True) as connection:
                identities.add(database_fence_identity(connection))
                names.add(process_lock_path(connection, "/shared/locks").name)
        self.assertEqual(1, len(identities))
        self.assertEqual(1, len(names))
        # The identity is the real server's: it matches what the admin session sees.
        with connect("ADMIN") as admin:
            self.assertEqual(identities, {database_fence_identity(admin)})

    def test_workers_reaching_one_database_by_different_dsns_exclude_each_other(self):
        self.api_records()
        directory = self.lock_dir()
        workers = [self.fenced_worker(DATA_LOCAL_JOB_REGISTRY, directory, dsn=dsn)[0]
                   for dsn in self.equivalent_dsns()]
        workers[0].start()
        for other in workers[1:]:
            with self.assertRaises(WorkerLockHeld):
                other.start()
        workers[0].close()
        # Released: a new worker, through yet another spelling, may now start.
        successor, _ = self.fenced_worker(
            DATA_LOCAL_JOB_REGISTRY, directory, dsn=self.equivalent_dsns()[1],
        )
        successor.start()
        successor.close()

    def test_the_api_host_replaces_a_stale_record_and_a_worker_on_the_old_commit_is_refused(self):
        old, new = "a" * 40, "b" * 40
        self.api_records(old)  # the previous deployment's record
        started = []

        async def transport(*_args, **_kwargs):
            started.append(recorded_deployment_commit(self.connection("WORKER")))

        with mock.patch.dict(os.environ, {"QP_GIT_COMMIT": new, "QP_RUNTIME_DSN": ""}), \
                mock.patch.object(api_server, "run_api_transport_server", side_effect=transport), \
                redirect_stdout(io.StringIO()):
            code = api_server.main(["--runtime-dsn", os.environ["RUNTIME_TEST_API_DSN"]])
        self.assertEqual(0, code)
        # The record already named the new commit when the transport started.
        self.assertEqual([new], started)
        stale, _ = self.worker_parts(commit=old)
        with self.assertRaises(WorkerCommitMismatch):
            stale.start()
        current, _ = self.worker_parts(commit=new)
        current.start()
        current.close()

    def test_the_api_host_without_a_runtime_dsn_starts_no_transport_and_keeps_no_stale_record(self):
        self.api_records("a" * 40)
        started = []

        async def transport(*_args, **_kwargs):
            started.append(1)

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"QP_GIT_COMMIT": "b" * 40, "QP_RUNTIME_DSN": ""}), \
                mock.patch.object(api_server, "run_api_transport_server", side_effect=transport), \
                mock.patch("sys.stderr", err), redirect_stdout(io.StringIO()):
            code = api_server.main([])
        self.assertEqual(api_server.EXIT_RUNTIME_NOT_CONFIGURED, code)
        self.assertEqual([], started)
        self.assertIn("runtime DSN is required", err.getvalue())
        # Nothing was written, and so nothing can be mistaken for the new commit:
        # the worker on the new commit is refused until a real API start records it.
        stale_view, _ = self.worker_parts(commit="b" * 40)
        with self.assertRaises(WorkerCommitMismatch):
            stale_view.start()


class PostgresWorkerProcesses(DisposableCase, unittest.TestCase):
    """#335: the host as real processes (two-process lock proof, kill and restart)."""

    COMMIT = "a" * 40
    HOST = ROOT / "tools" / "job_worker.py"

    def setUp(self):
        DisposableCase.setUp(self)
        record_deployment_commit(self.connection("API"), self.COMMIT)
        self.lock_directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.lock_directory, True)

    def spawn(self, *, commit=None):
        env = {
            **os.environ, "QP_RUNTIME_DSN": os.environ["RUNTIME_TEST_WORKER_DSN"],
            "QP_GIT_COMMIT": commit or self.COMMIT, "PYTHONUNBUFFERED": "1",
            # A shared, explicit lock directory: the file name still comes from
            # the database, so spawned workers contend for one file.
            "QP_WORKER_LOCK_DIR": self.lock_directory,
        }
        process = subprocess.Popen(
            [sys.executable, "-u", str(self.HOST), "--poll-interval", "0.2"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.reap, process)
        lines: queue.Queue = queue.Queue()
        threading.Thread(
            target=lambda: [lines.put(line.rstrip("\n")) for line in process.stdout], daemon=True,
        ).start()
        process.lines = lines
        return process

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def wait_for(self, process, text, timeout=30):
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            try:
                line = process.lines.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and process.lines.empty():
                    break
                continue
            seen.append(line)
            if text in line:
                return
        self.fail(f"{text!r} not seen; output={seen!r} exit={process.poll()!r}")

    def seed_running_job(self, request):
        store = DurableJobStore(self.connection("WORKER"))
        job = store.admit(replace(j03.admission(), request_identity=request))
        store.transition(job.job_id, JobState.QUEUED)
        store.claim(job.job_id)
        return job, store

    def test_a_second_worker_exits_nonzero_without_touching_any_job(self):
        first = self.spawn()
        self.wait_for(first, "J03_WORKER: READY")
        job, store = self.seed_running_job("request:two-process")  # after worker 1's recovery
        before = (store.get(job.job_id), store.attempts(job.job_id))
        second = self.spawn()
        self.assertEqual(2, second.wait(timeout=30))
        self.assertIn("REFUSED", second.stderr.read())
        self.assertEqual(before, (store.get(job.job_id), store.attempts(job.job_id)))
        self.assertEqual(JobState.RUNNING, store.get(job.job_id).state)
        self.assertIsNone(first.poll(), "the first worker must be unaffected")

    def test_a_worker_with_another_commit_refuses_to_start_and_touches_nothing(self):
        job, store = self.seed_running_job("request:commit")
        before = (store.get(job.job_id), store.attempts(job.job_id))
        process = self.spawn(commit="b" * 40)
        self.assertEqual(3, process.wait(timeout=30))
        self.assertIn("differs", process.stderr.read())
        self.assertEqual(before, (store.get(job.job_id), store.attempts(job.job_id)))
        # The lock was released: a matching worker can start right away.
        good = self.spawn()
        self.wait_for(good, "J03_WORKER: READY")

    def test_a_running_job_left_by_a_killed_worker_becomes_recovery_required_once(self):
        first = self.spawn()
        self.wait_for(first, "J03_WORKER: READY")
        job, store = self.seed_running_job("request:killed")  # in flight when it dies
        first.kill()
        first.wait(timeout=10)
        deadline = time.monotonic() + 30
        while True:  # the dead session's lock is released by the server
            second = self.spawn()
            try:
                self.wait_for(second, "J03_WORKER: READY", timeout=15)
                break
            except AssertionError:
                self.assertLess(time.monotonic(), deadline, "no worker could start")
                self.assertEqual(2, second.poll(), "only a held lock may delay start")
                time.sleep(0.3)
        recovered = store.get(job.job_id)
        self.assertEqual(JobState.RECOVERY_REQUIRED, recovered.state)
        self.assertEqual("restart_recovery_required", recovered.reason_code)
        attempts = store.attempts(job.job_id)
        self.assertEqual([(1, JobState.RECOVERY_REQUIRED)], [(a.attempt_no, a.state) for a in attempts])
        time.sleep(1.0)  # the running worker must not redispatch it
        self.assertEqual(JobState.RECOVERY_REQUIRED, store.get(job.job_id).state)
        self.assertEqual(attempts, store.attempts(job.job_id))


class PostgresJobTransport(DisposableCase, job_transport.JobTransportV1Tests):
    """#340: the j02-job-v1 contract on the runtime schema with the real roles.

    The family runs as the API login (admission, status, cancel); the worker
    under it is the real #335 lifecycle as the worker login, with the real
    two-part lock.
    """

    def open_store(self):
        return DurableJobStore(self.connection("API"))

    def provider(self):
        @contextmanager
        def session():
            connection = connect("API")
            try:
                yield DurableJobStore(connection)
            finally:
                connection.close()
        return session

    def corrupt_payload(self, job_id):
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.durable_jobs SET payload = '{}' WHERE job_id = %s", (job_id,))

    def make_worker(self, registry, stop):
        record_deployment_commit(self.connection("API"), "c" * 40)
        connection = self.connection("WORKER")
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        return JobWorker(
            store=DurableJobStore(connection), registry=registry,
            lock=CompositeWorkerLock([
                ProcessLifetimeLock(process_lock_path(connection, directory)),
                PostgresAdvisoryLock(connection),
            ]),
            commit_record=PostgresCommitRecord(connection), commit="c" * 40,
            stop=stop, poll_interval=0.01,
        )


class PostgresInputs(DisposableCase, k12.AdmittedInputManifestV1Tests):
    def store(self):
        return AdmittedInputStore(self.connection())

    def test_concurrent_delivery_starts_record_exactly_one_attempt(self):
        """#357: the attempt number is written with the state, under the store's lock."""

        store = self.store()
        sealed = store.seal(store.admit(k12.manifest()).admission_id)

        def begin(_):
            with connect("API") as connection:
                connection.execute("SET statement_timeout = '5s'")
                try:
                    return AdmittedInputStore(connection).begin_delivery(sealed.admission_id).delivery_attempt
                except AdmittedInputConflict:
                    return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(begin, range(4)))
        self.assertEqual([1], [attempt for attempt in results if attempt is not None])
        record = store.get(sealed.admission_id)
        self.assertEqual((AdmittedInputState.DELIVERY_PENDING, 1), (record.state, record.delivery_attempt))

    def test_a_stale_report_loses_to_a_concurrent_newer_attempt(self):
        store = self.store()
        sealed = store.seal(store.admit(k12.manifest()).admission_id)
        store.begin_delivery(sealed.admission_id)  # attempt 1
        digest = sealed.manifest.manifest_digest

        def stale(_):
            with connect("API") as connection:
                connection.execute("SET statement_timeout = '5s'")
                try:
                    AdmittedInputStore(connection).record_delivery(
                        sealed.admission_id, digest, delivery_attempt=1,
                    )
                    return "applied"
                except AdmittedInputError as error:
                    return type(error).__name__

        def newer(_):
            with connect("API") as connection:
                connection.execute("SET statement_timeout = '5s'")
                other = AdmittedInputStore(connection)
                try:
                    other.record_delivery_failure(
                        sealed.admission_id, "delivery_interrupted", delivery_attempt=1,
                    )
                    return other.begin_delivery(sealed.admission_id).delivery_attempt
                except AdmittedInputError:
                    return None  # the stale report ended attempt 1 first

        # The two orders are both legal: the stale report runs first (applied to the
        # attempt it names) or after the newer attempt began (refused). It is never
        # applied to attempt 2.
        with ThreadPoolExecutor(max_workers=2) as pool:
            reported, started = pool.submit(stale, 0), pool.submit(newer, 0)
            outcome, attempt = reported.result(), started.result()
        record = store.get(sealed.admission_id)
        if outcome == "applied":
            self.assertEqual(AdmittedInputState.DELIVERED, record.state)  # attempt 1 ended; the newer one conflicted
        else:
            self.assertEqual(
                (AdmittedInputState.DELIVERY_PENDING, 2, None),
                (record.state, record.delivery_attempt, record.delivery_failure_reason),
            )
            self.assertEqual(2, attempt)


class PostgresAdmittedInputTransport(
    DisposableCase, admitted_transport.AdmittedInputTransportV1Tests
):
    """#341: the j02-admitted-input-v1 contract on the runtime schema as the API login."""

    def setUp(self):
        DisposableCase.setUp(self)
        admitted_transport.AdmittedInputTransportV1Tests.setUp(self)

    def open_store(self):
        return AdmittedInputStore(self.connection("API"))

    def provider(self):
        @contextmanager
        def session():
            connection = connect("API")
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()
        return session


class PostgresResultImportTransport(
    DisposableCase, result_import.ResultImportTransportV1Tests
):
    """#342: the j02-result-import-v1 contract on the runtime schema as the API login.

    K13 registers the Experiment write and the evidence row in one transaction on
    one connection (ADR-0068 s4), so the service shares one autocommit connection
    with its repository and ledger.
    """

    def setUp(self):
        DisposableCase.setUp(self)
        result_import.ResultImportTransportV1Tests.setUp(self)

    def build_backend(self):
        self.admissions = AdmittedInputStore(self.connection())
        self.shared = self.connection()
        self.evidence = GovernedResultEvidenceStore(self.shared)
        self.experiments = ExperimentRepository(self.shared)

    def service_session(self, verify=None):
        verifier = self.staging.verify_output if verify is None else verify

        @contextmanager
        def session():
            yield GovernedResultImportService(
                self.admissions, self.experiments, self.evidence, verifier, result_import.CONTRACTS,
            )
        return session

    def bundle_rows(self):
        with connect("ADMIN") as admin:
            return admin.execute("SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0]

    def stored_payload(self, bundle_digest):
        with connect("ADMIN") as admin:
            return admin.execute(
                "SELECT payload FROM runtime.governed_result_bundles WHERE bundle_digest = %s",
                (bundle_digest,),
            ).fetchone()[0]


class PostgresResults(DisposableCase, k13.GovernedResultImportV1Tests):
    def setUp(self):
        DisposableCase.setUp(self)
        self.admissions = AdmittedInputStore(self.connection())
        # ADR-0068 s4 / #332: the Experiment write and the evidence record share
        # one autocommit connection so that they can be one transaction.
        self.shared = self.connection()
        self.evidence = GovernedResultEvidenceStore(self.shared)
        self.experiments = ExperimentRepository(self.shared)
        self.service = GovernedResultImportService(
            self.admissions, self.experiments, self.evidence,
            lambda evidence: evidence.content_checksum,
            (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
        )

    # -- #332: atomic K13 registration (ADR-0068 s4) ---------------------------

    TABLES = (
        "experiment.run_specs", "experiment.runs", "experiment.artifacts",
        "runtime.governed_result_bundles", "runtime.governed_result_outputs",
    )
    NOTHING = dict.fromkeys(TABLES, 0)
    ONE_IMPORT = {
        "experiment.runs": 1, "experiment.artifacts": 1,
        "runtime.governed_result_bundles": 1, "runtime.governed_result_outputs": 1,
    }

    def counts(self):
        """Committed row counts, read through an independent connection."""
        with connect("ADMIN") as admin:
            return {
                name: admin.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in self.TABLES
            }

    def import_counts(self):
        return {
            name: count for name, count in self.counts().items()
            if name != "experiment.run_specs"
        }

    def inject_after_experiment_write(self, error):
        """Fail the evidence write: the Experiment write has already happened."""
        def fault(bundle):
            raise error
        self.evidence.record = fault

    def test_fault_between_experiment_and_evidence_leaves_neither_row(self):
        bundle = self.bundle()
        self.inject_after_experiment_write(RuntimeError("injected before evidence"))
        with self.assertRaisesRegex(RuntimeError, "injected before evidence"):
            self.service.import_result(bundle)
        self.assertEqual(self.NOTHING, self.counts())
        # The connection is reusable: the transaction ended, it is not left aborted.
        self.assertEqual(
            psycopg.pq.TransactionStatus.IDLE, self.shared.info.transaction_status,
        )

    def test_fault_after_evidence_write_also_rolls_back_the_experiment(self):
        bundle = self.bundle()
        record = self.evidence.record

        def record_then_fail(candidate):
            record(candidate)
            raise RuntimeError("injected after evidence")

        self.evidence.record = record_then_fail
        with self.assertRaisesRegex(RuntimeError, "injected after evidence"):
            self.service.import_result(bundle)
        self.assertEqual(self.NOTHING, self.counts())

    def test_fault_inside_the_experiment_write_leaves_no_partial_experiment(self):
        bundle = self.bundle()

        def fail_artifact(*args, **kwargs):
            raise RuntimeError("injected artifact failure")

        # By now the run spec and the Run are written and the Run is SUCCEEDED.
        self.experiments._ensure_artifact = fail_artifact
        with self.assertRaises(GovernedResultImportError):
            self.service.import_result(bundle)
        self.assertEqual(self.NOTHING, self.counts())

    def test_resubmission_after_a_fault_matches_an_uninterrupted_import(self):
        bundle = self.bundle()
        self.inject_after_experiment_write(RuntimeError("injected"))
        with self.assertRaises(RuntimeError):
            self.service.import_result(bundle)
        del self.evidence.record  # drop the instance attribute shadowing the method
        recovered = self.service.import_result(bundle)
        self.assertEqual(RunState.SUCCEEDED, recovered.run_record.state)
        self.assertEqual(self.ONE_IMPORT, self.import_counts())
        after_recovery = self.counts()
        # Uninterrupted import of the same bundle from an empty state.
        with connect("ADMIN") as admin:
            admin.execute(
                "TRUNCATE runtime.governed_result_bundles, runtime.governed_result_outputs, "
                "experiment.studies CASCADE"
            )
        self.assertEqual(self.NOTHING, self.counts())
        uninterrupted = self.service.import_result(bundle)
        self.assertEqual(uninterrupted, recovered)
        self.assertEqual(after_recovery, self.counts())
        # An exact resubmission of an accepted bundle stays idempotent.
        self.assertEqual(recovered, self.service.import_result(bundle))
        self.assertEqual(after_recovery, self.counts())

    def test_without_a_shared_transaction_the_experiment_survives_the_fault(self):
        # Control: the pre-#332 composition (separate per-call commits). This is
        # the crash window the shared transaction closes, and shows the tests
        # above fail when the transaction boundary is removed.
        bundle = self.bundle()
        separate = ExperimentRepository(self.connection(autocommit=False))
        record_trial_attempt(
            separate,
            TrialAttemptResult(
                bundle.run_identity, RunState.SUCCEEDED,
                artifact_identities=tuple(o.registration.identity for o in bundle.outputs),
            ),
            artifact_registrations=tuple(o.registration for o in bundle.outputs),
        )
        # ...the process dies here: no evidence write.
        counts = self.counts()
        self.assertEqual(1, counts["experiment.runs"])
        self.assertEqual(1, counts["experiment.artifacts"])
        self.assertEqual(0, counts["runtime.governed_result_bundles"])

    def test_postgresql_composition_requires_one_shared_connection(self):
        other = ExperimentRepository(self.connection(autocommit=False))
        with self.assertRaisesRegex(ValueError, "share one connection"):
            GovernedResultImportService(
                self.admissions, other, self.evidence,
                lambda evidence: evidence.content_checksum,
                (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
            )

    def test_concurrent_identical_imports_converge_without_deadlock(self):
        bundle = self.bundle()

        def run_import(_):
            connection = connect("API")
            try:
                service = GovernedResultImportService(
                    AdmittedInputStore(connection), ExperimentRepository(connection),
                    GovernedResultEvidenceStore(connection),
                    lambda evidence: evidence.content_checksum,
                    (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
                )
                return service.import_result(bundle)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run_import, range(4)))
        self.assertEqual(1, len(set(results)))
        self.assertEqual(self.ONE_IMPORT, self.import_counts())

    def test_payload_bytes_and_identities_match_sqlite_and_survive_reconnect(self):
        bundle = self.bundle()
        native = k13.GovernedResultImportV1Tests("test_exact_resubmission_is_idempotent_and_conflicting_role_is_refused")
        native.setUp()
        self.addCleanup(native.admissions.connection.close)
        self.addCleanup(native.evidence.connection.close)
        local_bundle = native.bundle()
        self.assertEqual(local_bundle.canonical_payload_v1, bundle.canonical_payload_v1)
        self.assertEqual(local_bundle.bundle_digest, bundle.bundle_digest)
        self.assertEqual(local_bundle.admission_id, bundle.admission_id)
        native.service.import_result(local_bundle)
        self.service.import_result(bundle)
        with connect("API") as api:
            payload = api.execute(
                "SELECT payload FROM runtime.governed_result_bundles WHERE bundle_digest = %s",
                (bundle.bundle_digest,),
            ).fetchone()[0]
            self.assertEqual(local_bundle.canonical_payload_v1.encode(), payload.encode())
            manifest_payload = api.execute(
                "SELECT manifest_payload FROM runtime.admitted_inputs WHERE admission_id = %s",
                (bundle.admission_id,),
            ).fetchone()[0]
            self.assertEqual(local_bundle.consumed_manifest.canonical_payload_v1.encode(), manifest_payload.encode())
            GovernedResultEvidenceStore(api).record(bundle)
            self.assertEqual(1, api.execute("SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0])

    def test_concurrent_evidence_resubmission_and_conflicting_output(self):
        bundle = self.bundle()

        def record(candidate):
            with connect("API") as connection:
                GovernedResultEvidenceStore(connection).record(candidate)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(record, [bundle, bundle]))
        conflicting = replace(bundle, configuration={"different": True})
        with self.assertRaises(GovernedResultRefused):
            record(conflicting)
        with connect("API") as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0])

    def test_the_pre_check_is_one_consistent_read_against_an_identical_commit(self):
        """#354, deterministically: an identical import cannot commit between the reads."""
        bundle = self.bundle()
        winner = GovernedResultEvidenceStore(self.connection())
        checker_connection = self.connection()
        checker = GovernedResultEvidenceStore(checker_connection)
        real_execute = checker._db.execute
        observed = {"fired": False, "finished_during_check": None, "error": None}
        racing = threading.Thread(target=lambda: self._record_capturing(winner, bundle, observed))

        def interleaved(statement, parameters=()):
            result = real_execute(statement, parameters)
            if not observed["fired"] and "FROM governed_result_bundles" in statement:
                observed["fired"] = True  # the checker has just seen "no such bundle"
                racing.start()
                racing.join(0.5)          # give the identical import every chance to commit
                observed["finished_during_check"] = not racing.is_alive()
            return result

        checker._db.execute = interleaved
        checker.assert_admissible(bundle)  # must not refuse an identical bundle
        racing.join(30)
        self.assertTrue(observed["fired"], "the interleaving was not exercised")
        self.assertFalse(
            observed["finished_during_check"],
            "an identical import committed between the pre-check's reads",
        )
        self.assertFalse(racing.is_alive())
        self.assertIsNone(observed["error"])
        with connect("ADMIN") as admin:
            self.assertEqual(1, admin.execute(
                "SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0])

    @staticmethod
    def _record_capturing(store, bundle, observed):
        try:
            store.record(bundle)
        except BaseException as error:  # noqa: BLE001 - reported by the test
            observed["error"] = error

    def test_concurrent_identical_imports_converge_across_many_rounds(self):
        bundle = self.bundle()

        def run_import(_):
            with connect("API") as connection:
                service = GovernedResultImportService(
                    AdmittedInputStore(connection), ExperimentRepository(connection),
                    GovernedResultEvidenceStore(connection),
                    lambda evidence: evidence.content_checksum,
                    (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
                )
                return service.import_result(bundle)

        for round_number in range(6):
            with connect("ADMIN") as admin:
                admin.execute(
                    "TRUNCATE runtime.governed_result_bundles, runtime.governed_result_outputs, "
                    "experiment.studies CASCADE"
                )
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(run_import, range(6)))
            with self.subTest(round=round_number):
                self.assertEqual(1, len(set(results)))
                self.assertEqual(self.ONE_IMPORT, self.import_counts())

    def test_corrupt_bundle_payload_is_refused(self):
        bundle = self.bundle()
        self.evidence.record(bundle)
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.governed_result_bundles SET payload = '{}' WHERE bundle_digest = %s", (bundle.bundle_digest,))
        with self.assertRaisesRegex(GovernedResultRefused, "payload conflicts"):
            self.evidence.record(bundle)


class PostgresSafety(DisposableCase, unittest.TestCase):
    def test_job_bytes_match_sqlite_and_conflicting_admission_is_refused(self):
        sqlite = sqlite3.connect(":memory:")
        self.addCleanup(sqlite.close)
        local = DurableJobStore(sqlite).admit(j03.admission())
        store = DurableJobStore(self.connection())
        remote = store.admit(j03.admission())
        self.assertEqual(local, remote)
        payload = store.connection.execute("SELECT payload FROM runtime.durable_jobs").fetchone()[0]
        self.assertEqual(sqlite.execute("SELECT payload FROM durable_jobs").fetchone()[0].encode(), payload.encode())
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.durable_jobs SET payload = '{}' WHERE job_id = %s", (remote.job_id,))
        with self.assertRaises(DurableJobConflict):
            store.admit(j03.admission())

    def test_conflicting_admission_manifest_is_refused(self):
        store = AdmittedInputStore(self.connection())
        record = store.admit(k12.manifest())
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.admitted_inputs SET manifest_payload = '{}' WHERE admission_id = %s", (record.admission_id,))
        with self.assertRaises(AdmittedInputConflict):
            store.admit(k12.manifest())

    def test_concurrent_job_admission_and_claim_have_one_attempt(self):
        def admit(_):
            with connect("API") as connection:
                return DurableJobStore(connection).admit(j03.admission())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(admit, range(2))
        self.assertEqual(first, second)
        store = DurableJobStore(self.connection("WORKER"))
        store.transition(first.job_id, JobState.QUEUED)

        def claim(_):
            with connect("WORKER") as connection:
                return DurableJobStore(connection).transition(first.job_id, JobState.RUNNING)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(claim, range(2)))
        self.assertEqual(claimed[0], claimed[1])
        self.assertEqual((1,), tuple(item.attempt_no for item in store.attempts(first.job_id)))

    def test_delivery_cannot_revive_expired_input(self):
        store = AdmittedInputStore(self.connection())
        record = store.admit(k12.manifest())
        store.seal(record.admission_id)
        store.expire(
            record.admission_id,
            delivery_attempt=store.begin_delivery(record.admission_id).delivery_attempt,
        )
        with self.assertRaises(AdmittedInputError):
            store.record_delivery(
                record.admission_id, record.manifest.manifest_digest, delivery_attempt=1,
            )
        self.assertEqual(AdmittedInputState.EXPIRED, store.get(record.admission_id).state)

    def test_admission_read_check_write_obeys_transaction_lock(self):
        def admit():
            with connect("API") as connection:
                connection.execute("SET statement_timeout = '5s'")
                return DurableJobStore(connection).admit(j03.admission())

        with ThreadPoolExecutor(max_workers=1) as pool:
            with connect("ADMIN") as admin:
                with admin.transaction():
                    admin.execute("SELECT pg_advisory_xact_lock(%s)", (33101,))
                    pending = pool.submit(admit)
                    try:
                        with self.assertRaises(TimeoutError):
                            pending.result(timeout=0.5)
                    finally:
                        # The pool must never wait for a task behind a held
                        # lock when the assertion detects the regression.
                        admin.execute("SELECT 1")
            self.assertEqual(JobState.ADMITTED, pending.result(timeout=6).state)

    def test_ingest_is_actually_refused_on_every_runtime_table(self):
        ingest = self.connection("INGEST")
        for table in ("durable_jobs", "durable_job_attempts", "admitted_inputs",
                      "governed_result_bundles", "governed_result_outputs"):
            with self.subTest(table=table), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                ingest.execute(f"SELECT * FROM runtime.{table}")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            ingest.execute("SELECT * FROM experiment.studies")

    def test_worker_cannot_access_k12_k13_experiment_or_ddl(self):
        worker = self.connection("WORKER")
        for statement in (
            "SELECT * FROM runtime.admitted_inputs",
            "SELECT * FROM runtime.governed_result_bundles",
            "SELECT * FROM runtime.governed_result_outputs",
            "SELECT * FROM experiment.studies",
            "CREATE TABLE runtime.unauthorized (id integer)",
            "DELETE FROM runtime.durable_jobs",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                worker.execute(statement)

    def test_api_is_reader_in_catalog_and_not_runtime_ddl_or_delete(self):
        api = self.connection()
        api.execute("SELECT * FROM catalog.datasets LIMIT 0")
        for statement in (
            "CREATE TABLE runtime.unauthorized (id integer)",
            "DELETE FROM runtime.admitted_inputs",
            "UPDATE catalog.datasets SET dataset_id = dataset_id",
            "SET ROLE runtime_owner",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                api.execute(statement)

    def test_runtime_owner_and_reader_roles(self):
        admin = self.connection("ADMIN")
        rows = admin.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname IN ('runtime_owner','platform_api','platform_worker') ORDER BY rolname"
        ).fetchall()
        self.assertEqual([
            ("platform_api", True, False, False, False),
            ("platform_worker", True, False, False, False),
            ("runtime_owner", False, False, False, False),
        ], rows)
        self.assertEqual([("runtime_owner",)], admin.execute(
            "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relnamespace = 'runtime'::regnamespace AND relkind = 'r'"
        ).fetchall())
        admin.execute("SET ROLE runtime_reader")
        admin.execute("SELECT * FROM runtime.admitted_inputs")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            admin.execute("UPDATE runtime.admitted_inputs SET state = state")
        admin.execute("RESET ROLE")

    def test_constructor_does_not_close_or_commit_caller_connection(self):
        connection = self.connection(autocommit=False)
        with self.assertRaisesRegex(ValueError, "autocommit"):
            DurableJobStore(connection)
        self.assertFalse(connection.closed)
        auto = self.connection()
        DurableJobStore(auto).admit(j03.admission())
        self.assertFalse(auto.closed)
        self.assertEqual(psycopg.pq.TransactionStatus.IDLE, auto.info.transaction_status)


if __name__ == "__main__":
    for role in ("ADMIN", "API", "WORKER", "INGEST"):
        if not os.environ.get(f"RUNTIME_TEST_{role}_DSN"):
            raise SystemExit(f"RUNTIME_TEST_{role}_DSN is required (no PostgreSQL proof executed)")
    unittest.main(verbosity=2)
