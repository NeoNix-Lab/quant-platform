"""Closed data-local J03 handler registry and single-Job dispatch (P04a).

ADR-0068 section 6 over ADR-0062 (unchanged). The registry is a closed set
that is declared once, at construction, by composition code inside
``quant_platform.application``: each entry names an operation kind, an
immutable implementation identity and an ADR-0062 effect-safety class, and
carries the in-process callable that implements it. There is no way to select a
handler by a caller-supplied callable, module path or name, and the registry
refuses replay, sweep, training and PBO kinds, which are never data-local J03
work. The registry that ships (:data:`DATA_LOCAL_JOB_REGISTRY`) is empty: each
real handler enters through its own implementation issue citing ADR-0068.

Dispatch claims one ``QUEUED`` Job, persists the attempt as ``RUNNING`` before
the handler runs, invokes the handler in-process and records the terminal
outcome. It never retries: a ``FAILED`` Job is not redispatched, and a Job left
``RUNNING`` by a dead process is only ever marked ``RECOVERY_REQUIRED`` by
``DurableJobStore.recover_after_restart``. The worker process and its
advisory lock are a separate unit (#335); wire submission is #340.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re

from .durable_jobs import (
    DurableJobConflict,
    DurableJobError,
    DurableJobStore,
    EffectSafety,
    JobAdmission,
    JobRecord,
    JobState,
)


class JobDispatchError(DurableJobError):
    """Base class for registry and dispatch refusals."""


class JobHandlerNotRegistered(JobDispatchError):
    """A Job names an operation the closed registry does not declare."""


REASON_HANDLER_FAILED = "handler_failed"
REASON_HANDLER_RESULT_INVALID = "handler_result_invalid"
REASON_COMPLETED_AFTER_CANCELLATION_REQUEST = "completed_after_cancellation_request"
REASON_CANCELLATION_NOT_REQUESTED = "cancellation_not_requested"

# ADR-0068 section 6: replay, sweeps, training and PBO outside the J12 budget
# may not be registered. Matched on the hyphen-separated tokens of a kind.
PROHIBITED_KIND_TOKENS = frozenset({"replay", "sweep", "sweeps", "train", "training", "pbo"})
_KIND_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_TEXT = 512


class JobHandlerFailure(Exception):
    """A handler's own failure, with a stable reason code it declares.

    Any other exception from a handler is recorded as ``handler_failed`` with
    no detail: raw exception text is never durable Job evidence (ADR-0062
    section 5).
    """

    def __init__(self, reason_code: str, diagnostic_reference: str | None = None):
        super().__init__(reason_code)
        self.reason_code = _bounded_text(reason_code, "reason_code")
        self.diagnostic_reference = _bounded_optional(diagnostic_reference, "diagnostic_reference")


@dataclass(frozen=True, slots=True)
class JobResult:
    """A handler's success: the immutable result references it produced."""

    result_references: tuple[str, ...]
    diagnostic_reference: str | None = None


@dataclass(frozen=True, slots=True)
class JobCancelled:
    """A handler's acknowledgement that it stopped after a cancellation request."""

    reason_code: str
    diagnostic_reference: str | None = None


JobHandlerCallable = Callable[[JobAdmission], "JobResult | JobCancelled"]


@dataclass(frozen=True, slots=True)
class JobHandler:
    """One declared data-local handler."""

    operation_kind: str
    contract_version: str
    implementation_identity: str
    effect_safety: EffectSafety
    execute: JobHandlerCallable

    def __post_init__(self) -> None:
        kind = self.operation_kind
        if not isinstance(kind, str) or not _KIND_PATTERN.fullmatch(kind):
            # No dots, colons, slashes or underscores: a kind can never read as
            # a module path or a callable reference.
            raise ValueError("operation_kind must be lowercase hyphen-separated tokens")
        if PROHIBITED_KIND_TOKENS.intersection(kind.split("-")):
            raise ValueError(
                "replay, sweep, training and PBO operations are not data-local J03 handlers"
            )
        for field in ("contract_version", "implementation_identity"):
            _bounded_text(getattr(self, field), field)
        if not isinstance(self.effect_safety, EffectSafety):
            raise TypeError("effect_safety must be an EffectSafety")
        if not callable(self.execute):
            raise TypeError("execute must be callable")

    def declares(self, admission: JobAdmission) -> bool:
        """Whether an admission is exactly what this handler declares."""

        return (
            admission.operation_kind == self.operation_kind
            and admission.contract_version == self.contract_version
            and admission.implementation_identity == self.implementation_identity
            and admission.effect_safety is self.effect_safety
        )


