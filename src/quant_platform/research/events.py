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
  matching this codebase's E02/E04 modules).  It optionally accepts a
  sealed E04 ``FeatureArtifact`` as durable provenance for the supplied
  observations; when given one, it refuses anything that is not a genuine
  sealed ``FeatureArtifact`` instance (E04's private-construction guard
  makes an "unsealed" instance of that type impossible to hold in the
  first place), refuses an artifact that does not actually seal
  evidence for ``spec.observable_id``, refuses any observation not sealed
  by that artifact, and refuses any observation whose ``event_time`` falls
  outside ``artifact.declared_materialized_support``.
- A detected event's ``event_time`` is extracted from canonical observation
  support semantics (the observation's support coordinate, such as a market
  bucket/bar closing instant).  Opaque or unparseable support coordinates fail
  closed (``EventDetectionError``) rather than falling back to causal availability.
  ``event_time`` is never permitted to be later than ``causal_available_at``
  (fail closed; non-anticipation).
- A detected event's ``causal_available_at`` is the later of the
  triggering observation's own ``causal_available_at`` and (when present)
  its ``observed_finalized_at``.  E02 guarantees
  ``observed_finalized_at >= causal_available_at`` whenever the former is
  supplied, but never guarantees equality: a bar that closed at 10:00 can
  still be finalized at 10:05 (a correction/reconciliation window), and an
  event detected from it must not be treated as legally available before
  10:05 just because its bucket closed at 10:00.  Using
  ``observation.causal_available_at`` alone here -- as an earlier revision
  of this module did -- would leak: it could make a `DetectedEvent`
  causally available before the evidence that produced it was actually
  proven FINAL.  When ``observed_finalized_at`` is absent (nullable,
  ``nullable_observed_time_never_fabricated``), the structural floor is
  the only real evidence available and is used unchanged -- never
  fabricated forward.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, ClassVar

from ..features import (
    FeatureArtifact,
    FeatureArtifactError,
    FeatureArtifactLifecycle,
    FeatureDefinitionId,
    FeatureObservation,
    Instant,
    ObservationLifecycle,
)
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


def _stable_match_evidence(value: Any) -> MappingProxyType[str, Any]:
    """Canonicalize caller-supplied evidence into a genuinely immutable
    ``Mapping`` (`types.MappingProxyType`), so ``event.match_evidence["value"]``
    works directly as the public value shape -- not a tuple of pairs.  Keys
    are normalized and stored in sorted order so two mappings built from the
    same evidence in a different key order compare/serialize identically.
    """

    if not isinstance(value, Mapping):
        raise EventSpecError("match_evidence must be a mapping")
    items: dict[str, Any] = {}
    for key, item in value.items():
        items[_non_empty_text(key, "match_evidence key")] = item
    return MappingProxyType(dict(sorted(items.items())))


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
    match_evidence: Mapping[str, Any]

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


def _require_sealed_artifact_covers_observable(artifact: Any, observable_id: str) -> None:
    """Fail closed unless ``artifact`` is a genuine sealed E04 `FeatureArtifact`
    that actually seals evidence for ``observable_id``.

    E04's private-construction guard (`_SEAL_TOKEN`) makes it impossible to
    hold an "unsealed" `FeatureArtifact` instance at all -- only
    `seal_feature_artifact()` (after its FINAL-only proof gate) or
    `rehydrate_feature_artifact()` (trusted catalog reconstruction of an
    already-sealed record) can produce one.  So refusing anything that is
    not literally an instance of that type *is* the refusal of unsealed
    artifacts this function performs; a caller cannot construct a
    counterfeit one to bypass it.
    """

    if not isinstance(artifact, FeatureArtifact):
        raise EventDetectionError("artifact must be a sealed FeatureArtifact")
    if artifact.lifecycle != FeatureArtifactLifecycle.FINAL:
        raise EventDetectionError("artifact must be FINAL-sealed")  # pragma: no cover - E04 already guarantees this
    try:
        artifact.output_contract_for(observable_id)
    except FeatureArtifactError as exc:
        raise EventDetectionError(
            f"artifact does not seal any evidence for observable {observable_id}"
        ) from exc


_COORDINATE_TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z)"
)


