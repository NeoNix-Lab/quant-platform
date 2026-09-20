"""EventStudySpec v1 semantic model and parameter sweep runtime.

This module owns the bounded F04 semantic foundation only: an immutable,
reproducible declaration that pairs one F02 ``EventSpec`` with one or more
forward F03 ``OutcomeSpec``s under a single F01 hypothesis
(``EventStudySpec``), a deterministic evaluation runtime that assembles the
resulting event-outcome population and its summary aggregations
(``run_event_study``), and a comparative-grid extension over multiple study
configurations (``ParameterSweepSpec`` / ``run_parameter_sweep``).  It does
not:

- detect events or evaluate predicates (F02 ``EventSpec``/``detect_events``);
- measure forward outcomes (F03 ``OutcomeSpec``/``evaluate_outcome``);
- classify outcomes into discrete trade labels or triple-barrier states
  (F07 Label Engine -- ADR-0008);
- simulate a trading policy, position sizing, transaction costs, slippage,
  order execution or PnL (Strategy/Execution G/H spines) -- ADR-0007 keeps
  event-study phenomenon existence distinct from strategy backtesting: an
  ``EventStudyResult`` answers "does this conditional relationship show up
  in forward outcomes", never "would a strategy have profited";
- perform validation/purge/embargo (F06);
- open DataGateway, files or PostgreSQL (``quant_platform.research`` depends
  only on ``feature`` and ``shared`` -- see
  ``tests/test_package_boundaries_v1.py``);
- introduce retrospective/lookahead statistics or rolling cross-fold
  aggregations: every aggregate here summarizes exactly the population
  handed to ``run_event_study``, computed once, forward-looking only through
  the F03 outcomes it pairs -- never recomputed against future folds.

Design notes (frozen for this PR only -- not governance-authoritative; a
later dedicated governance pass materializes whichever of these become the
accepted ADR text):

- ``EventStudySpec`` semantic identity is exactly the tuple (study_key,
  semantic_version, hypothesis_id, event_spec_id, outcome_spec_ids).  No
  other field participates; ``description`` is administrative annotation
  only, excluded from identity and equality, mirroring F01's ``notes``.
- ``EventStudySpec.hypothesis_id`` must equal ``event_spec.hypothesis_id``:
  a study cannot claim to serve a different hypothesis than the event it
  studies already operationally binds to (fail closed on construction).
- ``outcome_specs`` is an ordered, duplicate-free tuple -- order is
  semantically meaningful (it fixes ``EventStudyResult.aggregates``
  ordering and the sweep grid's comparative table layout), unlike F01's
  ``observable_references`` set, so it is never silently reordered or
  deduplicated; a duplicate raises rather than collapsing silently.
- ``run_event_study`` evaluates every detected event against every declared
  ``OutcomeSpec`` via F03's ``evaluate_outcome``, slicing the supplied
  ``market_series`` to each event's own forward window (every observation
  at or after ``event.event_time``) before delegating -- it never
  reimplements forward-path walking itself.  It fails closed on an event
  bound to a different ``EventSpec`` than ``spec.event_spec``, a duplicate
  event, or a ``market_series`` supplied out of strictly increasing
  ``instant`` order.
- A ``PopulationRecord.realized_value`` is stored as ``Decimal`` (never a
  raw fraction string) so aggregation arithmetic is exact-precision
  ``Decimal`` throughout, not float; every division here runs inside a
  fixed-precision (``_DECIMAL_PRECISION`` significant digits) local
  context so results are deterministic and reproducible across processes,
  even where a fraction (e.g. a repeating decimal like ``1/3``) has no
  finite exact decimal expansion -- the same fixed precision is applied
  every time, so identical inputs always yield byte-identical output.
- ``AggregateMetrics``' mean/median/std-dev/min/max/positive-rate/
  negative-rate are computed only over ``COMPLETE`` population records; a
  ``0``-``completed_count`` outcome spec reports every metric as ``None``
  rather than fabricating a value from censored or insufficient-coverage
  outcomes, matching F03's own no-fabrication discipline.
- ``EventStudyResult.causal_available_at`` is validated structurally (not
  merely by convention in the evaluator) to be
  ``>= max(population_record.causal_available_at)`` -- the ADR-0006
  non-anticipation invariant this issue must guarantee -- mirroring how
  F03's ``Outcome`` enforces ``causal_available_at >= horizon_end`` in its
  own ``__post_init__``.
- ``ParameterSweepSpec.study_specs`` must all share the sweep's own
  ``hypothesis_id`` (fail closed on a mismatched grid point) and must be
  pairwise distinct by identity.  ``run_parameter_sweep`` accepts either one
  shared ``events`` sequence for every grid point, or a mapping keyed by
  each study's own ``event_spec.identity`` -- the latter is what a grid that
  varies the event predicate threshold (and therefore ``EventSpec``
  identity, and therefore the detected event population) requires.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
import hashlib
import json
import re
from typing import Any, ClassVar

from ..features import Instant
from .events import DetectedEvent, EventSpec
from .hypothesis import HypothesisSpecError, HypothesisSpecId
from .outcomes import (
    MarketObservation,
    OutcomeSpec,
    OutcomeSpecId,
    OutcomeState,
    evaluate_outcome,
)


EVENT_STUDY_SPEC_IDENTITY_DOMAIN = "event-study-spec-v1"
EVENT_STUDY_SPEC_MODEL_VERSION = "1"
EVENT_STUDY_RESULT_IDENTITY_DOMAIN = "event-study-result-v1"
PARAMETER_SWEEP_SPEC_IDENTITY_DOMAIN = "parameter-sweep-spec-v1"
PARAMETER_SWEEP_SPEC_MODEL_VERSION = "1"
PARAMETER_SWEEP_RESULT_IDENTITY_DOMAIN = "parameter-sweep-result-v1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")
_FRACTION_TEXT_RE = re.compile(r"^-?(0|[1-9][0-9]*)(/[1-9][0-9]*)?$")

_DECIMAL_PRECISION = 50


class EventStudyError(ValueError):
    """An EventStudySpec/ParameterSweepSpec/population/aggregate v1
    semantic value violates the frozen contract."""


class EventStudyRuntimeError(ValueError):
    """`run_event_study` or `run_parameter_sweep` was supplied evidence
    that would violate causal non-anticipation, binding consistency, or
    strict temporal order."""


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventStudyError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise EventStudyError(f"{field_name} must not contain control characters")
    return text


def _governed_key(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise EventStudyError(f"{field_name} must be a governed canonical key spelling")
    return text


def _semantic_version(value: Any, field_name: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field_name)
    if not _VERSION_RE.fullmatch(text):
        raise EventStudyError(f"{field_name} must be an explicit positive version")
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


def _validated_hypothesis_id(value: Any) -> str:
    text = _non_empty_text(value, "hypothesis_id")
    try:
        return str(HypothesisSpecId(text))
    except HypothesisSpecError as exc:
        raise EventStudyError(f"hypothesis_id must be a valid HypothesisSpecId: {exc}") from exc


def _validated_outcome_spec_id(value: Any, field_name: str = "outcome_spec_id") -> str:
    text = _non_empty_text(value, field_name)
    try:
        return str(OutcomeSpecId(text))
    except Exception as exc:
        raise EventStudyError(f"{field_name} must be a valid OutcomeSpecId: {exc}") from exc


def _enum(enum_type: Any, value: Any, field_name: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise EventStudyError(f"{field_name} is not a supported v1 value") from exc


def _decimal_from_fraction_text(text: str) -> Decimal:
    numerator_text, separator, denominator_text = text.partition("/")
    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        numerator = Decimal(numerator_text)
        denominator = Decimal(denominator_text) if separator else Decimal(1)
        return numerator / denominator


def _coerce_realized_value(value: Any, field_name: str = "realized_value") -> Decimal:
    if isinstance(value, bool):
        raise EventStudyError(f"{field_name} must be numeric, not boolean")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise EventStudyError(f"{field_name} must be finite")
        return value
    if isinstance(value, str):
        text = value.strip()
        if _FRACTION_TEXT_RE.fullmatch(text):
            return _decimal_from_fraction_text(text)
        raise EventStudyError(f"{field_name} must be an exact canonical fraction string, got {value!r}")
    raise EventStudyError(f"{field_name} has unsupported type: {type(value).__name__}")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# EventStudySpec identity and value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventStudySpecId:
    """Deterministic content-derived EventStudySpec identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "EventStudySpecId")
        prefix = f"{EVENT_STUDY_SPEC_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", text.removeprefix(prefix)):
            raise EventStudyError("EventStudySpecId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EventStudySpecId":
        return cls(f"{EVENT_STUDY_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EventStudySpec:
    """Immutable, reproducible declaration pairing one `EventSpec` with one
    or more forward `OutcomeSpec`s, bound to a hypothesis.

    ``EventStudySpec`` makes no claim about how events are detected (F02) or
    how outcomes are measured (F03); it only declares which of each to pair
    together, and which hypothesis the pairing operationally serves.
    """

    study_key: str
    semantic_version: str | int
    hypothesis_id: str
    event_spec: EventSpec
    outcome_specs: Iterable[OutcomeSpec]
    description: str | None = field(default=None, compare=False)

    identity_type: ClassVar[str] = "event-study-spec"
    identity_version: ClassVar[str] = EVENT_STUDY_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_key", _governed_key(self.study_key, "study_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        object.__setattr__(self, "hypothesis_id", _validated_hypothesis_id(self.hypothesis_id))

        if not isinstance(self.event_spec, EventSpec):
            raise EventStudyError("event_spec must be EventSpec")
        if self.hypothesis_id != self.event_spec.hypothesis_id:
            raise EventStudyError(
                "hypothesis_id must match event_spec.hypothesis_id: an event study cannot "
                "claim to serve a different hypothesis than the event it studies already binds to"
            )

        if isinstance(self.outcome_specs, (str, bytes, bytearray)):
            raise EventStudyError("outcome_specs must be an iterable of OutcomeSpec")
        outcome_specs = tuple(self.outcome_specs)
        if not outcome_specs:
            raise EventStudyError("EventStudySpec requires at least one outcome spec")
        seen: set[str] = set()
        for index, outcome_spec in enumerate(outcome_specs):
            if not isinstance(outcome_spec, OutcomeSpec):
                raise EventStudyError(f"outcome_specs[{index}] must be OutcomeSpec")
            if outcome_spec.identity in seen:
                raise EventStudyError(f"duplicate outcome_spec in outcome_specs: {outcome_spec.identity}")
            seen.add(outcome_spec.identity)
        object.__setattr__(self, "outcome_specs", outcome_specs)

        if self.description is not None:
            object.__setattr__(self, "description", _non_empty_text(self.description, "description"))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "study_key": self.study_key,
            "semantic_version": self.semantic_version,
            "hypothesis_id": self.hypothesis_id,
            "event_spec_id": self.event_spec.identity,
            "outcome_spec_ids": [spec.identity for spec in self.outcome_specs],
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
    def spec_id(self) -> EventStudySpecId:
        return EventStudySpecId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.spec_id)

    def stable_dict(self) -> dict[str, Any]:
        return {"spec_id": self.identity, "canonical_payload": self.canonical_payload(), "description": self.description}


# ---------------------------------------------------------------------------
# Population and aggregate metrics value models.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PopulationRecord:
    """One evaluated pairing of a `DetectedEvent` with the `Outcome`
    measured for it under one `OutcomeSpec`."""

    event_id: str
    event_time: Instant | str
    outcome_spec_id: str
    outcome_id: str
    state: OutcomeState | str
    realized_value: Decimal | str | None
    causal_available_at: Instant | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _non_empty_text(self.event_id, "event_id"))
        object.__setattr__(self, "event_time", Instant.parse(self.event_time))
        object.__setattr__(self, "outcome_spec_id", _validated_outcome_spec_id(self.outcome_spec_id))
        object.__setattr__(self, "outcome_id", _non_empty_text(self.outcome_id, "outcome_id"))
        object.__setattr__(self, "state", _enum(OutcomeState, self.state, "state"))
        object.__setattr__(self, "causal_available_at", Instant.parse(self.causal_available_at))

        if self.state is OutcomeState.COMPLETE:
            if self.realized_value is None:
                raise EventStudyError("a COMPLETE population record must carry a realized_value")
            object.__setattr__(self, "realized_value", _coerce_realized_value(self.realized_value))
        elif self.realized_value is not None:
            raise EventStudyError(
                f"a {OutcomeState(self.state).value} population record must not fabricate a realized_value"
            )

        if self.causal_available_at < self.event_time:
            raise EventStudyError(
                "causal_available_at cannot precede event_time: a population record can never "
                "be legally consumable before the event it measures even occurred"
            )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_time": self.event_time.isoformat(),
            "outcome_spec_id": self.outcome_spec_id,
            "outcome_id": self.outcome_id,
            "state": self.state.value,
            "realized_value": _decimal_text(self.realized_value),
            "causal_available_at": self.causal_available_at.isoformat(),
        }


