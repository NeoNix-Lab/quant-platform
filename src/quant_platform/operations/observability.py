"""Minimum K03 OperationalSignal v1 semantic runtime foundation.

Observability is evidence, not control: values in this module record facts
or claims about a subject plus their provenance.  They never mutate the
observed subject, authorize recovery/business action, or redefine an owner's
domain lifecycle.  This module intentionally contains no event bus, exporter,
durable signal store, background observer or monitoring-vendor integration --
it is the pure value/validation layer those later capabilities would sit on
top of.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from types import MappingProxyType
from typing import Any

from ..data.models import Instant


class InvalidOperationalSignal(ValueError):
    """Raised when a value violates the frozen K03 OperationalSignal v1 contract."""


class SignalKind(str, Enum):
    """Canonical v1 signal kinds. No generic custom-kind/plugin framework exists."""

    HEALTH_SNAPSHOT = "HEALTH_SNAPSHOT"
    LIFECYCLE_TRANSITION = "LIFECYCLE_TRANSITION"
    FAILURE = "FAILURE"
    OBSERVATION_UNAVAILABLE = "OBSERVATION_UNAVAILABLE"


class HealthState(str, Enum):
    """Canonical machine-readable health values.

    Observer unavailability/silence must never be normalized to ``HEALTHY``
    or ``FAILED``; it is represented as ``UNKNOWN`` evidence instead.
    """

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


_HEALTH_STATE_VALUES = {member.value for member in HealthState}

OPERATIONAL_SIGNAL_V1_SCHEMA_VERSION = "operational-signal-v1"
_OPERATIONAL_SIGNAL_V1_IDENTITY = "operational-signal-v1"
_OPERATIONAL_SIGNAL_V1_DOMAIN_TAG = (
    b"quant-platform/operational-signal-v1/signal-id\x00"
)


def _require_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationalSignal(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class SubjectReference:
    """A typed, owner-supplied stable reference to a signal's subject.

    ``subject_identity`` is supplied by the owning bounded context (for
    example a DatasetIdentity's stable string form, a RunIdentity, a
    storage-root id or a runtime-instance identity).  K03 does not replace
    those owner identities with a global observability id.
    """

    subject_kind: str
    subject_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "subject_kind", _require_non_empty_str(self.subject_kind, "subject_kind")
        )
        object.__setattr__(
            self,
            "subject_identity",
            _require_non_empty_str(self.subject_identity, "subject_identity"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A typed, owner-supplied stable reference to provenance/evidence.

    K03 does not duplicate owner payloads or reinterpret them; it only
    carries a stable pointer to evidence the owning capability can resolve.
    """

    evidence_kind: str
    evidence_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_kind", _require_non_empty_str(self.evidence_kind, "evidence_kind")
        )
        object.__setattr__(
            self,
            "evidence_identity",
            _require_non_empty_str(self.evidence_identity, "evidence_identity"),
        )


def _canonicalize_payload_value(value: Any) -> Any:
    """Normalize one payload value into the supported v1 canonical domain.

    Only ``str``/``bool``/``int``/``None`` leaves and ``Mapping``/``list``/
    ``tuple`` containers are supported.  Anything else (float, set, bytes,
    datetime, a custom object, ...) is explicitly refused rather than hashed
    via incidental Python representation.
    """

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        canonical_items = []
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise InvalidOperationalSignal("payload mapping keys must be non-empty strings")
            canonical_items.append((key, _canonicalize_payload_value(item)))
        canonical_items.sort(key=lambda pair: pair[0])
        return MappingProxyType(dict(canonical_items))
    if isinstance(value, (list, tuple)):
        return tuple(_canonicalize_payload_value(item) for item in value)
    raise InvalidOperationalSignal(f"unsupported payload value type: {type(value).__name__}")


