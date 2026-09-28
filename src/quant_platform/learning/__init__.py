"""Learning-owned supervised input projection primitives."""

from .supervised import (
    LEARNING_PROJECTION_IDENTITY_DOMAIN,
    LEARNING_SAMPLE_IDENTITY_DOMAIN,
    LearningError,
    ProjectionSide,
    SupervisedFeatureInput,
    SupervisedProjection,
    SupervisedProjectionRejection,
    SupervisedProjectionSample,
    SupervisedSampleCandidate,
    SupervisedSelectionPolicy,
    build_supervised_projection,
)

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