_METRIC_FIELDS = (
    "mean_realized_value",
    "median_realized_value",
    "std_dev_realized_value",
    "min_realized_value",
    "max_realized_value",
    "positive_rate",
    "negative_rate",
)


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    """Immutable deterministic summary of one `OutcomeSpec`'s population
    within an event study: state counts and exact-`Decimal` sample
    statistics over `COMPLETE` outcomes only."""

    outcome_spec_id: str
    sample_count: int
    completed_count: int
    censored_count: int
    insufficient_coverage_count: int
    mean_realized_value: Decimal | None
    median_realized_value: Decimal | None
    std_dev_realized_value: Decimal | None
    min_realized_value: Decimal | None
    max_realized_value: Decimal | None
    positive_rate: Decimal | None
    negative_rate: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_spec_id", _validated_outcome_spec_id(self.outcome_spec_id))

        for count_field in (
            "sample_count", "completed_count", "censored_count", "insufficient_coverage_count",
        ):
            value = getattr(self, count_field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EventStudyError(f"{count_field} must be a non-negative integer")

        if self.completed_count + self.censored_count + self.insufficient_coverage_count != self.sample_count:
            raise EventStudyError(
                "completed_count + censored_count + insufficient_coverage_count must equal sample_count"
            )

        if self.completed_count == 0:
            for metric_field in _METRIC_FIELDS:
                if getattr(self, metric_field) is not None:
                    raise EventStudyError(f"{metric_field} must be None when completed_count is 0")
        else:
            for metric_field in _METRIC_FIELDS:
                value = getattr(self, metric_field)
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise EventStudyError(f"{metric_field} must be a finite Decimal when completed_count > 0")
            for rate_field in ("positive_rate", "negative_rate"):
                value = getattr(self, rate_field)
                if value < 0 or value > 1:
                    raise EventStudyError(f"{rate_field} must be within [0, 1]")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "outcome_spec_id": self.outcome_spec_id,
            "sample_count": self.sample_count,
            "completed_count": self.completed_count,
            "censored_count": self.censored_count,
            "insufficient_coverage_count": self.insufficient_coverage_count,
            "mean_realized_value": _decimal_text(self.mean_realized_value),
            "median_realized_value": _decimal_text(self.median_realized_value),
            "std_dev_realized_value": _decimal_text(self.std_dev_realized_value),
            "min_realized_value": _decimal_text(self.min_realized_value),
            "max_realized_value": _decimal_text(self.max_realized_value),
            "positive_rate": _decimal_text(self.positive_rate),
            "negative_rate": _decimal_text(self.negative_rate),
        }