def _frame_canonical_value(value: Any) -> bytes:
    """Deterministically frame one already-canonicalized value into bytes."""

    if value is None:
        return b"\x00"
    if isinstance(value, bool):
        return b"\x01" + (b"\x01" if value else b"\x00")
    if isinstance(value, int):
        text = str(value).encode("utf-8")
        return b"\x02" + len(text).to_bytes(8, "big") + text
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"\x03" + len(encoded).to_bytes(8, "big") + encoded
    if isinstance(value, MappingProxyType):
        items = list(value.items())
        body = b"".join(_frame_canonical_value(k) + _frame_canonical_value(v) for k, v in items)
        return b"\x04" + len(items).to_bytes(8, "big") + body
    if isinstance(value, tuple):
        body = b"".join(_frame_canonical_value(item) for item in value)
        return b"\x05" + len(value).to_bytes(8, "big") + body
    raise InvalidOperationalSignal(  # pragma: no cover - unreachable after canonicalization
        f"unsupported canonical payload value: {type(value).__name__}"
    )


def _validate_payload_for_kind(kind: SignalKind, payload: Mapping[str, Any]) -> None:
    if kind is SignalKind.HEALTH_SNAPSHOT:
        health = payload.get("health")
        if not isinstance(health, str) or health not in _HEALTH_STATE_VALUES:
            raise InvalidOperationalSignal(
                "HEALTH_SNAPSHOT payload requires a canonical 'health' value"
            )
    elif kind is SignalKind.OBSERVATION_UNAVAILABLE:
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidOperationalSignal(
                "OBSERVATION_UNAVAILABLE payload requires a non-empty 'reason'"
            )
        health = payload.get("health")
        if health is not None and health != HealthState.UNKNOWN.value:
            raise InvalidOperationalSignal(
                "OBSERVATION_UNAVAILABLE cannot claim a subject health other than UNKNOWN"
            )
    elif kind is SignalKind.LIFECYCLE_TRANSITION:
        previous_state = payload.get("previous_state")
        resulting_state = payload.get("resulting_state")
        context = payload.get("context")
        if not isinstance(previous_state, str) or not previous_state.strip():
            raise InvalidOperationalSignal(
                "LIFECYCLE_TRANSITION payload requires a non-empty 'previous_state'"
            )
        if not isinstance(resulting_state, str) or not resulting_state.strip():
            raise InvalidOperationalSignal(
                "LIFECYCLE_TRANSITION payload requires a non-empty 'resulting_state'"
            )
        if not isinstance(context, MappingProxyType) or not context:
            raise InvalidOperationalSignal(
                "LIFECYCLE_TRANSITION payload requires non-empty transition 'context'"
            )
    elif kind is SignalKind.FAILURE:
        failure_code = payload.get("failure_code")
        context = payload.get("context")
        if not isinstance(failure_code, str) or not failure_code.strip():
            raise InvalidOperationalSignal(
                "FAILURE payload requires a non-empty machine-readable 'failure_code'"
            )
        if not isinstance(context, MappingProxyType):
            raise InvalidOperationalSignal(
                "FAILURE payload requires structured machine-readable 'context'"
            )
    else:  # pragma: no cover - SignalKind is exhaustive by construction
        raise InvalidOperationalSignal(f"unsupported signal kind: {kind!r}")


