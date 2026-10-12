"""Closed data-local J03 registry and dispatch (#334, P04a).

``JobDispatchV1Tests`` is backend-neutral: ``store()`` returns a *new* store on
its own connection, so the "durable before the handler runs" proof reads through
an independent connection. SQLite here; tests/integration_runtime_stores_postgres.py
subclasses it unchanged for the PostgreSQL runtime schema and its worker role.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.durable_jobs import (  # noqa: E402
    DurableJobConflict,
    DurableJobNotFound,
    DurableJobStore,
    EffectSafety,
    JobAdmission,
    JobState,
)
from quant_platform.application.job_dispatch import (  # noqa: E402
    DATA_LOCAL_JOB_HANDLERS,
    DATA_LOCAL_JOB_REGISTRY,
    JobCancelled,
    JobDispatcher,
    JobHandler,
    JobHandlerFailure,
    JobHandlerNotRegistered,
    JobHandlerRegistry,
    JobResult,
)

KIND = "catalog-maintenance-v1"
IDENTITY = "implementation:sha256:aaa"


def admission(
    *, kind: str = KIND, request: str = "request:1", identity: str = IDENTITY,
    contract: str = "v1", safety: EffectSafety = EffectSafety.PURE_REPLAYABLE,
) -> JobAdmission:
    return JobAdmission(
        operation_kind=kind, contract_version=contract, implementation_identity=identity,
        request_identity=request, canonical_parameters={"partition": "2024-01-01"},
        input_identities=("dataset:sha256:aaa",), effect_safety=safety,
    )


def handler(execute, *, kind: str = KIND, identity: str = IDENTITY,
            safety: EffectSafety = EffectSafety.PURE_REPLAYABLE) -> JobHandler:
    return JobHandler(kind, "v1", identity, safety, execute)


def succeeds(_admission: JobAdmission) -> JobResult:
    return JobResult(("result:sha256:1",))


class JobDispatchV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "jobs.sqlite"

    def store(self) -> DurableJobStore:
        connection = sqlite3.connect(self._path)
        self.addCleanup(connection.close)
        return DurableJobStore(connection)

    def dispatcher(self, execute=succeeds, **handler_options):
        store = self.store()
        registry = JobHandlerRegistry((handler(execute, **handler_options),))
        return JobDispatcher(store, registry), store

    def queued(self, dispatcher: JobDispatcher, **options):
        job = dispatcher.admit(admission(**options))
        dispatcher.store.transition(job.job_id, JobState.QUEUED)
        return job

    # -- registry: closed, declared, data-local only (AC1, AC4, AC5) ----------

    def test_the_shipped_registry_is_empty_and_refuses_every_admission(self) -> None:
        self.assertEqual((), DATA_LOCAL_JOB_HANDLERS)
        self.assertEqual(0, len(DATA_LOCAL_JOB_REGISTRY))
        store = self.store()
        dispatcher = JobDispatcher(store, DATA_LOCAL_JOB_REGISTRY)
        with self.assertRaises(JobHandlerNotRegistered):
            dispatcher.admit(admission())
        # Refused before it was persisted: there is no such Job.
        with self.assertRaises(DurableJobNotFound):
            store.get(admission().job_id)

    def test_admission_requires_the_exact_declared_operation(self) -> None:
        dispatcher, store = self.dispatcher()
        self.assertEqual(JobState.ADMITTED, dispatcher.admit(admission()).state)
        for label, candidate in {
            "unregistered kind": admission(kind="catalog-compaction-v1"),
            "other implementation": admission(identity="implementation:sha256:bbb"),
            "other contract": admission(contract="v2"),
            "other effect safety": admission(safety=EffectSafety.NO_EFFECT),
        }.items():
            with self.subTest(label):
                with self.assertRaises(JobHandlerNotRegistered):
                    dispatcher.admit(candidate)
                with self.assertRaises(DurableJobNotFound):
                    store.get(candidate.job_id)

    def test_replay_sweep_training_and_pbo_kinds_cannot_be_registered(self) -> None:
        for kind in (
            "replay-historical-v1", "deterministic-replay-v1", "parameter-sweep-v1",
            "sweeps-v1", "supervised-train-evaluate-v1", "training-v1", "validation-pbo-v1",
            "evaluate-pbo-v1",
        ):
            with self.subTest(kind):
                with self.assertRaisesRegex(ValueError, "not data-local"):
                    handler(succeeds, kind=kind)
                dispatcher = JobDispatcher(self.store(), DATA_LOCAL_JOB_REGISTRY)
                with self.assertRaises(JobHandlerNotRegistered):
                    dispatcher.admit(admission(kind=kind))

    def test_a_kind_can_never_name_a_callable_or_module(self) -> None:
        for kind in (
            "os.system", "package.module:function", "package/module", "package_module",
            "Upper-Case", "", " ", "-leading", "trailing-", "double--hyphen",
        ):
            with self.subTest(kind):
                with self.assertRaises(ValueError):
                    handler(succeeds, kind=kind)

    def test_the_registry_is_closed_and_validates_its_entries(self) -> None:
        registry = JobHandlerRegistry((handler(succeeds),))
        self.assertEqual((KIND,), registry.operation_kinds)
        self.assertFalse(hasattr(registry, "register"))
        with self.assertRaises(AttributeError):
            registry.extra = 1
        with self.assertRaisesRegex(ValueError, "unique"):
            JobHandlerRegistry((handler(succeeds), handler(succeeds)))
        for bad in (succeeds, KIND, ("a", "b"), None):
            with self.subTest(entry=repr(bad)):
                with self.assertRaises(TypeError):
                    JobHandlerRegistry((bad,))
        with self.assertRaises(TypeError):
            JobHandler(KIND, "v1", IDENTITY, EffectSafety.NO_EFFECT, "package.module:run")
        with self.assertRaises(TypeError):
            JobDispatcher(self.store(), (handler(succeeds),))

    # -- dispatch: durable RUNNING before the handler; outcomes (AC2) ---------

    def test_the_attempt_is_durable_as_running_before_the_handler_runs(self) -> None:
        observed = {}
        observer = self.store()

        def execute(received: JobAdmission) -> JobResult:
            job = observer.get(received.job_id)  # an independent connection
            observed["state"] = job.state
            observed["attempts"] = [(a.attempt_no, a.state) for a in observer.attempts(job.job_id)]
            return JobResult(("result:sha256:1",))

        dispatcher, _ = self.dispatcher(execute)
        job = self.queued(dispatcher)
        result = dispatcher.dispatch_next()
        self.assertEqual(JobState.RUNNING, observed["state"])
        self.assertEqual([(1, JobState.RUNNING)], observed["attempts"])
        self.assertEqual(JobState.SUCCEEDED, result.job.state)
        self.assertEqual(job.job_id, result.job.job_id)
        self.assertEqual(("result:sha256:1",), result.job.result_references)
        self.assertEqual(
            [(1, JobState.SUCCEEDED)],
            [(a.attempt_no, a.state) for a in dispatcher.store.attempts(job.job_id)],
        )

    def test_an_unexpected_exception_is_a_stable_failure_without_its_text(self) -> None:
        def execute(_admission):
            raise RuntimeError("secret-token /srv/path password=hunter2")

        dispatcher, store = self.dispatcher(execute)
        job = self.queued(dispatcher)
        failed = dispatcher.dispatch_next().job
        self.assertEqual(JobState.FAILED, failed.state)
        self.assertEqual("handler_failed", failed.reason_code)
        self.assertEqual((), failed.result_references)
        attempt = store.attempts(job.job_id)[0]
        for text in (failed.reason_code, failed.diagnostic_reference, attempt.failure_code,
                     attempt.evidence_reference):
            self.assertNotIn("secret", str(text))
            self.assertNotIn("hunter2", str(text))
        self.assertEqual((JobState.FAILED, "handler_failed"), (attempt.state, attempt.failure_code))

    def test_a_handler_may_declare_its_own_stable_reason_code(self) -> None:
        def execute(_admission):
            raise JobHandlerFailure("partition_missing", "evidence:catalog:gap-7")

        dispatcher, store = self.dispatcher(execute)
        job = self.queued(dispatcher)
        failed = dispatcher.dispatch_next().job
        self.assertEqual(("partition_missing", "evidence:catalog:gap-7"),
                         (failed.reason_code, failed.diagnostic_reference))
        attempt = store.attempts(job.job_id)[0]
        self.assertEqual("partition_missing", attempt.failure_code)
        with self.assertRaises(ValueError):
            JobHandlerFailure("", None)
        with self.assertRaises(ValueError):
            JobHandlerFailure("x" * 513)

    def test_success_without_result_references_is_refused_and_never_recorded(self) -> None:
        for label, outcome in {
            "no references": JobResult(()),
            "blank reference": JobResult((" ",)),
            "none": None,
            "wrong type": {"result_references": ["result:sha256:1"]},
            "oversized diagnostic": JobResult(("result:sha256:1",), "d" * 513),
        }.items():
            with self.subTest(label):
                dispatcher, store = self.dispatcher(lambda _a, outcome=outcome: outcome)
                job = self.queued(dispatcher, request=f"request:{label}")
                recorded = dispatcher.dispatch_next().job
                self.assertEqual(JobState.FAILED, recorded.state)
                self.assertEqual("handler_result_invalid", recorded.reason_code)
                self.assertEqual((), recorded.result_references)
                self.assertEqual(JobState.FAILED, store.attempts(job.job_id)[0].state)

    # -- no automatic retry (AC3) --------------------------------------------

    def test_a_failed_job_is_never_redispatched_by_a_later_call(self) -> None:
        calls = []

        def execute(_admission):
            calls.append(1)
            raise RuntimeError("boom")

        dispatcher, store = self.dispatcher(execute)
        job = self.queued(dispatcher)
        self.assertEqual(JobState.FAILED, dispatcher.dispatch_next().job.state)
        for _ in range(3):
            self.assertIsNone(dispatcher.dispatch_next().job)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(store.attempts(job.job_id)))
        self.assertEqual(JobState.FAILED, store.get(job.job_id).state)

    def test_recovery_required_is_not_dispatched_until_explicitly_requeued(self) -> None:
        calls = []

        def execute(_admission):
            calls.append(1)
            return JobResult(("result:sha256:1",))

        dispatcher, store = self.dispatcher(execute)
        job = dispatcher.admit(admission())
        store.transition(job.job_id, JobState.QUEUED)
        store.claim(job.job_id)  # a process that then died
        self.assertEqual((job.job_id,), tuple(r.job_id for r in store.recover_after_restart()))
        self.assertEqual(JobState.RECOVERY_REQUIRED, store.get(job.job_id).state)
        self.assertIsNone(dispatcher.dispatch_next().job)
        self.assertEqual([], calls)
        store.retry_or_resume(job.job_id)  # the explicit, proof-gated decision
        rerun = dispatcher.dispatch_next().job
        self.assertEqual(JobState.SUCCEEDED, rerun.state)
        self.assertEqual([1], calls)
        self.assertEqual([1, 2], [a.attempt_no for a in store.attempts(job.job_id)])

    def test_base_exceptions_propagate_and_leave_the_job_for_restart_recovery(self) -> None:
        def execute(_admission):
            raise KeyboardInterrupt

        dispatcher, store = self.dispatcher(execute)
        job = self.queued(dispatcher)
        with self.assertRaises(KeyboardInterrupt):
            dispatcher.dispatch_next()
        self.assertEqual(JobState.RUNNING, store.get(job.job_id).state)
        recovered = store.recover_after_restart()
        self.assertEqual(JobState.RECOVERY_REQUIRED, recovered[0].state)
        self.assertIsNone(dispatcher.dispatch_next().job)

    # -- claim and selection -------------------------------------------------

    def test_only_a_queued_job_can_be_claimed_and_only_once(self) -> None:
        store = self.store()
        job = store.admit(admission())
        with self.assertRaises(DurableJobConflict):
            store.claim(job.job_id)  # ADMITTED
        store.transition(job.job_id, JobState.QUEUED)
        self.assertEqual(JobState.RUNNING, store.claim(job.job_id).state)
        with self.assertRaises(DurableJobConflict):
            store.claim(job.job_id)  # a second claimer is refused, not told "ok"
        self.assertEqual(1, len(store.attempts(job.job_id)))
        with self.assertRaises(DurableJobNotFound):
            store.claim("job-v1:sha256:missing")

    def test_unregistered_queued_jobs_are_left_untouched_and_do_not_block_others(self) -> None:
        store = self.store()
        foreign = store.admit(admission(kind="catalog-compaction-v1", request="request:foreign"))
        store.transition(foreign.job_id, JobState.QUEUED)
        stale = store.admit(admission(identity="implementation:sha256:old", request="request:old"))
        store.transition(stale.job_id, JobState.QUEUED)
        dispatcher = JobDispatcher(store, JobHandlerRegistry((handler(succeeds),)))
        mine = self.queued(dispatcher, request="request:mine")
        result = dispatcher.dispatch_next()
        self.assertEqual(mine.job_id, result.job.job_id)
        # Order is deterministic but not a queue: how many it passed over before
        # reaching its own Job depends on the hash order of the three ids.
        self.assertLessEqual(result.skipped_unregistered, 2)
        for untouched in (foreign, stale):
            self.assertEqual(JobState.QUEUED, store.get(untouched.job_id).state)
            self.assertEqual((), store.attempts(untouched.job_id))
        idle = dispatcher.dispatch_next()
        self.assertIsNone(idle.job)
        self.assertEqual(2, idle.skipped_unregistered)

    def test_dispatch_runs_one_job_per_call(self) -> None:
        dispatcher, store = self.dispatcher()
        jobs = [self.queued(dispatcher, request=f"request:{index}") for index in range(3)]
        done = []
        for _ in range(3):
            result = dispatcher.dispatch_next()
            self.assertIsNotNone(result.job)
            done.append(result.job.job_id)
        self.assertEqual(sorted(job.job_id for job in jobs), sorted(done))
        self.assertEqual(3, len(set(done)))
        self.assertIsNone(dispatcher.dispatch_next().job)

    def test_queued_job_ids_are_deterministic_and_bounded(self) -> None:
        store = self.store()
        ids = []
        for index in range(4):
            job = store.admit(admission(request=f"request:{index}"))
            store.transition(job.job_id, JobState.QUEUED)
            ids.append(job.job_id)
        self.assertEqual(tuple(sorted(ids)), store.queued_job_ids())
        self.assertEqual(tuple(sorted(ids))[:2], store.queued_job_ids(2))
        for bad in (0, -1, True, "5"):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    store.queued_job_ids(bad)

    # -- cancellation (ADR-0062 section 5) -----------------------------------

    def test_a_handler_acknowledging_a_recorded_request_ends_cancelled(self) -> None:
        observer = self.store()

        def execute(received):
            observer.transition(received.job_id, JobState.CANCELLATION_REQUESTED)
            return JobCancelled("cancelled_by_request", "evidence:partial:none")

        dispatcher, store = self.dispatcher(execute)
        job = self.queued(dispatcher)
        cancelled = dispatcher.dispatch_next().job
        self.assertEqual(JobState.CANCELLED, cancelled.state)
        self.assertEqual(("cancelled_by_request", "evidence:partial:none"),
                         (cancelled.reason_code, cancelled.diagnostic_reference))
        self.assertEqual(JobState.CANCELLED, store.attempts(job.job_id)[0].state)

    def test_success_after_a_cancellation_request_is_not_recorded_as_success(self) -> None:
        observer = self.store()

        def execute(received):
            observer.transition(received.job_id, JobState.CANCELLATION_REQUESTED)
            return JobResult(("result:sha256:1",))

        dispatcher, _ = self.dispatcher(execute)
        self.queued(dispatcher)
        outcome = dispatcher.dispatch_next().job
        self.assertEqual(JobState.RECOVERY_REQUIRED, outcome.state)
        self.assertEqual("completed_after_cancellation_request", outcome.reason_code)

    def test_a_cancellation_nobody_requested_is_not_accepted(self) -> None:
        dispatcher, _ = self.dispatcher(lambda _a: JobCancelled("cancelled_by_request"))
        self.queued(dispatcher)
        outcome = dispatcher.dispatch_next().job
        self.assertEqual(JobState.FAILED, outcome.state)
        self.assertEqual("cancellation_not_requested", outcome.reason_code)


if __name__ == "__main__":
    unittest.main()
