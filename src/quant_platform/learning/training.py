"""I05 deterministic supervised training, evaluation and artifact emission.

The v1 runtime intentionally implements one dependency-free baseline:
``centroid_classifier_v1``.  It consumes I04 projections and emits I03/I02
Experiment artifacts; it does not introduce a model registry, job runtime,
strategy integration or physical artifact store.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
import math
from typing import Any

from quant_platform.experiments import (
    ArtifactContentIdentity,
    ArtifactIdentity,
    ArtifactRegistration,
    ExperimentRepository,
    MetricDefinition,
    MetricDirection,
    MetricResult,
    RunIdentity,
    RunRecord,
    RunState,
    TrialAttemptResult,
    record_trial_attempt,
)

from .supervised import ProjectionSide, SupervisedProjection, SupervisedProjectionSample


SUPERVISED_TRAINING_POLICY_IDENTITY_DOMAIN = "supervised-training-policy-v1"
SUPERVISED_NORMALIZER_IDENTITY_DOMAIN = "supervised-fold-normalizer-v1"
SUPERVISED_MODEL_IDENTITY_DOMAIN = "supervised-centroid-model-v1"
SUPERVISED_PREDICTION_ARTIFACT_SCHEMA = "supervised-predictions-v1"
SUPERVISED_METRIC_ARTIFACT_SCHEMA = "supervised-metrics-v1"
SUPERVISED_MODEL_ARTIFACT_SCHEMA = "supervised-model-v1"
SUPERVISED_NORMALIZER_ARTIFACT_SCHEMA = "supervised-normalizer-v1"


class SupervisedTrainingError(ValueError):
    """A supervised training/evaluation value violates I05 semantics."""


class SupervisedModelFamily(StrEnum):
    CENTROID_CLASSIFIER_V1 = "centroid_classifier_v1"


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SupervisedTrainingError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise SupervisedTrainingError(f"{field_name} must not contain control characters")
    return text


def _positive_version(value: str | int, field_name: str) -> str:
    text = str(value) if type(value) is int else _text(value, field_name)
    if not text.isdecimal() or int(text) < 1:
        raise SupervisedTrainingError(f"{field_name} must be a positive integer version")
    return text


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SupervisedTrainingError(f"{field_name} must be a finite decimal")
    if isinstance(value, Fraction):
        result = Decimal(value.numerator) / Decimal(value.denominator)
    elif isinstance(value, Decimal):
        result = value
    elif isinstance(value, str) and "/" in value:
        try:
            fraction = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise SupervisedTrainingError(f"{field_name} must be a finite decimal") from exc
        result = Decimal(fraction.numerator) / Decimal(fraction.denominator)
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise SupervisedTrainingError(f"{field_name} must be a finite decimal") from exc
    if not result.is_finite():
        raise SupervisedTrainingError(f"{field_name} must be a finite decimal")
    return result


def _decimal_text(value: Any, field_name: str = "decimal") -> str:
    text = format(_decimal(value, field_name).normalize(), "f")
    return "0" if text == "-0" else text


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise SupervisedTrainingError("float values must be finite")
        return _decimal_text(value)
    if isinstance(value, (Decimal, Fraction)):
        return _decimal_text(value)
    if hasattr(value, "stable_dict"):
        payload = value.stable_dict()
        if not isinstance(payload, Mapping):
            raise SupervisedTrainingError("stable_dict() must return a mapping")
        return _canonical_value(dict(payload))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise SupervisedTrainingError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise SupervisedTrainingError(f"unsupported canonical value type: {type(value).__name__}")


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _matrix(samples: Sequence[SupervisedProjectionSample]) -> tuple[tuple[Decimal, ...], ...]:
    rows = []
    for sample in samples:
        row = tuple(_decimal(value, f"sample {sample.sample_id} feature") for value in sample.feature_values)
        if not row:
            raise SupervisedTrainingError("training samples require at least one feature")
        rows.append(row)
    return tuple(rows)


def _labels(samples: Sequence[SupervisedProjectionSample]) -> tuple[str, ...]:
    return tuple(str(sample.label.value) for sample in samples)


def _weights(samples: Sequence[SupervisedProjectionSample]) -> tuple[Decimal, ...]:
    return tuple(_decimal(sample.sample_uniqueness_weight, "sample_uniqueness_weight") for sample in samples)


def _artifact_content(kind: str, schema: str, payload: Mapping[str, Any]) -> ArtifactContentIdentity:
    content_identity = f"{schema}:sha256:{_canonical_fingerprint(payload)}"
    return ArtifactContentIdentity(kind, schema, content_identity)


@dataclass(frozen=True, slots=True)
class SupervisedTrainingPolicy:
    """Deterministic I05 model/evaluation policy identity."""

    policy_key: str
    semantic_version: str | int
    implementation_code_identity: str
    model_family: SupervisedModelFamily | str = SupervisedModelFamily.CENTROID_CLASSIFIER_V1
    declared_class_labels: Sequence[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _text(self.policy_key, "policy_key"))
        object.__setattr__(self, "semantic_version", _positive_version(self.semantic_version, "semantic_version"))
        object.__setattr__(
            self,
            "implementation_code_identity",
            _text(self.implementation_code_identity, "implementation_code_identity"),
        )
        object.__setattr__(self, "model_family", SupervisedModelFamily(self.model_family))
        if self.declared_class_labels is not None:
            labels = tuple(_text(label, "declared_class_label") for label in self.declared_class_labels)
            if len(set(labels)) != len(labels):
                raise SupervisedTrainingError("declared_class_labels must be distinct")
            object.__setattr__(self, "declared_class_labels", labels)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": SUPERVISED_TRAINING_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "implementation_code_identity": self.implementation_code_identity,
            "model_family": self.model_family.value,
            "declared_class_labels": None if self.declared_class_labels is None else list(self.declared_class_labels),
        }

    @property
    def identity(self) -> str:
        return f"{SUPERVISED_TRAINING_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class FoldNormalizer:
    """Fold-local normalizer fitted only on training samples."""

    feature_schema: tuple[str, ...]
    means: tuple[str, ...]
    scales: tuple[str, ...]
    fitted_sample_ids: tuple[str, ...]

    @classmethod
    def fit(cls, feature_schema: Sequence[str], samples: Sequence[SupervisedProjectionSample]) -> "FoldNormalizer":
        schema = tuple(_text(item, "feature_schema") for item in feature_schema)
        rows = _matrix(samples)
        if not rows:
            raise SupervisedTrainingError("normalizer requires at least one training sample")
        width = len(rows[0])
        if len(schema) != width:
            raise SupervisedTrainingError("feature_schema width does not match training rows")
        for row in rows:
            if len(row) != width:
                raise SupervisedTrainingError("training rows must have stable width")
        means = []
        scales = []
        count = Decimal(len(rows))
        for column in range(width):
            values = [row[column] for row in rows]
            mean = sum(values, Decimal(0)) / count
            variance = sum((value - mean) * (value - mean) for value in values) / count
            with localcontext() as context:
                context.prec = 50
                scale = variance.sqrt() if variance > 0 else Decimal(1)
            if scale.copy_abs() < Decimal("1e-12"):
                scale = Decimal(1)
            means.append(_decimal_text(mean))
            scales.append(_decimal_text(scale))
        return cls(
            feature_schema=schema,
            means=tuple(means),
            scales=tuple(scales),
            fitted_sample_ids=tuple(sample.sample_id for sample in samples),
        )

    def __post_init__(self) -> None:
        schema = tuple(_text(item, "feature_schema") for item in self.feature_schema)
        means = tuple(_decimal_text(item, "normalizer mean") for item in self.means)
        scales = tuple(_decimal_text(item, "normalizer scale") for item in self.scales)
        if not schema or len(schema) != len(means) or len(schema) != len(scales):
            raise SupervisedTrainingError("normalizer schema, means and scales must have equal non-zero length")
        if any(_decimal(scale, "normalizer scale") <= 0 for scale in scales):
            raise SupervisedTrainingError("normalizer scales must be positive")
        object.__setattr__(self, "feature_schema", schema)
        object.__setattr__(self, "means", means)
        object.__setattr__(self, "scales", scales)
        object.__setattr__(self, "fitted_sample_ids", tuple(_text(item, "fitted_sample_id") for item in self.fitted_sample_ids))

    def transform_matrix(self, rows: Sequence[Sequence[Any]]) -> tuple[tuple[str, ...], ...]:
        means = tuple(_decimal(item, "normalizer mean") for item in self.means)
        scales = tuple(_decimal(item, "normalizer scale") for item in self.scales)
        transformed = []
        for row_index, row in enumerate(tuple(rows)):
            values = tuple(_decimal(value, f"row {row_index} feature") for value in row)
            if len(values) != len(means):
                raise SupervisedTrainingError("transformed row width does not match normalizer")
            transformed.append(tuple(_decimal_text((value - mean) / scale) for value, mean, scale in zip(values, means, scales)))
        return tuple(transformed)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": SUPERVISED_NORMALIZER_IDENTITY_DOMAIN,
            "feature_schema": list(self.feature_schema),
            "means": list(self.means),
            "scales": list(self.scales),
            "fitted_sample_ids": list(self.fitted_sample_ids),
        }

    @property
    def identity(self) -> str:
        return f"{SUPERVISED_NORMALIZER_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class CentroidClassifierModel:
    policy_identity: str
    feature_schema: tuple[str, ...]
    class_labels: tuple[str, ...]
    observed_class_labels: tuple[str, ...]
    centroids: Mapping[str, tuple[str, ...]]
    normalizer: FoldNormalizer
    train_sample_ids: tuple[str, ...]

    @classmethod
    def fit(
        cls,
        *,
        policy: SupervisedTrainingPolicy,
        projection: SupervisedProjection,
        normalizer: FoldNormalizer,
        train_samples: Sequence[SupervisedProjectionSample],
    ) -> "CentroidClassifierModel":
        labels = _labels(train_samples)
        if not labels:
            raise SupervisedTrainingError("model training requires at least one training label")
        declared = policy.declared_class_labels
        class_labels = declared or tuple(sorted(set(labels)))
        unknown = sorted(set(labels) - set(class_labels))
        if unknown:
            raise SupervisedTrainingError("training labels outside declared_class_labels: " + ",".join(unknown))
        rows = normalizer.transform_matrix(_matrix(train_samples))
        weights = _weights(train_samples)
        centroids: dict[str, tuple[str, ...]] = {}
        for class_label in sorted(set(labels)):
            class_rows = [
                (tuple(_decimal(value, "normalized feature") for value in row), weight)
                for row, label, weight in zip(rows, labels, weights)
                if label == class_label
            ]
            total_weight = sum((weight for _, weight in class_rows), Decimal(0))
            if total_weight <= 0:
                raise SupervisedTrainingError("class training weight must be positive")
            width = len(class_rows[0][0])
            centroid = []
            for column in range(width):
                centroid.append(
                    _decimal_text(
                        sum(row[column] * weight for row, weight in class_rows) / total_weight
                    )
                )
            centroids[class_label] = tuple(centroid)
        return cls(
            policy_identity=policy.identity,
            feature_schema=tuple(projection.feature_schema),
            class_labels=tuple(class_labels),
            observed_class_labels=tuple(sorted(set(labels))),
            centroids=centroids,
            normalizer=normalizer,
            train_sample_ids=tuple(sample.sample_id for sample in train_samples),
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_identity", _text(self.policy_identity, "policy_identity"))
        feature_schema = tuple(_text(item, "feature_schema") for item in self.feature_schema)
        class_labels = tuple(_text(item, "class_label") for item in self.class_labels)
        observed = tuple(_text(item, "observed_class_label") for item in self.observed_class_labels)
        if not feature_schema:
            raise SupervisedTrainingError("model feature_schema must not be empty")
        if not class_labels or len(set(class_labels)) != len(class_labels):
            raise SupervisedTrainingError("model class_labels must be non-empty and distinct")
        if not set(observed).issubset(set(class_labels)):
            raise SupervisedTrainingError("observed class labels must be a subset of class_labels")
        centroids = {}
        for label, row in self.centroids.items():
            label_text = _text(label, "centroid label")
            if label_text not in observed:
                raise SupervisedTrainingError("centroid label must be observed during training")
            values = tuple(_decimal_text(value, "centroid value") for value in row)
            if len(values) != len(feature_schema):
                raise SupervisedTrainingError("centroid width must match feature_schema")
            centroids[label_text] = values
        if set(centroids) != set(observed):
            raise SupervisedTrainingError("centroids must cover observed classes exactly")
        if not isinstance(self.normalizer, FoldNormalizer):
            raise SupervisedTrainingError("normalizer must be a FoldNormalizer")
        object.__setattr__(self, "feature_schema", feature_schema)
        object.__setattr__(self, "class_labels", class_labels)
        object.__setattr__(self, "observed_class_labels", observed)
        object.__setattr__(self, "centroids", {key: centroids[key] for key in sorted(centroids)})
        object.__setattr__(self, "train_sample_ids", tuple(_text(item, "train_sample_id") for item in self.train_sample_ids))

    def predict_samples(self, samples: Sequence[SupervisedProjectionSample]) -> tuple["SupervisedPrediction", ...]:
        rows = self.normalizer.transform_matrix(_matrix(samples))
        predictions = []
        for sample, row in zip(samples, rows):
            probabilities = self._probabilities(row)
            predicted_label = sorted(probabilities.items(), key=lambda item: (-_decimal(item[1], "probability"), item[0]))[0][0]
            predictions.append(
                SupervisedPrediction(
                    sample_id=sample.sample_id,
                    side=sample.side,
                    decision_time=sample.decision_time.isoformat(),
                    true_label=str(sample.label.value),
                    predicted_label=predicted_label,
                    probabilities=probabilities,
                )
            )
        return tuple(predictions)

    def _probabilities(self, normalized_row: Sequence[Any]) -> dict[str, str]:
        row = tuple(_decimal(value, "normalized feature") for value in normalized_row)
        exact = []
        scores: dict[str, Decimal] = {}
        for label in self.observed_class_labels:
            centroid = tuple(_decimal(value, "centroid value") for value in self.centroids[label])
            distance = sum((value - center) * (value - center) for value, center in zip(row, centroid))
            if distance == 0:
                exact.append(label)
            scores[label] = Decimal(1) / (Decimal(1) + distance)
        probabilities = {label: Decimal(0) for label in self.class_labels}
        if exact:
            share = Decimal(1) / Decimal(len(exact))
            for label in exact:
                probabilities[label] = share
        else:
            total = sum(scores.values(), Decimal(0))
            if total <= 0:
                raise SupervisedTrainingError("centroid probability score total must be positive")
            for label, score in scores.items():
                probabilities[label] = score / total
        running = Decimal(0)
        for label in self.class_labels[:-1]:
            running += probabilities[label]
        if self.class_labels:
            probabilities[self.class_labels[-1]] = Decimal(1) - running
        return {label: _decimal_text(probabilities[label], "probability") for label in self.class_labels}

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": SUPERVISED_MODEL_IDENTITY_DOMAIN,
            "model_family": SupervisedModelFamily.CENTROID_CLASSIFIER_V1.value,
            "policy_identity": self.policy_identity,
            "feature_schema": list(self.feature_schema),
            "class_labels": list(self.class_labels),
            "observed_class_labels": list(self.observed_class_labels),
            "centroids": {label: list(self.centroids[label]) for label in sorted(self.centroids)},
            "normalizer_identity": self.normalizer.identity,
            "normalizer": self.normalizer.stable_dict(),
            "train_sample_ids": list(self.train_sample_ids),
        }

    @property
    def identity(self) -> str:
        return f"{SUPERVISED_MODEL_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SupervisedPrediction:
    sample_id: str
    side: ProjectionSide | str
    decision_time: str
    true_label: str
    predicted_label: str
    probabilities: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _text(self.sample_id, "sample_id"))
        object.__setattr__(self, "side", ProjectionSide(self.side))
        object.__setattr__(self, "decision_time", _text(self.decision_time, "decision_time"))
        object.__setattr__(self, "true_label", _text(self.true_label, "true_label"))
        object.__setattr__(self, "predicted_label", _text(self.predicted_label, "predicted_label"))
        if not isinstance(self.probabilities, Mapping) or not self.probabilities:
            raise SupervisedTrainingError("probabilities must be a non-empty mapping")
        probabilities = {
            _text(label, "probability label"): _decimal_text(value, "probability")
            for label, value in self.probabilities.items()
        }
        total = sum((_decimal(value, "probability") for value in probabilities.values()), Decimal(0))
        if total != Decimal(1):
            raise SupervisedTrainingError("probabilities must sum to 1")
        object.__setattr__(self, "probabilities", {label: probabilities[label] for label in sorted(probabilities)})

    def stable_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "side": self.side.value,
            "decision_time": self.decision_time,
            "true_label": self.true_label,
            "predicted_label": self.predicted_label,
            "probabilities": dict(self.probabilities),
        }


@dataclass(frozen=True, slots=True)
class SupervisedMetricSummary:
    class_labels: tuple[str, ...]
    row_count: int
    accuracy: str
    macro_precision: str
    macro_recall: str
    macro_f1: str
    brier_score: str
    class_support: Mapping[str, int]
    confusion_matrix: tuple[tuple[int, ...], ...]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "class_labels": list(self.class_labels),
            "row_count": self.row_count,
            "accuracy": self.accuracy,
            "macro_precision": self.macro_precision,
            "macro_recall": self.macro_recall,
            "macro_f1": self.macro_f1,
            "brier_score": self.brier_score,
            "class_support": dict(self.class_support),
            "confusion_matrix": [list(row) for row in self.confusion_matrix],
        }


@dataclass(frozen=True, slots=True)
class SupervisedTrainingRunResult:
    policy: SupervisedTrainingPolicy
    projection: SupervisedProjection
    run_identity: RunIdentity
    normalizer: FoldNormalizer
    model: CentroidClassifierModel
    predictions: tuple[SupervisedPrediction, ...]
    metrics: SupervisedMetricSummary
    artifact_registrations: tuple[ArtifactRegistration, ...]
    attempt_result: TrialAttemptResult

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy": {"identity": self.policy.identity, "payload": self.policy.stable_dict()},
            "projection_identity": self.projection.identity,
            "run_identity": self.run_identity.stable_dict(),
            "normalizer_identity": self.normalizer.identity,
            "model_identity": self.model.identity,
            "predictions": [prediction.stable_dict() for prediction in self.predictions],
            "metrics": self.metrics.stable_dict(),
            "artifact_identities": [
                registration.identity.stable_dict()
                for registration in self.artifact_registrations
            ],
            "attempt_result": self.attempt_result.stable_dict(),
        }


def align_probability_columns(
    probabilities: Sequence[Mapping[str, Any]],
    target_labels: Sequence[str],
) -> tuple[dict[str, str], ...]:
    labels = tuple(_text(label, "target_label") for label in target_labels)
    if len(set(labels)) != len(labels):
        raise SupervisedTrainingError("target_labels must be distinct")
    aligned = []
    for row in probabilities:
        if not isinstance(row, Mapping):
            raise SupervisedTrainingError("probability row must be a mapping")
        values = {str(label): _decimal(value, "probability") for label, value in row.items()}
        unknown = sorted(set(values) - set(labels))
        if unknown:
            raise SupervisedTrainingError("probability row contains unknown labels: " + ",".join(unknown))
        result = {label: Decimal(0) for label in labels}
        for label, value in values.items():
            result[label] = value
        total = sum(result.values(), Decimal(0))
        if total != Decimal(1):
            raise SupervisedTrainingError("aligned probabilities must sum to 1")
        aligned.append({label: _decimal_text(result[label], "probability") for label in labels})
    return tuple(aligned)


def train_evaluate_supervised_baseline(
    *,
    projection: SupervisedProjection,
    run_identity: RunIdentity,
    policy: SupervisedTrainingPolicy,
) -> SupervisedTrainingRunResult:
    if not isinstance(projection, SupervisedProjection):
        raise SupervisedTrainingError("projection must be a SupervisedProjection")
    if not isinstance(run_identity, RunIdentity):
        raise SupervisedTrainingError("run_identity must be a RunIdentity")
    if not isinstance(policy, SupervisedTrainingPolicy):
        raise SupervisedTrainingError("policy must be a SupervisedTrainingPolicy")
    train_samples = projection.samples_for_side(ProjectionSide.TRAIN)
    eval_samples = projection.samples_for_side(ProjectionSide.TEST)
    if not train_samples:
        raise SupervisedTrainingError("training requires at least one TRAIN sample")
    if not eval_samples:
        raise SupervisedTrainingError("evaluation requires at least one TEST sample")

    normalizer = FoldNormalizer.fit(projection.feature_schema, train_samples)
    model = CentroidClassifierModel.fit(
        policy=policy,
        projection=projection,
        normalizer=normalizer,
        train_samples=train_samples,
    )
    predictions = model.predict_samples(eval_samples)
    metrics = compute_classification_metrics(predictions, model.class_labels)
    registrations = _artifact_registrations(
        run_identity=run_identity,
        projection=projection,
        policy=policy,
        normalizer=normalizer,
        model=model,
        predictions=predictions,
        metrics=metrics,
    )
    accuracy_metric = MetricDefinition("accuracy", 1, MetricDirection.MAXIMIZE, unit="ratio")
    attempt = TrialAttemptResult(
        run_identity,
        RunState.SUCCEEDED,
        metrics=(MetricResult(accuracy_metric, metrics.accuracy),),
        artifact_identities=tuple(registration.identity for registration in registrations),
    )
    return SupervisedTrainingRunResult(
        policy=policy,
        projection=projection,
        run_identity=run_identity,
        normalizer=normalizer,
        model=model,
        predictions=predictions,
        metrics=metrics,
        artifact_registrations=registrations,
        attempt_result=attempt,
    )


def record_supervised_training_run(
    repository: ExperimentRepository,
    result: SupervisedTrainingRunResult,
) -> RunRecord:
    if not isinstance(result, SupervisedTrainingRunResult):
        raise SupervisedTrainingError("result must be a SupervisedTrainingRunResult")
    return record_trial_attempt(
        repository,
        result.attempt_result,
        artifact_registrations=result.artifact_registrations,
    )


def compute_classification_metrics(
    predictions: Sequence[SupervisedPrediction],
    class_labels: Sequence[str],
) -> SupervisedMetricSummary:
    labels = tuple(_text(label, "class_label") for label in class_labels)
    if not labels:
        raise SupervisedTrainingError("class_labels must not be empty")
    label_index = {label: index for index, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    brier_total = Decimal(0)
    rows = tuple(predictions)
    if not rows:
        raise SupervisedTrainingError("classification metrics require predictions")
    for prediction in rows:
        if prediction.true_label not in label_index:
            raise SupervisedTrainingError("prediction true_label is outside class_labels")
        if prediction.predicted_label not in label_index:
            raise SupervisedTrainingError("prediction predicted_label is outside class_labels")
        matrix[label_index[prediction.true_label]][label_index[prediction.predicted_label]] += 1
        aligned = align_probability_columns((prediction.probabilities,), labels)[0]
        for label in labels:
            expected = Decimal(1) if label == prediction.true_label else Decimal(0)
            error = _decimal(aligned[label], "probability") - expected
            brier_total += error * error

    row_count = len(rows)
    correct = sum(matrix[index][index] for index in range(len(labels)))
    precision_values = []
    recall_values = []
    f1_values = []
    support: dict[str, int] = {}
    for label, index in label_index.items():
        tp = Decimal(matrix[index][index])
        fp = Decimal(sum(matrix[row][index] for row in range(len(labels))) - matrix[index][index])
        fn = Decimal(sum(matrix[index]) - matrix[index][index])
        precision = Decimal(0) if tp + fp == 0 else tp / (tp + fp)
        recall = Decimal(0) if tp + fn == 0 else tp / (tp + fn)
        f1 = Decimal(0) if precision + recall == 0 else Decimal(2) * precision * recall / (precision + recall)
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        support[label] = sum(matrix[index])
    count = Decimal(len(labels))
    return SupervisedMetricSummary(
        class_labels=labels,
        row_count=row_count,
        accuracy=_decimal_text(Decimal(correct) / Decimal(row_count), "accuracy"),
        macro_precision=_decimal_text(sum(precision_values, Decimal(0)) / count, "macro_precision"),
        macro_recall=_decimal_text(sum(recall_values, Decimal(0)) / count, "macro_recall"),
        macro_f1=_decimal_text(sum(f1_values, Decimal(0)) / count, "macro_f1"),
        brier_score=_decimal_text(brier_total / Decimal(row_count), "brier_score"),
        class_support=support,
        confusion_matrix=tuple(tuple(row) for row in matrix),
    )


def _artifact_registrations(
    *,
    run_identity: RunIdentity,
    projection: SupervisedProjection,
    policy: SupervisedTrainingPolicy,
    normalizer: FoldNormalizer,
    model: CentroidClassifierModel,
    predictions: Sequence[SupervisedPrediction],
    metrics: SupervisedMetricSummary,
) -> tuple[ArtifactRegistration, ...]:
    common = {
        "policy_identity": policy.identity,
        "projection_identity": projection.identity,
        "run_identity": run_identity.stable_dict(),
    }
    payloads = {
        "normalizer": {
            **common,
            "normalizer": normalizer.stable_dict(),
        },
        "model": {
            **common,
            "model": model.stable_dict(),
        },
        "predictions": {
            **common,
            "model_identity": model.identity,
            "predictions": [prediction.stable_dict() for prediction in predictions],
        },
        "metrics": {
            **common,
            "model_identity": model.identity,
            "metrics": metrics.stable_dict(),
        },
    }
    schema_by_role = {
        "normalizer": SUPERVISED_NORMALIZER_ARTIFACT_SCHEMA,
        "model": SUPERVISED_MODEL_ARTIFACT_SCHEMA,
        "predictions": SUPERVISED_PREDICTION_ARTIFACT_SCHEMA,
        "metrics": SUPERVISED_METRIC_ARTIFACT_SCHEMA,
    }
    registrations = []
    for role in ("normalizer", "model", "predictions", "metrics"):
        content = _artifact_content(f"supervised-{role}", schema_by_role[role], payloads[role])
        registrations.append(
            ArtifactRegistration(
                ArtifactIdentity(run_identity, role, content),
                locator={"content_identity": content.content_identity},
            )
        )
    return tuple(registrations)


__all__ = [
    "SUPERVISED_METRIC_ARTIFACT_SCHEMA",
    "SUPERVISED_MODEL_ARTIFACT_SCHEMA",
    "SUPERVISED_MODEL_IDENTITY_DOMAIN",
    "SUPERVISED_NORMALIZER_ARTIFACT_SCHEMA",
    "SUPERVISED_NORMALIZER_IDENTITY_DOMAIN",
    "SUPERVISED_PREDICTION_ARTIFACT_SCHEMA",
    "SUPERVISED_TRAINING_POLICY_IDENTITY_DOMAIN",
    "CentroidClassifierModel",
    "FoldNormalizer",
    "SupervisedMetricSummary",
    "SupervisedModelFamily",
    "SupervisedPrediction",
    "SupervisedTrainingError",
    "SupervisedTrainingPolicy",
    "SupervisedTrainingRunResult",
    "align_probability_columns",
    "compute_classification_metrics",
    "record_supervised_training_run",
    "train_evaluate_supervised_baseline",
]