# ---------------------------------------------------------------------------
# EventStudyResult value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    """Immutable record of one `run_event_study` evaluation: the evaluated
    population of event-outcome pairs and one `AggregateMetrics` per
    declared `OutcomeSpec`.

    The temporal availability invariant (``causal_available_at >=
    max(population_record.causal_available_at)``) is enforced here, not
    merely by convention in ``run_event_study``: no code path can construct
    an `EventStudyResult` that violates it.
    """

    study_spec_id: str
    population_records: Iterable[PopulationRecord]
    aggregates: Iterable[AggregateMetrics]
    causal_available_at: Instant | str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "study_spec_id", str(EventStudySpecId(_non_empty_text(self.study_spec_id, "study_spec_id")))
        )

        if isinstance(self.population_records, (str, bytes, bytearray)):
            raise EventStudyError("population_records must be an iterable of PopulationRecord")
        population_records = tuple(self.population_records)
        for index, record in enumerate(population_records):
            if not isinstance(record, PopulationRecord):
                raise EventStudyError(f"population_records[{index}] must be PopulationRecord")
        object.__setattr__(self, "population_records", population_records)

        if isinstance(self.aggregates, (str, bytes, bytearray)):
            raise EventStudyError("aggregates must be an iterable of AggregateMetrics")
        aggregates = tuple(self.aggregates)
        if not aggregates:
            raise EventStudyError("EventStudyResult requires at least one aggregate")
        for index, aggregate in enumerate(aggregates):
            if not isinstance(aggregate, AggregateMetrics):
                raise EventStudyError(f"aggregates[{index}] must be AggregateMetrics")
        object.__setattr__(self, "aggregates", aggregates)

        object.__setattr__(self, "causal_available_at", Instant.parse(self.causal_available_at))
        if population_records:
            max_record_causal = max(record.causal_available_at for record in population_records)
            if self.causal_available_at < max_record_causal:
                raise EventStudyError(
                    "causal_available_at must be >= max(population_record.causal_available_at): a "
                    "study result can never be legally consumable before every measurement that "
                    "composes it"
                )

    @property
    def study_id(self) -> str:
        payload = {
            "identity_domain": EVENT_STUDY_RESULT_IDENTITY_DOMAIN,
            "study_spec_id": self.study_spec_id,
            "population_records": [record.stable_dict() for record in self.population_records],
            "aggregates": [aggregate.stable_dict() for aggregate in self.aggregates],
            "causal_available_at": self.causal_available_at.isoformat(),
        }
        return f"{EVENT_STUDY_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "study_spec_id": self.study_spec_id,
            "population_records": [record.stable_dict() for record in self.population_records],
            "aggregates": [aggregate.stable_dict() for aggregate in self.aggregates],
            "causal_available_at": self.causal_available_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# ParameterSweepSpec identity and value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParameterSweepSpecId:
    """Deterministic content-derived ParameterSweepSpec identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "ParameterSweepSpecId")
        prefix = f"{PARAMETER_SWEEP_SPEC_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", text.removeprefix(prefix)):
            raise EventStudyError("ParameterSweepSpecId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ParameterSweepSpecId":
        return cls(f"{PARAMETER_SWEEP_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ParameterSweepSpec:
    """Immutable specification declaring a collection of `EventStudySpec`s
    covering a parameter grid (e.g. varying event predicate thresholds or
    forward horizons), all serving one hypothesis."""

    sweep_key: str
    semantic_version: str | int
    hypothesis_id: str
    study_specs: Iterable[EventStudySpec]

    identity_type: ClassVar[str] = "parameter-sweep-spec"
    identity_version: ClassVar[str] = PARAMETER_SWEEP_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "sweep_key", _governed_key(self.sweep_key, "sweep_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        object.__setattr__(self, "hypothesis_id", _validated_hypothesis_id(self.hypothesis_id))

        if isinstance(self.study_specs, (str, bytes, bytearray)):
            raise EventStudyError("study_specs must be an iterable of EventStudySpec")
        study_specs = tuple(self.study_specs)
        if not study_specs:
            raise EventStudyError("ParameterSweepSpec requires at least one study spec")
        seen: set[str] = set()
        for index, study_spec in enumerate(study_specs):
            if not isinstance(study_spec, EventStudySpec):
                raise EventStudyError(f"study_specs[{index}] must be EventStudySpec")
            if study_spec.hypothesis_id != self.hypothesis_id:
                raise EventStudyError(
                    f"study_specs[{index}] is bound to hypothesis {study_spec.hypothesis_id}, not "
                    f"the ParameterSweepSpec's hypothesis {self.hypothesis_id}"
                )
            if study_spec.identity in seen:
                raise EventStudyError(f"duplicate study_spec in study_specs: {study_spec.identity}")
            seen.add(study_spec.identity)
        object.__setattr__(self, "study_specs", study_specs)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "sweep_key": self.sweep_key,
            "semantic_version": self.semantic_version,
            "hypothesis_id": self.hypothesis_id,
            "study_spec_ids": [spec.identity for spec in self.study_specs],
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
    def spec_id(self) -> ParameterSweepSpecId:
        return ParameterSweepSpecId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.spec_id)

    def stable_dict(self) -> dict[str, Any]:
        return {"spec_id": self.identity, "canonical_payload": self.canonical_payload()}


# ---------------------------------------------------------------------------
# ParameterSweepResult value model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SweepSummaryRow:
    """One comparative row of a `ParameterSweepResult.summary_table`: the
    key aggregate metrics for one (study_spec, outcome_spec) grid cell."""

    study_spec_id: str
    outcome_spec_id: str
    sample_count: int
    completed_count: int
    censored_count: int
    insufficient_coverage_count: int
    mean_realized_value: Decimal | None
    positive_rate: Decimal | None
    negative_rate: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "study_spec_id", str(EventStudySpecId(_non_empty_text(self.study_spec_id, "study_spec_id")))
        )
        object.__setattr__(self, "outcome_spec_id", _validated_outcome_spec_id(self.outcome_spec_id))
        for count_field in (
            "sample_count", "completed_count", "censored_count", "insufficient_coverage_count",
        ):
            value = getattr(self, count_field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EventStudyError(f"{count_field} must be a non-negative integer")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "study_spec_id": self.study_spec_id,
            "outcome_spec_id": self.outcome_spec_id,
            "sample_count": self.sample_count,
            "completed_count": self.completed_count,
            "censored_count": self.censored_count,
            "insufficient_coverage_count": self.insufficient_coverage_count,
            "mean_realized_value": _decimal_text(self.mean_realized_value),
            "positive_rate": _decimal_text(self.positive_rate),
            "negative_rate": _decimal_text(self.negative_rate),
        }


@dataclass(frozen=True, slots=True)
class ParameterSweepResult:
    """Immutable collection of `EventStudyResult`s produced by
    `run_parameter_sweep`, plus a reproducible comparative summary table
    across the sweep grid."""

    sweep_spec_id: str
    study_results: Iterable[EventStudyResult]
    summary_table: Iterable[SweepSummaryRow]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "sweep_spec_id", str(ParameterSweepSpecId(_non_empty_text(self.sweep_spec_id, "sweep_spec_id")))
        )

        if isinstance(self.study_results, (str, bytes, bytearray)):
            raise EventStudyError("study_results must be an iterable of EventStudyResult")
        study_results = tuple(self.study_results)
        if not study_results:
            raise EventStudyError("ParameterSweepResult requires at least one study result")
        for index, result in enumerate(study_results):
            if not isinstance(result, EventStudyResult):
                raise EventStudyError(f"study_results[{index}] must be EventStudyResult")
        object.__setattr__(self, "study_results", study_results)

        if isinstance(self.summary_table, (str, bytes, bytearray)):
            raise EventStudyError("summary_table must be an iterable of SweepSummaryRow")
        summary_table = tuple(self.summary_table)
        for index, row in enumerate(summary_table):
            if not isinstance(row, SweepSummaryRow):
                raise EventStudyError(f"summary_table[{index}] must be SweepSummaryRow")
        object.__setattr__(self, "summary_table", summary_table)

    @property
    def sweep_id(self) -> str:
        payload = {
            "identity_domain": PARAMETER_SWEEP_RESULT_IDENTITY_DOMAIN,
            "sweep_spec_id": self.sweep_spec_id,
            "study_results": [
                {"study_spec_id": result.study_spec_id, "study_id": result.study_id}
                for result in self.study_results
            ],
            "summary_table": [row.stable_dict() for row in self.summary_table],
        }
        return f"{PARAMETER_SWEEP_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "sweep_id": self.sweep_id,
            "sweep_spec_id": self.sweep_spec_id,
            "study_results": [result.stable_dict() for result in self.study_results],
            "summary_table": [row.stable_dict() for row in self.summary_table],
        }


