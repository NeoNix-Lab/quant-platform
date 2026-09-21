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

Design notes (governed by ADR-0038, the accepted F03 Outcome v1 semantic
authority; this summary tracks that ADR and must not silently diverge from
it -- a genuine semantic change requires a contract-evolution decision, not
an edit here):

- ``OutcomeSpec`` semantic identity is exactly the tuple (outcome_key,
  semantic_version, horizon_duration, sampling_period, metric_kind,
  price_reference).  No other field participates.
- ``horizon_duration`` accepts either a governed unit-suffixed duration
  spelling (``5m``, ``15m``, ``1h``, ...; identical unit vocabulary to
  ``CandleDefinitionV1.duration_ns``) or a plain positive Python ``int``
  bar count.  ``sampling_period`` is always a duration spelling: the fixed
  expected spacing between consecutive forward observations (e.g. the
  candle/bar duration the caller is sourcing ``market_series`` from).  A
  duration horizon must be an exact multiple of ``sampling_period``
  (validated at construction) so that both horizon shapes resolve, without
  any dependence on the supplied market data, to the same thing: a fixed
  number of ``sampling_period``-spaced grid steps forward from the event.
  This is what lets ``evaluate_outcome`` compute ``horizon_end`` for a
  bar-count horizon exactly the same way as for a duration horizon
  (``count * sampling_period``), and walk both on one fixed grid.
- An ``Outcome``'s ``horizon_start`` is always the triggering event's own
  ``event_time`` -- the outcome measures the path forward from the moment
  the event occurred, never from when it became knowable.
- ``horizon_end`` is always ``event_time + spec.horizon_duration_ns``,
  computed purely from ``spec`` and ``event``; it never depends on the
  supplied market data, for either horizon shape.
- ``evaluate_outcome`` requires the first supplied ``MarketObservation`` to
  be the anchor: its ``instant`` must equal ``event.event_time`` exactly.
  From there the forward path is walked on the fixed ``sampling_period``
  grid: each expected grid instant must be matched exactly by the next
  supplied observation.  A grid instant with no matching observation --
  the series skips past it while more evidence remains -- means the
  evaluator cannot see through to a trustworthy boundary or path, so the
  result is ``INSUFFICIENT_COVERAGE`` rather than either fabricating a
  metric over an incomplete path or overshooting past the requested
  horizon to the next available price.
- Genuine Python iterator/generator exhaustion is never, by itself,
  evidence that the authoritative market data source has ended: it proves
  only that the caller stopped supplying values.  A plain exhausted
  ``market_series`` before ``horizon_end`` is therefore
  ``INSUFFICIENT_COVERAGE``, exactly like an interior gap.
  ``OutcomeState.CENSORED_END_OF_DATA`` requires the caller to supply the
  explicit local evidence marker ``EndOfData`` as the element of
  ``market_series`` at the point the authoritative source is known to have
  ended -- an auditable proposition, not an inference from iterable
  termination.  ``EndOfData`` may carry its own ``causal_available_at``
  (when the completeness confirmation itself has a knowable time); like
  every other consumed evidence, it can only ever push
  ``Outcome.causal_available_at`` forward, never earlier than
  ``horizon_end`` or any other consumed evidence.  Both failure modes are
  honest: neither ever reports a ``horizon_end`` that the evaluator did not
  actually establish from ``spec`` and ``event`` alone, and
  ``CENSORED_END_OF_DATA`` never merely guesses completeness from an
  arbitrary finite iterable running dry.
- Iterator exhaustion is detected with an implementation-local sentinel
  object distinct from every valid domain value, including ``None``: a
  malformed ``None`` (or any other non-``MarketObservation``,
  non-``EndOfData`` value) consumed at any position -- first or
  interior -- raises ``OutcomeEvaluationError`` rather than being mistaken
  for the stream ending.
- ``causal_available_at`` is the later of ``horizon_end``,
  ``event.causal_available_at``, and every consumed observation's own
  ``causal_available_at`` (anchor included, which itself defaults to the
  observation's ``instant`` when the caller supplies no independent
  finalization evidence -- mirroring F02's
  ``nullable_observed_time_never_fabricated`` floor).  Considering every
  consumed observation -- not just the boundary -- guarantees an outcome
  can never become available before any evidence actually used to compute
  it, including path metrics fed by intermediate observations.  This
  guarantees the ADR-0006 invariant ``causal_available_at >= horizon_end``
  unconditionally, enforced structurally in ``Outcome.__post_init__``
  rather than merely by convention in the evaluator.
- A non-``COMPLETE`` outcome never carries a ``realized_value``: censored or
  insufficient-coverage outcomes fail closed rather than fabricate a price
  that was never observed.
