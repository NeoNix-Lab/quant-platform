#!/usr/bin/env python3
"""F03 OutcomeSpec and forward outcome measurement runtime v1 proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from quant_platform.features import FeatureDefinitionId, Instant  # noqa: E402
from quant_platform.research import (  # noqa: E402
    ComparisonOperator,
    DetectedEvent,
    DirectionRequirement,
    EndOfData,
    EventSpec,
    HorizonKind,
    HypothesisSpec,
    MarketObservation,
    MetricKind,
    ObservableReference,
    Outcome,
    OutcomeError,
    OutcomeEvaluationError,
    OutcomeSpec,
    OutcomeSpecId,
    OutcomeState,
    PathMetrics,
    ThresholdPredicate,
    evaluate_outcome,
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

EVENT_SPEC = EventSpec(
    event_key="order_flow.stacked_imbalance_detected",
    semantic_version="1",
    hypothesis_id=HYPOTHESIS.identity,
    observable_id=str(STACKED_IMBALANCE_ID),
    predicate=ThresholdPredicate(
        ComparisonOperator.GREATER_THAN_OR_EQUAL, "2", DirectionRequirement.POSITIVE
    ),
)

EVENT_TIME = "2024-01-01T00:05:00Z"


def detected_event(*, event_time: str = EVENT_TIME, causal_available_at: str | None = None) -> DetectedEvent:
    return DetectedEvent(
        event_spec_id=EVENT_SPEC.identity,
        observable_id=str(STACKED_IMBALANCE_ID),
        observation_identity="feature-observation-v1:sha256:" + "a" * 64,
        event_time=event_time,
        causal_available_at=causal_available_at or event_time,
        match_evidence={"value": "2.5"},
    )


EVENT = detected_event()


def outcome_spec(**overrides) -> OutcomeSpec:
    kwargs = dict(
        outcome_key="forward_return.fixed_horizon",
        semantic_version="1",
        horizon_duration="5m",
        sampling_period="5m",
        metric_kind=MetricKind.FORWARD_RETURN,
        price_reference="close",
    )
    kwargs.update(overrides)
    return OutcomeSpec(**kwargs)


def bar(minutes: int, close: str, *, high: str | None = None, low: str | None = None) -> MarketObservation:
    instant = Instant.parse(EVENT_TIME).epoch_ns + minutes * 60_000_000_000
    prices = {"close": close}
    if high is not None:
        prices["high"] = high
    if low is not None:
        prices["low"] = low
    return MarketObservation(instant=Instant(instant), prices=prices)


class OutcomeSpecIdentityTests(unittest.TestCase):
    def test_same_semantic_declaration_yields_same_identity(self):
        a = outcome_spec()
        b = outcome_spec()
        self.assertEqual(a.identity, b.identity)
        self.assertIsInstance(a.spec_id, OutcomeSpecId)
        self.assertTrue(a.identity.startswith("outcome-spec-v1:sha256:"))

    def test_changed_semantic_field_yields_distinct_identity(self):
        baseline = outcome_spec()
        variants = [
            outcome_spec(outcome_key="forward_return.other"),
            outcome_spec(semantic_version="2"),
            outcome_spec(horizon_duration="10m", sampling_period="5m"),
            outcome_spec(horizon_duration=5),
            outcome_spec(sampling_period="1m"),
            outcome_spec(metric_kind=MetricKind.HIGH_LOW_EXCURSION),
            outcome_spec(price_reference="vwap"),
        ]
        for variant in variants:
            with self.subTest(variant=variant.canonical_utf8_serialization):
                self.assertNotEqual(baseline.identity, variant.identity)

    def test_equivalent_duration_spellings_collapse_to_one_identity(self):
        a = outcome_spec(horizon_duration="1m", sampling_period="1m")
        b = outcome_spec(horizon_duration="60s", sampling_period="60s")
        self.assertEqual(a.identity, b.identity)
        self.assertEqual(a.horizon_duration, b.horizon_duration)
        self.assertEqual(a.sampling_period, b.sampling_period)

    def test_bar_count_and_duration_horizons_never_collide(self):
        duration = outcome_spec(horizon_duration="5m", sampling_period="5m")
        bar_count = outcome_spec(horizon_duration=5, sampling_period="5m")
        self.assertNotEqual(duration.identity, bar_count.identity)
        self.assertEqual(duration.horizon_kind, HorizonKind.DURATION)
        self.assertEqual(bar_count.horizon_kind, HorizonKind.BAR_COUNT)
        self.assertEqual(bar_count.horizon_amount, 5)
        self.assertEqual(duration.horizon_duration_ns, 5 * 60_000_000_000)
        self.assertEqual(bar_count.horizon_duration_ns, 5 * 5 * 60_000_000_000)

    def test_outcome_spec_is_frozen(self):
        spec = outcome_spec()
        with self.assertRaises(FrozenInstanceError):
            spec.outcome_key = "mutated"  # type: ignore[misc]

    def test_rejects_ambiguous_bare_digit_string(self):
        with self.assertRaises(OutcomeError):
            outcome_spec(horizon_duration="5")

    def test_rejects_ungoverned_price_reference(self):
        with self.assertRaises(OutcomeError):
            outcome_spec(price_reference="Close Price")

    def test_rejects_bar_count_sampling_period(self):
        with self.assertRaises(OutcomeError):
            outcome_spec(sampling_period=5)

    def test_rejects_horizon_duration_not_a_multiple_of_sampling_period(self):
        with self.assertRaises(OutcomeError):
            outcome_spec(horizon_duration="7m", sampling_period="5m")

    def test_identity_is_stable_across_independent_processes(self):
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            from quant_platform.research import MetricKind, OutcomeSpec

            spec = OutcomeSpec(
                outcome_key="forward_return.fixed_horizon",
                semantic_version="1",
                horizon_duration="5m",
                sampling_period="5m",
                metric_kind=MetricKind.FORWARD_RETURN,
                price_reference="close",
            )
            print(spec.identity)
            """
        )
        import subprocess

        first = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        second = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(first.stdout.strip(), second.stdout.strip())
        self.assertEqual(outcome_spec().identity, first.stdout.strip())