# ---------------------------------------------------------------------------
# Study and sweep evaluation runtime.
# ---------------------------------------------------------------------------


def _aggregate_for_outcome_spec(
    outcome_spec: OutcomeSpec, population_records: Sequence[PopulationRecord]
) -> AggregateMetrics:
    matching = [record for record in population_records if record.outcome_spec_id == outcome_spec.identity]
    sample_count = len(matching)
    completed = [record for record in matching if record.state is OutcomeState.COMPLETE]
    censored_count = sum(1 for record in matching if record.state is OutcomeState.CENSORED_END_OF_DATA)
    insufficient_count = sum(1 for record in matching if record.state is OutcomeState.INSUFFICIENT_COVERAGE)
    completed_count = len(completed)

    if completed_count == 0:
        return AggregateMetrics(
            outcome_spec_id=outcome_spec.identity,
            sample_count=sample_count,
            completed_count=0,
            censored_count=censored_count,
            insufficient_coverage_count=insufficient_count,
            mean_realized_value=None,
            median_realized_value=None,
            std_dev_realized_value=None,
            min_realized_value=None,
            max_realized_value=None,
            positive_rate=None,
            negative_rate=None,
        )

    values = [record.realized_value for record in completed]
    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        count_decimal = Decimal(completed_count)
        mean_value = sum(values, start=Decimal(0)) / count_decimal
        sorted_values = sorted(values)
        midpoint = completed_count // 2
        if completed_count % 2 == 1:
            median_value = sorted_values[midpoint]
        else:
            median_value = (sorted_values[midpoint - 1] + sorted_values[midpoint]) / Decimal(2)
        variance = sum(((value - mean_value) ** 2 for value in values), start=Decimal(0)) / count_decimal
        std_dev_value = variance.sqrt()
        min_value = min(values)
        max_value = max(values)
        positive_count = sum(1 for value in values if value > 0)
        negative_count = sum(1 for value in values if value < 0)
        positive_rate = Decimal(positive_count) / count_decimal
        negative_rate = Decimal(negative_count) / count_decimal

    return AggregateMetrics(
        outcome_spec_id=outcome_spec.identity,
        sample_count=sample_count,
        completed_count=completed_count,
        censored_count=censored_count,
        insufficient_coverage_count=insufficient_count,
        mean_realized_value=mean_value,
        median_realized_value=median_value,
        std_dev_realized_value=std_dev_value,
        min_realized_value=min_value,
        max_realized_value=max_value,
        positive_rate=positive_rate,
        negative_rate=negative_rate,
    )


