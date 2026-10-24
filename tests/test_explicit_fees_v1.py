"""DG-N T03: a fill or a replay of an instrument other than BTCUSDT needs explicit fee rates (ADR-0074 section 7).

The default maker/taker rates are the Bybit BTCUSDT VIP0 rates. A schedule that defaulted a rate is refused, with a stable
reason, for every other instrument; a schedule with both rates stated is accepted for any instrument; and nothing about the
identity of a schedule, of a replay spec or of a BTCUSDT fill changes.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.golden_replay import build_golden_replay_spec, minimal_breakout_strategy  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.execution import (  # noqa: E402
    FEE_SCHEDULE_NOT_EXPLICIT,
    ExecutionError,
    FeeSchedule,
    FeeScheduleNotExplicit,
    LiquidityRole,
    Order,
    OrderSide,
    OrderType,
    build_fill,
)
from quant_platform.replay import ReplayError, ReplayFeeScheduleNotExplicit, ReplaySpec  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1, BYBIT_TRADE_V1_ORDERING_POLICY  # noqa: E402

NOT_BTC = tuple(item for item in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 if item != "BTCUSDT")
NOW = Instant.parse("2026-01-05T15:00:00Z")

# Computed on implement/fid before this change (git archive of 7c5c902): the identities must not move.
DEFAULT_SCHEDULE_IDENTITY = "fee-schedule-v1:sha256:d8bdfcf0dbc46c7f2f956a631c8be37d0687da1e05bdbda35384f66cd6bc1dea"
EXPLICIT_SCHEDULE_IDENTITY = "fee-schedule-v1:sha256:5b2cdd1733d4393d7dacb25bd18b07be090c5c51ec77c9f44b1eb168d0ec493e"
GOLDEN_SPEC_IDENTITY = "replay-spec-v1:sha256:6470fd7804216aacb8afaff6b749b63557e2dbab24d0c5be169df1bc4139cc72"


def explicit(key: str = "x.fees") -> FeeSchedule:
    return FeeSchedule(policy_key=key, maker_fee_rate="0.0001", taker_fee_rate="0.0004")


def fill_with(instrument: str, schedule: FeeSchedule):
    order = Order.create(
        instrument=instrument, side=OrderSide.BUY, order_type=OrderType.MARKET, quantity="2", submitted_at=NOW,
        provenance="test:fees",
    ).acknowledge()
    return build_fill(
        order, fill_time=NOW, quantity="2", reference_price="100", liquidity_role=LiquidityRole.TAKER,
        fee_schedule=schedule, source_evidence_identity="trade-v1:evidence",
    )


def spec_for(instrument: str, schedule: FeeSchedule) -> ReplaySpec:
    return ReplaySpec(
        dataset=DatasetIdentity("canonical", "trades", "bybit", instrument, "trade-v1"),
        start="2024-01-15T00:00:00Z", end="2024-01-15T01:00:00Z", strategy=minimal_breakout_strategy(),
        initial_capital="10000", fee_schedule=schedule, ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


class ScheduleTests(unittest.TestCase):
    def test_a_rate_left_out_is_a_defaulted_schedule(self) -> None:
        self.assertTrue(FeeSchedule(policy_key="x.fees").rates_defaulted)
        self.assertTrue(FeeSchedule(policy_key="x.fees", maker_fee_rate="0.0001").rates_defaulted)
        self.assertTrue(FeeSchedule(policy_key="x.fees", taker_fee_rate="0.0004").rates_defaulted)
        self.assertFalse(explicit().rates_defaulted)

    def test_stating_the_default_values_is_explicit(self) -> None:
        stated = FeeSchedule(policy_key="x.fees", maker_fee_rate="0.0002", taker_fee_rate="0.00055")
        self.assertFalse(stated.rates_defaulted)
        stated.require_explicit_for("ETHUSDT")

    def test_a_copy_of_a_defaulted_schedule_stays_defaulted(self) -> None:
        defaulted = FeeSchedule(policy_key="x.fees")
        for copy in (replace(defaulted, policy_key="eth.fees"), replace(defaulted, maker_fee_rate=Decimal("0.0001"))):
            self.assertTrue(copy.rates_defaulted)
            with self.assertRaises(FeeScheduleNotExplicit):
                copy.require_explicit_for("ETHUSDT")

    def test_the_identity_of_a_schedule_does_not_move(self) -> None:
        self.assertEqual(DEFAULT_SCHEDULE_IDENTITY, FeeSchedule(policy_key="btc.fees").identity)
        self.assertEqual(EXPLICIT_SCHEDULE_IDENTITY, FeeSchedule(
            policy_key="eth.fees", maker_fee_rate="0.0001", taker_fee_rate="0.0004").identity)
        # Same key and same rates: same identity whether the rates were stated or defaulted.
        self.assertEqual(
            FeeSchedule(policy_key="k.fees").identity,
            FeeSchedule(policy_key="k.fees", maker_fee_rate="0.0002", taker_fee_rate="0.00055").identity,
        )

    def test_the_default_arithmetic_is_unchanged(self) -> None:
        schedule = FeeSchedule(policy_key="btc.fees")
        self.assertEqual(Decimal("55"), schedule.fee_for(notional="100000", liquidity_role=LiquidityRole.TAKER))
        self.assertEqual(Decimal("20"), schedule.fee_for(notional="100000", liquidity_role=LiquidityRole.MAKER))


class FillTests(unittest.TestCase):
    def test_btcusdt_may_use_the_defaults(self) -> None:
        self.assertEqual(Decimal("0.11"), fill_with("BTCUSDT", FeeSchedule(policy_key="btc.fees")).fee)

    def test_every_other_allow_list_instrument_is_refused_with_a_stable_reason(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                with self.assertRaises(FeeScheduleNotExplicit) as refused:
                    fill_with(instrument, FeeSchedule(policy_key="x.fees"))
                self.assertEqual(FEE_SCHEDULE_NOT_EXPLICIT, refused.exception.reason)
                self.assertEqual("fee_schedule_not_explicit", refused.exception.reason)
                self.assertEqual(instrument, refused.exception.instrument)
                self.assertIsInstance(refused.exception, ExecutionError)
                self.assertTrue(str(refused.exception).startswith("fee_schedule_not_explicit: " + instrument))

    def test_an_instrument_outside_the_allow_list_is_refused_too(self) -> None:
        with self.assertRaises(FeeScheduleNotExplicit):
            fill_with("ADAUSDT", FeeSchedule(policy_key="x.fees"))

    def test_one_stated_rate_is_not_enough(self) -> None:
        with self.assertRaises(FeeScheduleNotExplicit):
            fill_with("ETHUSDT", FeeSchedule(policy_key="x.fees", maker_fee_rate="0.0001"))
        with self.assertRaises(FeeScheduleNotExplicit):
            fill_with("ETHUSDT", FeeSchedule(policy_key="x.fees", taker_fee_rate="0.0004"))

    def test_explicit_rates_are_charged_for_any_instrument(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                self.assertEqual(Decimal("0.08"), fill_with(instrument, explicit()).fee)  # 200 * 0.0004

    def test_the_refusal_comes_before_anything_else_is_checked(self) -> None:
        order = Order.create(
            instrument="ETHUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity="1", submitted_at=NOW,
            provenance="test:fees",
        )  # not acknowledged: the fee check must not depend on the order state
        with self.assertRaises(FeeScheduleNotExplicit):
            build_fill(order, fill_time=NOW, quantity="1", reference_price="100", liquidity_role=LiquidityRole.TAKER,
                       fee_schedule=FeeSchedule(policy_key="x.fees"), source_evidence_identity="trade-v1:evidence")


class ReplayTests(unittest.TestCase):
    def test_the_golden_spec_identity_does_not_move(self) -> None:
        self.assertEqual(GOLDEN_SPEC_IDENTITY, build_golden_replay_spec(
            start="2024-01-15T00:00:00Z", end="2024-01-15T01:00:00Z").identity)

    def test_btcusdt_may_replay_with_the_defaults(self) -> None:
        self.assertEqual(GOLDEN_SPEC_IDENTITY, spec_for("BTCUSDT", FeeSchedule(policy_key="golden.fees")).identity)

    def test_every_other_instrument_is_refused_before_any_data_is_read(self) -> None:
        for instrument in (*NOT_BTC, "ADAUSDT"):
            with self.subTest(instrument=instrument):
                with self.assertRaises(ReplayFeeScheduleNotExplicit) as refused:
                    spec_for(instrument, FeeSchedule(policy_key="x.fees"))
                self.assertEqual(FEE_SCHEDULE_NOT_EXPLICIT, refused.exception.reason)
                self.assertIsInstance(refused.exception, ReplayError)
                self.assertIn(instrument, str(refused.exception))

    def test_explicit_rates_replay_any_instrument(self) -> None:
        identities = set()
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                identities.add(spec_for(instrument, explicit()).identity)
        self.assertEqual(len(NOT_BTC), len(identities), "the instrument is part of the replay identity")


if __name__ == "__main__":
    unittest.main()