- ``realized_value`` and ``path_metrics`` are exact canonical fraction
  strings (``"<numerator>"`` or ``"<numerator>/<denominator>"``, lowest
  terms, no reducible or zero-denominator-style forms such as ``"2/2"`` or
  ``"0/3"``, and no signed-zero ``"-0"``; ``fractions.Fraction`` arithmetic
  over ``Decimal`` prices) so that outcome measurement never introduces
  float or rounding error and every number has exactly one byte
  representation -- the same exact-arithmetic discipline
  ``quant_platform.features.artifacts`` already applies to numerical
  equivalence checks.
- ``PathMetrics`` always includes the anchor's own zero return alongside
  every consumed observation's return: a path that only ever moves against
  the anchor (e.g. a straight decline) still reports a
  ``maximum_favorable_excursion`` of ``"0"`` rather than the least-bad
  observed loss, since the event-time price itself was always an
  available (if trivial) exit point.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
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
    numerator_text, separator, denominator_text = value.partition("/")
    fraction = Fraction(int(numerator_text), int(denominator_text) if separator else 1)
    if _fraction_text(fraction) != value:
        raise OutcomeError(
            f"{field_name} must be a canonical lowest-terms fraction string (no reducible "
            f"form, zero denominator collapse or signed zero), got {value!r}"
        )
    return value


def _max_instant(left: Instant, right: Instant) -> Instant:
    return left if left >= right else right


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


def _parse_duration_only(value: Any, field_name: str) -> int:
    """Parse a caller-supplied duration-only field (e.g. ``sampling_period``)
    into nanoseconds.  Unlike ``horizon_duration``, a bar count is never a
    valid shape here: a sampling period is inherently a wall-clock spacing."""

    if isinstance(value, str) and value.strip():
        match = _DURATION_RE.fullmatch(value.strip())
        if match:
            amount = int(match.group(1))
            if amount < 1:
                raise OutcomeError(f"{field_name} must be positive")
            return amount * _DURATION_UNITS[match.group(2)]
    raise OutcomeError(
        f"{field_name} must be a governed duration string (e.g. '5m', '15m', '1h'), got {value!r}"
    )


def _parse_canonical_duration_only(text: str) -> int:
    match = _CANONICAL_HORIZON_RE.fullmatch(text)
    if not match or match.group(2) != "ns":  # pragma: no cover - text always produced by __post_init__
        raise OutcomeError(f"malformed canonical sampling_period: {text!r}")
    return int(match.group(1))


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
    sampling_period: str
    metric_kind: str | MetricKind
    price_reference: str

    identity_type: ClassVar[str] = "outcome-spec"
    identity_version: ClassVar[str] = OUTCOME_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_key", _governed_key(self.outcome_key, "outcome_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        sampling_period_ns = _parse_duration_only(self.sampling_period, "sampling_period")
        object.__setattr__(self, "sampling_period", f"{sampling_period_ns}ns")
        kind, amount = _parse_horizon_input(self.horizon_duration)
        if kind is HorizonKind.DURATION and amount % sampling_period_ns != 0:
            raise OutcomeError(
                "horizon_duration must be an exact multiple of sampling_period so the "
                "forward path can be walked on a fixed grid without overshoot or gaps"
            )
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

    @property
    def sampling_period_ns(self) -> int:
        return _parse_canonical_duration_only(self.sampling_period)

    @property
    def horizon_duration_ns(self) -> int:
        """The horizon's total length in nanoseconds, computed purely from
        this spec (never from market data): a duration horizon's own
        nanosecond amount, or a bar-count horizon's count multiplied by
        ``sampling_period_ns``."""

        kind, amount = _parse_canonical_horizon(self.horizon_duration)
        if kind is HorizonKind.DURATION:
            return amount
        return amount * self.sampling_period_ns

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "outcome_key": self.outcome_key,
            "semantic_version": self.semantic_version,
            "horizon_duration": self.horizon_duration,
            "sampling_period": self.sampling_period,
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


