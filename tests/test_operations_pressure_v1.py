#!/usr/bin/env python3
"""K05 PressurePolicyDefinition v1 proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import inspect
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.operations.capacity import CapacityObservation, CapacityUnavailable
from quant_platform.operations.pressure import (
    PressureDecision,
    PressureDecisionUnavailable,
    PressureDecisionUnavailableReason,
    PressurePolicyDefinition,
    PressurePolicyError,
    PressureState,
    TimeToFullKind,
    WriteRateObservation,
    evaluate_pressure,
)
import quant_platform.operations.pressure as pressure_module


AS_OF = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
OBSERVED = AS_OF - timedelta(seconds=5)


def capacity(available_bytes: int, observed_at: datetime = OBSERVED) -> CapacityObservation:
    return CapacityObservation(
        storage_root_id="hot",
        root_path=Path("/srv/marketdata"),
        observed_at=observed_at,
        total_bytes=10_000,
        used_bytes=9_000,
        available_bytes=available_bytes,
    )


def capacity_policy() -> PressurePolicyDefinition:
    return PressurePolicyDefinition(
        max_capacity_observation_age=timedelta(seconds=10),
        pressure_available_bytes=100,
        critical_available_bytes=50,
        exhausted_available_bytes=10,
    )


def rate_policy() -> PressurePolicyDefinition:
    return PressurePolicyDefinition(
        max_capacity_observation_age=timedelta(seconds=10),
        pressure_available_bytes=100,
        critical_available_bytes=50,
        exhausted_available_bytes=0,
        time_to_full_enabled=True,
        max_rate_observation_age=timedelta(seconds=10),
        pressure_time_to_full=timedelta(seconds=100),
        critical_time_to_full=timedelta(seconds=10),
    )


def rate(
    bytes_per_second,
    *,
    observed_at: datetime = OBSERVED,
    window_start: datetime = AS_OF - timedelta(minutes=1),
    window_end: datetime = OBSERVED,
) -> WriteRateObservation:
    return WriteRateObservation(
        storage_root_id="hot",
        bytes_per_second=bytes_per_second,
        window_start=window_start,
        window_end=window_end,
        observed_at=observed_at,
        evidence_identity=f"rate-evidence:{bytes_per_second}:{observed_at.isoformat()}",
    )


class OperationsPressureV1Tests(unittest.TestCase):
    def test_policy_identity_is_canonical_and_semantic_changes_change_it(self):
        first = capacity_policy()
        equivalent = PressurePolicyDefinition(
            max_capacity_observation_age=timedelta(seconds=10),
            pressure_available_bytes=100,
            critical_available_bytes=50,
            exhausted_available_bytes=10,
        )
        changed_threshold = PressurePolicyDefinition(
            max_capacity_observation_age=timedelta(seconds=10),
            pressure_available_bytes=101,
            critical_available_bytes=50,
            exhausted_available_bytes=10,
        )
        changed_freshness = PressurePolicyDefinition(
            max_capacity_observation_age=timedelta(seconds=11),
            pressure_available_bytes=100,
            critical_available_bytes=50,
            exhausted_available_bytes=10,
        )
        changed_rate_participation = rate_policy()

        self.assertEqual(first.definition_identity, equivalent.definition_identity)
        self.assertNotEqual(first.definition_identity, changed_threshold.definition_identity)
        self.assertNotEqual(first.definition_identity, changed_freshness.definition_identity)
        self.assertNotEqual(first.definition_identity, changed_rate_participation.definition_identity)
        with self.assertRaises(FrozenInstanceError):
            first.pressure_available_bytes = 1  # type: ignore[misc]

    def test_malformed_policy_is_refused_explicitly(self):
        with self.assertRaisesRegex(PressurePolicyError, "exhausted <= critical <= pressure"):
            PressurePolicyDefinition(
                max_capacity_observation_age=timedelta(seconds=1),
                pressure_available_bytes=10,
                critical_available_bytes=20,
                exhausted_available_bytes=0,
            )
        with self.assertRaisesRegex(PressurePolicyError, "positive"):
            PressurePolicyDefinition(
                max_capacity_observation_age=timedelta(seconds=1),
                pressure_available_bytes=10,
                critical_available_bytes=5,
                exhausted_available_bytes=0,
                time_to_full_enabled=True,
                max_rate_observation_age=timedelta(seconds=1),
                pressure_time_to_full=timedelta(seconds=0),
                critical_time_to_full=timedelta(seconds=1),
            )
        with self.assertRaisesRegex(PressurePolicyError, "critical <= pressure"):
            PressurePolicyDefinition(
                max_capacity_observation_age=timedelta(seconds=1),
                pressure_available_bytes=10,
                critical_available_bytes=5,
                exhausted_available_bytes=0,
                time_to_full_enabled=True,
                max_rate_observation_age=timedelta(seconds=1),
                pressure_time_to_full=timedelta(seconds=1),
                critical_time_to_full=timedelta(seconds=2),
            )

    def test_capacity_threshold_boundaries_are_exact_and_non_authorizing(self):
        vectors = [
            (101, PressureState.NORMAL),
            (100, PressureState.PRESSURE),
            (99, PressureState.PRESSURE),
            (51, PressureState.PRESSURE),
            (50, PressureState.CRITICAL),
            (49, PressureState.CRITICAL),
            (11, PressureState.CRITICAL),
            (10, PressureState.EXHAUSTED),
            (0, PressureState.EXHAUSTED),
        ]

        for available_bytes, expected in vectors:
            with self.subTest(available_bytes=available_bytes):
                decision = evaluate_pressure(
                    policy=capacity_policy(),
                    capacity=capacity(available_bytes),
                    as_of=AS_OF,
                )

                self.assertIsInstance(decision, PressureDecision)
                self.assertEqual(expected, decision.state)
                self.assertFalse(decision.delete_authorized)
                self.assertFalse(decision.restrictions.delete_authorized)

    def test_freshness_uses_explicit_as_of_and_repeated_inputs_are_identical(self):
        policy = capacity_policy()
        boundary_observation = capacity(101, AS_OF - timedelta(seconds=10))
        just_stale = capacity(101, AS_OF - timedelta(seconds=10, microseconds=1))
        future = capacity(101, AS_OF + timedelta(microseconds=1))

        first = evaluate_pressure(policy=policy, capacity=boundary_observation, as_of=AS_OF)
        repeat = evaluate_pressure(policy=policy, capacity=boundary_observation, as_of=AS_OF)
        stale = evaluate_pressure(policy=policy, capacity=just_stale, as_of=AS_OF)
        future_result = evaluate_pressure(policy=policy, capacity=future, as_of=AS_OF)
        later = evaluate_pressure(
            policy=policy,
            capacity=boundary_observation,
            as_of=AS_OF + timedelta(microseconds=1),
        )

        self.assertIsInstance(first, PressureDecision)
        self.assertEqual(first.decision_identity, repeat.decision_identity)
        self.assertIsInstance(stale, PressureDecisionUnavailable)
        self.assertEqual(PressureDecisionUnavailableReason.CAPACITY_STALE, stale.reason)
        self.assertIsInstance(future_result, PressureDecisionUnavailable)
        self.assertEqual(PressureDecisionUnavailableReason.CAPACITY_FUTURE_DATED, future_result.reason)
        self.assertIsInstance(later, PressureDecisionUnavailable)
        self.assertEqual(PressureDecisionUnavailableReason.CAPACITY_STALE, later.reason)

    def test_evaluator_requires_explicit_aware_as_of_and_uses_no_host_clock(self):
        result = evaluate_pressure(
            policy=capacity_policy(),
            capacity=capacity(101),
            as_of=datetime(2026, 9, 15, 12, 0),
        )

        source = inspect.getsource(pressure_module.evaluate_pressure)
        self.assertIsInstance(result, PressureDecisionUnavailable)
        self.assertEqual(
            PressureDecisionUnavailableReason.MALFORMED_EVALUATION_INSTANT,
            result.reason,
        )
        self.assertNotIn("datetime.now", source)
        self.assertNotIn(".now(", source)

    def test_capacity_unavailable_and_malformed_capacity_fail_explicitly(self):
        unavailable = evaluate_pressure(
            policy=capacity_policy(),
            capacity=CapacityUnavailable("hot", Path("/srv/marketdata"), "root inaccessible"),
            as_of=AS_OF,
        )
        malformed = evaluate_pressure(
            policy=capacity_policy(),
            capacity=CapacityObservation("hot", Path("/srv/marketdata"), OBSERVED, 1, 1, -1),
            as_of=AS_OF,
        )

        self.assertIsInstance(unavailable, PressureDecisionUnavailable)
        self.assertEqual(PressureDecisionUnavailableReason.CAPACITY_UNAVAILABLE, unavailable.reason)
        self.assertIsInstance(malformed, PressureDecisionUnavailable)
        self.assertEqual(PressureDecisionUnavailableReason.CAPACITY_MALFORMED, malformed.reason)

    def test_time_to_full_threshold_boundaries_are_exact(self):
        vectors = [
            (Decimal("9"), PressureState.NORMAL, TimeToFullKind.FINITE),
            (Decimal("10"), PressureState.PRESSURE, TimeToFullKind.FINITE),
            (Decimal("11"), PressureState.PRESSURE, TimeToFullKind.FINITE),
            (Decimal("99"), PressureState.PRESSURE, TimeToFullKind.FINITE),
            (Decimal("100"), PressureState.CRITICAL, TimeToFullKind.FINITE),
            (Decimal("101"), PressureState.CRITICAL, TimeToFullKind.FINITE),
        ]

        for bytes_per_second, expected_state, expected_ttf in vectors:
            with self.subTest(bytes_per_second=str(bytes_per_second)):
                decision = evaluate_pressure(
                    policy=rate_policy(),
                    capacity=capacity(1000),
                    as_of=AS_OF,
                    rate=rate(bytes_per_second),
                )

                self.assertIsInstance(decision, PressureDecision)
                self.assertEqual(expected_state, decision.state)
                self.assertEqual(expected_ttf, decision.time_to_full.kind)

    def test_zero_rate_is_unbounded_and_byte_thresholds_still_govern(self):
        normal = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=rate(0),
        )
        byte_pressure = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(100),
            as_of=AS_OF,
            rate=rate(0),
        )

        self.assertIsInstance(normal, PressureDecision)
        self.assertEqual(PressureState.NORMAL, normal.state)
        self.assertEqual(TimeToFullKind.UNBOUNDED, normal.time_to_full.kind)
        self.assertIsNone(normal.time_to_full.finite_seconds)
        self.assertIsInstance(byte_pressure, PressureDecision)
        self.assertEqual(PressureState.PRESSURE, byte_pressure.state)

    def test_required_rate_evidence_failures_are_unavailable_when_non_exhausted(self):
        vectors = [
            (None, PressureDecisionUnavailableReason.MISSING_RATE),
            (rate(-1), PressureDecisionUnavailableReason.RATE_MALFORMED),
            (rate(float("inf")), PressureDecisionUnavailableReason.RATE_MALFORMED),
            (rate("not-a-number"), PressureDecisionUnavailableReason.RATE_MALFORMED),
            (
                rate(1, observed_at=AS_OF - timedelta(seconds=10, microseconds=1)),
                PressureDecisionUnavailableReason.RATE_STALE,
            ),
            (
                rate(1, observed_at=AS_OF + timedelta(microseconds=1)),
                PressureDecisionUnavailableReason.RATE_FUTURE_DATED,
            ),
            (
                rate(1, window_start=AS_OF, window_end=AS_OF - timedelta(seconds=1)),
                PressureDecisionUnavailableReason.RATE_MALFORMED,
            ),
        ]

        for rate_evidence, expected_reason in vectors:
            with self.subTest(expected_reason=expected_reason):
                result = evaluate_pressure(
                    policy=rate_policy(),
                    capacity=capacity(1000),
                    as_of=AS_OF,
                    rate=rate_evidence,
                )

                self.assertIsInstance(result, PressureDecisionUnavailable)
                self.assertEqual(expected_reason, result.reason)

    def test_byte_time_disagreement_chooses_more_severe_state(self):
        time_critical = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=rate(100),
        )
        byte_critical = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(50),
            as_of=AS_OF,
            rate=rate(1),
        )

        self.assertIsInstance(time_critical, PressureDecision)
        self.assertEqual(PressureState.CRITICAL, time_critical.state)
        self.assertIsInstance(byte_critical, PressureDecision)
        self.assertEqual(PressureState.CRITICAL, byte_critical.state)

    def test_exhausted_bytes_win_without_rate_forecast(self):
        decision = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(0),
            as_of=AS_OF,
            rate=None,
        )

        self.assertIsInstance(decision, PressureDecision)
        self.assertEqual(PressureState.EXHAUSTED, decision.state)
        self.assertEqual(TimeToFullKind.NOT_EVALUATED_EXHAUSTED, decision.time_to_full.kind)
        self.assertIsNone(decision.rate_evidence)

    def test_successful_and_unavailable_decisions_have_deterministic_identities(self):
        successful = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=rate(0),
        )
        successful_repeat = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=rate(0),
        )
        unavailable = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=None,
        )
        unavailable_repeat = evaluate_pressure(
            policy=rate_policy(),
            capacity=capacity(1000),
            as_of=AS_OF,
            rate=None,
        )

        self.assertIsInstance(successful, PressureDecision)
        self.assertIsInstance(successful_repeat, PressureDecision)
        self.assertEqual(successful.decision_identity, successful_repeat.decision_identity)
        self.assertIn("evidence_identity", successful.capacity_evidence)
        self.assertIsInstance(unavailable, PressureDecisionUnavailable)
        self.assertIsInstance(unavailable_repeat, PressureDecisionUnavailable)
        self.assertEqual(unavailable.decision_identity, unavailable_repeat.decision_identity)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
