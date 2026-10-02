#!/usr/bin/env python3
"""H01/H02 Order/Fill lifecycle, fee/slippage models and G03/G04 admission seam v1 tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.execution import (
    AdmissionOutcome,
    AdmissionRefusalReason,
    ExecutionError,
    FeeSchedule,
    Fill,
    LiquidityRole,
    Order,
    OrderAdmission,
    OrderSide,
    OrderStatus,
    OrderType,
    SyntheticSlippageModel,
    TimeInForce,
    admit_composition_result,
    build_fill,
    translate_intent,
    validate_leg_quantity_conservation,
)
from quant_platform.strategy import (
    CapitalRiskPolicy,
    CooldownPolicyDefinition,
    Direction,
    EntryPolicy,
    ExitPolicy,
    FixedFractionSizingPolicy,
    PositionPolicy,
    RealizedPositionOutcome,
    RiskSnapshot,
    SessionPolicyDefinition,
    SessionReferenceMarket,
    SignalCombinationMode,
    SignalCombinationPolicy,
    StrategyInput,
    StrategySpec,
    TradeHistoryEvidence,
    compose_decision,
)


class _PolicyStub:
    def __init__(self, label: str) -> None:
        self.label = label

    @property
    def identity(self) -> str:
        return f"{self.label}:sha256:" + ("a" * 64)

    def stable_dict(self):
        return {"policy_type": self.label, "identity": self.identity}


CLOSED_WEEKEND_AS_OF = Instant.parse("2026-09-26T12:00:00Z")  # Saturday
OPEN_WEEKDAY_AS_OF = Instant.parse("2026-09-28T15:00:00Z")  # Monday, NY regular session


def risk_policy(**overrides) -> CapitalRiskPolicy:
    values = {
        "policy_key": "btc.risk",
        "max_drawdown_fraction": "0.2",
        "max_position_notional_fraction": "0.5",
        "max_total_exposure_fraction": "0.8",
        "risk_per_trade_fraction": "0.02",
        "max_evidence_age_seconds": 3600,
    }
    values.update(overrides)
    return CapitalRiskPolicy(**values)


def sizing_policy(**overrides) -> FixedFractionSizingPolicy:
    values = {"policy_key": "btc.sizing", "lot_size": "0.001", "min_size": "0.001"}
    values.update(overrides)
    return FixedFractionSizingPolicy(**values)


def session_policy(*markets: SessionReferenceMarket) -> SessionPolicyDefinition:
    return SessionPolicyDefinition(
        policy_key="btc.session",
        reference_markets=markets or (SessionReferenceMarket.NEW_YORK,),
    )


def cooldown_policy(**overrides) -> CooldownPolicyDefinition:
    values = {
        "policy_key": "btc.cooldown",
        "post_loss_cooldown_seconds": 300,
        "consecutive_loss_count": 2,
        "consecutive_loss_cooldown_seconds": 900,
        "max_entries_per_utc_day": 10,
        "max_trade_history_age_seconds": 3600,
    }
    values.update(overrides)
    return CooldownPolicyDefinition(**values)


def snapshot(**overrides) -> RiskSnapshot:
    values = {
        "observed_at": OPEN_WEEKDAY_AS_OF,
        "equity": "10000",
        "peak_equity": "10000",
        "current_exposure_notional": "0",
        "evidence_identity": "risk-snapshot:test",
    }
    values.update(overrides)
    return RiskSnapshot(**values)


def outcome(opened: str, settled: str, pnl: str, label: str) -> RealizedPositionOutcome:
    return RealizedPositionOutcome(
        opened_at=Instant.parse(opened),
        settled_at=Instant.parse(settled),
        realized_pnl=pnl,
        evidence_identity=f"position-outcome:{label}",
    )


def eligible_trade_history(as_of: Instant) -> TradeHistoryEvidence:
    return TradeHistoryEvidence(observed_at=as_of, evidence_identity="trade-history:empty", outcomes=())


def spec(**policy_overrides) -> StrategySpec:
    values = {
        "risk_policy": risk_policy(),
        "sizing_policy": sizing_policy(),
        "session_policy": session_policy(),
        "cooldown_policy": cooldown_policy(),
    }
    values.update(policy_overrides)
    return StrategySpec(
        strategy_key="btc.breakout",
        semantic_version="1",
        entry_policy=EntryPolicy(
            policy_key="breakout.entry",
            direction=Direction.LONG,
            signal_key="signal.breakout",
        ),
        exit_policy=ExitPolicy(policy_key="breakout.exit", signal_key="signal.exit"),
        position_policy=PositionPolicy(
            policy_key="breakout.position",
            long_target_position="0.05",
            short_target_position="0.05",
        ),
        signal_combination_policy=SignalCombinationPolicy(
            policy_key="breakout.signals",
            mode=SignalCombinationMode.ALL,
            signal_keys=("signal.breakout",),
        ),
        execution_policy=_PolicyStub("execution-policy-test"),
        **values,
    )


def entry_intent(strategy_spec: StrategySpec, *, decision_time: Instant):
    result = compose_decision(
        strategy_spec,
        [
            StrategyInput(
                key="signal.breakout",
                value=True,
                available_at=decision_time,
                provenance="feature-artifact:breakout",
            )
        ],
        decision_time=decision_time,
        instrument="BTCUSDT",
    )
    assert result.decision_intent is not None
    return result.decision_intent


def exit_intent(strategy_spec: StrategySpec, *, decision_time: Instant):
    result = compose_decision(
        strategy_spec,
        [
            StrategyInput(
                key="signal.exit",
                value=True,
                available_at=decision_time,
                provenance="position-state:exit",
            )
        ],
        decision_time=decision_time,
        instrument="BTCUSDT",
    )
    assert result.decision_intent is not None
    return result.decision_intent


class OrderStateMachineTests(unittest.TestCase):
    def test_deterministic_identity_same_params_same_id_different_params_differ(self):
        first = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="0.5",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:order-a",
        )
        second = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="0.5",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:order-a",
        )
        third = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="0.5",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:order-b",
        )

        self.assertEqual(first.order_id, second.order_id)
        self.assertNotEqual(first.order_id, third.order_id)
        self.assertTrue(first.order_id.startswith("order-v1:sha256:"))

    def test_valid_transitions_succeed_through_full_fill(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:lifecycle",
        )
        acknowledged = order.acknowledge()
        self.assertEqual(OrderStatus.NEW, acknowledged.status)

        fill = Fill.create(
            order_id=acknowledged.order_id,
            fill_time=OPEN_WEEKDAY_AS_OF,
            price="50000",
            quantity="1",
            fee="2.75",
            liquidity_role=LiquidityRole.TAKER,
            source_evidence_identity="trade-v1:evidence",
        )
        filled = acknowledged.apply_fill(fill)
        self.assertEqual(OrderStatus.FILLED, filled.status)
        self.assertEqual(Decimal("1"), filled.filled_quantity)
        self.assertEqual(order.order_id, filled.order_id)

    def test_illegal_transitions_raise_execution_error(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:illegal",
        ).acknowledge()
        fill = Fill.create(
            order_id=order.order_id,
            fill_time=OPEN_WEEKDAY_AS_OF,
            price="50000",
            quantity="1",
            fee="2.75",
            liquidity_role=LiquidityRole.TAKER,
            source_evidence_identity="trade-v1:evidence",
        )
        filled = order.apply_fill(fill)

        with self.assertRaisesRegex(ExecutionError, "illegal order transition"):
            filled.cancel()

        canceled = order.cancel()
        with self.assertRaisesRegex(ExecutionError, "cannot apply fill to order in status"):
            canceled.apply_fill(fill)

        with self.assertRaisesRegex(ExecutionError, "fill would exceed order quantity"):
            order.apply_fill(
                Fill.create(
                    order_id=order.order_id,
                    fill_time=OPEN_WEEKDAY_AS_OF,
                    price="50000",
                    quantity="2",
                    fee="1",
                    liquidity_role=LiquidityRole.TAKER,
                    source_evidence_identity="trade-v1:evidence",
                )
            )

    def test_open_quantity_is_zero_once_an_order_reaches_a_terminal_status(self):
        base = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:terminal-open-quantity",
        )
        self.assertEqual(Decimal("1"), base.open_quantity)  # PENDING_NEW is still "open"

        acknowledged = base.acknowledge()
        self.assertEqual(Decimal("1"), acknowledged.open_quantity)

        canceled = acknowledged.cancel()
        self.assertEqual(Decimal("0"), canceled.open_quantity)

        rejected = base.reject()
        self.assertEqual(Decimal("0"), rejected.open_quantity)

        filled = acknowledged.apply_fill(
            Fill.create(
                order_id=acknowledged.order_id,
                fill_time=OPEN_WEEKDAY_AS_OF,
                price="50000",
                quantity="1",
                fee="1",
                liquidity_role=LiquidityRole.TAKER,
                source_evidence_identity="trade-v1:evidence",
            )
        )
        self.assertEqual(Decimal("0"), filled.open_quantity)

    def test_fill_quantity_must_be_positive(self):
        with self.assertRaisesRegex(ExecutionError, "quantity must be positive"):
            Fill.create(
                order_id="order-v1:sha256:" + ("a" * 64),
                fill_time=OPEN_WEEKDAY_AS_OF,
                price="50000",
                quantity="0",
                fee="0",
                liquidity_role=LiquidityRole.MAKER,
                source_evidence_identity="trade-v1:evidence",
            )

    def test_fill_time_before_submission_is_refused(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:temporal",
        ).acknowledge()
        earlier = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns - 1)
        with self.assertRaisesRegex(ExecutionError, "fill_time must not precede order submission"):
            order.apply_fill(
                Fill.create(
                    order_id=order.order_id,
                    fill_time=earlier,
                    price="50000",
                    quantity="1",
                    fee="1",
                    liquidity_role=LiquidityRole.TAKER,
                    source_evidence_identity="trade-v1:evidence",
                )
            )

    def test_partial_fills_must_be_chronologically_monotonic(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:monotonic",
        ).acknowledge()
        later = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns + 10)
        partially_filled = order.apply_fill(
            Fill.create(
                order_id=order.order_id,
                fill_time=later,
                price="50000",
                quantity="0.4",
                fee="1",
                liquidity_role=LiquidityRole.TAKER,
                source_evidence_identity="trade-v1:evidence-1",
            )
        )
        self.assertEqual(later, partially_filled.last_fill_time)

        with self.assertRaisesRegex(ExecutionError, "must not precede the order's previous fill"):
            partially_filled.apply_fill(
                Fill.create(
                    order_id=order.order_id,
                    fill_time=OPEN_WEEKDAY_AS_OF,
                    price="50000",
                    quantity="0.6",
                    fee="1",
                    liquidity_role=LiquidityRole.TAKER,
                    source_evidence_identity="trade-v1:evidence-2",
                )
            )

    def test_replace_price_preserves_order_id_and_respects_status(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity="1",
            limit_price="49000",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:replace",
        ).acknowledge()

        replaced = order.replace_price(limit_price="49500", at=OPEN_WEEKDAY_AS_OF)
        self.assertEqual(order.order_id, replaced.order_id)
        self.assertEqual(Decimal("49500"), replaced.limit_price)
        self.assertEqual(OPEN_WEEKDAY_AS_OF, replaced.updated_at)
        self.assertIsNone(order.updated_at)

        with self.assertRaisesRegex(ExecutionError, "does not carry a stop_price"):
            order.replace_price(stop_price="48000", at=OPEN_WEEKDAY_AS_OF)

        earlier = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns - 1)
        with self.assertRaisesRegex(ExecutionError, "must not precede order submission"):
            order.replace_price(limit_price="49500", at=earlier)

        filled = order.apply_fill(
            Fill.create(
                order_id=order.order_id,
                fill_time=OPEN_WEEKDAY_AS_OF,
                price="49000",
                quantity="1",
                fee="1",
                liquidity_role=LiquidityRole.MAKER,
                source_evidence_identity="trade-v1:evidence",
            )
        )
        with self.assertRaisesRegex(ExecutionError, "cannot replace price on order in status"):
            filled.replace_price(limit_price="49500", at=OPEN_WEEKDAY_AS_OF)

    def test_replace_price_refuses_time_travel_before_last_fill_or_previous_replace(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity="2",
            limit_price="49000",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:replace-causality",
        ).acknowledge()
        later = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns + 20)
        earlier_than_later = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns + 10)

        partially_filled = order.apply_fill(
            Fill.create(
                order_id=order.order_id,
                fill_time=later,
                price="49000",
                quantity="1",
                fee="1",
                liquidity_role=LiquidityRole.MAKER,
                source_evidence_identity="trade-v1:evidence",
            )
        )
        with self.assertRaisesRegex(ExecutionError, "must not precede the order's last fill"):
            partially_filled.replace_price(limit_price="49500", at=earlier_than_later)

        replaced_once = order.replace_price(limit_price="49500", at=later)
        with self.assertRaisesRegex(ExecutionError, "must not precede the order's previous replace"):
            replaced_once.replace_price(limit_price="49700", at=earlier_than_later)

    def test_order_type_price_shape_is_enforced(self):
        with self.assertRaisesRegex(ExecutionError, "require limit_price"):
            Order.create(
                instrument="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity="1",
                submitted_at=OPEN_WEEKDAY_AS_OF,
                provenance="test:shape",
            )
        with self.assertRaisesRegex(ExecutionError, "must not carry limit_price"):
            Order.create(
                instrument="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity="1",
                limit_price="49000",
                submitted_at=OPEN_WEEKDAY_AS_OF,
                provenance="test:shape",
            )


class FeeAndSlippageTests(unittest.TestCase):
    def test_fee_schedule_default_rates_and_exact_decimal_math(self):
        schedule = FeeSchedule(policy_key="btc.fees")
        taker_fee = schedule.fee_for(notional="100000", liquidity_role=LiquidityRole.TAKER)
        maker_fee = schedule.fee_for(notional="100000", liquidity_role=LiquidityRole.MAKER)
        self.assertEqual(Decimal("55"), taker_fee)
        self.assertEqual(Decimal("20"), maker_fee)

    def test_build_fill_applies_slippage_directionally_and_computes_fee(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:fill",
        ).acknowledge()
        slippage = SyntheticSlippageModel(policy_key="btc.slippage", slippage_bps="10")
        schedule = FeeSchedule(policy_key="btc.fees")

        fill = build_fill(
            order,
            fill_time=OPEN_WEEKDAY_AS_OF,
            quantity="1",
            reference_price="50000",
            liquidity_role=LiquidityRole.TAKER,
            fee_schedule=schedule,
            source_evidence_identity="trade-v1:evidence",
            slippage_model=slippage,
        )
        expected_price = Decimal("50000") + Decimal("50000") * Decimal("10") / Decimal("10000")
        self.assertEqual(expected_price, fill.price)
        self.assertEqual(expected_price * Decimal("0.00055"), fill.fee)

        sell_order = order  # side only used for slippage direction below
        sell_fill_price = slippage.adjusted_price(reference_price="50000", side=OrderSide.SELL)
        self.assertLess(sell_fill_price, Decimal("50000"))
        self.assertGreater(fill.price, Decimal("50000"))

    def test_build_fill_refuses_over_fill_of_open_quantity(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:overfill",
        ).acknowledge()
        with self.assertRaisesRegex(ExecutionError, "must not exceed the order's open quantity"):
            build_fill(
                order,
                fill_time=OPEN_WEEKDAY_AS_OF,
                quantity="2",
                reference_price="50000",
                liquidity_role=LiquidityRole.TAKER,
                fee_schedule=FeeSchedule(policy_key="btc.fees"),
                source_evidence_identity="trade-v1:evidence",
            )

    def test_build_fill_refuses_for_a_non_fillable_order(self):
        canceled = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:canceled",
        ).acknowledge().cancel()

        with self.assertRaisesRegex(ExecutionError, "cannot build fill for order in status"):
            build_fill(
                canceled,
                fill_time=OPEN_WEEKDAY_AS_OF,
                quantity="1",
                reference_price="50000",
                liquidity_role=LiquidityRole.TAKER,
                fee_schedule=FeeSchedule(policy_key="btc.fees"),
                source_evidence_identity="trade-v1:evidence",
            )

    def test_build_fill_refuses_fill_time_before_submission(self):
        order = Order.create(
            instrument="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1",
            submitted_at=OPEN_WEEKDAY_AS_OF,
            provenance="test:early-fill",
        ).acknowledge()
        earlier = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns - 1)

        with self.assertRaisesRegex(ExecutionError, "fill_time must not precede order submission"):
            build_fill(
                order,
                fill_time=earlier,
                quantity="1",
                reference_price="50000",
                liquidity_role=LiquidityRole.TAKER,
                fee_schedule=FeeSchedule(policy_key="btc.fees"),
                source_evidence_identity="trade-v1:evidence",
            )


class LegQuantityConservationTests(unittest.TestCase):
    def test_refuses_when_active_legs_exceed_open_quantity(self):
        leg_a = Order.create(
            instrument="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity="0.6", submitted_at=OPEN_WEEKDAY_AS_OF, provenance="test:leg-a", reduce_only=True,
        ).acknowledge()
        leg_b = Order.create(
            instrument="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity="0.6", submitted_at=OPEN_WEEKDAY_AS_OF, provenance="test:leg-b", reduce_only=True,
        ).acknowledge()

        with self.assertRaisesRegex(ExecutionError, "exceeds the open position quantity"):
            validate_leg_quantity_conservation(legs=(leg_a, leg_b), open_position_quantity="1")

        # No error when within budget.
        validate_leg_quantity_conservation(legs=(leg_a, leg_b), open_position_quantity="1.2")

    def test_canceled_legs_are_excluded_from_the_sum(self):
        leg_a = Order.create(
            instrument="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity="0.9", submitted_at=OPEN_WEEKDAY_AS_OF, provenance="test:leg-a", reduce_only=True,
        ).acknowledge().cancel()
        leg_b = Order.create(
            instrument="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity="0.5", submitted_at=OPEN_WEEKDAY_AS_OF, provenance="test:leg-b", reduce_only=True,
        ).acknowledge()

        validate_leg_quantity_conservation(legs=(leg_a, leg_b), open_position_quantity="0.5")


class OrderAdmissionInvariantTests(unittest.TestCase):
    def test_normalizes_plain_string_outcome_and_reasons(self):
        admission = OrderAdmission(
            outcome="ADMITTED",
            reasons=(),
            order=Order.create(
                instrument="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity="1",
                submitted_at=OPEN_WEEKDAY_AS_OF,
                provenance="test:admission-normalize",
            ),
            risk_decision=None,
            sizing_decision=None,
            session_decisions=(),
            cooldown_decision=None,
        )

        self.assertIs(AdmissionOutcome.ADMITTED, admission.outcome)
        self.assertTrue(admission.admitted)

        refused = OrderAdmission(
            outcome="REFUSED",
            reasons=("session_closed",),
            order=None,
            risk_decision=None,
            sizing_decision=None,
            session_decisions=(),
            cooldown_decision=None,
        )
        self.assertIs(AdmissionOutcome.REFUSED, refused.outcome)
        self.assertEqual((AdmissionRefusalReason.SESSION_CLOSED,), refused.reasons)

    def test_refuses_inconsistent_outcome_and_payload_combinations(self):
        with self.assertRaisesRegex(ExecutionError, "ADMITTED admission must carry an order"):
            OrderAdmission(
                outcome=AdmissionOutcome.ADMITTED,
                reasons=(),
                order=None,
                risk_decision=None,
                sizing_decision=None,
                session_decisions=(),
                cooldown_decision=None,
            )
        with self.assertRaisesRegex(ExecutionError, "REFUSED admission must carry no order"):
            OrderAdmission(
                outcome=AdmissionOutcome.REFUSED,
                reasons=(),
                order=None,
                risk_decision=None,
                sizing_decision=None,
                session_decisions=(),
                cooldown_decision=None,
            )


class TranslateIntentGatingTests(unittest.TestCase):
    def test_refuses_entry_when_reference_session_is_closed(self):
        strategy_spec = spec()
        intent = entry_intent(strategy_spec, decision_time=CLOSED_WEEKEND_AS_OF)

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=CLOSED_WEEKEND_AS_OF,
            reference_price="50000",
            risk_snapshot=snapshot(observed_at=CLOSED_WEEKEND_AS_OF),
            trade_history=eligible_trade_history(CLOSED_WEEKEND_AS_OF),
            provenance="test:closed-session",
        )

        self.assertEqual(AdmissionOutcome.REFUSED, admission.outcome)
        self.assertIn(AdmissionRefusalReason.SESSION_CLOSED, admission.reasons)
        self.assertIsNone(admission.order)

    def test_refuses_entry_when_cooldown_is_active(self):
        strategy_spec = spec(
            cooldown_policy=cooldown_policy(post_loss_cooldown_seconds=3600)
        )
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)
        history = TradeHistoryEvidence(
            observed_at=OPEN_WEEKDAY_AS_OF,
            evidence_identity="trade-history:recent-loss",
            outcomes=(
                outcome(
                    "2026-09-28T14:00:00Z",
                    "2026-09-28T14:55:00Z",
                    "-10",
                    "loss-1",
                ),
            ),
        )

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=OPEN_WEEKDAY_AS_OF,
            reference_price="50000",
            risk_snapshot=snapshot(observed_at=OPEN_WEEKDAY_AS_OF),
            trade_history=history,
            provenance="test:cooldown",
        )

        self.assertEqual(AdmissionOutcome.REFUSED, admission.outcome)
        self.assertIn(AdmissionRefusalReason.COOLDOWN_ACTIVE, admission.reasons)
        self.assertIsNone(admission.order)

    def test_refuses_entry_when_risk_snapshot_missing(self):
        strategy_spec = spec()
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=OPEN_WEEKDAY_AS_OF,
            reference_price="50000",
            risk_snapshot=None,
            trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
            provenance="test:missing-risk",
        )

        self.assertEqual(AdmissionOutcome.REFUSED, admission.outcome)
        self.assertIn(AdmissionRefusalReason.RISK_REFUSED, admission.reasons)
        self.assertIsNone(admission.order)

    def test_refuses_entry_when_sizing_rounds_to_zero_lots(self):
        strategy_spec = spec(sizing_policy=sizing_policy(lot_size="1", min_size="1"))
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=OPEN_WEEKDAY_AS_OF,
            reference_price="50000",
            risk_snapshot=snapshot(),
            trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
            provenance="test:sizing-zero",
        )

        self.assertEqual(AdmissionOutcome.REFUSED, admission.outcome)
        self.assertIn(AdmissionRefusalReason.SIZING_ZERO, admission.reasons)
        self.assertNotIn(AdmissionRefusalReason.RISK_REFUSED, admission.reasons)
        self.assertIsNone(admission.order)

    def test_admits_entry_with_sizing_decision_quantity_and_correct_side(self):
        strategy_spec = spec()
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=OPEN_WEEKDAY_AS_OF,
            reference_price="50000",
            risk_snapshot=snapshot(),
            trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
            provenance="test:admitted",
        )

        self.assertEqual(AdmissionOutcome.ADMITTED, admission.outcome)
        self.assertTrue(admission.admitted)
        self.assertEqual((), admission.reasons)
        self.assertIsNotNone(admission.order)
        self.assertEqual(OrderSide.BUY, admission.order.side)
        self.assertFalse(admission.order.reduce_only)
        self.assertEqual(admission.sizing_decision.size, admission.order.quantity)
        self.assertGreater(admission.order.quantity, Decimal("0"))

    def test_persistent_entry_signal_pyramids_without_an_external_gate(self):
        """ADR-0053 (#251): translate_intent has no position/ledger awareness
        and does not recognise 'already at this target'. Replaying the same
        entry signal across three ticks admits three full-size entry orders,
        not one -- this is the documented, intentional behavior a caller must
        gate externally, not a bug."""
        strategy_spec = spec()
        tick_times = (
            OPEN_WEEKDAY_AS_OF,
            Instant.parse("2026-09-28T15:01:00Z"),
            Instant.parse("2026-09-28T15:02:00Z"),
        )

        admissions = [
            translate_intent(
                entry_intent(strategy_spec, decision_time=tick_time),
                strategy_spec,
                submitted_at=tick_time,
                reference_price="50000",
                risk_snapshot=snapshot(observed_at=tick_time),
                trade_history=eligible_trade_history(tick_time),
                provenance="test:pyramiding",
            )
            for tick_time in tick_times
        ]

        for admission in admissions:
            self.assertEqual(AdmissionOutcome.ADMITTED, admission.outcome)
            self.assertEqual(OrderSide.BUY, admission.order.side)
            self.assertGreater(admission.order.quantity, Decimal("0"))

        # Three independent orders, each full-sized, not one order reused or
        # a later call reduced/no-op'd because a target was already reached.
        quantities = {admission.order.quantity for admission in admissions}
        self.assertEqual({admissions[0].order.quantity}, quantities)
        order_ids = {admission.order.order_id for admission in admissions}
        self.assertEqual(3, len(order_ids))

    def test_exit_bypasses_session_cooldown_and_risk_but_requires_explicit_quantity(self):
        strategy_spec = spec(
            cooldown_policy=cooldown_policy(post_loss_cooldown_seconds=3600),
        )
        intent = exit_intent(strategy_spec, decision_time=CLOSED_WEEKEND_AS_OF)
        breached_snapshot = snapshot(
            observed_at=CLOSED_WEEKEND_AS_OF,
            equity="7000",
            peak_equity="10000",
        )
        history = TradeHistoryEvidence(
            observed_at=CLOSED_WEEKEND_AS_OF,
            evidence_identity="trade-history:recent-loss",
            outcomes=(
                outcome("2026-09-26T10:00:00Z", "2026-09-26T11:55:00Z", "-10", "loss-1"),
            ),
        )

        with self.assertRaisesRegex(ExecutionError, "close_side and close_quantity are required"):
            translate_intent(
                intent,
                strategy_spec,
                submitted_at=CLOSED_WEEKEND_AS_OF,
                reference_price="50000",
                risk_snapshot=breached_snapshot,
                trade_history=history,
                provenance="test:exit-missing-quantity",
            )

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=CLOSED_WEEKEND_AS_OF,
            reference_price="50000",
            risk_snapshot=breached_snapshot,
            trade_history=history,
            provenance="test:exit",
            close_side=OrderSide.SELL,
            close_quantity="1",
        )

        self.assertEqual(AdmissionOutcome.ADMITTED, admission.outcome)
        self.assertTrue(admission.order.reduce_only)
        self.assertEqual(OrderSide.SELL, admission.order.side)
        self.assertEqual(Decimal("1"), admission.order.quantity)

    def test_exit_is_admitted_even_when_risk_snapshot_is_missing(self):
        # Regression: risk gating must restrict entries only. A missing or
        # stale risk feed must never trap an open position by refusing the
        # exit that would close it.
        strategy_spec = spec()
        intent = exit_intent(strategy_spec, decision_time=CLOSED_WEEKEND_AS_OF)

        admission = translate_intent(
            intent,
            strategy_spec,
            submitted_at=CLOSED_WEEKEND_AS_OF,
            reference_price="50000",
            risk_snapshot=None,
            trade_history=None,
            provenance="test:exit-no-risk-evidence",
            close_side=OrderSide.SELL,
            close_quantity="1",
        )

        self.assertEqual(AdmissionOutcome.ADMITTED, admission.outcome)
        self.assertEqual((), admission.reasons)
        self.assertFalse(admission.risk_decision.accepted)
        self.assertTrue(admission.order.reduce_only)

    def test_submitted_at_before_decision_time_is_refused(self):
        strategy_spec = spec()
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)
        earlier = Instant(OPEN_WEEKDAY_AS_OF.epoch_ns - 1)

        with self.assertRaisesRegex(ExecutionError, "submitted_at must not precede decision_time"):
            translate_intent(
                intent,
                strategy_spec,
                submitted_at=earlier,
                reference_price="50000",
                risk_snapshot=snapshot(),
                trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
                provenance="test:temporal",
            )

    def test_spec_identity_mismatch_is_refused(self):
        strategy_spec = spec()
        other_spec = spec(sizing_policy=sizing_policy(lot_size="0.01", min_size="0.01"))
        intent = entry_intent(strategy_spec, decision_time=OPEN_WEEKDAY_AS_OF)

        with self.assertRaisesRegex(ExecutionError, "does not match intent.strategy_identity"):
            translate_intent(
                intent,
                other_spec,
                submitted_at=OPEN_WEEKDAY_AS_OF,
                reference_price="50000",
                risk_snapshot=snapshot(),
                trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
                provenance="test:mismatch",
            )


class AdmitCompositionResultTests(unittest.TestCase):
    def test_no_decision_result_is_refused_without_evaluating_policies(self):
        strategy_spec = spec()
        result = compose_decision(
            strategy_spec,
            [],
            decision_time=OPEN_WEEKDAY_AS_OF,
            instrument="BTCUSDT",
        )

        admission = admit_composition_result(result, strategy_spec)

        self.assertEqual(AdmissionOutcome.REFUSED, admission.outcome)
        self.assertEqual((AdmissionRefusalReason.NO_DECISION,), admission.reasons)

    def test_decision_intent_result_delegates_to_translate_intent(self):
        strategy_spec = spec()
        result = compose_decision(
            strategy_spec,
            [
                StrategyInput(
                    key="signal.breakout",
                    value=True,
                    available_at=OPEN_WEEKDAY_AS_OF,
                    provenance="feature-artifact:breakout",
                )
            ],
            decision_time=OPEN_WEEKDAY_AS_OF,
            instrument="BTCUSDT",
        )

        admission = admit_composition_result(
            result,
            strategy_spec,
            submitted_at=OPEN_WEEKDAY_AS_OF,
            reference_price="50000",
            risk_snapshot=snapshot(),
            trade_history=eligible_trade_history(OPEN_WEEKDAY_AS_OF),
            provenance="test:composition",
        )

        self.assertEqual(AdmissionOutcome.ADMITTED, admission.outcome)
        self.assertEqual(OrderSide.BUY, admission.order.side)


if __name__ == "__main__":
    unittest.main()
