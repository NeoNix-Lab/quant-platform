"""H01 canonical Diagonal/Stacked Imbalance FeatureDefinitions (ADR-0035).

This module owns the two frozen H01 FeatureDefinition identities, their
exclusive parameter domains, the ``h01_imbalance@1`` FeatureSet identity, and
the Feature-owned evaluation seam that adapts a caller-supplied bucket-level
value shape into E05 kernel calls and E02 ``FeatureObservation`` values.

It has no dependency on ``quant_platform.representation`` runtime: callers
(Application) adapt the accepted D06 Footprint bucket into the
``H01BucketInput``/``H01LevelInput`` shapes declared here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any

from ..data.models import Instant
from .artifacts import FeatureSetDefinitionIdentity
from .definitions import (
    FeatureAvailabilitySemantics,
    FeatureDefinition,
    FeatureDefinitionError,
    FeatureFinalitySemantics,
    FeatureObservation,
    FeatureObservationIdentity,
    InputContractV1,
    InputMaturity,
    ObservationLifecycle,
    OutputContract,
    OutputDimension,
    OutputValueKind,
    SemanticParameterSpec,
    SemanticParameterType,
    SupportIdentity,
    SupportReference,
)
from .imbalance import (
    DiagonalImbalanceLevel,
    ImbalanceConfig,
    PriceLevelInput,
    StackedImbalanceLevel,
    compute_diagonal_imbalance,
    compute_stacked_imbalance,
)


DIAGONAL_IMBALANCE_FEATURE_KEY = "order_flow.diagonal_imbalance"
STACKED_IMBALANCE_FEATURE_KEY = "order_flow.stacked_imbalance"

FOOTPRINT_PRICE_LEVEL_CONTRACT = InputContractV1(
    "footprint.price_level",
    "1",
    ("buy_volume", "level_index", "sell_volume"),
)

H01_IMBALANCE_FEATURE_SET_IDENTITY = FeatureSetDefinitionIdentity("h01_imbalance", 1)

_IMBALANCE_RATIO_PARAM = SemanticParameterSpec(
    "imbalance_ratio",
    SemanticParameterType.DECIMAL,
    default="3",
)
_STACKED_MIN_LEVELS_PARAM = SemanticParameterSpec(
    "stacked_min_levels",
    SemanticParameterType.INTEGER,
    default=3,
    min_value=2,
)

_DIAGONAL_OUTPUT_CONTRACT = OutputContract(
    OutputValueKind.RECORD,
    "bucket_level_diagonal_imbalance_v1",
    OutputDimension.DIMENSIONLESS,
)
_STACKED_OUTPUT_CONTRACT = OutputContract(
    OutputValueKind.RECORD,
    "bucket_level_stacked_imbalance_v1",
    OutputDimension.DIMENSIONLESS,
)


def _parameter_value(definition: FeatureDefinition, name: str) -> Any:
    for parameter in definition.canonical_parameters:
        if parameter.name == name:
            return parameter.value
    raise FeatureDefinitionError(f"missing semantic parameter: {name}")  # pragma: no cover


def _require_exclusive_lower_bound(definition: FeatureDefinition, name: str, bound: str) -> None:
    """Reject a parameter value outside its exclusive E05 domain.

    The generic E02 ``SemanticParameterSpec.min_value`` bound is inclusive, so
    it cannot itself express ``imbalance_ratio > 1``; ADR-0035 requires the
    canonical H01 constructors to enforce that exclusive bound directly.
    """

    value = Decimal(_parameter_value(definition, name))
    if value <= Decimal(bound):
        raise FeatureDefinitionError(f"{name} must be strictly greater than {bound}")


def diagonal_imbalance_definition(*, imbalance_ratio: Any = "3") -> FeatureDefinition:
    """The canonical ``order_flow.diagonal_imbalance@1`` FeatureDefinition."""

    definition = FeatureDefinition(
        feature_key=DIAGONAL_IMBALANCE_FEATURE_KEY,
        semantic_version="1",
        parameter_schema=(_IMBALANCE_RATIO_PARAM,),
        parameters={"imbalance_ratio": imbalance_ratio},
        input_contract=FOOTPRINT_PRICE_LEVEL_CONTRACT,
        support=(SupportReference.current(),),
        input_maturity=InputMaturity.FINAL_ONLY,
        availability=FeatureAvailabilitySemantics(),
        finality=FeatureFinalitySemantics(),
        output_contract=_DIAGONAL_OUTPUT_CONTRACT,
    )
    _require_exclusive_lower_bound(definition, "imbalance_ratio", "1")
    return definition


def stacked_imbalance_definition(
    *, imbalance_ratio: Any = "3", stacked_min_levels: int = 3,
) -> FeatureDefinition:
    """The canonical ``order_flow.stacked_imbalance@1`` FeatureDefinition."""

    definition = FeatureDefinition(
        feature_key=STACKED_IMBALANCE_FEATURE_KEY,
        semantic_version="1",
        parameter_schema=(_IMBALANCE_RATIO_PARAM, _STACKED_MIN_LEVELS_PARAM),
        parameters={
            "imbalance_ratio": imbalance_ratio,
            "stacked_min_levels": stacked_min_levels,
        },
        input_contract=FOOTPRINT_PRICE_LEVEL_CONTRACT,
        support=(SupportReference.current(),),
        input_maturity=InputMaturity.FINAL_ONLY,
        availability=FeatureAvailabilitySemantics(),
        finality=FeatureFinalitySemantics(),
        output_contract=_STACKED_OUTPUT_CONTRACT,
    )
    _require_exclusive_lower_bound(definition, "imbalance_ratio", "1")
    return definition


@dataclass(frozen=True, slots=True)
class H01LevelInput:
    """One Feature-owned price-level row adapted from a D06 Footprint level."""

    level_index: int
    buy_volume: str
    sell_volume: str


@dataclass(frozen=True, slots=True)
class H01BucketInput:
    """One Feature-owned concrete H01 support coordinate.

    ``bucket_observation_identity`` is the caller-supplied (Application-owned)
    exact identity of the eligible FINAL D06 Footprint bucket this coordinate
    represents; this module does not derive it, since doing so would require
    importing Representation-owned Footprint types.
    """

    bucket_observation_identity: str
    causal_available_at: Instant | str
    observed_available_at: Instant | str | None = None
    observed_finalized_at: Instant | str | None = None
    levels: tuple[H01LevelInput, ...] = ()


def _calculation_float(value: str) -> float:
    """Deterministic bounded D06 exact-decimal -> E05 float conversion.

    Local to this calculation seam only (ADR-0035 section 9): the result is
    never treated as authoritative Footprint evidence or reused to derive
    provenance/identity.
    """

    return float(value)


def _price_level_rows(bucket: H01BucketInput) -> tuple[PriceLevelInput, ...]:
    return tuple(
        PriceLevelInput(
            bar_id=bucket.bucket_observation_identity,
            level_index=level.level_index,
            buy_volume=_calculation_float(level.buy_volume),
            sell_volume=_calculation_float(level.sell_volume),
        )
        for level in bucket.levels
    )


def _require_feature_key(definition: FeatureDefinition, expected_key: str) -> None:
    if definition.feature_key != expected_key:
        raise FeatureDefinitionError(
            f"expected the canonical {expected_key!r} FeatureDefinition, got {definition.feature_key!r}"
        )


def _support_identity(definition: FeatureDefinition, bucket: H01BucketInput) -> SupportIdentity:
    return SupportIdentity(
        definition.input_contract.identity,
        bucket.bucket_observation_identity,
        SupportReference.current(),
    )


def _normalize_ratio(value: float) -> float | None:
    return None if math.isnan(value) else value


def _diagonal_record(level: DiagonalImbalanceLevel) -> dict[str, Any]:
    return {
        "level_index": level.level_index,
        "ask_imbalance_ratio": _normalize_ratio(level.ask_imbalance_ratio),
        "bid_imbalance_ratio": _normalize_ratio(level.bid_imbalance_ratio),
        "imbalance_side": level.imbalance_side,
    }


def _stacked_record(level: StackedImbalanceLevel) -> dict[str, Any]:
    return {
        "level_index": level.level_index,
        "imbalance_side": level.imbalance_side,
        "stacked_imbalance": level.stacked_imbalance,
        "stacked_run_length": level.stacked_run_length,
    }


def evaluate_diagonal_imbalance(definition: FeatureDefinition, bucket: H01BucketInput) -> FeatureObservation:
    """Evaluate the canonical Diagonal Imbalance FeatureDefinition over one
    concrete FINAL Footprint bucket coordinate."""

    _require_feature_key(definition, DIAGONAL_IMBALANCE_FEATURE_KEY)
    config = ImbalanceConfig(imbalance_ratio=float(_parameter_value(definition, "imbalance_ratio")))
    computed = compute_diagonal_imbalance(_price_level_rows(bucket), config)
    return FeatureObservation(
        definition_id=definition.definition_id,
        support_identity=_support_identity(definition, bucket),
        value=[_diagonal_record(level) for level in computed],
        lifecycle=ObservationLifecycle.FINAL,
        causal_available_at=bucket.causal_available_at,
        observed_available_at=bucket.observed_available_at,
        observed_finalized_at=bucket.observed_finalized_at,
    )


def evaluate_stacked_imbalance(definition: FeatureDefinition, bucket: H01BucketInput) -> FeatureObservation:
    """Evaluate the canonical Stacked Imbalance FeatureDefinition over one
    concrete FINAL Footprint bucket coordinate.

    Consumes the same raw price-level rows as Diagonal Imbalance directly --
    never a previously computed Diagonal ``FeatureObservation`` -- so the two
    constituents remain independent per ADR-0035.
    """

    _require_feature_key(definition, STACKED_IMBALANCE_FEATURE_KEY)
    config = ImbalanceConfig(
        imbalance_ratio=float(_parameter_value(definition, "imbalance_ratio")),
        stacked_min_levels=int(_parameter_value(definition, "stacked_min_levels")),
    )
    computed = compute_stacked_imbalance(_price_level_rows(bucket), config)
    return FeatureObservation(
        definition_id=definition.definition_id,
        support_identity=_support_identity(definition, bucket),
        value=[_stacked_record(level) for level in computed],
        lifecycle=ObservationLifecycle.FINAL,
        causal_available_at=bucket.causal_available_at,
        observed_available_at=bucket.observed_available_at,
        observed_finalized_at=bucket.observed_finalized_at,
    )


def derive_h01_expected_observation_identities(
    *,
    diagonal_definition: FeatureDefinition,
    stacked_definition: FeatureDefinition,
    bucket_observation_identities: Iterable[str],
) -> tuple[FeatureObservationIdentity, ...]:
    """The exact H01 expected observation universe (ADR-0035 section 10):
    eligible FINAL bucket identities x {DiagonalDefinitionId, StackedDefinitionId}.
    """

    identities: list[FeatureObservationIdentity] = []
    for bucket_observation_identity in bucket_observation_identities:
        for definition in (diagonal_definition, stacked_definition):
            support = SupportIdentity(
                definition.input_contract.identity,
                bucket_observation_identity,
                SupportReference.current(),
            )
            identities.append(FeatureObservationIdentity(definition.definition_id, support))
    return tuple(identities)


__all__ = [
    "DIAGONAL_IMBALANCE_FEATURE_KEY",
    "FOOTPRINT_PRICE_LEVEL_CONTRACT",
    "H01_IMBALANCE_FEATURE_SET_IDENTITY",
    "STACKED_IMBALANCE_FEATURE_KEY",
    "H01BucketInput",
    "H01LevelInput",
    "derive_h01_expected_observation_identities",
    "diagonal_imbalance_definition",
    "evaluate_diagonal_imbalance",
    "evaluate_stacked_imbalance",
    "stacked_imbalance_definition",
]
