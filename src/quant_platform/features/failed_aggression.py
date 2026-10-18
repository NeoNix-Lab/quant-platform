"""Canonical failed-aggression FeatureDefinition and exact evaluator (ADR-0073 sections 1, 3 and 4).

This module owns the ``order_flow.failed_aggression@1`` FeatureDefinition, its
two identity-bearing parameters and the pure evaluator that turns one CLOSED
bar-flow bar into one E02 ``FeatureObservation``. It has no dependency on
``quant_platform.representation``: callers (Application) adapt a ``bar-flow-v1``
record into the ``FailedAggressionBarInput`` shape declared here. It owns no
registry, no server carriage and no EventSpec: an event is a hypothesis-bound
threshold over the observation value (ADR-0073 section 1), exercised by tests.

The rule is exact and division-free. Every operand is an exact decimal and the
arithmetic runs in a context that raises instead of rounding, so the
comparison ``D >= i * T`` never depends on a float or on a precision setting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, Rounded
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

# Raise instead of rounding: an inexact result can never be silently produced.
_EXACT = Context(prec=1000, traps=[Inexact, Rounded, InvalidOperation, Overflow])
_UNSIGNED_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


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
    """

    definition = FeatureDefinition(
        feature_key=FAILED_AGGRESSION_FEATURE_KEY,
        semantic_version="1",
        parameter_schema=(_IMBALANCE_THRESHOLD_PARAM, _WICK_RATIO_PARAM),
        parameters={"imbalance_threshold": imbalance_threshold, "wick_ratio": wick_ratio},
        input_contract=BAR_FLOW_BAR_CONTRACT,
        support=(SupportReference.current(),),
        input_maturity=InputMaturity.FINAL_ONLY,
        availability=FeatureAvailabilitySemantics(),
        finality=FeatureFinalitySemantics(),
        output_contract=_OUTPUT_CONTRACT,
    )
    if Decimal(_parameter_value(definition, "imbalance_threshold")) <= 0:
        raise FeatureDefinitionError("imbalance_threshold must be strictly greater than 0")
    if Decimal(_parameter_value(definition, "wick_ratio")) >= 1:
        raise FeatureDefinitionError("wick_ratio must be strictly less than 1")
    return definition


def _require_canonical_definition(definition: FeatureDefinition) -> tuple[Decimal, Decimal]:
    if not isinstance(definition, FeatureDefinition):
        raise FeatureDefinitionError("definition must be a FeatureDefinition")
    try:
        threshold = _parameter_value(definition, "imbalance_threshold")
        wick = _parameter_value(definition, "wick_ratio")
        canonical = failed_aggression_definition(imbalance_threshold=threshold, wick_ratio=wick)
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
    return Decimal(threshold), Decimal(wick)


def _exact_decimal(value: Any, field: str, *, positive: bool) -> Decimal:
    if type(value) is not str or not _UNSIGNED_DECIMAL.fullmatch(value):
        raise FeatureDefinitionError(f"{field} must be an unsigned exact decimal string")
    number = Decimal(value)
    if positive and number <= 0:
        raise FeatureDefinitionError(f"{field} must be positive")
    return number


@dataclass(frozen=True, slots=True)
class FailedAggressionBarInput:
    """One CLOSED bar-flow bar, adapted by Application from a ``bar-flow-v1`` record."""

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
        if volumes["taker_buy_volume"] + volumes["taker_sell_volume"] <= 0:
            raise FeatureDefinitionError("a bar carries volume")

    @property
    def coordinate(self) -> str:
        """The governed ``bar:<RFC3339>`` support coordinate (F02 reads the event time from it)."""
        return f"bar:{self.bucket_start.isoformat()}"  # type: ignore[union-attr]


def _signal(bar: FailedAggressionBarInput, threshold: Decimal, wick: Decimal) -> int:
    """ADR-0073 section 4: +1 trapped buyers, -1 trapped sellers, 0 otherwise."""

    ctx = _EXACT
    buy, sell = Decimal(bar.taker_buy_volume), Decimal(bar.taker_sell_volume)
    open_, high, low, close = (Decimal(bar.open), Decimal(bar.high), Decimal(bar.low), Decimal(bar.close))
    total, delta = ctx.add(buy, sell), ctx.subtract(buy, sell)
    imbalance_floor = ctx.multiply(threshold, total)
    wick_floor = ctx.multiply(wick, ctx.subtract(high, low))
    upper_wick = ctx.subtract(high, max(open_, close))
    lower_wick = ctx.subtract(min(open_, close), low)
    if delta >= imbalance_floor and upper_wick > wick_floor and close < open_:
        return 1
    if ctx.minus(delta) >= imbalance_floor and lower_wick > wick_floor and close > open_:
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

    The bars must be strictly increasing and non-overlapping. Absence is never
    converted to a ``0`` signal: a gap in the input is a gap in the output.
    """

    _require_canonical_definition(definition)
    observations: list[FeatureObservation] = []
    previous_end: Instant | None = None
    for bar in bars:
        if not isinstance(bar, FailedAggressionBarInput):
            raise FeatureDefinitionError("bars must contain only FailedAggressionBarInput values")
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
    "evaluate_failed_aggression",
    "evaluate_failed_aggression_bars",
    "failed_aggression_definition",
]
