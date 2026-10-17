"""FeatureDefinition v1 semantic identity and observation values.

This module owns the bounded E02 semantic foundation only.  It does not bind
datasets, select concrete producers, construct execution graphs, persist
feature artifacts, or discover provider infrastructure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import re
from typing import Any, ClassVar

from ..data.models import Instant
from quant_platform.canonical import canonical_bytes


FEATURE_DEFINITION_IDENTITY_DOMAIN = "feature-definition-v1"
INPUT_CONTRACT_IDENTITY_DOMAIN = "feature-input-contract-v1"
SUPPORT_IDENTITY_DOMAIN = "feature-support-v1"
FEATURE_OBSERVATION_IDENTITY_DOMAIN = "feature-observation-v1"
FEATURE_DEFINITION_MODEL_VERSION = "1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")
_PARAMETER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FeatureDefinitionError(ValueError):
    """A FeatureDefinition v1 semantic value violates the frozen contract."""


class SemanticParameterType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"
    BOOLEAN = "boolean"


class ParameterCollectionKind(StrEnum):
    SCALAR = "scalar"
    ORDERED_SEQUENCE = "ordered_sequence"
    UNORDERED_SET = "unordered_set"


class CanonicalParameterState(StrEnum):
    VALUE = "value"
    NULL = "null"
    OMITTED = "omitted"


class InputContractShape(StrEnum):
    ATOMIC = "atomic"
    COMPOSITE = "composite"


class SupportSelectorKind(StrEnum):
    CURRENT = "current"
    POINT = "point"
    WINDOW = "window"


class InputMaturity(StrEnum):
    AVAILABLE = "AVAILABLE"
    FINAL_ONLY = "FINAL_ONLY"


class ObservationLifecycle(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    FINAL = "FINAL"


class OutputValueKind(StrEnum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    RECORD = "record"


class OutputDimension(StrEnum):
    PRICE = "price"
    QUANTITY = "quantity"
    DURATION = "duration"
    DIMENSIONLESS = "dimensionless"


class NumericalEquivalenceKind(StrEnum):
    EXACT = "exact"
    QUANTIZED = "quantized"
    TOLERANT = "tolerant"


class NonObservationReason(StrEnum):
    INSUFFICIENT_SUPPORT = "insufficient_support"
    INITIALIZATION_REQUIRED = "initialization_required"
    UNSUPPORTED_SEMANTICS = "unsupported_semantics"


@dataclass(frozen=True, slots=True)
class _Missing:
    pass


_MISSING = _Missing()


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatureDefinitionError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise FeatureDefinitionError(f"{field} must not contain control characters")
    return text


def _governed_key(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise FeatureDefinitionError(
            f"{field} must be a governed canonical key spelling"
        )
    return text


def _semantic_version(value: Any, field: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field)
    if not _VERSION_RE.fullmatch(text):
        raise FeatureDefinitionError(f"{field} must be an explicit positive version")
    return text


def _parameter_name(value: Any, field: str = "parameter name") -> str:
    text = _non_empty_text(value, field)
    if not _PARAMETER_NAME_RE.fullmatch(text):
        raise FeatureDefinitionError(f"{field} must be a canonical parameter name")
    return text


def _strict_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise FeatureDefinitionError(f"{field} must be an integer")
    return value


def _positive_int(value: Any, field: str) -> int:
    result = _strict_int(value, field)
    if result < 1:
        raise FeatureDefinitionError(f"{field} must be positive")
    return result


def _enum(enum_type: Any, value: Any, field: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise FeatureDefinitionError(f"{field} is not a supported v1 value") from exc


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return hashlib.sha256(encoded).hexdigest()


def _stable_json(value: Any) -> str:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")


@dataclass(frozen=True, slots=True)
class FeatureKeyGovernance:
    """Canonical feature key plus external-boundary aliases."""

    canonical_key: str
    aliases: Iterable[str] = ()

    def __post_init__(self) -> None:
        canonical = _governed_key(self.canonical_key, "canonical_key")
        aliases = tuple(_non_empty_text(alias, "feature alias") for alias in self.aliases)
        if canonical in aliases:
            raise FeatureDefinitionError("feature aliases must not repeat canonical_key")
        duplicates = sorted({alias for alias in aliases if aliases.count(alias) > 1})
        if duplicates:
            raise FeatureDefinitionError(
                "duplicate feature aliases: " + ",".join(duplicates)
            )
        object.__setattr__(self, "canonical_key", canonical)
        object.__setattr__(self, "aliases", aliases)

    def normalize_external(self, value: str) -> str:
        text = _non_empty_text(value, "feature_key")
        if text == self.canonical_key or text in self.aliases:
            return self.canonical_key
        raise FeatureDefinitionError(f"unknown feature key or alias: {text}")


@dataclass(frozen=True, slots=True)
class CanonicalParameter:
    """One normalized semantic parameter participating in identity."""

    name: str
    state: CanonicalParameterState
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _parameter_name(self.name))
        object.__setattr__(self, "state", _enum(CanonicalParameterState, self.state, "canonical parameter state"))

    def stable_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state.value, "value": self.value}


@dataclass(frozen=True, slots=True)
class SemanticParameterSpec:
    """Small declared schema for one semantic FeatureDefinition parameter."""

    name: str
    value_type: SemanticParameterType
    required: bool = False
    default: Any = _MISSING
    allows_null: bool = False
    null_equivalent_to_omitted: bool = False
    collection: ParameterCollectionKind = ParameterCollectionKind.SCALAR
    min_value: Any = _MISSING
    max_value: Any = _MISSING

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _parameter_name(self.name))
        object.__setattr__(self, "value_type", _enum(SemanticParameterType, self.value_type, "semantic parameter type"))
        object.__setattr__(self, "collection", _enum(ParameterCollectionKind, self.collection, "parameter collection"))
        if self.required and self.default is not _MISSING:
            raise FeatureDefinitionError("required parameters must not declare defaults")
        if self.null_equivalent_to_omitted and not self.allows_null:
            raise FeatureDefinitionError(
                "null_equivalent_to_omitted requires allows_null"
            )
        if self.default is not _MISSING and self.default is not None:
            self._canonical_value(self.default, f"{self.name}.default")

    def canonicalize(self, value: Any = _MISSING) -> CanonicalParameter:
        if value is _MISSING:
            if self.default is not _MISSING:
                return CanonicalParameter(
                    self.name,
                    CanonicalParameterState.VALUE,
                    self._canonical_value(self.default, self.name),
                )
            if self.required:
                raise FeatureDefinitionError(f"missing required parameter: {self.name}")
            return CanonicalParameter(self.name, CanonicalParameterState.OMITTED)

        if value is None:
            if self.null_equivalent_to_omitted:
                if self.default is not _MISSING:
                    return CanonicalParameter(
                        self.name,
                        CanonicalParameterState.VALUE,
                        self._canonical_value(self.default, self.name),
                    )
                return CanonicalParameter(self.name, CanonicalParameterState.OMITTED)
            if not self.allows_null:
                raise FeatureDefinitionError(f"{self.name} does not allow null")
            return CanonicalParameter(self.name, CanonicalParameterState.NULL)

        return CanonicalParameter(
            self.name,
            CanonicalParameterState.VALUE,
            self._canonical_value(value, self.name),
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_type": self.value_type.value,
            "required": self.required,
            "default": (
                {"state": "none"}
                if self.default is _MISSING
                else self.canonicalize(self.default).stable_dict()
            ),
            "allows_null": self.allows_null,
            "null_equivalent_to_omitted": self.null_equivalent_to_omitted,
            "collection": self.collection.value,
            "min_value": (
                None
                if self.min_value is _MISSING
                else self._canonical_scalar(self.min_value, f"{self.name}.min_value", validate_bounds=False)
            ),
            "max_value": (
                None
                if self.max_value is _MISSING
                else self._canonical_scalar(self.max_value, f"{self.name}.max_value", validate_bounds=False)
            ),
        }

    def _canonical_value(self, value: Any, field: str) -> Any:
        if self.collection == ParameterCollectionKind.SCALAR:
            return self._canonical_scalar(value, field)
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
            raise FeatureDefinitionError(f"{field} must be a sequence")
        values = [self._canonical_scalar(item, field) for item in value]
        if self.collection == ParameterCollectionKind.ORDERED_SEQUENCE:
            return values
        unique = {_stable_json(item): item for item in values}
        if len(unique) != len(values):
            raise FeatureDefinitionError(f"{field} unordered set contains duplicates")
        return [unique[key] for key in sorted(unique)]

    def _canonical_scalar(self, value: Any, field: str, *, validate_bounds: bool = True) -> Any:
        if self.value_type == SemanticParameterType.INTEGER:
            result = _strict_int(value, field)
            if validate_bounds:
                self._validate_bounds(result, field)
            return result
        if self.value_type == SemanticParameterType.DECIMAL:
            result = self._canonical_decimal(value, field)
            if validate_bounds:
                self._validate_bounds(Decimal(result), field)
            return result
        if self.value_type == SemanticParameterType.BOOLEAN:
            if type(value) is not bool:
                raise FeatureDefinitionError(f"{field} must be boolean")
            return value
        if not isinstance(value, str):
            raise FeatureDefinitionError(f"{field} must be a string")
        if any(ord(character) < 32 for character in value):
            raise FeatureDefinitionError(f"{field} must not contain control characters")
        return value

    def _canonical_decimal(self, value: Any, field: str) -> str:
        if isinstance(value, bool) or isinstance(value, float):
            raise FeatureDefinitionError(f"{field} must be an exact decimal")
        if type(value) is int:
            decimal = Decimal(value)
        elif isinstance(value, str):
            try:
                decimal = Decimal(value)
            except InvalidOperation as exc:
                raise FeatureDefinitionError(f"{field} must be an exact decimal") from exc
        elif isinstance(value, Decimal):
            decimal = value
        else:
            raise FeatureDefinitionError(f"{field} must be an exact decimal")
        if not decimal.is_finite():
            raise FeatureDefinitionError(f"{field} must be finite")
        text = format(decimal.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text == "-0" else text

    def _validate_bounds(self, value: Any, field: str) -> None:
        if self.min_value is not _MISSING:
            minimum = self._canonical_scalar(
                self.min_value,
                f"{self.name}.min_value",
                validate_bounds=False,
            )
            if isinstance(value, Decimal):
                minimum = Decimal(minimum)
            if value < minimum:
                raise FeatureDefinitionError(f"{field} is below the declared domain")
        if self.max_value is not _MISSING:
            maximum = self._canonical_scalar(
                self.max_value,
                f"{self.name}.max_value",
                validate_bounds=False,
            )
            if isinstance(value, Decimal):
                maximum = Decimal(maximum)
            if value > maximum:
                raise FeatureDefinitionError(f"{field} is above the declared domain")


@dataclass(frozen=True, slots=True)
class FeatureDefinitionId:
    """Deterministic content-derived FeatureDefinition identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "FeatureDefinitionId")
        prefix = f"{FEATURE_DEFINITION_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or len(text.removeprefix(prefix)) != 64:
            raise FeatureDefinitionError("FeatureDefinitionId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "FeatureDefinitionId":
        return cls(
            f"{FEATURE_DEFINITION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"
        )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class InputContractV1:
    """Versioned semantic capability required by a FeatureDefinition."""

    contract_key: str
    contract_version: str | int
    required_observables: Iterable[str]
    shape: InputContractShape = InputContractShape.ATOMIC
    constituent_contracts: Iterable["InputContractV1"] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "contract_key", _governed_key(self.contract_key, "contract_key"))
        object.__setattr__(self, "contract_version", _semantic_version(self.contract_version, "contract_version"))
        object.__setattr__(self, "shape", _enum(InputContractShape, self.shape, "InputContract shape"))
        observables = tuple(sorted(_governed_key(item, "required observable") for item in self.required_observables))
        if not observables:
            raise FeatureDefinitionError("InputContract requires at least one observable")
        if len(set(observables)) != len(observables):
            raise FeatureDefinitionError("InputContract required observables must be unique")
        constituents = tuple(self.constituent_contracts)
        for index, constituent in enumerate(constituents):
            if not isinstance(constituent, InputContractV1):
                raise FeatureDefinitionError(
                    f"constituent_contracts[{index}] must be InputContractV1"
                )
        if self.shape == InputContractShape.ATOMIC and constituents:
            raise FeatureDefinitionError("atomic InputContract must not declare constituents")
        if self.shape == InputContractShape.COMPOSITE and not constituents:
            raise FeatureDefinitionError("composite InputContract requires constituents")
        object.__setattr__(self, "required_observables", observables)
        object.__setattr__(
            self,
            "constituent_contracts",
            tuple(sorted(constituents, key=lambda item: item.identity)),
        )

    @property
    def identity(self) -> str:
        return f"{INPUT_CONTRACT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": INPUT_CONTRACT_IDENTITY_DOMAIN,
            "contract_key": self.contract_key,
            "contract_version": self.contract_version,
            "required_observables": list(self.required_observables),
            "shape": self.shape.value,
            "constituent_contracts": [
                item.stable_dict() for item in self.constituent_contracts
            ],
        }


