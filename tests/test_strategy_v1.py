#!/usr/bin/env python3
"""G01/G02 StrategySpec and DecisionIntent v1 tests."""

from __future__ import annotations

from dataclasses import dataclass
import unittest

from quant_platform.data.models import Instant
from quant_platform.strategy import (
    DecisionIntent,
    Direction,
    EntryPolicy,
    ExitPolicy,
    NoDecision,
    PositionPolicy,
    SignalCombinationMode,
    SignalCombinationPolicy,
    StrategyError,
    StrategyInput,
    StrategySpec,
    compose_decision,
)


@dataclass(frozen=True)
class _PolicyStub:
    label: str

    @property
    def identity(self) -> str:
        return f"{self.label}:sha256:" + ("a" * 64)

    def stable_dict(self):
        return {"policy_type": self.label, "identity": self.identity}


def _spec() -> StrategySpec:
    return StrategySpec(
        strategy_key="btc.breakout",
        semantic_version="1",
        entry_policy=EntryPolicy(
            policy_key="breakout.entry",
            direction=Direction.LONG,
            signal_key="signal.breakout",
            confidence="0.75",
        ),
        exit_policy=ExitPolicy(
            policy_key="breakout.exit",
            signal_key="signal.exit",
        ),
        position_policy=PositionPolicy(
            policy_key="breakout.position",
            long_target_position="1.5",
            short_target_position="1",
        ),
        sizing_policy=_PolicyStub("sizing-policy-test"),
        risk_policy=_PolicyStub("risk-policy-test"),
        session_policy=_PolicyStub("session-policy-test"),
        cooldown_policy=_PolicyStub("cooldown-policy-test"),
        signal_combination_policy=SignalCombinationPolicy(
            policy_key="breakout.signals",
            mode=SignalCombinationMode.ALL,
            signal_keys=("signal.breakout",),
        ),
        execution_policy=_PolicyStub("execution-policy-test"),
        notes="administrative note excluded from identity",
    )


class StrategyV1Tests(unittest.TestCase):
    def test_strategy_spec_identity_is_content_derived_and_excludes_notes(self):
        first = _spec()
        second = StrategySpec(
            strategy_key="btc.breakout",
            semantic_version=1,
            entry_policy=first.entry_policy,
            exit_policy=first.exit_policy,
            position_policy=first.position_policy,
            sizing_policy=first.sizing_policy,
            risk_policy=first.risk_policy,
            session_policy=first.session_policy,
            cooldown_policy=first.cooldown_policy,
            signal_combination_policy=first.signal_combination_policy,
            execution_policy=first.execution_policy,
            notes="different admin note",
        )

        self.assertEqual(first.strategy_identity, second.strategy_identity)
        self.assertTrue(first.strategy_identity.startswith("strategy-spec-v1:sha256:"))

    def test_strategy_spec_refuses_missing_risk_or_sizing_policy(self):
        spec = _spec()

        with self.assertRaises(StrategyError):
            StrategySpec(
                strategy_key="btc.breakout",
                semantic_version="1",
                entry_policy=spec.entry_policy,
                exit_policy=spec.exit_policy,
                position_policy=spec.position_policy,
                sizing_policy=None,  # type: ignore[arg-type]
                risk_policy=spec.risk_policy,
                session_policy=spec.session_policy,
                cooldown_policy=spec.cooldown_policy,
                signal_combination_policy=spec.signal_combination_policy,
                execution_policy=spec.execution_policy,
            )

        with self.assertRaises(StrategyError):
            StrategySpec(
                strategy_key="btc.breakout",
                semantic_version="1",
                entry_policy=spec.entry_policy,
                exit_policy=spec.exit_policy,
                position_policy=spec.position_policy,
                sizing_policy=spec.sizing_policy,
                risk_policy=None,  # type: ignore[arg-type]
                session_policy=spec.session_policy,
                cooldown_policy=spec.cooldown_policy,
                signal_combination_policy=spec.signal_combination_policy,
                execution_policy=spec.execution_policy,
            )

    def test_session_and_cooldown_policy_slots_preserve_supplied_objects(self):
        spec = _spec()

        self.assertIsInstance(spec.session_policy, _PolicyStub)
        self.assertIsInstance(spec.cooldown_policy, _PolicyStub)
        self.assertEqual(
            spec.canonical_payload()["session_policy"]["identity"],
            spec.session_policy.identity,
        )
        self.assertEqual(
            spec.canonical_payload()["cooldown_policy"]["identity"],
            spec.cooldown_policy.identity,
        )

    def test_decision_intent_refuses_late_input(self):
        spec = _spec()
        decision_time = Instant.parse("2026-09-26T12:00:00Z")
        late_input = StrategyInput(
            key="signal.breakout",
            value=True,
            available_at=Instant.parse("2026-09-26T12:00:00.000000001Z"),
            provenance="feature-artifact:late",
        )

        with self.assertRaises(StrategyError):
            compose_decision(
                spec,
                [late_input],
                decision_time=decision_time,
                instrument="BTCUSDT",
            )

    def test_identical_composition_is_bit_identical(self):
        spec = _spec()
        decision_time = Instant.parse("2026-09-26T12:00:00Z")
        inputs = [
            StrategyInput(
                key="signal.breakout",
                value=True,
                available_at=decision_time,
                provenance="feature-artifact:breakout",
            )
        ]

        first = compose_decision(
            spec,
            inputs,
            decision_time=decision_time,
            instrument="BTCUSDT",
        )
        second = compose_decision(
            spec,
            list(reversed(inputs)),
            decision_time=decision_time,
            instrument="BTCUSDT",
        )

        self.assertIsInstance(first.outcome, DecisionIntent)
        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertEqual(first.outcome.identity, second.outcome.identity)
        self.assertEqual(first.outcome.canonical_payload(), second.outcome.canonical_payload())
        self.assertEqual(first.outcome.canonical_payload()["target_position"], "1.5")
        self.assertEqual(first.outcome.canonical_payload()["direction"], "LONG")

    def test_composition_returns_explicit_no_decision_when_signals_do_not_pass(self):
        spec = _spec()
        decision_time = Instant.parse("2026-09-26T12:00:00Z")
        result = compose_decision(
            spec,
            [
                StrategyInput(
                    key="signal.breakout",
                    value=False,
                    available_at=decision_time,
                    provenance="feature-artifact:breakout",
                )
            ],
            decision_time=decision_time,
            instrument="BTCUSDT",
        )

        self.assertIsInstance(result.outcome, NoDecision)
        self.assertIsNone(result.decision_intent)
        self.assertEqual(
            result.no_decision.canonical_payload()["reason"],
            "signal_conditions_not_met",
        )

    def test_exit_signal_wins_and_produces_flat_intent(self):
        spec = _spec()
        decision_time = Instant.parse("2026-09-26T12:00:00Z")
        result = compose_decision(
            spec,
            [
                StrategyInput(
                    key="signal.breakout",
                    value=True,
                    available_at=decision_time,
                    provenance="feature-artifact:breakout",
                ),
                StrategyInput(
                    key="signal.exit",
                    value=True,
                    available_at=decision_time,
                    provenance="position-state:exit",
                ),
            ],
            decision_time=decision_time,
            instrument="BTCUSDT",
        )

        self.assertIsInstance(result.outcome, DecisionIntent)
        self.assertEqual(result.outcome.canonical_payload()["direction"], "FLAT")
        self.assertEqual(result.outcome.canonical_payload()["target_position"], "0")


if __name__ == "__main__":
    unittest.main()
