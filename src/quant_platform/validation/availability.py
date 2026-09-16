"""F06 availability/purge/embargo classification.

This module owns the bounded Validation-side decision seam only: given one
F05 ``WalkForwardFold``, a non-negative embargo duration and one candidate
decision/reference instant with its declared dependency evidence, it produces
a deterministic fail-closed classification.

It deliberately does not import Feature-owned (E02) runtime types.  The
dependency evidence values below are Validation-owned and only mirror the
E02 causal-availability-floor / observed-evidence / PROVISIONAL-FINAL shape
that the frozen F06 contract requires a decision-time dependency to carry.
Target/outcome/label semantics (F07) are not defined here: a fold-completion
dependency is accepted as a generic declared dependency, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest
from quant_platform.validation.walk_forward import WalkForwardFold


class DependencyCutoffRole(StrEnum):
    """Which candidate-relative instant a dependency must be admissible by."""

    DECISION_TIME = "decision_time"
    FOLD_COMPLETION = "fold_completion"


class DependencyMaturity(StrEnum):
    AVAILABLE = "AVAILABLE"
    FINAL_ONLY = "FINAL_ONLY"


class DependencyLifecycle(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    FINAL = "FINAL"


class CandidateClassification(StrEnum):
    OUT_OF_FOLD = "OUT_OF_FOLD"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
    PURGED = "PURGED"
    EMBARGOED = "EMBARGOED"
    ADMITTED = "ADMITTED"


@dataclass(frozen=True, slots=True)
class Embargo:
    """Non-negative wall-clock pre-test embargo duration, in nanoseconds."""

    duration_ns: int

    def __post_init__(self) -> None:
        if type(self.duration_ns) is not int:
            raise InvalidRequest("embargo duration_ns must be an integer")
        if self.duration_ns < 0:
            raise InvalidRequest("embargo duration_ns must be non-negative")

    def stable_dict(self) -> dict[str, int]:
        return {"duration_ns": self.duration_ns}


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    """One declared candidate dependency's support and availability evidence.

    ``sufficient=False`` represents explicit E02-style non-observation
    (insufficient declared support/history): every availability field must be
    left unset, so absence is never padded, synthesized or inferred.
    """

    identity: str
    cutoff_role: DependencyCutoffRole
    sufficient: bool = True
    support: CoverageInterval | None = None
    required_maturity: DependencyMaturity | None = None
    lifecycle: DependencyLifecycle | None = None
    causal_available_at: Instant | datetime | str | None = None
    observed_available_at: Instant | datetime | str | None = None
    observed_finalized_at: Instant | datetime | str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise InvalidRequest("dependency identity must be a non-empty string")
        object.__setattr__(
            self, "cutoff_role", _enum(DependencyCutoffRole, self.cutoff_role, "cutoff_role")
        )
        if type(self.sufficient) is not bool:
            raise InvalidRequest("dependency sufficient must be a boolean")

        if not self.sufficient:
            if (
                self.support is not None
                or self.required_maturity is not None
                or self.lifecycle is not None
                or self.causal_available_at is not None
                or self.observed_available_at is not None
                or self.observed_finalized_at is not None
            ):
                raise InvalidRequest(
                    "insufficient dependency evidence must not carry support or "
                    "availability/finality fields"
                )
            return

        if not isinstance(self.support, CoverageInterval):
            raise InvalidRequest("sufficient dependency evidence requires support")
        required_maturity = _enum(
            DependencyMaturity, self.required_maturity, "required_maturity"
        )
        lifecycle = _enum(DependencyLifecycle, self.lifecycle, "lifecycle")
        object.__setattr__(self, "required_maturity", required_maturity)
        object.__setattr__(self, "lifecycle", lifecycle)

        causal_available_at = _parse_instant(
            self.causal_available_at, "causal_available_at"
        )
        object.__setattr__(self, "causal_available_at", causal_available_at)

        observed_available_at = (
            None
            if self.observed_available_at is None
            else _parse_instant(self.observed_available_at, "observed_available_at")
        )
        observed_finalized_at = (
            None
            if self.observed_finalized_at is None
            else _parse_instant(self.observed_finalized_at, "observed_finalized_at")
        )
        if observed_available_at is not None and observed_available_at < causal_available_at:
            raise InvalidRequest(
                "observed availability cannot precede the causal availability floor"
            )
        if lifecycle == DependencyLifecycle.PROVISIONAL and observed_finalized_at is not None:
            raise InvalidRequest("PROVISIONAL dependency evidence cannot carry finalization evidence")
        if observed_finalized_at is not None:
            if observed_finalized_at < causal_available_at:
                raise InvalidRequest(
                    "observed finalization cannot precede the causal availability floor"
                )
            if observed_available_at is not None and observed_finalized_at < observed_available_at:
                raise InvalidRequest("observed finalization cannot precede observed availability")
        object.__setattr__(self, "observed_available_at", observed_available_at)
        object.__setattr__(self, "observed_finalized_at", observed_finalized_at)


@dataclass(frozen=True, slots=True)
class ValidationCandidate:
    """One candidate decision/reference instant plus its dependency evidence."""

    d: Instant | datetime | str
    dependencies: tuple[DependencyEvidence, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "d", _parse_instant(self.d, "d"))
        dependencies = tuple(self.dependencies)
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, DependencyEvidence):
                raise InvalidRequest(f"dependencies[{index}] must be DependencyEvidence")
        object.__setattr__(self, "dependencies", dependencies)


@dataclass(frozen=True, slots=True)
class CandidateClassificationResult:
    classification: CandidateClassification
    reasons: tuple[str, ...] = ()

    def stable_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "reasons": list(self.reasons),
        }


def classify_candidate(
    fold: WalkForwardFold,
    embargo: Embargo,
    candidate: ValidationCandidate,
) -> CandidateClassificationResult:
    """Classify one candidate against one fold under the frozen F06 precedence.

    Precedence: UNAVAILABLE -> INSUFFICIENT_SUPPORT -> PURGED -> EMBARGOED ->
    ADMITTED, evaluated only after an explicit out-of-fold rejection.
    """

    if not isinstance(fold, WalkForwardFold):
        raise InvalidRequest("fold must be a WalkForwardFold")
    if not isinstance(embargo, Embargo):
        raise InvalidRequest("embargo must be an Embargo")
    if not isinstance(candidate, ValidationCandidate):
        raise InvalidRequest("candidate must be a ValidationCandidate")

    d = candidate.d
    if d < fold.train.start or d >= fold.test.end:
        return CandidateClassificationResult(CandidateClassification.OUT_OF_FOLD)

    is_train_side = d < fold.test.start

    unavailable_reasons: list[str] = []
    insufficient_reasons: list[str] = []
    admissible_dependencies: list[DependencyEvidence] = []

    for dependency in candidate.dependencies:
        cutoff = (
            d
            if dependency.cutoff_role == DependencyCutoffRole.DECISION_TIME
            else fold.test.start
        )
        verdict, reason = _evaluate_dependency(dependency, cutoff)
        if verdict == "unavailable":
            unavailable_reasons.append(reason)
        elif verdict == "insufficient":
            insufficient_reasons.append(reason)
        else:
            admissible_dependencies.append(dependency)

    if unavailable_reasons:
        return CandidateClassificationResult(
            CandidateClassification.UNAVAILABLE, tuple(unavailable_reasons)
        )
    if insufficient_reasons:
        return CandidateClassificationResult(
            CandidateClassification.INSUFFICIENT_SUPPORT, tuple(insufficient_reasons)
        )

    if is_train_side:
        held_out = fold.test
        purge_reasons = tuple(
            dependency.identity
            for dependency in admissible_dependencies
            if _overlaps(dependency.support, held_out)
        )
        if purge_reasons:
            return CandidateClassificationResult(CandidateClassification.PURGED, purge_reasons)

        embargo_start_ns = fold.test.start.epoch_ns - embargo.duration_ns
        if d.epoch_ns >= embargo_start_ns:
            return CandidateClassificationResult(CandidateClassification.EMBARGOED)

    return CandidateClassificationResult(CandidateClassification.ADMITTED)


def _evaluate_dependency(
    dependency: DependencyEvidence, cutoff: Instant
) -> tuple[str, str]:
    if not dependency.sufficient:
        detail = dependency.detail or "insufficient declared support"
        return "insufficient", f"{dependency.identity}: {detail}"

    if dependency.required_maturity == DependencyMaturity.FINAL_ONLY:
        if dependency.observed_finalized_at is None:
            return "unavailable", f"{dependency.identity}: finality not proven by cutoff"
        if dependency.observed_finalized_at <= cutoff:
            return "ok", ""
        return "unavailable", f"{dependency.identity}: finality proven after cutoff"

    effective_available_at = (
        dependency.causal_available_at
        if dependency.observed_available_at is None
        else dependency.observed_available_at
    )
    if effective_available_at <= cutoff:
        return "ok", ""
    return "unavailable", f"{dependency.identity}: not available by cutoff"


def _overlaps(support: CoverageInterval, held_out: CoverageInterval) -> bool:
    return support.start < held_out.end and held_out.start < support.end


def _enum(enum_type: Any, value: Any, field_name: str) -> Any:
    if value is None:
        raise InvalidRequest(f"{field_name} is required")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise InvalidRequest(f"{field_name} is not a supported value") from exc


def _parse_instant(value: Instant | datetime | str | None, field_name: str) -> Instant:
    if value is None:
        raise InvalidRequest(f"{field_name} is required")
    try:
        return Instant.parse(value)
    except InvalidRequest as exc:
        raise InvalidRequest(f"{field_name} must be a valid UTC instant") from exc


__all__ = [
    "CandidateClassification",
    "CandidateClassificationResult",
    "DependencyCutoffRole",
    "DependencyEvidence",
    "DependencyLifecycle",
    "DependencyMaturity",
    "Embargo",
    "ValidationCandidate",
    "classify_candidate",
]
