#!/usr/bin/env python3
"""F02 EventSpec and causal event detection runtime v1 proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from quant_platform.data.models import CoverageInterval, DatasetIdentity, NaturalPartitionIdentity  # noqa: E402
from quant_platform.features import (  # noqa: E402
    BoundInputEvidence,
    BoundOutputPartition,
    BoundSourceDataset,
    BoundSourcePartition,
    ConstituentFeatureOutput,
    FeatureArtifact,
    FeatureArtifactContentIdentity,
    FeatureArtifactError,
    FeatureDefinitionId,
    FeatureObservation,
    FeatureSetDefinitionIdentity,
    InputContractV1,
    Instant,
    NumericalEquivalence,
    ObservationLifecycle,
    OutputContract,
    OutputDimension,
    OutputValueKind,
    SupportIdentity,
    SupportReference,
    SupportShape,
    compute_observation_evidence_fingerprint,
    rehydrate_feature_artifact,
    seal_feature_artifact,
)
from quant_platform.research import (  # noqa: E402
    ComparisonOperator,
    DetectedEvent,
    DirectionRequirement,
    EventDetectionError,
    EventSpec,
    EventSpecError,
    EventSpecId,
    HypothesisSpec,
    ObservableReference,
    ThresholdPredicate,
    detect_events,
)


def observable_id(key: str) -> FeatureDefinitionId:
    return FeatureDefinitionId.from_payload({"feature_key": key})


STACKED_IMBALANCE_ID = observable_id("order_flow.stacked_imbalance")
OTHER_OBSERVABLE_ID = observable_id("order_flow.diagonal_imbalance")

CONTRACT = InputContractV1("footprint.price_level", "1", ("buy_volume", "sell_volume"))


def hypothesis(**overrides) -> HypothesisSpec:
    kwargs = dict(
        hypothesis_key="stacked_imbalance_precedes_move",
        semantic_version="1",
        statement=(
            "Stacked buy-side imbalance beyond a threshold tends to precede "
            "a short-horizon directional move."
        ),
        observable_references=(ObservableReference(STACKED_IMBALANCE_ID),),
    )
    kwargs.update(overrides)
    return HypothesisSpec(**kwargs)


HYPOTHESIS = hypothesis()


def event_spec(**overrides) -> EventSpec:
    kwargs = dict(
        event_key="order_flow.stacked_imbalance_detected",
        semantic_version="1",
        hypothesis_id=HYPOTHESIS.identity,
        observable_id=str(STACKED_IMBALANCE_ID),
        predicate=ThresholdPredicate(
            ComparisonOperator.GREATER_THAN_OR_EQUAL,
            "2",
            DirectionRequirement.POSITIVE,
        ),
    )
    kwargs.update(overrides)
    return EventSpec(**kwargs)


def observation(
    *,
    definition_id: FeatureDefinitionId = STACKED_IMBALANCE_ID,
    bucket: str | None = None,
    value=2.5,
    lifecycle: ObservationLifecycle = ObservationLifecycle.FINAL,
    causal_available_at: str = "2024-01-01T00:00:01Z",
    observed_finalized_at: str | None = None,
) -> FeatureObservation:
    if bucket is None:
        bucket = f"bar:{causal_available_at}"
    support = SupportIdentity(CONTRACT.identity, bucket, SupportReference.current())
    kwargs = dict(
        definition_id=definition_id,
        support_identity=support,
        value=value,
        lifecycle=lifecycle,
        causal_available_at=causal_available_at,
    )
    if lifecycle == ObservationLifecycle.FINAL:
        kwargs["observed_finalized_at"] = observed_finalized_at or causal_available_at
    return FeatureObservation(**kwargs)


ARTIFACT_SOURCE_DATASET_IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
ARTIFACT_SUPPORT_INTERVAL = CoverageInterval(
    Instant.parse("2024-01-01T00:00:00Z"), Instant.parse("2024-01-02T00:00:00Z")
)


def artifact_output_dataset_identity(slug: str = "order_flow", version: int = 1) -> DatasetIdentity:
    return DatasetIdentity(
        "features", slug, "bybit", "BTCUSDT", "feature-v1",
        feature_set_slug=slug, feature_set_version=version,
    )


def sealed_artifact_for(
    definition_id: FeatureDefinitionId,
    observations: Sequence[FeatureObservation] | None = None,
) -> FeatureArtifact:
    """A minimal, genuinely sealed E04 FeatureArtifact covering exactly
    ``definition_id``, built the same way tests/test_feature_artifact_v1.py
    builds one: via `seal_feature_artifact()`, never direct construction."""

    output_contract = OutputContract(
        OutputValueKind.NUMERIC, "scalar", OutputDimension.DIMENSIONLESS, NumericalEquivalence.exact(),
    )
    if observations is None:
        sealed_observations = (
            FeatureObservation(
                definition_id=definition_id,
                support_identity=SupportIdentity(
                    CONTRACT.identity,
                    f"bar:{ARTIFACT_SUPPORT_INTERVAL.start.isoformat()}",
                    SupportReference.current(),
                ),
                value="1.0",
                lifecycle=ObservationLifecycle.FINAL,
                causal_available_at=ARTIFACT_SUPPORT_INTERVAL.start,
            ),
        )
    else:
        sealed_observations = tuple(observations)
    output_partition = BoundOutputPartition(
        natural_identity=NaturalPartitionIdentity(artifact_output_dataset_identity(), "dt=2024-01-01", 1),
        content_sha256="a" * 64,
        manifest_sha256=compute_observation_evidence_fingerprint(sealed_observations),
    )
    source_partition = BoundSourcePartition(
        natural_identity=NaturalPartitionIdentity(ARTIFACT_SOURCE_DATASET_IDENTITY, "dt=2024-01-01", 1),
        content_sha256="1" * 64,
        manifest_sha256="1" * 64,
    )
    declared_support = SupportShape(intervals=(ARTIFACT_SUPPORT_INTERVAL,))
    return seal_feature_artifact(
        feature_set_definition_identity=FeatureSetDefinitionIdentity("order_flow", 1),
        bound_input_evidence=BoundInputEvidence(
            sources=(BoundSourceDataset(ARTIFACT_SOURCE_DATASET_IDENTITY, (source_partition,)),),
            consumed_support=declared_support,
            provenance_identity="test-provenance:1",
        ),
        declared_materialized_support=declared_support,
        implementation_code_identity="commit-1",
        content_identity=FeatureArtifactContentIdentity(
            output_partitions=(output_partition,),
            declared_support=declared_support,
        ),
        constituent_output_contracts=(ConstituentFeatureOutput(definition_id, output_contract),),
        observations=sealed_observations,
        expected_observation_identities=tuple(obs.identity for obs in sealed_observations),
    )


class EventSpecIdentityTests(unittest.TestCase):
    def test_same_semantic_declaration_yields_same_identity(self):
        a = event_spec()
        b = event_spec()
        self.assertEqual(a.identity, b.identity)
        self.assertIsInstance(a.spec_id, EventSpecId)

    def test_changed_semantic_field_yields_distinct_identity(self):
        baseline = event_spec()
        other_hypothesis = hypothesis(hypothesis_key="different_hypothesis")
        variants = [
            event_spec(event_key="order_flow.other_event"),
            event_spec(semantic_version="2"),
            event_spec(hypothesis_id=other_hypothesis.identity),
            event_spec(observable_id=str(OTHER_OBSERVABLE_ID)),
            event_spec(
                predicate=ThresholdPredicate(
                    ComparisonOperator.GREATER_THAN_OR_EQUAL, "3", DirectionRequirement.POSITIVE
                )
            ),
            event_spec(
                predicate=ThresholdPredicate(
                    ComparisonOperator.LESS_THAN, "2", DirectionRequirement.POSITIVE
                )
            ),
            event_spec(
                predicate=ThresholdPredicate(
                    ComparisonOperator.GREATER_THAN_OR_EQUAL, "2", DirectionRequirement.ANY
                )
            ),
        ]
        for variant in variants:
            with self.subTest(variant=variant.event_key):
                self.assertNotEqual(baseline.identity, variant.identity)

    def test_canonical_payload_excludes_runtime_locators(self):
        spec = event_spec()
        payload_text = spec.canonical_utf8_serialization
        for runtime_locator in ("dataset", "venue", "instrument", "path", "backend", "pid"):
            self.assertNotIn(runtime_locator, payload_text)
        self.assertTrue(spec.identity.startswith("event-spec-v1:sha256:"))

    def test_event_spec_is_frozen(self):
        spec = event_spec()
        with self.assertRaises(FrozenInstanceError):
            spec.event_key = "mutated"  # type: ignore[misc]

    def test_identity_is_stable_across_independent_processes(self):
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            from quant_platform.features import FeatureDefinitionId
            from quant_platform.research import (
                ComparisonOperator, DirectionRequirement, EventSpec, HypothesisSpec,
                ObservableReference, ThresholdPredicate,
            )

            stacked = FeatureDefinitionId.from_payload(
                {{"feature_key": "order_flow.stacked_imbalance"}}
            )
            hyp = HypothesisSpec(
                hypothesis_key="stacked_imbalance_precedes_move",
                semantic_version="1",
                statement=(
                    "Stacked buy-side imbalance beyond a threshold tends to precede "
                    "a short-horizon directional move."
                ),
                observable_references=(ObservableReference(stacked),),
            )
            spec = EventSpec(
                event_key="order_flow.stacked_imbalance_detected",
                semantic_version="1",
                hypothesis_id=hyp.identity,
                observable_id=str(stacked),
                predicate=ThresholdPredicate(
                    ComparisonOperator.GREATER_THAN_OR_EQUAL, "2", DirectionRequirement.POSITIVE
                ),
            )
            print(spec.identity)
            """
        )
        identities = set()
        for hash_seed in ("0", "1", "random"):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            identities.add(result.stdout.strip())
        self.assertEqual(1, len(identities))
        self.assertEqual(event_spec().identity, next(iter(identities)))


