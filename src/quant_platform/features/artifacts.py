"""FeatureArtifact v1: durable materialization identity for one FeatureSetDefinition.

This module owns only the bounded E04 runtime foundation: deterministic
`FeatureArtifactIdentity`, immutable bound input/source/support evidence,
explicit implementation code identity, content identity, FINAL-only
materialization eligibility, duplicate/conflict classification, and a pure
independent-recomputation equivalence helper driven by caller-supplied
constituent `FeatureDefinition`/`OutputContract` semantics.

It does not construct execution graphs, select providers, persist bytes,
decide E06 canonical H01 binding, or manage cache/storage policy.  It never
imports `quant_platform.data` beyond the shared, pure identity primitives in
`quant_platform.data.models` (`DatasetIdentity`, `NaturalPartitionIdentity`,
`CoverageInterval`, `Instant`): DataGateway, manifest loading, materializer
and publication implementation stay entirely outside this package's reach,
per the `feature` package-boundary owner.

`FeatureSetDefinition` constituent membership/order stays owned by catalog
authority (`feature_set_definitions.slug`/`.version`): E04 consumes the
existing natural key `(slug, version)` as a portable runtime identity and
never invents a second bundle hash from raw constituent feature ids.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from ..data.models import CoverageInterval, DatasetIdentity, NaturalPartitionIdentity
from .definitions import (
    FeatureDefinitionId,
    FeatureObservation,
    NumericalEquivalenceKind,
    ObservationLifecycle,
    OutputContract,
    OutputValueKind,
)


FEATURE_ARTIFACT_IDENTITY_DOMAIN = "feature-artifact-v1"
FEATURE_ARTIFACT_CONTENT_IDENTITY_DOMAIN = "feature-artifact-content-v1"
BOUND_INPUT_EVIDENCE_IDENTITY_DOMAIN = "feature-artifact-bound-input-v1"
FEATURE_ARTIFACT_MODEL_VERSION = "1"

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FeatureArtifactError(ValueError):
    """A FeatureArtifact v1 semantic value violates the frozen contract."""


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatureArtifactError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise FeatureArtifactError(f"{field_name} must not contain control characters")
    return text


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.strip().lower()):
        raise FeatureArtifactError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value.strip().lower()


def _positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise FeatureArtifactError(f"{field_name} must be a positive integer")
    return value


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_key(payload: Mapping[str, Any]) -> str:
    """Deterministic sort key for values whose dataclasses have no natural
    ordering, so caller-supplied collections canonicalize regardless of the
    order they were passed in."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class FeatureSetDefinitionIdentity:
    """Portable runtime identity for one governed FeatureSetDefinition bundle."""

    slug: str
    version: int

    def __post_init__(self) -> None:
        text = _non_empty_text(self.slug, "FeatureSetDefinitionIdentity.slug")
        if not _SLUG_RE.fullmatch(text):
            raise FeatureArtifactError(
                "FeatureSetDefinitionIdentity.slug must be a governed canonical slug spelling"
            )
        object.__setattr__(self, "slug", text)
        object.__setattr__(self, "version", _positive_int(self.version, "FeatureSetDefinitionIdentity.version"))

    @property
    def portable(self) -> str:
        return f"{self.slug}@v{self.version}"

    def stable_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "version": self.version}

    def __str__(self) -> str:
        return self.portable


