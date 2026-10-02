#!/usr/bin/env python3
"""F04 EventStudySpec and parameter sweep runtime v1 proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from pathlib import Path
import sys
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from quant_platform.features import FeatureDefinitionId, Instant  # noqa: E402
import quant_platform.research.studies as studies_module  # noqa: E402
from quant_platform.research import (  # noqa: E402
    AggregateMetrics,
    ComparisonOperator,
    DetectedEvent,
    DirectionRequirement,
    EventSpec,
    EventStudyError,
    EventStudyResult,
    EventStudyRuntimeError,
    EventStudySpec,
    EventStudySpecId,
    HypothesisSpec,
    MarketObservation,
    MetricKind,
    ObservableReference,
    OutcomeSpec,
    OutcomeState,
    ParameterSweepResult,
    ParameterSweepSpec,
    ParameterSweepSpecId,
    PopulationRecord,
    SweepSummaryRow,
    ThresholdPredicate,
    run_event_study,
    run_parameter_sweep,
)


def observable_id(key: str) -> FeatureDefinitionId:
    return FeatureDefinitionId.from_payload({"feature_key": key})


STACKED_IMBALANCE_ID = observable_id("order_flow.stacked_imbalance")

HYPOTHESIS = HypothesisSpec(
    hypothesis_key="stacked_imbalance_precedes_move",
    semantic_version="1",
    statement=(
        "Stacked buy-side imbalance beyond a threshold tends to precede "
        "a short-horizon directional move."
    ),
    observable_references=(ObservableReference(STACKED_IMBALANCE_ID),),
)


def event_spec(threshold: str = "2") -> EventSpec:
    return EventSpec(
        event_key="order_flow.stacked_imbalance_detected",
        semantic_version="1",
        hypothesis_id=HYPOTHESIS.identity,
        observable_id=str(STACKED_IMBALANCE_ID),
        predicate=ThresholdPredicate(
            ComparisonOperator.GREATER_THAN_OR_EQUAL, threshold, DirectionRequirement.POSITIVE
        ),
    )


EVENT_SPEC = event_spec()


def outcome_spec(**overrides) -> OutcomeSpec:
    kwargs = dict(
        outcome_key="forward_return.fixed_horizon",
        semantic_version="1",
        horizon_duration="10m",
        sampling_period="5m",
        metric_kind=MetricKind.FORWARD_RETURN,
        price_reference="close",
    )
    kwargs.update(overrides)
    return OutcomeSpec(**kwargs)


OUTCOME_SPEC = outcome_spec()


def study_spec(**overrides) -> EventStudySpec:
    kwargs = dict(
        study_key="order_flow.imbalance_event_study",
        semantic_version="1",
        hypothesis_id=HYPOTHESIS.identity,
        event_spec=EVENT_SPEC,
        outcome_specs=(OUTCOME_SPEC,),
    )
    kwargs.update(overrides)
    return EventStudySpec(**kwargs)


def detected_event(
    *,
    event_time: str,
    causal_available_at: str | None = None,
    spec: EventSpec = EVENT_SPEC,
    observation_suffix: str = "a" * 64,
) -> DetectedEvent:
    return DetectedEvent(
        event_spec_id=spec.identity,
        observable_id=str(STACKED_IMBALANCE_ID),
        observation_identity="feature-observation-v1:sha256:" + observation_suffix,
        event_time=event_time,
        causal_available_at=causal_available_at or event_time,
        match_evidence={"value": "2.5"},
    )


def bar(base_time: str, minutes: int, close: str) -> MarketObservation:
    instant = Instant.parse(base_time).epoch_ns + minutes * 60_000_000_000
    return MarketObservation(instant=Instant(instant), prices={"close": close})


def market_series_for(base_time: str, closes: dict[int, str]) -> list[MarketObservation]:
    return [bar(base_time, minute, close) for minute, close in sorted(closes.items())]


class EventStudySpecIdentityTests(unittest.TestCase):
    def test_same_semantic_declaration_yields_same_identity(self):
        a = study_spec()
        b = study_spec()
        self.assertEqual(a.identity, b.identity)
        self.assertIsInstance(a.spec_id, EventStudySpecId)
        self.assertTrue(a.identity.startswith("event-study-spec-v1:sha256:"))

    def test_changed_semantic_field_yields_distinct_identity(self):
        baseline = study_spec()
        variants = [
            study_spec(study_key="order_flow.other_study"),
            study_spec(semantic_version="2"),
            study_spec(event_spec=event_spec("3")),
            study_spec(outcome_specs=(outcome_spec(price_reference="vwap"),)),
            study_spec(outcome_specs=(OUTCOME_SPEC, outcome_spec(outcome_key="forward_return.other"))),
        ]
        for variant in variants:
            with self.subTest(variant=variant.canonical_utf8_serialization):
                self.assertNotEqual(baseline.identity, variant.identity)

    def test_description_does_not_affect_identity_or_equality(self):
        a = study_spec(description="alpha")
        b = study_spec(description="beta")
        self.assertEqual(a.identity, b.identity)
        self.assertEqual(a, b)

    def test_event_study_spec_is_frozen(self):
        spec = study_spec()
        with self.assertRaises(FrozenInstanceError):
            spec.study_key = "mutated"  # type: ignore[misc]

    def test_rejects_mismatched_hypothesis_binding(self):
        other_hypothesis = HypothesisSpec(
            hypothesis_key="unrelated_hypothesis",
            semantic_version="1",
            statement="An unrelated conditional relationship.",
            observable_references=(ObservableReference(STACKED_IMBALANCE_ID),),
        )
        with self.assertRaises(EventStudyError):
            study_spec(hypothesis_id=other_hypothesis.identity)

    def test_rejects_empty_outcome_specs(self):
        with self.assertRaises(EventStudyError):
            study_spec(outcome_specs=())

    def test_rejects_duplicate_outcome_spec(self):
        with self.assertRaises(EventStudyError):
            study_spec(outcome_specs=(OUTCOME_SPEC, OUTCOME_SPEC))

    def test_rejects_non_event_spec(self):
        with self.assertRaises(EventStudyError):
            study_spec(event_spec="not-an-event-spec")

    def test_rejects_non_outcome_spec_member(self):
        with self.assertRaises(EventStudyError):
            study_spec(outcome_specs=(OUTCOME_SPEC, "not-an-outcome-spec"))


class ValidatedOutcomeSpecIdErrorNarrowingTests(unittest.TestCase):
    """Regression coverage for #246 (H4): _validated_outcome_spec_id narrowed
    from a bare ``except Exception`` to ``except OutcomeError`` so that an
    unrelated bug in OutcomeSpecId construction is no longer relabeled as a
    validation failure."""

    def test_malformed_outcome_spec_id_is_still_wrapped_as_event_study_error(self):
        with self.assertRaises(EventStudyError) as caught:
            studies_module._validated_outcome_spec_id("not-a-valid-outcome-spec-id")
        self.assertIn("must be a valid OutcomeSpecId", str(caught.exception))

    def test_unrelated_construction_error_is_no_longer_masked(self):
        class _BrokenOutcomeSpecId:
            def __init__(self, _value):
                raise TypeError("unexpected constructor failure")

        with mock.patch.object(studies_module, "OutcomeSpecId", _BrokenOutcomeSpecId):
            with self.assertRaises(TypeError):
                studies_module._validated_outcome_spec_id("irrelevant")

    def test_identity_is_stable_across_independent_processes(self):
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            from quant_platform.features import FeatureDefinitionId
            from quant_platform.research import (
                ComparisonOperator, DirectionRequirement, EventSpec, EventStudySpec,
                HypothesisSpec, MetricKind, ObservableReference, OutcomeSpec, ThresholdPredicate,
            )

            observable = FeatureDefinitionId.from_payload({{"feature_key": "order_flow.stacked_imbalance"}})
            hypothesis = HypothesisSpec(
                hypothesis_key="stacked_imbalance_precedes_move",
                semantic_version="1",
                statement=(
                    "Stacked buy-side imbalance beyond a threshold tends to precede "
                    "a short-horizon directional move."
                ),
                observable_references=(ObservableReference(observable),),
            )
            spec = EventSpec(
                event_key="order_flow.stacked_imbalance_detected",
                semantic_version="1",
                hypothesis_id=hypothesis.identity,
                observable_id=str(observable),
                predicate=ThresholdPredicate(
                    ComparisonOperator.GREATER_THAN_OR_EQUAL, "2", DirectionRequirement.POSITIVE
                ),
            )
            outcome = OutcomeSpec(
                outcome_key="forward_return.fixed_horizon",
                semantic_version="1",
                horizon_duration="10m",
                sampling_period="5m",
                metric_kind=MetricKind.FORWARD_RETURN,
                price_reference="close",
            )
            study = EventStudySpec(
                study_key="order_flow.imbalance_event_study",
                semantic_version="1",
                hypothesis_id=hypothesis.identity,
                event_spec=spec,
                outcome_specs=(outcome,),
            )
            print(study.identity)
            """
        )
        import subprocess

        first = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        second = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(first.stdout.strip(), second.stdout.strip())
        self.assertEqual(study_spec().identity, first.stdout.strip())


