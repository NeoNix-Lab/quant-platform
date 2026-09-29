#!/usr/bin/env python3
"""B06 -> D04 gap-safe live candle composition tests (issue #206)."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access import (  # noqa: E402
    LiveGapEvent,
    LiveGapStatus,
    LiveStreamCursorV1,
    LiveStreamRequest,
    LiveTradeEvent,
)
from quant_platform.application import compose_live_candle_stream  # noqa: E402
from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    CandleDefinitionV1,
    CandleRuntimeState,
    aggregate_historical_candles,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")


class FakeGateway:
    def __init__(self, events):
        self.events = tuple(events)
        self.calls = []

    def live_stream(self, request, *, cursor=None, batch_size=65_536):
        self.calls.append((request, cursor, batch_size))
        return iter(self.events)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def live_request() -> LiveStreamRequest:
    return LiveStreamRequest(IDENTITY, ordering_policy=TRADES_CANONICAL_TOTAL_ORDER_V1)


def cursor(ts: str, trade_id: str, *, segment: str = "segment-a") -> LiveStreamCursorV1:
    return LiveStreamCursorV1(
        schema_version="live-stream-cursor-v1",
        dataset_identity=IDENTITY,
        ordering_policy=TRADES_CANONICAL_TOTAL_ORDER_V1,
        coverage_segment_id=segment,
        last_canonical_exchange_ts=instant(ts),
        last_canonical_trade_id=trade_id,
    )


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


def trade_event(ts: str, price: str, size: str, trade_id: str, *, segment: str = "segment-a"):
    record = trade(ts, price, size, trade_id)
    return LiveTradeEvent(record=record, cursor=cursor(ts, trade_id, segment=segment))


class LiveCandleStreamCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = CandleDefinitionV1.from_duration("5m")

    def test_gap_free_stream_closed_records_match_historical_d03(self):
        source_events = (
            trade_event("2024-01-01T10:00:00Z", "100.5000", "0.100", "1"),
            trade_event("2024-01-01T10:04:59Z", "101.000", "1.000", "2"),
            trade_event("2024-01-01T10:05:00Z", "99.25", "0.0200", "3"),
            trade_event("2024-01-01T10:09:59Z", "102", "2.5", "4"),
            trade_event("2024-01-01T10:10:00Z", "103", "1", "5"),
        )
        gateway = FakeGateway(source_events)

        report = compose_live_candle_stream(gateway, live_request(), self.definition)

        historical = aggregate_historical_candles(
            tuple(event.record for event in source_events[:4]),
            self.definition,
            "2024-01-01T10:00:00Z",
            "2024-01-01T10:10:00Z",
        )
        self.assertEqual(historical, report.closed_records)
        self.assertEqual("5", report.final_cursor.last_canonical_trade_id)
        self.assertEqual((live_request(), None, 65_536), gateway.calls[0])

    def test_internal_gap_bucket_is_not_sealed_as_closed(self):
        first = trade_event("2024-01-01T10:00:00Z", "100", "1", "1")
        second = trade_event("2024-01-01T10:01:00Z", "101", "1", "2")
        open_gap = LiveGapEvent(
            schema_version="live-gap-event-v1",
            gap_id="gap-in-10-00-bucket",
            status=LiveGapStatus.OPEN,
            previous_cursor=second.cursor,
            lower_bound=instant("2024-01-01T10:02:00Z"),
            upper_bound=None,
            resumed_cursor=None,
            reason="fixture internal gap",
        )
        resumed = trade_event(
            "2024-01-01T10:04:00Z",
            "102",
            "1",
            "3",
            segment="segment-b",
        )
        closed_gap = LiveGapEvent(
            schema_version="live-gap-event-v1",
            gap_id=open_gap.gap_id,
            status=LiveGapStatus.CLOSED,
            previous_cursor=second.cursor,
            lower_bound=open_gap.lower_bound,
            upper_bound=resumed.cursor.last_canonical_exchange_ts,
            resumed_cursor=resumed.cursor,
            reason="fixture resumed",
        )
        source_events = (
            first,
            second,
            open_gap,
            closed_gap,
            resumed,
            trade_event("2024-01-01T10:06:00Z", "103", "1", "4", segment="segment-b"),
            trade_event("2024-01-01T10:10:00Z", "104", "1", "5", segment="segment-b"),
        )
        gateway = FakeGateway(source_events)

        report = compose_live_candle_stream(gateway, live_request(), self.definition)

        closed_starts = [record.bucket_start for record in report.closed_records]
        self.assertNotIn("2024-01-01T10:00:00Z", closed_starts)
        self.assertEqual(["2024-01-01T10:05:00Z"], closed_starts)
        self.assertEqual(2, len(report.gap_events))
        self.assertTrue(
            all(
                update.state is not CandleRuntimeState.CLOSED
                or update.record.bucket_start != "2024-01-01T10:00:00Z"
                for update in report.updates
            )
        )


if __name__ == "__main__":
    unittest.main()
