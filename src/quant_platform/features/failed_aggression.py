"""Canonical failed-aggression FeatureDefinition and exact evaluator (ADR-0073 sections 1, 3 and 4).

This module owns the ``order_flow.failed_aggression@1`` FeatureDefinition, its
two identity-bearing parameters and the pure evaluator that turns one CLOSED
bar-flow bar into one E02 ``FeatureObservation``. It has no dependency on
``quant_platform.representation``: callers (Application) adapt a ``bar-flow-v1``
record into the ``FailedAggressionBarInput`` shape declared here. It owns no
registry, no server carriage and no EventSpec: an event is a hypothesis-bound
threshold over the observation value (ADR-0073 section 1), exercised by tests.

The rule is exact and division-free. Every operand is an exact decimal held as
an arbitrary-size integer coefficient and a scale, so the comparison
``D >= i * T`` depends on no float, no precision setting and no ambient
``decimal`` context, and has no operand-size ceiling.

Concrete support. The observation's support coordinate is the governed
``bar:<bucket_start>`` shape that F02 reads the event time from. It does not
carry the bar duration, which belongs to the bar-flow definition (ADR-0073 §1,
§2). Within one call every bar therefore has the same duration, and a stream is
only meaningful together with the bar-flow definition identity it was computed
from; the same coordinate from two different durations is **not** distinguished
by the observation identity (see the pull request for the escalation).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
import re
from typing import Any

from ..data.models import Instant
from .definitions import (
    FeatureAvailabilitySemantics,
    FeatureDefinition,
    FeatureDefinitionError,
    FeatureFinalitySemantics,
    FeatureObservation,
    InputContractV1,
    InputMaturity,
    NumericalEquivalence,
    ObservationLifecycle,
    OutputContract,
    OutputDimension,
    OutputValueKind,
    SemanticParameterSpec,
    SemanticParameterType,
    SupportIdentity,
    SupportReference,
)


FAILED_AGGRESSION_FEATURE_KEY = "order_flow.failed_aggression"
FAILED_AGGRESSION_SIGNAL_SHAPE = "failed_aggression_signal_v1"

BAR_FLOW_BAR_CONTRACT = InputContractV1(
    "bar_flow.bar",
    "1",
    ("close", "high", "low", "open", "taker_buy_volume", "taker_sell_volume"),
)

_IMBALANCE_THRESHOLD_PARAM = SemanticParameterSpec(
    "imbalance_threshold",
    SemanticParameterType.DECIMAL,
    default="0.3",
    min_value="0",
    max_value="1",
)
_WICK_RATIO_PARAM = SemanticParameterSpec(
    "wick_ratio",
    SemanticParameterType.DECIMAL,
    default="0.5",
    min_value="0",
    max_value="1",
)

_OUTPUT_CONTRACT = OutputContract(
    OutputValueKind.NUMERIC,
    FAILED_AGGRESSION_SIGNAL_SHAPE,
    OutputDimension.DIMENSIONLESS,
    NumericalEquivalence.exact(),
)

# E02 normalizes decimal parameters in the ambient context. Definition identity
# is built and checked in this fixed context (Python's own default), so it never
# depends on the caller's ``decimal`` settings.
_E02_DECIMAL_CONTEXT = Context(
    prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow],
)
_UNSIGNED_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
MIN_BAR_DURATION_NS = 1_000_000_000
MAX_BAR_DURATION_NS = 86_400_000_000_000


class _Exact:
    """An unsigned or signed exact decimal: ``coef / 10**scale``, with no division and no rounding."""

    __slots__ = ("coef", "scale")

    def __init__(self, coef: int, scale: int) -> None:
        self.coef, self.scale = coef, scale

    @classmethod
    def parse(cls, text: str) -> "_Exact":
        whole, _, fraction = text.partition(".")
        return cls(int(whole + fraction), len(fraction))

    @staticmethod
    def _align(left: "_Exact", right: "_Exact") -> tuple[int, int, int]:
        scale = max(left.scale, right.scale)
        return left.coef * 10 ** (scale - left.scale), right.coef * 10 ** (scale - right.scale), scale

    def __add__(self, other: "_Exact") -> "_Exact":
        a, b, scale = self._align(self, other)
        return _Exact(a + b, scale)

    def __sub__(self, other: "_Exact") -> "_Exact":
        a, b, scale = self._align(self, other)
        return _Exact(a - b, scale)

    def __mul__(self, other: "_Exact") -> "_Exact":
        return _Exact(self.coef * other.coef, self.scale + other.scale)

    def __neg__(self) -> "_Exact":
        return _Exact(-self.coef, self.scale)

    def __lt__(self, other: "_Exact") -> bool:
        a, b, _ = self._align(self, other)
        return a < b

    def __gt__(self, other: "_Exact") -> bool:
        return other < self

    def __le__(self, other: "_Exact") -> bool:
        return not other < self

    def __ge__(self, other: "_Exact") -> bool:
        return not self < other


def _larger(left: _Exact, right: _Exact) -> _Exact:
    return left if left > right else right


def _smaller(left: _Exact, right: _Exact) -> _Exact:
    return left if left < right else right


def _parameter_value(definition: FeatureDefinition, name: str) -> Any:
    for parameter in definition.canonical_parameters:
        if parameter.name == name:
            return parameter.value
    raise FeatureDefinitionError(f"missing semantic parameter: {name}")


def failed_aggression_definition(
    *, imbalance_threshold: Any = "0.3", wick_ratio: Any = "0.5",
) -> FeatureDefinition:
    """The canonical ``order_flow.failed_aggression@1`` FeatureDefinition.

    ``imbalance_threshold`` is in ``(0, 1]`` and ``wick_ratio`` in ``[0, 1)``. The
    generic E02 parameter bound is inclusive on both sides, so the exclusive
    ends are enforced here, as ADR-0035 section 4 does for ``imbalance_ratio``.
    The identity is built in a fixed decimal context, so it does not depend on
    the caller's ``decimal`` settings, and a parameter the identity cannot hold
    exactly (more than 28 significant digits) is refused rather than rounded.
    """

    supplied = {"imbalance_threshold": imbalance_threshold, "wick_ratio": wick_ratio}
    with localcontext(_E02_DECIMAL_CONTEXT):
        definition = FeatureDefinition(
            feature_key=FAILED_AGGRESSION_FEATURE_KEY,
            semantic_version="1",
            parameter_schema=(_IMBALANCE_THRESHOLD_PARAM, _WICK_RATIO_PARAM),
            parameters=supplied,
            input_contract=BAR_FLOW_BAR_CONTRACT,
            support=(SupportReference.current(),),
            input_maturity=InputMaturity.FINAL_ONLY,
            availability=FeatureAvailabilitySemantics(),
            finality=FeatureFinalitySemantics(),
            output_contract=_OUTPUT_CONTRACT,
        )
    for name, raw in supplied.items():
        canonical = Decimal(_parameter_value(definition, name))
        # Decimal comparison is exact whatever the context: a rounded parameter differs from its input.
        if Decimal(raw) != canonical:
            raise FeatureDefinitionError(f"{name} cannot be held exactly by the definition identity")
    if Decimal(_parameter_value(definition, "imbalance_threshold")) <= 0:
        raise FeatureDefinitionError("imbalance_threshold must be strictly greater than 0")
    if Decimal(_parameter_value(definition, "wick_ratio")) >= 1:
        raise FeatureDefinitionError("wick_ratio must be strictly less than 1")
    return definition


def _require_canonical_definition(definition: FeatureDefinition) -> tuple[_Exact, _Exact]:
    if not isinstance(definition, FeatureDefinition):
        raise FeatureDefinitionError("definition must be a FeatureDefinition")
    try:
        threshold = _parameter_value(definition, "imbalance_threshold")
        wick = _parameter_value(definition, "wick_ratio")
        canonical = failed_aggression_definition(imbalance_threshold=threshold, wick_ratio=wick)
        exact = _Exact.parse(threshold), _Exact.parse(wick)
    except Exception as exc:
        # Same broad net as ADR-0035's canonical checks: a foreign definition fails
        # in heterogeneous ways and is reported uniformly as "not the canonical one".
        raise FeatureDefinitionError(
            f"definition does not match canonical failed aggression: {exc}"
        ) from exc
    if definition.definition_id != canonical.definition_id:
        raise FeatureDefinitionError(
            f"expected the canonical {FAILED_AGGRESSION_FEATURE_KEY!r} FeatureDefinition, "
            f"got {definition.definition_id}"
        )
    return exact


def _exact_decimal(value: Any, field: str, *, positive: bool) -> _Exact:
    if type(value) is not str or not _UNSIGNED_DECIMAL.fullmatch(value):
        raise FeatureDefinitionError(f"{field} must be an unsigned exact decimal string")
    number = _Exact.parse(value)
    if positive and number.coef <= 0:
        raise FeatureDefinitionError(f"{field} must be positive")
    return number


@dataclass(frozen=True, slots=True)
class FailedAggressionBarInput:
    """One CLOSED bar-flow bar, adapted by Application from a ``bar-flow-v1`` record.

    The bar's duration must be one a bar-flow definition can have (1 second to
    1 day, ADR-0073 section 2).
    """

    bucket_start: Instant | str
    bucket_end: Instant | str
    open: str
    high: str
    low: str
    close: str
    taker_buy_volume: str
    taker_sell_volume: str
    observed_available_at: Instant | str | None = None
    observed_finalized_at: Instant | str | None = None

    def __post_init__(self) -> None:
        start, end = Instant.parse(self.bucket_start), Instant.parse(self.bucket_end)
        if start >= end:
            raise FeatureDefinitionError("bar bucket must have positive half-open support")
        if not MIN_BAR_DURATION_NS <= end.epoch_ns - start.epoch_ns <= MAX_BAR_DURATION_NS:
            raise FeatureDefinitionError("bar duration must be between 1 second and 1 day inclusive")
        object.__setattr__(self, "bucket_start", start)
        object.__setattr__(self, "bucket_end", end)
        for name in ("observed_available_at", "observed_finalized_at"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else Instant.parse(value))
        prices = {name: _exact_decimal(getattr(self, name), name, positive=True)
                  for name in ("open", "high", "low", "close")}
        volumes = {name: _exact_decimal(getattr(self, name), name, positive=False)
                   for name in ("taker_buy_volume", "taker_sell_volume")}
        if not prices["low"] <= prices["open"] <= prices["high"] or not prices["low"] <= prices["close"] <= prices["high"]:
            raise FeatureDefinitionError("open and close must lie within low and high")
        if (volumes["taker_buy_volume"] + volumes["taker_sell_volume"]).coef <= 0:
            raise FeatureDefinitionError("a bar carries volume")

    @property
    def duration_ns(self) -> int:
        return self.bucket_end.epoch_ns - self.bucket_start.epoch_ns  # type: ignore[union-attr]

    @property
    def coordinate(self) -> str:
        """The governed ``bar:<RFC3339>`` support coordinate (F02 reads the event time from it).

        It carries the start only: the duration belongs to the bar-flow definition.
        """
        return f"bar:{self.bucket_start.isoformat()}"  # type: ignore[union-attr]


def _signal(bar: FailedAggressionBarInput, threshold: _Exact, wick: _Exact) -> int:
    """ADR-0073 section 4: +1 trapped buyers, -1 trapped sellers, 0 otherwise."""

    buy, sell = _Exact.parse(bar.taker_buy_volume), _Exact.parse(bar.taker_sell_volume)
    open_, high, low, close = (_Exact.parse(bar.open), _Exact.parse(bar.high), _Exact.parse(bar.low), _Exact.parse(bar.close))
    total, delta = buy + sell, buy - sell
    imbalance_floor = threshold * total
    wick_floor = wick * (high - low)
    upper_wick = high - _larger(open_, close)
    lower_wick = _smaller(open_, close) - low
    if delta >= imbalance_floor and upper_wick > wick_floor and close < open_:
        return 1
    if -delta >= imbalance_floor and lower_wick > wick_floor and close > open_:
        return -1
    return 0


def evaluate_failed_aggression(
    definition: FeatureDefinition, bar: FailedAggressionBarInput,
) -> FeatureObservation:
    """Evaluate the canonical definition over one CLOSED bar: one FINAL observation."""

    threshold, wick = _require_canonical_definition(definition)
    if not isinstance(bar, FailedAggressionBarInput):
        raise FeatureDefinitionError("bar must be a FailedAggressionBarInput")
    return FeatureObservation(
        definition_id=definition.definition_id,
        support_identity=SupportIdentity(
            definition.input_contract.identity, bar.coordinate, SupportReference.current(),
        ),
        value=_signal(bar, threshold, wick),
        lifecycle=ObservationLifecycle.FINAL,
        causal_available_at=bar.bucket_end,
        observed_available_at=bar.observed_available_at,
        observed_finalized_at=bar.observed_finalized_at,
    )


def evaluate_failed_aggression_bars(
    definition: FeatureDefinition, bars: Iterable[FailedAggressionBarInput],
) -> tuple[FeatureObservation, ...]:
    """One observation per supplied bar, in order; an omitted bar yields no observation.

    The bars must be strictly increasing, non-overlapping and of one duration, so
    that no two observations of a call share a support coordinate for different
    supports. Absence is never converted to a ``0`` signal: a gap in the input is
    a gap in the output.
    """

    _require_canonical_definition(definition)
    observations: list[FeatureObservation] = []
    previous_end: Instant | None = None
    duration_ns: int | None = None
    for bar in bars:
        if not isinstance(bar, FailedAggressionBarInput):
            raise FeatureDefinitionError("bars must contain only FailedAggressionBarInput values")
        if duration_ns is not None and bar.duration_ns != duration_ns:
            raise FeatureDefinitionError("bars of one stream must all have the same duration")
        duration_ns = bar.duration_ns
        if previous_end is not None and bar.bucket_start < previous_end:
            raise FeatureDefinitionError("bars must be strictly increasing and must not overlap")
        previous_end = bar.bucket_end  # type: ignore[assignment]
        observations.append(evaluate_failed_aggression(definition, bar))
    return tuple(observations)


__all__ = [
    "BAR_FLOW_BAR_CONTRACT",
    "FAILED_AGGRESSION_FEATURE_KEY",
    "FAILED_AGGRESSION_SIGNAL_SHAPE",
    "FailedAggressionBarInput",
    "MAX_BAR_DURATION_NS",
    "MIN_BAR_DURATION_NS",
    "evaluate_failed_aggression",
    "evaluate_failed_aggression_bars",
    "failed_aggression_definition",
]