EVENT_TIME = "2024-01-01T00:05:00Z"


class RunEventStudySingleOutcomeTests(unittest.TestCase):
    def test_completed_population_and_aggregate_for_single_event(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        result = run_event_study(spec, [event], series)

        self.assertIsInstance(result, EventStudyResult)
        self.assertEqual(1, len(result.population_records))
        record = result.population_records[0]
        self.assertIsInstance(record, PopulationRecord)
        self.assertEqual(OutcomeState.COMPLETE, record.state)
        self.assertEqual(Decimal("1") / Decimal("10"), record.realized_value)

        self.assertEqual(1, len(result.aggregates))
        aggregate = result.aggregates[0]
        self.assertIsInstance(aggregate, AggregateMetrics)
        self.assertEqual(1, aggregate.sample_count)
        self.assertEqual(1, aggregate.completed_count)
        self.assertEqual(0, aggregate.censored_count)
        self.assertEqual(0, aggregate.insufficient_coverage_count)
        self.assertEqual(Decimal("1") / Decimal("10"), aggregate.mean_realized_value)
        self.assertEqual(Decimal("1") / Decimal("10"), aggregate.median_realized_value)
        self.assertEqual(Decimal(0), aggregate.std_dev_realized_value)
        self.assertEqual(Decimal(1), aggregate.positive_rate)
        self.assertEqual(Decimal(0), aggregate.negative_rate)

    def test_multiple_events_aggregate_across_the_population(self):
        spec = study_spec()
        event_a = detected_event(event_time=EVENT_TIME, observation_suffix="a" * 64)
        second_time = "2024-01-01T01:00:00Z"
        event_b = detected_event(event_time=second_time, observation_suffix="b" * 64)

        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"}) + market_series_for(
            second_time, {0: "100", 5: "95", 10: "90"}
        )
        result = run_event_study(spec, [event_a, event_b], series)

        self.assertEqual(2, len(result.population_records))
        aggregate = result.aggregates[0]
        self.assertEqual(2, aggregate.sample_count)
        self.assertEqual(2, aggregate.completed_count)
        # (+1/10 and -1/10) average to exactly zero.
        self.assertEqual(Decimal(0), aggregate.mean_realized_value)
        self.assertEqual(Decimal("1") / Decimal("2"), aggregate.positive_rate)
        self.assertEqual(Decimal("1") / Decimal("2"), aggregate.negative_rate)


