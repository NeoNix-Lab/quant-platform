#!/usr/bin/env python3
"""H05 deterministic historical replay runtime v1 tests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord
from quant_platform.execution import FeeSchedule
from quant_platform.replay import HistoricalReplayRuntime, ReplayContext, ReplayError, ReplaySpec
from quant_platform.strategy import (
    CapitalRiskPolicy,
    CooldownPolicyDefinition,
    Direction,
    EntryPolicy,
    ExitPolicy,
    FixedFractionSizingPolicy,
    PositionPolicy,
    SessionPolicyDefinition,
    SessionReferenceMarket,
    SignalCombinationMode,
    SignalCombinationPolicy,
    StrategyInput,
    StrategySpec,
)


@dataclass(frozen=True)
class _PolicyStub:
    label: str

    @property
    def identity(self) -> str:
        return f"{self.label}:sha256:" + ("a" * 64)

    def stable_dict(self):
        return {"policy_type": self.label, "identity": self.identity}


class _Scan:
    def __init__(self, batches):
        self._batches = tuple(tuple(batch) for batch in batches)
        self.completed_metadata = None

    def __iter__(self):
        for batch in self._batches:
            yield batch


class _GatewaySpy:
    def __init__(self, records):
        self.records = tuple(records)
        self.scan_calls = []
        self.read_called = False

    def scan(self, request, *, batch_size=65_536):
        self.scan_calls.append((request, batch_size))
        return _Scan((self.records[:1], self.records[1:]))

    def read(self, request):
        self.read_called = True
        raise AssertionError("replay must not materialize data through DataGateway.read()")


DATASET = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")


def _record(ts: str, price: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=Instant.parse(ts),
        price=price,
        size="1",
        aggressor_side="buy",
        trade_id=trade_id,
        sequence=trade_id.removeprefix("t"),
    )


def _strategy() -> StrategySpec:
    return StrategySpec(
        strategy_key="replay.breakout",
        semantic_version="1",
        entry_policy=EntryPolicy(
            policy_key="replay.entry",
            direction=Direction.LONG,
            signal_key="signal.entry",
            confidence="1",
        ),
        exit_policy=ExitPolicy(
            policy_key="replay.exit",
            signal_key="signal.exit",
        ),
        position_policy=PositionPolicy(
            policy_key="replay.position",
            long_target_position="1",
            short_target_position="1",
        ),
        sizing_policy=FixedFractionSizingPolicy(
            policy_key="replay.sizing",
            lot_size="1",
            min_size="1",
        ),
        risk_policy=CapitalRiskPolicy(
            policy_key="replay.risk",
            max_drawdown_fraction="1",
            max_position_notional_fraction="1",
            max_total_exposure_fraction="1",
            risk_per_trade_fraction="1",
            max_evidence_age_seconds=60,
        ),
        session_policy=SessionPolicyDefinition(
            policy_key="replay.session",
            reference_markets=(SessionReferenceMarket.NEW_YORK,),
        ),
        cooldown_policy=CooldownPolicyDefinition(
            policy_key="replay.cooldown",
            post_loss_cooldown_seconds=0,
            consecutive_loss_count=2,
            consecutive_loss_cooldown_seconds=0,
            max_entries_per_utc_day=100,
            max_trade_history_age_seconds=60,
        ),
        signal_combination_policy=SignalCombinationPolicy(
            policy_key="replay.signals",
            mode=SignalCombinationMode.ALL,
            signal_keys=("signal.entry",),
        ),
        execution_policy=_PolicyStub("replay_execution_policy"),
    )


def _spec() -> ReplaySpec:
    return ReplaySpec(
        dataset=DATASET,
        start="2026-01-05T14:59:00Z",
        end="2026-01-05T15:02:00Z",
        strategy=_strategy(),
        initial_capital="10000",
        fee_schedule=FeeSchedule(policy_key="zero_fee", maker_fee_rate="0", taker_fee_rate="0"),
        ordering_policy="bybit-trade-key-v1",
        batch_size=1,
    )


def _features(record: TradeRecord, context: ReplayContext):
    if record.trade_id == "t1":
        return (
            StrategyInput(
                key="signal.entry",
                value=True,
                available_at=record.exchange_ts,
                provenance="fixture:entry",
            ),
        )
    return (
        StrategyInput(
            key="signal.entry",
            value=False,
            available_at=record.exchange_ts,
            provenance="fixture:no-entry",
        ),
        StrategyInput(
            key="signal.exit",
            value=True,
            available_at=record.exchange_ts,
            provenance="fixture:exit",
        ),
    )


class ReplayV1Tests(unittest.TestCase):
    def test_replay_is_bitwise_reproducible_and_accounting_conserves(self):
        records = (
            _record("2026-01-05T15:00:00Z", "100", "t1"),
            _record("2026-01-05T15:01:00Z", "110", "t2"),
        )

        first = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())
        second = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())

        self.assertEqual(first.trace_fingerprint, second.trace_fingerprint)
        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertEqual([order.order_id for order in first.orders], [order.order_id for order in second.orders])
        self.assertEqual([fill.fill_id for fill in first.fills], [fill.fill_id for fill in second.fills])
        self.assertEqual(Decimal("10010"), first.final_ledger.cash)
        self.assertEqual(Decimal("10"), first.final_ledger.realized_pnl_total)
        self.assertEqual(Decimal("10010"), first.equity_curve[-1].equity)
        self.assertEqual(first.final_ledger.cash, first.final_ledger.mark_to_market_equity({"BTCUSDT": "110"}))

    def test_replay_uses_datagateway_scan_not_read(self):
        records = (_record("2026-01-05T15:00:00Z", "100", "t1"),)
        gateway = _GatewaySpy(records)

        HistoricalReplayRuntime(gateway, _features).run(_spec())

        self.assertEqual(1, len(gateway.scan_calls))
        self.assertFalse(gateway.read_called)
        request, batch_size = gateway.scan_calls[0]
        self.assertEqual(DATASET, request.dataset_selector)
        self.assertEqual(1, batch_size)
        self.assertEqual("2026-01-05T14:59:00Z", request.start.isoformat())
        self.assertEqual("2026-01-05T15:02:00Z", request.end.isoformat())

    def test_future_available_feature_fails_closed_before_decision(self):
        records = (_record("2026-01-05T15:00:00Z", "100", "t1"),)

        def leaky_features(record: TradeRecord, context: ReplayContext):
            return (
                StrategyInput(
                    key="signal.entry",
                    value=True,
                    available_at="2026-01-05T15:00:00.000000001Z",
                    provenance="fixture:leak",
                ),
            )

        with self.assertRaisesRegex(ReplayError, "availability exceeds decision_time"):
            HistoricalReplayRuntime(_GatewaySpy(records), leaky_features).run(_spec())

    def test_replay_enforces_temporal_chain_for_generated_execution(self):
        records = (
            _record("2026-01-05T15:00:00Z", "100", "t1"),
            _record("2026-01-05T15:01:00Z", "110", "t2"),
        )

        result = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())

        self.assertEqual(2, len(result.orders))
        self.assertEqual(2, len(result.fills))
        for order, fill in zip(result.orders, result.fills, strict=True):
            self.assertLessEqual(order.submitted_at, fill.fill_time)
        self.assertLessEqual(result.fills[-1].fill_time, result.final_ledger.updated_at)

    def test_scan_order_regression_fails_closed(self):
        records = (
            _record("2026-01-05T15:01:00Z", "100", "t1"),
            _record("2026-01-05T15:00:00Z", "110", "t2"),
        )

        with self.assertRaisesRegex(ReplayError, "out of temporal order"):
            HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())

    def test_flat_without_open_position_is_traced_without_synthetic_order(self):
        records = (_record("2026-01-05T15:00:00Z", "100", "t2"),)

        result = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())

        self.assertEqual((), result.orders)
        self.assertEqual((), result.fills)
        self.assertEqual("REFUSED", result.admissions[0]["outcome"])
        self.assertEqual(["no_open_position_to_close"], result.admissions[0]["reasons"])
        self.assertEqual(Decimal("10000"), result.final_ledger.cash)


if __name__ == "__main__":
    unittest.main()
