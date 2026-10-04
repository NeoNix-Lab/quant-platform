#!/usr/bin/env python3
"""G01/G02 StrategySpec and DecisionIntent v1 tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.strategy import (
    CapitalRiskPolicy,
    CooldownPolicyDefinition,
    DecisionIntent,
    Direction,
    EntryPolicy,
    ExitPolicy,
    FixedFractionSizingPolicy,
    NoDecision,
    PositionPolicy,
    SessionPolicyDefinition,
    SessionReferenceMarket,
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
        sizing_policy=FixedFractionSizingPolicy(
            policy_key="breakout.sizing",
            lot_size="0.1",
            min_size="0.1",
        ),
        risk_policy=CapitalRiskPolicy(
            policy_key="breakout.risk",
            max_drawdown_fraction="0.2",
            max_position_notional_fraction="0.5",
            max_total_exposure_fraction="1",
            risk_per_trade_fraction="0.02",
            max_evidence_age_seconds=60,
        ),
        session_policy=SessionPolicyDefinition(
            policy_key="breakout.sessions",
            reference_markets=(SessionReferenceMarket.NEW_YORK,),
        ),
        cooldown_policy=CooldownPolicyDefinition(
            policy_key="breakout.cooldown",
            post_loss_cooldown_seconds=300,
            consecutive_loss_count=2,
            consecutive_loss_cooldown_seconds=900,
            max_entries_per_utc_day=3,
            max_trade_history_age_seconds=60,
        ),
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

    def test_execution_policy_content_change_changes_strategy_identity(self):
        """ADR-0051 (#249): execution_policy is the intended extension point
        for consumer-owned signal rule/threshold content specifically because
        it participates in strategy_identity -- two strategies differing only
        in that content must never collide on one identity."""
        first = _spec()
        second = StrategySpec(
            strategy_key=first.strategy_key,
            semantic_version=first.semantic_version,
            entry_policy=first.entry_policy,
            exit_policy=first.exit_policy,
            position_policy=first.position_policy,
            sizing_policy=first.sizing_policy,
            risk_policy=first.risk_policy,
            session_policy=first.session_policy,
            cooldown_policy=first.cooldown_policy,
            signal_combination_policy=first.signal_combination_policy,
            execution_policy=_PolicyStub("execution-policy-different-threshold"),
        )

        self.assertNotEqual(first.strategy_identity, second.strategy_identity)

    def test_entry_policy_rejects_flat_direction(self):
        """ADR-0052 (#250): one EntryPolicy is always exactly one directional
        side; FLAT is not a valid entry direction (only LONG/SHORT)."""
        with self.assertRaises(StrategyError):
            EntryPolicy(
                policy_key="breakout.entry",
                direction=Direction.FLAT,
                signal_key="signal.breakout",
            )

    def test_direction_has_no_both_value(self):
        """ADR-0052 (#250): a single EntryPolicy cannot express 'long or short
        depending on signal' -- Direction has exactly LONG/SHORT/FLAT."""
        self.assertEqual({"LONG", "SHORT", "FLAT"}, {member.value for member in Direction})

    def test_long_and_short_spec_pair_get_independent_strategy_identities(self):
        """ADR-0052 (#250): a two-sided strategy is two single-direction
        StrategySpecs, not one two-sided spec. Proves the pair's own
        guarantee: specs differing only in entry_policy.direction get
        different strategy_identity values, hence independent G04
        session/cooldown state (keyed by strategy_identity)."""
        long_spec = _spec()
        short_spec = StrategySpec(
            strategy_key=long_spec.strategy_key,
            semantic_version=long_spec.semantic_version,
            entry_policy=EntryPolicy(
                policy_key=long_spec.entry_policy.policy_key,
                direction=Direction.SHORT,
                signal_key=long_spec.entry_policy.signal_key,
                confidence=long_spec.entry_policy.confidence,
            ),
            exit_policy=long_spec.exit_policy,
            position_policy=long_spec.position_policy,
            sizing_policy=long_spec.sizing_policy,
            risk_policy=long_spec.risk_policy,
            session_policy=long_spec.session_policy,
            cooldown_policy=long_spec.cooldown_policy,
            signal_combination_policy=long_spec.signal_combination_policy,
            execution_policy=long_spec.execution_policy,
        )

        self.assertEqual(Direction.LONG, long_spec.entry_policy.direction)
        self.assertEqual(Direction.SHORT, short_spec.entry_policy.direction)
        self.assertNotEqual(long_spec.strategy_identity, short_spec.strategy_identity)

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

        self.assertIsInstance(spec.sizing_policy, FixedFractionSizingPolicy)
        self.assertIsInstance(spec.risk_policy, CapitalRiskPolicy)
        self.assertIsInstance(spec.session_policy, SessionPolicyDefinition)
        self.assertIsInstance(spec.cooldown_policy, CooldownPolicyDefinition)
        self.assertEqual(
            spec.canonical_payload()["session_policy"]["identity"],
            spec.session_policy.identity,
        )
        self.assertEqual(
            spec.canonical_payload()["cooldown_policy"]["identity"],
            spec.cooldown_policy.identity,
        )

    def test_strategy_spec_refuses_unrelated_policy_objects_for_g03_g04_slots(self):
        spec = _spec()

        with self.assertRaisesRegex(StrategyError, "risk_policy must be CapitalRiskPolicy"):
            StrategySpec(
                strategy_key="btc.breakout",
                semantic_version="1",
                entry_policy=spec.entry_policy,
                exit_policy=spec.exit_policy,
                position_policy=spec.position_policy,
                sizing_policy=spec.sizing_policy,
                risk_policy=_PolicyStub("risk-policy-test"),  # type: ignore[arg-type]
                session_policy=spec.session_policy,
                cooldown_policy=spec.cooldown_policy,
                signal_combination_policy=spec.signal_combination_policy,
                execution_policy=spec.execution_policy,
            )

        with self.assertRaisesRegex(StrategyError, "session_policy must be SessionPolicyDefinition"):
            StrategySpec(
                strategy_key="btc.breakout",
                semantic_version="1",
                entry_policy=spec.entry_policy,
                exit_policy=spec.exit_policy,
                position_policy=spec.position_policy,
                sizing_policy=spec.sizing_policy,
                risk_policy=spec.risk_policy,
                session_policy=_PolicyStub("session-policy-test"),  # type: ignore[arg-type]
                cooldown_policy=spec.cooldown_policy,
                signal_combination_policy=spec.signal_combination_policy,
                execution_policy=spec.execution_policy,
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