@dataclass(frozen=True, slots=True)
class BoundSourcePartition:
    natural_identity: NaturalPartitionIdentity
    content_sha256: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.natural_identity, NaturalPartitionIdentity):
            raise FeatureArtifactError("natural_identity must be NaturalPartitionIdentity")
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "manifest_sha256", _sha256_hex(self.manifest_sha256, "manifest_sha256"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "natural_identity": self.natural_identity.stable_dict(),
            "content_sha256": self.content_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class BoundSourceDataset:
    dataset_identity: DatasetIdentity
    partitions: tuple[BoundSourcePartition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise FeatureArtifactError("dataset_identity must be DatasetIdentity")
        partitions = tuple(self.partitions)
        if not partitions:
            raise FeatureArtifactError("BoundSourceDataset requires at least one exact source partition")
        for index, item in enumerate(partitions):
            if not isinstance(item, BoundSourcePartition):
                raise FeatureArtifactError(f"partitions[{index}] must be BoundSourcePartition")
            if item.natural_identity.dataset_identity != self.dataset_identity:
                raise FeatureArtifactError(
                    f"partitions[{index}] natural_identity.dataset_identity does not match "
                    "the enclosing BoundSourceDataset.dataset_identity"
                )
        keys = {_canonical_key(item.natural_identity.stable_dict()) for item in partitions}
        if len(keys) != len(partitions):
            raise FeatureArtifactError("BoundSourceDataset partitions must be distinct natural identities")
        object.__setattr__(self, "partitions", tuple(sorted(partitions, key=lambda item: _canonical_key(item.stable_dict()))))

    def stable_dict(self) -> dict[str, Any]:
        return {"dataset_identity": self.dataset_identity.stable_dict(), "partitions": [item.stable_dict() for item in self.partitions]}


@dataclass(frozen=True, slots=True)
class SupportShape:
    intervals: tuple[CoverageInterval, ...]

    def __post_init__(self) -> None:
        intervals = tuple(self.intervals)
        if not intervals:
            raise FeatureArtifactError("SupportShape requires at least one interval")
        for index, item in enumerate(intervals):
            if not isinstance(item, CoverageInterval):
                raise FeatureArtifactError(f"intervals[{index}] must be CoverageInterval")
        ordered = tuple(sorted(intervals, key=lambda item: _canonical_key(item.stable_dict())))
        for previous, current in zip(ordered, ordered[1:]):
            if current.start < previous.end:
                raise FeatureArtifactError("SupportShape intervals must not overlap")
        object.__setattr__(self, "intervals", ordered)

    def stable_dict(self) -> dict[str, Any]:
        return {"intervals": [item.stable_dict() for item in self.intervals]}


@dataclass(frozen=True, slots=True)
class BoundInputEvidence:
    sources: tuple[BoundSourceDataset, ...]
    consumed_support: SupportShape
    provenance_identity: str

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        if not sources:
            raise FeatureArtifactError("BoundInputEvidence requires at least one bound source dataset")
        for index, item in enumerate(sources):
            if not isinstance(item, BoundSourceDataset):
                raise FeatureArtifactError(f"sources[{index}] must be BoundSourceDataset")
        keys = {_canonical_key(item.dataset_identity.stable_dict()) for item in sources}
        if len(keys) != len(sources):
            raise FeatureArtifactError("BoundInputEvidence sources must be distinct dataset identities")
        if not isinstance(self.consumed_support, SupportShape):
            raise FeatureArtifactError("consumed_support must be SupportShape")
        object.__setattr__(self, "provenance_identity", _non_empty_text(self.provenance_identity, "provenance_identity"))
        object.__setattr__(self, "sources", tuple(sorted(sources, key=lambda item: _canonical_key(item.stable_dict()))))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "sources": [item.stable_dict() for item in self.sources],
            "consumed_support": self.consumed_support.stable_dict(),
            "provenance_identity": self.provenance_identity,
        }

    @property
    def identity(self) -> str:
        return f"{BOUND_INPUT_EVIDENCE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class FeatureArtifactIdentity:
    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "FeatureArtifactIdentity")
        prefix = f"{FEATURE_ARTIFACT_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix):
            raise FeatureArtifactError("FeatureArtifactIdentity must be a v1 sha256 identity")
        digest = _sha256_hex(text.removeprefix(prefix), "FeatureArtifactIdentity digest")
        object.__setattr__(self, "value", f"{prefix}{digest}")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "FeatureArtifactIdentity":
        return cls(f"{FEATURE_ARTIFACT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BoundOutputPartition:
    natural_identity: NaturalPartitionIdentity
    content_sha256: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.natural_identity, NaturalPartitionIdentity):
            raise FeatureArtifactError("natural_identity must be NaturalPartitionIdentity")
        if self.natural_identity.dataset_identity.layer != "features":
            raise FeatureArtifactError("output partition dataset_identity.layer must be 'features'")
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "manifest_sha256", _sha256_hex(self.manifest_sha256, "manifest_sha256"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "natural_identity": self.natural_identity.stable_dict(),
            "content_sha256": self.content_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class FeatureArtifactContentIdentity:
    output_partitions: tuple[BoundOutputPartition, ...]
    declared_support: CoverageInterval

    def __post_init__(self) -> None:
        partitions = tuple(self.output_partitions)
        if not partitions:
            raise FeatureArtifactError("FeatureArtifactContentIdentity requires at least one bound output partition")
        for index, item in enumerate(partitions):
            if not isinstance(item, BoundOutputPartition):
                raise FeatureArtifactError(f"output_partitions[{index}] must be BoundOutputPartition")
        keys = {_canonical_key(item.natural_identity.stable_dict()) for item in partitions}
        if len(keys) != len(partitions):
            raise FeatureArtifactError("output_partitions must be distinct natural identities")
        if not isinstance(self.declared_support, CoverageInterval):
            raise FeatureArtifactError("declared_support must be CoverageInterval")
        object.__setattr__(self, "output_partitions", tuple(sorted(partitions, key=lambda item: _canonical_key(item.stable_dict()))))

    def stable_dict(self) -> dict[str, Any]:
        return {"output_partitions": [item.stable_dict() for item in self.output_partitions], "declared_support": self.declared_support.stable_dict()}

    @property
    def identity(self) -> str:
        return f"{FEATURE_ARTIFACT_CONTENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class ConstituentFeatureOutput:
    definition_id: FeatureDefinitionId
    output_contract: OutputContract

    def __post_init__(self) -> None:
        if isinstance(self.definition_id, str):
            object.__setattr__(self, "definition_id", FeatureDefinitionId(self.definition_id))
        elif not isinstance(self.definition_id, FeatureDefinitionId):
            raise FeatureArtifactError("definition_id must be FeatureDefinitionId")
        if not isinstance(self.output_contract, OutputContract):
            raise FeatureArtifactError("output_contract must be OutputContract")

    def stable_dict(self) -> dict[str, Any]:
        return {"definition_id": str(self.definition_id), "output_contract": self.output_contract.stable_dict()}


class FeatureArtifactLifecycle(StrEnum):
    FINAL = "FINAL"


def require_final_observations(
    observations: Sequence[FeatureObservation],
    *,
    constituent_output_contracts: Sequence[ConstituentFeatureOutput],
    declared_materialized_support: CoverageInterval,
) -> None:
    constituents = tuple(constituent_output_contracts)
    if not constituents:
        raise FeatureArtifactError("require_final_observations requires at least one declared constituent output contract")
    if not isinstance(declared_materialized_support, CoverageInterval):
        raise FeatureArtifactError("declared_materialized_support must be CoverageInterval")
    declared_ids = {str(item.definition_id) for item in constituents}
    items = tuple(observations)
    if not items:
        raise FeatureArtifactError("materialization requires at least one FINAL observation evidencing claimed support")
    covered_ids: set[str] = set()
    for item in items:
        if not isinstance(item, FeatureObservation):
            raise FeatureArtifactError("observations must be FeatureObservation values")
        if item.lifecycle != ObservationLifecycle.FINAL:
            raise FeatureArtifactError(f"non-FINAL observation {item.identity} cannot be sealed into a FeatureArtifact v1")
        definition_id = str(item.definition_id)
        if definition_id not in declared_ids:
            raise FeatureArtifactError(f"observation {item.identity} does not belong to any declared constituent FeatureDefinition")
        if item.causal_available_at < declared_materialized_support.start or item.causal_available_at >= declared_materialized_support.end:
            raise FeatureArtifactError(f"observation {item.identity} causal evidence falls outside declared_materialized_support")
        covered_ids.add(definition_id)
    missing = declared_ids - covered_ids
    if missing:
        raise FeatureArtifactError(f"missing FINAL observation evidence for declared constituent(s): {sorted(missing)}")


def _feature_artifact_identity_payload(
    *,
    feature_set_definition_identity: FeatureSetDefinitionIdentity,
    bound_input_evidence: BoundInputEvidence,
    declared_materialized_support: CoverageInterval,
    materialization_contract_version: str,
    implementation_code_identity: str,
) -> dict[str, Any]:
    if not isinstance(feature_set_definition_identity, FeatureSetDefinitionIdentity):
        raise FeatureArtifactError("feature_set_definition_identity must be FeatureSetDefinitionIdentity")
    if not isinstance(bound_input_evidence, BoundInputEvidence):
        raise FeatureArtifactError("bound_input_evidence must be BoundInputEvidence")
    if not isinstance(declared_materialized_support, CoverageInterval):
        raise FeatureArtifactError("declared_materialized_support must be CoverageInterval")
    return {
        "identity_domain": FEATURE_ARTIFACT_IDENTITY_DOMAIN,
        "feature_set_definition_identity": feature_set_definition_identity.stable_dict(),
        "bound_input_evidence_identity": bound_input_evidence.identity,
        "declared_materialized_support": declared_materialized_support.stable_dict(),
        "materialization_contract_version": _non_empty_text(materialization_contract_version, "materialization_contract_version"),
        "implementation_code_identity": _non_empty_text(implementation_code_identity, "implementation_code_identity"),
    }


@dataclass(frozen=True, slots=True)
class FeatureArtifact:
    feature_set_definition_identity: FeatureSetDefinitionIdentity
    bound_input_evidence: BoundInputEvidence
    declared_materialized_support: CoverageInterval
    implementation_code_identity: str
    content_identity: FeatureArtifactContentIdentity
    constituent_output_contracts: tuple[ConstituentFeatureOutput, ...]
    materialization_contract_version: str = FEATURE_ARTIFACT_MODEL_VERSION
    lifecycle: FeatureArtifactLifecycle = FeatureArtifactLifecycle.FINAL
    physical_locators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.feature_set_definition_identity, FeatureSetDefinitionIdentity):
            raise FeatureArtifactError("feature_set_definition_identity must be FeatureSetDefinitionIdentity")
        if not isinstance(self.bound_input_evidence, BoundInputEvidence):
            raise FeatureArtifactError("bound_input_evidence must be BoundInputEvidence")
        if not isinstance(self.declared_materialized_support, CoverageInterval):
            raise FeatureArtifactError("declared_materialized_support must be CoverageInterval")
        object.__setattr__(self, "implementation_code_identity", _non_empty_text(self.implementation_code_identity, "implementation_code_identity"))
        object.__setattr__(self, "materialization_contract_version", _non_empty_text(self.materialization_contract_version, "materialization_contract_version"))
        if not isinstance(self.content_identity, FeatureArtifactContentIdentity):
            raise FeatureArtifactError("content_identity must be FeatureArtifactContentIdentity")
        for index, item in enumerate(self.content_identity.output_partitions):
            output_identity = item.natural_identity.dataset_identity
            if output_identity.feature_set_slug != self.feature_set_definition_identity.slug or output_identity.feature_set_version != self.feature_set_definition_identity.version:
                raise FeatureArtifactError(f"content_identity.output_partitions[{index}] does not belong to the declared FeatureSetDefinitionIdentity natural key")
        if self.content_identity.declared_support != self.declared_materialized_support:
            raise FeatureArtifactError("content_identity.declared_support must equal declared_materialized_support")
        lifecycle = self.lifecycle
        if isinstance(lifecycle, str):
            try:
                lifecycle = FeatureArtifactLifecycle(lifecycle)
            except ValueError as exc:
                raise FeatureArtifactError(f"unknown FeatureArtifact lifecycle: {lifecycle!r}") from exc
        if lifecycle != FeatureArtifactLifecycle.FINAL:
            raise FeatureArtifactError("FeatureArtifact v1 durability is FINAL-only")
        object.__setattr__(self, "lifecycle", lifecycle)
        constituents = tuple(self.constituent_output_contracts)
        if not constituents:
            raise FeatureArtifactError("FeatureArtifact requires at least one constituent output-contract identity")
        for index, item in enumerate(constituents):
            if not isinstance(item, ConstituentFeatureOutput):
                raise FeatureArtifactError(f"constituent_output_contracts[{index}] must be ConstituentFeatureOutput")
        definition_ids = {str(item.definition_id) for item in constituents}
        if len(definition_ids) != len(constituents):
            raise FeatureArtifactError("constituent_output_contracts must be distinct FeatureDefinitionId values")
        object.__setattr__(self, "constituent_output_contracts", tuple(sorted(constituents, key=lambda item: str(item.definition_id))))
        object.__setattr__(self, "physical_locators", tuple(self.physical_locators))
        for index, item in enumerate(self.physical_locators):
            _non_empty_text(item, f"physical_locators[{index}]")

    def identity_payload(self) -> dict[str, Any]:
        return _feature_artifact_identity_payload(
            feature_set_definition_identity=self.feature_set_definition_identity,
            bound_input_evidence=self.bound_input_evidence,
            declared_materialized_support=self.declared_materialized_support,
            materialization_contract_version=self.materialization_contract_version,
            implementation_code_identity=self.implementation_code_identity,
        )

    @property
    def identity(self) -> FeatureArtifactIdentity:
        return FeatureArtifactIdentity.from_payload(self.identity_payload())

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity": str(self.identity),
            "feature_set_definition_identity": self.feature_set_definition_identity.stable_dict(),
            "bound_input_evidence": self.bound_input_evidence.stable_dict(),
            "declared_materialized_support": self.declared_materialized_support.stable_dict(),
            "materialization_contract_version": self.materialization_contract_version,
            "implementation_code_identity": self.implementation_code_identity,
            "content_identity": self.content_identity.stable_dict(),
            "lifecycle": self.lifecycle.value,
            "constituent_output_contracts": [item.stable_dict() for item in self.constituent_output_contracts],
            "physical_locators": list(self.physical_locators),
        }

    def relocated(self, *, physical_locators: Iterable[str]) -> "FeatureArtifact":
        return FeatureArtifact(
            feature_set_definition_identity=self.feature_set_definition_identity,
            bound_input_evidence=self.bound_input_evidence,
            declared_materialized_support=self.declared_materialized_support,
            implementation_code_identity=self.implementation_code_identity,
            content_identity=self.content_identity,
            constituent_output_contracts=self.constituent_output_contracts,
            materialization_contract_version=self.materialization_contract_version,
            lifecycle=self.lifecycle,
            physical_locators=tuple(physical_locators),
        )

    def output_contract_for(self, definition_id: FeatureDefinitionId | str) -> OutputContract:
        target = str(definition_id)
        for item in self.constituent_output_contracts:
            if str(item.definition_id) == target:
                return item.output_contract
        raise FeatureArtifactError(f"no constituent output contract for {target}")


