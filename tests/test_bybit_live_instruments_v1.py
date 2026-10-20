"""DG-N T02 part 1: the live source adapter takes the instrument as an explicit argument (ADR-0074 sections 2 and 3).

Every function accepts exactly the instruments of ``BYBIT_LINEAR_TRADE_INSTRUMENTS_V1``, refuses a topic or a symbol of another
instrument, and gives with no argument the BTCUSDT result it gave before (values pinned from ``implement/fid`` before the change).
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1  # noqa: E402
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    SUPPORTED_SYMBOL,
    SUPPORTED_TOPIC,
    BybitLiveSourceError,
    LiveSessionTracker,
    SessionState,
    bybit_live_dataset_identity,
    bybit_live_topic,
    canonicalize_bybit_live_message,
    canonicalize_bybit_live_trade,
    canonicalize_bybit_recent_public_trade,
)

NOT_BTC = tuple(item for item in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 if item != "BTCUSDT")
REFUSED = ("BTCUSD", "BTCUSDC", "ADAUSDT", "btcusdt", "BTCUSDT ", "", None, 7)

# Computed on implement/fid (c6ea1dc) before this change, from BTC_MESSAGE below.
BTC_MESSAGE_FINGERPRINT = "eb0b2f294aac45bc950ba2efb3275aee2194c1b82d2a8db0e529f20ce9aa878c"
BTC_RECORDS = [
    ("BTCUSDT", 1705276800492000000, "41731.10", "0.004", "buy", "a-1", "123"),
    ("BTCUSDT", 1705276800726000000, "41731.00", "0.012", "sell", "a-2", "124"),
]
BTC_RECENT = ("BTCUSDT", 1705276800492000000, "41731.10", "0.004", "buy", "e-1", "123")


def message(instrument: str = "BTCUSDT", *, symbol: str | None = None) -> dict:
    return {
        "topic": f"publicTrade.{instrument}", "type": "snapshot", "ts": 1705276800500,
        "data": [
            {"i": "a-1", "s": symbol or instrument, "T": 1705276800492, "S": "Buy", "p": "41731.10", "v": "0.004", "seq": 123,
             "L": "PlusTick", "BT": False},
            {"i": "a-2", "s": symbol or instrument, "T": 1705276800726, "S": "Sell", "p": "41731.00", "v": "0.012", "seq": 124},
        ],
    }


def recent(instrument: str = "BTCUSDT") -> dict:
    return {"execId": "e-1", "symbol": instrument, "time": "1705276800492", "side": "Buy", "price": "41731.10",
            "size": "0.004", "seq": "123"}


def shape(record: TradeRecord) -> tuple:
    return (record.instrument, record.exchange_ts.epoch_ns, record.price, record.size, record.aggressor_side, record.trade_id,
            record.sequence)


class TopicAndIdentityTests(unittest.TestCase):
    def test_topic_and_identity_follow_the_allow_list(self) -> None:
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            with self.subTest(instrument=instrument):
                self.assertEqual(f"publicTrade.{instrument}", bybit_live_topic(instrument))
                self.assertEqual(
                    DatasetIdentity("canonical", "trades", "bybit", instrument, "trade-v1"),
                    bybit_live_dataset_identity(instrument),
                )

    def test_the_defaults_are_btcusdt_byte_for_byte(self) -> None:
        self.assertEqual("BTCUSDT", SUPPORTED_SYMBOL)
        self.assertEqual("publicTrade.BTCUSDT", SUPPORTED_TOPIC)
        self.assertEqual(SUPPORTED_TOPIC, bybit_live_topic())
        self.assertEqual(
            {"layer": "canonical", "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
             "record_schema_id": "trade-v1"},
            bybit_live_dataset_identity().stable_dict(),
        )

    def test_anything_outside_the_allow_list_is_refused(self) -> None:
        for instrument in REFUSED:
            with self.subTest(instrument=instrument):
                for call in (bybit_live_topic, bybit_live_dataset_identity, LiveSessionTracker):
                    with self.assertRaises(BybitLiveSourceError) as refused:
                        call(instrument)
                    self.assertEqual("instrument", refused.exception.field)
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_live_message(message(), instrument=instrument)
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_live_trade(message()["data"][0], instrument=instrument)
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_recent_public_trade(recent(), instrument=instrument)


class CanonicalizationTests(unittest.TestCase):
    def test_btcusdt_results_are_unchanged(self) -> None:
        batch = canonicalize_bybit_live_message(message())
        self.assertEqual(BTC_MESSAGE_FINGERPRINT, batch.message_evidence.message_fingerprint_sha256)
        self.assertEqual("publicTrade.BTCUSDT", batch.message_evidence.topic)
        self.assertEqual(BTC_RECORDS, [shape(record) for record in batch.records])
        self.assertEqual(BTC_RECENT, shape(canonicalize_bybit_recent_public_trade(recent())))
        explicit = canonicalize_bybit_live_message(message(), instrument="BTCUSDT")
        self.assertEqual(batch, explicit)

    def test_every_instrument_canonicalizes_its_own_messages_and_rows(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                batch = canonicalize_bybit_live_message(message(instrument), instrument=instrument)
                self.assertEqual(f"publicTrade.{instrument}", batch.message_evidence.topic)
                self.assertEqual([(instrument, *record[1:]) for record in BTC_RECORDS], [shape(r) for r in batch.records])
                self.assertEqual({"bybit"}, {r.venue for r in batch.records})
                self.assertEqual((instrument, *BTC_RECENT[1:]), shape(
                    canonicalize_bybit_recent_public_trade(recent(instrument), instrument=instrument)))
                self.assertEqual(instrument, canonicalize_bybit_live_trade(
                    message(instrument)["data"][0], instrument=instrument).instrument)

    def test_a_message_or_row_of_another_instrument_is_refused(self) -> None:
        for wanted in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            for actual in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
                if wanted == actual:
                    continue
                with self.subTest(wanted=wanted, actual=actual):
                    with self.assertRaisesRegex(BybitLiveSourceError, "unsupported live topic"):
                        canonicalize_bybit_live_message(message(actual), instrument=wanted)
                    with self.assertRaisesRegex(BybitLiveSourceError, "unsupported symbol"):
                        canonicalize_bybit_live_trade(message(actual)["data"][0], instrument=wanted)
                    with self.assertRaisesRegex(BybitLiveSourceError, "unsupported symbol"):
                        canonicalize_bybit_recent_public_trade(recent(actual), instrument=wanted)
                    # the right topic with a row of another symbol inside it
                    with self.assertRaisesRegex(BybitLiveSourceError, "unsupported symbol"):
                        canonicalize_bybit_live_message(message(wanted, symbol=actual), instrument=wanted)

    def test_the_default_still_refuses_every_other_instrument(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_live_message(message(instrument))
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_live_trade(message(instrument)["data"][0])
                with self.assertRaises(BybitLiveSourceError):
                    canonicalize_bybit_recent_public_trade(recent(instrument))


class TrackerTests(unittest.TestCase):
    def test_a_tracker_follows_one_instrument(self) -> None:
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            with self.subTest(instrument=instrument):
                tracker = LiveSessionTracker(instrument)
                tracker.connected(conn_id="c")
                tracker.subscribed(conn_id="c")  # the default is this session's own topic
                self.assertEqual(SessionState.SUBSCRIBED, tracker.state)
                tracker.observed_message(canonicalize_bybit_live_message(message(instrument), instrument=instrument))
                self.assertEqual(SessionState.ACQUIRING, tracker.state)
                evidence = tracker.evidence()
                self.assertEqual((1, 2), (evidence.validated_messages, evidence.accepted_records))

    def test_the_implicit_subscription_works_for_every_instrument(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument):
                tracker = LiveSessionTracker(instrument)
                tracker.connected()
                tracker.observed_message(canonicalize_bybit_live_message(message(instrument), instrument=instrument))
                tracker.subscribed(topic=f"publicTrade.{instrument}")  # a late ack of the same topic is confirmation
                self.assertEqual(SessionState.ACQUIRING, tracker.state)

    def test_an_acknowledgement_for_another_topic_is_refused(self) -> None:
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            other = next(item for item in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 if item != instrument)
            with self.subTest(instrument=instrument):
                tracker = LiveSessionTracker(instrument)
                tracker.connected()
                with self.assertRaisesRegex(BybitLiveSourceError, "unsupported topic"):
                    tracker.subscribed(topic=f"publicTrade.{other}")

    def test_a_batch_of_another_instrument_is_never_counted(self) -> None:
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            other = next(item for item in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 if item != instrument)
            with self.subTest(instrument=instrument):
                tracker = LiveSessionTracker(instrument)
                tracker.connected()
                tracker.subscribed()
                with self.assertRaisesRegex(BybitLiveSourceError, "another topic"):
                    tracker.observed_message(canonicalize_bybit_live_message(message(other), instrument=other))
                self.assertEqual(0, tracker.evidence().validated_messages)


if __name__ == "__main__":
    unittest.main()
