"""I03 trial accounting, comparable population and metric comparison.

This module is a semantic layer over I01/I02 Experiment primitives.  It does
not persist a parallel ledger, schedule work, read artifacts or compute model
metrics from payload files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any

from .identities import ArtifactIdentity, RunIdentity, StudyIdentity
from .persistence import (
    ArtifactRegistration,
    ExperimentRepository,
    RunRecord,
    RunState,
)


COMPARABLE_TRIAL_POPULATION_IDENTITY_DOMAIN = "comparable-trial-population-v1"
EXPERIMENT_METRIC_DEFINITION_IDENTITY_DOMAIN = "experiment-metric-definition-v1"
TRIAL_METRIC_COMPARISON_IDENTITY_DOMAIN = "trial-metric-comparison-v1"


class ExperimentAccountingError(ValueError):
    """An I03 accounting/comparison value violates canonical semantics."""


class MetricDirection(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class MetricValueKind(StrEnum):
    SCALAR = "scalar"
    DISTRIBUTION = "distribution"


class MetricStatus(StrEnum):
    EVALUABLE = "evaluable"
    NON_EVALUABLE = "non_evaluable"


class ComparisonEntryStatus(StrEnum):
    EVALUABLE = "evaluable"
    NON_EVALUABLE = "non_evaluable"


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentAccountingError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ExperimentAccountingError(f"{field_name} must not contain control characters")
    return text


def _positive_version(value: str | int, field_name: str) -> str:
    text = str(value) if type(value) is int else _text(value, field_name)
    if not text.isdecimal() or int(text) < 1:
        raise ExperimentAccountingError(f"{field_name} must be a positive integer version")
    return text


def _decimal_text(value: Any, field_name: str) -> str:
    if isinstance(value, bool):
        raise ExperimentAccountingError(f"{field_name} must be a finite decimal")
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentAccountingError(f"{field_name} must be a finite decimal") from exc
    if not decimal.is_finite():
        raise ExperimentAccountingError(f"{field_name} must be a finite decimal")
    text = format(decimal.normalize(), "f")
    return "0" if text == "-0" else text


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ExperimentAccountingError("float values must be finite")
        return _decimal_text(value, "float")
    if isinstance(value, Decimal):
        return _decimal_text(value, "decimal")
    if hasattr(value, "stable_dict"):
        payload = value.stable_dict()
        if not isinstance(payload, Mapping):
            raise ExperimentAccountingError("stable_dict() must return a mapping")
        return _canonical_value(dict(payload))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ExperimentAccountingError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ExperimentAccountingError(f"unsupported canonical value type: {type(value).__name__}")


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_key(run: RunIdentity) -> tuple[str, str]:
    return (run.run_spec_identity.fingerprint, run.execution_id)


def _run_stable(run: RunIdentity) -> dict[str, str]:
    return run.stable_dict()


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One governed metric slot comparable across a closed trial population."""

    metric_key: str
    semantic_version: str | int
    direction: MetricDirection | str
    value_kind: MetricValueKind | str = MetricValueKind.SCALAR
    unit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_key", _text(self.metric_key, "metric_key"))
        object.__setattr__(self, "semantic_version", _positive_version(self.semantic_version, "semantic_version"))
        object.__setattr__(self, "direction", MetricDirection(self.direction))
        object.__setattr__(self, "value_kind", MetricValueKind(self.value_kind))
        if self.unit is not None:
            object.__setattr__(self, "unit", _text(self.unit, "unit"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": EXPERIMENT_METRIC_DEFINITION_IDENTITY_DOMAIN,
            "metric_key": self.metric_key,
            "semantic_version": self.semantic_version,
            "direction": self.direction.value,
            "value_kind": self.value_kind.value,
            "unit": self.unit,
        }

    @property
    def identity(self) -> str:
        return f"{EXPERIMENT_METRIC_DEFINITION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class MetricResult:
    """A metric value or explicit non-evaluable state for one RunIdentity."""

    metric_definition: MetricDefinition
    value: Any = None
    status: MetricStatus | str = MetricStatus.EVALUABLE
    non_evaluable_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_definition, MetricDefinition):
            raise ExperimentAccountingError("metric_definition must be a MetricDefinition")
        object.__setattr__(self, "status", MetricStatus(self.status))
        if self.status is MetricStatus.NON_EVALUABLE:
            object.__setattr__(self, "value", None)
            object.__setattr__(self, "non_evaluable_reason", _text(self.non_evaluable_reason, "non_evaluable_reason"))
            return
        if self.non_evaluable_reason is not None:
            raise ExperimentAccountingError("evaluable metric must not carry non_evaluable_reason")
        if self.metric_definition.value_kind is MetricValueKind.SCALAR:
            object.__setattr__(self, "value", _decimal_text(self.value, "metric value"))
            return
        if isinstance(self.value, (str, bytes, bytearray)) or not isinstance(self.value, Sequence):
            raise ExperimentAccountingError("distribution metric value must be a sequence")
        values = tuple(_decimal_text(item, "metric distribution value") for item in self.value)
        if not values:
            raise ExperimentAccountingError("distribution metric value must not be empty")
        object.__setattr__(self, "value", values)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "metric_definition": {
                "identity": self.metric_definition.identity,
                "payload": self.metric_definition.stable_dict(),
            },
            "status": self.status.value,
            "value": list(self.value) if isinstance(self.value, tuple) else self.value,
            "non_evaluable_reason": self.non_evaluable_reason,
        }

    @property
    def scalar_decimal(self) -> Decimal:
        if self.status is not MetricStatus.EVALUABLE:
            raise ExperimentAccountingError("non-evaluable metric has no scalar value")
        if self.metric_definition.value_kind is not MetricValueKind.SCALAR:
            raise ExperimentAccountingError("distribution metric cannot be used as scalar comparator")
        return Decimal(str(self.value))