class FullHorizonEvaluationTests(unittest.TestCase):
    def test_forward_return_over_completed_duration_horizon(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [
            bar(0, "100"),
            bar(5, "105"),
            bar(10, "110"),
            bar(15, "108"),
        ]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("1/10", outcome.realized_value)
        self.assertEqual(Instant.parse(EVENT_TIME).epoch_ns + 10 * 60_000_000_000, outcome.horizon_end.epoch_ns)
        self.assertIsNotNone(outcome.path_metrics)

    def test_high_low_excursion_metric(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m", metric_kind=MetricKind.HIGH_LOW_EXCURSION)
        series = [
            bar(0, "100", high="100", low="100"),
            bar(5, "102", high="106", low="99"),
            bar(10, "101", high="103", low="98"),
        ]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual(Fraction(106 - 98, 100), Fraction(outcome.realized_value))

    def test_extrema_metric_picks_largest_magnitude_return(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m", metric_kind=MetricKind.EXTREMA)
        series = [bar(0, "100"), bar(5, "80"), bar(10, "103")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual(Fraction(-20, 100), Fraction(outcome.realized_value))

    def test_bar_count_horizon_completes_after_n_forward_observations(self):
        spec = outcome_spec(horizon_duration=2, sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), bar(10, "103"), bar(15, "90")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual(bar(10, "103").instant.epoch_ns, outcome.horizon_end.epoch_ns)
        self.assertEqual(Fraction(3, 100), Fraction(outcome.realized_value))


class NoOvershootAndGapDetectionTests(unittest.TestCase):
    """P1 fix: a missing exact boundary observation must never be papered
    over by reading a later price (overshoot), and an interior gap must be
    detected for every metric kind, not merely a missing anchor."""

    def test_missing_exact_boundary_is_insufficient_coverage_not_overshoot(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        # bar(5) is missing: the boundary at exactly +10m cannot be trusted,
        # even though a later price is available.
        series = [bar(0, "100"), bar(10, "104")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_interior_gap_before_boundary_is_insufficient_coverage(self):
        spec = outcome_spec(horizon_duration="15m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), bar(15, "108")]  # bar(10) missing
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_high_low_excursion_with_interior_gap_is_insufficient_coverage(self):
        spec = outcome_spec(horizon_duration="15m", sampling_period="5m", metric_kind=MetricKind.HIGH_LOW_EXCURSION)
        series = [
            bar(0, "100", high="100", low="100"),
            bar(5, "101", high="105", low="99"),
            # bar(10) missing -- the true high/low over the full path is unknown.
            bar(15, "104", high="106", low="100"),
        ]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_extrema_with_interior_gap_is_insufficient_coverage(self):
        spec = outcome_spec(horizon_duration="15m", sampling_period="5m", metric_kind=MetricKind.EXTREMA)
        series = [bar(0, "100"), bar(5, "80"), bar(15, "103")]  # bar(10) missing
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)


class CensoringTests(unittest.TestCase):
    def test_duration_horizon_insufficient_when_data_exhausts_without_eod_proof(self):
        # Finding B: a plain exhausted iterable is never, by itself, proof
        # the authoritative source ended -- it proves only that the caller
        # stopped supplying evidence.
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_duration_horizon_censored_with_explicit_end_of_data_evidence(self):
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), EndOfData()]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertIsNone(outcome.realized_value)
        self.assertIsNotNone(outcome.path_metrics)
        expected_horizon_end = Instant.parse(EVENT_TIME).epoch_ns + 20 * 60_000_000_000
        self.assertEqual(expected_horizon_end, outcome.horizon_end.epoch_ns)
        # Data ran out well before the horizon elapsed: causal_available_at
        # cannot be pinned to the last observation's own instant (that would
        # violate the temporal availability invariant), so it falls back to
        # horizon_end itself.
        self.assertEqual(outcome.horizon_end.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_duration_horizon_insufficient_with_no_forward_bars_and_no_eod_proof(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series = [bar(0, "100")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)
        self.assertIsNone(outcome.path_metrics)

    def test_duration_horizon_censored_with_no_forward_bars_and_explicit_eod_proof(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series = [bar(0, "100"), EndOfData()]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertIsNone(outcome.realized_value)
        self.assertIsNone(outcome.path_metrics)

    def test_bar_count_horizon_insufficient_when_forward_bars_exhaust_without_eod_proof(self):
        spec = outcome_spec(horizon_duration=5, sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_bar_count_horizon_censored_when_insufficient_forward_bars_with_eod_proof(self):
        spec = outcome_spec(horizon_duration=5, sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), EndOfData()]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_interior_gap_state_is_not_overridden_by_later_end_of_data_evidence(self):
        # Credited: interior gaps remain INSUFFICIENT_COVERAGE regardless of
        # end-of-data provenance -- only the first unmatched grid instant
        # governs the outcome.
        spec = outcome_spec(horizon_duration="15m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), bar(15, "108"), EndOfData()]  # bar(10) missing
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_missing_anchor_is_insufficient_coverage_not_censored(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series = [bar(5, "101"), bar(10, "103")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)
        self.assertIsNone(outcome.path_metrics)

    def test_empty_series_is_insufficient_coverage(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        outcome = evaluate_outcome(spec, EVENT, [])
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_end_of_data_as_the_only_element_is_insufficient_coverage_not_censored(self):
        # No anchor was ever established, so there is nothing to censor,
        # even though the caller supplied explicit end-of-data evidence.
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        outcome = evaluate_outcome(spec, EVENT, [EndOfData()])
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)
        self.assertIsNone(outcome.path_metrics)

    def test_never_fabricates_a_price_for_a_censored_outcome(self):
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), EndOfData()]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        with self.assertRaises(OutcomeError):
            Outcome(
                outcome_spec_id=spec.identity,
                event_id=EVENT.event_id,
                horizon_start=outcome.horizon_start,
                horizon_end=outcome.horizon_end,
                causal_available_at=outcome.causal_available_at,
                state=OutcomeState.CENSORED_END_OF_DATA,
                realized_value="1/10",
            )


class CausalAvailabilityTests(unittest.TestCase):
    def test_complete_outcome_causal_available_at_never_precedes_horizon_end(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "105"), bar(10, "110")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertGreaterEqual(outcome.causal_available_at.epoch_ns, outcome.horizon_end.epoch_ns)

    def test_censored_outcome_causal_available_at_never_precedes_horizon_end(self):
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "101"), EndOfData()]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertGreaterEqual(outcome.causal_available_at.epoch_ns, outcome.horizon_end.epoch_ns)

    def test_insufficient_coverage_causal_available_at_never_precedes_horizon_end(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        outcome = evaluate_outcome(spec, EVENT, [])
        self.assertGreaterEqual(outcome.causal_available_at.epoch_ns, outcome.horizon_end.epoch_ns)

    def test_end_of_data_causal_available_at_cannot_precede_horizon_end_or_consumed_evidence(self):
        # Acceptance #7: the EndOfData marker's own causal_available_at must
        # never pull Outcome.causal_available_at earlier than horizon_end or
        # any other consumed evidence, even when it is itself very early.
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        early = bar(0, "100").instant
        series = [bar(0, "100"), bar(5, "101"), EndOfData(causal_available_at=early)]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertEqual(outcome.horizon_end.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_end_of_data_causal_available_at_pushes_availability_forward_when_later(self):
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        late = Instant(bar(0, "100").instant.epoch_ns + 3_600_000_000_000)
        series = [bar(0, "100"), bar(5, "101"), EndOfData(causal_available_at=late)]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.CENSORED_END_OF_DATA, outcome.state)
        self.assertEqual(late.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_boundary_finalization_later_than_bucket_time_pushes_causal_available_at_forward(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        late_finalized = MarketObservation(
            instant=bar(10, "110").instant,
            prices={"close": "110"},
            causal_available_at=Instant(bar(10, "110").instant.epoch_ns + 30_000_000_000),
        )
        series = [bar(0, "100"), bar(5, "105"), late_finalized]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual(late_finalized.causal_available_at.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_causal_available_at_reflects_an_intermediate_observation_not_just_the_boundary(self):
        # P1 fix: an intermediate (non-boundary) observation with a very
        # late finalization time must still push availability forward, even
        # though both horizon_end and the boundary's own causal time are
        # much earlier.
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        very_late_mid = MarketObservation(
            instant=bar(5, "105").instant,
            prices={"close": "105"},
            causal_available_at=Instant(bar(5, "105").instant.epoch_ns + 3_600_000_000_000),
        )
        series = [bar(0, "100"), very_late_mid, bar(10, "110")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual(very_late_mid.causal_available_at.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_late_off_grid_gap_observation_pushes_causal_available_at_forward(self):
        # P1 regression: the off-grid candidate that establishes the gap is
        # itself genuinely consumed evidence and must participate in
        # Outcome.causal_available_at (ADR-0038 §7), not merely horizon_end.
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        off_grid_instant = bar(0, "100").instant.epoch_ns + 6 * 60_000_000_000
        late_causal = Instant(off_grid_instant + 24 * 3_600_000_000_000)
        off_grid = MarketObservation(
            instant=Instant(off_grid_instant),
            prices={"close": "101"},
            causal_available_at=late_causal,
        )
        series = [bar(0, "100"), off_grid]  # bar(5) missing -- off_grid lands at +6m
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertEqual(late_causal.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_late_finalizing_mismatched_anchor_observation_pushes_causal_available_at_forward(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        late_causal = Instant(bar(5, "101").instant.epoch_ns + 24 * 3_600_000_000_000)
        mismatched_first = MarketObservation(
            instant=bar(5, "101").instant,
            prices={"close": "101"},
            causal_available_at=late_causal,
        )
        outcome = evaluate_outcome(spec, EVENT, [mismatched_first])
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertEqual(late_causal.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_late_end_of_data_encountered_during_remaining_validation_pushes_causal_available_at_forward(self):
        # P1 regression: an EndOfData marker consumed by
        # _validate_remaining_within_horizon (after an earlier gap already
        # decided the state) must still push causal_available_at forward.
        spec = outcome_spec(horizon_duration="20m", sampling_period="5m")
        late_causal = Instant(bar(10, "102").instant.epoch_ns + 24 * 3_600_000_000_000)
        series = [bar(0, "100"), bar(10, "102"), EndOfData(causal_available_at=late_causal)]  # bar(5) missing
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertEqual(late_causal.epoch_ns, outcome.causal_available_at.epoch_ns)

    def test_outcome_construction_rejects_causal_available_at_before_horizon_end(self):
        with self.assertRaises(OutcomeError):
            Outcome(
                outcome_spec_id=outcome_spec().identity,
                event_id=EVENT.event_id,
                horizon_start=EVENT_TIME,
                horizon_end="2024-01-01T00:10:00Z",
                causal_available_at="2024-01-01T00:09:00Z",
                state=OutcomeState.COMPLETE,
                realized_value="1/10",
            )


class PathMetricsZeroBaselineTests(unittest.TestCase):
    """P2 fix: the anchor's own zero return must participate in the
    favorable/adverse excursion extremes."""

    def test_all_decline_path_reports_zero_maximum_favorable_excursion(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "95"), bar(10, "90")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("0", outcome.path_metrics.maximum_favorable_excursion)
        self.assertEqual(Fraction(-10, 100), Fraction(outcome.path_metrics.maximum_adverse_excursion))

    def test_all_advance_path_reports_zero_maximum_adverse_excursion(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "110"), bar(10, "120")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("0", outcome.path_metrics.maximum_adverse_excursion)
        self.assertEqual(Fraction(20, 100), Fraction(outcome.path_metrics.maximum_favorable_excursion))


class FractionCanonicalizationTests(unittest.TestCase):
    """P2 fix: realized_value/path_metrics must reject reducible, zero
    denominator-collapse and signed-zero fraction spellings."""

    def _outcome_kwargs(self):
        return dict(
            outcome_spec_id=outcome_spec().identity,
            event_id=EVENT.event_id,
            horizon_start=EVENT_TIME,
            horizon_end="2024-01-01T00:10:00Z",
            causal_available_at="2024-01-01T00:10:00Z",
            state=OutcomeState.COMPLETE,
        )

    def test_rejects_reducible_fraction(self):
        with self.assertRaises(OutcomeError):
            Outcome(**self._outcome_kwargs(), realized_value="2/2")

    def test_rejects_zero_over_nonzero_denominator(self):
        with self.assertRaises(OutcomeError):
            Outcome(**self._outcome_kwargs(), realized_value="0/3")

    def test_rejects_signed_zero(self):
        with self.assertRaises(OutcomeError):
            Outcome(**self._outcome_kwargs(), realized_value="-0")

    def test_accepts_already_canonical_values(self):
        outcome = Outcome(**self._outcome_kwargs(), realized_value="1/25")
        self.assertEqual("1/25", outcome.realized_value)

    def test_path_metrics_rejects_non_canonical_fraction(self):
        with self.assertRaises(OutcomeError):
            PathMetrics(maximum_favorable_excursion="2/2", maximum_adverse_excursion="0")
        with self.assertRaises(OutcomeError):
            PathMetrics(maximum_favorable_excursion="0", maximum_adverse_excursion="-0")


class NumericalExactnessTests(unittest.TestCase):
    def test_forward_return_is_an_exact_fraction_not_a_rounded_float(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series = [bar(0, "3"), bar(5, "1")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("-2/3", outcome.realized_value)
        self.assertEqual(Fraction(-2, 3), Fraction(outcome.realized_value))

    def test_integral_return_canonicalizes_without_a_denominator(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series = [bar(0, "50"), bar(5, "100")]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual("1", outcome.realized_value)

    def test_identical_inputs_yield_byte_identical_outcomes(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series_a = [bar(0, "100"), bar(5, "105"), bar(10, "110")]
        series_b = [bar(0, "100"), bar(5, "105"), bar(10, "110")]
        outcome_a = evaluate_outcome(spec, EVENT, series_a)
        outcome_b = evaluate_outcome(spec, EVENT, series_b)
        self.assertEqual(outcome_a.stable_dict(), outcome_b.stable_dict())
        self.assertEqual(outcome_a.outcome_id, outcome_b.outcome_id)

    def test_outcome_id_ignores_notes_free_realized_value_divergence_source(self):
        # Two evaluations that agree on spec/event/horizon bounds but reach
        # a different realized_value (e.g. a revised market snapshot) still
        # share the same outcome_id: identity binds spec + event + horizon
        # bounds only, never the measured value.
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        series_a = [bar(0, "100"), bar(5, "110")]
        series_b = [bar(0, "100"), bar(5, "90")]
        outcome_a = evaluate_outcome(spec, EVENT, series_a)
        outcome_b = evaluate_outcome(spec, EVENT, series_b)
        self.assertNotEqual(outcome_a.realized_value, outcome_b.realized_value)
        self.assertEqual(outcome_a.outcome_id, outcome_b.outcome_id)


class EvaluationRuntimeGuardTests(unittest.TestCase):
    def test_rejects_out_of_order_market_series(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(10, "110"), bar(5, "105")]
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, series)

    def test_rejects_duplicate_instant_in_market_series(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "105"), bar(5, "106")]
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, series)

    def test_rejects_non_market_observation_elements(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, [{"instant": EVENT_TIME, "close": "100"}])

    def test_market_observation_rejects_missing_required_price_field(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m", price_reference="vwap")
        series = [bar(0, "100"), bar(5, "105"), bar(10, "110")]
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, series)

    def test_market_observation_rejects_empty_prices_mapping(self):
        with self.assertRaises(OutcomeEvaluationError):
            MarketObservation(instant=EVENT_TIME, prices={})


class ExhaustionSentinelTests(unittest.TestCase):
    """Finding A (F03 post-#86 adversarial review): genuine iterator
    exhaustion must never be mistaken for a malformed element, and every
    malformed element -- including a literal ``None`` -- must fail closed
    rather than being silently treated as the stream ending."""

    def test_none_as_first_element_raises(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, [None])

    def test_none_after_valid_anchor_raises(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), None]
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, series)

    def test_none_at_interior_grid_step_raises_not_insufficient_coverage(self):
        # Before the Finding A fix, [valid anchor, None] was silently
        # mistaken for a real forward observation slot exhausting and
        # produced a domain state instead of failing closed.
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")
        series = [bar(0, "100"), bar(5, "105"), None]
        with self.assertRaises(OutcomeEvaluationError):
            evaluate_outcome(spec, EVENT, series)

    def test_genuine_empty_iterator_is_insufficient_coverage_not_an_error(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        outcome = evaluate_outcome(spec, EVENT, iter([]))
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)


class LazyEvaluationStreamTests(unittest.TestCase):
    def test_evaluator_ignores_out_of_order_or_malformed_tail_after_horizon_end(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        # Tail contains out-of-order bars and malformed non-MarketObservation items after 5m horizon.
        series = [
            bar(0, "100"),
            bar(5, "110"),
            bar(2, "95"),
            "not_a_market_observation",
            {"price": "100"},
            bar(10, "120"),
        ]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("1/10", outcome.realized_value)

    def test_evaluator_terminates_on_unbounded_series(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")

        def unbounded_series():
            yield bar(0, "100")
            yield bar(5, "105")
            yield bar(10, "110")
            minute = 11
            while True:
                yield bar(minute, "110")
                minute += 1

        outcome = evaluate_outcome(spec, EVENT, unbounded_series())
        self.assertEqual(OutcomeState.COMPLETE, outcome.state)
        self.assertEqual("1/10", outcome.realized_value)

    def test_evaluator_terminates_on_unbounded_series_with_gap(self):
        spec = outcome_spec(horizon_duration="10m", sampling_period="5m")

        def unbounded_series_with_gap():
            yield bar(0, "100")
            yield bar(6, "106")  # Gap: expected 5m
            minute = 7
            while True:
                yield bar(minute, "110")
                minute += 1

        outcome = evaluate_outcome(spec, EVENT, unbounded_series_with_gap())
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)

    def test_evaluator_ignores_malformed_tail_beyond_horizon_after_gap(self):
        spec = outcome_spec(horizon_duration="5m", sampling_period="5m")
        # Series has gap at 5m (first observation is at 10m, beyond 5m horizon), followed by malformed tail.
        series = [
            bar(0, "100"),
            bar(10, "110"),
            "garbage_after_horizon",
            bar(2, "90"),
        ]
        outcome = evaluate_outcome(spec, EVENT, series)
        self.assertEqual(OutcomeState.INSUFFICIENT_COVERAGE, outcome.state)
        self.assertIsNone(outcome.realized_value)


if __name__ == "__main__":
    unittest.main()
