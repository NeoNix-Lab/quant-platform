#!/usr/bin/env python3
"""G03/G04 risk, sizing, session and cooldown runtime v1 tests."""

from __future__ import annotations

from pathlib import Path
import inspect
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.strategy import (
    CapitalRiskPolicy,
    CooldownDecision,
    CooldownDecisionUnavailable,
    CooldownPolicyDefinition,
    CooldownState,
    CooldownUnavailableReason,
    FixedFractionSizingPolicy,
    RealizedPositionOutcome,
    RiskDecisionState,
    RiskSnapshot,
    SessionPolicyDefinition,
    SessionReferenceMarket,
    SessionState,
    TradeHistoryEvidence,
)
import quant_platform.strategy as strategy_module


AS_OF = Instant.parse("2026-09-26T12:00:00Z")


def risk_policy() -> CapitalRiskPolicy:
    return CapitalRiskPolicy(
        policy_key="btc.risk",
        max_drawdown_fraction="0.2",
        max_position_notional_fraction="0.5",
        max_total_exposure_fraction="0.8",
        risk_per_trade_fraction="0.02",
        max_evidence_age_seconds=60,
    )


def snapshot(**overrides) -> RiskSnapshot:
    values = {
        "observed_at": AS_OF,
        "equity": "10000",
        "peak_equity": "10000",
        "current_exposure_notional": "0",
        "evidence_identity": "risk-snapshot:test",
    }
    values.update(overrides)
    return RiskSnapshot(**values)


def cooldown_policy() -> CooldownPolicyDefinition:
    return CooldownPolicyDefinition(
        policy_key="btc.cooldown",
        post_loss_cooldown_seconds=300,
        consecutive_loss_count=2,
        consecutive_loss_cooldown_seconds=900,
        max_entries_per_utc_day=3,
        max_trade_history_age_seconds=60,
    )


def outcome(opened: str, settled: str, pnl: str, label: str) -> RealizedPositionOutcome:
    return RealizedPositionOutcome(
        opened_at=Instant.parse(opened),
        settled_at=Instant.parse(settled),
        realized_pnl=pnl,
        evidence_identity=f"position-outcome:{label}",
    )