class RunEventStudyMultipleOutcomesTests(unittest.TestCase):
    def test_multiple_outcome_specs_produce_one_aggregate_each(self):
        short_outcome = outcome_spec(outcome_key="forward_return.short", horizon_duration="5m")
        long_outcome = outcome_spec(outcome_key="forward_return.long", horizon_duration="10m")
        spec = study_spec(outcome_specs=(short_outcome, long_outcome))
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})

        result = run_event_study(spec, [event], series)

        self.assertEqual(2, len(result.population_records))
        self.assertEqual(2, len(result.aggregates))
        self.assertEqual(short_outcome.identity, result.aggregates[0].outcome_spec_id)
        self.assertEqual(long_outcome.identity, result.aggregates[1].outcome_spec_id)
        self.assertEqual(Decimal("1") / Decimal("20"), result.aggregates[0].mean_realized_value)
        self.assertEqual(Decimal("1") / Decimal("10"), result.aggregates[1].mean_realized_value)


class BoundaryCensoringTests(unittest.TestCase):
    def test_mixed_states_are_tracked_without_fabricating_values(self):
        # Events share one continuous forward-looking timeline, so each
        # event's own forward window is exactly "every observation at or
        # after its event_time".
        #
        # ADR-0038 (F03 Finding B): CENSORED_END_OF_DATA requires explicit
        # end-of-data evidence; run_event_study's market_series is a plain
        # finite Sequence[MarketObservation] with no such evidence seam, so
        # a stream that simply runs dry -- like an interior gap -- is
        # INSUFFICIENT_COVERAGE, never CENSORED_END_OF_DATA.
        spec = study_spec()
        event_complete = detected_event(event_time=EVENT_TIME, observation_suffix="a" * 64)
        gap_time = "2024-01-01T02:00:00Z"
        event_gap = detected_event(event_time=gap_time, observation_suffix="b" * 64)
        exhausted_time = "2024-01-01T03:00:00Z"
        event_exhausted = detected_event(event_time=exhausted_time, observation_suffix="c" * 64)

        series = (
            market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
            + market_series_for(gap_time, {0: "100", 10: "108"})  # bar(5) missing
            + market_series_for(exhausted_time, {0: "100", 5: "101"})  # data runs out, nothing follows
        )
        result = run_event_study(spec, [event_complete, event_exhausted, event_gap], series)

        states = {record.event_id: record.state for record in result.population_records}
        self.assertEqual(OutcomeState.COMPLETE, states[event_complete.event_id])
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, states[event_exhausted.event_id])
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, states[event_gap.event_id])

        for record in result.population_records:
            if record.state is not OutcomeState.COMPLETE:
                self.assertIsNone(record.realized_value)

        aggregate = result.aggregates[0]
        self.assertEqual(3, aggregate.sample_count)
        self.assertEqual(1, aggregate.completed_count)
        self.assertEqual(0, aggregate.censored_count)
        self.assertEqual(2, aggregate.insufficient_coverage_count)


