"""PressurePolicyDefinition v1 pure capacity-pressure evaluation.

K05 consumes K04 capacity evidence, one explicit evaluation instant and
optional caller-supplied write-rate evidence. It does not observe filesystems,
sample write history, mutate storage, schedule work or authorize deletion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
import hashlib
from typing import Any, Mapping

from .capacity import CapacityObservation, CapacityUnavailable
from quant_platform.canonical import canonical_bytes


PRESSURE_POLICY_DEFINITION_V1_VERSION = "1"
PRESSURE_POLICY_IDENTITY_DOMAIN = "pressure-policy-definition-v1"
PRESSURE_DECISION_IDENTITY_DOMAIN = "pressure-decision-v1"
PRESSURE_DECISION_UNAVAILABLE_IDENTITY_DOMAIN = "pressure-decision-unavailable-v1"
CAPACITY_EVIDENCE_IDENTITY_DOMAIN = "capacity-observation-v1"
RATE_EVIDENCE_IDENTITY_DOMAIN = "write-rate-observation-v1"


class PressurePolicyError(ValueError):
    """PressurePolicyDefinition v1 semantic validation failed."""


class PressureState(StrEnum):
    NORMAL = "NORMAL"
    PRESSURE = "PRESSURE"
    CRITICAL = "CRITICAL"
    EXHAUSTED = "EXHAUSTED"


class PressureDecisionUnavailableReason(StrEnum):
    MISSING_CAPACITY = "missing_capacity"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    CAPACITY_STALE = "capacity_stale"
    CAPACITY_FUTURE_DATED = "capacity_future_dated"
    CAPACITY_MALFORMED = "capacity_malformed"
    MISSING_RATE = "missing_rate"
    RATE_STALE = "rate_stale"
    RATE_FUTURE_DATED = "rate_future_dated"
    RATE_MALFORMED = "rate_malformed"
    MALFORMED_EVALUATION_INSTANT = "malformed_evaluation_instant"


class TimeToFullKind(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUATED_EXHAUSTED = "not_evaluated_exhausted"
    UNBOUNDED = "unbounded"
    FINITE = "finite"


@dataclass(frozen=True, slots=True)
class PressurePolicyDefinition:
    """Immutable K05 v1 pressure policy definition.

    Numeric thresholds are deployment configuration, but their ordering,
    freshness and optional time-to-full semantics are canonical v1 meaning.
    """

    max_capacity_observation_age: timedelta
    pressure_available_bytes: int
    critical_available_bytes: int
    exhausted_available_bytes: int
    time_to_full_enabled: bool = False
    max_rate_observation_age: timedelta | None = None
    pressure_time_to_full: timedelta | None = None
    critical_time_to_full: timedelta | None = None
    semantic_version: str = PRESSURE_POLICY_DEFINITION_V1_VERSION

    def __post_init__(self) -> None:
        if self.semantic_version != PRESSURE_POLICY_DEFINITION_V1_VERSION:
            raise PressurePolicyError("PressurePolicyDefinition v1 requires semantic_version == '1'")
        capacity_age_us = _timedelta_us(
            self.max_capacity_observation_age,
            "max_capacity_observation_age",
            allow_zero=True,
        )
        pressure_bytes = _non_negative_int(
            self.pressure_available_bytes,
            "pressure_available_bytes",
        )
        critical_bytes = _non_negative_int(
            self.critical_available_bytes,
            "critical_available_bytes",
        )
        exhausted_bytes = _non_negative_int(
            self.exhausted_available_bytes,
            "exhausted_available_bytes",
        )
        if not exhausted_bytes <= critical_bytes <= pressure_bytes:
            raise PressurePolicyError(
                "byte thresholds must satisfy exhausted <= critical <= pressure"
            )
        if type(self.time_to_full_enabled) is not bool:
            raise PressurePolicyError("time_to_full_enabled must be boolean")
        if self.time_to_full_enabled:
            if (
                self.max_rate_observation_age is None
                or self.pressure_time_to_full is None
                or self.critical_time_to_full is None
            ):
                raise PressurePolicyError(
                    "enabled time-to-full requires rate freshness and pressure/critical thresholds"
                )
            _timedelta_us(
                self.max_rate_observation_age,
                "max_rate_observation_age",
                allow_zero=True,
            )
            pressure_ttf_us = _timedelta_us(
                self.pressure_time_to_full,
                "pressure_time_to_full",
                allow_zero=False,
            )
            critical_ttf_us = _timedelta_us(
                self.critical_time_to_full,
                "critical_time_to_full",
                allow_zero=False,
            )
            if critical_ttf_us > pressure_ttf_us:
                raise PressurePolicyError(
                    "time-to-full thresholds must satisfy critical <= pressure"
                )
            object.__setattr__(self, "max_rate_observation_age", self.max_rate_observation_age)
            object.__setattr__(self, "pressure_time_to_full", self.pressure_time_to_full)
            object.__setattr__(self, "critical_time_to_full", self.critical_time_to_full)
        elif (
            self.max_rate_observation_age is not None
            or self.pressure_time_to_full is not None
            or self.critical_time_to_full is not None
        ):
            raise PressurePolicyError("disabled time-to-full must not declare rate thresholds")
        object.__setattr__(self, "max_capacity_observation_age", timedelta(microseconds=capacity_age_us))
        object.__setattr__(self, "pressure_available_bytes", pressure_bytes)
        object.__setattr__(self, "critical_available_bytes", critical_bytes)
        object.__setattr__(self, "exhausted_available_bytes", exhausted_bytes)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "definition_version": PRESSURE_POLICY_DEFINITION_V1_VERSION,
            "freshness": {
                "capacity_observation_max_age_us": str(
                    _timedelta_us(
                        self.max_capacity_observation_age,
                        "max_capacity_observation_age",
                        allow_zero=True,
                    )
                ),
                "fresh_when": "age <= max_age",
                "future_dated_evidence": "unavailable",
            },
            "identity_excludes": [
                "host_id",
                "process_id",
                "physical_mount_path",
                "current_capacity_values",
                "monitoring_exporter",
            ],
            "pressure_states": ["NORMAL", "PRESSURE", "CRITICAL", "EXHAUSTED"],
            "semantic_version": self.semantic_version,
            "thresholds": {
                "available_bytes": {
                    "pressure": str(self.pressure_available_bytes),
                    "critical": str(self.critical_available_bytes),
                    "exhausted": str(self.exhausted_available_bytes),
                    "ordering": "0 <= exhausted <= critical <= pressure",
                }
            },
            "time_to_full": {"enabled": self.time_to_full_enabled},
        }
        if self.time_to_full_enabled:
            payload["time_to_full"] = {
                "enabled": True,
                "max_rate_observation_age_us": str(
                    _timedelta_us(
                        self.max_rate_observation_age,
                        "max_rate_observation_age",
                        allow_zero=True,
                    )
                ),
                "pressure_time_to_full_us": str(
                    _timedelta_us(
                        self.pressure_time_to_full,
                        "pressure_time_to_full",
                        allow_zero=False,
                    )
                ),
                "critical_time_to_full_us": str(
                    _timedelta_us(
                        self.critical_time_to_full,
                        "critical_time_to_full",
                        allow_zero=False,
                    )
                ),
                "zero_rate": "unbounded_no_finite_forecast",
            }
        return payload

    @property
    def canonical_utf8_serialization(self) -> str:
        return _canonical_json(self.canonical_payload)

    @property
    def definition_identity(self) -> str:
        digest = _canonical_fingerprint(self.canonical_payload)
        return f"{PRESSURE_POLICY_IDENTITY_DOMAIN}:sha256:{digest}"

    @property
    def identity(self) -> str:
        return self.definition_identity

    def stable_dict(self) -> dict[str, Any]:
        return {
            "definition_identity": self.definition_identity,
            "canonical_payload": self.canonical_payload,
        }


@dataclass(frozen=True, slots=True)
class WriteRateObservation:
    """Caller-supplied immutable write-rate evidence for optional forecasting."""

    storage_root_id: str
    bytes_per_second: Any
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    evidence_identity: str


@dataclass(frozen=True, slots=True)
class TimeToFullEstimate:
    kind: TimeToFullKind
    finite_seconds: str | None = None

    def stable_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "finite_seconds": self.finite_seconds,
        }


@dataclass(frozen=True, slots=True)
class PressureRestrictions:
    """K05 upper-bound restrictions; never mutation/deletion grants."""

    state: PressureState
    optional_bulk_recomputable_work_allowed: bool
    new_data_producing_writes_allowed: bool
    safety_writes_require_independent_capacity_proof: bool
    diagnostics_allowed: bool = True
    independently_authorized_recovery_only: bool = True
    delete_authorized: bool = False

    def stable_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "optional_bulk_recomputable_work_allowed": self.optional_bulk_recomputable_work_allowed,
            "new_data_producing_writes_allowed": self.new_data_producing_writes_allowed,
            "safety_writes_require_independent_capacity_proof": (
                self.safety_writes_require_independent_capacity_proof
            ),
            "diagnostics_allowed": self.diagnostics_allowed,
            "independently_authorized_recovery_only": self.independently_authorized_recovery_only,
            "delete_authorized": self.delete_authorized,
        }


@dataclass(frozen=True, slots=True)
class PressureDecision:
    """One successful deterministic pressure evaluation."""

    storage_root_id: str
    policy_definition_identity: str
    as_of: datetime
    capacity_evidence: Mapping[str, Any]
    rate_evidence: Mapping[str, Any] | None
    time_to_full: TimeToFullEstimate
    state: PressureState
    restrictions: PressureRestrictions

    @property
    def decision_identity(self) -> str:
        return f"{PRESSURE_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    @property
    def identity(self) -> str:
        return self.decision_identity

    @property
    def delete_authorized(self) -> bool:
        return self.restrictions.delete_authorized

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": PRESSURE_DECISION_IDENTITY_DOMAIN,
            "storage_root_id": self.storage_root_id,
            "policy_definition_identity": self.policy_definition_identity,
            "as_of": _instant_string(self.as_of),
            "capacity_evidence": dict(self.capacity_evidence),
            "rate_evidence": None if self.rate_evidence is None else dict(self.rate_evidence),
            "time_to_full": self.time_to_full.stable_dict(),
            "state": self.state.value,
            "restrictions": self.restrictions.stable_dict(),
        }
        if include_identity:
            payload["decision_identity"] = self.decision_identity
        return payload


@dataclass(frozen=True, slots=True)
class PressureDecisionUnavailable:
    """Explicit K05 refusal to fabricate a pressure state."""

    reason: PressureDecisionUnavailableReason
    storage_root_id: str | None
    policy_definition_identity: str | None
    as_of: str | None
    evidence: Mapping[str, Any]

    @property
    def decision_identity(self) -> str:
        return f"{PRESSURE_DECISION_UNAVAILABLE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    @property
    def identity(self) -> str:
        return self.decision_identity

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": PRESSURE_DECISION_UNAVAILABLE_IDENTITY_DOMAIN,
            "reason": self.reason.value,
            "storage_root_id": self.storage_root_id,
            "policy_definition_identity": self.policy_definition_identity,
            "as_of": self.as_of,
            "evidence": dict(self.evidence),
        }
        if include_identity:
            payload["decision_identity"] = self.decision_identity
        return payload


def evaluate_pressure(
    *,
    policy: PressurePolicyDefinition,
    capacity: CapacityObservation | CapacityUnavailable | None,
    as_of: datetime,
    rate: WriteRateObservation | None = None,
) -> PressureDecision | PressureDecisionUnavailable:
    """Evaluate K05 pressure from explicit evidence and an explicit UTC instant."""

    policy_id = policy.definition_identity if isinstance(policy, PressurePolicyDefinition) else None
    try:
        evaluation_instant = _utc_datetime(as_of, "as_of")
    except PressurePolicyError as exc:
        return PressureDecisionUnavailable(
            reason=PressureDecisionUnavailableReason.MALFORMED_EVALUATION_INSTANT,
            storage_root_id=_storage_root_id_from(capacity, rate),
            policy_definition_identity=policy_id,
            as_of=None,
            evidence={"error": str(exc), "as_of_repr": repr(as_of)},
        )
    as_of_text = _instant_string(evaluation_instant)

    capacity_result = _capacity_evidence(policy, capacity, evaluation_instant)
    if isinstance(capacity_result, PressureDecisionUnavailable):
        return _with_evaluation_context(capacity_result, policy_id, as_of_text)
    capacity_evidence = capacity_result
    available_bytes = capacity_evidence["available_bytes"]

    if available_bytes <= policy.exhausted_available_bytes:
        state = PressureState.EXHAUSTED
        time_to_full = TimeToFullEstimate(TimeToFullKind.NOT_EVALUATED_EXHAUSTED)
        rate_evidence = None
    elif policy.time_to_full_enabled:
        rate_result = _rate_evidence(policy, rate, evaluation_instant)
        if isinstance(rate_result, PressureDecisionUnavailable):
            return _with_evaluation_context(rate_result, policy_id, as_of_text)
        rate_evidence = rate_result
        time_to_full = _time_to_full(available_bytes, rate_evidence["bytes_per_second"])
        state = _classify(policy, available_bytes, time_to_full, rate_evidence["bytes_per_second"])
    else:
        rate_evidence = None
        time_to_full = TimeToFullEstimate(TimeToFullKind.NOT_APPLICABLE)
        state = _classify(policy, available_bytes, time_to_full)

    return PressureDecision(
        storage_root_id=capacity_evidence["storage_root_id"],
        policy_definition_identity=policy.definition_identity,
        as_of=evaluation_instant,
        capacity_evidence=capacity_evidence,
        rate_evidence=rate_evidence,
        time_to_full=time_to_full,
        state=state,
        restrictions=restrictions_for_state(state),
    )


def restrictions_for_state(state: PressureState) -> PressureRestrictions:
    normalized = PressureState(state)
    if normalized == PressureState.NORMAL:
        return PressureRestrictions(normalized, True, True, False)
    if normalized == PressureState.PRESSURE:
        return PressureRestrictions(normalized, False, True, False)
    if normalized == PressureState.CRITICAL:
        return PressureRestrictions(normalized, False, False, True)
    if normalized == PressureState.EXHAUSTED:
        return PressureRestrictions(normalized, False, False, True)
    raise AssertionError("unreachable pressure state")


def _capacity_evidence(
    policy: PressurePolicyDefinition,
    capacity: CapacityObservation | CapacityUnavailable | None,
    as_of: datetime,
) -> dict[str, Any] | PressureDecisionUnavailable:
    if capacity is None:
        return _unavailable(
            PressureDecisionUnavailableReason.MISSING_CAPACITY,
            None,
            {"required": "fresh CapacityObservation"},
        )
    if isinstance(capacity, CapacityUnavailable):
        return _unavailable(
            PressureDecisionUnavailableReason.CAPACITY_UNAVAILABLE,
            _non_empty_or_none(capacity.storage_root_id),
            {
                "reason": capacity.reason,
                "root_path_excluded_from_identity": True,
            },
        )
    if not isinstance(capacity, CapacityObservation):
        return _unavailable(
            PressureDecisionUnavailableReason.CAPACITY_MALFORMED,
            _storage_root_id_from(capacity, None),
            {"error": "capacity must be CapacityObservation or CapacityUnavailable"},
        )
    try:
        storage_root_id = _non_empty_text(capacity.storage_root_id, "capacity.storage_root_id")
        observed_at = _utc_datetime(capacity.observed_at, "capacity.observed_at")
        total_bytes = _non_negative_int(capacity.total_bytes, "capacity.total_bytes")
        used_bytes = _non_negative_int(capacity.used_bytes, "capacity.used_bytes")
        available_bytes = _non_negative_int(capacity.available_bytes, "capacity.available_bytes")
    except PressurePolicyError as exc:
        return _unavailable(
            PressureDecisionUnavailableReason.CAPACITY_MALFORMED,
            _storage_root_id_from(capacity, None),
            {"error": str(exc)},
        )
    age = as_of - observed_at
    if age < timedelta(0):
        return _unavailable(
            PressureDecisionUnavailableReason.CAPACITY_FUTURE_DATED,
            storage_root_id,
            {"observed_at": _instant_string(observed_at), "as_of": _instant_string(as_of)},
        )
    if age > policy.max_capacity_observation_age:
        return _unavailable(
            PressureDecisionUnavailableReason.CAPACITY_STALE,
            storage_root_id,
            {
                "observed_at": _instant_string(observed_at),
                "age_us": str(_timedelta_us(age, "capacity_age", allow_zero=True)),
                "max_age_us": str(
                    _timedelta_us(
                        policy.max_capacity_observation_age,
                        "max_capacity_observation_age",
                        allow_zero=True,
                    )
                ),
            },
        )
    payload = {
        "storage_root_id": storage_root_id,
        "observed_at": _instant_string(observed_at),
        "age_us": str(_timedelta_us(age, "capacity_age", allow_zero=True)),
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "available_bytes": available_bytes,
        "os_reported_available_bytes_used_directly": True,
        "root_path_excluded_from_identity": True,
    }
    payload["evidence_identity"] = (
        f"{CAPACITY_EVIDENCE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"
    )
    return payload


def _rate_evidence(
    policy: PressurePolicyDefinition,
    rate: WriteRateObservation | None,
    as_of: datetime,
) -> dict[str, Any] | PressureDecisionUnavailable:
    if rate is None:
        return _unavailable(
            PressureDecisionUnavailableReason.MISSING_RATE,
            None,
            {"required": "fresh WriteRateObservation"},
        )
    if not isinstance(rate, WriteRateObservation):
        return _unavailable(
            PressureDecisionUnavailableReason.RATE_MALFORMED,
            _storage_root_id_from(None, rate),
            {"error": "rate must be WriteRateObservation"},
        )
    try:
        storage_root_id = _non_empty_text(rate.storage_root_id, "rate.storage_root_id")
        observed_at = _utc_datetime(rate.observed_at, "rate.observed_at")
        window_start = _utc_datetime(rate.window_start, "rate.window_start")
        window_end = _utc_datetime(rate.window_end, "rate.window_end")
        if window_end < window_start:
            raise PressurePolicyError("rate window_end must not precede window_start")
        bytes_per_second = _non_negative_decimal(
            rate.bytes_per_second,
            "rate.bytes_per_second",
        )
        evidence_identity = _non_empty_text(rate.evidence_identity, "rate.evidence_identity")
    except PressurePolicyError as exc:
        return _unavailable(
            PressureDecisionUnavailableReason.RATE_MALFORMED,
            _storage_root_id_from(None, rate),
            {"error": str(exc)},
        )
    age = as_of - observed_at
    if age < timedelta(0):
        return _unavailable(
            PressureDecisionUnavailableReason.RATE_FUTURE_DATED,
            storage_root_id,
            {"observed_at": _instant_string(observed_at), "as_of": _instant_string(as_of)},
        )
    if age > policy.max_rate_observation_age:
        return _unavailable(
            PressureDecisionUnavailableReason.RATE_STALE,
            storage_root_id,
            {
                "observed_at": _instant_string(observed_at),
                "age_us": str(_timedelta_us(age, "rate_age", allow_zero=True)),
                "max_age_us": str(
                    _timedelta_us(
                        policy.max_rate_observation_age,
                        "max_rate_observation_age",
                        allow_zero=True,
                    )
                ),
            },
        )
    payload = {
        "storage_root_id": storage_root_id,
        "observed_at": _instant_string(observed_at),
        "age_us": str(_timedelta_us(age, "rate_age", allow_zero=True)),
        "window_start": _instant_string(window_start),
        "window_end": _instant_string(window_end),
        "bytes_per_second": _decimal_string(bytes_per_second),
        "caller_evidence_identity": evidence_identity,
    }
    payload["canonical_evidence_identity"] = (
        f"{RATE_EVIDENCE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"
    )
    return payload


def _classify(
    policy: PressurePolicyDefinition,
    available_bytes: int,
    time_to_full: TimeToFullEstimate,
    rate_bytes_per_second: str | None = None,
) -> PressureState:
    finite_rate = Decimal(rate_bytes_per_second) if rate_bytes_per_second is not None else None
    if available_bytes <= policy.exhausted_available_bytes:
        return PressureState.EXHAUSTED
    if available_bytes <= policy.critical_available_bytes:
        return PressureState.CRITICAL
    if finite_rate is not None and _finite_time_at_or_below(
        available_bytes,
        finite_rate,
        policy.critical_time_to_full,
    ):
        return PressureState.CRITICAL
    if available_bytes <= policy.pressure_available_bytes:
        return PressureState.PRESSURE
    if finite_rate is not None and _finite_time_at_or_below(
        available_bytes,
        finite_rate,
        policy.pressure_time_to_full,
    ):
        return PressureState.PRESSURE
    return PressureState.NORMAL


def _time_to_full(available_bytes: int, rate_bytes_per_second: str) -> TimeToFullEstimate:
    rate = Decimal(rate_bytes_per_second)
    if rate == 0:
        return TimeToFullEstimate(TimeToFullKind.UNBOUNDED)
    precision = max(28, len(str(available_bytes)) + len(rate.as_tuple().digits) + 10)
    with localcontext() as context:
        context.prec = precision
        finite_seconds = Decimal(available_bytes) / rate
    return TimeToFullEstimate(
        TimeToFullKind.FINITE,
        _decimal_string(finite_seconds),
    )


def _finite_time_at_or_below(
    available_bytes: int,
    rate_bytes_per_second: Decimal,
    threshold: timedelta | None,
) -> bool:
    if threshold is None:
        return False
    return Decimal(available_bytes) <= _duration_seconds(threshold) * rate_bytes_per_second


def _duration_seconds(value: timedelta | None) -> Decimal:
    if value is None:
        return Decimal("-1")
    return Decimal(_timedelta_us(value, "duration", allow_zero=False)) / Decimal(1_000_000)


def _unavailable(
    reason: PressureDecisionUnavailableReason,
    storage_root_id: str | None,
    evidence: Mapping[str, Any],
) -> PressureDecisionUnavailable:
    return PressureDecisionUnavailable(
        reason=reason,
        storage_root_id=storage_root_id,
        policy_definition_identity=None,
        as_of=None,
        evidence=evidence,
    )


def _with_evaluation_context(
    unavailable: PressureDecisionUnavailable,
    policy_id: str | None,
    as_of_text: str,
) -> PressureDecisionUnavailable:
    return PressureDecisionUnavailable(
        reason=unavailable.reason,
        storage_root_id=unavailable.storage_root_id,
        policy_definition_identity=policy_id,
        as_of=as_of_text,
        evidence=unavailable.evidence,
    )


def _utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise PressurePolicyError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PressurePolicyError(f"{field} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    if normalized.tzinfo is None or normalized.utcoffset() != timedelta(0):
        raise PressurePolicyError(f"{field} must be UTC-aware")
    return normalized


def _instant_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timedelta_us(value: Any, field: str, *, allow_zero: bool) -> int:
    if not isinstance(value, timedelta):
        raise PressurePolicyError(f"{field} must be a timedelta")
    microseconds = (
        (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    )
    if microseconds < 0 or (microseconds == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise PressurePolicyError(f"{field} must be {qualifier}")
    return microseconds


def _non_negative_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise PressurePolicyError(f"{field} must be an integer")
    if value < 0:
        raise PressurePolicyError(f"{field} must be non-negative")
    return value


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PressurePolicyError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise PressurePolicyError(f"{field} must not contain control characters")
    return text


def _non_empty_or_none(value: Any) -> str | None:
    try:
        return _non_empty_text(value, "storage_root_id")
    except PressurePolicyError:
        return None


def _non_negative_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PressurePolicyError(f"{field} must be numeric")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise PressurePolicyError(f"{field} must be finite")
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, Decimal):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise PressurePolicyError(f"{field} must be numeric")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise PressurePolicyError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise PressurePolicyError(f"{field} must be finite")
    if result < 0:
        raise PressurePolicyError(f"{field} must be non-negative")
    return result


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _storage_root_id_from(capacity: Any, rate: Any) -> str | None:
    for source in (capacity, rate):
        if source is not None and hasattr(source, "storage_root_id"):
            value = getattr(source, "storage_root_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "CAPACITY_EVIDENCE_IDENTITY_DOMAIN",
    "PRESSURE_DECISION_IDENTITY_DOMAIN",
    "PRESSURE_DECISION_UNAVAILABLE_IDENTITY_DOMAIN",
    "PRESSURE_POLICY_DEFINITION_V1_VERSION",
    "PRESSURE_POLICY_IDENTITY_DOMAIN",
    "RATE_EVIDENCE_IDENTITY_DOMAIN",
    "PressureDecision",
    "PressureDecisionUnavailable",
    "PressureDecisionUnavailableReason",
    "PressurePolicyDefinition",
    "PressurePolicyError",
    "PressureRestrictions",
    "PressureState",
    "TimeToFullEstimate",
    "TimeToFullKind",
    "WriteRateObservation",
    "evaluate_pressure",
    "restrictions_for_state",
]
