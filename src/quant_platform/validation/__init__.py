"""Validation-owned temporal schedule, availability/purge/embargo, label and lockbox primitives."""

from .availability import (
    CandidateClassification,
    CandidateClassificationResult,
    DependencyCutoffRole,
    DependencyEvidence,
    DependencyLifecycle,
    DependencyMaturity,
    Embargo,
    ValidationCandidate,
    classify_candidate,
)
from .labels import (
    LABEL_DEFINITION_IDENTITY_DOMAIN,
    LABEL_RESULT_IDENTITY_DOMAIN,
    LabelCensoringPolicy,
    LabelDefinition,
    LabelError,
    LabelResult,
    LabelStatus,
    LabelTransformKind,
    OutcomeEvidence,
    OutcomeState,
    as_training_dependency_evidence,
    evaluate_label,
)
from .lockbox import (
    LOCKBOX_IDENTITY_DOMAIN,
    Lockbox,
    LockboxError,
    LockboxVisibility,
)
from .walk_forward import (
    WalkForwardFold,
    WalkForwardScheduleSpec,
    build_walk_forward_folds,
)

__all__ = [
    "CandidateClassification",
    "CandidateClassificationResult",
    "DependencyCutoffRole",
    "DependencyEvidence",
    "DependencyLifecycle",
    "DependencyMaturity",
    "Embargo",
    "LABEL_DEFINITION_IDENTITY_DOMAIN",
    "LABEL_RESULT_IDENTITY_DOMAIN",
    "LOCKBOX_IDENTITY_DOMAIN",
    "LabelCensoringPolicy",
    "LabelDefinition",
    "LabelError",
    "LabelResult",
    "LabelStatus",
    "LabelTransformKind",
    "Lockbox",
    "LockboxError",
    "LockboxVisibility",
    "OutcomeEvidence",
    "OutcomeState",
    "ValidationCandidate",
    "WalkForwardFold",
    "WalkForwardScheduleSpec",
    "as_training_dependency_evidence",
    "build_walk_forward_folds",
    "classify_candidate",
    "evaluate_label",
]