class CausalAvailabilityInvariantTests(unittest.TestCase):
    def test_study_result_causal_available_at_is_max_over_population(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        result = run_event_study(spec, [event], series)

        max_population_causal = max(record.causal_available_at for record in result.population_records)
        self.assertEqual(max_population_causal.epoch_ns, result.causal_available_at.epoch_ns)
        self.assertGreaterEqual(result.causal_available_at.epoch_ns, max_population_causal.epoch_ns)

    def test_construction_rejects_causal_available_at_before_population_maximum(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        result = run_event_study(spec, [event], series)
        record = result.population_records[0]
        with self.assertRaises(EventStudyError):
            EventStudyResult(
                study_spec_id=spec.identity,
                population_records=(record,),
                aggregates=result.aggregates,
                causal_available_at=Instant(record.causal_available_at.epoch_ns - 1),
            )


class RunEventStudyRuntimeGuardTests(unittest.TestCase):
    def test_rejects_empty_events(self):
        spec = study_spec()
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [], market_series_for(EVENT_TIME, {0: "100"}))

    def test_rejects_event_bound_to_a_different_event_spec(self):
        spec = study_spec()
        other_event = detected_event(event_time=EVENT_TIME, spec=event_spec("5"))
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [other_event], series)

    def test_rejects_event_carrying_the_expected_spec_id_but_a_mismatched_observable(self):
        # A DetectedEvent's event_spec_id and observable_id are independent
        # fields (DetectedEvent does not itself know EventSpec.observable_id),
        # so an event could carry the expected spec identity while its own
        # observable_id points somewhere else entirely. That must still be
        # rejected: it is exactly the mismatched observable/event binding
        # the fail-closed contract requires closing.
        spec = study_spec()
        other_observable = observable_id("order_flow.unrelated_observable")
        mismatched_event = DetectedEvent(
            event_spec_id=EVENT_SPEC.identity,
            observable_id=str(other_observable),
            observation_identity="feature-observation-v1:sha256:" + "d" * 64,
            event_time=EVENT_TIME,
            causal_available_at=EVENT_TIME,
            match_evidence={"value": "2.5"},
        )
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [mismatched_event], series)

    def test_rejects_duplicate_event(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [event, event], series)

    def test_rejects_out_of_order_market_series(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        series = [bar(EVENT_TIME, 0, "100"), bar(EVENT_TIME, 10, "110"), bar(EVENT_TIME, 5, "105")]
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [event], series)

    def test_rejects_non_detected_event_element(self):
        spec = study_spec()
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, ["not-an-event"], market_series_for(EVENT_TIME, {0: "100"}))

    def test_rejects_non_market_observation_element(self):
        spec = study_spec()
        event = detected_event(event_time=EVENT_TIME)
        with self.assertRaises(EventStudyRuntimeError):
            run_event_study(spec, [event], [{"instant": EVENT_TIME}])


