"""Hermetic tests for aggregated L2 book normalization v1."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import (  # noqa: E402
    Instant,
    InvalidRequest,
    L2BookEvent,
    L2LevelChange,
)
from quant_platform.source_adapters.l2 import (  # noqa: E402
    L2SourceError,
    normalize_binance_l2_diff,
    normalize_binance_l2_snapshot,
    normalize_bybit_l2_message,
    normalize_coinbase_level2_message,
    normalize_kraken_l2_message,
    normalize_okx_l2_message,
)


class L2BookNormalizationV1Tests(unittest.TestCase):
    def test_bybit_historical_and_live_share_same_logical_event_semantics(self):
        message = {
            "topic": "orderbook.500.BTCUSDT",
            "type": "delta",
            "ts": 1705276800123,
            "data": {
                "s": "BTCUSDT",
                "u": 123456,
                "seq": 78910,
                "b": [["41731.10", "0.400"], ["41730.00", "0"]],
                "a": [["41731.20", "0.250"]],
            },
        }

        historical = normalize_bybit_l2_message(message, acquisition_mode="historical_archive")
        live = normalize_bybit_l2_message(message, acquisition_mode="live_websocket")

        self.assertEqual(historical.venue, "bybit")
        self.assertEqual(historical.event_type, "delta")
        self.assertEqual(historical.source_depth_limit, 500)
        self.assertEqual(historical.exchange_ts, Instant(1705276800123 * 1_000_000))
        self.assertEqual(historical.native_sequence, "78910")
        self.assertEqual(historical.native_update_id, "123456")
        self.assertEqual(historical.bids[0], L2LevelChange("bid", "41731.10", "0.400", "upsert"))
        self.assertEqual(historical.bids[1], L2LevelChange("bid", "41730.00", "0", "delete"))
        self.assertEqual(historical.asks[0], L2LevelChange("ask", "41731.20", "0.250", "upsert"))
        self.assertEqual(live.stable_dict() | {"acquisition_mode": "historical_archive"}, historical.stable_dict())

    def test_okx_preserves_prev_sequence_and_aggregate_order_count(self):
        event = normalize_okx_l2_message(
            {
                "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
                "action": "update",
                "data": [
                    {
                        "ts": "1705276800456",
                        "seqId": "101",
                        "prevSeqId": "100",
                        "bids": [["41731.10", "2.5", "0", "7"]],
                        "asks": [["41731.20", "0", "0", "0"]],
                    }
                ],
            },
            acquisition_mode="live_websocket",
        )

        self.assertEqual(event.venue, "okx")
        self.assertEqual(event.event_type, "delta")
        self.assertEqual(event.native_sequence, "101")
        self.assertEqual(event.native_prev_sequence, "100")
        self.assertEqual(event.continuity_token, "seq:101|prevSeq:100")
        self.assertEqual(event.bids[0].order_count, "7")
        self.assertEqual(event.asks[0].action, "delete")

    def test_binance_snapshot_and_diff_keep_rest_and_websocket_boundaries_explicit(self):
        snapshot = normalize_binance_l2_snapshot(
            {
                "lastUpdateId": 42,
                "bids": [["41731.10", "1.0"]],
                "asks": [["41731.20", "2.0"]],
            },
            exchange_ts="2024-01-15T00:00:00Z",
            instrument="BTCUSDT",
        )
        diff = normalize_binance_l2_diff(
            {
                "e": "depthUpdate",
                "E": 1705276800001,
                "T": 1705276800000,
                "s": "BTCUSDT",
                "U": 43,
                "u": 44,
                "pu": 42,
                "b": [["41731.10", "0"]],
                "a": [["41731.20", "2.1"]],
            }
        )

        self.assertEqual(snapshot.acquisition_mode, "rest_snapshot")
        self.assertEqual(snapshot.event_type, "snapshot")
        self.assertEqual(snapshot.native_update_id, "42")
        self.assertEqual(diff.acquisition_mode, "live_websocket")
        self.assertEqual(diff.event_type, "delta")
        self.assertEqual(diff.native_prev_sequence, "42")
        self.assertEqual(diff.continuity_token, "U:43|u:44|pu:42")

    def test_kraken_book_is_l2_even_when_checksum_is_present(self):
        event = normalize_kraken_l2_message(
            {
                "channel": "book",
                "type": "snapshot",
                "data": [
                    {
                        "symbol": "BTC/USD",
                        "timestamp": "2024-01-15T00:00:00.123Z",
                        "checksum": 123456789,
                        "bids": [{"price": "41731.10", "qty": "1.0"}],
                        "asks": [{"price": "41731.20", "qty": "0"}],
                    }
                ],
            }
        )

        self.assertEqual(event.venue, "kraken")
        self.assertEqual(event.event_type, "snapshot")
        self.assertEqual(event.native_sequence, "123456789")
        self.assertEqual(event.asks[0].action, "delete")

    def test_coinbase_level2_accepts_l2_and_rejects_full_channel_order_events(self):
        snapshot = normalize_coinbase_level2_message(
            {
                "type": "snapshot",
                "product_id": "BTC-USD",
                "bids": [["41731.10", "1.0"]],
                "asks": [["41731.20", "1.1"]],
            },
            exchange_ts="2024-01-15T00:00:00Z",
        )
        update = normalize_coinbase_level2_message(
            {
                "type": "l2update",
                "product_id": "BTC-USD",
                "time": "2024-01-15T00:00:01Z",
                "changes": [["buy", "41731.10", "0"], ["sell", "41731.20", "2.0"]],
            }
        )

        self.assertEqual(snapshot.event_type, "snapshot")
        self.assertEqual(update.bids[0].side, "bid")
        self.assertEqual(update.bids[0].action, "delete")
        self.assertEqual(update.asks[0].side, "ask")
        with self.assertRaises(L2SourceError):
            normalize_coinbase_level2_message(
                {
                    "type": "received",
                    "product_id": "BTC-USD",
                    "order_id": "order-1",
                    "time": "2024-01-15T00:00:01Z",
                }
            )

    def test_l2_record_refuses_mbo_shape_and_invalid_delete_semantics(self):
        with self.assertRaises(InvalidRequest):
            L2LevelChange("bid", "41731.10", "0.1", "delete")
        with self.assertRaises(InvalidRequest):
            L2BookEvent(
                venue="bybit",
                market_type="linear_perp",
                instrument="BTCUSDT",
                native_symbol="BTCUSDT",
                source_channel="orderbook.500.BTCUSDT",
                acquisition_mode="live_websocket",
                event_type="delta",
                exchange_ts=Instant(1),
                bids=(L2LevelChange("ask", "41731.10", "1", "upsert"),),
                asks=(),
            )

    def test_event_identity_is_deterministic_and_sensitive_to_acquisition_mode(self):
        event = normalize_bybit_l2_message(
            {
                "topic": "orderbook.200.BTCUSDT",
                "type": "snapshot",
                "ts": 1705276800000,
                "data": {
                    "s": "BTCUSDT",
                    "u": 1,
                    "seq": 1,
                    "b": [["41731.10", "1"]],
                    "a": [["41731.20", "1"]],
                },
            },
            acquisition_mode="historical_archive",
        )
        same = normalize_bybit_l2_message(
            {
                "topic": "orderbook.200.BTCUSDT",
                "type": "snapshot",
                "ts": 1705276800000,
                "data": {
                    "s": "BTCUSDT",
                    "u": 1,
                    "seq": 1,
                    "b": [["41731.10", "1"]],
                    "a": [["41731.20", "1"]],
                },
            },
            acquisition_mode="historical_archive",
        )
        live = normalize_bybit_l2_message(
            {
                "topic": "orderbook.200.BTCUSDT",
                "type": "snapshot",
                "ts": 1705276800000,
                "data": {
                    "s": "BTCUSDT",
                    "u": 1,
                    "seq": 1,
                    "b": [["41731.10", "1"]],
                    "a": [["41731.20", "1"]],
                },
            },
            acquisition_mode="live_websocket",
        )

        self.assertEqual(event.identity, same.identity)
        self.assertNotEqual(event.identity, live.identity)


if __name__ == "__main__":
    unittest.main()