@dataclass(frozen=True, slots=True)
class EndOfData:
    """Explicit, caller-supplied evidence that the authoritative market data
    source has no further observations beyond this point in
    ``market_series``.

    Finding B (F03 post-#86 adversarial review): an arbitrary finite
    iterable running dry proves only that the caller stopped supplying
    evidence, never that the underlying dataset actually ended.
    ``evaluate_outcome`` therefore never infers ``CENSORED_END_OF_DATA``
    from bare iterator exhaustion; a caller who holds genuine authority that
    no further evidence exists (e.g. a venue/session-close confirmation or
    their source's own completeness watermark) supplies one ``EndOfData`` as
    the next element of ``market_series`` in place of the missing
    observation instead.

    ``causal_available_at`` is when the completeness confirmation itself
    became knowable; it participates in ``Outcome.causal_available_at``
    exactly like any other consumed evidence's own causal time -- taken as
    part of a running maximum -- so it can never pull availability earlier
    than ``horizon_end`` or any other consumed evidence, only forward or not
    at all when omitted.
    """

    causal_available_at: Instant | str | None = None

    def __post_init__(self) -> None:
        if self.causal_available_at is not None:
            object.__setattr__(self, "causal_available_at", Instant.parse(self.causal_available_at))


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
    # The anchor itself (a zero return against its own price) is always an
    # available reference point along the path, not merely the forward
    # observations: a path that only ever declines still has a "best"
    # outcome of exiting flat at the event, not the least-bad observed loss.
    returns = [Fraction(0)] + [
        _return_fraction(start, observation.price_for(spec.price_reference)) for observation in path
    ]
    return PathMetrics(
        maximum_favorable_excursion=_fraction_text(max(returns)),
        maximum_adverse_excursion=_fraction_text(min(returns)),
    )


# Implementation-local sentinel distinct from every valid domain value
# (including ``None``) used to detect genuine iterator exhaustion without
# ever mistaking a malformed element for the stream ending (Finding A).
_EXHAUSTED = object()


def _validate_remaining_within_horizon(
    iterator: Iterator[MarketObservation | EndOfData],
    previous_instant: Instant,
    horizon_end: Instant,
    causal_watermark: Instant,
) -> Instant:
    """Validate/consume ``iterator`` up through ``horizon_end``, returning
    ``causal_watermark`` folded with every consumed element's own
    ``causal_available_at`` (`MarketObservation` or `EndOfData`) -- every
    element inspected here was genuinely consumed evidence and must
    participate in ``Outcome.causal_available_at`` exactly like the main
    walk's own consumed evidence (ADR-0038 §7)."""

    if previous_instant > horizon_end:
        return causal_watermark
    for remaining in iterator:
        if isinstance(remaining, EndOfData):
            if remaining.causal_available_at is not None:
                causal_watermark = _max_instant(causal_watermark, remaining.causal_available_at)
            return causal_watermark
        if not isinstance(remaining, MarketObservation):
            raise OutcomeEvaluationError("market_series must contain only MarketObservation values")
        if remaining.instant <= previous_instant:
            raise OutcomeEvaluationError(
                "market_series must be supplied in strictly increasing instant order"
            )
        previous_instant = remaining.instant
        causal_watermark = _max_instant(causal_watermark, remaining.causal_available_at)
        if remaining.instant > horizon_end:
            break
    return causal_watermark