@dataclass(frozen=True, slots=True)
class SupportReference:
    """Closed v1 logical support selector."""

    kind: SupportSelectorKind
    offset: int | None = None
    end_offset: int | None = None
    length: int | None = None

    def __post_init__(self) -> None:
        kind = _enum(SupportSelectorKind, self.kind, "support selector")
        object.__setattr__(self, "kind", kind)
        if kind == SupportSelectorKind.CURRENT:
            if self.offset is not None or self.end_offset is not None or self.length is not None:
                raise FeatureDefinitionError("current() takes no offsets or length")
        elif kind == SupportSelectorKind.POINT:
            object.__setattr__(self, "offset", _strict_int(self.offset, "point offset"))
            if self.end_offset is not None or self.length is not None:
                raise FeatureDefinitionError("point() takes only offset")
        elif kind == SupportSelectorKind.WINDOW:
            object.__setattr__(self, "end_offset", _strict_int(self.end_offset, "window end_offset"))
            object.__setattr__(self, "length", _positive_int(self.length, "window length"))
            if self.offset is not None:
                raise FeatureDefinitionError("window() takes end_offset and length only")

    @classmethod
    def current(cls) -> "SupportReference":
        return cls(SupportSelectorKind.CURRENT)

    @classmethod
    def point(cls, offset: int) -> "SupportReference":
        return cls(SupportSelectorKind.POINT, offset=offset)

    @classmethod
    def window(cls, end_offset: int, length: int) -> "SupportReference":
        return cls(SupportSelectorKind.WINDOW, end_offset=end_offset, length=length)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "offset": self.offset,
            "end_offset": self.end_offset,
            "length": self.length,
            "unit": "logical_observation",
        }


