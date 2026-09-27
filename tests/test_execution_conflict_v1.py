#!/usr/bin/env python3
"""H03 intra-bar conflict resolution, OCO cascade and exit-leg management v1 tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.execution import (
    ExecutionError,
    ExitLegManager,
    Fill,
    LiquidityRole,
    OcoGroup,
    Order,
    OrderSide,
    OrderType,
    PendingTrigger,
    PriceEvent,
    TriggerDirection,
    TriggerRole,
    resolve_intra_bar_triggers,
    validate_leg_quantity_conservation,
)


AS_OF = Instant.parse("2026-09-28T15:00:00Z")


def event(seconds: int, trade_id: str, price: str) -> PriceEvent:
    return PriceEvent(exchange_ts=Instant(AS_OF.epoch_ns + seconds * 1_000_000_000), trade_id=trade_id, price=price)


def make_order(*, side: OrderSide, quantity: str = "1", provenance: str, reduce_only: bool = True) -> Order:
    return Order.create(
        instrument="BTCUSDT",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        submitted_at=AS_OF,
        provenance=provenance,
        reduce_only=reduce_only,
    ).acknowledge()


class IntraBarResolutionTests(unittest.TestCase):
    def test_resolves_in_strict_exchange_ts_trade_id_order(self):
        stop = make_order(side=OrderSide.SELL, provenance="test:stop")
        target = make_order(side=OrderSide.SELL, provenance="test:target")
        pending = (
            PendingTrigger(
                order=stop, trigger_price="49000", direction=TriggerDirection.AT_OR_BELOW,
                role=TriggerRole.STOP_LOSS, conflict_group="pos.long",
            ),
            PendingTrigger(
                order=target, trigger_price="51000", direction=TriggerDirection.AT_OR_ABOVE,
                role=TriggerRole.TAKE_PROFIT, conflict_group="pos.long",
            ),
        )
        events = (
            event(2, "t2", "49500"),   # neither level crossed yet
            event(1, "t1", "51500"),   # out of order on purpose: target level, but earlier trade_id at later index
            event(3, "t3", "48500"),   # stop level crossed
        )
        # Feed events out of chronological order to prove the resolver sorts them itself.
        resolved = resolve_intra_bar_triggers(pending, events)

        self.assertEqual(2, len(resolved))
        # Chronological order is t1 (sec=1) then t3 (sec=3): target fires first, then stop.
        self.assertIs(target, resolved[0].trigger.order)
        self.assertEqual("t1", resolved[0].price_event.trade_id)
        self.assertIs(stop, resolved[1].trigger.order)
        self.assertEqual("t3", resolved[1].price_event.trade_id)

    def test_conservative_tie_break_stop_loss_wins_over_take_profit_same_event(self):
        stop = make_order(side=OrderSide.SELL, provenance="test:stop")
        target = make_order(side=OrderSide.SELL, provenance="test:target")
        # A single gap event crosses both levels at once (both AT_OR_ABOVE
        # their own trigger price, so both are simultaneously triggerable).
        pending = (
            PendingTrigger(
                order=stop, trigger_price="49000", direction=TriggerDirection.AT_OR_ABOVE,
                role=TriggerRole.STOP_LOSS, conflict_group="pos.long",
            ),
            PendingTrigger(
                order=target, trigger_price="51000", direction=TriggerDirection.AT_OR_ABOVE,
                role=TriggerRole.TAKE_PROFIT, conflict_group="pos.long",
            ),
        )
        events = (event(1, "t1", "60000"),)

        resolved = resolve_intra_bar_triggers(pending, events)

        self.assertEqual(1, len(resolved))
        self.assertIs(stop, resolved[0].trigger.order)

    def test_unrelated_conflict_groups_do_not_suppress_each_other(self):
        position_a_stop = make_order(side=OrderSide.SELL, provenance="test:a-stop")
        position_b_target = make_order(side=OrderSide.BUY, provenance="test:b-target")
        pending = (
            PendingTrigger(
                order=position_a_stop, trigger_price="49000", direction=TriggerDirection.AT_OR_BELOW,
                role=TriggerRole.STOP_LOSS, conflict_group="btc.long",
            ),
            PendingTrigger(
                order=position_b_target, trigger_price="49000", direction=TriggerDirection.AT_OR_BELOW,
                role=TriggerRole.TAKE_PROFIT, conflict_group="btc.short",
            ),
        )
        events = (event(1, "t1", "48000"),)

        resolved = resolve_intra_bar_triggers(pending, events)

        self.assertEqual(2, len(resolved))
        fired_orders = {r.trigger.order.order_id for r in resolved}
        self.assertEqual({position_a_stop.order_id, position_b_target.order_id}, fired_orders)

    def test_losing_tie_break_candidate_remains_pending_for_a_later_event(self):
        stop = make_order(side=OrderSide.SELL, provenance="test:stop")
        target = make_order(side=OrderSide.SELL, provenance="test:target")
        pending = (
            PendingTrigger(
                order=stop, trigger_price="49000", direction=TriggerDirection.AT_OR_ABOVE,
                role=TriggerRole.STOP_LOSS, conflict_group="pos.long",
            ),
            PendingTrigger(
                order=target, trigger_price="49000", direction=TriggerDirection.AT_OR_ABOVE,
                role=TriggerRole.TAKE_PROFIT, conflict_group="pos.long",
            ),
        )
        events = (
            event(1, "t1", "50000"),   # both cross; stop wins, target deferred
            event(2, "t2", "50000"),   # target now fires alone (stop already resolved/removed)
        )

        resolved = resolve_intra_bar_triggers(pending, events)

        self.assertEqual(2, len(resolved))
        self.assertIs(stop, resolved[0].trigger.order)
        self.assertEqual("t1", resolved[0].price_event.trade_id)
        self.assertIs(target, resolved[1].trigger.order)
        self.assertEqual("t2", resolved[1].price_event.trade_id)

    def test_no_bar_level_heuristic_only_real_events_matter(self):
        # A level between two real trade prices must NOT be considered crossed,
        # even though a naive high/low bar heuristic would assume it was touched.
        stop = make_order(side=OrderSide.SELL, provenance="test:stop")
        pending = (
            PendingTrigger(
                order=stop, trigger_price="49500", direction=TriggerDirection.AT_OR_BELOW,
                role=TriggerRole.STOP_LOSS, conflict_group="pos.long",
            ),
        )
        events = (event(1, "t1", "50000"), event(2, "t2", "49700"))  # never actually reaches 49500

        resolved = resolve_intra_bar_triggers(pending, events)

        self.assertEqual((), resolved)


class OcoGroupTests(unittest.TestCase):
    def test_any_fill_cascades_cancellation_to_every_sibling(self):
        leg_a = make_order(side=OrderSide.BUY, provenance="test:leg-a", reduce_only=False)
        leg_b = make_order(side=OrderSide.SELL, provenance="test:leg-b", reduce_only=False)
        leg_c = make_order(side=OrderSide.BUY, provenance="test:leg-c", reduce_only=False)
        group = OcoGroup.create("oco.btc.breakout", (leg_a, leg_b, leg_c))

        partial_fill = Fill.create(
            order_id=leg_a.order_id,
            fill_time=AS_OF,
            price="50000",
            quantity="0.3",
            fee="1",
            liquidity_role=LiquidityRole.TAKER,
            source_evidence_identity="trade-v1:evidence",
        )
        updated = group.apply_fill(partial_fill)

        self.assertEqual(updated.members[leg_a.order_id].status.value, "PARTIALLY_FILLED")
        self.assertEqual(updated.members[leg_b.order_id].status.value, "CANCELED")
        self.assertEqual(updated.members[leg_c.order_id].status.value, "CANCELED")

    def test_dynamic_add_and_remove_leg(self):
        leg_a = make_order(side=OrderSide.BUY, provenance="test:leg-a", reduce_only=False)
        group = OcoGroup.create("oco.btc.breakout", (leg_a,))
        leg_b = make_order(side=OrderSide.SELL, provenance="test:leg-b", reduce_only=False)

        expanded = group.add_leg(leg_b)
        self.assertEqual(2, len(expanded.members))

        with self.assertRaisesRegex(ExecutionError, "already a member"):
            expanded.add_leg(leg_b)

        shrunk = expanded.remove_leg(leg_a.order_id)
        self.assertEqual((leg_b.order_id,), tuple(shrunk.members.keys()))

        with self.assertRaisesRegex(ExecutionError, "is not a member"):
            shrunk.remove_leg(leg_a.order_id)

    def test_fill_for_unknown_order_is_refused(self):
        leg_a = make_order(side=OrderSide.BUY, provenance="test:leg-a", reduce_only=False)
        group = OcoGroup.create("oco.btc.breakout", (leg_a,))
        foreign_fill = Fill.create(
            order_id="order-v1:sha256:" + ("a" * 64),
            fill_time=AS_OF,
            price="50000",
            quantity="1",
            fee="1",
            liquidity_role=LiquidityRole.TAKER,
            source_evidence_identity="trade-v1:evidence",
        )
        with self.assertRaisesRegex(ExecutionError, "is not a member of this OCO group"):
            group.apply_fill(foreign_fill)


class ExitLegManagerTests(unittest.TestCase):
    def test_add_leg_reuses_validate_leg_quantity_conservation(self):
        manager = ExitLegManager.create("btc.long")
        leg_a = make_order(side=OrderSide.SELL, quantity="0.6", provenance="test:leg-a")

        manager = manager.add_leg(leg_a, open_position_quantity="1")

        leg_b = make_order(side=OrderSide.SELL, quantity="0.6", provenance="test:leg-b")
        with self.assertRaisesRegex(ExecutionError, "exceeds the open position quantity"):
            manager.add_leg(leg_b, open_position_quantity="1")

        # Same invariant as calling validate_leg_quantity_conservation directly.
        with self.assertRaisesRegex(ExecutionError, "exceeds the open position quantity"):
            validate_leg_quantity_conservation(
                legs=(*manager.legs.values(), leg_b), open_position_quantity="1"
            )

    def test_add_leg_refuses_non_reduce_only_orders(self):
        manager = ExitLegManager.create("btc.long")
        entry_shaped_order = make_order(side=OrderSide.SELL, provenance="test:not-exit", reduce_only=False)
        with self.assertRaisesRegex(ExecutionError, "must be reduce_only"):
            manager.add_leg(entry_shaped_order, open_position_quantity="1")

    def test_filling_one_leg_never_touches_siblings(self):
        manager = ExitLegManager.create("btc.long")
        stop = make_order(side=OrderSide.SELL, quantity="0.5", provenance="test:stop")
        target = make_order(side=OrderSide.SELL, quantity="0.5", provenance="test:target")
        manager = manager.add_leg(stop, open_position_quantity="1")
        manager = manager.add_leg(target, open_position_quantity="1")

        fill = Fill.create(
            order_id=target.order_id,
            fill_time=AS_OF,
            price="50000",
            quantity="0.5",
            fee="1",
            liquidity_role=LiquidityRole.MAKER,
            source_evidence_identity="trade-v1:evidence",
        )
        updated = manager.apply_fill(fill)

        self.assertEqual("FILLED", updated.legs[target.order_id].status.value)
        self.assertEqual("NEW", updated.legs[stop.order_id].status.value)
        self.assertEqual(Decimal("0.5"), updated.active_open_quantity)


if __name__ == "__main__":
    unittest.main()