def seal_feature_artifact(
    *,
    feature_set_definition_identity: FeatureSetDefinitionIdentity,
    bound_input_evidence: BoundInputEvidence,
    declared_materialized_support: CoverageInterval,
    implementation_code_identity: str,
    content_identity: FeatureArtifactContentIdentity,
    constituent_output_contracts: Sequence[ConstituentFeatureOutput],
    observations: Sequence[FeatureObservation],
    materialization_contract_version: str = FEATURE_ARTIFACT_MODEL_VERSION,
    physical_locators: Sequence[str] = (),
) -> FeatureArtifact:
    constituents = tuple(constituent_output_contracts)
    require_final_observations(
        observations,
        constituent_output_contracts=constituents,
        declared_materialized_support=declared_materialized_support,
    )
    return FeatureArtifact(
        feature_set_definition_identity=feature_set_definition_identity,
        bound_input_evidence=bound_input_evidence,
        declared_materialized_support=declared_materialized_support,
        implementation_code_identity=implementation_code_identity,
        content_identity=content_identity,
        constituent_output_contracts=constituents,
        materialization_contract_version=materialization_contract_version,
        physical_locators=tuple(physical_locators),
    )


def verify_matches_request(
    artifact: FeatureArtifact,
    *,
    feature_set_definition_identity: FeatureSetDefinitionIdentity,
    bound_input_evidence: BoundInputEvidence,
    declared_materialized_support: CoverageInterval,
    implementation_code_identity: str,
    content_identity: FeatureArtifactContentIdentity,
    constituent_output_contracts: Sequence[ConstituentFeatureOutput],
    materialization_contract_version: str = FEATURE_ARTIFACT_MODEL_VERSION,
) -> None:
    """Verify all trusted pre-payload metadata, including durable content and constituents."""

    expected_identity = FeatureArtifactIdentity.from_payload(
        _feature_artifact_identity_payload(
            feature_set_definition_identity=feature_set_definition_identity,
            bound_input_evidence=bound_input_evidence,
            declared_materialized_support=declared_materialized_support,
            materialization_contract_version=materialization_contract_version,
            implementation_code_identity=implementation_code_identity,
        )
    )
    if artifact.identity != expected_identity:
        raise FeatureArtifactError(
            "loaded artifact identity does not match the exact requested feature-set/support/source/code/contract-version binding"
        )
    if not isinstance(content_identity, FeatureArtifactContentIdentity):
        raise FeatureArtifactError("trusted content_identity must be FeatureArtifactContentIdentity")
    if artifact.content_identity != content_identity:
        raise FeatureArtifactError("loaded artifact content_identity does not match the trusted request")

    constituents = tuple(constituent_output_contracts)
    if not constituents:
        raise FeatureArtifactError("trusted constituent_output_contracts must not be empty")
    for index, item in enumerate(constituents):
        if not isinstance(item, ConstituentFeatureOutput):
            raise FeatureArtifactError(
                f"trusted constituent_output_contracts[{index}] must be ConstituentFeatureOutput"
            )
    definition_ids = {str(item.definition_id) for item in constituents}
    if len(definition_ids) != len(constituents):
        raise FeatureArtifactError("trusted constituent_output_contracts must contain distinct FeatureDefinitionId values")
    expected_constituents = tuple(sorted(constituents, key=lambda item: str(item.definition_id)))
    if artifact.constituent_output_contracts != expected_constituents:
        raise FeatureArtifactError(
            "loaded artifact constituent output-contract evidence does not match the trusted request"
        )


