"""OutcomeSpec v1 semantic model and forward outcome measurement runtime.

This module owns the bounded F03 semantic foundation only: an immutable,
reproducible declaration of a forward evaluation horizon and price
observation rule (``OutcomeSpec``), plus a deterministic, non-anticipating
evaluation function (``evaluate_outcome``) that walks a strictly
time-ordered forward market path relative to a detected event and emits a
traceable ``Outcome`` record.  It does not:

- classify outcomes into discrete trade labels or triple-barrier states
  (F07 Label Engine);
- assign or simulate a trading policy, position sizing, transaction costs,
  slippage or order execution (Strategy/Execution G/H spines) -- ADR-0007
  keeps forward outcome measurement distinct from strategy backtesting;
- perform validation/purge/embargo (F06);
- open DataGateway, files or PostgreSQL (``quant_platform.research`` depends
  only on ``feature`` and ``shared`` -- see
  ``tests/test_package_boundaries_v1.py``);
- consume the ``representation`` layer's ``CandleRecord`` directly: the
  ``research`` package owner is not permitted to depend on
  ``representation`` (package boundary), so this module defines its own
  minimal ``MarketObservation`` projection.  Callers already holding
  canonical D02/D03 candle data project it into that shape before calling
  ``evaluate_outcome``.

Design notes (frozen for this PR only -- not governance-authoritative; a
later dedicated governance pass materializes whichever of these become the
accepted ADR text):

- ``OutcomeSpec`` semantic identity is exactly the tuple (outcome_key,
  semantic_version, horizon_duration, metric_kind, price_reference).  No
  other field participates.
- ``horizon_duration`` accepts either a governed unit-suffixed duration
  spelling (``5m``, ``15m``, ``1h``, ...; identical unit vocabulary to
  ``CandleDefinitionV1.duration_ns``) or a plain positive Python ``int``
  bar count.  The two shapes are semantically distinct (wall-clock horizon
  vs. a fixed count of forward observations) and are canonicalized to
  ``"<amount>ns"`` or ``"<amount>bars"`` respectively, so equivalent
  spellings (e.g. ``"1m"`` and ``"60s"``) collapse to one identity while a
  bar-count horizon never collides with a duration horizon.
- An ``Outcome``'s ``horizon_start`` is always the triggering event's own
  ``event_time`` -- the outcome measures the path forward from the moment
  the event occurred, never from when it became knowable.
- For a duration horizon, ``horizon_end`` is computed purely from
  ``spec`` and ``event`` (``event_time + horizon_duration``); it never
  depends on the supplied market data.  For a bar-count horizon,
  ``horizon_end`` is the instant of the Nth forward observation once N
  forward observations have been consumed; when fewer are available before
  the stream ends, the last actually-consumed instant (or the anchor's own
  instant, if none were consumed) stands in as the honest boundary of
  available evidence -- never a fabricated projection.
- ``evaluate_outcome`` requires the first supplied ``MarketObservation`` to
  be the anchor: its ``instant`` must equal ``event.event_time`` exactly.
  When the supplied series does not even start at the event's own instant
  (including an empty series), the outcome is marked
  ``INSUFFICIENT_COVERAGE`` -- a distinct failure mode from running out of
  forward data mid-horizon (``CENSORED_END_OF_DATA``), because the
  evaluation cannot even establish a starting reference price.
- ``causal_available_at`` is always the later of ``horizon_end`` and the
  boundary/last-seen observation's own ``causal_available_at`` (which
  itself defaults to the observation's ``instant`` when the caller supplies
  no independent finalization evidence -- mirroring F02's
  ``nullable_observed_time_never_fabricated`` floor).  This guarantees the
  ADR-0006 invariant ``causal_available_at >= horizon_end`` unconditionally,
  enforced structurally in ``Outcome.__post_init__`` rather than merely by
  convention in the evaluator.
- A non-``COMPLETE`` outcome never carries a ``realized_value``: censored or
  insufficient-coverage outcomes fail closed rather than fabricate a price
  that was never observed.
- ``realized_value`` and ``path_metrics`` are exact canonical fraction
  strings (``"<numerator>"`` or ``"<numerator>/<denominator>"``, lowest
  terms, ``fractions.Fraction`` arithmetic over ``Decimal`` prices) so that
  outcome measurement never introduces float or rounding error -- the same
  exact-arithmetic discipline ``quant_platform.features.artifacts`` already
  applies to numerical equivalence checks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, ClassVar

from ..features import Instant
from .events import DetectedEvent


OUTCOME_SPEC_IDENTITY_DOMAIN = "outcome-spec-v1"
OUTCOME_SPEC_MODEL_VERSION = "1"
OUTCOME_IDENTITY_DOMAIN = "outcome-v1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")
_DURATION_RE = re.compile(r"^([0-9]+)(ns|us|ms|s|m|h|d)$")
_DURATION_UNITS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 3_600 * 1_000_000_000,
    "d": 86_400 * 1_000_000_000,
}
_CANONICAL_HORIZON_RE = re.compile(r"^([1-9][0-9]*)(ns|bars)$")
_FRACTION_TEXT_RE = re.compile(r"^-?(0|[1-9][0-9]*)(/[1-9][0-9]*)?$")


class OutcomeError(ValueError):
    """An OutcomeSpec/Outcome/PathMetrics v1 semantic value violates the
    frozen contract."""


class OutcomeEvaluationError(ValueError):
    """`evaluate_outcome` was supplied evidence that would violate causal
    non-anticipation, strict temporal order, or the required price shape."""


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise OutcomeError(f"{field_name} must not contain control characters")
    return text


def _governed_key(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise OutcomeError(f"{field_name} must be a governed canonical key spelling")
    return text


def _semantic_version(value: Any, field_name: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field_name)
    if not _VERSION_RE.fullmatch(text):
        raise OutcomeError(f"{field_name} must be an explicit positive version")
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
        raise OutcomeEvaluationError(f"{field_name} must be numeric, not boolean")
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
            raise OutcomeEvaluationError(f"{field_name} is not a valid numeric value: {value!r}") from exc
    else:
        raise OutcomeEvaluationError(f"{field_name} has unsupported numeric type: {type(value).__name__}")
    if not result.is_finite():
        raise OutcomeEvaluationError(f"{field_name} must be finite")
    return result


def _enum(enum_type: Any, value: Any, field_name: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise OutcomeError(f"{field_name} is not a supported v1 value") from exc


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _validated_fraction_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _FRACTION_TEXT_RE.fullmatch(value):
        raise OutcomeError(f"{field_name} must be an exact canonical fraction string, got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Horizon and metric vocabulary.
# ---------------------------------------------------------------------------


class HorizonKind(StrEnum):
    DURATION = "duration"
    BAR_COUNT = "bar_count"


class MetricKind(StrEnum):
    FORWARD_RETURN = "forward_return"
    HIGH_LOW_EXCURSION = "high_low_excursion"
    EXTREMA = "extrema"


class OutcomeState(StrEnum):
    COMPLETE = "complete"
    CENSORED_END_OF_DATA = "censored_end_of_data"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


def _parse_horizon_input(value: Any) -> tuple[HorizonKind, int]:
    """Parse a caller-supplied ``horizon_duration`` into its canonical
    ``(kind, amount)`` pair.  A Python ``int`` is always a bar count; a
    string must be a governed unit-suffixed duration spelling.  Bare
    numeric strings are rejected rather than guessed at, to keep the two
    horizon shapes unambiguous.
    """

    if isinstance(value, bool):
        raise OutcomeError("horizon_duration must be a duration string or a positive integer bar count")
    if isinstance(value, int):
        if value < 1:
            raise OutcomeError("horizon_duration bar count must be a positive integer")
        return HorizonKind.BAR_COUNT, value
    if isinstance(value, str) and value.strip():
        match = _DURATION_RE.fullmatch(value.strip())
        if match:
            amount = int(match.group(1))
            if amount < 1:
                raise OutcomeError("horizon_duration must be positive")
            return HorizonKind.DURATION, amount * _DURATION_UNITS[match.group(2)]
    raise OutcomeError(
        "horizon_duration must be a governed duration string (e.g. '5m', '15m', '1h') "
        f"or a positive integer bar count, got {value!r}"
    )


def _parse_canonical_horizon(text: str) -> tuple[HorizonKind, int]:
    match = _CANONICAL_HORIZON_RE.fullmatch(text)
    if not match:  # pragma: no cover - text always produced by __post_init__
        raise OutcomeError(f"malformed canonical horizon_duration: {text!r}")
    kind = HorizonKind.DURATION if match.group(2) == "ns" else HorizonKind.BAR_COUNT
    return kind, int(match.group(1))


# ---------------------------------------------------------------------------
# OutcomeSpec identity and value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutcomeSpecId:
    """Deterministic content-derived OutcomeSpec identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "OutcomeSpecId")
        prefix = f"{OUTCOME_SPEC_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", text.removeprefix(prefix)):
            raise OutcomeError("OutcomeSpecId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OutcomeSpecId":
        return cls(f"{OUTCOME_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OutcomeSpec:
    """Immutable, reproducible declaration of a forward evaluation horizon
    and price/return observation rule.

    ``OutcomeSpec`` makes no claim about how a value it produces should be
    classified into a label (F07) or traded (Strategy/Execution); it only
    fixes how far forward to look and how to summarize what happened.
    """

    outcome_key: str
    semantic_version: str | int
    horizon_duration: str | int
    metric_kind: str | MetricKind
    price_reference: str

    identity_type: ClassVar[str] = "outcome-spec"
    identity_version: ClassVar[str] = OUTCOME_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_key", _governed_key(self.outcome_key, "outcome_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        kind, amount = _parse_horizon_input(self.horizon_duration)
        canonical_horizon = f"{amount}ns" if kind is HorizonKind.DURATION else f"{amount}bars"
        object.__setattr__(self, "horizon_duration", canonical_horizon)
        object.__setattr__(self, "metric_kind", _enum(MetricKind, self.metric_kind, "metric_kind"))
        object.__setattr__(self, "price_reference", _governed_key(self.price_reference, "price_reference"))

    @property
    def horizon_kind(self) -> HorizonKind:
        return _parse_canonical_horizon(self.horizon_duration)[0]

    @property
    def horizon_amount(self) -> int:
        return _parse_canonical_horizon(self.horizon_duration)[1]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "outcome_key": self.outcome_key,
            "semantic_version": self.semantic_version,
            "horizon_duration": self.horizon_duration,
            "metric_kind": self.metric_kind.value,
            "price_reference": self.price_reference,
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
    def spec_id(self) -> OutcomeSpecId:
        return OutcomeSpecId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.spec_id)

    def stable_dict(self) -> dict[str, Any]:
        return {"spec_id": self.identity, "canonical_payload": self.canonical_payload()}


# ---------------------------------------------------------------------------
# Market path projection consumed by the evaluator.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """One forward market observation available to outcome evaluation.

    Deliberately independent of the ``representation`` layer's
    ``CandleRecord`` (a different package owner the ``research`` package is
    not permitted to depend on -- see
    ``tests/test_package_boundaries_v1.py``).  Callers project whichever
    canonical candle/price representation they hold into this minimal
    shape before calling ``evaluate_outcome``.

    ``causal_available_at`` defaults to ``instant`` when not supplied
    (nullable, never fabricated forward), mirroring F02's floor for a
    finalization time that may not be independently known.
    """

    instant: Instant | str
    prices: Mapping[str, Any]
    causal_available_at: Instant | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "instant", Instant.parse(self.instant))
        if not isinstance(self.prices, Mapping) or not self.prices:
            raise OutcomeEvaluationError("MarketObservation.prices must be a non-empty mapping")
        normalized: dict[str, Decimal] = {}
        for key, value in self.prices.items():
            if not isinstance(key, str) or not _GOVERNED_KEY_RE.fullmatch(key):
                raise OutcomeEvaluationError(f"MarketObservation price field name must be governed: {key!r}")
            normalized[key] = _to_decimal(value, f"price field {key!r}")
        object.__setattr__(self, "prices", MappingProxyType(normalized))
        causal_source = self.causal_available_at if self.causal_available_at is not None else self.instant
        object.__setattr__(self, "causal_available_at", Instant.parse(causal_source))
        if self.causal_available_at < self.instant:
            raise OutcomeEvaluationError(
                "MarketObservation causal_available_at cannot precede its own instant"
            )

    def price_for(self, field_name: str) -> Decimal:
        if field_name not in self.prices:
            raise OutcomeEvaluationError(
                f"market observation at {self.instant.isoformat()} is missing price field {field_name!r}"
            )
        return self.prices[field_name]


# ---------------------------------------------------------------------------
# Outcome value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PathMetrics:
    """Immutable summary of the realized forward path, independent of
    ``metric_kind``: the best and worst returns (relative to the anchor
    price at ``price_reference``) observed anywhere along the consumed
    path."""

    maximum_favorable_excursion: str
    maximum_adverse_excursion: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "maximum_favorable_excursion",
            _validated_fraction_text(self.maximum_favorable_excursion, "maximum_favorable_excursion"),
        )
        object.__setattr__(
            self,
            "maximum_adverse_excursion",
            _validated_fraction_text(self.maximum_adverse_excursion, "maximum_adverse_excursion"),
        )

    def stable_dict(self) -> dict[str, str]:
        return {
            "maximum_favorable_excursion": self.maximum_favorable_excursion,
            "maximum_adverse_excursion": self.maximum_adverse_excursion,
        }


@dataclass(frozen=True, slots=True)
class Outcome:
    """One traceable forward-path measurement anchored to a `DetectedEvent`.

    ``outcome_id`` is a computed property, deterministically derived from
    ``outcome_spec_id``, ``event_id`` and this outcome's own horizon
    bounds -- never from ``realized_value`` or ``path_metrics``, so two
    evaluations that agree on spec, event and horizon share one identity
    regardless of how the underlying market path was supplied.

    The temporal availability invariant (``causal_available_at >=
    horizon_end``) and the no-fabrication invariant (a non-``COMPLETE``
    state never carries a ``realized_value``) are enforced here, not merely
    by convention in ``evaluate_outcome``: no code path can construct an
    ``Outcome`` that violates either.
    """

    outcome_spec_id: str
    event_id: str
    horizon_start: Instant | str
    horizon_end: Instant | str
    causal_available_at: Instant | str
    state: OutcomeState | str
    realized_value: str | None
    path_metrics: PathMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_spec_id", str(OutcomeSpecId(_non_empty_text(self.outcome_spec_id, "outcome_spec_id")))
        )
        object.__setattr__(self, "event_id", _non_empty_text(self.event_id, "event_id"))
        object.__setattr__(self, "horizon_start", Instant.parse(self.horizon_start))
        object.__setattr__(self, "horizon_end", Instant.parse(self.horizon_end))
        if self.horizon_end < self.horizon_start:
            raise OutcomeError("horizon_end cannot precede horizon_start")
        object.__setattr__(self, "causal_available_at", Instant.parse(self.causal_available_at))
        if self.causal_available_at < self.horizon_end:
            raise OutcomeError(
                "causal_available_at cannot be earlier than horizon_end: an outcome can never "
                "be legally consumable before its forward horizon has fully elapsed"
            )
        object.__setattr__(self, "state", _enum(OutcomeState, self.state, "state"))
        if self.state is OutcomeState.COMPLETE:
            if self.realized_value is None:
                raise OutcomeError("a COMPLETE outcome must carry a realized_value")
            object.__setattr__(self, "realized_value", _validated_fraction_text(self.realized_value, "realized_value"))
        elif self.realized_value is not None:
            raise OutcomeError(
                f"a {OutcomeState(self.state).value} outcome must not fabricate a realized_value"
            )
        if self.path_metrics is not None and not isinstance(self.path_metrics, PathMetrics):
            raise OutcomeError("path_metrics must be PathMetrics or None")

    @property
    def outcome_id(self) -> str:
        payload = {
            "identity_domain": OUTCOME_IDENTITY_DOMAIN,
            "outcome_spec_id": self.outcome_spec_id,
            "event_id": self.event_id,
            "horizon_start": self.horizon_start.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
        }
        return f"{OUTCOME_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "outcome_spec_id": self.outcome_spec_id,
            "event_id": self.event_id,
            "horizon_start": self.horizon_start.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
            "causal_available_at": self.causal_available_at.isoformat(),
            "state": self.state.value,
            "realized_value": self.realized_value,
            "path_metrics": self.path_metrics.stable_dict() if self.path_metrics is not None else None,
        }


# ---------------------------------------------------------------------------
# Forward outcome evaluation.
# ---------------------------------------------------------------------------


def _return_fraction(start: Decimal, end: Decimal) -> Fraction:
    if start == 0:
        raise OutcomeEvaluationError("cannot compute a forward return against a zero anchor price")
    return Fraction(end) / Fraction(start) - 1


def _compute_metric(spec: OutcomeSpec, anchor: MarketObservation, boundary: MarketObservation,
                     path: list[MarketObservation]) -> str:
    start = anchor.price_for(spec.price_reference)
    if spec.metric_kind is MetricKind.FORWARD_RETURN:
        end = boundary.price_for(spec.price_reference)
        return _fraction_text(_return_fraction(start, end))
    if spec.metric_kind is MetricKind.HIGH_LOW_EXCURSION:
        highs = [observation.price_for("high") for observation in path]
        lows = [observation.price_for("low") for observation in path]
        if start == 0:
            raise OutcomeEvaluationError("cannot compute a forward excursion against a zero anchor price")
        excursion = Fraction(max(highs) - min(lows)) / Fraction(start)
        return _fraction_text(excursion)
    if spec.metric_kind is MetricKind.EXTREMA:
        returns = [_return_fraction(start, observation.price_for(spec.price_reference)) for observation in path]
        extreme = max(returns, key=abs)
        return _fraction_text(extreme)
    raise AssertionError(f"unhandled metric_kind: {spec.metric_kind}")  # pragma: no cover


def _path_metrics(spec: OutcomeSpec, anchor: MarketObservation, path: list[MarketObservation]) -> PathMetrics | None:
    if not path:
        return None
    start = anchor.price_for(spec.price_reference)
    if start == 0:
        raise OutcomeEvaluationError("cannot compute path metrics against a zero anchor price")
    returns = [_return_fraction(start, observation.price_for(spec.price_reference)) for observation in path]
    return PathMetrics(
        maximum_favorable_excursion=_fraction_text(max(returns)),
        maximum_adverse_excursion=_fraction_text(min(returns)),
    )


def evaluate_outcome(
    spec: OutcomeSpec,
    event: DetectedEvent,
    market_series: Iterable[MarketObservation],
) -> Outcome:
    """Deterministically measure ``spec``'s forward horizon/metric relative
    to ``event`` over a strictly time-ordered ``market_series``.

    Fails closed (`OutcomeEvaluationError`) on a non-`MarketObservation`
    element or observations supplied out of strict increasing ``instant``
    order -- rather than silently reordering or deduplicating evidence a
    caller may not have intended to supply that way.

    The first element of ``market_series`` must be the anchor: its
    ``instant`` must equal ``event.event_time`` exactly.  When it is not
    (including an empty series), the result is `OutcomeState.INSUFFICIENT_COVERAGE`:
    there is no starting reference price to measure a forward return
    against.

    For a duration horizon, forward observations are consumed until one is
    found at or after ``horizon_end`` (computed purely from ``spec`` and
    ``event``, independent of the data); that observation is the boundary.
    For a bar-count horizon, the boundary is the Nth forward observation.
    If the series ends before a boundary is reached, the result is
    `OutcomeState.CENSORED_END_OF_DATA` with `realized_value = None` --
    never a fabricated price.

    `Outcome.causal_available_at` is never earlier than `Outcome.horizon_end`
    (enforced structurally by `Outcome.__post_init__`), forbidding lookahead.
    """

    if not isinstance(spec, OutcomeSpec):
        raise OutcomeEvaluationError("spec must be OutcomeSpec")
    if not isinstance(event, DetectedEvent):
        raise OutcomeEvaluationError("event must be DetectedEvent")

    observations: list[MarketObservation] = []
    previous_instant: Instant | None = None
    for observation in market_series:
        if not isinstance(observation, MarketObservation):
            raise OutcomeEvaluationError("market_series must contain only MarketObservation values")
        if previous_instant is not None and observation.instant <= previous_instant:
            raise OutcomeEvaluationError(
                "market_series must be supplied in strictly increasing instant order"
            )
        previous_instant = observation.instant
        observations.append(observation)

    if not observations or observations[0].instant != event.event_time:
        horizon_end = (
            Instant(event.event_time.epoch_ns + spec.horizon_amount)
            if spec.horizon_kind is HorizonKind.DURATION
            else event.event_time
        )
        causal_available_at = horizon_end if horizon_end > event.causal_available_at else event.causal_available_at
        return Outcome(
            outcome_spec_id=spec.identity,
            event_id=event.event_id,
            horizon_start=event.event_time,
            horizon_end=horizon_end,
            causal_available_at=causal_available_at,
            state=OutcomeState.INSUFFICIENT_COVERAGE,
            realized_value=None,
            path_metrics=None,
        )

    anchor = observations[0]
    forward = observations[1:]

    if spec.horizon_kind is HorizonKind.DURATION:
        horizon_end = Instant(event.event_time.epoch_ns + spec.horizon_amount)
        consumed: list[MarketObservation] = []
        boundary: MarketObservation | None = None
        for observation in forward:
            if observation.instant < horizon_end:
                consumed.append(observation)
                continue
            boundary = observation
            break
    else:
        count = spec.horizon_amount
        if len(forward) >= count:
            consumed = forward[: count - 1]
            boundary = forward[count - 1]
            horizon_end = boundary.instant
        else:
            consumed = forward
            boundary = None
            horizon_end = consumed[-1].instant if consumed else anchor.instant

    if boundary is None:
        last_seen = consumed[-1] if consumed else anchor
        causal_available_at = (
            horizon_end if horizon_end > last_seen.causal_available_at else last_seen.causal_available_at
        )
        return Outcome(
            outcome_spec_id=spec.identity,
            event_id=event.event_id,
            horizon_start=event.event_time,
            horizon_end=horizon_end,
            causal_available_at=causal_available_at,
            state=OutcomeState.CENSORED_END_OF_DATA,
            realized_value=None,
            path_metrics=_path_metrics(spec, anchor, consumed),
        )

    realized_path = consumed + [boundary]
    realized_value = _compute_metric(spec, anchor, boundary, realized_path)
    causal_available_at = (
        horizon_end if horizon_end > boundary.causal_available_at else boundary.causal_available_at
    )
    return Outcome(
        outcome_spec_id=spec.identity,
        event_id=event.event_id,
        horizon_start=event.event_time,
        horizon_end=horizon_end,
        causal_available_at=causal_available_at,
        state=OutcomeState.COMPLETE,
        realized_value=realized_value,
        path_metrics=_path_metrics(spec, anchor, realized_path),
    )


__all__ = [
    "OUTCOME_IDENTITY_DOMAIN",
    "OUTCOME_SPEC_IDENTITY_DOMAIN",
    "OUTCOME_SPEC_MODEL_VERSION",
    "HorizonKind",
    "MarketObservation",
    "MetricKind",
    "Outcome",
    "OutcomeError",
    "OutcomeEvaluationError",
    "OutcomeSpec",
    "OutcomeSpecId",
    "OutcomeState",
    "PathMetrics",
    "evaluate_outcome",
]
