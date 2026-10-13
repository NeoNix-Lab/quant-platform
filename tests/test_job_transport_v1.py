"""``j02-job-v1``: data-local J03 jobs over authenticated J02 (#340, P05b).

``JobTransportV1Tests`` is backend-neutral: ``provider()`` yields a *new* store
on its own connection per request, as the family composition requires. SQLite
here; tests/integration_runtime_stores_postgres.py subclasses it for the
PostgreSQL runtime schema and its real roles.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import quant_platform.application.api_transport_server as server  # noqa: E402
from quant_platform.application.framed_result_transport import (  # noqa: E402
    J14_MESSAGE_FAMILY,
    J14_SCHEMA_VERSION,
    FramedResultConfig,
    FramedResultReceiver,
    FramedResultResumeRequest,
    FramedResultTransferStore,
)
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
                       request_id="req-1", handlers=None, framed_transfers=None,
                       framed_resume_scopes=None):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 32
        socket = Socket([json.dumps({
            "message_family": J02_JOB_FAMILY, "schema_version": J02_JOB_REQUEST_VERSION,
            "operation": operation, "request_id": request_id, "request": request,
        })])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=handlers or self.routes(registry),
            framed_transfers=framed_transfers, framed_resume_scopes=framed_resume_scopes,
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

    # -- result bodies as J14 transfers (ADR-0069 section 4, ADR-0066 sections 2-3) -----

    FRAME_BYTES = 1024  # small frames keep the multi-frame case cheap to test

    def transfers(self, **limits) -> FramedResultTransferStore:
        return FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=self.FRAME_BYTES, **limits)
        )

    def with_bodies(self, bodies, transfers=None, resolver=None):
        transfers = transfers if transfers is not None else self.transfers()
        resolver = resolver or (lambda reference: bodies.get(reference))
        registry = JobHandlerRegistry((declaration(),))
        return job_family_handlers(
            self.provider(), registry, result_bodies=resolver, transfers=transfers,
        ), transfers

    async def resume(self, transfers, transfer_id, index=0, *, scopes=ALL_SCOPES, payload=None,
                     resume_scopes=frozenset({READ})):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 32
        message = payload if payload is not None else {
            "message_family": J14_MESSAGE_FAMILY, "schema_version": J14_SCHEMA_VERSION,
            "message_type": "resume", "transfer_id": transfer_id,
            "next_verified_chunk_index": index,
        }
        socket = Socket([json.dumps(message)])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=self.routes(), framed_transfers=transfers,
            framed_resume_scopes=resume_scopes if transfers is not None else None,
        )
        return socket, configuration

    def resume_message(self, transfer_id, **fields):
        message = {
            "message_family": J14_MESSAGE_FAMILY, "schema_version": J14_SCHEMA_VERSION,
            "message_type": "resume", "transfer_id": transfer_id, "next_verified_chunk_index": 0,
        }
        message.update(fields)
        return message

    async def decoder_calls(self, transfers, payloads, *, scopes, resume_scopes=frozenset({READ})):
        """Run each payload through a connection; return (sockets, decoder call count)."""

        sockets = []
        with patch.object(
            FramedResultResumeRequest, "from_message",
            wraps=FramedResultResumeRequest.from_message,
        ) as decoder:
            for payload in payloads:
                socket, _ = await self.resume(
                    transfers, None, payload=payload, scopes=scopes, resume_scopes=resume_scopes,
                )
                sockets.append(socket)
        return sockets, decoder.call_count

    async def result_with(self, handlers, job_id, **options):
        return await self.call("result", {"job_id": job_id}, handlers=handlers, **options)

    LARGE = {"rows": [{"n": n, "text": "x" * 20} for n in range(400)]}

    async def test_a_body_that_fits_one_frame_is_delivered_inline(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-inline")
        body = {"rows": [1, 2, 3]}
        handlers, transfers = self.with_bodies({"result:sha256:9": body})
        result = self.ok(await self.result_with(handlers, job.job_id))
        self.assertEqual(["result:sha256:9"], result["result_references"])
        self.assertEqual(
            [{"reference": "result:sha256:9", "delivery": "inline", "body": body}], result["bodies"],
        )
        self.assertEqual(0, len(transfers._transfers))  # nothing retained for a small body

    async def test_a_body_without_a_resolution_is_none_and_no_resolver_adds_nothing(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-none")
        handlers, _ = self.with_bodies({})
        result = self.ok(await self.result_with(handlers, job.job_id))
        self.assertEqual([{"reference": "result:sha256:9", "delivery": "none"}], result["bodies"])
        plain = self.ok(await self.result_with(self.routes(), job.job_id))
        self.assertEqual({"job_id", "state", "result_references"}, set(plain))

    async def test_a_large_body_is_a_j14_transfer_that_reassembles_exactly(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-framed")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        result = self.ok(await self.result_with(handlers, job.job_id))
        (entry,) = result["bodies"]
        self.assertEqual("framed", entry["delivery"])
        self.assertEqual("result:sha256:9", entry["logical_result_identity"])  # the reference
        self.assertGreater(entry["chunk_count"], 1)
        self.assertNotIn("body", entry)

        socket, configuration = await self.resume(transfers, entry["transfer_id"], 0)
        self.assertEqual([], socket.close_calls)
        self.assertEqual(entry["chunk_count"], len(socket.responses))
        receiver = FramedResultReceiver()
        reassembled = None
        for frame in socket.responses:
            self.assertEqual(J14_MESSAGE_FAMILY, frame["message_family"])
            self.assertEqual(entry["transfer_id"], frame["transfer_id"])
            self.assertEqual(entry["payload_sha256"], frame["payload_sha256"])
            reassembled = receiver.receive(frame)
        self.assertEqual(self.LARGE, reassembled)
        self.assertEqual(
            READ, configuration.evidence_log.events[-1].requested_scope,
        )  # retrieval is a read, audited under the read scope

    async def test_resume_returns_exactly_the_remaining_frames_and_a_resubmitted_job_reuses_the_transfer(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-resume")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        first = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        again = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        self.assertEqual(first, again)  # same bytes, same identity: one retained transfer
        self.assertEqual(1, len(transfers._transfers))
        socket, _ = await self.resume(transfers, first["transfer_id"], 2)
        self.assertEqual([2 + n for n in range(first["chunk_count"] - 2)],
                         [frame["chunk_index"] for frame in socket.responses])

    async def test_resume_outcomes_are_typed_and_never_confirm_what_is_unknown(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-outcomes")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        entry = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        for label, transfer_id, index, outcome in (
            ("unknown transfer", "0" * 64, 0, "transfer_expired"),
            ("index past the end", entry["transfer_id"], entry["chunk_count"] + 1, "integrity_failure"),
        ):
            with self.subTest(label):
                socket, _ = await self.resume(transfers, transfer_id, index)
                self.assertEqual([], socket.close_calls)
                (message,) = socket.responses
                self.assertEqual(
                    (J14_MESSAGE_FAMILY, "outcome", outcome),
                    (message["message_family"], message["message_type"], message["outcome"]),
                )
        malformed = {"message_family": J14_MESSAGE_FAMILY, "schema_version": J14_SCHEMA_VERSION,
                     "message_type": "resume", "transfer_id": "not-a-digest",
                     "next_verified_chunk_index": -1}
        socket, _ = await self.resume(transfers, None, payload=malformed)
        self.assertEqual("integrity_failure", socket.responses[0]["outcome"])
        self.assertIsNone(socket.responses[0]["transfer_id"])  # never reflects arbitrary text

    async def test_retrieval_needs_the_read_scope_the_transfer_was_admitted_under(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-scope")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        entry = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        for scopes in (frozenset({SUBMIT}), frozenset({"j02.market_data.read"})):
            with self.subTest(scopes=sorted(scopes)):
                socket, configuration = await self.resume(
                    transfers, entry["transfer_id"], 0, scopes=scopes,
                )
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)  # no frame to a caller without the scope
                self.assertEqual(READ, configuration.evidence_log.events[-1].requested_scope)
        granted, _ = await self.resume(transfers, entry["transfer_id"], 0, scopes=frozenset({READ}))
        self.assertEqual(entry["chunk_count"], len(granted.responses))

    def malformed_resumes(self, transfer_id):
        """Every way a resume can be wrong, all naming a transfer that exists."""

        return {
            "negative index": self.resume_message(transfer_id, next_verified_chunk_index=-1),
            "string index": self.resume_message(transfer_id, next_verified_chunk_index="0"),
            "float index": self.resume_message(transfer_id, next_verified_chunk_index=1.5),
            "missing index": {
                key: value for key, value in self.resume_message(transfer_id).items()
                if key != "next_verified_chunk_index"
            },
            "wrong schema version": self.resume_message(transfer_id, schema_version="j14-v0"),
            "wrong message type": self.resume_message(transfer_id, message_type="frame"),
            "extra nonsense": self.resume_message(transfer_id, next_verified_chunk_index=[0]),
        }

    async def test_resume_is_authorized_before_it_is_decoded(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-predecode")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        entry = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        payloads = {
            "well formed": self.resume_message(entry["transfer_id"]),
            **self.malformed_resumes(entry["transfer_id"]),
            "unknown transfer": self.resume_message("0" * 64),
            "not a digest": self.resume_message("not-a-digest"),
            "no transfer id": {
                key: value for key, value in self.resume_message("x").items() if key != "transfer_id"
            },
        }
        # Without any scope a transfer can be admitted under, nothing is decoded
        # and nothing is answered: a malformed message gets no more than a valid one.
        sockets, decoded = await self.decoder_calls(
            transfers, list(payloads.values()), scopes=frozenset({SUBMIT}),
        )
        self.assertEqual(0, decoded)
        for label, socket in zip(payloads, sockets):
            with self.subTest(label):
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)

    async def test_resume_decoding_waits_for_the_scope_of_the_transfer_it_names(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-owner-first")
        handlers, transfers = self.with_bodies({"result:sha256:9": self.LARGE})
        entry = self.ok(await self.result_with(handlers, job.job_id))["bodies"][0]
        market = "j02.market_data.read"
        both = frozenset({READ, market})
        # This caller holds a resumable scope, just not the one this transfer needs.
        named = {
            "well formed": self.resume_message(entry["transfer_id"]),
            **self.malformed_resumes(entry["transfer_id"]),
        }
        sockets, decoded = await self.decoder_calls(
            transfers, list(named.values()), scopes=frozenset({market}), resume_scopes=both,
        )
        self.assertEqual(0, decoded)
        for label, socket in zip(named, sockets):
            with self.subTest(label):
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)
        # With a resumable scope and nothing named that exists, the decoder is
        # reached and answers with a typed outcome rather than a closed socket.
        unnamed = [self.resume_message("0" * 64), self.resume_message("not-a-digest")]
        sockets, decoded = await self.decoder_calls(
            transfers, unnamed, scopes=frozenset({market}), resume_scopes=both,
        )
        self.assertGreater(decoded, 0)
        for socket in sockets:
            self.assertEqual([], socket.close_calls)
            self.assertIn(socket.responses[0]["outcome"], {"transfer_expired", "integrity_failure"})
        # A denial for a set of scopes names no single operation scope.
        _, configuration = await self.resume(
            transfers, entry["transfer_id"], scopes=frozenset({SUBMIT}), resume_scopes=both,
        )
        self.assertEqual(
            server.J02_EVIDENCE_NO_OPERATION_SCOPE, configuration.evidence_log.events[-1].requested_scope,
        )
        self.assertEqual("deny", configuration.evidence_log.events[-1].decision)

    async def test_a_transfer_without_a_scope_is_never_served_over_the_wire(self) -> None:
        transfers = self.transfers()
        (frame, *_) = transfers.admit(logical_result_identity="result:sha256:9", result=self.LARGE)
        self.assertEqual((None,), transfers.retained_owner(frame.transfer_id))
        # An in-process holder of the store can still resume it; the wire cannot.
        for label, scopes in (
            ("submit only", frozenset({SUBMIT})),
            ("read", frozenset({READ})),
            ("every job scope", ALL_SCOPES),
        ):
            with self.subTest(label):
                socket, _ = await self.resume(transfers, frame.transfer_id, 0, scopes=scopes)
                frames = [r for r in socket.responses if r.get("message_type") == "frame"]
                self.assertEqual([], frames)
                if SUBMIT in scopes and READ not in scopes:
                    self.assertEqual([(1008, "policy denied")], socket.close_calls)
                else:
                    self.assertEqual([], socket.close_calls)
                    (message,) = socket.responses
                    self.assertEqual("transfer_expired", message["outcome"])
                    # Exactly what an unknown transfer says.
                    unknown, _ = await self.resume(transfers, "0" * 64, 0, scopes=scopes)
                    self.assertEqual(
                        {**unknown.responses[0], "transfer_id": frame.transfer_id}, message,
                    )

    async def test_a_transfer_admitted_for_a_scope_the_connection_does_not_resume_is_expired(self) -> None:
        transfers = self.transfers()
        other = "j02.admitted_input.read"
        (frame, *_) = transfers.admit(
            logical_result_identity="input:sha256:7", result=self.LARGE, owner=other,
        )
        # Holds the owner scope but the connection is not composed to resume it.
        socket, _ = await self.resume(
            transfers, frame.transfer_id, 0, scopes=frozenset({READ, other}),
        )
        self.assertEqual([], socket.close_calls)
        self.assertEqual("transfer_expired", socket.responses[0]["outcome"])
        # Composed for it, the same caller receives it.
        socket, _ = await self.resume(
            transfers, frame.transfer_id, 0, scopes=frozenset({other}),
            resume_scopes=frozenset({other}),
        )
        self.assertEqual("frame", socket.responses[0]["message_type"])

    async def test_framed_transfers_are_never_composed_without_authentication(self) -> None:
        transfers = self.transfers()
        unauthenticated = Socket([json.dumps(self.resume_message("0" * 64))])
        with self.assertRaises(ValueError):
            await server.handle_api_transport_connection(
                unauthenticated, execute=MagicMock(), remote_security=None,
                framed_transfers=transfers, framed_resume_scopes=frozenset({READ}),
            )
        self.assertEqual([], unauthenticated.responses)  # nothing was served
        with self.assertRaises(ValueError):
            await server.run_api_transport_server(
                server.ApiTransportServerConfig(), stop_event=asyncio.Event(),
                execute=MagicMock(), framed_transfers=transfers,
                framed_resume_scopes=frozenset({READ}),
            )
        configuration = security(principal=server.RemoteJ02Principal("deck", ALL_SCOPES))
        for label, scopes in (
            ("no scopes", None),
            ("empty scopes", frozenset()),
            ("a bare string", READ),
            ("not a J02 scope", frozenset({"admin"})),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                await server.handle_api_transport_connection(
                    Socket([]), execute=MagicMock(), remote_security=configuration,
                    framed_transfers=transfers, framed_resume_scopes=scopes,
                )
        with self.assertRaises(ValueError):  # scopes for nothing
            await server.handle_api_transport_connection(
                Socket([]), execute=MagicMock(), remote_security=configuration,
                framed_resume_scopes=frozenset({READ}),
            )
        with self.assertRaises(TypeError):
            await server.handle_api_transport_connection(
                Socket([]), execute=MagicMock(), remote_security=configuration,
                framed_transfers=object(), framed_resume_scopes=frozenset({READ}),
            )

    async def test_j14_is_unsupported_unless_the_connection_is_composed_with_a_store(self) -> None:
        socket, _ = await self.resume(None, "0" * 64, 0)
        self.assertEqual([], socket.close_calls)
        (response,) = socket.responses
        self.assertEqual("invalid_request", response["error"]["code"])
        self.assertNotIn("message_type", response)

    async def test_transfer_admission_is_bounded_and_refused_not_retained_without_limit(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-bound")
        small = self.transfers(max_retained_payload_bytes=1024, max_in_flight_transfers=1)
        handlers, _ = self.with_bodies({"result:sha256:9": self.LARGE}, transfers=small)
        response = await self.result_with(handlers, job.job_id)
        error = self.refused(response, "transfer_refused", code="result_too_large")
        self.assertEqual("the result would exceed the maximum J02 response size", error["message"])
        self.assertEqual(0, len(small._transfers))

    async def test_a_resolver_failure_or_a_non_canonical_body_is_an_integrity_failure(self) -> None:
        job, _ = self.seed(JobState.SUCCEEDED, request_identity="request:body-faults")

        def explode(_reference):
            raise RuntimeError("/srv/secret/path token=abc")

        for label, resolver, reason in (
            ("resolver raises", explode, "result_body_unavailable"),
            ("not canonical JSON", lambda _r: {"value": {1, 2}}, "result_body_not_canonical"),
        ):
            with self.subTest(label):
                handlers, _ = self.with_bodies({}, resolver=resolver)
                response = await self.result_with(handlers, job.job_id)
                error = self.refused(response, reason, code="integrity_failure")
                self.assertNotIn("secret", json.dumps(error))
                self.assertNotIn("token", json.dumps(error))

    def test_a_resolver_needs_a_transfer_store_and_must_be_callable(self) -> None:
        registry = JobHandlerRegistry((declaration(),))
        with self.assertRaises(TypeError):
            job_family_handlers(self.provider(), registry, result_bodies=lambda _r: None)
        with self.assertRaises(TypeError):
            job_family_handlers(
                self.provider(), registry, result_bodies="resolver", transfers=self.transfers(),
            )

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