class ReproducibilityTests(unittest.TestCase):
    def test_identical_inputs_yield_identical_study_id(self):
        spec = study_spec()
        event_a = detected_event(event_time=EVENT_TIME)
        event_b = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})

        result_a = run_event_study(spec, [event_a], list(series))
        result_b = run_event_study(spec, [event_b], list(series))

        self.assertEqual(result_a.study_id, result_b.study_id)
        self.assertEqual(result_a.stable_dict(), result_b.stable_dict())


class ParameterSweepSpecIdentityTests(unittest.TestCase):
    def _grid(self):
        short_spec = study_spec(
            study_key="order_flow.imbalance_event_study_short",
            outcome_specs=(outcome_spec(outcome_key="forward_return.short", horizon_duration="5m"),),
        )
        long_spec = study_spec(
            study_key="order_flow.imbalance_event_study_long",
            outcome_specs=(outcome_spec(outcome_key="forward_return.long", horizon_duration="10m"),),
        )
        return short_spec, long_spec

    def test_same_semantic_declaration_yields_same_identity(self):
        short_spec, long_spec = self._grid()

        def build():
            return ParameterSweepSpec(
                sweep_key="order_flow.imbalance_horizon_sweep",
                semantic_version="1",
                hypothesis_id=HYPOTHESIS.identity,
                study_specs=(short_spec, long_spec),
            )

        a, b = build(), build()
        self.assertEqual(a.identity, b.identity)
        self.assertIsInstance(a.spec_id, ParameterSweepSpecId)
        self.assertTrue(a.identity.startswith("parameter-sweep-spec-v1:sha256:"))

    def test_grid_order_affects_identity(self):
        short_spec, long_spec = self._grid()
        forward = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_horizon_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(short_spec, long_spec),
        )
        reversed_ = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_horizon_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(long_spec, short_spec),
        )
        self.assertNotEqual(forward.identity, reversed_.identity)

    def test_rejects_empty_study_specs(self):
        with self.assertRaises(EventStudyError):
            ParameterSweepSpec(
                sweep_key="order_flow.imbalance_horizon_sweep",
                semantic_version="1",
                hypothesis_id=HYPOTHESIS.identity,
                study_specs=(),
            )

    def test_rejects_duplicate_study_spec(self):
        short_spec, _ = self._grid()
        with self.assertRaises(EventStudyError):
            ParameterSweepSpec(
                sweep_key="order_flow.imbalance_horizon_sweep",
                semantic_version="1",
                hypothesis_id=HYPOTHESIS.identity,
                study_specs=(short_spec, short_spec),
            )

    def test_rejects_study_spec_bound_to_a_different_hypothesis(self):
        other_hypothesis = HypothesisSpec(
            hypothesis_key="unrelated_hypothesis",
            semantic_version="1",
            statement="An unrelated conditional relationship.",
            observable_references=(ObservableReference(STACKED_IMBALANCE_ID),),
        )
        short_spec, _ = self._grid()
        with self.assertRaises(EventStudyError):
            ParameterSweepSpec(
                sweep_key="order_flow.imbalance_horizon_sweep",
                semantic_version="1",
                hypothesis_id=other_hypothesis.identity,
                study_specs=(short_spec,),
            )


