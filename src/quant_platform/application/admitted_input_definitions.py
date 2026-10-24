"""Server registry of canonical feature definitions for ``admit`` (issue #463, ADR-0073 section 5).

A deck result must be attributable to a feature definition the *server* knows.
``admit`` may therefore name definitions; the server canonicalizes each one
with its own library at its own commit and seals the identities in the
manifest's ``definition_identities`` (ADR-0072 section 1, Amendment 2).

The registry is a closed table in code: a ``FeatureDefinition`` is code plus
parameters (ADR-0016), not a catalog row, and nothing persists single
definitions. v1 has one entry, ``order_flow.failed_aggression`` version ``1``.
Adding an entry is a code change at the deck-pinned commit and needs no wire
change.

Everything here is pure: no store, no gateway, no clock. A request is checked
in :func:`canonicalize_definitions`, which raises :class:`DefinitionRefusal`
with one of eight stable reasons; the transport maps that to
``invalid_request`` before anything is resolved or sealed. The server attests
that it *admitted a request that named these definitions*; it does not
evaluate the deck's feature code (ADR-0069 section 7).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any

from ..data.models import DataGatewayError, Instant
from ..features.definitions import FeatureDefinition, FeatureDefinitionError
from ..features.failed_aggression import FAILED_AGGRESSION_FEATURE_KEY, failed_aggression_definition
from ..representation.bar_flow import BarFlowDefinitionV1
from ..representation.candles import parse_duration_ns
from .market_data import TRADES_V1_KIND, TRADES_V1_VERSION, ConsumerMarketDataQuery

MAX_DEFINITIONS = 8
MAX_PARAMETERS = 16

REASON_EXCEED_BOUND = "definitions_exceed_bound"
REASON_UNKNOWN = "definition_unknown"
REASON_PARAMETERS = "definition_parameters_invalid"
REASON_BAR_DURATION = "definition_bar_duration_invalid"
REASON_MISALIGNED = "definition_interval_misaligned"
REASON_DURATION_MIXED = "definition_bar_duration_mixed"
REASON_DUPLICATE = "definition_duplicate"
REASON_INPUT_UNAVAILABLE = "definition_input_unavailable"
DEFINITION_REFUSAL_REASONS = (
    REASON_EXCEED_BOUND, REASON_UNKNOWN, REASON_PARAMETERS, REASON_BAR_DURATION,
    REASON_MISALIGNED, REASON_DURATION_MIXED, REASON_DUPLICATE, REASON_INPUT_UNAVAILABLE,
)

# A wire decimal is plain digits: no sign, exponent, whitespace, NaN or Infinity. Its length is
# the message bound's; the precision and the domain are the canonical constructor's to refuse.
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class DefinitionRefusal(ValueError):
    """The request names definitions the server refuses; ``reason`` is a stable token."""

    def __init__(self, reason: str):
        if reason not in DEFINITION_REFUSAL_REASONS:
            raise ValueError("unknown definition refusal reason")
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _RegistryRow:
    """What the server knows about one definition: its inputs, parameters and constructor."""

    parameter_kinds: Mapping[str, str]  # name -> "decimal" | "integer"
    bar_flow: bool  # needs a bar-flow-v1 representation (hence a bar_duration)
    dataset_kind: str
    dataset_version: int
    build: Callable[[Mapping[str, Any]], FeatureDefinition]


def _failed_aggression(parameters: Mapping[str, Any]) -> FeatureDefinition:
    return failed_aggression_definition(**parameters)


_REGISTRY: Mapping[tuple[str, str], _RegistryRow] = MappingProxyType({
    (FAILED_AGGRESSION_FEATURE_KEY, "1"): _RegistryRow(
        parameter_kinds=MappingProxyType({"imbalance_threshold": "decimal", "wick_ratio": "decimal"}),
        bar_flow=True,
        dataset_kind=TRADES_V1_KIND,
        dataset_version=TRADES_V1_VERSION,
        build=_failed_aggression,
    ),
})
REGISTERED_DEFINITIONS = tuple(sorted(_REGISTRY))


@dataclass(frozen=True, slots=True)
class DefinitionRequest:
    """One canonicalized entry of ``definitions``.

    Produced by :func:`canonicalize_definitions` only: ``parameters`` holds the
    parameters the entry supplied, sorted by name (an omitted parameter takes
    the definition's default), and ``bar_duration_ns`` is the duration compared
    by nanosecond value, so ``"1m"`` and ``"60s"`` are one entry.
    """

    feature_key: str
    semantic_version: str
    bar_duration_ns: int | None
    parameters: tuple[tuple[str, str | int], ...]

    def feature_definition(self) -> FeatureDefinition:
        row = _REGISTRY.get((self.feature_key, self.semantic_version))
        if row is None:
            raise DefinitionRefusal(REASON_UNKNOWN)
        return _build(row, dict(self.parameters))

    def bar_flow_definition(self) -> BarFlowDefinitionV1 | None:
        if self.bar_duration_ns is None:
            return None
        try:
            return BarFlowDefinitionV1(self.bar_duration_ns)
        except (DataGatewayError, ValueError) as error:
            raise DefinitionRefusal(REASON_BAR_DURATION) from error

    def identities(self) -> tuple[str, ...]:
        bar_flow = self.bar_flow_definition()
        feature = self.feature_definition().identity
        return (feature,) if bar_flow is None else (bar_flow.definition_identity, feature)


def definition_identities(definitions: Sequence[DefinitionRequest]) -> tuple[str, ...]:
    """The manifest's ``definition_identities``: sorted, deduplicated identity strings."""
    return tuple(sorted({identity for item in definitions for identity in item.identities()}))


def _build(row: _RegistryRow, parameters: Mapping[str, Any]) -> FeatureDefinition:
    try:
        return row.build(parameters)
    except (FeatureDefinitionError, ArithmeticError, TypeError, ValueError) as error:
        raise DefinitionRefusal(REASON_PARAMETERS) from error


def _parameters(row: _RegistryRow, value: Any) -> tuple[tuple[str, str | int], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or len(value) > MAX_PARAMETERS:
        raise DefinitionRefusal(REASON_PARAMETERS)
    supplied: dict[str, str | int] = {}
    for name, item in value.items():
        kind = row.parameter_kinds.get(name) if type(name) is str else None
        if kind == "decimal" and type(item) is str and _DECIMAL.fullmatch(item):
            supplied[name] = item
        elif kind == "integer" and type(item) is int:
            supplied[name] = item
        else:
            raise DefinitionRefusal(REASON_PARAMETERS)
    _build(row, supplied)  # the declared domain is the constructor's
    return tuple(sorted(supplied.items()))


def _bar_duration_ns(row: _RegistryRow, value: Any) -> int | None:
    if not row.bar_flow:
        if value is not None:
            raise DefinitionRefusal(REASON_BAR_DURATION)
        return None
    if type(value) is not str:
        raise DefinitionRefusal(REASON_BAR_DURATION)
    try:
        return BarFlowDefinitionV1(parse_duration_ns(value)).duration_ns
    except (DataGatewayError, ValueError, ArithmeticError) as error:
        raise DefinitionRefusal(REASON_BAR_DURATION) from error


def _require_aligned(dataset: ConsumerMarketDataQuery, duration_ns: int) -> None:
    try:
        start, end = Instant.parse(dataset.start), Instant.parse(dataset.end)
    except DataGatewayError as error:
        raise ValueError("the requested interval is not a UTC interval") from error
    if start.epoch_ns % duration_ns or end.epoch_ns % duration_ns:
        raise DefinitionRefusal(REASON_MISALIGNED)


def _entry(entry: Mapping[str, Any], dataset: ConsumerMarketDataQuery | None) -> DefinitionRequest:
    key, version = entry["feature_key"], entry["semantic_version"]
    row = _REGISTRY.get((key, version)) if type(key) is str and type(version) is str else None
    if row is None:
        raise DefinitionRefusal(REASON_UNKNOWN)
    representation = None if dataset is None else dataset.representation
    if (
        representation is None
        or representation.kind != row.dataset_kind
        or type(representation.version) is not int
        or representation.version != row.dataset_version
    ):
        raise DefinitionRefusal(REASON_INPUT_UNAVAILABLE)
    parameters = _parameters(row, entry.get("parameters"))
    duration = _bar_duration_ns(row, entry.get("bar_duration"))
    if duration is not None:
        _require_aligned(dataset, duration)
    return DefinitionRequest(key, version, duration, parameters)


def canonicalize_definitions(
    entries: Sequence[Mapping[str, Any]], dataset: ConsumerMarketDataQuery | None,
) -> tuple[DefinitionRequest, ...]:
    """Validate the ``definitions`` of an ``admit`` request against the registry.

    Each entry is checked in order (unknown, input, parameters, bar duration,
    alignment); only then the entries together: one bar duration for the whole
    admission (ADR-0073 Amendment 1) and no two entries with the same identities.
    """

    if len(entries) > MAX_DEFINITIONS:
        raise DefinitionRefusal(REASON_EXCEED_BOUND)
    requests = tuple(_entry(entry, dataset) for entry in entries)
    if len({item.bar_duration_ns for item in requests if item.bar_duration_ns is not None}) > 1:
        raise DefinitionRefusal(REASON_DURATION_MIXED)
    features = [item.feature_definition().identity for item in requests]
    if len(set(features)) != len(features):
        raise DefinitionRefusal(REASON_DUPLICATE)
    return requests
