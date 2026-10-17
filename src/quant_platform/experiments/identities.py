"""Typed Study/Trial/Run/Artifact identities for the Experiment System.

This module owns only immutable semantic identity values and deterministic
fingerprints.  It deliberately does not allocate execution ids, persist rows,
schedule jobs, read artifacts or discover filesystem/object-store content.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import math
from typing import Any, ClassVar

from quant_platform.canonical import canonical_bytes


class ExperimentIdentityError(ValueError):
    """An experiment identity value violates its semantic contract."""


_JSON_OBJECT = "json-object"
_JSON_ARRAY = "json-array"
_FORBIDDEN_RUN_CONFIG_KEYS = frozenset({
    "attempt",
    "attempt_number",
    "cache_location",
    "completed_at",
    "created_at",
    "cwd",
    "database_id",
    "directory",
    "execution_id",
    "file_path",
    "host",
    "hostname",
    "log_location",
    "object_store_url",
    "path",
    "pid",
    "retry",
    "retry_count",
    "row_id",
    "started_at",
    "temp_path",
    "temporary_filename",
    "temporary_path",
    "timestamp",
    "tmp_path",
    "workdir",
    "working_directory",
})


def _non_empty_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentIdentityError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ExperimentIdentityError(f"{field} must not contain control characters")
    return text


def _dimension_name(value: str, field: str = "trial dimension") -> str:
    text = _non_empty_text(value, field)
    if any(character.isspace() for character in text):
        raise ExperimentIdentityError(f"{field} must not contain whitespace")
    return text


def _freeze_json(value: Any, field: str) -> Any:
    """Return an immutable JSON value accepted by the fingerprint convention."""

    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ExperimentIdentityError(f"{field} must be finite JSON")
        return value
    if isinstance(value, Mapping):
        items = []
        seen = set()
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ExperimentIdentityError(f"{field} mapping keys must be non-empty strings")
            if key in seen:
                raise ExperimentIdentityError(f"{field} contains a duplicate mapping key: {key}")
            seen.add(key)
            items.append((key, _freeze_json(item, f"{field}.{key}")))
        return (_JSON_OBJECT, tuple(sorted(items, key=lambda pair: pair[0])))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return (_JSON_ARRAY, tuple(_freeze_json(item, field) for item in value))
    raise ExperimentIdentityError(f"{field} must be JSON-compatible")


def _reject_runtime_locator_config(value: Any, field: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_RUN_CONFIG_KEYS:
                    raise ExperimentIdentityError(
                        f"{field}.{key} is runtime locator or attempt metadata"
                    )
            _reject_runtime_locator_config(item, f"{field}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_runtime_locator_config(item, f"{field}[{index}]")


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and value[0] == _JSON_OBJECT:
        return {key: _json_value(item) for key, item in value[1]}
    if isinstance(value, tuple) and len(value) == 2 and value[0] == _JSON_ARRAY:
        return [_json_value(item) for item in value[1]]
    return value


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return hashlib.sha256(encoded).hexdigest()


def _require_reference(value: Any, field: str) -> "IdentityReference":
    if not isinstance(value, IdentityReference):
        raise ExperimentIdentityError(f"{field} must be an IdentityReference")
    return value


def _reference_tuple(values: Iterable["IdentityReference"], field: str) -> tuple["IdentityReference", ...]:
    if isinstance(values, (str, bytes, bytearray)) or values is None:
        raise ExperimentIdentityError(f"{field} must be an iterable of IdentityReference values")
    result = tuple(values)
    for index, value in enumerate(result):
        _require_reference(value, f"{field}[{index}]")
    return result


@dataclass(frozen=True, slots=True)
class IdentityReference:
    """Opaque identity supplied by another bounded context."""

    identity_kind: str
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity_kind", _non_empty_text(self.identity_kind, "identity_kind"))
        object.__setattr__(self, "identity", _non_empty_text(self.identity, "identity"))

    def stable_dict(self) -> dict[str, str]:
        return {"identity_kind": self.identity_kind, "identity": self.identity}


@dataclass(frozen=True, slots=True)
class ComparisonProtocolIdentity:
    """Comparison protocol identity plus the Trial dimensions it declares."""

    identity: IdentityReference
    trial_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_reference(self.identity, "comparison protocol identity")
        dimensions = tuple(_dimension_name(value) for value in self.trial_dimensions)
        duplicates = sorted({value for value in dimensions if dimensions.count(value) > 1})
        if duplicates:
            raise ExperimentIdentityError(
                "comparison protocol declares duplicate trial dimensions: "
                + ",".join(duplicates)
            )
        object.__setattr__(self, "trial_dimensions", dimensions)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.stable_dict(),
            "trial_dimensions": list(self.trial_dimensions),
        }


@dataclass(frozen=True, slots=True)
class StudyIdentity:
    """The research question/protocol and comparison universe."""

    study_definition_version: str
    research_identity: IdentityReference
    evaluation_objective_identity: IdentityReference
    comparison_protocol_identity: ComparisonProtocolIdentity

    identity_type: ClassVar[str] = "study"
    identity_version: ClassVar[str] = "1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "study_definition_version",
            _non_empty_text(self.study_definition_version, "study_definition_version"),
        )
        _require_reference(self.research_identity, "research_identity")
        _require_reference(self.evaluation_objective_identity, "evaluation_objective_identity")
        if not isinstance(self.comparison_protocol_identity, ComparisonProtocolIdentity):
            raise ExperimentIdentityError(
                "comparison_protocol_identity must be a ComparisonProtocolIdentity"
            )

    @property
    def declared_trial_dimensions(self) -> tuple[str, ...]:
        return self.comparison_protocol_identity.trial_dimensions

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "study_definition_version": self.study_definition_version,
            "research_identity": self.research_identity.stable_dict(),
            "evaluation_objective_identity": self.evaluation_objective_identity.stable_dict(),
            "comparison_protocol_identity": self.comparison_protocol_identity.stable_dict(),
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class TrialDimensionAssignment:
    """One declared Study dimension assigned for a Trial."""

    dimension: str
    value: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimension", _dimension_name(self.dimension))
        object.__setattr__(self, "value", _freeze_json(self.value, self.dimension))

    def stable_value(self) -> Any:
        return _json_value(self.value)


def _assignment_tuple(values: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> tuple[TrialDimensionAssignment, ...]:
    if isinstance(values, Mapping):
        raw_items = list(values.items())
    elif isinstance(values, (str, bytes, bytearray)) or values is None:
        raise ExperimentIdentityError("trial assignments must be a mapping or iterable of pairs")
    else:
        raw_items = []
        for item in values:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)) or len(item) != 2:
                raise ExperimentIdentityError("trial assignments must be pairs")
            raw_items.append((item[0], item[1]))

    seen = set()
    assignments = []
    duplicates = set()
    for name, value in raw_items:
        dimension = _dimension_name(name)
        if dimension in seen:
            duplicates.add(dimension)
        seen.add(dimension)
        assignments.append(TrialDimensionAssignment(dimension, value))
    if duplicates:
        raise ExperimentIdentityError(
            "duplicate trial dimension assignments: " + ",".join(sorted(duplicates))
        )
    return tuple(assignments)


@dataclass(frozen=True, slots=True)
class TrialIdentity:
    """A Study plus exactly one assignment for every declared Trial dimension."""

    study_identity: StudyIdentity
    assignments: Mapping[str, Any] | Iterable[tuple[str, Any]]

    identity_type: ClassVar[str] = "trial"
    identity_version: ClassVar[str] = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.study_identity, StudyIdentity):
            raise ExperimentIdentityError("study_identity must be a StudyIdentity")
        assignments = _assignment_tuple(self.assignments)
        declared = set(self.study_identity.declared_trial_dimensions)
        supplied = {assignment.dimension for assignment in assignments}
        missing = sorted(declared - supplied)
        undeclared = sorted(supplied - declared)
        if missing:
            raise ExperimentIdentityError("missing trial dimension assignments: " + ",".join(missing))
        if undeclared:
            raise ExperimentIdentityError("undeclared trial dimension assignments: " + ",".join(undeclared))
        order = {dimension: index for index, dimension in enumerate(self.study_identity.declared_trial_dimensions)}
        object.__setattr__(
            self,
            "assignments",
            tuple(sorted(assignments, key=lambda assignment: order[assignment.dimension])),
        )

    def stable_assignments(self) -> dict[str, Any]:
        return {
            assignment.dimension: assignment.stable_value()
            for assignment in self.assignments
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "study_identity": self.study_identity.fingerprint,
            "trial_assignments": self.stable_assignments(),
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class RunSpecIdentity:
    """Deterministic reproducibility fingerprint for one executable Trial spec."""

    trial_identity: TrialIdentity
    code_identity: IdentityReference
    data_identities: Iterable[IdentityReference] = ()
    feature_identities: Iterable[IdentityReference] = ()
    research_label_identities: Iterable[IdentityReference] = ()
    validation_identity: IdentityReference | None = None
    strategy_policy_execution_identities: Iterable[IdentityReference] = ()
    environment_identity: IdentityReference | None = None
    run_configuration: Mapping[str, Any] = field(default_factory=dict)

    identity_type: ClassVar[str] = "run-spec"
    identity_version: ClassVar[str] = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.trial_identity, TrialIdentity):
            raise ExperimentIdentityError("trial_identity must be a TrialIdentity")
        _require_reference(self.code_identity, "code_identity")
        if self.validation_identity is not None:
            _require_reference(self.validation_identity, "validation_identity")
        if self.environment_identity is not None:
            _require_reference(self.environment_identity, "environment_identity")
        object.__setattr__(self, "data_identities", _reference_tuple(self.data_identities, "data_identities"))
        object.__setattr__(
            self,
            "feature_identities",
            _reference_tuple(self.feature_identities, "feature_identities"),
        )
        object.__setattr__(
            self,
            "research_label_identities",
            _reference_tuple(self.research_label_identities, "research_label_identities"),
        )
        object.__setattr__(
            self,
            "strategy_policy_execution_identities",
            _reference_tuple(
                self.strategy_policy_execution_identities,
                "strategy_policy_execution_identities",
            ),
        )
        object.__setattr__(
            self,
            "run_configuration",
            _freeze_json(self.run_configuration, "run_configuration"),
        )
        _reject_runtime_locator_config(
            _json_value(self.run_configuration),
            "run_configuration",
        )

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "trial_identity": self.trial_identity.fingerprint,
            "data_identities": [value.stable_dict() for value in self.data_identities],
            "feature_identities": [value.stable_dict() for value in self.feature_identities],
            "research_label_identities": [
                value.stable_dict() for value in self.research_label_identities
            ],
            "validation_identity": (
                None if self.validation_identity is None else self.validation_identity.stable_dict()
            ),
            "strategy_policy_execution_identities": [
                value.stable_dict() for value in self.strategy_policy_execution_identities
            ],
            "code_identity": self.code_identity.stable_dict(),
            "environment_identity": (
                None if self.environment_identity is None else self.environment_identity.stable_dict()
            ),
            "explicit_run_configuration": _json_value(self.run_configuration),
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """One concrete execution of a RunSpec."""

    run_spec_identity: RunSpecIdentity
    execution_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_spec_identity, RunSpecIdentity):
            raise ExperimentIdentityError("run_spec_identity must be a RunSpecIdentity")
        object.__setattr__(self, "execution_id", _non_empty_text(self.execution_id, "execution_id"))

    def stable_dict(self) -> dict[str, str]:
        return {
            "run_spec_identity": self.run_spec_identity.fingerprint,
            "execution_id": self.execution_id,
        }


@dataclass(frozen=True, slots=True)
class ArtifactContentIdentity:
    """Content-equivalence identity supplied by the producing boundary."""

    artifact_kind: str
    artifact_schema_version: str
    content_identity: str

    identity_type: ClassVar[str] = "artifact-content"
    identity_version: ClassVar[str] = "1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_kind", _non_empty_text(self.artifact_kind, "artifact_kind"))
        object.__setattr__(
            self,
            "artifact_schema_version",
            _non_empty_text(self.artifact_schema_version, "artifact_schema_version"),
        )
        object.__setattr__(
            self,
            "content_identity",
            _non_empty_text(self.content_identity, "content_identity"),
        )

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "artifact_kind": self.artifact_kind,
            "artifact_schema_version": self.artifact_schema_version,
            "content_identity": self.content_identity,
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """A Run-produced artifact role plus content identity and provenance."""

    run_identity: RunIdentity
    artifact_role: str
    artifact_content_identity: ArtifactContentIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.run_identity, RunIdentity):
            raise ExperimentIdentityError("run_identity must be a RunIdentity")
        object.__setattr__(self, "artifact_role", _non_empty_text(self.artifact_role, "artifact_role"))
        if not isinstance(self.artifact_content_identity, ArtifactContentIdentity):
            raise ExperimentIdentityError(
                "artifact_content_identity must be an ArtifactContentIdentity"
            )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "run_identity": self.run_identity.stable_dict(),
            "artifact_role": self.artifact_role,
            "artifact_content_identity": self.artifact_content_identity.fingerprint,
        }


__all__ = [
    "ArtifactContentIdentity",
    "ArtifactIdentity",
    "ComparisonProtocolIdentity",
    "ExperimentIdentityError",
    "IdentityReference",
    "RunIdentity",
    "RunSpecIdentity",
    "StudyIdentity",
    "TrialDimensionAssignment",
    "TrialIdentity",
]