@dataclass(frozen=True, slots=True)
class FeatureAvailabilitySemantics:
    causal_floor_rule: str = "after_required_support_available"
    observed_evidence_policy: str = "nullable_observed_time_never_fabricated"

    def __post_init__(self) -> None:
        object.__setattr__(self, "causal_floor_rule", _non_empty_text(self.causal_floor_rule, "causal_floor_rule"))
        object.__setattr__(
            self,
            "observed_evidence_policy",
            _non_empty_text(self.observed_evidence_policy, "observed_evidence_policy"),
        )

    def stable_dict(self) -> dict[str, str]:
        return {
            "causal_floor_rule": self.causal_floor_rule,
            "observed_evidence_policy": self.observed_evidence_policy,
        }


@dataclass(frozen=True, slots=True)
class FeatureFinalitySemantics:
    finality_rule: str = "all_required_support_final"

    def __post_init__(self) -> None:
        object.__setattr__(self, "finality_rule", _non_empty_text(self.finality_rule, "finality_rule"))

    def stable_dict(self) -> dict[str, str]:
        return {"finality_rule": self.finality_rule}


@dataclass(frozen=True, slots=True)
class InitializationSemantics:
    minimum_history: int = 0
    seed: str | None = None
    rule: str = "none"

    def __post_init__(self) -> None:
        history = _strict_int(self.minimum_history, "minimum_history")
        if history < 0:
            raise FeatureDefinitionError("minimum_history must not be negative")
        object.__setattr__(self, "minimum_history", history)
        if self.seed is not None:
            object.__setattr__(self, "seed", _non_empty_text(self.seed, "seed"))
        object.__setattr__(self, "rule", _non_empty_text(self.rule, "initialization rule"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "minimum_history": self.minimum_history,
            "seed": self.seed,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class NumericalEquivalence:
    kind: NumericalEquivalenceKind
    version: str | int = "1"
    parameters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(NumericalEquivalenceKind, self.kind, "numerical equivalence kind"))
        object.__setattr__(self, "version", _semantic_version(self.version, "numerical equivalence version"))
        parameters = {}
        for key, value in self.parameters.items():
            parameters[_parameter_name(key, "numerical equivalence parameter")] = _non_empty_text(
                value,
                "numerical equivalence parameter value",
            )
        object.__setattr__(self, "parameters", tuple(sorted(parameters.items())))

    @classmethod
    def exact(cls, version: str | int = "1") -> "NumericalEquivalence":
        return cls(NumericalEquivalenceKind.EXACT, version)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "version": self.version,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class OutputContract:
    value_kind: OutputValueKind
    shape: str
    dimension: OutputDimension
    numerical_equivalence: NumericalEquivalence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_kind", _enum(OutputValueKind, self.value_kind, "output value kind"))
        object.__setattr__(self, "shape", _non_empty_text(self.shape, "output shape"))
        object.__setattr__(self, "dimension", _enum(OutputDimension, self.dimension, "output dimension"))
        if self.value_kind == OutputValueKind.NUMERIC:
            if not isinstance(self.numerical_equivalence, NumericalEquivalence):
                raise FeatureDefinitionError("numeric output requires numerical equivalence")
        elif self.numerical_equivalence is not None:
            raise FeatureDefinitionError("non-numeric output must not declare numerical equivalence")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "value_kind": self.value_kind.value,
            "shape": self.shape,
            "dimension": self.dimension.value,
            "numerical_equivalence": (
                None
                if self.numerical_equivalence is None
                else self.numerical_equivalence.stable_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """Immutable canonical definition of one semantic observable."""

    feature_key: str
    semantic_version: str | int
    parameter_schema: Iterable[SemanticParameterSpec]
    input_contract: InputContractV1
    support: Iterable[SupportReference]
    input_maturity: InputMaturity
    availability: FeatureAvailabilitySemantics
    finality: FeatureFinalitySemantics
    output_contract: OutputContract
    parameters: Mapping[str, Any] = field(default_factory=dict)
    initialization: InitializationSemantics = field(default_factory=InitializationSemantics)

    identity_type: ClassVar[str] = "feature-definition"
    identity_version: ClassVar[str] = FEATURE_DEFINITION_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_key", _governed_key(self.feature_key, "feature_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        schema = _schema_tuple(self.parameter_schema)
        supplied = _parameter_mapping(self.parameters)
        unknown = sorted(set(supplied) - {spec.name for spec in schema})
        if unknown:
            raise FeatureDefinitionError("unknown semantic parameters: " + ",".join(unknown))
        canonical = tuple(
            spec.canonicalize(supplied[spec.name] if spec.name in supplied else _MISSING)
            for spec in schema
        )
        if not isinstance(self.input_contract, InputContractV1):
            raise FeatureDefinitionError("FeatureDefinition requires exactly one InputContractV1")
        support = tuple(self.support)
        if not support:
            raise FeatureDefinitionError("FeatureDefinition requires declared support")
        for index, item in enumerate(support):
            if not isinstance(item, SupportReference):
                raise FeatureDefinitionError(f"support[{index}] must be SupportReference")
        if not isinstance(self.availability, FeatureAvailabilitySemantics):
            raise FeatureDefinitionError("availability must be FeatureAvailabilitySemantics")
        if not isinstance(self.finality, FeatureFinalitySemantics):
            raise FeatureDefinitionError("finality must be FeatureFinalitySemantics")
        if not isinstance(self.output_contract, OutputContract):
            raise FeatureDefinitionError("output_contract must be OutputContract")
        if not isinstance(self.initialization, InitializationSemantics):
            raise FeatureDefinitionError("initialization must be InitializationSemantics")
        object.__setattr__(self, "parameter_schema", schema)
        object.__setattr__(self, "parameters", canonical)
        object.__setattr__(self, "support", support)
        object.__setattr__(self, "input_maturity", _enum(InputMaturity, self.input_maturity, "input maturity"))

    @property
    def canonical_parameters(self) -> tuple[CanonicalParameter, ...]:
        return self.parameters  # type: ignore[return-value]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "feature_key": self.feature_key,
            "semantic_version": self.semantic_version,
            "parameter_schema": [spec.stable_dict() for spec in self.parameter_schema],
            "canonical_parameters": [item.stable_dict() for item in self.canonical_parameters],
            "input_contract": self.input_contract.stable_dict(),
            "input_contract_identity": self.input_contract.identity,
            "support": [item.stable_dict() for item in self.support],
            "input_maturity": self.input_maturity.value,
            "availability": self.availability.stable_dict(),
            "finality": self.finality.stable_dict(),
            "initialization": self.initialization.stable_dict(),
            "output_contract": self.output_contract.stable_dict(),
        }

    @property
    def canonical_utf8_serialization(self) -> str:
        return canonical_bytes(self.canonical_payload(), profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")

    @property
    def definition_id(self) -> FeatureDefinitionId:
        return FeatureDefinitionId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.definition_id)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "definition_id": self.identity,
            "canonical_payload": self.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class SupportIdentity:
    """Unambiguous concrete support binding supplied by the input contract."""

    input_contract_identity: str
    observation_identity: str
    support_reference: SupportReference

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_contract_identity",
            _non_empty_text(self.input_contract_identity, "input_contract_identity"),
        )
        object.__setattr__(
            self,
            "observation_identity",
            _non_empty_text(self.observation_identity, "observation_identity"),
        )
        if not isinstance(self.support_reference, SupportReference):
            raise FeatureDefinitionError("support_reference must be SupportReference")

    @property
    def identity(self) -> str:
        return f"{SUPPORT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": SUPPORT_IDENTITY_DOMAIN,
            "input_contract_identity": self.input_contract_identity,
            "observation_identity": self.observation_identity,
            "support_reference": self.support_reference.stable_dict(),
        }