class RunParameterSweepTests(unittest.TestCase):
    def test_executes_every_grid_point_with_shared_events(self):
        short_spec = study_spec(
            study_key="order_flow.imbalance_event_study_short",
            outcome_specs=(outcome_spec(outcome_key="forward_return.short", horizon_duration="5m"),),
        )
        long_spec = study_spec(
            study_key="order_flow.imbalance_event_study_long",
            outcome_specs=(outcome_spec(outcome_key="forward_return.long", horizon_duration="10m"),),
        )
        sweep_spec = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_horizon_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(short_spec, long_spec),
        )
        event = detected_event(event_time=EVENT_TIME)
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})

        result = run_parameter_sweep(sweep_spec, [event], series)

        self.assertIsInstance(result, ParameterSweepResult)
        self.assertEqual(2, len(result.study_results))
        self.assertEqual(2, len(result.summary_table))
        self.assertEqual(short_spec.identity, result.study_results[0].study_spec_id)
        self.assertEqual(long_spec.identity, result.study_results[1].study_spec_id)
        self.assertTrue(result.sweep_id.startswith("parameter-sweep-result-v1:sha256:"))

    def test_executes_grid_with_per_event_spec_mapping(self):
        threshold_two = study_spec(event_spec=event_spec("2"))
        threshold_three = study_spec(
            study_key="order_flow.imbalance_event_study_tight",
            event_spec=event_spec("3"),
        )
        sweep_spec = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_threshold_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(threshold_two, threshold_three),
        )
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        events_by_event_spec = {
            event_spec("2").identity: [detected_event(event_time=EVENT_TIME, spec=event_spec("2"))],
            event_spec("3").identity: [
                detected_event(event_time=EVENT_TIME, spec=event_spec("3"), observation_suffix="b" * 64)
            ],
        }

        result = run_parameter_sweep(sweep_spec, events_by_event_spec, series)
        self.assertEqual(2, len(result.study_results))

    def test_mapping_missing_a_grid_points_events_fails_closed(self):
        threshold_two = study_spec(event_spec=event_spec("2"))
        threshold_three = study_spec(
            study_key="order_flow.imbalance_event_study_tight",
            event_spec=event_spec("3"),
        )
        sweep_spec = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_threshold_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(threshold_two, threshold_three),
        )
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})
        events_by_event_spec = {
            event_spec("2").identity: [detected_event(event_time=EVENT_TIME, spec=event_spec("2"))],
        }
        with self.assertRaises(EventStudyRuntimeError):
            run_parameter_sweep(sweep_spec, events_by_event_spec, series)

    def test_reproducibility_of_sweep_id(self):
        short_spec = study_spec(
            study_key="order_flow.imbalance_event_study_short",
            outcome_specs=(outcome_spec(outcome_key="forward_return.short", horizon_duration="5m"),),
        )
        sweep_spec = ParameterSweepSpec(
            sweep_key="order_flow.imbalance_horizon_sweep",
            semantic_version="1",
            hypothesis_id=HYPOTHESIS.identity,
            study_specs=(short_spec,),
        )
        series = market_series_for(EVENT_TIME, {0: "100", 5: "105", 10: "110"})

        result_a = run_parameter_sweep(sweep_spec, [detected_event(event_time=EVENT_TIME)], list(series))
        result_b = run_parameter_sweep(sweep_spec, [detected_event(event_time=EVENT_TIME)], list(series))

        self.assertEqual(result_a.sweep_id, result_b.sweep_id)
        self.assertEqual(result_a.stable_dict(), result_b.stable_dict())

    def test_rejects_non_parameter_sweep_spec(self):
        with self.assertRaises(EventStudyRuntimeError):
            run_parameter_sweep("not-a-sweep-spec", [], [])


