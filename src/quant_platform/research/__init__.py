"""Research-owned semantic capabilities (F01 HypothesisSpec, F02 EventSpec)."""

from .events import (
    DETECTED_EVENT_IDENTITY_DOMAIN,
    EVENT_SPEC_IDENTITY_DOMAIN,
    EVENT_SPEC_MODEL_VERSION,
    ComparisonOperator,
    DetectedEvent,
    DirectionRequirement,
    EventDetectionError,
    EventSpec,
    EventSpecError,
    EventSpecId,
    ThresholdPredicate,
    detect_events,
)
from .hypothesis import (
    HYPOTHESIS_SPEC_IDENTITY_DOMAIN,
    HYPOTHESIS_SPEC_MODEL_VERSION,
    HypothesisSpec,
    HypothesisSpecError,
    HypothesisSpecId,
    ObservableReference,
)

__all__ = [
    "DETECTED_EVENT_IDENTITY_DOMAIN",
    "EVENT_SPEC_IDENTITY_DOMAIN",
    "EVENT_SPEC_MODEL_VERSION",
    "HYPOTHESIS_SPEC_IDENTITY_DOMAIN",
    "HYPOTHESIS_SPEC_MODEL_VERSION",
    "ComparisonOperator",
    "DetectedEvent",
    "DirectionRequirement",
    "EventDetectionError",
    "EventSpec",
    "EventSpecError",
    "EventSpecId",
    "HypothesisSpec",
    "HypothesisSpecError",
    "HypothesisSpecId",
    "ObservableReference",
    "ThresholdPredicate",
    "detect_events",
]
