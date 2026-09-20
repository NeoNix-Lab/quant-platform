"""EventSpec v1 semantic model and causal event detection runtime.

This module owns the bounded F02 semantic foundation only: an immutable,
reproducible declaration of a conditional predicate over one canonical E02
observable (``EventSpec``), plus a deterministic, non-anticipating detection
function (``detect_events``) that evaluates that predicate against a
strictly time-ordered stream of FINAL ``FeatureObservation`` values and
emits traceable ``DetectedEvent`` records.  It does not:

- define forward outcomes/labels/censoring (F03 OutcomeSpec/Outcome);
- perform validation/purge/embargo (F06);
- simulate strategy/execution, fills, orders, stop losses or PnL (G/H
  spines) -- ADR-0007 keeps event research distinct from strategy
  backtesting;
- compute retrospective/lookahead statistics (e.g. global sample
  percentiles or rolling quantiles spanning future test folds) -- an
  ``EventSpec`` predicate threshold is always a fixed value declared at
  spec-construction time, never computed from the data being scanned;
- open DataGateway, files or PostgreSQL (``quant_platform.research``
  depends only on ``feature`` and ``shared`` -- see
  ``tests/test_package_boundaries_v1.py``);
- introduce a generic expression parser or runtime DSL: the only supported
  predicate shape is a single fixed-threshold comparison against one
  observable's value, optionally combined with a sign/direction
  requirement.

Design notes (frozen for this PR only -- not governance-authoritative; a
later dedicated governance pass materializes whichever of these become the
accepted ADR text):

- ``EventSpec`` semantic identity is exactly the tuple (event_key,
  semantic_version, hypothesis_id, observable_id, predicate).  No other
  field participates.
- ``hypothesis_id`` and ``observable_id`` are validated as syntactically
  genuine ``HypothesisSpecId`` / ``FeatureDefinitionId`` v1 identities
  (F01/E02 own those identity domains; F02 never redefines or re-derives
  them, only binds to them).
- ``detect_events`` requires every supplied observation's
  ``definition_id`` to equal the ``EventSpec.observable_id`` it is bound
  to, requires strictly non-decreasing ``causal_available_at`` order, and
  refuses any non-FINAL observation or duplicate observation identity --
  all fail closed rather than silently skip (adversarial-vector style,
  matching this codebase's E02/E04 modules).
- A detected event's ``causal_available_at`` is set to exactly the
  triggering observation's own ``causal_available_at`` (the tightest legal
  value that still satisfies "never precede the latest causal_available_at
  of its required feature observations", since v1 introduces no additional
  detection-side latency).
- A detected event's ``event_time`` is set to exactly the triggering
  observation's own ``causal_available_at``.  E02's frozen contract
  exposes no separate, narrower "market bucket closed" instant: per
  ``FeatureAvailabilitySemantics.causal_floor_rule ==
  "after_required_support_available"``, ``causal_available_at`` already
  *is* the moment the required support closed, for any `FeatureDefinition`
  that adds no additional publication latency of its own.  The only other
  candidate field, ``observed_available_at``, is guaranteed by E02's own
  validation to be greater than or equal to ``causal_available_at`` (it
  can never precede the causal floor), so using it as ``event_time`` would
  silently invert which of the two fields is earlier.  Reusing
  ``causal_available_at`` for both fields in v1 is therefore the honest,
  non-fabricated answer rather than an arbitrary collapse -- and remains
  forward-compatible: a future revision modeling per-event additional
  detection latency can widen ``causal_available_at`` beyond
  ``event_time`` without changing this module's identity/evaluation
  contracts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, ClassVar

from ..data.models import Instant
from ..features import FeatureDefinitionId, FeatureObservation, ObservationLifecycle
from .hypothesis import HypothesisSpecError, HypothesisSpecId


EVENT_SPEC_IDENTITY_DOMAIN = "event-spec-v1"
EVENT_SPEC_MODEL_VERSION = "1"
DETECTED_EVENT_IDENTITY_DOMAIN = "detected-event-v1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")


class EventSpecError(ValueError):
    """An EventSpec v1 semantic value violates the frozen contract."""


class EventDetectionError(ValueError):
    """`detect_events` was supplied evidence that would violate causal
    non-anticipation, FINAL-only eligibility, or strict temporal order."""


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventSpecError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise EventSpecError(f"{field_name} must not contain control characters")
    return text


def _governed_key(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise EventSpecError(f"{field_name} must be a governed canonical key spelling")
    return text


def _semantic_version(value: Any, field_name: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field_name)
    if not _VERSION_RE.fullmatch(text):
        raise EventSpecError(f"{field_name} must be an explicit positive version")
    return text


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _to_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise EventSpecError(f"{field_name} must be numeric, not boolean")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise EventSpecError(f"{field_name} is not a valid numeric value: {value!r}") from exc
    else:
        raise EventSpecError(f"{field_name} has unsupported numeric type: {type(value).__name__}")
    if not result.is_finite():
        raise EventSpecError(f"{field_name} must be finite")
    return result


def _canonical_decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _validated_hypothesis_id(value: Any) -> str:
    text = _non_empty_text(value, "hypothesis_id")
    try:
        return str(HypothesisSpecId(text))
    except HypothesisSpecError as exc:
        raise EventSpecError(f"hypothesis_id must be a valid HypothesisSpecId: {exc}") from exc


def _validated_observable_id(value: Any) -> str:
    text = _non_empty_text(value, "observable_id")
    try:
        return str(FeatureDefinitionId(text))
    except ValueError as exc:
        raise EventSpecError(f"observable_id must be a valid FeatureDefinitionId: {exc}") from exc


# ---------------------------------------------------------------------------
# Predicate: fixed threshold comparison with optional direction matching.
# ---------------------------------------------------------------------------


class ComparisonOperator(StrEnum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"


class DirectionRequirement(StrEnum):
    ANY = "any"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NON_NEGATIVE = "non_negative"
    NON_POSITIVE = "non_positive"


_COMPARISONS = {
    ComparisonOperator.GREATER_THAN: lambda value, threshold: value > threshold,
    ComparisonOperator.GREATER_THAN_OR_EQUAL: lambda value, threshold: value >= threshold,
    ComparisonOperator.LESS_THAN: lambda value, threshold: value < threshold,
    ComparisonOperator.LESS_THAN_OR_EQUAL: lambda value, threshold: value <= threshold,
    ComparisonOperator.EQUAL: lambda value, threshold: value == threshold,
    ComparisonOperator.NOT_EQUAL: lambda value, threshold: value != threshold,
}

_DIRECTION_CHECKS = {
    DirectionRequirement.ANY: lambda value: True,
    DirectionRequirement.POSITIVE: lambda value: value > 0,
    DirectionRequirement.NEGATIVE: lambda value: value < 0,
    DirectionRequirement.NON_NEGATIVE: lambda value: value >= 0,
    DirectionRequirement.NON_POSITIVE: lambda value: value <= 0,
}


@dataclass(frozen=True, slots=True)
class ThresholdPredicate:
    """A single deterministic, fixed-threshold comparison rule.

    ``threshold`` is a value declared once at spec-construction time -- it
    is never computed from the observation stream being scanned, which is
    what keeps this free of retrospective/lookahead statistics (e.g. a
    rolling or global sample percentile).
    """

    operator: ComparisonOperator
    threshold: Decimal | int | str
    direction: DirectionRequirement = DirectionRequirement.ANY

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator", _enum(ComparisonOperator, self.operator, "operator"))
        object.__setattr__(self, "direction", _enum(DirectionRequirement, self.direction, "direction"))
        threshold = _to_decimal(self.threshold, "threshold")
        object.__setattr__(self, "threshold", _canonical_decimal_text(threshold))

    def evaluate(self, value: Any) -> bool:
        observed = _to_decimal(value, "observation value")
        threshold = Decimal(self.threshold)
        if not _COMPARISONS[self.operator](observed, threshold):
            return False
        return _DIRECTION_CHECKS[self.direction](observed)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": "threshold",
            "operator": self.operator.value,
            "threshold": self.threshold,
            "direction": self.direction.value,
        }


def _enum(enum_type: Any, value: Any, field_name: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise EventSpecError(f"{field_name} is not a supported v1 value") from exc


# ---------------------------------------------------------------------------
# EventSpec identity and value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventSpecId:
    """Deterministic content-derived EventSpec identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "EventSpecId")
        prefix = f"{EVENT_SPEC_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or not re.fullmatch(
            r"[0-9a-f]{64}", text.removeprefix(prefix)
        ):
            raise EventSpecError("EventSpecId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EventSpecId":
        return cls(f"{EVENT_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EventSpec:
    """Immutable, reproducible declaration of a conditional predicate over
    one canonical E02 observable, bound to an F01 hypothesis.

    ``EventSpec`` makes no claim about forward outcomes (F03), validation
    (F06) or trading (Strategy/Execution).  It binds exactly one
    ``observable_id`` -- the target ``FeatureDefinitionId`` the predicate
    evaluates -- and exactly one ``hypothesis_id`` this event operationally
    serves as detection evidence for.
    """

    event_key: str
    semantic_version: str | int
    hypothesis_id: str
    observable_id: str
    predicate: ThresholdPredicate

    identity_type: ClassVar[str] = "event-spec"
    identity_version: ClassVar[str] = EVENT_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_key", _governed_key(self.event_key, "event_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        object.__setattr__(self, "hypothesis_id", _validated_hypothesis_id(self.hypothesis_id))
        object.__setattr__(self, "observable_id", _validated_observable_id(self.observable_id))
        if not isinstance(self.predicate, ThresholdPredicate):
            raise EventSpecError("predicate must be ThresholdPredicate")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "event_key": self.event_key,
            "semantic_version": self.semantic_version,
            "hypothesis_id": self.hypothesis_id,
            "observable_id": self.observable_id,
            "predicate": self.predicate.stable_dict(),
        }

    @property
    def canonical_utf8_serialization(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def spec_id(self) -> EventSpecId:
        return EventSpecId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.spec_id)

    def stable_dict(self) -> dict[str, Any]:
        return {"spec_id": self.identity, "canonical_payload": self.canonical_payload()}


# ---------------------------------------------------------------------------
# DetectedEvent value model.
# ---------------------------------------------------------------------------


def _stable_match_evidence(value: Any) -> tuple[tuple[str, Any], ...]:
    """Canonicalize caller-supplied evidence into a hashable, order-independent
    tuple of pairs -- the same idiom this codebase already uses for
    ``NumericalEquivalence.parameters`` (`quant_platform.features.definitions`)
    to keep a frozen dataclass's auto-generated `__hash__`/`__eq__` well
    defined without a second, unbounded-shape immutable-mapping type."""

    if not isinstance(value, Mapping):
        raise EventSpecError("match_evidence must be a mapping")
    items: dict[str, Any] = {}
    for key, item in value.items():
        text_key = _non_empty_text(key, "match_evidence key")
        try:
            hash(item)
        except TypeError as exc:
            raise EventSpecError(f"match_evidence[{text_key!r}] must be a hashable value") from exc
        items[text_key] = item
    return tuple(sorted(items.items()))


@dataclass(frozen=True, slots=True)
class DetectedEvent:
    """One traceable occurrence where an `EventSpec` predicate was
    satisfied by a FINAL feature observation.

    ``event_id`` is a computed property, deterministically derived from
    the owning ``event_spec_id``, ``observable_id``, the triggering
    observation's own identity, and this event's own ``event_time`` /
    ``causal_available_at`` -- never from ``match_evidence``, so
    re-serializing identical evidence in a different key order never
    changes identity.
    """

    event_spec_id: str
    observable_id: str
    observation_identity: str
    event_time: Instant | str
    causal_available_at: Instant | str
    match_evidence: Mapping[str, Any] | tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_spec_id", str(EventSpecId(_non_empty_text(self.event_spec_id, "event_spec_id")))
        )
        object.__setattr__(self, "observable_id", _validated_observable_id(self.observable_id))
        object.__setattr__(
            self,
            "observation_identity",
            _non_empty_text(self.observation_identity, "observation_identity"),
        )
        object.__setattr__(self, "event_time", Instant.parse(self.event_time))
        object.__setattr__(self, "causal_available_at", Instant.parse(self.causal_available_at))
        if self.event_time > self.causal_available_at:
            raise EventSpecError(
                "event_time cannot be later than causal_available_at: a detected event "
                "can never be legally consumable before the moment it occurred"
            )
        object.__setattr__(self, "match_evidence", _stable_match_evidence(self.match_evidence))

    @property
    def event_id(self) -> str:
        payload = {
            "identity_domain": DETECTED_EVENT_IDENTITY_DOMAIN,
            "event_spec_id": self.event_spec_id,
            "observable_id": self.observable_id,
            "observation_identity": self.observation_identity,
            "event_time": self.event_time.isoformat(),
            "causal_available_at": self.causal_available_at.isoformat(),
        }
        return f"{DETECTED_EVENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_spec_id": self.event_spec_id,
            "observable_id": self.observable_id,
            "observation_identity": self.observation_identity,
            "event_time": self.event_time.isoformat(),
            "causal_available_at": self.causal_available_at.isoformat(),
            "match_evidence": dict(self.match_evidence),
        }


