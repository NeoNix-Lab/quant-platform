"""Outcome-derived labels, censoring and target support projection v1 (F07).

This module implements the canonical outcome-derived label foundation governed by
ADR-0036 under the Validation owner (``quant_platform.validation``).

It preserves package ownership boundaries:
- ``validation`` depends strictly on ``{validation, shared}``;
- It does NOT import ``quant_platform.research`` runtime types;
- Source Outcome evidence is received through the Validation-owned immutable
  ``OutcomeEvidence`` projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
import hashlib
import re
from types import MappingProxyType
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest
from .availability import (
    DependencyCutoffRole,
    DependencyEvidence,
    DependencyLifecycle,
    DependencyMaturity,
)


LABEL_DEFINITION_IDENTITY_DOMAIN = "label-definition-v1"
LABEL_RESULT_IDENTITY_DOMAIN = "label-result-v1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")
_FRACTION_TEXT_RE = re.compile(r"^-?(0|[1-9][0-9]*)(/[1-9][0-9]*)?$")


class LabelError(ValueError):
    """An F07 label definition or evaluation violates the canonical contract."""


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LabelError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(c) < 32 for c in text):
        raise LabelError(f"{field_name} must not contain control characters")
    return text


def _governed_key(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise LabelError(f"{field_name} must be a governed canonical key spelling")
    return text


def _semantic_version(value: Any, field_name: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field_name)
    if not _VERSION_RE.fullmatch(text):
        raise LabelError(f"{field_name} must be an explicit positive version")
    return text


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _validated_fraction_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _FRACTION_TEXT_RE.fullmatch(value):
        raise LabelError(f"{field_name} must be an exact canonical fraction string, got {value!r}")
    numerator_text, separator, denominator_text = value.partition("/")
    fraction = Fraction(int(numerator_text), int(denominator_text) if separator else 1)
    if _fraction_text(fraction) != value:
        raise LabelError(
            f"{field_name} must be a canonical lowest-terms fraction string (no reducible "
            f"form, zero denominator collapse or signed zero), got {value!r}"
        )
    return value


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Outcome source vocabulary and Validation-owned projection.
# ---------------------------------------------------------------------------


class OutcomeState(StrEnum):
    """Mirror of F03 Outcome states, owned locally by Validation."""

    COMPLETE = "complete"
    CENSORED_END_OF_DATA = "censored_end_of_data"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


@dataclass(frozen=True, slots=True)
class OutcomeEvidence:
    """Validation-owned immutable projection of F03 Outcome evidence.

    This projection preserves the source identities, state, horizon, causal
    availability and canonical value without importing Research runtime types.
    """

    outcome_id: str
    outcome_spec_id: str
    event_id: str
    horizon_start: Instant | str
    horizon_end: Instant | str
    causal_available_at: Instant | str
    state: OutcomeState | str
    realized_value: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_id", _non_empty_text(self.outcome_id, "outcome_id"))
        if not self.outcome_id.startswith("outcome-v1:sha256:"):
            raise LabelError("outcome_id must be a v1 outcome sha256 identity")

        object.__setattr__(self, "outcome_spec_id", _non_empty_text(self.outcome_spec_id, "outcome_spec_id"))
        if not self.outcome_spec_id.startswith("outcome-spec-v1:sha256:"):
            raise LabelError("outcome_spec_id must be a v1 outcome-spec sha256 identity")

        object.__setattr__(self, "event_id", _non_empty_text(self.event_id, "event_id"))

        try:
            start = Instant.parse(self.horizon_start)
            end = Instant.parse(self.horizon_end)
            causal = Instant.parse(self.causal_available_at)
        except InvalidRequest as exc:
            raise LabelError(f"invalid instant in OutcomeEvidence: {exc}") from exc

        if end < start:
            raise LabelError("horizon_end cannot precede horizon_start")
        if causal < end:
            raise LabelError("causal_available_at cannot precede horizon_end")

        object.__setattr__(self, "horizon_start", start)
        object.__setattr__(self, "horizon_end", end)
        object.__setattr__(self, "causal_available_at", causal)

        try:
            state_enum = OutcomeState(self.state)
        except ValueError as exc:
            raise LabelError(f"unsupported outcome state: {self.state!r}") from exc
        object.__setattr__(self, "state", state_enum)

        if self.state is OutcomeState.COMPLETE:
            if self.realized_value is None:
                raise LabelError("a COMPLETE outcome must carry a realized_value")
            object.__setattr__(
                self, "realized_value", _validated_fraction_text(self.realized_value, "realized_value")
            )
        elif self.realized_value is not None:
            raise LabelError(
                f"a {self.state.value} outcome must not carry a realized_value"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OutcomeEvidence:
        return cls(
            outcome_id=payload["outcome_id"],
            outcome_spec_id=payload["outcome_spec_id"],
            event_id=payload.get("event_id", payload.get("source_event_id", "")),
            horizon_start=payload["horizon_start"],
            horizon_end=payload["horizon_end"],
            causal_available_at=payload["causal_available_at"],
            state=payload["state"],
            realized_value=payload.get("realized_value"),
        )


# ---------------------------------------------------------------------------
# LabelDefinition model and transformation kinds.
# ---------------------------------------------------------------------------


class LabelTransformKind(StrEnum):
    IDENTITY_VALUE = "identity_value"
    ORDERED_THRESHOLDS = "ordered_thresholds"


class LabelCensoringPolicy(StrEnum):
    REQUIRE_COMPLETE = "require_complete"


@dataclass(frozen=True, slots=True)
class LabelDefinition:
    """Canonical immutable outcome-derived label definition governed by ADR-0036."""

    label_key: str
    semantic_version: str | int
    source_outcome_spec_id: str
    transform_kind: LabelTransformKind | str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    censoring_policy: LabelCensoringPolicy | str = LabelCensoringPolicy.REQUIRE_COMPLETE
    output_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label_key", _governed_key(self.label_key, "label_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))

        outcome_spec_id = _non_empty_text(self.source_outcome_spec_id, "source_outcome_spec_id")
        if not outcome_spec_id.startswith("outcome-spec-v1:sha256:"):
            raise LabelError("source_outcome_spec_id must be a v1 outcome-spec sha256 identity")
        object.__setattr__(self, "source_outcome_spec_id", outcome_spec_id)

        try:
            kind = LabelTransformKind(self.transform_kind)
        except ValueError as exc:
            raise LabelError(f"unsupported transform_kind: {self.transform_kind!r}") from exc
        object.__setattr__(self, "transform_kind", kind)

        try:
            policy = LabelCensoringPolicy(self.censoring_policy)
        except ValueError as exc:
            raise LabelError(f"unsupported censoring_policy: {self.censoring_policy!r}") from exc
        object.__setattr__(self, "censoring_policy", policy)

        if not isinstance(self.parameters, Mapping):
            raise LabelError("parameters must be a mapping")

        normalized_params: dict[str, Any] = {}
        if kind is LabelTransformKind.IDENTITY_VALUE:
            if self.parameters:
                raise LabelError("IDENTITY_VALUE accepts no parameters")
        elif kind is LabelTransformKind.ORDERED_THRESHOLDS:
            if "thresholds" not in self.parameters or "classes" not in self.parameters:
                raise LabelError("ORDERED_THRESHOLDS requires 'thresholds' and 'classes' parameters")

            thresholds_raw = self.parameters["thresholds"]
            if not isinstance(thresholds_raw, (list, tuple)) or not thresholds_raw:
                raise LabelError("thresholds must be a non-empty sequence of canonical fraction strings")

            validated_thresholds: list[str] = []
            parsed_fractions: list[Fraction] = []
            for i, t in enumerate(thresholds_raw):
                val_text = _validated_fraction_text(t, f"threshold[{i}]")
                frac = Fraction(val_text)
                if parsed_fractions and frac <= parsed_fractions[-1]:
                    raise LabelError(
                        f"thresholds must be strictly increasing: threshold[{i}] ({val_text}) "
                        f"<= threshold[{i-1}] ({validated_thresholds[-1]})"
                    )
                parsed_fractions.append(frac)
                validated_thresholds.append(val_text)

            classes_raw = self.parameters["classes"]
            if not isinstance(classes_raw, (list, tuple)):
                raise LabelError("classes must be a sequence of class values")

            if len(classes_raw) != len(validated_thresholds) + 1:
                raise LabelError(
                    f"ORDERED_THRESHOLDS requires exactly N + 1 classes for N thresholds: "
                    f"got {len(classes_raw)} classes for {len(validated_thresholds)} thresholds"
                )

            validated_classes: list[int | str] = []
            for i, c in enumerate(classes_raw):
                if isinstance(c, bool) or not isinstance(c, (int, str)):
                    raise LabelError(f"class[{i}] must be an integer or string, got {type(c).__name__}")
                if isinstance(c, str):
                    c = _non_empty_text(c, f"class[{i}]")
                validated_classes.append(c)

            normalized_params["thresholds"] = tuple(validated_thresholds)
            normalized_params["classes"] = tuple(validated_classes)

        object.__setattr__(self, "parameters", MappingProxyType(normalized_params))

        if not isinstance(self.output_schema, Mapping):
            raise LabelError("output_schema must be a mapping")
        object.__setattr__(self, "output_schema", MappingProxyType(dict(self.output_schema)))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": LABEL_DEFINITION_IDENTITY_DOMAIN,
            "label_key": self.label_key,
            "semantic_version": self.semantic_version,
            "source_outcome_spec_id": self.source_outcome_spec_id,
            "transform_kind": self.transform_kind.value,
            "parameters": {
                k: list(v) if isinstance(v, tuple) else v
                for k, v in self.parameters.items()
            },
            "censoring_policy": self.censoring_policy.value,
            "output_schema": dict(self.output_schema),
        }

    @property
    def label_definition_id(self) -> str:
        return f"{LABEL_DEFINITION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.label_definition_id


# ---------------------------------------------------------------------------
# LabelResult model and evaluation runtime.
# ---------------------------------------------------------------------------


class LabelStatus(StrEnum):
    LABELED = "labeled"
    NO_VALUE_CENSORED = "no_value_censored"
    NO_VALUE_INSUFFICIENT = "no_value_insufficient"


@dataclass(frozen=True, slots=True)
class LabelResult:
    """Canonical immutable label result traceable to definition and source evidence."""

    label_definition_id: str
    source_outcome_id: str
    source_outcome_spec_id: str
    source_state: OutcomeState
    horizon_start: Instant
    horizon_end: Instant
    causal_available_at: Instant
    status: LabelStatus
    value: str | int | None
    source_realized_value: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label_definition_id", _non_empty_text(self.label_definition_id, "label_definition_id"))
        object.__setattr__(self, "source_outcome_id", _non_empty_text(self.source_outcome_id, "source_outcome_id"))
        object.__setattr__(self, "source_outcome_spec_id", _non_empty_text(self.source_outcome_spec_id, "source_outcome_spec_id"))

        if not isinstance(self.source_state, OutcomeState):
            raise LabelError("source_state must be an OutcomeState")
        if not isinstance(self.status, LabelStatus):
            raise LabelError("status must be a LabelStatus")

        if not isinstance(self.horizon_start, Instant) or not isinstance(self.horizon_end, Instant):
            raise LabelError("horizon bounds must be Instant instances")
        if not isinstance(self.causal_available_at, Instant):
            raise LabelError("causal_available_at must be an Instant instance")

        if self.status is LabelStatus.LABELED:
            if self.source_state is not OutcomeState.COMPLETE:
                raise LabelError("LABELED status requires source_state COMPLETE")
            if self.source_realized_value is None:
                raise LabelError("LABELED status requires source_realized_value")
            if self.value is None:
                raise LabelError("LABELED status requires a non-None value")
        else:
            if self.value is not None:
                raise LabelError(f"{self.status.value} must not carry a label value")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": LABEL_RESULT_IDENTITY_DOMAIN,
            "label_definition_id": self.label_definition_id,
            "source_outcome_id": self.source_outcome_id,
            "source_outcome_spec_id": self.source_outcome_spec_id,
            "source_state": self.source_state.value,
            "horizon_start": self.horizon_start.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
            "causal_available_at": self.causal_available_at.isoformat(),
            "source_realized_value": self.source_realized_value,
            "status": self.status.value,
            "value": self.value,
        }

    @property
    def result_id(self) -> str:
        return f"{LABEL_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.result_id


def evaluate_label(definition: LabelDefinition, outcome: OutcomeEvidence) -> LabelResult:
    """Evaluate one OutcomeEvidence against one LabelDefinition deterministically.

    Fails closed if the source OutcomeSpec ID does not match the definition's expectation.
    """
    if definition.source_outcome_spec_id != outcome.outcome_spec_id:
        raise LabelError(
            f"source OutcomeSpec mismatch: definition requires {definition.source_outcome_spec_id}, "
            f"outcome carries {outcome.outcome_spec_id}"
        )

    if outcome.state is OutcomeState.CENSORED_END_OF_DATA:
        return LabelResult(
            label_definition_id=definition.label_definition_id,
            source_outcome_id=outcome.outcome_id,
            source_outcome_spec_id=outcome.outcome_spec_id,
            source_state=outcome.state,
            horizon_start=outcome.horizon_start,
            horizon_end=outcome.horizon_end,
            causal_available_at=outcome.causal_available_at,
            status=LabelStatus.NO_VALUE_CENSORED,
            value=None,
            source_realized_value=None,
        )

    if outcome.state is OutcomeState.INSUFFICIENT_COVERAGE:
        return LabelResult(
            label_definition_id=definition.label_definition_id,
            source_outcome_id=outcome.outcome_id,
            source_outcome_spec_id=outcome.outcome_spec_id,
            source_state=outcome.state,
            horizon_start=outcome.horizon_start,
            horizon_end=outcome.horizon_end,
            causal_available_at=outcome.causal_available_at,
            status=LabelStatus.NO_VALUE_INSUFFICIENT,
            value=None,
            source_realized_value=None,
        )

    # State is COMPLETE.
    assert outcome.realized_value is not None
    source_val = outcome.realized_value

    if definition.transform_kind is LabelTransformKind.IDENTITY_VALUE:
        label_val: str | int = source_val
    elif definition.transform_kind is LabelTransformKind.ORDERED_THRESHOLDS:
        x = Fraction(source_val)
        thresholds = [Fraction(t) for t in definition.parameters["thresholds"]]
        classes = definition.parameters["classes"]

        # Interval semantics:
        # x < t_0           -> class 0
        # t_0 <= x < t_1    -> class 1
        # ...
        # t_{N-1} <= x      -> class N
        assigned_class = classes[-1]
        for idx, thresh in enumerate(thresholds):
            if x < thresh:
                assigned_class = classes[idx]
                break
        label_val = assigned_class
    else:  # pragma: no cover
        raise LabelError(f"unhandled transform kind: {definition.transform_kind}")

    return LabelResult(
        label_definition_id=definition.label_definition_id,
        source_outcome_id=outcome.outcome_id,
        source_outcome_spec_id=outcome.outcome_spec_id,
        source_state=outcome.state,
        horizon_start=outcome.horizon_start,
        horizon_end=outcome.horizon_end,
        causal_available_at=outcome.causal_available_at,
        status=LabelStatus.LABELED,
        value=label_val,
        source_realized_value=source_val,
    )


# ---------------------------------------------------------------------------
# Target support and F06 projection.
# ---------------------------------------------------------------------------


def as_training_dependency_evidence(label_result: LabelResult) -> DependencyEvidence:
    """Project one LabelResult into the canonical F06 DependencyEvidence seam.

    The semantic target support [horizon_start, horizon_end] is projected into
    the half-open interval [horizon_start, horizon_end + 1ns) so that the consumed
    terminal instant is preserved.

    For COMPLETE labeled results, the dependency is sufficient and available at
    label_result.causal_available_at under FOLD_COMPLETION cutoff role.
    For non-complete (censored/insufficient) results, sufficient=False is emitted.
    """
    if label_result.status is not LabelStatus.LABELED:
        return DependencyEvidence(
            identity=label_result.result_id,
            cutoff_role=DependencyCutoffRole.FOLD_COMPLETION,
            sufficient=False,
            detail=f"label_{label_result.status.value}",
        )

    # Project [horizon_start, horizon_end] to [horizon_start, horizon_end + 1ns)
    support_interval = CoverageInterval(
        label_result.horizon_start,
        Instant(label_result.horizon_end.epoch_ns + 1),
    )

    return DependencyEvidence(
        identity=label_result.result_id,
        cutoff_role=DependencyCutoffRole.FOLD_COMPLETION,
        sufficient=True,
        support=support_interval,
        required_maturity=DependencyMaturity.AVAILABLE,
        lifecycle=DependencyLifecycle.FINAL,
        causal_available_at=label_result.causal_available_at,
        observed_available_at=label_result.causal_available_at,
        observed_finalized_at=label_result.causal_available_at,
        contemporaneous_version_proven=True,
        detail="training_target",
    )


__all__ = [
    "LABEL_DEFINITION_IDENTITY_DOMAIN",
    "LABEL_RESULT_IDENTITY_DOMAIN",
    "LabelCensoringPolicy",
    "LabelDefinition",
    "LabelError",
    "LabelResult",
    "LabelStatus",
    "LabelTransformKind",
    "OutcomeEvidence",
    "OutcomeState",
    "as_training_dependency_evidence",
    "evaluate_label",
]