class EventSpecValidationTests(unittest.TestCase):
    def test_empty_event_key_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(event_key="")

    def test_non_canonical_event_key_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(event_key="Not A Valid Key")

    def test_invalid_semantic_version_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(semantic_version="0")
        with self.assertRaises(EventSpecError):
            event_spec(semantic_version="v1")

    def test_malformed_hypothesis_id_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(hypothesis_id="not-a-real-id")
        with self.assertRaises(EventSpecError):
            event_spec(hypothesis_id="feature-definition-v1:sha256:" + "a" * 64)

    def test_malformed_observable_id_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(observable_id="not-a-real-id")
        with self.assertRaises(EventSpecError):
            event_spec(observable_id="hypothesis-spec-v1:sha256:" + "a" * 64)

    def test_non_predicate_value_is_refused(self):
        with self.assertRaises(EventSpecError):
            event_spec(predicate={"operator": "greater_than", "threshold": "2"})


class ThresholdPredicateTests(unittest.TestCase):
    def test_boundary_conditions_on_threshold(self):
        gte = ThresholdPredicate(ComparisonOperator.GREATER_THAN_OR_EQUAL, "2")
        self.assertTrue(gte.evaluate(2))
        self.assertTrue(gte.evaluate("2.0"))
        self.assertTrue(gte.evaluate(2.5))
        self.assertFalse(gte.evaluate(1.9999))

        gt = ThresholdPredicate(ComparisonOperator.GREATER_THAN, "2")
        self.assertFalse(gt.evaluate(2))
        self.assertTrue(gt.evaluate("2.0000001"))

        lte = ThresholdPredicate(ComparisonOperator.LESS_THAN_OR_EQUAL, "2")
        self.assertTrue(lte.evaluate(2))
        self.assertFalse(lte.evaluate(2.0001))

        lt = ThresholdPredicate(ComparisonOperator.LESS_THAN, "2")
        self.assertFalse(lt.evaluate(2))
        self.assertTrue(lt.evaluate(1.9999))

        eq = ThresholdPredicate(ComparisonOperator.EQUAL, "2")
        self.assertTrue(eq.evaluate("2.0"))
        self.assertFalse(eq.evaluate(2.0001))

        ne = ThresholdPredicate(ComparisonOperator.NOT_EQUAL, "2")
        self.assertFalse(ne.evaluate(2))
        self.assertTrue(ne.evaluate(2.0001))

    def test_direction_requirement_is_enforced_independently_of_threshold(self):
        positive_only = ThresholdPredicate(
            ComparisonOperator.GREATER_THAN_OR_EQUAL, "-100", DirectionRequirement.POSITIVE
        )
        self.assertTrue(positive_only.evaluate(5))
        self.assertFalse(positive_only.evaluate(0))
        self.assertFalse(positive_only.evaluate(-5))

        non_negative = ThresholdPredicate(
            ComparisonOperator.GREATER_THAN_OR_EQUAL, "-100", DirectionRequirement.NON_NEGATIVE
        )
        self.assertTrue(non_negative.evaluate(0))

        negative_only = ThresholdPredicate(
            ComparisonOperator.LESS_THAN_OR_EQUAL, "100", DirectionRequirement.NEGATIVE
        )
        self.assertTrue(negative_only.evaluate(-1))
        self.assertFalse(negative_only.evaluate(0))

    def test_non_numeric_value_fails_closed(self):
        predicate = ThresholdPredicate(ComparisonOperator.GREATER_THAN, "2")
        with self.assertRaises(EventSpecError):
            predicate.evaluate("not-a-number")
        with self.assertRaises(EventSpecError):
            predicate.evaluate(True)

    def test_invalid_threshold_is_refused_at_construction(self):
        with self.assertRaises(EventSpecError):
            ThresholdPredicate(ComparisonOperator.GREATER_THAN, "not-a-number")
        with self.assertRaises(EventSpecError):
            ThresholdPredicate("worse_than", "2")  # type: ignore[arg-type]