class ArtifactRegistrationOutcome(StrEnum):
    IDEMPOTENT_DUPLICATE = "IDEMPOTENT_DUPLICATE"
    CONFLICT = "CONFLICT"


def classify_registration(*, existing: FeatureArtifact, candidate: FeatureArtifact) -> ArtifactRegistrationOutcome:
    if existing.identity != candidate.identity:
        raise FeatureArtifactError("classify_registration requires existing and candidate to share FeatureArtifactIdentity")
    if existing.content_identity == candidate.content_identity:
        return ArtifactRegistrationOutcome.IDEMPOTENT_DUPLICATE
    return ArtifactRegistrationOutcome.CONFLICT


def _to_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise FeatureArtifactError(f"{field_name} must be numeric, not boolean")
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
            raise FeatureArtifactError(f"{field_name} is not a valid numeric value: {value!r}") from exc
    else:
        raise FeatureArtifactError(f"{field_name} has unsupported numeric type: {type(value).__name__}")
    if not result.is_finite():
        raise FeatureArtifactError(f"{field_name} must be finite")
    return result


def _equivalence_parameters(equivalence: Any) -> dict[str, str]:
    return dict(equivalence.parameters)


_SUPPORTED_NUMERICAL_EQUIVALENCE_VERSIONS = frozenset({"1"})