@dataclass(frozen=True, slots=True)
class FeatureObservationIdentity:
    definition_id: FeatureDefinitionId
    support_identity: SupportIdentity

    def __post_init__(self) -> None:
        if isinstance(self.definition_id, str):
            object.__setattr__(self, "definition_id", FeatureDefinitionId(self.definition_id))
        elif not isinstance(self.definition_id, FeatureDefinitionId):
            raise FeatureDefinitionError("definition_id must be FeatureDefinitionId")
        if not isinstance(self.support_identity, SupportIdentity):
            raise FeatureDefinitionError("support_identity must be SupportIdentity")

    @property
    def identity(self) -> str:
        return f"{FEATURE_OBSERVATION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"

    def stable_dict(self) -> dict[str, str]:
        return {
            "identity_domain": FEATURE_OBSERVATION_IDENTITY_DOMAIN,
            "definition_id": str(self.definition_id),
            "support_identity": self.support_identity.identity,
        }


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    """One FeatureDefinition evaluated over one concrete semantic support."""

    definition_id: FeatureDefinitionId | str
    support_identity: SupportIdentity
    value: Any
    lifecycle: ObservationLifecycle
    causal_available_at: Instant | str
    observed_available_at: Instant | str | None = None
    observed_finalized_at: Instant | str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.definition_id, str):
            object.__setattr__(self, "definition_id", FeatureDefinitionId(self.definition_id))
        elif not isinstance(self.definition_id, FeatureDefinitionId):
            raise FeatureDefinitionError("definition_id must be FeatureDefinitionId")
        if not isinstance(self.support_identity, SupportIdentity):
            raise FeatureDefinitionError("support_identity must be SupportIdentity")
        lifecycle = _enum(ObservationLifecycle, self.lifecycle, "observation lifecycle")
        object.__setattr__(self, "lifecycle", lifecycle)
        causal = Instant.parse(self.causal_available_at)
        object.__setattr__(self, "causal_available_at", causal)
        observed_available = (
            None if self.observed_available_at is None else Instant.parse(self.observed_available_at)
        )
        observed_finalized = (
            None if self.observed_finalized_at is None else Instant.parse(self.observed_finalized_at)
        )
        if observed_available is not None and observed_available < causal:
            raise FeatureDefinitionError("observed availability cannot precede causal availability floor")
        if lifecycle == ObservationLifecycle.PROVISIONAL and observed_finalized is not None:
            raise FeatureDefinitionError("PROVISIONAL observation cannot carry finalization evidence")
        if observed_finalized is not None:
            if observed_finalized < causal:
                raise FeatureDefinitionError("observed finalization cannot precede causal availability floor")
            if observed_available is not None and observed_finalized < observed_available:
                raise FeatureDefinitionError("observed finalization cannot precede observed availability")
        object.__setattr__(self, "observed_available_at", observed_available)
        object.__setattr__(self, "observed_finalized_at", observed_finalized)

    @property
    def observation_identity(self) -> FeatureObservationIdentity:
        return FeatureObservationIdentity(self.definition_id, self.support_identity)

    @property
    def identity(self) -> str:
        return self.observation_identity.identity

    def transition_to(
        self,
        lifecycle: ObservationLifecycle,
        *,
        value: Any = _MISSING,
        observed_available_at: Instant | str | None | _Missing = _MISSING,
        observed_finalized_at: Instant | str | None | _Missing = _MISSING,
    ) -> "FeatureObservation":
        next_lifecycle = _enum(ObservationLifecycle, lifecycle, "observation lifecycle")
        if self.lifecycle == ObservationLifecycle.FINAL and next_lifecycle == ObservationLifecycle.PROVISIONAL:
            raise FeatureDefinitionError("FINAL observations cannot regress to PROVISIONAL")
        return FeatureObservation(
            definition_id=self.definition_id,
            support_identity=self.support_identity,
            value=self.value if value is _MISSING else value,
            lifecycle=next_lifecycle,
            causal_available_at=self.causal_available_at,
            observed_available_at=(
                self.observed_available_at
                if observed_available_at is _MISSING
                else observed_available_at
            ),
            observed_finalized_at=(
                self.observed_finalized_at
                if observed_finalized_at is _MISSING
                else observed_finalized_at
            ),
        )