class DetectEventsTests(unittest.TestCase):
    def test_no_matching_observations_yield_empty_list(self):
        spec = event_spec()
        below_threshold = observation(value=1.0, causal_available_at="2024-01-01T00:00:01Z")
        result = detect_events(spec, [below_threshold])
        self.assertEqual([], result)

    def test_empty_observations_yield_empty_list_cleanly(self):
        spec = event_spec()
        self.assertEqual([], detect_events(spec, []))
        self.assertEqual([], detect_events(spec, iter(())))

    def test_matching_observation_produces_traceable_detected_event(self):
        spec = event_spec()
        match = observation(
            bucket="bar:2024-01-01T00:05:00Z",
            value=3.25,
            causal_available_at="2024-01-01T00:05:01Z",
        )
        [detected] = detect_events(spec, [match])
        self.assertIsInstance(detected, DetectedEvent)
        self.assertEqual(spec.identity, detected.event_spec_id)
        self.assertEqual(str(STACKED_IMBALANCE_ID), detected.observable_id)
        self.assertEqual(match.identity, detected.observation_identity)
        self.assertEqual("2024-01-01T00:05:00Z", str(detected.event_time))
        self.assertEqual("2024-01-01T00:05:01Z", str(detected.causal_available_at))
        self.assertEqual(3.25, detected.match_evidence["value"])
        self.assertTrue(detected.event_id.startswith("detected-event-v1:sha256:"))

    def test_causal_available_at_never_precedes_observation_causal_floor(self):
        spec = event_spec()
        match = observation(causal_available_at="2024-01-01T00:00:01Z")
        [detected] = detect_events(spec, [match])
        self.assertGreaterEqual(detected.causal_available_at, match.causal_available_at)
        self.assertEqual(detected.causal_available_at, match.causal_available_at)

    def test_event_time_is_the_bucket_close_not_the_finalization_timestamp(self):
        # A bar closing (causal_available_at) at 10:00 that is only
        # confirmed FINAL (observed_finalized_at) at 10:05 must still report
        # event_time == 10:00 -- the market bucket's own instant -- never
        # drifting forward to the later finalization/knowledge timestamp.
        spec = event_spec()
        late_finalized = observation(
            value=5.0,
            causal_available_at="2024-01-01T10:00:00Z",
            observed_finalized_at="2024-01-01T10:05:00Z",
        )
        [detected] = detect_events(spec, [late_finalized])
        self.assertEqual("2024-01-01T10:00:00Z", str(detected.event_time))

    def test_causal_available_at_reflects_delayed_finalization_latency(self):
        # Non-anticipation: if the evidence was not actually proven FINAL
        # until 10:05, the detected event must not be treated as legally
        # available before that -- even though the bucket itself closed
        # (causal_available_at) at 10:00.
        spec = event_spec()
        late_finalized = observation(
            value=5.0,
            causal_available_at="2024-01-01T10:00:00Z",
            observed_finalized_at="2024-01-01T10:05:00Z",
        )
        [detected] = detect_events(spec, [late_finalized])
        self.assertEqual("2024-01-01T10:05:00Z", str(detected.causal_available_at))
        self.assertGreater(detected.causal_available_at, detected.event_time)
        self.assertGreaterEqual(detected.causal_available_at, late_finalized.observed_finalized_at)

    def test_detect_events_accepts_a_sealed_artifact_covering_the_observable(self):
        spec = event_spec()
        match = observation(value=5.0)
        artifact = sealed_artifact_for(STACKED_IMBALANCE_ID, [match])
        result = detect_events(spec, [match], artifact=artifact)
        self.assertEqual(1, len(result))

    def test_detect_events_refuses_observation_outside_artifact_declared_support(self):
        spec = event_spec()
        inside = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        outside = observation(
            bucket="bar:2024-01-03T00:00:00Z",
            value=5.0,
            causal_available_at="2024-01-03T00:00:01Z",
        )
        artifact = sealed_artifact_for(STACKED_IMBALANCE_ID, [inside])
        with self.assertRaisesRegex(EventDetectionError, "falls outside artifact declared_materialized_support"):
            detect_events(spec, [inside, outside], artifact=artifact)

    def test_detect_events_refuses_observation_with_different_identity_in_same_interval(self):
        spec = event_spec()
        inside = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        different_identity_same_interval = observation(
            bucket="bar:2024-01-01T13:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T13:00:01Z",
        )
        different_value_same_coordinate = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=6.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        artifact = sealed_artifact_for(STACKED_IMBALANCE_ID, [inside])
        with self.assertRaisesRegex(EventDetectionError, "was not sealed by artifact"):
            detect_events(spec, [different_identity_same_interval], artifact=artifact)
        with self.assertRaisesRegex(EventDetectionError, "was not sealed by artifact"):
            detect_events(spec, [different_value_same_coordinate], artifact=artifact)

    def test_detect_events_consumes_observations_directly_from_artifact(self):
        spec = event_spec()
        match = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        artifact = sealed_artifact_for(STACKED_IMBALANCE_ID, [match])
        result = detect_events(spec, artifact)
        self.assertEqual(1, len(result))
        self.assertEqual(match.identity, result[0].observation_identity)

    def test_event_time_derived_from_canonical_support_coordinate(self):
        spec = event_spec()
        obs = observation(
            bucket="bar:2024-01-01T08:30:00Z",
            value=5.0,
            causal_available_at="2024-01-01T08:30:05Z",
            observed_finalized_at="2024-01-01T08:35:00Z",
        )
        [detected] = detect_events(spec, [obs])
        self.assertEqual("2024-01-01T08:30:00Z", str(detected.event_time))
        self.assertEqual("2024-01-01T08:35:00Z", str(detected.causal_available_at))

    def test_support_coordinate_later_than_causal_availability_fails_closed(self):
        spec = event_spec()
        bad_obs = observation(
            bucket="bar:2024-01-01T10:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T09:00:00Z",
        )
        with self.assertRaisesRegex(EventDetectionError, "later than causal_available_at"):
            detect_events(spec, [bad_obs])

    def test_opaque_or_unparseable_support_coordinate_fails_closed(self):
        spec = event_spec()
        opaque = observation(
            bucket="opaque-sequence-42",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        with self.assertRaisesRegex(EventDetectionError, "governed canonical bucket-time coordinate shape"):
            detect_events(spec, [opaque])

    def test_malformed_support_coordinate_prefix_fails_closed(self):
        spec = event_spec()
        malformed_prefix = observation(
            bucket="opaque:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        with self.assertRaisesRegex(EventDetectionError, "governed canonical bucket-time coordinate shape"):
            detect_events(spec, [malformed_prefix])

    def test_malformed_support_coordinate_suffix_fails_closed(self):
        spec = event_spec()
        malformed_suffix = observation(
            bucket="bar:2024-01-01T12:00:00Z:junk",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        with self.assertRaisesRegex(EventDetectionError, "governed canonical bucket-time coordinate shape"):
            detect_events(spec, [malformed_suffix])

    def test_multiple_timestamps_in_support_coordinate_fails_closed(self):
        spec = event_spec()
        multi_ts = observation(
            bucket="bar:2024-01-01T12:00:00Z:2024-01-01T13:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T13:00:01Z",
        )
        with self.assertRaisesRegex(EventDetectionError, "governed canonical bucket-time coordinate shape"):
            detect_events(spec, [multi_ts])

    def test_rehydrated_artifact_with_empty_evidence_is_refused(self):
        spec = event_spec()
        obs = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        sealed = sealed_artifact_for(STACKED_IMBALANCE_ID, [obs])
        rehydrated = rehydrate_feature_artifact(
            expected_identity=sealed.identity,
            feature_set_definition_identity=sealed.feature_set_definition_identity,
            bound_input_evidence=sealed.bound_input_evidence,
            declared_materialized_support=sealed.declared_materialized_support,
            implementation_code_identity=sealed.implementation_code_identity,
            content_identity=sealed.content_identity,
            constituent_output_contracts=sealed.constituent_output_contracts,
            materialization_contract_version=sealed.materialization_contract_version,
            physical_locators=sealed.physical_locators,
            sealed_observation_identities=(),
            sealed_observations=(),
            sealed_observation_digest="",
        )
        with self.assertRaisesRegex(EventDetectionError, "lacks sealed observation evidence"):
            detect_events(spec, [obs], artifact=rehydrated)

    def test_detect_events_refuses_substituted_forged_evidence_in_rehydrated_artifact(self):
        # Codex finding: rehydration must not allow non-empty forged/substituted
        # observations while preserving the artifact identity.
        spec = event_spec()
        real_obs = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        sealed = sealed_artifact_for(STACKED_IMBALANCE_ID, [real_obs])
        forged_obs = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=999.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        # Rehydrating with non-empty forged evidence differing from content commitment fails closed
        with self.assertRaisesRegex(FeatureArtifactError, "evidence substitution or forgery rejected"):
            rehydrate_feature_artifact(
                expected_identity=sealed.identity,
                feature_set_definition_identity=sealed.feature_set_definition_identity,
                bound_input_evidence=sealed.bound_input_evidence,
                declared_materialized_support=sealed.declared_materialized_support,
                implementation_code_identity=sealed.implementation_code_identity,
                content_identity=sealed.content_identity,
                constituent_output_contracts=sealed.constituent_output_contracts,
                materialization_contract_version=sealed.materialization_contract_version,
                physical_locators=sealed.physical_locators,
                sealed_observation_identities=(forged_obs.identity,),
                sealed_observations=(forged_obs,),
            )

    def test_detect_events_refuses_artifact_with_forged_evidence_differing_from_content_commitment(self):
        # detect_events fails closed if an artifact's sealed observations do not match
        # its identity-bound partition commitment manifest hash.
        spec = event_spec()
        obs = observation(
            bucket="bar:2024-01-01T12:00:00Z",
            value=5.0,
            causal_available_at="2024-01-01T12:00:01Z",
        )
        tampered_content = FeatureArtifactContentIdentity(
            output_partitions=(
                BoundOutputPartition(
                    natural_identity=NaturalPartitionIdentity(artifact_output_dataset_identity(), "dt=2024-01-01", 1),
                    content_sha256="0" * 64,
                    manifest_sha256="0" * 64,
                ),
            ),
            declared_support=SupportShape(intervals=(ARTIFACT_SUPPORT_INTERVAL,)),
        )
        mismatched_artifact = seal_feature_artifact(
            feature_set_definition_identity=FeatureSetDefinitionIdentity("order_flow", 1),
            bound_input_evidence=BoundInputEvidence(
                sources=(BoundSourceDataset(ARTIFACT_SOURCE_DATASET_IDENTITY, (
                    BoundSourcePartition(
                        natural_identity=NaturalPartitionIdentity(ARTIFACT_SOURCE_DATASET_IDENTITY, "dt=2024-01-01", 1),
                        content_sha256="1" * 64,
                        manifest_sha256="1" * 64,
                    ),
                )),),
                consumed_support=SupportShape(intervals=(ARTIFACT_SUPPORT_INTERVAL,)),
                provenance_identity="b04-coverage-reconstruction:1",
            ),
            declared_materialized_support=SupportShape(intervals=(ARTIFACT_SUPPORT_INTERVAL,)),
            implementation_code_identity="commit-1",
            content_identity=tampered_content,
            constituent_output_contracts=(
                ConstituentFeatureOutput(
                    STACKED_IMBALANCE_ID,
                    OutputContract(
                        OutputValueKind.NUMERIC, "scalar", OutputDimension.DIMENSIONLESS, NumericalEquivalence.exact(),
                    ),
                ),
            ),
            observations=[obs],
            expected_observation_identities=[obs.identity],
        )
        with self.assertRaisesRegex(EventDetectionError, "forged or substituted evidence"):
            detect_events(spec, [obs], artifact=mismatched_artifact)

    def test_detect_events_refuses_an_unsealed_artifact(self):
        spec = event_spec()
        match = observation(value=5.0)

        class NotSealed:
            lifecycle = "FINAL"

        with self.assertRaisesRegex(EventDetectionError, "sealed"):
            detect_events(spec, [match], artifact=NotSealed())  # type: ignore[arg-type]

    def test_detect_events_refuses_an_artifact_that_does_not_cover_the_observable(self):
        spec = event_spec()
        match = observation(value=5.0)
        mismatched_artifact = sealed_artifact_for(OTHER_OBSERVABLE_ID)
        with self.assertRaisesRegex(EventDetectionError, "does not seal"):
            detect_events(spec, [match], artifact=mismatched_artifact)

    def test_provisional_observations_are_refused(self):
        spec = event_spec()
        provisional = observation(value=5.0, lifecycle=ObservationLifecycle.PROVISIONAL)
        with self.assertRaisesRegex(EventDetectionError, "FINAL"):
            detect_events(spec, [provisional])

    def test_observation_for_a_different_observable_is_refused(self):
        spec = event_spec()
        mismatched = observation(definition_id=OTHER_OBSERVABLE_ID, value=5.0)
        with self.assertRaisesRegex(EventDetectionError, "observable"):
            detect_events(spec, [mismatched])

    def test_observations_out_of_temporal_order_are_refused(self):
        spec = event_spec()
        first = observation(bucket="bar:2024-01-01T00:00:00Z", causal_available_at="2024-01-01T00:00:01Z")
        second = observation(bucket="bar:2024-01-01T00:01:00Z", causal_available_at="2024-01-01T00:01:01Z")
        with self.assertRaisesRegex(EventDetectionError, "order"):
            detect_events(spec, [second, first])

    def test_equal_causal_available_at_is_accepted_as_non_decreasing(self):
        spec = event_spec()
        first = observation(bucket="bar:2024-01-01T00:00:00Z", value=5.0, causal_available_at="2024-01-01T00:00:01Z")
        second = observation(bucket="bar:2024-01-01T00:00:00.500Z", value=5.0, causal_available_at="2024-01-01T00:00:01Z")
        result = detect_events(spec, [first, second])
        self.assertEqual(2, len(result))

    def test_duplicate_observation_identity_is_refused(self):
        spec = event_spec()
        match = observation(value=5.0)
        with self.assertRaisesRegex(EventDetectionError, "duplicate"):
            detect_events(spec, [match, match])

    def test_non_feature_observation_input_is_refused(self):
        spec = event_spec()
        with self.assertRaises(EventDetectionError):
            detect_events(spec, [{"value": 5.0}])  # type: ignore[list-item]

    def test_non_event_spec_first_argument_is_refused(self):
        with self.assertRaises(EventDetectionError):
            detect_events("not-a-spec", [])  # type: ignore[arg-type]

    def test_reproducibility_across_independent_calls(self):
        spec = event_spec()
        observations = [
            observation(bucket="bar:2024-01-01T00:00:00Z", value=2.0, causal_available_at="2024-01-01T00:00:01Z"),
            observation(bucket="bar:2024-01-01T00:01:00Z", value=0.5, causal_available_at="2024-01-01T00:01:01Z"),
            observation(bucket="bar:2024-01-01T00:02:00Z", value=4.0, causal_available_at="2024-01-01T00:02:01Z"),
        ]
        first_run = detect_events(spec, observations)
        second_run = detect_events(spec, observations)
        self.assertEqual([event.event_id for event in first_run], [event.event_id for event in second_run])
        self.assertEqual(2, len(first_run))

    def test_reproducibility_across_independent_processes(self):
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            from quant_platform.features import (
                FeatureDefinitionId, FeatureObservation, InputContractV1, ObservationLifecycle,
                SupportIdentity, SupportReference,
            )
            from quant_platform.research import (
                ComparisonOperator, DirectionRequirement, EventSpec, HypothesisSpec,
                ObservableReference, ThresholdPredicate, detect_events,
            )

            stacked = FeatureDefinitionId.from_payload(
                {{"feature_key": "order_flow.stacked_imbalance"}}
            )
            hyp = HypothesisSpec(
                hypothesis_key="stacked_imbalance_precedes_move",
                semantic_version="1",
                statement=(
                    "Stacked buy-side imbalance beyond a threshold tends to precede "
                    "a short-horizon directional move."
                ),
                observable_references=(ObservableReference(stacked),),
            )
            spec = EventSpec(
                event_key="order_flow.stacked_imbalance_detected",
                semantic_version="1",
                hypothesis_id=hyp.identity,
                observable_id=str(stacked),
                predicate=ThresholdPredicate(
                    ComparisonOperator.GREATER_THAN_OR_EQUAL, "2", DirectionRequirement.POSITIVE
                ),
            )
            contract = InputContractV1("footprint.price_level", "1", ("buy_volume", "sell_volume"))
            support = SupportIdentity(contract.identity, "bar:2024-01-01T00:05:00Z", SupportReference.current())
            match = FeatureObservation(
                stacked, support, value=3.25, lifecycle=ObservationLifecycle.FINAL,
                causal_available_at="2024-01-01T00:05:01Z",
                observed_finalized_at="2024-01-01T00:05:01Z",
            )
            [detected] = detect_events(spec, [match])
            print(detected.event_id)
            """
        )
        identities = set()
        for hash_seed in ("0", "1", "random"):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            identities.add(result.stdout.strip())
        self.assertEqual(1, len(identities))

        in_process = detect_events(
            event_spec(),
            [
                observation(
                    bucket="bar:2024-01-01T00:05:00Z",
                    value=3.25,
                    causal_available_at="2024-01-01T00:05:01Z",
                )
            ],
        )
        self.assertEqual(in_process[0].event_id, next(iter(identities)))


class DetectedEventValueModelTests(unittest.TestCase):
    def test_detected_event_is_frozen(self):
        spec = event_spec()
        [detected] = detect_events(spec, [observation(value=5.0)])
        with self.assertRaises(FrozenInstanceError):
            detected.event_time = "2024-01-01T00:00:00Z"  # type: ignore[misc]

    def test_event_id_excludes_match_evidence_key_order(self):
        base = DetectedEvent(
            event_spec_id=event_spec().identity,
            observable_id=str(STACKED_IMBALANCE_ID),
            observation_identity="feature-observation-v1:sha256:" + "a" * 64,
            event_time="2024-01-01T00:00:01Z",
            causal_available_at="2024-01-01T00:00:01Z",
            match_evidence={"value": 3.25, "operator": "greater_than_or_equal"},
        )
        reordered = DetectedEvent(
            event_spec_id=event_spec().identity,
            observable_id=str(STACKED_IMBALANCE_ID),
            observation_identity="feature-observation-v1:sha256:" + "a" * 64,
            event_time="2024-01-01T00:00:01Z",
            causal_available_at="2024-01-01T00:00:01Z",
            match_evidence={"operator": "greater_than_or_equal", "value": 3.25},
        )
        self.assertEqual(base.event_id, reordered.event_id)

    def test_event_id_changes_with_observation_identity(self):
        kwargs = dict(
            event_spec_id=event_spec().identity,
            observable_id=str(STACKED_IMBALANCE_ID),
            event_time="2024-01-01T00:00:01Z",
            causal_available_at="2024-01-01T00:00:01Z",
            match_evidence={"value": 3.25},
        )
        first = DetectedEvent(observation_identity="feature-observation-v1:sha256:" + "a" * 64, **kwargs)
        second = DetectedEvent(observation_identity="feature-observation-v1:sha256:" + "b" * 64, **kwargs)
        self.assertNotEqual(first.event_id, second.event_id)

    def test_event_time_later_than_causal_available_at_is_refused(self):
        with self.assertRaises(EventSpecError):
            DetectedEvent(
                event_spec_id=event_spec().identity,
                observable_id=str(STACKED_IMBALANCE_ID),
                observation_identity="feature-observation-v1:sha256:" + "a" * 64,
                event_time="2024-01-01T00:00:05Z",
                causal_available_at="2024-01-01T00:00:01Z",
                match_evidence={"value": 3.25},
            )

    def test_malformed_event_spec_id_is_refused(self):
        with self.assertRaises(EventSpecError):
            DetectedEvent(
                event_spec_id="not-a-real-id",
                observable_id=str(STACKED_IMBALANCE_ID),
                observation_identity="feature-observation-v1:sha256:" + "a" * 64,
                event_time="2024-01-01T00:00:01Z",
                causal_available_at="2024-01-01T00:00:01Z",
                match_evidence={},
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