@dataclass(frozen=True, slots=True)
class ComparableTrialPopulation:
    """Closed comparable set of concrete RunIdentity attempts for I03."""

    population_key: str
    semantic_version: str | int
    study_identity: StudyIdentity
    run_identities: Sequence[RunIdentity]
    metric_definitions: Sequence[MetricDefinition]
    closure_reason: str = "closed-before-comparison"

    def __post_init__(self) -> None:
        object.__setattr__(self, "population_key", _text(self.population_key, "population_key"))
        object.__setattr__(self, "semantic_version", _positive_version(self.semantic_version, "semantic_version"))
        if not isinstance(self.study_identity, StudyIdentity):
            raise ExperimentAccountingError("study_identity must be a StudyIdentity")
        runs = tuple(self.run_identities)
        if not runs:
            raise ExperimentAccountingError("run_identities must not be empty")
        for index, run in enumerate(runs):
            if not isinstance(run, RunIdentity):
                raise ExperimentAccountingError(f"run_identities[{index}] must be a RunIdentity")
            if run.run_spec_identity.trial_identity.study_identity.fingerprint != self.study_identity.fingerprint:
                raise ExperimentAccountingError("population run belongs to a different StudyIdentity")
        ordered_runs = tuple(sorted(runs, key=_run_key))
        if len({_run_key(run) for run in ordered_runs}) != len(ordered_runs):
            raise ExperimentAccountingError("population run identities must be distinct")
        object.__setattr__(self, "run_identities", ordered_runs)

        metrics = tuple(self.metric_definitions)
        if not metrics:
            raise ExperimentAccountingError("metric_definitions must not be empty")
        for index, metric in enumerate(metrics):
            if not isinstance(metric, MetricDefinition):
                raise ExperimentAccountingError(f"metric_definitions[{index}] must be a MetricDefinition")
        ordered_metrics = tuple(sorted(metrics, key=lambda metric: metric.identity))
        if len({metric.identity for metric in ordered_metrics}) != len(ordered_metrics):
            raise ExperimentAccountingError("metric definitions must be distinct")
        object.__setattr__(self, "metric_definitions", ordered_metrics)
        object.__setattr__(self, "closure_reason", _text(self.closure_reason, "closure_reason"))

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": COMPARABLE_TRIAL_POPULATION_IDENTITY_DOMAIN,
            "population_key": self.population_key,
            "semantic_version": self.semantic_version,
            "study_identity": self.study_identity.fingerprint,
            "run_identities": [_run_stable(run) for run in self.run_identities],
            "metric_definitions": [
                {"identity": metric.identity, "payload": metric.stable_dict()}
                for metric in self.metric_definitions
            ],
            "closure_reason": self.closure_reason,
        }
        if include_identity:
            payload["population_identity"] = self.identity
        return payload

    @property
    def identity(self) -> str:
        payload = self.stable_dict(include_identity=False)
        return f"{COMPARABLE_TRIAL_POPULATION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def contains(self, run: RunIdentity) -> bool:
        return _run_key(run) in {_run_key(item) for item in self.run_identities}