def _extract_event_time(observation: FeatureObservation) -> Instant:
    """Extract canonical event instant from observation support semantics.

    Pulls the bucket/bar timestamp from the observation's support coordinate
    (e.g. ``"bar:2024-01-01T00:05:00Z"`` -> ``2024-01-01T00:05:00Z``).

    Fails closed (EventDetectionError) if the support coordinate does not embed
    a valid RFC 3339 timestamp (event time must not fall back to causal
    availability; support/bucket time is independent of availability).

    Fails closed if the extracted timestamp is later than
    ``observation.causal_available_at`` (non-anticipation: an event cannot
    occur after the moment its observation became causally available).
    """

    coord = observation.support_identity.observation_identity
    matches = _COORDINATE_TIMESTAMP_RE.findall(coord)
    if not matches:
        raise EventDetectionError(
            f"observation {observation.identity} support coordinate {coord!r} does not "
            "contain a valid canonical support timestamp (fail closed; event time cannot "
            "fall back to causal availability)"
        )
    for raw in reversed(matches):
        try:
            candidate = Instant.parse(raw)
        except Exception:
            continue
        if candidate <= observation.causal_available_at:
            return candidate
        raise EventDetectionError(
            f"observation {observation.identity} support coordinate timestamp "
            f"{candidate.isoformat()} is later than causal_available_at "
            f"{observation.causal_available_at.isoformat()}"
        )
    raise EventDetectionError(
        f"observation {observation.identity} support coordinate {coord!r} contains no parseable "
        "RFC 3339 timestamp"
    )


def _artifact_covers_event_time(artifact: FeatureArtifact, event_time: Instant) -> bool:
    """Verify that `event_time` falls within `artifact.declared_materialized_support`."""
    return any(
        interval.start <= event_time < interval.end
        for interval in artifact.declared_materialized_support.intervals
    )


def detect_events(
    spec: EventSpec,
    observations: Iterable[FeatureObservation] | FeatureArtifact,
    *,
    artifact: FeatureArtifact | None = None,
) -> list[DetectedEvent]:
    """Deterministically evaluate ``spec.predicate`` over a strictly
    time-ordered stream of FINAL ``FeatureObservation`` values bound to
    ``spec.observable_id``.

    Fails closed (`EventDetectionError`) on: a non-FINAL observation, an
    observation bound to a different ``FeatureDefinitionId``, observations
    supplied out of strictly non-decreasing ``causal_available_at`` order,
    or a duplicate observation identity in the same call -- rather than
    silently skipping evidence a caller may not have intended to omit.

    ``observations`` may be an iterable of `FeatureObservation` or a sealed
    `FeatureArtifact` directly.  When passed a `FeatureArtifact`, observations
    bound to ``spec.observable_id`` are consumed directly from its sealed evidence.

    ``artifact``, when supplied, is durable E04 provenance for
    ``observations``: it must be a genuine sealed `FeatureArtifact` that
    seals evidence for ``spec.observable_id`` (see
    `_require_sealed_artifact_covers_observable`), every observation must
    have been sealed by that artifact, and every observation's event_time
    must fall within the artifact's `declared_materialized_support`, or
    detection is refused before any event is emitted.

    Byte-identical results for byte-identical inputs: no field of a
    resulting `DetectedEvent` (including `event_id`) depends on anything
    but the supplied `spec`, `observations` and `artifact`.
    """

    if not isinstance(spec, EventSpec):
        raise EventDetectionError("spec must be EventSpec")

    if isinstance(observations, FeatureArtifact):
        if artifact is not None and artifact != observations:
            raise EventDetectionError("conflicting artifact arguments supplied to detect_events")
        artifact = observations
        observations = [
            obs for obs in artifact.sealed_observations
            if str(obs.definition_id) == spec.observable_id
        ]
        if not observations:
            raise EventDetectionError(
                f"artifact {artifact.identity} contains no sealed observations for observable {spec.observable_id}"
            )

    if artifact is not None:
        _require_sealed_artifact_covers_observable(artifact, spec.observable_id)

    sealed_by_id: dict[str, FeatureObservation] = {}
    if artifact is not None and artifact.sealed_observations:
        sealed_by_id = {obs.identity: obs for obs in artifact.sealed_observations}

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

        event_time = _extract_event_time(observation)
        if artifact is not None:
            if not _artifact_covers_event_time(artifact, event_time):
                raise EventDetectionError(
                    f"observation {observation.identity} at event_time {event_time.isoformat()} "
                    "falls outside artifact declared_materialized_support"
                )
            if (
                artifact.sealed_observation_identities
                and observation.identity not in artifact.sealed_observation_identities
            ):
                raise EventDetectionError(
                    f"observation {observation.identity} was not sealed by artifact {artifact.identity}"
                )
            if sealed_by_id:
                sealed_obs = sealed_by_id.get(observation.identity)
                if sealed_obs is None or sealed_obs != observation:
                    raise EventDetectionError(
                        f"observation {observation.identity} was not sealed by artifact {artifact.identity}"
                    )

        if not spec.predicate.evaluate(observation.value):
            continue

        causal_available_at = (
            observation.observed_finalized_at
            if observation.observed_finalized_at is not None
            and observation.observed_finalized_at > observation.causal_available_at
            else observation.causal_available_at
        )
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
