"""Opt-in ``j02-job-v1``: data-local J03 jobs over authenticated J02 (issue #340).

ADR-0069 section 4 over ADR-0062 (unchanged). Four operations, scoped per
operation by the dispatcher before any decoding (``submit``/``cancel`` need
``j02.jobs.submit``; ``status``/``result`` need ``j02.jobs.read``):

- ``submit`` admits only what the closed registry declares and queues it.
  The *server* supplies the handler's contract version, immutable
  implementation identity and effect-safety class from its own declaration; the
  wire never carries them, so a client cannot target a different
  implementation, and a kind that is not declared is ``invalid_request``.
- ``status`` returns only the bounded ADR-0062 section 5 fields.
- ``result`` returns the immutable result references of a ``SUCCEEDED`` Job.
- ``cancel`` follows ADR-0062 section 5: ``CANCELLED`` before dispatch,
  ``CANCELLATION_REQUESTED`` while running.

Not here, by design: lifecycle changes, recovery or retry over the wire
(``RECOVERY_REQUIRED`` stays an operator action, ADR-0069 section 2), any new
semantics, and result *bodies*. The Job store holds references only and no
body resolver or J14 route over J02 exists yet; see the issue's hand-off note.
Composition is explicit and opt-in (:func:`job_family_handlers`), like the J10
and J12 families: no host wires it implicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from .api_transport_server import J02FamilyHandler
from .durable_jobs import (
    DurableJobConflict, DurableJobNotFound, DurableJobStore, JobAdmission, JobRecord, JobState,
)
from .job_dispatch import JobDispatcher, JobHandlerNotRegistered, JobHandlerRegistry
from .market_data import ConsumerApiError, ConsumerErrorCode

J02_JOB_FAMILY = "j02-job-v1"
J02_JOB_REQUEST_VERSION = J02_JOB_FAMILY + "-request-v1"
J02_JOB_RESPONSE_VERSION = J02_JOB_FAMILY + "-response-v1"

# Operation -> remote scope. Mirrors ADR-0069 section 2; the dispatcher
# validates each route against its own declaration as well.
J02_JOB_SCOPES = {
    "submit": "j02.jobs.submit",
    "cancel": "j02.jobs.submit",
    "status": "j02.jobs.read",
    "result": "j02.jobs.read",
}

# Implementation bounds (not ADR values): identities are short tokens, and a
# parameter document that needs more than this is not an admission record.
MAX_TEXT_LENGTH = 512
MAX_INPUT_IDENTITIES = 256
MAX_PARAMETER_CHARACTERS = 64 * 1024  # all keys and string values together
MAX_PARAMETER_NODES = 4096
MAX_PARAMETER_DEPTH = 16

JobStoreProvider = Callable[[], AbstractContextManager[DurableJobStore]]

_INVALID = "the request is not valid"
_CANCELLED_BY_REQUEST = "cancelled_by_request"
_SUBMIT_KEYS = {
    "operation_kind", "request_identity", "canonical_parameters",
    "input_identities", "effect_safety_proof_reference",
}


def _refusal(reason: str, **context: Any) -> ConsumerApiError:
    """A fixed message plus a stable reason: no store text is ever forwarded."""
    return ConsumerApiError(
        ConsumerErrorCode.INVALID_REQUEST, _INVALID, context={"reason": reason, **context},
    )


def _text(value: Any) -> str:
    if type(value) is not str or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise ValueError("bounded non-empty text required")
    return value


def _only(body: Any, keys: set[str]) -> Mapping[str, Any]:
    if type(body) is not dict or set(body) != keys:
        raise ValueError("the request object fields do not match")
    return body


class _Submit:
    __slots__ = ("operation_kind", "request_identity", "parameters", "inputs", "proof")

    def __init__(self, body: Mapping[str, Any]):
        item = _only(body, _SUBMIT_KEYS)
        self.operation_kind = _text(item["operation_kind"])
        self.request_identity = _text(item["request_identity"])
        parameters = item["canonical_parameters"]
        if type(parameters) is not dict:
            raise ValueError("canonical_parameters must be an object")
        _bound_parameters(parameters)
        self.parameters = parameters
        inputs = item["input_identities"]
        if type(inputs) is not list or len(inputs) > MAX_INPUT_IDENTITIES:
            raise ValueError("input_identities must be a bounded array")
        self.inputs = tuple(_text(entry) for entry in inputs)
        proof = item["effect_safety_proof_reference"]
        self.proof = None if proof is None else _text(proof)


def _bound_parameters(document: Any) -> None:
    """Refuse an oversized or over-deep parameter document without serializing it.

    A walk with a node, character and depth budget: it bounds what is persisted
    in the Job store and adds no canonical-serialization site of its own (the
    admission record is serialized by the store, once).
    """

    nodes = characters = 0
    pending = [(document, 1)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_PARAMETER_NODES or depth > MAX_PARAMETER_DEPTH:
            raise ValueError("canonical_parameters exceed the bound")
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("canonical_parameters keys must be strings")
                characters += len(key)
                pending.append((item, depth + 1))
        elif type(value) is list:
            pending.extend((item, depth + 1) for item in value)
        elif type(value) is str:
            characters += len(value)
        elif value is not None and type(value) not in (bool, int):
            raise ValueError("canonical_parameters must be lossless JSON")
        if characters > MAX_PARAMETER_CHARACTERS:
            raise ValueError("canonical_parameters exceed the bound")


class _JobReference:
    __slots__ = ("job_id",)

    def __init__(self, body: Mapping[str, Any]):
        self.job_id = _text(_only(body, {"job_id"})["job_id"])


def status_document(record: JobRecord, attempts: Any) -> dict[str, Any]:
    """The bounded ADR-0062 section 5 projection of a Job. Nothing else."""

    admission = record.admission
    return {
        "job_id": record.job_id,
        "operation_kind": admission.operation_kind,
        "contract_version": admission.contract_version,
        "request_identity": admission.request_identity,
        "state": record.state.value,
        "reason_code": record.reason_code,
        "result_references": list(record.result_references),
        "diagnostic_reference": record.diagnostic_reference,
        "attempts": [
            {
                "attempt_no": attempt.attempt_no,
                "state": attempt.state.value,
                "failure_code": attempt.failure_code,
                "evidence_reference": attempt.evidence_reference,
            }
            for attempt in attempts
        ],
    }


class JobTransport:
    """Decode and execute the four operations against a store and a registry."""

    def __init__(self, store_provider: JobStoreProvider, registry: JobHandlerRegistry):
        if not callable(store_provider):
            raise TypeError("store_provider must be callable")
        if not isinstance(registry, JobHandlerRegistry):
            raise TypeError("registry must be a JobHandlerRegistry")
        self._provider = store_provider
        self._registry = registry

    # -- decode: pure, never touches the store ---------------------------------

    def decode(self, operation: str, body: Mapping[str, Any]) -> Any:
        try:
            if operation == "submit":
                return self._admission(_Submit(body))
            if operation in ("status", "result", "cancel"):
                return _JobReference(body)
        except ConsumerApiError:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as error:
            raise _refusal("invalid_request_shape") from error
        raise _refusal("unsupported_operation")  # pragma: no cover - routes are fixed

    def _admission(self, request: _Submit) -> JobAdmission:
        try:
            handler = self._registry.declaration(request.operation_kind)
        except JobHandlerNotRegistered as error:
            raise _refusal("unregistered_operation_kind") from error
        # Server-owned: the deck states what it wants run, never which
        # implementation or effect-safety class that is.
        return JobAdmission(
            operation_kind=handler.operation_kind,
            contract_version=handler.contract_version,
            implementation_identity=handler.implementation_identity,
            request_identity=request.request_identity,
            canonical_parameters=request.parameters,
            input_identities=request.inputs,
            effect_safety=handler.effect_safety,
            effect_safety_proof_reference=request.proof,
        )

    # -- execute ---------------------------------------------------------------

    def execute(self, operation: str, decoded: Any) -> dict[str, Any]:
        with self._provider() as store:
            if operation == "submit":
                return self._submit(store, decoded)
            if operation == "status":
                return self._status(store, decoded.job_id)
            if operation == "result":
                return self._result(store, decoded.job_id)
            return self._cancel(store, decoded.job_id)

    def _submit(self, store: DurableJobStore, admission: JobAdmission) -> dict[str, Any]:
        try:
            record = JobDispatcher(store, self._registry).admit(admission)
        except JobHandlerNotRegistered as error:
            raise _refusal("unregistered_operation_kind") from error
        except DurableJobConflict as error:
            # Same job_id, different immutable fields: never a success.
            raise ConsumerApiError(
                ConsumerErrorCode.INTEGRITY_FAILURE,
                "the platform cannot produce a trustworthy result",
                context={"reason": "job_admission_conflict"},
            ) from error
        if record.state is JobState.ADMITTED:
            try:
                record = store.transition(record.job_id, JobState.QUEUED)
            except DurableJobConflict:
                # Raced with a worker or a cancellation: report where it is.
                record = store.get(record.job_id)
        return status_document(record, store.attempts(record.job_id))

    def _status(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        record = self._get(store, job_id)
        return status_document(record, store.attempts(job_id))

    def _result(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        record = self._get(store, job_id)
        if record.state is not JobState.SUCCEEDED:
            raise _refusal("job_not_succeeded", state=record.state.value)
        return {
            "job_id": record.job_id,
            "state": record.state.value,
            "result_references": list(record.result_references),
        }

    def _cancel(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        # Decided from the state each transition itself validates: a race with a
        # worker's claim is refused by the store and decided again, and the only
        # concurrent changes are the forward ones, so this converges.
        for _ in range(3):
            record = self._get(store, job_id)
            state = record.state
            if state in (JobState.CANCELLED, JobState.CANCELLATION_REQUESTED):
                return status_document(record, store.attempts(job_id))
            if state in (JobState.ADMITTED, JobState.QUEUED):
                target, options = JobState.CANCELLED, {"reason_code": _CANCELLED_BY_REQUEST}
            elif state is JobState.RUNNING:
                target, options = JobState.CANCELLATION_REQUESTED, {}
            elif state is JobState.RECOVERY_REQUIRED:
                # An explicit recovery decision, which stays an operator action.
                raise _refusal("job_recovery_required")
            else:  # SUCCEEDED, FAILED
                raise _refusal("job_already_terminal", state=state.value)
            try:
                record = store.transition(job_id, target, **options)
            except DurableJobConflict:
                if store.get(job_id).state is state:
                    raise
                continue
            return status_document(record, store.attempts(job_id))
        return status_document(store.get(job_id), store.attempts(job_id))  # pragma: no cover

    @staticmethod
    def _get(store: DurableJobStore, job_id: str) -> JobRecord:
        try:
            return store.get(job_id)
        except DurableJobNotFound as error:
            raise _refusal("job_not_found") from error


def job_family_handlers(
    store_provider: JobStoreProvider, registry: JobHandlerRegistry,
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host and no other family is added.

    ``store_provider`` yields a store per request (its own connection, closed on
    exit), so concurrent requests never share a transaction. ``registry`` is the
    closed registry that the worker dispatches from.
    """

    transport = JobTransport(store_provider, registry)
    return {
        (J02_JOB_FAMILY, J02_JOB_REQUEST_VERSION, operation): J02FamilyHandler(
            scope, J02_JOB_RESPONSE_VERSION,
            lambda body, operation=operation: transport.decode(operation, body),
            lambda decoded, operation=operation: transport.execute(operation, decoded),
        )
        for operation, scope in J02_JOB_SCOPES.items()
    }


__all__ = [
    "J02_JOB_FAMILY",
    "J02_JOB_REQUEST_VERSION",
    "J02_JOB_RESPONSE_VERSION",
    "J02_JOB_SCOPES",
    "JobTransport",
    "job_family_handlers",
    "status_document",
]