class JobHandlerRegistry:
    """A closed, immutable set of declared handlers keyed by operation kind."""

    __slots__ = ("_by_kind",)

    def __init__(self, handlers: Iterable[JobHandler] = ()):
        by_kind: dict[str, JobHandler] = {}
        for handler in handlers:
            if not isinstance(handler, JobHandler):
                raise TypeError("registry entries must be JobHandler declarations")
            if handler.operation_kind in by_kind:
                raise ValueError("operation kinds must be unique within the registry")
            by_kind[handler.operation_kind] = handler
        object.__setattr__(self, "_by_kind", by_kind)

    def __setattr__(self, name, value):  # pragma: no cover - immutability guard
        raise AttributeError("JobHandlerRegistry is immutable")

    @property
    def operation_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_kind))

    def __len__(self) -> int:
        return len(self._by_kind)

    def handler_for(self, admission: JobAdmission) -> JobHandler:
        """The handler that declares exactly this admission, or a refusal."""

        handler = self._by_kind.get(admission.operation_kind)
        if handler is None or not handler.declares(admission):
            raise JobHandlerNotRegistered(
                "operation kind, contract, implementation identity and effect safety "
                "are not declared by the closed registry"
            )
        return handler


# Data-local handlers: materialization of representations and feature
# artifacts, historical imports, catalog maintenance. None ships in P04a.
DATA_LOCAL_JOB_HANDLERS: tuple[JobHandler, ...] = ()
DATA_LOCAL_JOB_REGISTRY = JobHandlerRegistry(DATA_LOCAL_JOB_HANDLERS)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What one dispatch call did.

    ``job`` is the Job it ran to a durable outcome, or ``None`` when no
    registered ``QUEUED`` Job exists. ``skipped_unregistered`` counts ``QUEUED``
    Jobs it saw and left untouched because the registry does not declare them.
    """

    job: JobRecord | None
    skipped_unregistered: int = 0


class JobDispatcher:
    """Admit registered work and run one claimed Job per call, without retry."""

    def __init__(self, store: DurableJobStore, registry: JobHandlerRegistry):
        if not isinstance(store, DurableJobStore):
            raise TypeError("store must be a DurableJobStore")
        if not isinstance(registry, JobHandlerRegistry):
            raise TypeError("registry must be a JobHandlerRegistry")
        self.store = store
        self.registry = registry

    def admit(self, admission: JobAdmission) -> JobRecord:
        """Admit only what the closed registry declares exactly."""

        if not isinstance(admission, JobAdmission):
            raise TypeError("admission must be a JobAdmission")
        self.registry.handler_for(admission)
        return self.store.admit(admission)

    def dispatch_next(self, *, page_size: int = 100) -> DispatchResult:
        """Claim and run the first registered ``QUEUED`` Job, if any.

        The queue is walked in keyset pages of ``page_size`` until a
        registered Job is claimed or it is exhausted, so Jobs the registry does
        not declare can delay a call but never starve the ones that it does.
        Skipped Jobs are neither run nor altered.
        """

        skipped = 0
        after: str | None = None
        while True:
            page = self.store.queued_job_ids(page_size, after=after)
            if not page:
                return DispatchResult(None, skipped)
            for job_id in page:
                admission = self.store.get(job_id).admission
                try:
                    handler = self.registry.handler_for(admission)
                except JobHandlerNotRegistered:
                    skipped += 1
                    continue
                try:
                    self.store.claim(job_id)
                except DurableJobConflict:
                    continue  # No longer QUEUED: not ours to run.
                return DispatchResult(self._run(job_id, admission, handler), skipped)
            after = page[-1]

    def _run(self, job_id: str, admission: JobAdmission, handler: JobHandler) -> JobRecord:
        # The attempt is already durable as RUNNING (claim committed it).
        try:
            outcome = handler.execute(admission)
        except JobHandlerFailure as failure:
            return self._record(job_id, _Failed(failure.reason_code, failure.diagnostic_reference))
        except Exception:
            return self._record(job_id, _Failed(REASON_HANDLER_FAILED))
        # BaseException (interpreter exit, KeyboardInterrupt) is deliberately not
        # caught: the Job stays RUNNING and restart recovery marks it
        # RECOVERY_REQUIRED, never SUCCEEDED and never silently redispatched.
        return self._record(job_id, _settle(outcome))

    def _record(self, job_id: str, outcome: "_Succeeded | _Failed | _Cancelled") -> JobRecord:
        """Record a finished handler's outcome against the Job's *current* state.

        A cancellation request can arrive at any moment while the handler runs
        or while this call is deciding. The decision is therefore derived from
        the state each ``transition`` itself validates: if the state changed
        between the read and the write the transition is refused, and the
        decision is made again from the new state. It cannot loop: the only
        concurrent change is the one-way ``RUNNING -> CANCELLATION_REQUESTED``.
        """

        for _ in range(3):
            current = self.store.get(job_id)
            state, options = outcome.decide(current.state)
            try:
                return self.store.transition(job_id, state, **options)
            except DurableJobConflict:
                if self.store.get(job_id).state is current.state:
                    raise  # Not a race: a genuine refusal.
        return self.store.get(job_id)  # pragma: no cover - the state is one-way


@dataclass(frozen=True, slots=True)
class _Succeeded:
    result_references: tuple[str, ...]
    diagnostic_reference: str | None

    def decide(self, current: JobState) -> tuple[JobState, dict]:
        if current is JobState.CANCELLATION_REQUESTED:
            # ADR-0062 section 5: success cannot follow a cancellation request;
            # the handler's effects happened, so no cancellation is inferred.
            return JobState.RECOVERY_REQUIRED, {
                "reason_code": REASON_COMPLETED_AFTER_CANCELLATION_REQUEST,
            }
        return JobState.SUCCEEDED, {
            "result_references": self.result_references,
            "diagnostic_reference": self.diagnostic_reference,
        }


@dataclass(frozen=True, slots=True)
class _Failed:
    reason_code: str
    diagnostic_reference: str | None = None

    def decide(self, current: JobState) -> tuple[JobState, dict]:
        return JobState.FAILED, {
            "reason_code": self.reason_code, "diagnostic_reference": self.diagnostic_reference,
        }


@dataclass(frozen=True, slots=True)
class _Cancelled:
    reason_code: str
    diagnostic_reference: str | None

    def decide(self, current: JobState) -> tuple[JobState, dict]:
        if current is not JobState.CANCELLATION_REQUESTED:
            # An acknowledgement of a request nobody recorded proves nothing.
            return JobState.FAILED, {"reason_code": REASON_CANCELLATION_NOT_REQUESTED}
        return JobState.CANCELLED, {
            "reason_code": self.reason_code, "diagnostic_reference": self.diagnostic_reference,
        }


def _settle(outcome: object) -> "_Succeeded | _Failed | _Cancelled":
    """Validate what a handler returned, whatever it returned, into an outcome.

    Anything that is not exactly a ``JobResult`` with a non-empty tuple/list of
    bounded non-empty strings (a bare ``str`` is an iterable of characters, not
    a reference list) or a well-formed ``JobCancelled`` is an invalid result.
    """

    invalid = _Failed(REASON_HANDLER_RESULT_INVALID)
    try:
        if isinstance(outcome, JobCancelled):
            return _Cancelled(
                _bounded_text(outcome.reason_code, "reason_code"),
                _bounded_optional(outcome.diagnostic_reference, "diagnostic_reference"),
            )
        if not isinstance(outcome, JobResult):
            return invalid
        references = outcome.result_references
        if not isinstance(references, (tuple, list)) or not references:
            return invalid
        checked = tuple(_bounded_text(item, "result_reference") for item in references)
        return _Succeeded(
            checked, _bounded_optional(outcome.diagnostic_reference, "diagnostic_reference"),
        )
    except Exception:
        return invalid


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ValueError(f"{field} must be a bounded non-empty string")
    return value


def _bounded_optional(value: object, field: str) -> str | None:
    return None if value is None else _bounded_text(value, field)


__all__ = [
    "DATA_LOCAL_JOB_HANDLERS",
    "DATA_LOCAL_JOB_REGISTRY",
    "DispatchResult",
    "JobCancelled",
    "JobDispatchError",
    "JobDispatcher",
    "JobHandler",
    "JobHandlerFailure",
    "JobHandlerNotRegistered",
    "JobHandlerRegistry",
    "JobResult",
    "PROHIBITED_KIND_TOKENS",
]
