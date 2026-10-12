"""``j02-job-v1``: data-local J03 jobs over authenticated J02 (#340, P05b).

``JobTransportV1Tests`` is backend-neutral: ``provider()`` yields a *new* store
on its own connection per request, as the family composition requires. SQLite
here; tests/integration_runtime_stores_postgres.py subclasses it for the
PostgreSQL runtime schema and its real roles.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import quant_platform.application.api_transport_server as server  # noqa: E402
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
from quant_platform.application.job_transport import (  # noqa: E402
    J02_JOB_FAMILY,
    J02_JOB_REQUEST_VERSION,
    J02_JOB_RESPONSE_VERSION,
    J02_JOB_SCOPES,
    job_family_handlers,
)
from quant_platform.application.job_worker_runtime import JobWorker  # noqa: E402
from test_api_transport_families_v1 import Socket  # noqa: E402
from test_remote_j02_security_v1 import security  # noqa: E402

KIND = "catalog-maintenance-v1"
IDENTITY = "implementation:sha256:aaa"
SUBMIT, READ = "j02.jobs.submit", "j02.jobs.read"
ALL_SCOPES = frozenset({SUBMIT, READ})
STATUS_FIELDS = {
    "job_id", "operation_kind", "contract_version", "request_identity", "state",
    "reason_code", "result_references", "diagnostic_reference", "attempts",
}
ATTEMPT_FIELDS = {"attempt_no", "state", "failure_code", "evidence_reference"}
SUBMIT_RECEIPT = {"job_id", "operation_kind", "request_identity"}
CANCEL_RECEIPT = {"job_id", "cancellation"}
# Everything a completed Job holds that needs the read scope to be observed.
PROTECTED_MARKERS = (
    "result:sha256", "evidence:", "handler_failed", "diagnostic", "attempts", "attempt_no",
    "failure_code", "evidence_reference", "result_references", "reason_code",
    "SUCCEEDED", "FAILED", "RUNNING", "QUEUED", "CANCELLED", "RECOVERY_REQUIRED",
)


def succeeds(_admission):
    return JobResult(("result:sha256:1", "result:sha256:2"))


def declaration(execute=succeeds, kind=KIND, safety=EffectSafety.PURE_REPLAYABLE) -> JobHandler:
    return JobHandler(kind, "v1", IDENTITY, safety, execute)


def submit_body(**overrides):
    body = {
        "operation_kind": KIND,
        "request_identity": "request:1",
        "canonical_parameters": {"partition": "2024-01-01"},
        "input_identities": ["dataset:sha256:aaa"],
        "effect_safety_proof_reference": None,
    }
    body.update(overrides)
    return body


def expected_admission(**overrides) -> JobAdmission:
    body = submit_body(**overrides)
    return JobAdmission(
        operation_kind=body["operation_kind"], contract_version="v1",
        implementation_identity=IDENTITY, request_identity=body["request_identity"],
        canonical_parameters=body["canonical_parameters"],
        input_identities=tuple(body["input_identities"]),
        effect_safety=EffectSafety.PURE_REPLAYABLE,
        effect_safety_proof_reference=body["effect_safety_proof_reference"],
    )


class JobTransportV1Tests(unittest.IsolatedAsyncioTestCase):
    # -- backend hooks -----------------------------------------------------------

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "jobs.sqlite"

    def open_store(self) -> DurableJobStore:
        connection = sqlite3.connect(self._path)
        self.addCleanup(connection.close)
        return DurableJobStore(connection)

    def provider(self):
        @contextmanager
        def session():
            connection = sqlite3.connect(self._path)
            try:
                yield DurableJobStore(connection)
            finally:
                connection.close()
        return session

    def corrupt_payload(self, job_id: str) -> None:
        connection = sqlite3.connect(self._path)
        connection.execute("UPDATE durable_jobs SET payload = '{}' WHERE job_id = ?", (job_id,))
        connection.commit()
        connection.close()

    def make_worker(self, registry, stop) -> JobWorker:
        """The real worker lifecycle; the lock and commit record are fakes here."""

        class Held:
            def acquire(self):
                return True

            def held(self):
                return True

            def release(self):
                pass

        class Recorded:
            def recorded(self):
                return "c" * 40

        return JobWorker(
            store=self.open_store(), registry=registry, lock=Held(), commit_record=Recorded(),
            commit="c" * 40, stop=stop, poll_interval=0.01,
        )

    # -- wire helpers ------------------------------------------------------------

    def routes(self, registry=None):
        # An empty registry is falsy (it has a length), so test identity, not truth.
        registry = JobHandlerRegistry((declaration(),)) if registry is None else registry
        return job_family_handlers(self.provider(), registry)

    async def exchange(self, operation, request, *, scopes=ALL_SCOPES, registry=None,
                       request_id="req-1", handlers=None):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 32
        socket = Socket([json.dumps({
            "message_family": J02_JOB_FAMILY, "schema_version": J02_JOB_REQUEST_VERSION,
            "operation": operation, "request_id": request_id, "request": request,
        })])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=handlers or self.routes(registry),
        )
        return socket, configuration

    async def call(self, operation, request, **options):
        socket, _ = await self.exchange(operation, request, **options)
        self.assertEqual([], socket.close_calls)
        return socket.responses[0]

    def ok(self, response):
        self.assertEqual("ok", response["status"], response)
        self.assertEqual(J02_JOB_FAMILY, response["message_family"])
        self.assertEqual(J02_JOB_RESPONSE_VERSION, response["schema_version"])
        return response["result"]

    def refused(self, response, reason, code="invalid_request"):
        self.assertEqual("error", response["status"], response)
        self.assertEqual(code, response["error"]["code"])
        self.assertEqual(reason, response["error"]["context"]["reason"])
        return response["error"]

    def seed(self, state=JobState.QUEUED, **overrides):
        """A registered Job placed directly in a state, bypassing the wire."""
        store = self.open_store()
        job = store.admit(expected_admission(**overrides))
        path = {
            JobState.ADMITTED: (),
            JobState.QUEUED: (JobState.QUEUED,),
            JobState.RUNNING: (JobState.QUEUED, JobState.RUNNING),
            JobState.CANCELLATION_REQUESTED: (
                JobState.QUEUED, JobState.RUNNING, JobState.CANCELLATION_REQUESTED),
            JobState.SUCCEEDED: (JobState.QUEUED, JobState.RUNNING),
            JobState.FAILED: (JobState.QUEUED, JobState.RUNNING),
            JobState.RECOVERY_REQUIRED: (JobState.QUEUED, JobState.RUNNING),
        }[state]
        for step in path:
            store.transition(job.job_id, step)
        if state is JobState.SUCCEEDED:
            store.transition(job.job_id, JobState.SUCCEEDED, result_references=("result:sha256:9",))
        elif state is JobState.FAILED:
            store.transition(job.job_id, JobState.FAILED, reason_code="handler_failed")
        elif state is JobState.RECOVERY_REQUIRED:
            store.recover_after_restart()
        return job, store

    # -- submit (AC1, AC2) ---------------------------------------------------------

    async def test_submit_of_a_registered_kind_returns_the_deterministic_queued_job(self) -> None:
        result = self.ok(await self.call("submit", submit_body()))
        self.assertEqual(expected_admission().job_id, result["job_id"])
        # A receipt: what the caller submitted, never an observation of the Job.
        self.assertEqual(SUBMIT_RECEIPT, set(result))
        self.assertEqual((KIND, "request:1"), (result["operation_kind"], result["request_identity"]))
        store = self.open_store()
        self.assertEqual((result["job_id"],), store.queued_job_ids())
        self.assertEqual(JobState.QUEUED, store.get(result["job_id"]).state)
        # The state is available, with the read scope, from status.
        status = self.ok(await self.call("status", {"job_id": result["job_id"]}))
        self.assertEqual("QUEUED", status["state"])

    async def test_an_identical_resubmission_returns_the_same_job_and_adds_nothing(self) -> None:
        first = self.ok(await self.call("submit", submit_body()))
        second = self.ok(await self.call("submit", submit_body(), request_id="req-2"))
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.open_store().queued_job_ids()))
        # Another request identity is another Job, not a conflict.
        other = self.ok(await self.call("submit", submit_body(request_identity="request:2")))
        self.assertNotEqual(first["job_id"], other["job_id"])

    async def test_a_resubmission_after_the_job_moved_gives_the_same_receipt(self) -> None:
        job = self.ok(await self.call("submit", submit_body()))
        store = self.open_store()
        store.claim(job["job_id"])
        again = self.ok(await self.call("submit", submit_body(), request_id="req-2"))
        self.assertEqual(job, again)  # nothing about the move leaks to a submitter
        self.assertEqual(SUBMIT_RECEIPT, set(again))
        status = self.ok(await self.call("status", {"job_id": job["job_id"]}))
        self.assertEqual(("RUNNING", [1]), (status["state"], [a["attempt_no"] for a in status["attempts"]]))

    async def test_a_conflicting_stored_admission_fails_closed(self) -> None:
        job = self.ok(await self.call("submit", submit_body()))
        self.corrupt_payload(job["job_id"])  # different immutable fields under this job_id
        response = await self.call("submit", submit_body(), request_id="req-2")
        self.assertEqual("error", response["status"])
        self.assertEqual("integrity_failure", response["error"]["code"])
        self.assertEqual("job_admission_conflict", response["error"]["context"]["reason"])

    async def test_an_unregistered_kind_is_invalid_request_and_nothing_is_persisted(self) -> None:
        for label, kind, registry in (
            ("undeclared", "catalog-compaction-v1", None),
            ("empty shipped registry", KIND, DATA_LOCAL_JOB_REGISTRY),
        ):
            with self.subTest(label):
                response = await self.call(
                    "submit", submit_body(operation_kind=kind), registry=registry,
                )
                self.refused(response, "unregistered_operation_kind")
        self.assertEqual((), self.open_store().queued_job_ids())

    async def test_the_deck_cannot_name_an_implementation_or_effect_safety(self) -> None:
        for extra in (
            {"implementation_identity": "implementation:sha256:evil"},
            {"contract_version": "v9"},
            {"effect_safety": "NO_EFFECT"},
            {"callable": "os.system"},
            {"module": "package.module"},
        ):
            with self.subTest(extra=sorted(extra)):
                response = await self.call("submit", {**submit_body(), **extra})
                self.refused(response, "invalid_request_shape")
        self.assertEqual((), self.open_store().queued_job_ids())

    async def test_malformed_submissions_are_invalid_request(self) -> None:
        cases = {
            "missing field": {k: v for k, v in submit_body().items() if k != "input_identities"},
            "non-object parameters": submit_body(canonical_parameters=["x"]),
            "blank request identity": submit_body(request_identity=" "),
            "oversized identity": submit_body(request_identity="r" * 513),
            "non-string input": submit_body(input_identities=["ok", 5]),
            "duplicate inputs": submit_body(input_identities=["a", "a"]),
            "too many inputs": submit_body(input_identities=[f"i{n}" for n in range(257)]),
            "oversized parameters": submit_body(canonical_parameters={"k": "v" * 70_000}),
            "too many parameter nodes": submit_body(canonical_parameters={"k": list(range(5000))}),
            "over-deep parameters": submit_body(
                canonical_parameters={"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {
                    "j": {"k": {"l": {"m": {"n": {"o": {"p": {"q": 1}}}}}}}}}}}}}}}}}),
            "unneeded safety proof": submit_body(effect_safety_proof_reference="proof:1"),
            "non-string kind": submit_body(operation_kind=7),
        }
        for label, body in cases.items():
            with self.subTest(label):
                response = await self.call("submit", body)
                self.assertEqual("invalid_request", response["error"]["code"])
        self.assertEqual((), self.open_store().queued_job_ids())

    async def test_a_float_parameter_is_refused_before_decoding(self) -> None:
        configuration = security(principal=server.RemoteJ02Principal("deck", ALL_SCOPES))
        raw = (
            '{"message_family":"%s","schema_version":"%s","operation":"submit","request_id":"r",'
            '"request":{"operation_kind":"%s","request_identity":"request:1",'
            '"canonical_parameters":{"threshold":0.1},"input_identities":[],'
            '"effect_safety_proof_reference":null}}' % (J02_JOB_FAMILY, J02_JOB_REQUEST_VERSION, KIND)
        )
        socket = Socket([raw])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=self.routes(),
        )
        self.assertEqual("invalid_request", socket.responses[0]["error"]["code"])
        self.assertEqual((), self.open_store().queued_job_ids())

    # -- status (AC3) ----------------------------------------------------------------

    async def test_status_exposes_only_the_bounded_adr_0062_fields(self) -> None:
        job, store = self.seed(JobState.FAILED)
        status = self.ok(await self.call("status", {"job_id": job.job_id}))
        self.assertEqual(STATUS_FIELDS, set(status))
        self.assertEqual("FAILED", status["state"])
        self.assertEqual("handler_failed", status["reason_code"])
        (attempt,) = status["attempts"]
        self.assertEqual(ATTEMPT_FIELDS, set(attempt))
        self.assertEqual((1, "FAILED", "handler_failed"),
                         (attempt["attempt_no"], attempt["state"], attempt["failure_code"]))
        # No admission parameters, input identities, paths or logs leave the server.
        wire = json.dumps(status)
        for forbidden in ("partition", "dataset:sha256", "implementation:sha256", "canonical_parameters",
                          "input_identities", "stderr", "traceback", "/"):
            self.assertNotIn(forbidden, wire)

    async def test_status_of_an_unknown_job_is_invalid_request_without_store_text(self) -> None:
        response = await self.call("status", {"job_id": "job-v1:sha256:" + "0" * 64})
        error = self.refused(response, "job_not_found")
        self.assertEqual("the request is not valid", error["message"])
        for bad in ({"job_id": 5}, {"job_id": ""}, {}, {"job_id": "x", "extra": 1}):
            with self.subTest(bad=bad):
                self.assertEqual(
                    "invalid_request", (await self.call("status", bad))["error"]["code"],
                )

    # -- result ----------------------------------------------------------------------

    async def test_result_returns_the_references_of_a_succeeded_job_only(self) -> None:
        done, _ = self.seed(JobState.SUCCEEDED)
        result = self.ok(await self.call("result", {"job_id": done.job_id}))
        self.assertEqual({"job_id", "state", "result_references"}, set(result))
        self.assertEqual(["result:sha256:9"], result["result_references"])
        for state in (JobState.QUEUED, JobState.RUNNING, JobState.FAILED):
            with self.subTest(state=state.value):
                job, _ = self.seed(state, request_identity=f"request:{state.value}")
                error = self.refused(
                    await self.call("result", {"job_id": job.job_id}), "job_not_succeeded",
                )
                self.assertEqual(state.value, error["context"]["state"])

    # -- cancel (AC4) ----------------------------------------------------------------

    async def test_cancel_before_dispatch_is_cancelled(self) -> None:
        for state in (JobState.ADMITTED, JobState.QUEUED):
            with self.subTest(state=state.value):
                job, store = self.seed(state, request_identity=f"request:{state.value}")
                cancelled = self.ok(await self.call("cancel", {"job_id": job.job_id}))
                self.assertEqual(CANCEL_RECEIPT, set(cancelled))
                self.assertEqual("recorded", cancelled["cancellation"])
                record = store.get(job.job_id)
                self.assertEqual((JobState.CANCELLED, "cancelled_by_request"),
                                 (record.state, record.reason_code))
                self.assertNotIn(job.job_id, store.queued_job_ids())
                # Idempotent: a repeat reports the same outcome.
                self.assertEqual(
                    cancelled, self.ok(await self.call("cancel", {"job_id": job.job_id})),
                )

    async def test_cancel_while_running_only_records_a_request(self) -> None:
        job, store = self.seed(JobState.RUNNING)
        requested = self.ok(await self.call("cancel", {"job_id": job.job_id}))
        self.assertEqual(CANCEL_RECEIPT, set(requested))
        self.assertEqual(JobState.CANCELLATION_REQUESTED, store.get(job.job_id).state)
        # The same receipt whether it only requested (running) or finished
        # (queued): which one it was is an observation, and needs status.
        self.assertEqual(
            [(1, "CANCELLATION_REQUESTED")],
            [(a.attempt_no, a.state.value) for a in store.attempts(job.job_id)],
        )
        self.assertEqual(requested, self.ok(await self.call("cancel", {"job_id": job.job_id})))
        self.assertEqual(1, len(store.attempts(job.job_id)))

    async def test_cancel_refuses_terminal_jobs_and_never_decides_recovery(self) -> None:
        for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.RECOVERY_REQUIRED):
            with self.subTest(state=state.value):
                job, store = self.seed(state, request_identity=f"request:cancel:{state.value}")
                error = self.refused(
                    await self.call("cancel", {"job_id": job.job_id}), "job_not_cancellable",
                )
                # One reason for all three, and no state: telling them apart is a read.
                self.assertEqual({"reason": "job_not_cancellable"}, error["context"])
                self.assertEqual(state, store.get(job.job_id).state)

    # -- a submit-only caller must learn nothing it could only read ------------------

    SUBMIT_ONLY = frozenset({SUBMIT})

    def assert_no_observation(self, response):
        wire = json.dumps(response["result"] if response["status"] == "ok" else response["error"])
        for marker in PROTECTED_MARKERS:
            self.assertNotIn(marker, wire, f"{marker!r} leaked to a submit-only caller")

    async def test_resubmitting_a_finished_job_discloses_nothing_with_only_the_submit_scope(self) -> None:
        for index, state in enumerate(
            (JobState.SUCCEEDED, JobState.FAILED, JobState.RECOVERY_REQUIRED)
        ):
            with self.subTest(state=state.value):
                # Neutral identity: the receipt echoes it, so it must not spell a state.
                request = f"request:disclosure:{index}"
                job, store = self.seed(state, request_identity=request)
                response = await self.call(
                    "submit", submit_body(request_identity=request), scopes=self.SUBMIT_ONLY,
                )
                result = self.ok(response)
                self.assertEqual(SUBMIT_RECEIPT, set(result))
                self.assertEqual(job.job_id, result["job_id"])
                self.assert_no_observation(response)
                # ...and the submission did not change the finished Job.
                self.assertEqual(state, store.get(job.job_id).state)

    async def test_cancel_discloses_nothing_with_only_the_submit_scope(self) -> None:
        for state in (JobState.QUEUED, JobState.RUNNING, JobState.CANCELLATION_REQUESTED):
            with self.subTest(state=state.value):
                job, _ = self.seed(state, request_identity=f"request:cancel-leak:{state.value}")
                response = await self.call(
                    "cancel", {"job_id": job.job_id}, scopes=self.SUBMIT_ONLY,
                )
                self.assertEqual(CANCEL_RECEIPT, set(self.ok(response)))
                self.assert_no_observation(response)
        for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.RECOVERY_REQUIRED):
            with self.subTest(refused=state.value):
                job, _ = self.seed(state, request_identity=f"request:cancel-refused:{state.value}")
                response = await self.call(
                    "cancel", {"job_id": job.job_id}, scopes=self.SUBMIT_ONLY,
                )
                self.refused(response, "job_not_cancellable")
                self.assert_no_observation(response)

    async def test_the_same_submit_only_caller_still_cannot_read(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:still-denied")
        for operation in ("status", "result"):
            with self.subTest(operation=operation):
                socket, _ = await self.exchange(
                    operation, {"job_id": job.job_id}, scopes=self.SUBMIT_ONLY,
                )
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)

    async def test_a_read_only_caller_cannot_submit_or_cancel_and_still_observes(self) -> None:
        job, store = self.seed(JobState.SUCCEEDED, request_identity="request:read-only")
        read_only = frozenset({READ})
        for operation, body in (
            ("submit", submit_body(request_identity="request:read-only")),
            ("cancel", {"job_id": job.job_id}),
        ):
            with self.subTest(operation=operation):
                socket, _ = await self.exchange(operation, body, scopes=read_only)
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
        status = self.ok(await self.call("status", {"job_id": job.job_id}, scopes=read_only))
        self.assertEqual("SUCCEEDED", status["state"])
        result = self.ok(await self.call("result", {"job_id": job.job_id}, scopes=read_only))
        self.assertEqual(["result:sha256:9"], result["result_references"])

    # -- scopes (ADR-0069 section 2) -------------------------------------------------

    async def test_every_operation_is_denied_before_decoding_without_its_own_scope(self) -> None:
        job, _ = self.seed(JobState.QUEUED)
        bodies = {
            "submit": submit_body(), "cancel": {"job_id": job.job_id},
            "status": {"job_id": job.job_id}, "result": {"job_id": job.job_id},
        }
        self.assertEqual({"submit": SUBMIT, "cancel": SUBMIT, "status": READ, "result": READ},
                         J02_JOB_SCOPES)
        for operation, needed in J02_JOB_SCOPES.items():
            # Holding every other J02 scope, including the sibling job scope, is
            # not enough: scopes never imply one another.
            for scopes in (ALL_SCOPES - {needed}, frozenset({"j02.market_data.read"})):
                with self.subTest(operation=operation, scopes=sorted(scopes)):
                    spy = MagicMock(side_effect=AssertionError("decoded without the scope"))
                    handlers = self.routes()
                    key = (J02_JOB_FAMILY, J02_JOB_REQUEST_VERSION, operation)
                    route = handlers[key]
                    handlers[key] = server.J02FamilyHandler(
                        route.required_scope, route.response_schema_version, spy, route.execute,
                    )
                    socket, configuration = await self.exchange(
                        operation, bodies[operation], scopes=scopes, handlers=handlers,
                    )
                    self.assertEqual([(1008, "policy denied")], socket.close_calls)
                    self.assertEqual([], socket.responses)
                    spy.assert_not_called()
                    self.assertEqual(needed, configuration.evidence_log.events[-1].requested_scope)
        self.assertEqual(JobState.QUEUED, self.open_store().get(job.job_id).state)

    async def test_the_routes_are_exactly_the_four_declared_operations(self) -> None:
        routes = self.routes()
        self.assertEqual(
            {(J02_JOB_FAMILY, J02_JOB_REQUEST_VERSION, op) for op in J02_JOB_SCOPES}, set(routes),
        )
        for (_, _, operation), route in routes.items():
            self.assertEqual(J02_JOB_SCOPES[operation], route.required_scope)
            self.assertIsNone(route.additional_scopes)

    # -- end to end with the worker (AC5) ---------------------------------------------

    async def test_submit_then_worker_executes_then_result_returns_its_references(self) -> None:
        calls = []
        stop = threading.Event()

        def execute(admission):
            calls.append(admission.request_identity)
            stop.set()  # one pass of the real worker loop
            return JobResult(("result:sha256:aaa", "result:sha256:bbb"), "evidence:run:1")

        registry = JobHandlerRegistry((declaration(execute),))
        submitted = self.ok(await self.call("submit", submit_body(), registry=registry))
        # Nothing has run yet: result is refused until SUCCEEDED.
        pending = await self.call("result", {"job_id": submitted["job_id"]}, registry=registry)
        self.refused(pending, "job_not_succeeded", code="invalid_request")
        self.assertEqual([], calls)

        worker = self.make_worker(registry, stop)
        # A worker that never sees the Job must fail this test, not hang it.
        watchdog = threading.Timer(20, stop.set)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        self.assertEqual(1, worker.serve(), "the worker did not dispatch the submitted job")

        status = self.ok(await self.call("status", {"job_id": submitted["job_id"]}, registry=registry))
        self.assertEqual("SUCCEEDED", status["state"])
        self.assertEqual("evidence:run:1", status["diagnostic_reference"])
        self.assertEqual([(1, "SUCCEEDED")], [(a["attempt_no"], a["state"]) for a in status["attempts"]])
        result = self.ok(await self.call("result", {"job_id": submitted["job_id"]}, registry=registry))
        self.assertEqual(["result:sha256:aaa", "result:sha256:bbb"], result["result_references"])
        self.assertEqual(["request:1"], calls)  # ran once, in the worker, never in the API
        # A cancel after the fact is a refusal, not a rewrite of the outcome.
        self.refused(
            await self.call("cancel", {"job_id": submitted["job_id"]}, registry=registry),
            "job_not_cancellable",
        )


if __name__ == "__main__":
    unittest.main()