@dataclass(frozen=True, slots=True)
class NonObservation:
    """Explicit evaluation outcome for undefined support."""

    reason: NonObservationReason
    feature_definition_id: FeatureDefinitionId | str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _enum(NonObservationReason, self.reason, "non-observation reason"))
        if self.feature_definition_id is not None:
            if isinstance(self.feature_definition_id, str):
                object.__setattr__(self, "feature_definition_id", FeatureDefinitionId(self.feature_definition_id))
            elif not isinstance(self.feature_definition_id, FeatureDefinitionId):
                raise FeatureDefinitionError("feature_definition_id must be FeatureDefinitionId")
        if self.detail:
            object.__setattr__(self, "detail", _non_empty_text(self.detail, "detail"))


def _schema_tuple(values: Iterable[SemanticParameterSpec]) -> tuple[SemanticParameterSpec, ...]:
    if isinstance(values, (str, bytes, bytearray)) or values is None:
        raise FeatureDefinitionError("parameter_schema must be an iterable of specs")
    specs = tuple(values)
    for index, spec in enumerate(specs):
        if not isinstance(spec, SemanticParameterSpec):
            raise FeatureDefinitionError(f"parameter_schema[{index}] must be SemanticParameterSpec")
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise FeatureDefinitionError("duplicate parameter specs: " + ",".join(duplicates))
    return tuple(sorted(specs, key=lambda spec: spec.name))