def run_event_study(
    spec: EventStudySpec,
    events: Sequence[DetectedEvent],
    market_series: Sequence[MarketObservation],
) -> EventStudyResult:
    """Deterministically evaluate every ``events`` element against every
    ``spec.outcome_specs`` entry via F03's ``evaluate_outcome``, assembling
    the resulting `PopulationRecord`s and one `AggregateMetrics` per
    outcome spec.

    Fails closed (`EventStudyRuntimeError`) on: a non-`EventStudySpec`
    ``spec``, an empty ``events``, a non-`DetectedEvent` element, an event
    bound to a different `EventSpec` than ``spec.event_spec``, a duplicate
    event, a non-`MarketObservation` element in ``market_series``, or a
    ``market_series`` supplied out of strictly increasing ``instant`` order.

    Each event's forward outcome evaluation is anchored to exactly the
    ``market_series`` observations at or after ``event.event_time`` -- the
    same non-anticipating slice F03's ``evaluate_outcome`` already requires
    an anchor to start from -- so no observation preceding an event can ever
    influence that event's outcome measurement.
    """

    if not isinstance(spec, EventStudySpec):
        raise EventStudyRuntimeError("spec must be EventStudySpec")

    if isinstance(events, (str, bytes, bytearray)):
        raise EventStudyRuntimeError("events must be a sequence of DetectedEvent")
    events = list(events)
    if not events:
        raise EventStudyRuntimeError("run_event_study requires at least one detected event")
    seen_event_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, DetectedEvent):
            raise EventStudyRuntimeError(f"events[{index}] must be DetectedEvent")
        if event.event_spec_id != spec.event_spec.identity:
            raise EventStudyRuntimeError(
                f"events[{index}] is bound to event_spec {event.event_spec_id}, not the "
                f"EventStudySpec's event_spec {spec.event_spec.identity}"
            )
        if event.event_id in seen_event_ids:
            raise EventStudyRuntimeError(f"duplicate event supplied: {event.event_id}")
        seen_event_ids.add(event.event_id)

    if isinstance(market_series, (str, bytes, bytearray)):
        raise EventStudyRuntimeError("market_series must be a sequence of MarketObservation")
    market_series = list(market_series)
    previous_instant: Instant | None = None
    for index, observation in enumerate(market_series):
        if not isinstance(observation, MarketObservation):
            raise EventStudyRuntimeError(f"market_series[{index}] must be MarketObservation")
        if previous_instant is not None and observation.instant <= previous_instant:
            raise EventStudyRuntimeError("market_series must be supplied in strictly increasing instant order")
        previous_instant = observation.instant

    population_records: list[PopulationRecord] = []
    for event in events:
        forward_series = [observation for observation in market_series if observation.instant >= event.event_time]
        for outcome_spec in spec.outcome_specs:
            outcome = evaluate_outcome(outcome_spec, event, forward_series)
            population_records.append(
                PopulationRecord(
                    event_id=event.event_id,
                    event_time=event.event_time,
                    outcome_spec_id=outcome.outcome_spec_id,
                    outcome_id=outcome.outcome_id,
                    state=outcome.state,
                    realized_value=outcome.realized_value,
                    causal_available_at=outcome.causal_available_at,
                )
            )

    aggregates = tuple(
        _aggregate_for_outcome_spec(outcome_spec, population_records) for outcome_spec in spec.outcome_specs
    )
    causal_available_at = max(record.causal_available_at for record in population_records)

    return EventStudyResult(
        study_spec_id=spec.identity,
        population_records=tuple(population_records),
        aggregates=aggregates,
        causal_available_at=causal_available_at,
    )


