#!/usr/bin/env python3
"""H04 hedge-mode positions and double-entry portfolio ledger v1 tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.execution import Fill, LiquidityRole, Order, OrderSide, OrderType
from quant_platform.portfolio import (
    HedgePosition,
    LedgerTransaction,
    PortfolioError,
    PortfolioLedger,
    PositionDirection,
    PositionSide,
)


AS_OF = Instant.parse("2026-09-28T15:00:00Z")


def later(seconds: int) -> Instant:
    return Instant(AS_OF.epoch_ns + seconds * 1_000_000_000)


def make_order(*, side: OrderSide, quantity: str, reduce_only: bool, at: Instant, provenance: str) -> Order:
    return Order.create(
        instrument="BTCUSDT",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        submitted_at=at,
        provenance=provenance,
        reduce_only=reduce_only,
    ).acknowledge()


def make_fill(*, order: Order, price: str, quantity: str, fee: str, at: Instant, label: str) -> Fill:
    return Fill.create(
        order_id=order.order_id,
        fill_time=at,
        price=price,
        quantity=quantity,
        fee=fee,
        liquidity_role=LiquidityRole.TAKER,
        source_evidence_identity=f"trade-v1:{label}",
    )


class PositionSideTests(unittest.TestCase):
    def test_apply_increase_computes_weighted_average_basis(self):
        side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.LONG, as_of=AS_OF, provenance="test:flat"
        )
        first = side.apply_increase(quantity="1", price="50000", at=later(1), provenance="test:fill-1")
        second = first.apply_increase(quantity="1", price="52000", at=later(2), provenance="test:fill-2")

        self.assertEqual(Decimal("2"), second.quantity)
        self.assertEqual(Decimal("51000"), second.average_basis)
        self.assertEqual(later(1), second.opened_at)
        self.assertEqual(later(2), second.updated_at)

    def test_apply_decrease_realizes_pnl_with_correct_sign_per_direction(self):
        long_side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.LONG, as_of=AS_OF, provenance="test:flat"
        ).apply_increase(quantity="1", price="50000", at=later(1), provenance="test:open")
        closed_long, long_delta = long_side.apply_decrease(
            quantity="1", price="51000", at=later(2), provenance="test:close"
        )
        self.assertEqual(Decimal("1000"), long_delta)
        self.assertEqual(Decimal("0"), closed_long.quantity)
        self.assertIsNone(closed_long.opened_at)

        short_side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.SHORT, as_of=AS_OF, provenance="test:flat"
        ).apply_increase(quantity="1", price="50000", at=later(1), provenance="test:open")
        closed_short, short_delta = short_side.apply_decrease(
            quantity="1", price="51000", at=later(2), provenance="test:close"
        )
        self.assertEqual(Decimal("-1000"), short_delta)  # short loses as price rises

    def test_decrease_refuses_to_exceed_open_quantity(self):
        side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.LONG, as_of=AS_OF, provenance="test:flat"
        ).apply_increase(quantity="1", price="50000", at=later(1), provenance="test:open")
        with self.assertRaisesRegex(PortfolioError, "cannot reduce a position side"):
            side.apply_decrease(quantity="2", price="50000", at=later(2), provenance="test:close")

    def test_market_value_matches_naive_formula_for_long_but_not_short(self):
        long_side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.LONG, as_of=AS_OF, provenance="test:flat"
        ).apply_increase(quantity="1", price="50000", at=later(1), provenance="test:open")
        short_side = PositionSide.flat(
            instrument="BTCUSDT", direction=PositionDirection.SHORT, as_of=AS_OF, provenance="test:flat"
        ).apply_increase(quantity="1", price="50000", at=later(1), provenance="test:open")

        # At mark == basis, both formulas coincide (no PnL yet).
        self.assertEqual(Decimal("50000"), long_side.market_value("50000"))
        self.assertEqual(Decimal("50000"), short_side.market_value("50000"))

        # Price rises: long gains (naive formula agrees), short loses (naive
        # formula price*qty would wrongly say the short's value also rose).
        self.assertEqual(Decimal("51000"), long_side.market_value("51000"))
        self.assertEqual(Decimal("49000"), short_side.market_value("51000"))


class HedgePositionTests(unittest.TestCase):
    def test_buy_and_sell_route_to_correct_side_by_reduce_only(self):
        hedge = HedgePosition.flat(instrument="BTCUSDT", as_of=AS_OF, provenance="test:flat")

        buy_open = make_order(
            side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:buy-open"
        )
        hedge, delta = hedge.apply_fill(
            make_fill(order=buy_open, price="50000", quantity="1", fee="0", at=later(1), label="buy-open"),
            buy_open,
        )
        self.assertEqual(Decimal("1"), hedge.long.quantity)
        self.assertEqual(Decimal("0"), hedge.short.quantity)
        self.assertEqual(Decimal("0"), delta)

        sell_open = make_order(
            side=OrderSide.SELL, quantity="2", reduce_only=False, at=AS_OF, provenance="test:sell-open"
        )
        hedge, delta = hedge.apply_fill(
            make_fill(order=sell_open, price="50000", quantity="2", fee="0", at=later(2), label="sell-open"),
            sell_open,
        )
        self.assertEqual(Decimal("1"), hedge.long.quantity)
        self.assertEqual(Decimal("2"), hedge.short.quantity)

        # Both sides now genuinely concurrent (hedge-mode): reduce the SHORT
        # side via a reduce_only BUY, leaving LONG untouched.
        buy_close = make_order(
            side=OrderSide.BUY, quantity="1", reduce_only=True, at=AS_OF, provenance="test:buy-close"
        )
        hedge, delta = hedge.apply_fill(
            make_fill(order=buy_close, price="51000", quantity="1", fee="0", at=later(3), label="buy-close"),
            buy_close,
        )
        self.assertEqual(Decimal("1"), hedge.long.quantity)  # untouched
        self.assertEqual(Decimal("1"), hedge.short.quantity)  # reduced from 2 to 1
        self.assertEqual(Decimal("-1000"), delta)  # short lost as price rose


class LedgerTransactionTests(unittest.TestCase):
    def test_refuses_unbalanced_or_single_line_entries(self):
        from quant_platform.portfolio import AccountType, EntrySide, JournalLine

        with self.assertRaisesRegex(PortfolioError, "at least two lines"):
            LedgerTransaction.create(
                recorded_at=AS_OF,
                description="degenerate",
                lines=(JournalLine(account=AccountType.CASH, side=EntrySide.DEBIT, amount="1"),),
            )
        with self.assertRaisesRegex(PortfolioError, "not balanced"):
            LedgerTransaction.create(
                recorded_at=AS_OF,
                description="unbalanced",
                lines=(
                    JournalLine(account=AccountType.CASH, side=EntrySide.DEBIT, amount="10"),
                    JournalLine(account=AccountType.CAPITAL, side=EntrySide.CREDIT, amount="5"),
                ),
            )


class PortfolioLedgerTests(unittest.TestCase):
    def test_open_records_a_balanced_initial_capital_transaction(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)

        self.assertEqual(Decimal("10000"), ledger.cash)
        self.assertEqual(Decimal("0"), ledger.position_asset)
        self.assertEqual(Decimal("10000"), ledger.book_equity)
        self.assertEqual(1, len(ledger.transactions))

    def test_equity_is_unchanged_by_opening_a_position_at_its_own_fill_price(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        fill = make_fill(order=order, price="50000", quantity="0.1", fee="0", at=later(1), label="open")

        ledger = ledger.apply_fill(fill, order)

        self.assertEqual(Decimal("10000"), ledger.book_equity)
        self.assertEqual(Decimal("10000"), ledger.mark_to_market_equity({"BTCUSDT": "50000"}))

    def test_book_and_mark_to_market_equity_diverge_and_reconcile_on_close(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        entry = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        ledger = ledger.apply_fill(
            make_fill(order=entry, price="50000", quantity="0.1", fee="0", at=later(1), label="open"), entry
        )

        # Price rises: book equity (cost-basis) is unchanged, mark-to-market
        # equity reflects the unrealized gain.
        self.assertEqual(Decimal("10000"), ledger.book_equity)
        self.assertEqual(Decimal("10010"), ledger.mark_to_market_equity({"BTCUSDT": "50100"}))

        exit_order = make_order(
            side=OrderSide.SELL, quantity="0.1", reduce_only=True, at=later(1), provenance="test:close"
        )
        ledger = ledger.apply_fill(
            make_fill(order=exit_order, price="50100", quantity="0.1", fee="0", at=later(2), label="close"),
            exit_order,
        )

        # After closing at the same mark price, book equity has caught up to
        # what mark-to-market equity already showed, and both now agree.
        self.assertEqual(Decimal("10010"), ledger.book_equity)
        self.assertEqual(Decimal("10010"), ledger.mark_to_market_equity({"BTCUSDT": "50100"}))
        self.assertEqual(Decimal("10"), ledger.realized_pnl_total)
        self.assertEqual(Decimal("0"), ledger.position_asset)

    def test_fees_reduce_cash_and_equity_exactly(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        fill = make_fill(order=order, price="50000", quantity="0.1", fee="2.5", at=later(1), label="open")

        ledger = ledger.apply_fill(fill, order)

        self.assertEqual(Decimal("9997.5"), ledger.book_equity)
        self.assertEqual(Decimal("2.5"), ledger.fees_paid_total)

    def test_loss_exceeding_cost_basis_still_balances(self):
        # A short that blows through its own cost basis on close: cash_delta
        # (cost_basis_removed + realized_delta) goes negative -- exercises
        # the Credit-Cash branch of the closing journal construction.
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        open_short = make_order(
            side=OrderSide.SELL, quantity="1", reduce_only=False, at=AS_OF, provenance="test:open-short"
        )
        ledger = ledger.apply_fill(
            make_fill(order=open_short, price="100", quantity="1", fee="0", at=later(1), label="open-short"),
            open_short,
        )
        close_short = make_order(
            side=OrderSide.BUY, quantity="1", reduce_only=True, at=later(1), provenance="test:close-short"
        )
        ledger = ledger.apply_fill(
            make_fill(order=close_short, price="1000", quantity="1", fee="0", at=later(2), label="close-short"),
            close_short,
        )

        self.assertEqual(Decimal("-900"), ledger.realized_pnl_total)
        self.assertEqual(Decimal("9100"), ledger.book_equity)

    def test_hedge_mode_dual_position_tracked_independently_end_to_end(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        long_open = make_order(
            side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:long-open"
        )
        short_open = make_order(
            side=OrderSide.SELL, quantity="1", reduce_only=False, at=AS_OF, provenance="test:short-open"
        )
        ledger = ledger.apply_fill(
            make_fill(order=long_open, price="100", quantity="1", fee="0", at=later(1), label="long-open"),
            long_open,
        )
        ledger = ledger.apply_fill(
            make_fill(order=short_open, price="100", quantity="1", fee="0", at=later(2), label="short-open"),
            short_open,
        )

        hedge = ledger.positions["BTCUSDT"]
        self.assertEqual(Decimal("1"), hedge.long.quantity)
        self.assertEqual(Decimal("1"), hedge.short.quantity)

        # Price moves to 110: long gains 10, short loses 10 -- net unrealized
        # is zero, proving the two sides are tracked with independent,
        # correctly opposed sensitivity rather than one net position.
        self.assertEqual(Decimal("10"), hedge.long.unrealized_pnl("110"))
        self.assertEqual(Decimal("-10"), hedge.short.unrealized_pnl("110"))
        self.assertEqual(Decimal("0"), hedge.unrealized_pnl("110"))
        self.assertEqual(Decimal("10000"), ledger.mark_to_market_equity({"BTCUSDT": "110"}))

    def test_fill_time_must_not_precede_ledger_state(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=later(5))
        order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        early_fill = make_fill(order=order, price="50000", quantity="0.1", fee="0", at=later(1), label="early")

        with self.assertRaisesRegex(PortfolioError, "must not precede the ledger's current state"):
            ledger.apply_fill(early_fill, order)

    def test_ledger_refuses_duplicate_fill_identity(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:open")
        fill = make_fill(order=order, price="100", quantity="1", fee="0", at=later(1), label="open")

        ledger = ledger.apply_fill(fill, order)

        with self.assertRaisesRegex(PortfolioError, "fill_id was already consumed"):
            ledger.apply_fill(fill, order)

    def test_ledger_refuses_fill_that_h01_order_state_machine_rejects(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:open")
        oversized = make_fill(order=order, price="100", quantity="2", fee="0", at=later(1), label="oversized")

        with self.assertRaisesRegex(PortfolioError, "order fill admission failed: fill would exceed order quantity"):
            ledger.apply_fill(oversized, order)

        canceled = order.cancel()
        valid_shape_fill = make_fill(order=canceled, price="100", quantity="1", fee="0", at=later(1), label="canceled")
        with self.assertRaisesRegex(PortfolioError, "order fill admission failed: cannot apply fill"):
            ledger.apply_fill(valid_shape_fill, canceled)

    def test_ledger_accepts_partial_remainder_only_with_updated_order_state(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:open")
        first_fill = make_fill(order=order, price="100", quantity="0.4", fee="0", at=later(1), label="partial-1")
        ledger = ledger.apply_fill(first_fill, order)
        partially_filled_order = order.apply_fill(first_fill)

        too_large_remainder = make_fill(
            order=partially_filled_order, price="100", quantity="0.7", fee="0", at=later(2), label="partial-too-large"
        )
        with self.assertRaisesRegex(PortfolioError, "order fill admission failed: fill would exceed order quantity"):
            ledger.apply_fill(too_large_remainder, partially_filled_order)

        final_fill = make_fill(
            order=partially_filled_order, price="100", quantity="0.6", fee="0", at=later(2), label="partial-2"
        )
        ledger = ledger.apply_fill(final_fill, partially_filled_order)

        self.assertEqual(Decimal("1.0"), ledger.positions["BTCUSDT"].long.quantity)
        self.assertEqual((first_fill.fill_id, final_fill.fill_id), ledger.consumed_fill_ids)

    def test_fill_settlement_transactions_are_bound_to_fill_evidence(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        first_order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:one")
        second_order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:two")
        first_fill = make_fill(order=first_order, price="50000", quantity="0.1", fee="1", at=later(1), label="same")
        second_fill = make_fill(order=second_order, price="50000", quantity="0.1", fee="1", at=later(1), label="same")

        ledger = ledger.apply_fill(first_fill, first_order)
        ledger = ledger.apply_fill(second_fill, second_order)

        self.assertNotEqual(ledger.transactions[1].transaction_id, ledger.transactions[2].transaction_id)
        self.assertEqual(first_fill.fill_id, ledger.transactions[1].evidence["fill_id"])
        self.assertEqual(second_fill.fill_id, ledger.transactions[2].evidence["fill_id"])

    def test_missing_mark_price_is_refused(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        order = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        ledger = ledger.apply_fill(
            make_fill(order=order, price="50000", quantity="0.1", fee="0", at=later(1), label="open"), order
        )
        with self.assertRaisesRegex(PortfolioError, "missing mark price"):
            ledger.mark_to_market_equity({})

    def test_fully_closed_instrument_does_not_require_a_mark_price(self):
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        entry = make_order(side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open")
        ledger = ledger.apply_fill(
            make_fill(order=entry, price="50000", quantity="0.1", fee="0", at=later(1), label="open"), entry
        )
        exit_order = make_order(
            side=OrderSide.SELL, quantity="0.1", reduce_only=True, at=later(1), provenance="test:close"
        )
        ledger = ledger.apply_fill(
            make_fill(order=exit_order, price="50000", quantity="0.1", fee="0", at=later(2), label="close"),
            exit_order,
        )

        self.assertEqual(Decimal("0"), ledger.positions["BTCUSDT"].long.quantity)
        # No mark price supplied for BTCUSDT at all: must not raise, since
        # there is no live exposure left to value.
        self.assertEqual(ledger.book_equity, ledger.mark_to_market_equity({}))

    def test_repeating_fraction_basis_exact_accounting_single_exit(self):
        # 1 @ 100 + 2 @ 101 => total cost 302 across 3 units.
        # basis = 302/3 = 100.66666... (non-terminating).
        # On single full close (3 @ 102), cost_basis_removed must equal 302
        # exactly without decimal division residue underflow.
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        e1 = make_order(side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:e1")
        ledger = ledger.apply_fill(make_fill(order=e1, price="100", quantity="1", fee="0", at=later(1), label="e1"), e1)
        e2 = make_order(side=OrderSide.BUY, quantity="2", reduce_only=False, at=later(1), provenance="test:e2")
        ledger = ledger.apply_fill(make_fill(order=e2, price="101", quantity="2", fee="0", at=later(2), label="e2"), e2)

        exit_order = make_order(side=OrderSide.SELL, quantity="3", reduce_only=True, at=later(2), provenance="test:exit")
        ledger = ledger.apply_fill(make_fill(order=exit_order, price="102", quantity="3", fee="0", at=later(3), label="x"), exit_order)

        self.assertEqual(Decimal("10004"), ledger.cash)
        self.assertEqual(Decimal("0"), ledger.position_asset)
        self.assertEqual(Decimal("4"), ledger.realized_pnl_total)
        self.assertEqual(Decimal("10004"), ledger.book_equity)

    def test_repeating_fraction_basis_exact_accounting_laddered_exits(self):
        # Same non-terminating basis closed in 2 separate fills (1 @ 102, then 2 @ 102).
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        e1 = make_order(side=OrderSide.BUY, quantity="1", reduce_only=False, at=AS_OF, provenance="test:e1")
        ledger = ledger.apply_fill(make_fill(order=e1, price="100", quantity="1", fee="0", at=later(1), label="e1"), e1)
        e2 = make_order(side=OrderSide.BUY, quantity="2", reduce_only=False, at=later(1), provenance="test:e2")
        ledger = ledger.apply_fill(make_fill(order=e2, price="101", quantity="2", fee="0", at=later(2), label="e2"), e2)

        x1 = make_order(side=OrderSide.SELL, quantity="1", reduce_only=True, at=later(2), provenance="test:x1")
        ledger = ledger.apply_fill(make_fill(order=x1, price="102", quantity="1", fee="0", at=later(3), label="x1"), x1)
        x2 = make_order(side=OrderSide.SELL, quantity="2", reduce_only=True, at=later(3), provenance="test:x2")
        ledger = ledger.apply_fill(make_fill(order=x2, price="102", quantity="2", fee="0", at=later(4), label="x2"), x2)

        self.assertEqual(Decimal("10004"), ledger.cash)
        self.assertEqual(Decimal("0"), ledger.position_asset)
        self.assertEqual(Decimal("4"), ledger.realized_pnl_total)
        self.assertEqual(Decimal("10004"), ledger.book_equity)

    def test_repeating_fraction_basis_exact_accounting_short_position(self):
        # Short 1 @ 100 + Short 2 @ 101 => total cost 302 across 3 units.
        # Cover in 2 fills at 98: profit is (302 - 3*98) = 8.
        ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
        e1 = make_order(side=OrderSide.SELL, quantity="1", reduce_only=False, at=AS_OF, provenance="test:e1")
        ledger = ledger.apply_fill(make_fill(order=e1, price="100", quantity="1", fee="0", at=later(1), label="e1"), e1)
        e2 = make_order(side=OrderSide.SELL, quantity="2", reduce_only=False, at=later(1), provenance="test:e2")
        ledger = ledger.apply_fill(make_fill(order=e2, price="101", quantity="2", fee="0", at=later(2), label="e2"), e2)

        x1 = make_order(side=OrderSide.BUY, quantity="1", reduce_only=True, at=later(2), provenance="test:x1")
        ledger = ledger.apply_fill(make_fill(order=x1, price="98", quantity="1", fee="0", at=later(3), label="x1"), x1)
        x2 = make_order(side=OrderSide.BUY, quantity="2", reduce_only=True, at=later(3), provenance="test:x2")
        ledger = ledger.apply_fill(make_fill(order=x2, price="98", quantity="2", fee="0", at=later(4), label="x2"), x2)

        self.assertEqual(Decimal("10008"), ledger.cash)
        self.assertEqual(Decimal("0"), ledger.position_asset)
        self.assertEqual(Decimal("8"), ledger.realized_pnl_total)
        self.assertEqual(Decimal("10008"), ledger.book_equity)

    def test_ledger_identity_is_deterministic_for_identical_history(self):
        def build() -> PortfolioLedger:
            ledger = PortfolioLedger.open(initial_capital="10000", as_of=AS_OF)
            order = make_order(
                side=OrderSide.BUY, quantity="0.1", reduce_only=False, at=AS_OF, provenance="test:open"
            )
            fill = make_fill(order=order, price="50000", quantity="0.1", fee="1", at=later(1), label="open")
            return ledger.apply_fill(fill, order)

        first = build()
        second = build()
        self.assertEqual(first.identity, second.identity)
        self.assertTrue(first.identity.startswith("portfolio-ledger-v1:sha256:"))


if __name__ == "__main__":
    unittest.main()