def _quantize_to_multiple(value: Decimal, quantum: Decimal) -> Decimal:
    return (value / quantum).to_integral_value(rounding=ROUND_HALF_EVEN) * quantum


def values_semantically_equivalent(output_contract: OutputContract, left: Any, right: Any) -> bool:
    if output_contract.value_kind != OutputValueKind.NUMERIC:
        return left == right
    equivalence = output_contract.numerical_equivalence
    if equivalence is None:
        raise FeatureArtifactError("numeric output_contract requires numerical_equivalence")
    if equivalence.version not in _SUPPORTED_NUMERICAL_EQUIVALENCE_VERSIONS:
        raise FeatureArtifactError(
            f"unsupported numerical equivalence version {equivalence.version!r} for kind {equivalence.kind}"
        )
    left_value = _to_decimal(left, "left")
    right_value = _to_decimal(right, "right")
    if equivalence.kind == NumericalEquivalenceKind.EXACT:
        return left_value == right_value
    if equivalence.kind == NumericalEquivalenceKind.QUANTIZED:
        parameters = _equivalence_parameters(equivalence)
        quantum_text = parameters.get("quantum")
        if quantum_text is None:
            raise FeatureArtifactError("QUANTIZED numerical equivalence requires a 'quantum' parameter")
        quantum = _to_decimal(quantum_text, "quantum")
        if quantum <= 0:
            raise FeatureArtifactError("QUANTIZED 'quantum' parameter must be positive")
        return _quantize_to_multiple(left_value, quantum) == _quantize_to_multiple(right_value, quantum)
    if equivalence.kind == NumericalEquivalenceKind.TOLERANT:
        parameters = _equivalence_parameters(equivalence)
        absolute_text = parameters.get("absolute")
        relative_text = parameters.get("relative")
        if absolute_text is None and relative_text is None:
            raise FeatureArtifactError("TOLERANT numerical equivalence requires an 'absolute' and/or 'relative' parameter")
        absolute = _to_decimal(absolute_text, "absolute") if absolute_text is not None else Decimal(0)
        relative = _to_decimal(relative_text, "relative") if relative_text is not None else Decimal(0)
        if absolute < 0 or relative < 0:
            raise FeatureArtifactError("TOLERANT parameters must not be negative")
        threshold = absolute + relative * max(abs(left_value), abs(right_value))
        return abs(left_value - right_value) <= threshold
    raise FeatureArtifactError(f"unsupported numerical equivalence kind: {equivalence.kind}")