@dataclass(frozen=True, slots=True)
class TrialAttemptResult:
    """One durable trial attempt outcome projected into comparable accounting."""

    run_identity: RunIdentity
    state: RunState | str
    metrics: Sequence[MetricResult] = ()
    artifact_identities: Sequence[ArtifactIdentity] = ()
    failure_details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_identity, RunIdentity):
            raise ExperimentAccountingError("run_identity must be a RunIdentity")
        object.__setattr__(self, "state", RunState(self.state))
        metrics = tuple(self.metrics)
        metric_ids = []
        for index, metric in enumerate(metrics):
            if not isinstance(metric, MetricResult):
                raise ExperimentAccountingError(f"metrics[{index}] must be a MetricResult")
            metric_ids.append(metric.metric_definition.identity)
        if len(set(metric_ids)) != len(metric_ids):
            raise ExperimentAccountingError("attempt metrics must be distinct")
        object.__setattr__(self, "metrics", tuple(sorted(metrics, key=lambda item: item.metric_definition.identity)))
        artifacts = tuple(self.artifact_identities)
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, ArtifactIdentity):
                raise ExperimentAccountingError(f"artifact_identities[{index}] must be an ArtifactIdentity")
            if artifact.run_identity.stable_dict() != self.run_identity.stable_dict():
                raise ExperimentAccountingError("artifact run identity does not match attempt run identity")
        object.__setattr__(
            self,
            "artifact_identities",
            tuple(sorted(artifacts, key=lambda item: item.stable_dict()["artifact_role"])),
        )
        if self.failure_details is not None:
            if not isinstance(self.failure_details, Mapping):
                raise ExperimentAccountingError("failure_details must be a mapping")
            object.__setattr__(self, "failure_details", MappingProxyType(_canonical_value(dict(self.failure_details))))

    def metric_for(self, metric_definition: MetricDefinition) -> MetricResult | None:
        for metric in self.metrics:
            if metric.metric_definition.identity == metric_definition.identity:
                return metric
        return None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "run_identity": self.run_identity.stable_dict(),
            "state": self.state.value,
            "metrics": [metric.stable_dict() for metric in self.metrics],
            "artifact_identities": [artifact.stable_dict() for artifact in self.artifact_identities],
            "failure_details": None if self.failure_details is None else dict(self.failure_details),
        }