# ---------------------------------------------------------------------------
# Causal event detection.
# ---------------------------------------------------------------------------


def detect_events(spec: EventSpec, observations: Iterable[FeatureObservation]) -> list[DetectedEvent]:
    """Deterministically evaluate ``spec.predicate`` over a strictly
    time-ordered stream of FINAL ``FeatureObservation`` values bound to
    ``spec.observable_id``.

    Fails closed (`EventDetectionError`) on: a non-FINAL observation, an
    observation bound to a different ``FeatureDefinitionId``, observations
    supplied out of strictly non-decreasing ``causal_available_at`` order,
    or a duplicate observation identity in the same call -- rather than
    silently skipping evidence a caller may not have intended to omit.

    Byte-identical results for byte-identical inputs: no field of a
    resulting `DetectedEvent` (including `event_id`) depends on anything
    but the supplied `spec` and `observations`.
    """

    if not isinstance(spec, EventSpec):
        raise EventDetectionError("spec must be EventSpec")

    events: list[DetectedEvent] = []
    seen_identities: set[str] = set()
    previous_causal_available_at: Instant | None = None

    for observation in observations:
        if not isinstance(observation, FeatureObservation):
            raise EventDetectionError("observations must contain only FeatureObservation values")
        if str(observation.definition_id) != spec.observable_id:
            raise EventDetectionError(
                f"observation {observation.identity} is bound to {observation.definition_id}, "
                f"not the EventSpec observable {spec.observable_id}"
            )
        if observation.lifecycle != ObservationLifecycle.FINAL:
            raise EventDetectionError(
                f"non-FINAL observation {observation.identity} cannot be evaluated by "
                "detect_events (FINAL-only)"
            )
        if observation.identity in seen_identities:
            raise EventDetectionError(f"duplicate observation identity supplied: {observation.identity}")
        seen_identities.add(observation.identity)
        if (
            previous_causal_available_at is not None
            and observation.causal_available_at < previous_causal_available_at
        ):
            raise EventDetectionError(
                "observations must be supplied in strictly non-decreasing causal_available_at order"
            )
        previous_causal_available_at = observation.causal_available_at

        if not spec.predicate.evaluate(observation.value):
            continue

        causal_available_at = observation.causal_available_at
        event_time = causal_available_at
        match_evidence = {
            "value": observation.value,
            "operator": spec.predicate.operator.value,
            "threshold": spec.predicate.threshold,
            "direction": spec.predicate.direction.value,
        }
        events.append(
            DetectedEvent(
                event_spec_id=spec.identity,
                observable_id=spec.observable_id,
                observation_identity=observation.identity,
                event_time=event_time,
                causal_available_at=causal_available_at,
                match_evidence=match_evidence,
            )
        )

    return events


__all__ = [
    "DETECTED_EVENT_IDENTITY_DOMAIN",
    "EVENT_SPEC_IDENTITY_DOMAIN",
    "EVENT_SPEC_MODEL_VERSION",
    "ComparisonOperator",
    "DetectedEvent",
    "DirectionRequirement",
    "EventDetectionError",
    "EventSpec",
    "EventSpecError",
    "EventSpecId",
    "ThresholdPredicate",
    "detect_events",
]