def _parameter_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise FeatureDefinitionError("parameters must be a mapping")
    result = {}
    for key, value in values.items():
        name = _parameter_name(key)
        if name in result:
            raise FeatureDefinitionError("duplicate semantic parameter: " + name)
        result[name] = value
    return result


__all__ = [
    "FEATURE_DEFINITION_IDENTITY_DOMAIN",
    "FEATURE_DEFINITION_MODEL_VERSION",
    "FEATURE_OBSERVATION_IDENTITY_DOMAIN",
    "INPUT_CONTRACT_IDENTITY_DOMAIN",
    "SUPPORT_IDENTITY_DOMAIN",
    "CanonicalParameter",
    "CanonicalParameterState",
    "FeatureAvailabilitySemantics",
    "FeatureDefinition",
    "FeatureDefinitionError",
    "FeatureDefinitionId",
    "FeatureFinalitySemantics",
    "FeatureKeyGovernance",
    "FeatureObservation",
    "FeatureObservationIdentity",
    "InitializationSemantics",
    "InputContractShape",
    "InputContractV1",
    "InputMaturity",
    "NonObservation",
    "NonObservationReason",
    "NumericalEquivalence",
    "NumericalEquivalenceKind",
    "ObservationLifecycle",
    "OutputContract",
    "OutputDimension",
    "OutputValueKind",
    "ParameterCollectionKind",
    "SemanticParameterSpec",
    "SemanticParameterType",
    "SupportIdentity",
    "SupportReference",
    "SupportSelectorKind",
]