@dataclass(frozen=True, slots=True)
class MetricComparisonEntry:
    run_identity: RunIdentity
    status: ComparisonEntryStatus | str
    rank: int | None = None
    metric_value: str | None = None
    non_evaluable_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_identity, RunIdentity):
            raise ExperimentAccountingError("run_identity must be a RunIdentity")
        object.__setattr__(self, "status", ComparisonEntryStatus(self.status))
        if self.status is ComparisonEntryStatus.EVALUABLE:
            if self.rank is None or type(self.rank) is not int or self.rank < 1:
                raise ExperimentAccountingError("evaluable comparison entry requires a positive rank")
            object.__setattr__(self, "metric_value", _decimal_text(self.metric_value, "metric_value"))
            if self.non_evaluable_reason is not None:
                raise ExperimentAccountingError("evaluable comparison entry must not carry non_evaluable_reason")
        else:
            object.__setattr__(self, "rank", None)
            object.__setattr__(self, "metric_value", None)
            object.__setattr__(self, "non_evaluable_reason", _text(self.non_evaluable_reason, "non_evaluable_reason"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "run_identity": self.run_identity.stable_dict(),
            "status": self.status.value,
            "rank": self.rank,
            "metric_value": self.metric_value,
            "non_evaluable_reason": self.non_evaluable_reason,
        }


@dataclass(frozen=True, slots=True)
class TrialMetricComparison:
    population: ComparableTrialPopulation
    metric_definition: MetricDefinition
    entries: Sequence[MetricComparisonEntry]

    def __post_init__(self) -> None:
        if not isinstance(self.population, ComparableTrialPopulation):
            raise ExperimentAccountingError("population must be a ComparableTrialPopulation")
        if not isinstance(self.metric_definition, MetricDefinition):
            raise ExperimentAccountingError("metric_definition must be a MetricDefinition")
        if self.metric_definition.identity not in {metric.identity for metric in self.population.metric_definitions}:
            raise ExperimentAccountingError("metric_definition is not declared by the population")
        entries = tuple(self.entries)
        if len(entries) != len(self.population.run_identities):
            raise ExperimentAccountingError("comparison entries must cover the full population")
        if {_run_key(entry.run_identity) for entry in entries} != {_run_key(run) for run in self.population.run_identities}:
            raise ExperimentAccountingError("comparison entries must match the population exactly")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda entry: _run_key(entry.run_identity))))

    @property
    def selected_entry(self) -> MetricComparisonEntry | None:
        evaluable = [entry for entry in self.entries if entry.status is ComparisonEntryStatus.EVALUABLE]
        if not evaluable:
            return None
        return sorted(evaluable, key=lambda entry: (entry.rank, *_run_key(entry.run_identity)))[0]

    @property
    def identity(self) -> str:
        return f"{TRIAL_METRIC_COMPARISON_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": TRIAL_METRIC_COMPARISON_IDENTITY_DOMAIN,
            "population_identity": self.population.identity,
            "metric_definition": {
                "identity": self.metric_definition.identity,
                "payload": self.metric_definition.stable_dict(),
            },
            "tie_break": "rank-by-metric-then-run-spec-fingerprint-then-execution-id",
            "entries": [entry.stable_dict() for entry in self.entries],
            "selected_run_identity": None if self.selected_entry is None else self.selected_entry.run_identity.stable_dict(),
        }
        if include_identity:
            payload["comparison_identity"] = self.identity
        return payload


def record_trial_attempt(
    repository: ExperimentRepository,
    attempt: TrialAttemptResult,
    *,
    artifact_registrations: Sequence[ArtifactRegistration] = (),
) -> RunRecord:
    """Persist one attempt through I02 only, preserving restart idempotency."""

    if not isinstance(repository, ExperimentRepository):
        raise ExperimentAccountingError("repository must be an ExperimentRepository")
    if not isinstance(attempt, TrialAttemptResult):
        raise ExperimentAccountingError("attempt must be a TrialAttemptResult")
    registrations = tuple(artifact_registrations)
    for index, registration in enumerate(registrations):
        if not isinstance(registration, ArtifactRegistration):
            raise ExperimentAccountingError(f"artifact_registrations[{index}] must be ArtifactRegistration")
        if registration.identity.run_identity.stable_dict() != attempt.run_identity.stable_dict():
            raise ExperimentAccountingError("artifact registration run identity does not match attempt")
    if attempt.artifact_identities:
        registered = {registration.identity.stable_dict()["artifact_role"] for registration in registrations}
        expected = {artifact.stable_dict()["artifact_role"] for artifact in attempt.artifact_identities}
        if registered != expected:
            raise ExperimentAccountingError("artifact registrations do not match attempt artifact identities")

    current = repository.register_run(attempt.run_identity)
    if attempt.state is RunState.REGISTERED:
        return current
    if attempt.state is RunState.RUNNING:
        return repository.transition_run(attempt.run_identity, RunState.RUNNING)
    if attempt.state is RunState.FAILED:
        return repository.transition_run(
            attempt.run_identity,
            RunState.FAILED,
            failure_details=attempt.failure_details,
        )
    if current.state is RunState.REGISTERED:
        repository.transition_run(attempt.run_identity, RunState.RUNNING)
    return repository.succeed_run_with_artifacts(attempt.run_identity, registrations)


