"""I04 supervised input selection and anti-leakage projection.

This module owns only the Learning-side projection from already-governed
Feature/F07/Validation values into a deterministic supervised input universe.
It deliberately reuses Validation's fold, availability, purge, embargo and
lockbox seams instead of defining competing temporal authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
from types import MappingProxyType
from typing import Any

from quant_platform.data.models import CoverageInterval, Instant
from quant_platform.features.definitions import FeatureObservation, ObservationLifecycle
from quant_platform.validation.availability import (
    CandidateClassification,
    DependencyCutoffRole,
    DependencyEvidence,
    DependencyLifecycle,
    DependencyMaturity,
    Embargo,
    ValidationCandidate,
    classify_candidate,
)
from quant_platform.validation.labels import LabelResult, LabelStatus, as_training_dependency_evidence
from quant_platform.validation.lockbox import Lockbox, LockboxError
from quant_platform.validation.walk_forward import WalkForwardFold


LEARNING_SELECTION_POLICY_IDENTITY_DOMAIN = "supervised-selection-policy-v1"
LEARNING_SAMPLE_IDENTITY_DOMAIN = "supervised-sample-v1"
LEARNING_PROJECTION_IDENTITY_DOMAIN = "supervised-projection-v1"
LEARNING_MODEL_VERSION = "1"


class LearningError(ValueError):
    """A supervised learning input value violates I04 semantics."""


class ProjectionSide(StrEnum):
    TRAIN = "train"
    TEST = "test"


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise LearningError("decimal values must be finite")
        return str(value)
    if isinstance(value, Fraction):
        return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    if isinstance(value, Instant):
        return value.isoformat()
    if isinstance(value, CoverageInterval):
        return value.stable_dict()
    if hasattr(value, "stable_dict"):
        payload = value.stable_dict()
        if not isinstance(payload, Mapping):
            raise LearningError("stable_dict() must return a mapping")
        return _canonical_value(dict(payload))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise LearningError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise LearningError(f"unsupported canonical value type: {type(value).__name__}")


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise LearningError(f"{field_name} must not contain control characters")
    return text


def _positive_version(value: str | int, field_name: str) -> str:
    text = str(value) if type(value) is int else _text(value, field_name)
    if not text.isdecimal() or int(text) < 1:
        raise LearningError(f"{field_name} must be a positive integer version")
    return text


@dataclass(frozen=True, slots=True)
class SupervisedSelectionPolicy:
    """Deterministic I04 selection policy identity.

    The policy is intentionally small: the actual availability, purge,
    embargo and lockbox decisions are delegated to Validation-owned seams.
    """

    policy_key: str
    semantic_version: str | int
    implementation_code_identity: str
    require_labeled_targets: bool = True
    sample_uniqueness_version: str | int = "1"
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _text(self.policy_key, "policy_key"))
        object.__setattr__(self, "semantic_version", _positive_version(self.semantic_version, "semantic_version"))
        object.__setattr__(
            self,
            "implementation_code_identity",
            _text(self.implementation_code_identity, "implementation_code_identity"),
        )
        if type(self.require_labeled_targets) is not bool:
            raise LearningError("require_labeled_targets must be a boolean")
        object.__setattr__(
            self,
            "sample_uniqueness_version",
            _positive_version(self.sample_uniqueness_version, "sample_uniqueness_version"),
        )
        if self.notes is not None:
            object.__setattr__(self, "notes", _text(self.notes, "notes"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": LEARNING_SELECTION_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "implementation_code_identity": self.implementation_code_identity,
            "require_labeled_targets": self.require_labeled_targets,
            "sample_uniqueness_version": self.sample_uniqueness_version,
            "notes": self.notes,
        }

    @property
    def identity(self) -> str:
        return f"{LEARNING_SELECTION_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SupervisedFeatureInput:
    """One selected feature value plus explicit support evidence for F06."""

    observation: FeatureObservation
    support: CoverageInterval
    contemporaneous_version_proven: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.observation, FeatureObservation):
            raise LearningError("observation must be a FeatureObservation")
        if not isinstance(self.support, CoverageInterval):
            raise LearningError("support must be a CoverageInterval")
        if self.support.start >= self.support.end:
            raise LearningError("feature support must be non-empty")
        if type(self.contemporaneous_version_proven) is not bool:
            raise LearningError("contemporaneous_version_proven must be a boolean")

    @property
    def dependency_evidence(self) -> DependencyEvidence:
        lifecycle = (
            DependencyLifecycle.FINAL
            if self.observation.lifecycle is ObservationLifecycle.FINAL
            else DependencyLifecycle.PROVISIONAL
        )
        return DependencyEvidence(
            identity=self.observation.identity,
            cutoff_role=DependencyCutoffRole.DECISION_TIME,
            support=self.support,
            required_maturity=DependencyMaturity.AVAILABLE,
            lifecycle=lifecycle,
            causal_available_at=self.observation.causal_available_at,
            observed_available_at=self.observation.observed_available_at,
            observed_finalized_at=self.observation.observed_finalized_at,
            contemporaneous_version_proven=self.contemporaneous_version_proven,
            detail="feature_input",
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "observation_identity": self.observation.identity,
            "definition_id": str(self.observation.definition_id),
            "support_identity": self.observation.support_identity.identity,
            "support": self.support.stable_dict(),
            "value": _canonical_value(self.observation.value),
            "lifecycle": self.observation.lifecycle.value,
            "causal_available_at": self.observation.causal_available_at.isoformat(),
            "observed_available_at": (
                None
                if self.observation.observed_available_at is None
                else self.observation.observed_available_at.isoformat()
            ),
            "observed_finalized_at": (
                None
                if self.observation.observed_finalized_at is None
                else self.observation.observed_finalized_at.isoformat()
            ),
            "contemporaneous_version_proven": self.contemporaneous_version_proven,
        }


@dataclass(frozen=True, slots=True)
class SupervisedSampleCandidate:
    """One candidate supervised sample before Validation classification."""

    sample_id: str
    decision_time: Instant | str
    features: Sequence[SupervisedFeatureInput]
    label: LabelResult
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _text(self.sample_id, "sample_id"))
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        features = tuple(self.features)
        if not features:
            raise LearningError("sample candidate requires at least one feature")
        for index, feature in enumerate(features):
            if not isinstance(feature, SupervisedFeatureInput):
                raise LearningError(f"features[{index}] must be SupervisedFeatureInput")
        feature_ids = [feature.observation.identity for feature in features]
        if len(set(feature_ids)) != len(feature_ids):
            raise LearningError("sample candidate features must be distinct observations")
        object.__setattr__(self, "features", tuple(sorted(features, key=lambda item: item.observation.identity)))
        if not isinstance(self.label, LabelResult):
            raise LearningError("label must be a LabelResult")
        if not isinstance(self.metadata, Mapping):
            raise LearningError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(_canonical_value(dict(self.metadata))))

    @property
    def dependencies(self) -> tuple[DependencyEvidence, ...]:
        return tuple(feature.dependency_evidence for feature in self.features) + (
            as_training_dependency_evidence(self.label),
        )

    def validation_candidate(self) -> ValidationCandidate:
        return ValidationCandidate(d=self.decision_time, dependencies=self.dependencies)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "decision_time": self.decision_time.isoformat(),
            "features": [feature.stable_dict() for feature in self.features],
            "label_result_id": self.label.result_id,
            "label_status": self.label.status.value,
            "label_value": _canonical_value(self.label.value),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SupervisedProjectionSample:
    sample_id: str
    side: ProjectionSide
    decision_time: Instant
    feature_inputs: tuple[SupervisedFeatureInput, ...]
    label: LabelResult
    validation_classification: CandidateClassification
    sample_uniqueness_weight: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _text(self.sample_id, "sample_id"))
        object.__setattr__(self, "side", ProjectionSide(self.side))
        if not isinstance(self.decision_time, Instant):
            raise LearningError("decision_time must be an Instant")
        if self.validation_classification is not CandidateClassification.ADMITTED:
            raise LearningError("SupervisedProjectionSample requires ADMITTED classification")
        _fraction_text(self.sample_uniqueness_weight, "sample_uniqueness_weight")

    @property
    def sample_identity(self) -> str:
        return f"{LEARNING_SAMPLE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self._identity_payload())}"

    @property
    def identity(self) -> str:
        return self.sample_identity

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["sample_uniqueness_weight"] = self.sample_uniqueness_weight
        if include_identity:
            payload["sample_identity"] = self.sample_identity
        return payload

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": LEARNING_SAMPLE_IDENTITY_DOMAIN,
            "sample_id": self.sample_id,
            "side": self.side.value,
            "decision_time": self.decision_time.isoformat(),
            "feature_inputs": [feature.stable_dict() for feature in self.feature_inputs],
            "label_result_id": self.label.result_id,
            "label_value": _canonical_value(self.label.value),
        }


@dataclass(frozen=True, slots=True)
class SupervisedProjectionRejection:
    sample_id: str
    decision_time: Instant
    reason: str
    detail: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _text(self.sample_id, "sample_id"))
        if not isinstance(self.decision_time, Instant):
            raise LearningError("decision_time must be an Instant")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        details = tuple(_text(item, "detail") for item in self.detail)
        object.__setattr__(self, "detail", details)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "decision_time": self.decision_time.isoformat(),
            "reason": self.reason,
            "detail": list(self.detail),
        }


@dataclass(frozen=True, slots=True)
class SupervisedProjection:
    fold: WalkForwardFold
    embargo: Embargo
    selection_policy: SupervisedSelectionPolicy
    samples: tuple[SupervisedProjectionSample, ...]
    rejections: tuple[SupervisedProjectionRejection, ...] = ()
    lockbox_identity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fold, WalkForwardFold):
            raise LearningError("fold must be a WalkForwardFold")
        if not isinstance(self.embargo, Embargo):
            raise LearningError("embargo must be an Embargo")
        if not isinstance(self.selection_policy, SupervisedSelectionPolicy):
            raise LearningError("selection_policy must be a SupervisedSelectionPolicy")
        samples = tuple(self.samples)
        sample_identities = [sample.sample_identity for sample in samples]
        if len(set(sample_identities)) != len(sample_identities):
            raise LearningError("projection sample identities must be unique")
        object.__setattr__(
            self,
            "samples",
            tuple(sorted(samples, key=lambda item: (item.decision_time.epoch_ns, item.sample_id))),
        )
        rejections = tuple(self.rejections)
        object.__setattr__(
            self,
            "rejections",
            tuple(sorted(rejections, key=lambda item: (item.decision_time.epoch_ns, item.sample_id))),
        )
        if self.lockbox_identity is not None:
            object.__setattr__(self, "lockbox_identity", _text(self.lockbox_identity, "lockbox_identity"))

    @property
    def projection_identity(self) -> str:
        return f"{LEARNING_PROJECTION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    @property
    def identity(self) -> str:
        return self.projection_identity

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": LEARNING_PROJECTION_IDENTITY_DOMAIN,
            "model_version": LEARNING_MODEL_VERSION,
            "fold": self.fold.stable_dict(),
            "embargo": self.embargo.stable_dict(),
            "selection_policy": {
                "identity": self.selection_policy.identity,
                "payload": self.selection_policy.stable_dict(),
            },
            "lockbox_identity": self.lockbox_identity,
            "samples": [sample.stable_dict() for sample in self.samples],
            "rejections": [rejection.stable_dict() for rejection in self.rejections],
        }
        if include_identity:
            payload["projection_identity"] = self.projection_identity
        return payload


def build_supervised_projection(
    *,
    fold: WalkForwardFold,
    embargo: Embargo,
    selection_policy: SupervisedSelectionPolicy,
    candidates: Sequence[SupervisedSampleCandidate],
    lockbox: Lockbox | None = None,
) -> SupervisedProjection:
    """Build a deterministic supervised projection from candidate samples.

    Unavailable, purged, embargoed, censored, insufficient or lockbox-
    contaminated candidates are explicit rejections. Only Validation-admitted
    samples receive H12 uniqueness weights, computed over that admitted
    temporally admissible universe.
    """

    if not isinstance(fold, WalkForwardFold):
        raise LearningError("fold must be a WalkForwardFold")
    if not isinstance(embargo, Embargo):
        raise LearningError("embargo must be an Embargo")
    if not isinstance(selection_policy, SupervisedSelectionPolicy):
        raise LearningError("selection_policy must be a SupervisedSelectionPolicy")
    if lockbox is not None and not isinstance(lockbox, Lockbox):
        raise LearningError("lockbox must be a Lockbox")

    items = tuple(candidates)
    seen_sample_ids: set[str] = set()
    admitted: list[tuple[SupervisedSampleCandidate, CandidateClassification]] = []
    rejections: list[SupervisedProjectionRejection] = []

    for index, candidate in enumerate(items):
        if not isinstance(candidate, SupervisedSampleCandidate):
            raise LearningError(f"candidates[{index}] must be SupervisedSampleCandidate")
        if candidate.sample_id in seen_sample_ids:
            raise LearningError(f"duplicate sample_id: {candidate.sample_id}")
        seen_sample_ids.add(candidate.sample_id)

        validation_candidate = candidate.validation_candidate()
        if lockbox is not None:
            try:
                lockbox.check_development_candidate(validation_candidate)
            except LockboxError as exc:
                rejections.append(
                    SupervisedProjectionRejection(
                        sample_id=candidate.sample_id,
                        decision_time=candidate.decision_time,
                        reason="LOCKBOX_CONTAMINATION",
                        detail=(str(exc),),
                    )
                )
                continue

        if selection_policy.require_labeled_targets and candidate.label.status is not LabelStatus.LABELED:
            rejections.append(
                SupervisedProjectionRejection(
                    sample_id=candidate.sample_id,
                    decision_time=candidate.decision_time,
                    reason="LABEL_NOT_AVAILABLE",
                    detail=(candidate.label.status.value,),
                )
            )
            continue

        result = classify_candidate(fold, embargo, validation_candidate)
        if result.classification is CandidateClassification.ADMITTED:
            admitted.append((candidate, result.classification))
        else:
            rejections.append(
                SupervisedProjectionRejection(
                    sample_id=candidate.sample_id,
                    decision_time=candidate.decision_time,
                    reason=result.classification.value,
                    detail=tuple(result.reasons),
                )
            )

    weights = _sample_uniqueness_weights(tuple(candidate for candidate, _ in admitted))
    samples = tuple(
        SupervisedProjectionSample(
            sample_id=candidate.sample_id,
            side=ProjectionSide.TRAIN if candidate.decision_time < fold.test.start else ProjectionSide.TEST,
            decision_time=candidate.decision_time,
            feature_inputs=tuple(candidate.features),
            label=candidate.label,
            validation_classification=classification,
            sample_uniqueness_weight=weights[candidate.sample_id],
        )
        for candidate, classification in admitted
    )
    return SupervisedProjection(
        fold=fold,
        embargo=embargo,
        selection_policy=selection_policy,
        samples=samples,
        rejections=tuple(rejections),
        lockbox_identity=None if lockbox is None else lockbox.identity,
    )


def _sample_uniqueness_weights(candidates: tuple[SupervisedSampleCandidate, ...]) -> dict[str, str]:
    intervals: dict[str, CoverageInterval] = {}
    for candidate in candidates:
        target = as_training_dependency_evidence(candidate.label)
        if target.support is None or target.support.start >= target.support.end:
            intervals[candidate.sample_id] = CoverageInterval(candidate.decision_time, candidate.decision_time)
        else:
            intervals[candidate.sample_id] = target.support

    weights: dict[str, str] = {}
    for sample_id, interval in intervals.items():
        duration = interval.end.epoch_ns - interval.start.epoch_ns
        if duration <= 0:
            weights[sample_id] = "0"
            continue
        boundaries = {interval.start.epoch_ns, interval.end.epoch_ns}
        for other in intervals.values():
            if other.start < interval.end and interval.start < other.end:
                boundaries.add(max(interval.start.epoch_ns, other.start.epoch_ns))
                boundaries.add(min(interval.end.epoch_ns, other.end.epoch_ns))
        ordered = sorted(boundaries)
        total = Fraction(0, 1)
        for left, right in zip(ordered, ordered[1:]):
            if left == right:
                continue
            concurrency = sum(
                1
                for other in intervals.values()
                if other.start.epoch_ns <= left and right <= other.end.epoch_ns
            )
            if concurrency <= 0:
                raise LearningError("sample uniqueness interval has zero concurrency")
            total += Fraction(right - left, duration) * Fraction(1, concurrency)
        weights[sample_id] = _fraction_to_text(total)
    return weights


def _fraction_text(value: Any, field_name: str) -> Fraction:
    try:
        result = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise LearningError(f"{field_name} must be a canonical fraction") from exc
    return result


def _fraction_to_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


__all__ = [
    "LEARNING_PROJECTION_IDENTITY_DOMAIN",
    "LEARNING_SAMPLE_IDENTITY_DOMAIN",
    "LearningError",
    "ProjectionSide",
    "SupervisedFeatureInput",
    "SupervisedProjection",
    "SupervisedProjectionRejection",
    "SupervisedProjectionSample",
    "SupervisedSampleCandidate",
    "SupervisedSelectionPolicy",
    "build_supervised_projection",
]
