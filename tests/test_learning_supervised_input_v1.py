#!/usr/bin/env python3
"""I04 supervised input selection and anti-leakage proof."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, Instant
from quant_platform.features.definitions import (
    FeatureDefinitionId,
    FeatureObservation,
    ObservationLifecycle,
    SupportIdentity,
    SupportReference,
)
from quant_platform.learning import (
    LearningError,
    ProjectionSide,
    SupervisedFeatureInput,
    SupervisedSampleCandidate,
    SupervisedSelectionPolicy,
    build_supervised_projection,
)
from quant_platform.validation import (
    Embargo,
    LabelDefinition,
    LabelStatus,
    LabelTransformKind,
    Lockbox,
    OutcomeEvidence,
    OutcomeState,
    WalkForwardFold,
    evaluate_label,
)


FEATURE_ID_A = "feature-definition-v1:sha256:" + "a" * 64
FEATURE_ID_B = "feature-definition-v1:sha256:" + "b" * 64
OUTCOME_SPEC_ID = "outcome-spec-v1:sha256:" + "c" * 64


def instant(value: str) -> Instant:
    return Instant.parse(value)


def interval(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(instant(start), instant(end))


def fold() -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=0,
        train=interval("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
        test=interval("2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z"),
    )


def policy() -> SupervisedSelectionPolicy:
    return SupervisedSelectionPolicy(
        policy_key="i04.supervised_input",
        semantic_version=1,
        implementation_code_identity="test-code-ref",
    )


def feature(
    feature_id: str,
    *,
    observation_identity: str,
    support: tuple[str, str],
    value: str = "1",
    causal_available_at: str = "2026-01-01T00:00:00Z",
    observed_available_at: str | None = "2026-01-01T00:00:00Z",
    lifecycle: ObservationLifecycle = ObservationLifecycle.PROVISIONAL,
) -> SupervisedFeatureInput:
    support_identity = SupportIdentity(
        input_contract_identity="feature-input-contract-v1:sha256:" + "d" * 64,
        observation_identity=observation_identity,
        support_reference=SupportReference.current(),
    )
    observation = FeatureObservation(
        definition_id=FeatureDefinitionId(feature_id),
        support_identity=support_identity,
        value=value,
        lifecycle=lifecycle,
        causal_available_at=causal_available_at,
        observed_available_at=observed_available_at,
        observed_finalized_at=(
            causal_available_at if lifecycle is ObservationLifecycle.FINAL else None
        ),
    )
    return SupervisedFeatureInput(observation=observation, support=interval(*support))


def label(
    outcome_id_suffix: str,
    *,
    horizon: tuple[str, str],
    causal_available_at: str,
    value: str = "1/100",
    state: OutcomeState = OutcomeState.COMPLETE,
):
    definition = LabelDefinition(
        label_key="returns.fwd",
        semantic_version=1,
        source_outcome_spec_id=OUTCOME_SPEC_ID,
        transform_kind=LabelTransformKind.IDENTITY_VALUE,
    )
    outcome = OutcomeEvidence(
        outcome_id="outcome-v1:sha256:" + outcome_id_suffix * 64,
        outcome_spec_id=OUTCOME_SPEC_ID,
        event_id=f"event-{outcome_id_suffix}",
        horizon_start=horizon[0],
        horizon_end=horizon[1],
        causal_available_at=causal_available_at,
        state=state,
        realized_value=value if state is OutcomeState.COMPLETE else None,
    )
    return evaluate_label(definition, outcome)


def candidate(
    sample_id: str,
    d: str,
    *,
    features: tuple[SupervisedFeatureInput, ...] | None = None,
    target=None,
) -> SupervisedSampleCandidate:
    return SupervisedSampleCandidate(
        sample_id=sample_id,
        decision_time=d,
        features=features
        or (
            feature(
                FEATURE_ID_A,
                observation_identity=f"obs-{sample_id}",
                support=("2026-01-01T00:00:00Z", d),
            ),
        ),
        label=target
        or label(
            sample_id[-1],
            horizon=(d, "2026-01-01T00:45:00Z"),
            causal_available_at="2026-01-01T00:45:00Z",
        ),
    )


class SupervisedInputV1Tests(unittest.TestCase):
    def test_admitted_projection_is_deterministic_and_ordered(self):
        later = candidate("sample-b", "2026-01-01T00:20:00Z")
        earlier = candidate(
            "sample-a",
            "2026-01-01T00:10:00Z",
            target=label(
                "a",
                horizon=("2026-01-01T00:10:00Z", "2026-01-01T00:30:00Z"),
                causal_available_at="2026-01-01T00:30:00Z",
            ),
        )

        first = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(later, earlier),
        )
        second = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(earlier, later),
        )

        self.assertEqual(("sample-a", "sample-b"), tuple(sample.sample_id for sample in first.samples))
        self.assertEqual(ProjectionSide.TRAIN, first.samples[0].side)
        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertTrue(first.identity.startswith("supervised-projection-v1:sha256:"))

    def test_future_feature_availability_is_rejected_by_validation(self):
        future_feature = feature(
            FEATURE_ID_A,
            observation_identity="future-feature",
            support=("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"),
            causal_available_at="2026-01-01T00:11:00Z",
            observed_available_at="2026-01-01T00:11:00Z",
        )
        projection = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(
                candidate("sample-a", "2026-01-01T00:10:00Z", features=(future_feature,)),
            ),
        )

        self.assertEqual((), projection.samples)
        self.assertEqual("UNAVAILABLE", projection.rejections[0].reason)
        self.assertIn("not available by cutoff", projection.rejections[0].detail[0])

    def test_non_complete_label_is_refused_without_fabricating_a_target(self):
        censored = label(
            "e",
            horizon=("2026-01-01T00:10:00Z", "2026-01-01T00:30:00Z"),
            causal_available_at="2026-01-01T00:30:00Z",
            state=OutcomeState.CENSORED_END_OF_DATA,
        )
        self.assertEqual(LabelStatus.NO_VALUE_CENSORED, censored.status)
        projection = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(candidate("sample-e", "2026-01-01T00:10:00Z", target=censored),),
        )

        self.assertEqual((), projection.samples)
        self.assertEqual("LABEL_NOT_AVAILABLE", projection.rejections[0].reason)

    def test_target_support_at_held_out_boundary_is_purged(self):
        target = label(
            "f",
            horizon=("2026-01-01T00:45:00Z", "2026-01-01T01:00:00Z"),
            causal_available_at="2026-01-01T01:00:00Z",
        )
        projection = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(candidate("sample-f", "2026-01-01T00:45:00Z", target=target),),
        )

        self.assertEqual((), projection.samples)
        self.assertEqual("PURGED", projection.rejections[0].reason)

    def test_lockbox_contamination_is_refused(self):
        contaminated_target = label(
            "a",
            horizon=("2026-01-01T00:50:00Z", "2026-01-01T01:10:00Z"),
            causal_available_at="2026-01-01T01:10:00Z",
        )
        projection = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            lockbox=Lockbox(
                lockbox_id="lockbox-v1",
                holdout=interval("2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z"),
            ),
            candidates=(
                candidate("sample-a", "2026-01-01T00:50:00Z", target=contaminated_target),
            ),
        )

        self.assertEqual((), projection.samples)
        self.assertEqual("LOCKBOX_CONTAMINATION", projection.rejections[0].reason)

    def test_duplicate_sample_id_fails_closed(self):
        with self.assertRaises(LearningError):
            build_supervised_projection(
                fold=fold(),
                embargo=Embargo(0),
                selection_policy=policy(),
                candidates=(
                    candidate("sample-a", "2026-01-01T00:10:00Z"),
                    candidate("sample-a", "2026-01-01T00:20:00Z"),
                ),
            )

    def test_sample_uniqueness_uses_only_temporally_admitted_universe(self):
        first = candidate(
            "sample-a",
            "2026-01-01T00:10:00Z",
            target=label(
                "a",
                horizon=("2026-01-01T00:10:00Z", "2026-01-01T00:30:00Z"),
                causal_available_at="2026-01-01T00:30:00Z",
            ),
        )
        second = candidate(
            "sample-b",
            "2026-01-01T00:20:00Z",
            target=label(
                "b",
                horizon=("2026-01-01T00:20:00Z", "2026-01-01T00:40:00Z"),
                causal_available_at="2026-01-01T00:40:00Z",
            ),
        )
        purged = candidate(
            "sample-c",
            "2026-01-01T00:50:00Z",
            features=(
                feature(
                    FEATURE_ID_B,
                    observation_identity="purged-feature",
                    support=("2026-01-01T00:50:00Z", "2026-01-01T01:05:00Z"),
                ),
            ),
            target=label(
                "d",
                horizon=("2026-01-01T00:50:00Z", "2026-01-01T00:55:00Z"),
                causal_available_at="2026-01-01T00:55:00Z",
            ),
        )

        projection = build_supervised_projection(
            fold=fold(),
            embargo=Embargo(0),
            selection_policy=policy(),
            candidates=(purged, second, first),
        )

        weights = {
            sample.sample_id: Fraction(sample.sample_uniqueness_weight)
            for sample in projection.samples
        }
        expected = Fraction(1800000000001, 2400000000002)
        self.assertEqual({"sample-a": expected, "sample-b": expected}, weights)
        self.assertEqual(("sample-c",), tuple(rejection.sample_id for rejection in projection.rejections))
        self.assertEqual("PURGED", projection.rejections[0].reason)


if __name__ == "__main__":
    unittest.main()