def compare_trial_attempts(
    population: ComparableTrialPopulation,
    attempts: Sequence[TrialAttemptResult],
    metric_definition: MetricDefinition,
) -> TrialMetricComparison:
    """Compare one metric over a closed population without dropping members."""

    if not isinstance(population, ComparableTrialPopulation):
        raise ExperimentAccountingError("population must be a ComparableTrialPopulation")
    if not isinstance(metric_definition, MetricDefinition):
        raise ExperimentAccountingError("metric_definition must be a MetricDefinition")
    if metric_definition.value_kind is not MetricValueKind.SCALAR:
        raise ExperimentAccountingError("comparison metric must be scalar")
    if metric_definition.identity not in {metric.identity for metric in population.metric_definitions}:
        raise ExperimentAccountingError("metric_definition is not declared by the population")

    population_keys = {_run_key(run) for run in population.run_identities}
    attempts_by_run: dict[tuple[str, str], TrialAttemptResult] = {}
    for index, attempt in enumerate(tuple(attempts)):
        if not isinstance(attempt, TrialAttemptResult):
            raise ExperimentAccountingError(f"attempts[{index}] must be TrialAttemptResult")
        key = _run_key(attempt.run_identity)
        if key not in population_keys:
            raise ExperimentAccountingError("attempt run identity is outside the comparable population")
        existing = attempts_by_run.get(key)
        if existing is not None:
            if _canonical_value(existing.stable_dict()) != _canonical_value(attempt.stable_dict()):
                raise ExperimentAccountingError("conflicting duplicate attempt result")
            continue
        attempts_by_run[key] = attempt

    raw_entries: list[tuple[RunIdentity, Decimal | None, str | None]] = []
    for run in population.run_identities:
        attempt = attempts_by_run.get(_run_key(run))
        if attempt is None:
            raw_entries.append((run, None, "missing_attempt"))
            continue
        if attempt.state is not RunState.SUCCEEDED:
            raw_entries.append((run, None, f"run_state:{attempt.state.value}"))
            continue
        metric = attempt.metric_for(metric_definition)
        if metric is None:
            raw_entries.append((run, None, "metric_missing"))
            continue
        if metric.status is MetricStatus.NON_EVALUABLE:
            raw_entries.append((run, None, f"metric_non_evaluable:{metric.non_evaluable_reason}"))
            continue
        raw_entries.append((run, metric.scalar_decimal, None))

    evaluable = [(run, value) for run, value, reason in raw_entries if value is not None and reason is None]
    if metric_definition.direction is MetricDirection.MAXIMIZE:
        ranked = sorted(evaluable, key=lambda item: (-item[1], *_run_key(item[0])))
    else:
        ranked = sorted(evaluable, key=lambda item: (item[1], *_run_key(item[0])))
    ranks = {_run_key(run): rank for rank, (run, _) in enumerate(ranked, start=1)}

    entries = []
    for run, value, reason in raw_entries:
        if value is None:
            entries.append(
                MetricComparisonEntry(
                    run_identity=run,
                    status=ComparisonEntryStatus.NON_EVALUABLE,
                    non_evaluable_reason=reason,
                )
            )
        else:
            entries.append(
                MetricComparisonEntry(
                    run_identity=run,
                    status=ComparisonEntryStatus.EVALUABLE,
                    rank=ranks[_run_key(run)],
                    metric_value=_decimal_text(value, "metric_value"),
                )
            )
    return TrialMetricComparison(population, metric_definition, tuple(entries))


__all__ = [
    "COMPARABLE_TRIAL_POPULATION_IDENTITY_DOMAIN",
    "EXPERIMENT_METRIC_DEFINITION_IDENTITY_DOMAIN",
    "TRIAL_METRIC_COMPARISON_IDENTITY_DOMAIN",
    "ComparableTrialPopulation",
    "ComparisonEntryStatus",
    "ExperimentAccountingError",
    "MetricComparisonEntry",
    "MetricDefinition",
    "MetricDirection",
    "MetricResult",
    "MetricStatus",
    "MetricValueKind",
    "TrialAttemptResult",
    "TrialMetricComparison",
    "compare_trial_attempts",
    "record_trial_attempt",
]