def recomputation_equivalent(
    *,
    output_contracts: Mapping[FeatureDefinitionId, OutputContract],
    left: Sequence[FeatureObservation],
    right: Sequence[FeatureObservation],
) -> bool:
    left_by_id = _keyed_by_identity(left, "left")
    right_by_id = _keyed_by_identity(right, "right")
    if not left_by_id or not right_by_id:
        return False
    if set(left_by_id) != set(right_by_id):
        return False
    for key, left_observation in left_by_id.items():
        right_observation = right_by_id[key]
        definition_id = left_observation.definition_id
        contract = output_contracts.get(definition_id)
        if contract is None:
            raise FeatureArtifactError(f"no governing OutputContract supplied for {definition_id}")
        if not values_semantically_equivalent(contract, left_observation.value, right_observation.value):
            return False
    return True


def _keyed_by_identity(observations: Sequence[FeatureObservation], side: str) -> dict[str, FeatureObservation]:
    result: dict[str, FeatureObservation] = {}
    for item in observations:
        if not isinstance(item, FeatureObservation):
            raise FeatureArtifactError(f"{side} must contain only FeatureObservation values")
        if item.identity in result:
            raise FeatureArtifactError(f"{side} contains duplicate observation identity {item.identity}")
        result[item.identity] = item
    return result


__all__ = [
    "FEATURE_ARTIFACT_IDENTITY_DOMAIN",
    "FEATURE_ARTIFACT_CONTENT_IDENTITY_DOMAIN",
    "BOUND_INPUT_EVIDENCE_IDENTITY_DOMAIN",
    "FEATURE_ARTIFACT_MODEL_VERSION",
    "ArtifactRegistrationOutcome",
    "BoundInputEvidence",
    "BoundOutputPartition",
    "BoundSourceDataset",
    "BoundSourcePartition",
    "ConstituentFeatureOutput",
    "FeatureArtifact",
    "FeatureArtifactContentIdentity",
    "FeatureArtifactError",
    "FeatureArtifactIdentity",
    "FeatureArtifactLifecycle",
    "FeatureSetDefinitionIdentity",
    "SupportShape",
    "classify_registration",
    "recomputation_equivalent",
    "require_final_observations",
    "seal_feature_artifact",
    "values_semantically_equivalent",
    "verify_matches_request",
]