def _compute_signal_id(
    *,
    subject: SubjectReference,
    capability_id: str,
    kind: SignalKind,
    correlation_id: str | None,
    sequence: int | None,
    observed_at: Instant,
    occurred_at: Instant | None,
    payload: Any,
    evidence: tuple[EvidenceReference, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(_OPERATIONAL_SIGNAL_V1_DOMAIN_TAG)
    digest.update(_frame_canonical_value(OPERATIONAL_SIGNAL_V1_SCHEMA_VERSION))
    digest.update(_frame_canonical_value(subject.subject_kind))
    digest.update(_frame_canonical_value(subject.subject_identity))
    digest.update(_frame_canonical_value(capability_id))
    digest.update(_frame_canonical_value(kind.value))
    digest.update(_frame_canonical_value(correlation_id))
    digest.update(_frame_canonical_value(sequence))
    digest.update(_frame_canonical_value(observed_at.isoformat()))
    digest.update(
        _frame_canonical_value(occurred_at.isoformat() if occurred_at is not None else None)
    )
    digest.update(_frame_canonical_value(payload))
    digest.update(len(evidence).to_bytes(8, "big"))
    for ref in evidence:
        digest.update(_frame_canonical_value(ref.evidence_kind))
        digest.update(_frame_canonical_value(ref.evidence_identity))
    return f"{_OPERATIONAL_SIGNAL_V1_IDENTITY}:sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationalSignalV1:
    """The minimum canonical OperationalSignal v1 semantic envelope.

    Transport receive time, exporter id, dashboard id, storage offset,
    physical log path and presentation text are not semantic signal
    identity; ``diagnostics`` exists precisely to carry that kind of
    non-semantic metadata without it affecting ``signal_id``.
    """

    subject: SubjectReference
    capability_id: str
    kind: SignalKind
    observed_at: Instant
    payload: Mapping[str, Any]
    occurred_at: Instant | None = None
    correlation_id: str | None = None
    sequence: int | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    diagnostics: Mapping[str, str] = MappingProxyType({})
    schema_version: str = field(init=False, default=OPERATIONAL_SIGNAL_V1_SCHEMA_VERSION)
    signal_id: str = field(init=False, default="")

    def __post_init__(self) -> None:
        if not isinstance(self.subject, SubjectReference):
            raise InvalidOperationalSignal("subject must be a SubjectReference")
        object.__setattr__(
            self, "capability_id", _require_non_empty_str(self.capability_id, "capability_id")
        )
        if not isinstance(self.kind, SignalKind):
            raise InvalidOperationalSignal("kind must be a SignalKind")
        if not isinstance(self.observed_at, Instant):
            raise InvalidOperationalSignal("observed_at must be a canonical Instant")
        if self.occurred_at is not None and not isinstance(self.occurred_at, Instant):
            raise InvalidOperationalSignal(
                "occurred_at must be a canonical Instant when supplied"
            )

        if self.correlation_id is not None:
            object.__setattr__(
                self,
                "correlation_id",
                _require_non_empty_str(self.correlation_id, "correlation_id"),
            )
        if self.sequence is not None:
            if self.correlation_id is None:
                raise InvalidOperationalSignal("sequence requires a correlation_id")
            if (
                not isinstance(self.sequence, int)
                or isinstance(self.sequence, bool)
                or self.sequence < 1
            ):
                raise InvalidOperationalSignal("sequence must be a positive integer")

        if not isinstance(self.payload, Mapping):
            raise InvalidOperationalSignal("payload must be a mapping")
        normalized_payload = _canonicalize_payload_value(dict(self.payload))
        _validate_payload_for_kind(self.kind, normalized_payload)
        object.__setattr__(self, "payload", normalized_payload)

        for ref in self.evidence:
            if not isinstance(ref, EvidenceReference):
                raise InvalidOperationalSignal("evidence entries must be EvidenceReference values")
        canonical_evidence = tuple(
            sorted(set(self.evidence), key=lambda ref: (ref.evidence_kind, ref.evidence_identity))
        )
        object.__setattr__(self, "evidence", canonical_evidence)

        if not isinstance(self.diagnostics, Mapping):
            raise InvalidOperationalSignal("diagnostics must be a mapping of str to str")
        normalized_diagnostics = {}
        for key, item in self.diagnostics.items():
            if not isinstance(key, str) or not key or not isinstance(item, str):
                raise InvalidOperationalSignal("diagnostics must map non-empty str keys to str values")
            normalized_diagnostics[key] = item
        object.__setattr__(self, "diagnostics", MappingProxyType(normalized_diagnostics))

        signal_id = _compute_signal_id(
            subject=self.subject,
            capability_id=self.capability_id,
            kind=self.kind,
            correlation_id=self.correlation_id,
            sequence=self.sequence,
            observed_at=self.observed_at,
            occurred_at=self.occurred_at,
            payload=self.payload,
            evidence=self.evidence,
        )
        object.__setattr__(self, "signal_id", signal_id)


class SignalRelation(str, Enum):
    """The relation between two signals sharing one stream position."""

    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


def compare_signals(first: OperationalSignalV1, second: OperationalSignalV1) -> SignalRelation:
    """Classify two signals claiming the same correlated stream position.

    Same subject + correlation_id + sequence + equivalent canonical content
    is an idempotent duplicate delivery; same position with incompatible
    canonical semantic content is an explicit conflict.
    """

    if (
        first.subject != second.subject
        or first.correlation_id != second.correlation_id
        or first.sequence != second.sequence
    ):
        raise InvalidOperationalSignal(
            "signals are not comparable: they do not share one stream position"
        )
    if first.correlation_id is None or first.sequence is None:
        raise InvalidOperationalSignal(
            "comparable signals require a correlated sequence position"
        )
    return SignalRelation.DUPLICATE if first.signal_id == second.signal_id else SignalRelation.CONFLICT


@dataclass(frozen=True, slots=True)
class SignalStreamGap:
    """One explicit missing sequence number within an observed stream range."""

    subject: SubjectReference
    correlation_id: str
    missing_sequence: int


@dataclass(frozen=True, slots=True)
class SignalStreamConflict:
    """Two or more incompatible signals claiming the same stream position."""

    subject: SubjectReference
    correlation_id: str
    sequence: int
    signals: tuple[OperationalSignalV1, ...]


@dataclass(frozen=True, slots=True)
class OrderedSignalStream:
    """One correlated stream, canonically ordered by sequence.

    ``ordered_signals`` holds one representative per observed sequence
    (duplicates collapse to their shared ``signal_id``).  A sequence gap or
    an unresolved conflict must never be treated as a complete history:
    consumers must consult ``gaps``/``conflicts`` explicitly.
    """

    subject: SubjectReference
    correlation_id: str
    ordered_signals: tuple[OperationalSignalV1, ...]
    gaps: tuple[SignalStreamGap, ...]
    conflicts: tuple[SignalStreamConflict, ...]


def build_signal_stream(signals: Iterable[OperationalSignalV1]) -> OrderedSignalStream:
    """Canonically order a correlated stream and surface gaps/conflicts explicitly.

    Delivery order of ``signals`` is irrelevant; sequence order is
    authoritative.  Every input signal must share one subject and
    correlation_id and must carry a sequence -- standalone snapshots do not
    belong to a stream and cannot be ordered by this function.
    """

    ordered_inputs = tuple(signals)
    if not ordered_inputs:
        raise InvalidOperationalSignal("a signal stream requires at least one signal")

    subject = ordered_inputs[0].subject
    correlation_id = ordered_inputs[0].correlation_id
    if correlation_id is None:
        raise InvalidOperationalSignal("a signal stream requires a correlation_id")

    by_sequence: dict[int, list[OperationalSignalV1]] = {}
    for signal in ordered_inputs:
        if signal.subject != subject or signal.correlation_id != correlation_id:
            raise InvalidOperationalSignal(
                "all signals in a stream must share one subject and correlation_id"
            )
        if signal.sequence is None:
            raise InvalidOperationalSignal(
                "every signal in a stream must carry a sequence"
            )
        by_sequence.setdefault(signal.sequence, []).append(signal)

    ordered_signals: list[OperationalSignalV1] = []
    conflicts: list[SignalStreamConflict] = []
    for sequence in sorted(by_sequence):
        bucket = by_sequence[sequence]
        distinct_by_id: dict[str, OperationalSignalV1] = {}
        for signal in bucket:
            distinct_by_id.setdefault(signal.signal_id, signal)
        distinct = tuple(distinct_by_id.values())
        if len(distinct) > 1:
            conflicts.append(SignalStreamConflict(subject, correlation_id, sequence, distinct))
        ordered_signals.append(distinct[0])

    observed_sequences = sorted(by_sequence)
    gaps = tuple(
        SignalStreamGap(subject, correlation_id, missing)
        for missing in range(observed_sequences[0], observed_sequences[-1] + 1)
        if missing not in by_sequence
    )

    return OrderedSignalStream(
        subject=subject,
        correlation_id=correlation_id,
        ordered_signals=tuple(ordered_signals),
        gaps=gaps,
        conflicts=tuple(conflicts),
    )