def run_parameter_sweep(
    sweep_spec: ParameterSweepSpec,
    events: Sequence[DetectedEvent] | Mapping[str, Sequence[DetectedEvent]],
    market_series: Sequence[MarketObservation],
) -> ParameterSweepResult:
    """Deterministically execute `run_event_study` for every
    ``sweep_spec.study_specs`` grid point, producing one `EventStudyResult`
    per point plus a reproducible comparative ``summary_table``.

    ``events`` may be a single sequence shared by every grid point, or a
    mapping keyed by each study's own ``event_spec.identity`` -- required
    when the grid varies the event predicate threshold (and therefore the
    detected event population) rather than only the forward horizon.  Fails
    closed (`EventStudyRuntimeError`) on a non-`ParameterSweepSpec`
    ``sweep_spec`` or a mapping missing a grid point's ``event_spec``.
    """

    if not isinstance(sweep_spec, ParameterSweepSpec):
        raise EventStudyRuntimeError("sweep_spec must be ParameterSweepSpec")

    is_mapping = isinstance(events, Mapping)
    if not is_mapping and isinstance(events, (str, bytes, bytearray)):
        raise EventStudyRuntimeError("events must be a sequence or mapping of DetectedEvent sequences")

    study_results: list[EventStudyResult] = []
    for study_spec in sweep_spec.study_specs:
        if is_mapping:
            key = study_spec.event_spec.identity
            if key not in events:
                raise EventStudyRuntimeError(
                    f"events mapping is missing detected events for event_spec {key} "
                    f"(study_spec {study_spec.identity})"
                )
            events_for_study = events[key]
        else:
            events_for_study = events
        study_results.append(run_event_study(study_spec, events_for_study, market_series))

    summary_table = tuple(
        SweepSummaryRow(
            study_spec_id=result.study_spec_id,
            outcome_spec_id=aggregate.outcome_spec_id,
            sample_count=aggregate.sample_count,
            completed_count=aggregate.completed_count,
            censored_count=aggregate.censored_count,
            insufficient_coverage_count=aggregate.insufficient_coverage_count,
            mean_realized_value=aggregate.mean_realized_value,
            positive_rate=aggregate.positive_rate,
            negative_rate=aggregate.negative_rate,
        )
        for result in study_results
        for aggregate in result.aggregates
    )

    return ParameterSweepResult(
        sweep_spec_id=sweep_spec.identity,
        study_results=tuple(study_results),
        summary_table=summary_table,
    )


__all__ = [
    "EVENT_STUDY_RESULT_IDENTITY_DOMAIN",
    "EVENT_STUDY_SPEC_IDENTITY_DOMAIN",
    "EVENT_STUDY_SPEC_MODEL_VERSION",
    "PARAMETER_SWEEP_RESULT_IDENTITY_DOMAIN",
    "PARAMETER_SWEEP_SPEC_IDENTITY_DOMAIN",
    "PARAMETER_SWEEP_SPEC_MODEL_VERSION",
    "AggregateMetrics",
    "EventStudyError",
    "EventStudyResult",
    "EventStudyRuntimeError",
    "EventStudySpec",
    "EventStudySpecId",
    "ParameterSweepResult",
    "ParameterSweepSpec",
    "ParameterSweepSpecId",
    "PopulationRecord",
    "SweepSummaryRow",
    "run_event_study",
    "run_parameter_sweep",
]