class AggregateMetricsExactnessTests(unittest.TestCase):
    def test_mean_median_min_max_and_std_dev_over_four_completed_samples(self):
        base_times = [
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            "2024-01-01T02:00:00Z",
            "2024-01-01T03:00:00Z",
        ]
        closes = [("100", "110"), ("100", "120"), ("100", "90"), ("100", "100")]
        spec = study_spec(outcome_specs=(outcome_spec(horizon_duration="5m"),))
        events = [
            detected_event(event_time=base_times[i], observation_suffix=chr(97 + i) * 64)
            for i in range(4)
        ]
        series: list[MarketObservation] = []
        for base_time, (start_close, end_close) in zip(base_times, closes):
            series.extend(market_series_for(base_time, {0: start_close, 5: end_close}))

        result = run_event_study(spec, events, series)
        aggregate = result.aggregates[0]

        # Realized returns: 1/10, 1/5, -1/10, 0
        expected_values = sorted(
            [Decimal(1) / Decimal(10), Decimal(1) / Decimal(5), Decimal(-1) / Decimal(10), Decimal(0)]
        )
        # Match the runtime's fixed-precision Decimal context (see
        # `_DECIMAL_PRECISION` in studies.py) so an irrational std-dev
        # compares equal rather than differing only in trailing digits.
        with localcontext() as ctx:
            ctx.prec = 50
            expected_mean = sum(expected_values, Decimal(0)) / Decimal(4)
            expected_median = (expected_values[1] + expected_values[2]) / Decimal(2)
            expected_variance = sum(((v - expected_mean) ** 2 for v in expected_values), Decimal(0)) / Decimal(4)
            expected_std_dev = expected_variance.sqrt()

        self.assertEqual(4, aggregate.completed_count)
        self.assertEqual(expected_mean, aggregate.mean_realized_value)
        self.assertEqual(expected_median, aggregate.median_realized_value)
        self.assertEqual(expected_std_dev, aggregate.std_dev_realized_value)
        self.assertEqual(min(expected_values), aggregate.min_realized_value)
        self.assertEqual(max(expected_values), aggregate.max_realized_value)
        self.assertEqual(Decimal(2) / Decimal(4), aggregate.positive_rate)
        self.assertEqual(Decimal(1) / Decimal(4), aggregate.negative_rate)

    def test_zero_completed_count_reports_none_for_every_metric(self):
        spec = study_spec(outcome_specs=(outcome_spec(horizon_duration="5m"),))
        event = detected_event(event_time=EVENT_TIME)
        # No forward bars at all and no explicit end-of-data evidence
        # (ADR-0038) -> INSUFFICIENT_COVERAGE.
        series = market_series_for(EVENT_TIME, {0: "100"})
        result = run_event_study(spec, [event], series)
        aggregate = result.aggregates[0]

        self.assertEqual(0, aggregate.completed_count)
        self.assertIsNone(aggregate.mean_realized_value)
        self.assertIsNone(aggregate.median_realized_value)
        self.assertIsNone(aggregate.std_dev_realized_value)
        self.assertIsNone(aggregate.min_realized_value)
        self.assertIsNone(aggregate.max_realized_value)
        self.assertIsNone(aggregate.positive_rate)
        self.assertIsNone(aggregate.negative_rate)


