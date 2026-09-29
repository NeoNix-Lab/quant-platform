#!/usr/bin/env python3
"""D04 incremental/live candle runtime proof for CandleDefinition v1."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    CandleDefinitionV1,
    CandleFinalizationError,
    CandleOrderingError,
    CandleRuntimeState,
    IncrementalCandleBuilder,
    aggregate_historical_candles,
    closed_candle_records,
)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def trade(ts: str, price: str, size: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price=price,
        size=size,
        aggressor_side="buy",
        trade_id=trade_id,
    )


class IncrementalLiveCandleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = CandleDefinitionV1.from_duration("5m")

    def test_closed_incremental_output_matches_historical_d03_records(self):
        trades = (
            trade("2024-01-01T10:00:00Z", "100.5000", "0.100", "1"),
            trade("2024-01-01T10:04:59.999999999Z", "101.000", "1.000", "2"),
            trade("2024-01-01T10:05:00Z", "99.25", "0.0200", "3"),
            trade("2024-01-01T10:09:59Z", "102", "2.5", "4"),
        )
        historical = aggregate_historical_candles(
            trades,
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:10:00Z",
        )
        builder = IncrementalCandleBuilder(self.definition)

        partials = tuple(builder.consume(record) for record in trades)
        closed = builder.close_through("2024-01-01T10:10:00Z")

        self.assertTrue(all(update.state is CandleRuntimeState.PARTIAL for update in partials))
        self.assertEqual(historical, closed_candle_records(closed))
        self.assertEqual(["2024-01-01T10:00:00Z", "2024-01-01T10:05:00Z"], [r.bucket_start for r in historical])

    def test_partial_updates_are_distinguishable_and_excluded_from_closed_only_consumers(self):
        builder = IncrementalCandleBuilder(self.definition)

        partial = builder.consume(trade("2024-01-01T10:00:00Z", "10.00", "1.0", "1"))

        self.assertEqual(CandleRuntimeState.PARTIAL, partial.state)
        self.assertEqual((), closed_candle_records((partial,)))

        [closed] = builder.close_through("2024-01-01T10:05:00Z")
        self.assertEqual(CandleRuntimeState.CLOSED, closed.state)
        self.assertEqual((closed.record,), closed_candle_records((partial, closed)))

    def test_partial_state_never_looks_ahead_to_later_trades(self):
        builder = IncrementalCandleBuilder(self.definition)

        first = builder.consume(trade("2024-01-01T10:00:00Z", "10.00", "1.0", "1"))
        second = builder.consume(trade("2024-01-01T10:01:00Z", "12.00", "2.0", "2"))

        self.assertEqual(
            {
                "bucket_start": "2024-01-01T10:00:00Z",
                "bucket_end": "2024-01-01T10:05:00Z",
                "open": "10",
                "high": "10",
                "low": "10",
                "close": "10",
                "volume": "1",
                "trade_count": "1",
            },
            first.record.stable_dict(),
        )
        self.assertEqual("12", second.record.high)
        self.assertEqual("3", second.record.volume)
        self.assertEqual("2", second.record.trade_count)

    def test_future_bucket_trade_does_not_close_previous_bucket_without_watermark(self):
        builder = IncrementalCandleBuilder(self.definition)

        first = builder.consume(trade("2024-01-01T10:00:00Z", "10", "1", "1"))
        second = builder.consume(trade("2024-01-01T10:05:00Z", "20", "2", "2"))

        self.assertEqual((), closed_candle_records((first, second)))
        [closed_first] = builder.close_through("2024-01-01T10:05:00Z")
        self.assertEqual("2024-01-01T10:00:00Z", closed_first.record.bucket_start)
        self.assertEqual("10", closed_first.record.close)

    def test_late_trade_after_source_finalization_is_rejected(self):
        builder = IncrementalCandleBuilder(self.definition)

        builder.close_through("2024-01-01T10:05:00Z")

        with self.assertRaises(CandleFinalizationError):
            builder.consume(trade("2024-01-01T10:04:59Z", "10", "1", "late"))

    def test_out_of_order_trade_sequence_is_rejected(self):
        builder = IncrementalCandleBuilder(self.definition)

        builder.consume(trade("2024-01-01T10:01:00Z", "10", "1", "2"))

        with self.assertRaises(CandleOrderingError):
            builder.consume(trade("2024-01-01T10:00:59Z", "9", "1", "1"))

    def test_observed_available_at_cannot_precede_closed_candle_causal_floor(self):
        builder = IncrementalCandleBuilder(self.definition)
        builder.consume(trade("2024-01-01T10:00:00Z", "10", "1", "1"))

        with self.assertRaises(CandleFinalizationError):
            builder.close_through(
                "2024-01-01T10:05:00Z",
                observed_available_at="2024-01-01T10:04:59Z",
            )

        [closed] = builder.close_through(
            "2024-01-01T10:05:00Z",
            observed_available_at="2024-01-01T10:05:00Z",
        )
        self.assertEqual(CandleRuntimeState.CLOSED, closed.state)


if __name__ == "__main__":
    unittest.main()
