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

Scopes never imply one another (ADR-0069 section 2), so what ``submit`` and
``cancel`` return is a *receipt*: the Job's identity, which the caller already
supplied or can compute, and nothing observed about it. State, attempts, reason
and result or diagnostic references need ``j02.jobs.read`` and are returned only
by ``status`` and ``result``; otherwise a resubmission or a cancel would be a
read of a completed Job without the read scope. Refusals carry a reason and no
state for the same reason.

Result bodies (ADR-0069 section 4, ADR-0066 sections 2 and 3). The Job store
holds references only, so a body comes from a composed *resolver*: a callable
that maps a result reference to its complete JSON body, or ``None``. With one,
``result`` adds a ``bodies`` entry per reference: ``inline`` when the canonical
body fits in one J14 frame, otherwise ``framed`` - a descriptor of a retained
J14 transfer whose logical result identity is the reference. The frames are
then fetched with a J14 ``resume`` message (``next_verified_chunk_index`` 0 for
all of them), which the connection handler answers only for a caller holding
``j02.jobs.read``, the scope the transfer was admitted under. Without a
resolver, ``result`` is exactly the references, as before.

Not here, by design: lifecycle changes, recovery or retry over the wire
(``RECOVERY_REQUIRED`` stays an operator action, ADR-0069 section 2), and any
new Job semantics. Composition is explicit and opt-in
(:func:`job_family_handlers`), like the J10 and J12 families: no host wires it
implicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from .api_transport_server import J02FamilyHandler
from .durable_jobs import (
    DurableJobConflict, DurableJobNotFound, DurableJobStore, JobAdmission, JobRecord, JobState,
)
from .framed_result_transport import (
    FramedResultTransferStore, FramedResultTransportError, frame_complete_result,
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
# reference -> the complete JSON body that reference names, or None for "no body".
ResultBodyResolver = Callable[[str], Any]

_INVALID = "the request is not valid"
_CANCELLED_BY_REQUEST = "cancelled_by_request"
_SUBMIT_KEYS = {
    "operation_kind", "request_identity", "canonical_parameters",
    "input_identities", "effect_safety_proof_reference",
}


def _integrity(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INTEGRITY_FAILURE,
        "the platform cannot produce a trustworthy result",
        context={"reason": reason},
    )


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


def _submit_receipt(record: JobRecord) -> dict[str, Any]:
    """What a submit-only caller may learn: the identity it submitted."""

    admission = record.admission
    return {
        "job_id": record.job_id,
        "operation_kind": admission.operation_kind,
        "request_identity": admission.request_identity,
    }


def _cancel_receipt(record: JobRecord) -> dict[str, Any]:
    """The cancellation is recorded; whether it already took effect is a read."""

    return {"job_id": record.job_id, "cancellation": "recorded"}


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

    def __init__(
        self,
        store_provider: JobStoreProvider,
        registry: JobHandlerRegistry,
        *,
        result_bodies: ResultBodyResolver | None = None,
        transfers: FramedResultTransferStore | None = None,
    ):
        if not callable(store_provider):
            raise TypeError("store_provider must be callable")
        if not isinstance(registry, JobHandlerRegistry):
            raise TypeError("registry must be a JobHandlerRegistry")
        if result_bodies is not None:
            if not callable(result_bodies):
                raise TypeError("result_bodies must be callable")
            if not isinstance(transfers, FramedResultTransferStore):
                # A body over one frame has nowhere to be retained without it.
                raise TypeError("result_bodies requires a FramedResultTransferStore")
        self._provider = store_provider
        self._registry = registry
        self._bodies = result_bodies
        self._transfers = transfers

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
            raise _integrity("job_admission_conflict") from error
        if record.state is JobState.ADMITTED:
            try:
                store.transition(record.job_id, JobState.QUEUED)
            except DurableJobConflict:
                # Raced with a worker or a cancellation: it has moved on, which
                # is not an error and is not reported to a submit-only caller.
                pass
        return _submit_receipt(record)

    def _status(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        record = self._get(store, job_id)
        return status_document(record, store.attempts(job_id))

    def _result(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        record = self._get(store, job_id)
        if record.state is not JobState.SUCCEEDED:
            raise _refusal("job_not_succeeded", state=record.state.value)
        document: dict[str, Any] = {
            "job_id": record.job_id,
            "state": record.state.value,
            "result_references": list(record.result_references),
        }
        if self._bodies is not None:
            document["bodies"] = self._deliveries(record.result_references)
        return document

    def _deliveries(self, references: tuple[str, ...]) -> list[dict[str, Any]]:
        """The delivery of every reference, published as one unit.

        Every body is resolved and checked first; the framed ones are then
        admitted as a single batch, so a refusal (or a resolver that fails on a
        later reference) publishes nothing of this call, and never withdraws a
        transfer that was already retained.
        """

        deliveries: list[dict[str, Any] | None] = []
        framed: list[tuple[int, str, Any]] = []  # (position, reference, body)
        for reference in references:
            try:
                body = self._bodies(reference)
            except Exception as error:  # A resolver's own message is never forwarded.
                raise _integrity("result_body_unavailable") from error
            if body is None:
                deliveries.append({"reference": reference, "delivery": "none"})
                continue
            try:
                frames = frame_complete_result(
                    logical_result_identity=reference, result=body, config=self._transfers.config,
                )
            except (TypeError, ValueError, ArithmeticError, RecursionError) as error:
                raise _integrity("result_body_not_canonical") from error
            if len(frames) == 1:
                # One frame is one message: nothing to frame, nothing to retain.
                deliveries.append({"reference": reference, "delivery": "inline", "body": body})
            else:
                framed.append((len(deliveries), reference, body))
                deliveries.append(None)
        if framed:
            try:
                admitted = self._transfers.admit_many(
                    [(reference, body) for _, reference, body in framed],
                    owner=J02_JOB_SCOPES["result"],
                )
            except FramedResultTransportError as error:
                # ADR-0066 section 3: refuse admission rather than retain without bound.
                raise ConsumerApiError(
                    ConsumerErrorCode.RESULT_TOO_LARGE,
                    "the result would exceed the maximum J02 response size",
                    context={"reason": "transfer_refused"},
                ) from error
            for (position, reference, _), frames in zip(framed, admitted):
                first = frames[0]
                deliveries[position] = {
                    "reference": reference,
                    "delivery": "framed",
                    "logical_result_identity": first.logical_result_identity,
                    "transfer_id": first.transfer_id,
                    "payload_sha256": first.payload_sha256,
                    "chunk_count": first.chunk_count,
                }
        return deliveries  # type: ignore[return-value]

    def _cancel(self, store: DurableJobStore, job_id: str) -> dict[str, Any]:
        # Decided from the state each transition itself validates: a race with a
        # worker's claim is refused by the store and decided again, and the only
        # concurrent changes are the forward ones, so this converges.
        for _ in range(3):
            record = self._get(store, job_id)
            state = record.state
            if state in (JobState.CANCELLED, JobState.CANCELLATION_REQUESTED):
                return _cancel_receipt(record)
            if state in (JobState.ADMITTED, JobState.QUEUED):
                target, options = JobState.CANCELLED, {"reason_code": _CANCELLED_BY_REQUEST}
            elif state is JobState.RUNNING:
                target, options = JobState.CANCELLATION_REQUESTED, {}
            else:
                # SUCCEEDED and FAILED are finished; RECOVERY_REQUIRED is an
                # explicit recovery decision that stays an operator action. One
                # reason for all three: which of them it is would be a read.
                raise _refusal("job_not_cancellable")
            try:
                record = store.transition(job_id, target, **options)
            except DurableJobConflict:
                if store.get(job_id).state is state:
                    raise
                continue
            return _cancel_receipt(record)
        return _cancel_receipt(store.get(job_id))  # pragma: no cover

    @staticmethod
    def _get(store: DurableJobStore, job_id: str) -> JobRecord:
        try:
            return store.get(job_id)
        except DurableJobNotFound as error:
            raise _refusal("job_not_found") from error


def job_family_handlers(
    store_provider: JobStoreProvider,
    registry: JobHandlerRegistry,
    *,
    result_bodies: ResultBodyResolver | None = None,
    transfers: FramedResultTransferStore | None = None,
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host and no other family is added.

    ``store_provider`` yields a store per request (its own connection, closed on
    exit), so concurrent requests never share a transaction. ``registry`` is the
    closed registry that the worker dispatches from. ``result_bodies`` and
    ``transfers`` are optional and go together; the *same* ``transfers`` store
    must be given to the connection handler (``framed_transfers=``) so that it
    can answer J14 ``resume`` messages for what ``result`` admitted.
    """

    transport = JobTransport(
        store_provider, registry, result_bodies=result_bodies, transfers=transfers,
    )
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
    "ResultBodyResolver",
    "job_family_handlers",
    "status_document",
]