class PopulationRecordValidationTests(unittest.TestCase):
    def test_complete_record_requires_realized_value(self):
        with self.assertRaises(EventStudyError):
            PopulationRecord(
                event_id="evt",
                event_time=EVENT_TIME,
                outcome_spec_id=OUTCOME_SPEC.identity,
                outcome_id="outcome-v1:sha256:" + "a" * 64,
                state=OutcomeState.COMPLETE,
                realized_value=None,
                causal_available_at=EVENT_TIME,
            )

    def test_non_complete_record_rejects_fabricated_realized_value(self):
        with self.assertRaises(EventStudyError):
            PopulationRecord(
                event_id="evt",
                event_time=EVENT_TIME,
                outcome_spec_id=OUTCOME_SPEC.identity,
                outcome_id="outcome-v1:sha256:" + "a" * 64,
                state=OutcomeState.CENSORED_END_OF_DATA,
                realized_value=Decimal("1"),
                causal_available_at=EVENT_TIME,
            )

    def test_causal_available_at_cannot_precede_event_time(self):
        with self.assertRaises(EventStudyError):
            PopulationRecord(
                event_id="evt",
                event_time="2024-01-01T00:10:00Z",
                outcome_spec_id=OUTCOME_SPEC.identity,
                outcome_id="outcome-v1:sha256:" + "a" * 64,
                state=OutcomeState.CENSORED_END_OF_DATA,
                realized_value=None,
                causal_available_at="2024-01-01T00:00:00Z",
            )


class AggregateMetricsValidationTests(unittest.TestCase):
    def test_counts_must_sum_to_sample_count(self):
        with self.assertRaises(EventStudyError):
            AggregateMetrics(
                outcome_spec_id=OUTCOME_SPEC.identity,
                sample_count=3,
                completed_count=1,
                censored_count=1,
                insufficient_coverage_count=0,
                mean_realized_value=Decimal(0),
                median_realized_value=Decimal(0),
                std_dev_realized_value=Decimal(0),
                min_realized_value=Decimal(0),
                max_realized_value=Decimal(0),
                positive_rate=Decimal(0),
                negative_rate=Decimal(0),
            )

    def test_rejects_rate_out_of_bounds(self):
        with self.assertRaises(EventStudyError):
            AggregateMetrics(
                outcome_spec_id=OUTCOME_SPEC.identity,
                sample_count=1,
                completed_count=1,
                censored_count=0,
                insufficient_coverage_count=0,
                mean_realized_value=Decimal(0),
                median_realized_value=Decimal(0),
                std_dev_realized_value=Decimal(0),
                min_realized_value=Decimal(0),
                max_realized_value=Decimal(0),
                positive_rate=Decimal("1.5"),
                negative_rate=Decimal(0),
            )


class SweepSummaryRowValidationTests(unittest.TestCase):
    def _row_kwargs(self, **overrides):
        kwargs = dict(
            study_spec_id=study_spec().identity,
            outcome_spec_id=OUTCOME_SPEC.identity,
            sample_count=1,
            completed_count=1,
            censored_count=0,
            insufficient_coverage_count=0,
            mean_realized_value=Decimal(0),
            positive_rate=Decimal(0),
            negative_rate=Decimal(0),
        )
        kwargs.update(overrides)
        return kwargs

    def test_accepts_a_valid_row(self):
        row = SweepSummaryRow(**self._row_kwargs())
        self.assertEqual(Decimal(0), row.mean_realized_value)

    def test_counts_must_sum_to_sample_count(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(sample_count=3, completed_count=1, censored_count=1))

    def test_rejects_non_finite_mean_realized_value(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(mean_realized_value=float("inf")))

    def test_rejects_float_metric_value(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(mean_realized_value=0.1))

    def test_rejects_rate_out_of_bounds(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(positive_rate=Decimal("1.5")))

    def test_rejects_negative_rate(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(negative_rate=Decimal("-0.1")))

    def test_metric_fields_must_be_none_when_completed_count_is_zero(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(
                **self._row_kwargs(
                    sample_count=1,
                    completed_count=0,
                    censored_count=1,
                    mean_realized_value=Decimal(0),
                    positive_rate=None,
                    negative_rate=None,
                )
            )

    def test_metric_fields_must_not_be_none_when_completed_count_is_positive(self):
        with self.assertRaises(EventStudyError):
            SweepSummaryRow(**self._row_kwargs(mean_realized_value=None))


if __name__ == "__main__":
    unittest.main()
