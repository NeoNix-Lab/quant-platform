#!/usr/bin/env python3
"""H05 deterministic historical replay runtime v1 tests."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord
from quant_platform.execution import FeeSchedule
import quant_platform.strategy as strategy_module
from quant_platform.replay import (
    HistoricalReplayRuntime,
    ReplayContext,
    ReplayError,
    ReplayOutputConfig,
    ReplayOutputMode,
    ReplaySpec,
)
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
        self.assertEqual(
            "replay-spec-v1:sha256:e930cfc678274a61c8078fbba99b1efb6287740a62a57dbc5deee95cd92c1559",
            first.spec_identity,
        )
        self.assertEqual(
            "replay-result-v1:sha256:11c55c7ac41fad4152959dbc49fed620df8a436d361bc63a550d8d26ab301984",
            first.identity,
        )
        self.assertEqual(
            "replay-summary-v1:sha256:b30117624964014c71068ad4a84069425f42a9e92c94ff6626d05541fd985e28",
            first.summary.digest,
        )
        self.assertEqual(
            [
                "portfolio-ledger-v1:sha256:f0456226a7cdeefaeec1322ff8728e91210f12709cbd0712bb35e97fc6afa9de",
                "portfolio-ledger-v1:sha256:7eace7a517a57c5d6467b77ce79049df11cc2a7e0158b6af8c0d382371a7f88f",
            ],
            [snapshot.ledger_identity for snapshot in first.equity_curve],
        )
        self.assertEqual([order.order_id for order in first.orders], [order.order_id for order in second.orders])
        self.assertEqual([fill.fill_id for fill in first.fills], [fill.fill_id for fill in second.fills])
        self.assertEqual(Decimal("10010"), first.final_ledger.cash)
        self.assertEqual(Decimal("10"), first.final_ledger.realized_pnl_total)
        self.assertEqual(Decimal("10010"), first.equity_curve[-1].equity)
        self.assertEqual(first.final_ledger.cash, first.final_ledger.mark_to_market_equity({"BTCUSDT": "110"}))

    def test_replay_caches_strategy_and_policy_identities_per_immutable_spec(self):
        original_fingerprint = strategy_module._canonical_fingerprint
        strategy_identity_hashes = 0

        def count_strategy_identity_hashes(payload):
            nonlocal strategy_identity_hashes
            if payload.get("identity_type") == "strategy-spec":
                strategy_identity_hashes += 1
            return original_fingerprint(payload)

        with patch.object(strategy_module, "_canonical_fingerprint", count_strategy_identity_hashes):
            strategy = dataclass_replace(_strategy())
            spec = dataclass_replace(_spec(), strategy=strategy)
            expected = strategy.strategy_identity
            hashes_after_construction = strategy_identity_hashes
            result = HistoricalReplayRuntime(
                _GatewaySpy(
                    (
                        _record("2026-01-05T15:00:00Z", "100", "t1"),
                        _record("2026-01-05T15:00:10Z", "100", "t2"),
                        _record("2026-01-05T15:00:20Z", "100", "t3"),
                    )
                ),
                _features,
            ).run(spec)

        self.assertEqual(expected, strategy.strategy_identity)
        self.assertEqual(spec.identity, result.spec_identity)
        self.assertGreater(hashes_after_construction, 0)
        self.assertEqual(hashes_after_construction, strategy_identity_hashes)

    def test_summary_mode_preserves_accounting_and_digest_without_retaining_trace(self):
        records = (
            _record("2026-01-05T15:00:00Z", "100", "t1"),
            _record("2026-01-05T15:01:00Z", "110", "t2"),
        )

        full = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(_spec())
        summary = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(
            _spec(), output=ReplayOutputConfig(ReplayOutputMode.SUMMARY)
        )
        repeated_summary = HistoricalReplayRuntime(_GatewaySpy(records), _features).run(
            _spec(), output=ReplayOutputConfig(ReplayOutputMode.SUMMARY)
        )

        self.assertEqual(full.final_ledger.stable_dict(), summary.final_ledger.stable_dict())
        self.assertEqual(full.summary.digest, summary.summary.digest)
        self.assertEqual(summary.summary.digest, repeated_summary.summary.digest)
        self.assertEqual(len(full.decisions), summary.summary.decision_count)
        self.assertEqual(len(full.admissions), summary.summary.admission_count)
        self.assertEqual(len(full.equity_curve), summary.summary.equity_snapshot_count)
        self.assertEqual(len(full.orders), summary.summary.order_count)
        self.assertEqual(len(full.fills), summary.summary.fill_count)
        self.assertEqual((), summary.decisions)
        self.assertEqual((), summary.admissions)
        self.assertEqual((), summary.equity_curve)
        self.assertEqual((), summary.orders)
        self.assertEqual((), summary.fills)

    def test_persistent_entry_signal_noops_after_reaching_target_position(self):
        records = (
            _record("2026-01-05T15:00:00Z", "100", "t1"),
            _record("2026-01-05T15:00:10Z", "100", "t2"),
            _record("2026-01-05T15:00:20Z", "100", "t3"),
        )

        def persistent_entry_features(record, context):
            return (
                StrategyInput(
                    key="signal.entry",
                    value=True,
                    available_at=record.exchange_ts,
                    provenance="test:persistent-entry",
                ),
            )

        result = HistoricalReplayRuntime(
            _GatewaySpy(records), persistent_entry_features
        ).run(_spec())

        admitted = [a for a in result.admissions if a["outcome"] == "ADMITTED"]
        refused = [a for a in result.admissions if a["outcome"] == "REFUSED"]
        self.assertEqual(1, len(admitted))
        self.assertEqual(2, len(refused))
        self.assertEqual("BUY", admitted[0]["order"]["side"])
        self.assertEqual(Decimal("1"), Decimal(admitted[0]["order"]["quantity"]))
        for admission in refused:
            self.assertEqual(["already_at_target_position"], admission["reasons"])
            self.assertEqual("1", admission["current_quantity"])
            self.assertEqual("1", admission["target_quantity"])
        position = result.final_ledger.positions["BTCUSDT"]
        self.assertEqual(Decimal("1"), position.long.quantity)

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

    def test_peak_equity_tracks_mark_to_market_swings_with_no_trade(self):
        # Regression for the adversarial-review exploit: a spike-then-reversal
        # with no trade in between must still raise peak_equity on the spike
        # tick, so a later CapitalRiskPolicy drawdown check sees the real
        # drawdown from that peak, not a stale one from the last fill.
        tight_strategy = dataclass_replace(
            _strategy(),
            risk_policy=CapitalRiskPolicy(
                policy_key="replay.risk.tight",
                max_drawdown_fraction="0.01",
                max_position_notional_fraction="1",
                max_total_exposure_fraction="1",
                risk_per_trade_fraction="1",
                max_evidence_age_seconds=60,
            ),
        )
        spec = dataclass_replace(_spec(), strategy=tight_strategy)

        records = (
            _record("2026-01-05T15:00:00Z", "1000", "t1"),  # open long @ 1000
            _record("2026-01-05T15:00:30Z", "2000", "t2"),  # spike: equity 11000, no trade
            _record("2026-01-05T15:01:00Z", "1050", "t3"),  # drop: equity 10050, re-entry signal
        )

        def features(record, context):
            if record.trade_id in ("t1", "t3"):
                return (
                    StrategyInput(
                        key="signal.entry", value=True, available_at=record.exchange_ts, provenance="fixture"
                    ),
                )
            return (
                StrategyInput(
                    key="signal.entry", value=False, available_at=record.exchange_ts, provenance="fixture"
                ),
            )

        result = HistoricalReplayRuntime(_GatewaySpy(records), features).run(spec)

        # The spike tick (t2) must itself have already raised peak_equity to
        # 11000, proving no-trade ticks are not silently skipped.
        self.assertEqual(Decimal("11000"), result.equity_curve[1].equity)

        # t3's own risk evaluation must have been evaluated against that same
        # 11000 peak (not a stale 10000) -- checked directly on the risk
        # decision's own evidence, independent of t1's re-signal happening to
        # also qualify CapitalRiskPolicy's separate de-risking exemption.
        t3_admission = result.admissions[-1]
        risk_snapshot_evidence = t3_admission["risk_decision"]["evidence"]
        self.assertEqual("11000", risk_snapshot_evidence["peak_equity"])
        self.assertEqual("10050", risk_snapshot_evidence["equity"])

    def test_feature_provider_sees_current_tick_peak_equity_not_stale(self):
        # F2: context.peak_equity must already reflect this record's own
        # mark-to-market swing, not just whatever the last fill produced.
        records = (
            _record("2026-01-05T15:00:00Z", "1000", "t1"),  # open long @ 1000
            _record("2026-01-05T15:00:30Z", "2000", "t2"),  # spike, no trade
        )
        seen_peaks: list[Decimal] = []

        def features(record, context):
            seen_peaks.append(context.peak_equity)
            if record.trade_id == "t1":
                return (
                    StrategyInput(
                        key="signal.entry", value=True, available_at=record.exchange_ts, provenance="fixture"
                    ),
                )
            return ()

        HistoricalReplayRuntime(_GatewaySpy(records), features).run(_spec())

        # t1's own context is built before any position exists (peak == initial capital).
        self.assertEqual(Decimal("10000"), seen_peaks[0])
        # t2's context must already see the 11000 mark-to-market peak from t2
        # itself, not the stale 10000 carried over from t1.
        self.assertEqual(Decimal("11000"), seen_peaks[1])

    def test_batch_size_does_not_affect_replay_spec_identity(self):
        # F3: batch_size is a pure I/O paging knob and must not change the
        # deterministic identity used to prove "same replay, same result".
        base = _spec()
        different_batching = dataclass_replace(base, batch_size=4096)

        self.assertNotEqual(base.batch_size, different_batching.batch_size)
        self.assertEqual(base.identity, different_batching.identity)

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