class StrategyRiskSessionV1Tests(unittest.TestCase):
    def test_risk_policy_identity_and_drawdown_refusal_are_deterministic(self):
        policy = risk_policy()
        equivalent = risk_policy()
        breached = snapshot(equity="7900", peak_equity="10000")

        first = policy.evaluate(
            snapshot=breached,
            as_of=AS_OF,
            reference_price="100",
            target_position="1",
        )
        repeat = policy.evaluate(
            snapshot=breached,
            as_of=AS_OF,
            reference_price="100",
            target_position="1",
        )

        self.assertEqual(policy.identity, equivalent.identity)
        self.assertEqual(first.identity, repeat.identity)
        self.assertEqual(RiskDecisionState.REFUSED, first.state)
        self.assertEqual("max_drawdown_breached", first.reason)
        self.assertEqual("0", first.stable_dict()["risk_budget_notional"])

    def test_sizing_policy_caps_by_risk_budget_and_lot_size(self):
        risk = risk_policy().evaluate(
            snapshot=snapshot(),
            as_of=AS_OF,
            reference_price="250",
            target_position="10",
        )
        sizing = FixedFractionSizingPolicy(
            policy_key="btc.sizing",
            lot_size="0.1",
            min_size="0.1",
            max_size="5",
        )

        decision = sizing.evaluate(
            risk_decision=risk,
            reference_price="250",
            target_position="10",
        )

        self.assertEqual(RiskDecisionState.ACCEPTED, risk.state)
        self.assertEqual("200", risk.stable_dict()["risk_budget_notional"])
        self.assertEqual("0.8", decision.stable_dict()["size"])
        self.assertEqual(decision.identity, sizing.evaluate(
            risk_decision=risk,
            reference_price="250",
            target_position="10",
        ).identity)

    def test_risk_policy_treats_target_position_as_post_decision_state(self):
        policy = CapitalRiskPolicy(
            policy_key="btc.risk",
            max_drawdown_fraction="0.2",
            max_position_notional_fraction="1",
            max_total_exposure_fraction="1",
            risk_per_trade_fraction="0.1",
            max_evidence_age_seconds=60,
        )

        decision = policy.evaluate(
            snapshot=snapshot(current_exposure_notional="9000"),
            as_of=AS_OF,
            reference_price="10000",
            target_position="0.9",
        )

        self.assertEqual(RiskDecisionState.ACCEPTED, decision.state)
        self.assertEqual("9000", decision.stable_dict()["requested_notional"])

    def test_risk_and_sizing_validate_evidence_types(self):
        policy = risk_policy()
        sizing = FixedFractionSizingPolicy(policy_key="btc.sizing", lot_size="0.1")

        with self.assertRaisesRegex(ValueError, "snapshot must be RiskSnapshot"):
            policy.evaluate(  # type: ignore[arg-type]
                snapshot=object(),
                as_of=AS_OF,
                reference_price="100",
                target_position="1",
            )
        with self.assertRaisesRegex(ValueError, "risk_decision must be RiskDecision"):
            sizing.evaluate(  # type: ignore[arg-type]
                risk_decision=object(),
                reference_price="100",
                target_position="1",
            )

    def test_session_policy_handles_dst_holiday_and_tokyo_lunch_break(self):
        policy = SessionPolicyDefinition(
            policy_key="reference.sessions",
            reference_markets=(
                SessionReferenceMarket.NEW_YORK,
                SessionReferenceMarket.LONDON,
                SessionReferenceMarket.TOKYO,
            ),
        )

        ny_summer = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-07-01T13:45:00Z"))
        }[SessionReferenceMarket.NEW_YORK]
        london_christmas = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-12-25T10:00:00Z"))
        }[SessionReferenceMarket.LONDON]
        tokyo_lunch = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-09-24T03:00:00Z"))
        }[SessionReferenceMarket.TOKYO]
        tokyo_afternoon = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-09-24T04:00:00Z"))
        }[SessionReferenceMarket.TOKYO]

        self.assertEqual(SessionState.OPEN, ny_summer.state)
        self.assertEqual("regular", ny_summer.phase)
        self.assertEqual(SessionState.CLOSED, london_christmas.state)
        self.assertEqual("market_closed_date", london_christmas.reason)
        self.assertEqual(SessionState.CLOSED, tokyo_lunch.state)
        self.assertEqual("outside_session_phase", tokyo_lunch.reason)
        self.assertEqual(SessionState.OPEN, tokyo_afternoon.state)
        self.assertEqual("afternoon", tokyo_afternoon.phase)

    def test_session_policy_uses_static_tokyo_holidays_and_suppresses_early_close_phases(self):
        policy = SessionPolicyDefinition(
            policy_key="reference.sessions",
            reference_markets=(SessionReferenceMarket.TOKYO, SessionReferenceMarket.LONDON),
        )

        tokyo_golden_week = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-05-06T04:00:00Z"))
        }[SessionReferenceMarket.TOKYO]
        tokyo_silver_week = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2026-09-22T04:00:00Z"))
        }[SessionReferenceMarket.TOKYO]
        london_auction_leak = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2024-12-24T16:32:00Z"))
        }[SessionReferenceMarket.LONDON]
        london_post_leak = {
            decision.reference_market: decision
            for decision in policy.evaluate(Instant.parse("2024-12-24T16:50:00Z"))
        }[SessionReferenceMarket.LONDON]

        self.assertEqual(SessionState.CLOSED, tokyo_golden_week.state)
        self.assertEqual("market_closed_date", tokyo_golden_week.reason)
        self.assertEqual(SessionState.CLOSED, tokyo_silver_week.state)
        self.assertEqual("market_closed_date", tokyo_silver_week.reason)
        self.assertEqual(SessionState.CLOSED, london_auction_leak.state)
        self.assertEqual("outside_session_phase", london_auction_leak.reason)
        self.assertEqual(SessionState.CLOSED, london_post_leak.state)
        self.assertEqual("outside_session_phase", london_post_leak.reason)

    def test_cooldown_combines_loss_triggers_and_never_blocks_exits(self):
        policy = cooldown_policy()
        history = TradeHistoryEvidence(
            observed_at=AS_OF,
            evidence_identity="trade-history:test",
            outcomes=(
                outcome("2026-09-26T09:00:00Z", "2026-09-26T11:56:00Z", "-10", "loss-1"),
                outcome("2026-09-26T10:00:00Z", "2026-09-26T11:58:00Z", "-1", "loss-2"),
            ),
        )

        entry_decision = policy.evaluate(
            trade_history=history,
            as_of=AS_OF,
            requires_new_entry=True,
        )
        exit_decision = policy.evaluate(
            trade_history=None,
            as_of=AS_OF,
            requires_new_entry=False,
        )

        self.assertIsInstance(entry_decision, CooldownDecision)
        self.assertEqual(CooldownState.COOLING_DOWN, entry_decision.state)
        self.assertEqual("2026-09-26T12:13:00Z", entry_decision.cooldown_until.isoformat())
        self.assertFalse(entry_decision.new_entries_allowed)
        self.assertTrue(entry_decision.exits_allowed)
        self.assertIsInstance(exit_decision, CooldownDecision)
        self.assertEqual(CooldownState.ELIGIBLE, exit_decision.state)
        self.assertTrue(exit_decision.new_entries_allowed)
        self.assertTrue(exit_decision.exits_allowed)
        self.assertEqual("trade-history-not-required-for-exit", exit_decision.trade_history_identity)

    def test_cooldown_frequency_cap_and_unavailable_evidence_are_explicit(self):
        policy = cooldown_policy()
        history = TradeHistoryEvidence(
            observed_at=AS_OF,
            evidence_identity="trade-history:test",
            outcomes=(
                outcome("2026-09-26T01:00:00Z", "2026-09-26T01:05:00Z", "1", "win-1"),
                outcome("2026-09-26T02:00:00Z", "2026-09-26T02:05:00Z", "1", "win-2"),
                outcome("2026-09-26T03:00:00Z", "2026-09-26T03:05:00Z", "1", "win-3"),
            ),
        )

        frequency = policy.evaluate(
            trade_history=history,
            as_of=AS_OF,
            requires_new_entry=True,
        )
        missing = policy.evaluate(
            trade_history=None,
            as_of=AS_OF,
            requires_new_entry=True,
        )

        self.assertIsInstance(frequency, CooldownDecision)
        self.assertEqual(CooldownState.FREQUENCY_LIMITED, frequency.state)
        self.assertFalse(frequency.new_entries_allowed)
        self.assertIsInstance(missing, CooldownDecisionUnavailable)
        self.assertEqual(CooldownUnavailableReason.MISSING_TRADE_HISTORY, missing.reason)

    def test_cooldown_future_dated_outcomes_are_not_malformed(self):
        policy = cooldown_policy()
        history = TradeHistoryEvidence(
            observed_at=AS_OF,
            evidence_identity="trade-history:future",
            outcomes=(
                outcome("2026-09-26T12:00:00Z", "2026-09-26T12:00:00.000000001Z", "-1", "future"),
            ),
        )

        decision = policy.evaluate(
            trade_history=history,
            as_of=AS_OF,
            requires_new_entry=True,
        )

        self.assertIsInstance(decision, CooldownDecisionUnavailable)
        self.assertEqual(CooldownUnavailableReason.TRADE_HISTORY_FUTURE_DATED, decision.reason)

    def test_cooldown_consecutive_loss_anchor_ignores_later_breakeven_timestamp(self):
        policy = CooldownPolicyDefinition(
            policy_key="btc.cooldown",
            post_loss_cooldown_seconds=300,
            consecutive_loss_count=2,
            consecutive_loss_cooldown_seconds=900,
            max_entries_per_utc_day=10,
            max_trade_history_age_seconds=60,
        )
        as_of = Instant.parse("2026-09-26T11:59:30Z")
        history = TradeHistoryEvidence(
            observed_at=as_of,
            evidence_identity="trade-history:breakeven",
            outcomes=(
                outcome("2026-09-26T09:00:00Z", "2026-09-26T11:40:00Z", "-10", "loss-1"),
                outcome("2026-09-26T09:30:00Z", "2026-09-26T11:45:00Z", "-1", "loss-2"),
                outcome("2026-09-26T10:00:00Z", "2026-09-26T11:59:00Z", "0", "flat"),
            ),
        )

        decision = policy.evaluate(
            trade_history=history,
            as_of=as_of,
            requires_new_entry=True,
        )

        self.assertIsInstance(decision, CooldownDecision)
        self.assertEqual(CooldownState.COOLING_DOWN, decision.state)
        self.assertEqual("2026-09-26T12:00:00Z", decision.cooldown_until.isoformat())

    def test_runtime_uses_explicit_instants_not_host_clock_or_timezone_database(self):
        source = inspect.getsource(strategy_module)

        self.assertNotIn("datetime.now", source)
        self.assertNotIn(".now(", source)
        self.assertNotIn("zoneinfo", source)
        self.assertNotIn("def _nth_weekday", source)
        self.assertNotIn("def _japan_autumn_equinox", source)


if __name__ == "__main__":
    unittest.main()