def evaluate_outcome(
    spec: OutcomeSpec,
    event: DetectedEvent,
    market_series: Iterable[MarketObservation | EndOfData],
) -> Outcome:
    """Deterministically measure ``spec``'s forward horizon/metric relative
    to ``event`` over a strictly time-ordered ``market_series``.

    Fails closed (`OutcomeEvaluationError`) on any consumed element that is
    neither a `MarketObservation` nor an `EndOfData` marker -- including
    `None` -- or observations supplied out of strict increasing ``instant``
    order within the forward horizon, rather than silently reordering,
    deduplicating or mistaking malformed evidence for the stream ending.
    Genuine iterator exhaustion is detected with an implementation-local
    sentinel distinct from every valid domain value, so it can never be
    confused with a malformed element.  Observations strictly beyond
    ``horizon_end`` are not consumed once the outcome boundary is reached.

    The first element of ``market_series`` must be the anchor: its
    ``instant`` must equal ``event.event_time`` exactly.  When it is not
    (including an empty series or a series that opens with `EndOfData`), the
    result is `OutcomeState.INSUFFICIENT_COVERAGE`: there is no starting
    reference price to measure a forward return against, so there is nothing
    to censor either.

    ``horizon_end`` is always ``event.event_time + spec.horizon_duration_ns``
    -- computed purely from ``spec`` and ``event``, independent of the
    supplied data, for both a duration and a bar-count horizon.  The forward
    path is then walked on the fixed ``spec.sampling_period_ns`` grid
    anchored at ``event.event_time``: each expected grid instant must be
    matched exactly by the next observation in ``market_series``.

    - A grid instant with no matching observation -- the series skips ahead
      of it, or a plain iterator/generator runs dry before reaching it --
      means the evaluator cannot see through to a trustworthy boundary or
      path: `OutcomeState.INSUFFICIENT_COVERAGE`.  A bare exhausted iterable
      is never, by itself, proof the authoritative source ended.
    - `OutcomeState.CENSORED_END_OF_DATA` is emitted only when the caller
      supplies an explicit `EndOfData` marker as the element of
      ``market_series`` at the point the authoritative source is known to
      have ended, in place of the missing grid observation -- auditable
      completeness evidence, never inferred from iterable termination.
    - Matching every grid instant up to and including ``horizon_end`` is
      `OutcomeState.COMPLETE`.

    `Outcome.causal_available_at` is the later of ``horizon_end``,
    ``event.causal_available_at``, and every consumed observation's or
    `EndOfData`'s own ``causal_available_at`` (anchor included) -- never
    merely the boundary observation's -- so a late-finalizing observation
    anywhere along the path, or a late-confirmed `EndOfData`, still pushes
    availability forward, forbidding lookahead, and can never pull it
    earlier than ``horizon_end``.
    """

    if not isinstance(spec, OutcomeSpec):
        raise OutcomeEvaluationError("spec must be OutcomeSpec")
    if not isinstance(event, DetectedEvent):
        raise OutcomeEvaluationError("event must be DetectedEvent")

    horizon_end = Instant(event.event_time.epoch_ns + spec.horizon_duration_ns)

    iterator = iter(market_series)
    first = next(iterator, _EXHAUSTED)
    if first is _EXHAUSTED or isinstance(first, EndOfData):
        # No anchor was ever established -- with or without explicit
        # end-of-data evidence there is no starting reference price to
        # measure a forward return against, so this can never be more than
        # INSUFFICIENT_COVERAGE (never CENSORED_END_OF_DATA: there is
        # nothing to censor without an anchor).
        causal_available_at = _max_instant(horizon_end, event.causal_available_at)
        if isinstance(first, EndOfData) and first.causal_available_at is not None:
            causal_available_at = _max_instant(causal_available_at, first.causal_available_at)
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

    if not isinstance(first, MarketObservation):
        raise OutcomeEvaluationError("market_series must contain only MarketObservation values")

    if first.instant != event.event_time:
        causal_watermark = _max_instant(event.causal_available_at, first.causal_available_at)
        causal_watermark = _validate_remaining_within_horizon(iterator, first.instant, horizon_end, causal_watermark)
        causal_available_at = _max_instant(horizon_end, causal_watermark)
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

    anchor = first
    previous_instant = anchor.instant
    causal_watermark = _max_instant(event.causal_available_at, anchor.causal_available_at)
    sampling_period_ns = spec.sampling_period_ns

    consumed: list[MarketObservation] = []
    boundary: MarketObservation | None = None
    state = OutcomeState.INSUFFICIENT_COVERAGE
    expected_ns = anchor.instant.epoch_ns + sampling_period_ns

    while expected_ns <= horizon_end.epoch_ns:
        candidate = next(iterator, _EXHAUSTED)
        if candidate is _EXHAUSTED:
            # Genuine iterator exhaustion is never, by itself, proof the
            # authoritative source ended (Finding B): it proves only that
            # the caller stopped supplying evidence.
            state = OutcomeState.INSUFFICIENT_COVERAGE
            break

        if isinstance(candidate, EndOfData):
            # Explicit, auditable end-of-data evidence: the caller holds
            # genuine authority that no further evidence exists.
            if candidate.causal_available_at is not None:
                causal_watermark = _max_instant(causal_watermark, candidate.causal_available_at)
            state = OutcomeState.CENSORED_END_OF_DATA
            break

        if not isinstance(candidate, MarketObservation):
            raise OutcomeEvaluationError("market_series must contain only MarketObservation values")
        if candidate.instant <= previous_instant:
            raise OutcomeEvaluationError(
                "market_series must be supplied in strictly increasing instant order"
            )

        if candidate.instant.epoch_ns != expected_ns:
            state = OutcomeState.INSUFFICIENT_COVERAGE
            causal_watermark = _max_instant(causal_watermark, candidate.causal_available_at)
            causal_watermark = _validate_remaining_within_horizon(
                iterator, candidate.instant, horizon_end, causal_watermark
            )
            break

        causal_watermark = _max_instant(causal_watermark, candidate.causal_available_at)
        previous_instant = candidate.instant

        if expected_ns == horizon_end.epoch_ns:
            boundary = candidate
            state = OutcomeState.COMPLETE
            break

        consumed.append(candidate)
        expected_ns += sampling_period_ns

    causal_available_at = _max_instant(horizon_end, causal_watermark)

    if boundary is None:
        return Outcome(
            outcome_spec_id=spec.identity,
            event_id=event.event_id,
            horizon_start=event.event_time,
            horizon_end=horizon_end,
            causal_available_at=causal_available_at,
            state=state,
            realized_value=None,
            path_metrics=_path_metrics(spec, anchor, consumed),
        )

    realized_path = consumed + [boundary]
    realized_value = _compute_metric(spec, anchor, boundary, realized_path)
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
    "EndOfData",
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
