"""Hermetic proof for A11 Bybit BTCUSDT live trade acquisition semantics."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    BYBIT_LIVE_MAPPING_V1,
    BYBIT_LIVE_SOURCE_SEMANTICS_V1,
    BybitLiveIntegrityError,
    BybitLiveSourceError,
    LiveSessionTracker,
    ReconnectStatus,
    SessionState,
    TradeKeyV1,
    build_bybit_live_coverage_document,
    bybit_live_dataset_identity,
    canonicalize_bybit_live_message,
    canonicalize_bybit_recent_public_trade,
    converge_historical_live_records,
    deduplicate_live_records,
    reconcile_after_disconnect,
    trade_key_v1,
)


def live_row(**overrides):
    values = {
        "T": 1705276800490,
        "s": "BTCUSDT",
        "S": "Buy",
        "v": "0.00400",
        "p": "41731.10",
        "i": "tid-1",
        "seq": 1783284617,
    }
    values.update(overrides)
    return values


def live_message(*rows, **overrides):
    values = {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": 1705276800495,
        "data": list(rows or [live_row()]),
    }
    values.update(overrides)
    return values


def rest_row(**overrides):
    values = {
        "execId": "tid-1",
        "symbol": "BTCUSDT",
        "price": "41731.10",
        "size": "0.00400",
        "side": "Buy",
        "time": "1705276800490",
        "seq": "1783284617",
    }
    values.update(overrides)
    return values


class BybitLiveSourceV1Tests(unittest.TestCase):
    def test_valid_live_message_maps_exact_canonical_trade_record(self):
        batch = canonicalize_bybit_live_message(live_message())
        self.assertEqual(batch.message_evidence.source_semantics_id, BYBIT_LIVE_SOURCE_SEMANTICS_V1)
        self.assertEqual(batch.message_evidence.mapping_id, BYBIT_LIVE_MAPPING_V1)
        self.assertEqual(batch.message_evidence.provider_message_ts_ms, 1705276800495)
        self.assertEqual(batch.message_evidence.trade_count, 1)

        record = batch.records[0]
        self.assertIsInstance(record, TradeRecord)
        self.assertEqual(record.venue, "bybit")
        self.assertEqual(record.instrument, "BTCUSDT")
        self.assertEqual(record.exchange_ts, Instant(1705276800490 * 1_000_000))
        self.assertEqual(record.price, "41731.10")
        self.assertEqual(record.size, "0.00400")
        self.assertEqual(record.aggressor_side, "buy")
        self.assertEqual(record.trade_id, "tid-1")
        self.assertEqual(record.sequence, "1783284617")
        self.assertIsNone(record.receive_ts)

    def test_provider_message_ts_is_evidence_not_receive_ts(self):
        batch = canonicalize_bybit_live_message(live_message(ts=1705276800999))
        self.assertEqual(batch.message_evidence.provider_message_ts_ms, 1705276800999)
        self.assertIsNone(batch.records[0].receive_ts)

    def test_malformed_or_wrong_source_fields_fail_closed(self):
        cases = (
            ("topic", live_message(topic="publicTrade.ETHUSDT")),
            ("type", live_message(type="delta")),
            ("ts", live_message(ts="1705276800495")),
            ("data", live_message(data={})),
            ("s", live_message(live_row(s="ETHUSDT"))),
            ("S", live_message(live_row(S="BUY"))),
            ("p", live_message(live_row(p="0"))),
            ("v", live_message(live_row(v="1e-3"))),
            ("i", live_message(live_row(i=""))),
            ("T", live_message(live_row(T=True))),
            ("seq", live_message(live_row(seq="1783284617"))),
        )
        for field, message in cases:
            with self.subTest(field=field):
                with self.assertRaises(BybitLiveSourceError) as caught:
                    canonicalize_bybit_live_message(message)
                self.assertEqual(caught.exception.field, field)

    def test_multiple_messages_sharing_seq_do_not_collapse_distinct_trade_keys(self):
        batch = canonicalize_bybit_live_message(live_message(
            live_row(i="a", T=1705276800490, seq=42),
            live_row(i="b", T=1705276800490, seq=42),
        ))
        deduped = deduplicate_live_records(batch.records)
        self.assertEqual([record.trade_id for record in deduped], ["a", "b"])
        self.assertEqual([record.sequence for record in deduped], ["42", "42"])

    def test_duplicate_same_key_same_payload_is_idempotent(self):
        first = canonicalize_bybit_live_message(live_message(live_row(i="same"))).records[0]
        second = canonicalize_bybit_live_message(live_message(live_row(i="same"))).records[0]
        self.assertEqual(deduplicate_live_records([first, second]), (first,))

    def test_duplicate_same_key_conflicting_payload_fails_closed(self):
        first = canonicalize_bybit_live_message(live_message(live_row(i="same"))).records[0]
        changed_price = replace(first, price="41731.11")
        with self.assertRaises(BybitLiveIntegrityError):
            deduplicate_live_records([first, changed_price])
        changed_sequence = replace(first, sequence="1783284618")
        with self.assertRaises(BybitLiveIntegrityError):
            deduplicate_live_records([first, changed_sequence])

    def test_historical_live_cutover_is_one_deterministic_history(self):
        historical = (
            TradeRecord("bybit", "BTCUSDT", Instant(1), "100", "1", "buy", trade_id="a"),
            TradeRecord("bybit", "BTCUSDT", Instant(2), "101", "1", "sell", trade_id="b"),
        )
        # A real live overlap record always carries a populated `sequence`
        # (canonicalize_bybit_live_trade/canonicalize_bybit_recent_public_trade
        # both require it); the historical side is always `sequence=None`
        # (bybit_historical.py never provides execution sequence). Asserting
        # `sequence=None` on the live side here as well would silently mask
        # the asymmetry this test exists to cover.
        live_overlap = replace(historical[1], sequence="1783284617")
        live_after = TradeRecord("bybit", "BTCUSDT", Instant(3), "102", "1", "buy", trade_id="c", sequence="9")
        result = converge_historical_live_records(
            historical,
            (live_overlap, live_after),
            cutover_key=trade_key_v1(historical[-1]),
        )
        self.assertEqual([record.trade_id for record in result], ["a", "b", "c"])
        # The accepted overlap record is the historical one (sequence=None);
        # the live record's sequence is evidence, not a conflict, and is not
        # required to survive into the merged canonical history.
        self.assertIsNone(result[1].sequence)

        conflicting_overlap = replace(live_overlap, size="2")
        with self.assertRaises(BybitLiveIntegrityError):
            converge_historical_live_records(
                historical,
                (conflicting_overlap,),
                cutover_key=trade_key_v1(historical[-1]),
            )

    def test_session_disconnect_makes_coverage_non_complete(self):
        tracker = LiveSessionTracker()
        tracker.connected(conn_id="c1")
        tracker.subscribed(topic="publicTrade.BTCUSDT", conn_id="c1")
        tracker.disconnected("socket closed")
        evidence = tracker.evidence()
        self.assertEqual(evidence.final_state, SessionState.DISCONNECTED)
        self.assertFalse(evidence.can_assert_complete_interval)
        document = build_bybit_live_coverage_document(
            dataset_identity=bybit_live_dataset_identity(),
            coverage_id="coverage-live-1",
            intent_start="2024-01-15T00:00:00Z",
            intent_end="2024-01-15T00:01:00Z",
            assertion_id="assertion-1",
            assertion_start="2024-01-15T00:00:00Z",
            assertion_end="2024-01-15T00:01:00Z",
            partition_key="dt=2024-01-15",
            revision=1,
            session_evidence=evidence,
            created_at="2024-01-15T00:01:01Z",
            producer="test",
            code_ref="test",
        )
        self.assertEqual(document["assertions"][0]["status"], "known_gap")
        self.assertEqual(document["assertions"][0]["evidence"][1]["kind"], "transport_interruption")

    def test_zero_trade_message_alone_is_not_absence_heuristic(self):
        tracker = LiveSessionTracker()
        tracker.connected(conn_id="c1")
        tracker.subscribed(topic="publicTrade.BTCUSDT", conn_id="c1")
        batch = canonicalize_bybit_live_message(live_message(data=[]))
        tracker.observed_message(batch)
        evidence = tracker.evidence()
        self.assertTrue(evidence.can_assert_complete_interval)
        self.assertEqual(evidence.accepted_records, 0)
        document = build_bybit_live_coverage_document(
            dataset_identity=bybit_live_dataset_identity(),
            coverage_id="coverage-live-empty",
            intent_start="2024-01-15T00:00:00Z",
            intent_end="2024-01-15T00:00:01Z",
            assertion_id="assertion-empty",
            assertion_start="2024-01-15T00:00:00Z",
            assertion_end="2024-01-15T00:00:01Z",
            partition_key="dt=2024-01-15",
            revision=1,
            session_evidence=evidence,
            created_at="2024-01-15T00:00:02Z",
            producer="test",
            code_ref="test",
        )
        self.assertEqual(document["assertions"][0]["status"], "complete")
        self.assertEqual(document["assertions"][0]["evidence"][0]["accepted_records"], 0)
        self.assertIn("ACQUIRING", document["assertions"][0]["evidence"][0]["states"])

    def test_recent_public_trade_mapping_matches_live_identity_and_order(self):
        record = canonicalize_bybit_recent_public_trade(rest_row())
        live = canonicalize_bybit_live_message(live_message()).records[0]
        self.assertEqual(trade_key_v1(record), trade_key_v1(live))
        self.assertEqual(record.sequence, "1783284617")
        self.assertIsNone(record.receive_ts)

    def test_bounded_reconnect_restores_continuity_only_when_anchor_is_present(self):
        durable = TradeKeyV1("bybit", "BTCUSDT", Instant(1000), "anchor")
        anchor = TradeRecord("bybit", "BTCUSDT", Instant(1000), "100", "1", "buy", trade_id="anchor", sequence="10")
        rest_after = TradeRecord("bybit", "BTCUSDT", Instant(1001), "101", "1", "buy", trade_id="after", sequence="11")
        ws_duplicate = replace(rest_after)
        result = reconcile_after_disconnect(
            last_durable_key=durable,
            recent_rest_records=(rest_after, anchor),
            buffered_ws_records=(ws_duplicate,),
        )
        self.assertEqual(result.status, ReconnectStatus.CONTINUITY_RESTORED)
        self.assertEqual([record.trade_id for record in result.accepted_records], ["after"])

        missing = reconcile_after_disconnect(
            last_durable_key=durable,
            recent_rest_records=(rest_after,),
            buffered_ws_records=(),
        )
        self.assertEqual(missing.status, ReconnectStatus.UNRESOLVED_GAP)
        self.assertEqual(missing.accepted_records, ())
        self.assertEqual(missing.evidence["coverage_status"], "non_complete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
