"""Hermetic tests for Bybit live L2 order book acquisition semantics (A14f v2).

Tests:
- Normalization of Bybit L2 live WebSocket messages.
- Rejection of messages missing engine time (cts).
- In-band resync state machine:
  - Startup unanchored delta rejection.
  - Snapshot anchoring.
  - Delta continuity (u + 1).
  - Transport disconnect and heartbeat timeout handling.
  - Update discontinuity detection, re-subscribe signaling, and gap assertions.
  - Never fabricates synthetic snapshots.
- Historical/live cutover convergence and conflict resolution.
- Exact topic, control frames and snapshot handling in a running stream.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BybitL2SequenceConflict,
)
from quant_platform.source_adapters.bybit_l2_live import (  # noqa: E402
    BybitL2LiveIntegrityError,
    BybitL2LiveResyncStateMachine,
    BybitL2LiveSourceError,
    L2GapReason,
    L2ResyncState,
    is_bybit_l2_data_frame,
    canonicalize_bybit_l2_live_message,
    converge_historical_live_l2,
)


def sample_message(*, msg_type: str = "delta", u: int = 1001, seq: int = 5001, cts: int | None = 1705276800100, topic: str = "orderbook.200.BTCUSDT"):
    msg = {
        "topic": topic,
        "type": msg_type,
        "ts": 1705276800105,
        "data": {
            "s": "BTCUSDT",
            "b": [["50000.00", "1.5"]],
            "a": [["50001.00", "2.0"]],
            "u": u,
            "seq": seq,
        },
    }
    if cts is not None:
        msg["cts"] = cts
    return msg


def millis(ms: int) -> Instant:
    return Instant(ms * 1_000_000)


class BybitL2LiveSourceTests(unittest.TestCase):
    def test_canonicalize_valid_live_message(self):
        msg = sample_message(msg_type="snapshot", u=1000, seq=5000, cts=1705276800000)
        event = canonicalize_bybit_l2_live_message(msg)

        self.assertEqual(event.venue, "bybit")
        self.assertEqual(event.instrument, "BTCUSDT")
        self.assertEqual(event.event_type, "snapshot")
        self.assertEqual(event.acquisition_mode, "live_websocket")
        self.assertEqual(event.native_sequence, "5000")
        self.assertEqual(event.native_update_id, "1000")
        self.assertEqual(event.exchange_ts, millis(1705276800000))
        self.assertEqual(event.provider_ts, millis(1705276800105))

    def test_canonicalize_missing_cts_fails_closed(self):
        msg = sample_message(cts=None)
        with self.assertRaises(BybitL2LiveSourceError) as ctx:
            canonicalize_bybit_l2_live_message(msg)
        self.assertEqual(ctx.exception.reason, "missing_engine_time")

    def test_canonicalize_invalid_topic_fails(self):
        msg = sample_message(topic="publicTrade.BTCUSDT")
        with self.assertRaises(BybitL2LiveSourceError) as ctx:
            canonicalize_bybit_l2_live_message(msg)
        self.assertEqual(ctx.exception.reason, "unsupported_topic")

    def test_resync_state_machine_initial_unanchored_deltas_dropped(self):
        sm = BybitL2LiveResyncStateMachine()
        self.assertEqual(sm.state, L2ResyncState.AWAITING_INITIAL_SNAPSHOT)

        # Delta arrives before snapshot: must be dropped
        delta_msg = sample_message(msg_type="delta", u=100, seq=200)
        accepted, closed_gap, resub = sm.process_message(delta_msg)
        self.assertIsNone(accepted)
        self.assertIsNone(closed_gap)
        self.assertFalse(resub)
        self.assertEqual(sm.state, L2ResyncState.AWAITING_INITIAL_SNAPSHOT)

        # Authentic snapshot arrives: anchors stream
        snap_msg = sample_message(msg_type="snapshot", u=101, seq=201)
        accepted, closed_gap, resub = sm.process_message(snap_msg)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.event_type, "snapshot")
        self.assertEqual(sm.state, L2ResyncState.STREAMING)
        self.assertEqual(sm.last_sequence, 201)
        self.assertEqual(sm.last_update_id, 101)

    def test_resync_state_machine_continuous_deltas(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))

        # First delta: u = 101, seq = 202
        d1, _, _ = sm.process_message(sample_message(msg_type="delta", u=101, seq=202))
        self.assertIsNotNone(d1)
        self.assertEqual(sm.last_update_id, 101)
        self.assertEqual(sm.last_sequence, 202)

        # Second delta: u = 102, seq = 203
        d2, _, _ = sm.process_message(sample_message(msg_type="delta", u=102, seq=203))
        self.assertIsNotNone(d2)
        self.assertEqual(sm.last_update_id, 102)
        self.assertEqual(sm.last_sequence, 203)

    def test_resync_state_machine_update_discontinuity_triggers_resync(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))

        # Expected u=101, but venue message has u=105 (discontinuity!)
        d1, closed_gap, resub = sm.process_message(sample_message(msg_type="delta", u=105, seq=205))
        self.assertIsNone(d1)
        self.assertTrue(resub)
        self.assertEqual(sm.state, L2ResyncState.AWAITING_RESYNC_SNAPSHOT)
        self.assertIsNotNone(sm.active_gap)
        self.assertEqual(sm.active_gap.reason, L2GapReason.SEQUENCE_DISCONTINUITY.value)
        self.assertEqual(sm.active_gap.last_good_sequence, 200)
        self.assertEqual(sm.active_gap.last_good_update_id, 100)

        # Further deltas during unanchored resync state are dropped
        d2, _, resub2 = sm.process_message(sample_message(msg_type="delta", u=106, seq=206))
        self.assertIsNone(d2)
        self.assertFalse(resub2)

        # Fresh in-band snapshot arrives! Closes the gap
        snap_resync = sample_message(msg_type="snapshot", u=200, seq=350, cts=1705276800500)
        accepted, closed_gap, _ = sm.process_message(snap_resync)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.event_type, "snapshot")
        self.assertEqual(sm.state, L2ResyncState.STREAMING)
        self.assertIsNotNone(closed_gap)
        self.assertEqual(closed_gap.resync_sequence, 350)
        self.assertEqual(closed_gap.resync_update_id, 200)
        self.assertEqual(closed_gap.dropped_deltas_count, 2)

    def test_resync_state_machine_disconnect_and_reconnect(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200, cts=1705276800100))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201, cts=1705276800110))

        # Disconnect occurs
        gap = sm.on_disconnected(at=millis(1705276800120))
        self.assertEqual(sm.state, L2ResyncState.DISCONNECTED)
        self.assertIsNotNone(gap)
        self.assertEqual(gap.reason, L2GapReason.CONNECTION_LOSS.value)

        # Reconnect occurs
        sm.on_connected()
        self.assertEqual(sm.state, L2ResyncState.AWAITING_RESYNC_SNAPSHOT)

        # Snapshot closes gap
        snap = sample_message(msg_type="snapshot", u=300, seq=400, cts=1705276800200)
        accepted, closed_gap, _ = sm.process_message(snap)
        self.assertIsNotNone(accepted)
        self.assertIsNotNone(closed_gap)
        self.assertEqual(closed_gap.gap_start, millis(1705276800120))
        self.assertEqual(closed_gap.gap_end, millis(1705276800200))
        self.assertEqual(sm.state, L2ResyncState.STREAMING)

    def test_resync_state_machine_idempotent_duplicate_delta_ignored(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))
        d1, _, _ = sm.process_message(sample_message(msg_type="delta", u=101, seq=201))
        self.assertIsNotNone(d1)

        # Idempotent replay of same delta (same seq, same u)
        d_dup, _, resub = sm.process_message(sample_message(msg_type="delta", u=101, seq=201))
        self.assertIsNone(d_dup)
        self.assertFalse(resub)
        self.assertEqual(sm.state, L2ResyncState.STREAMING)

    def test_resync_state_machine_sequence_regression_fails_closed(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201))

        # Regressed sequence (seq=195 < 201)
        d_reg, _, resub = sm.process_message(sample_message(msg_type="delta", u=102, seq=195))
        self.assertIsNone(d_reg)
        self.assertTrue(resub)
        self.assertEqual(sm.state, L2ResyncState.AWAITING_RESYNC_SNAPSHOT)
        self.assertEqual(sm.active_gap.reason, L2GapReason.OUT_OF_ORDER_SEQUENCE.value)

    def test_converge_historical_live_cutover(self):
        h1 = canonicalize_bybit_l2_live_message(sample_message(msg_type="snapshot", u=100, seq=1000), acquisition_mode="historical_archive")
        h2 = canonicalize_bybit_l2_live_message(sample_message(msg_type="delta", u=101, seq=1001), acquisition_mode="historical_archive")
        l1 = canonicalize_bybit_l2_live_message(sample_message(msg_type="delta", u=101, seq=1001), acquisition_mode="live_websocket")
        l2 = canonicalize_bybit_l2_live_message(sample_message(msg_type="delta", u=102, seq=1002), acquisition_mode="live_websocket")

        # Overlapping seq=1001 with identical payload collapses idempotently
        converged = converge_historical_live_l2([h1, h2], [l1, l2], cut_sequence=1001)
        self.assertEqual([int(e.native_sequence) for e in converged], [1000, 1001, 1002])

    def test_converge_historical_live_conflicting_payload_fails_closed(self):
        h1 = canonicalize_bybit_l2_live_message(sample_message(msg_type="delta", u=100, seq=1000))
        # Different bid price for same seq=1000
        msg_diff = sample_message(msg_type="delta", u=100, seq=1000)
        msg_diff["data"]["b"] = [["49999.00", "5.0"]]
        l1 = canonicalize_bybit_l2_live_message(msg_diff)

        with self.assertRaises(BybitL2SequenceConflict):
            converge_historical_live_l2([h1], [l1])

    def test_other_instrument_or_depth_is_refused_not_normalized(self):
        for topic in ("orderbook.50.ETHUSDT", "orderbook.50.BTCUSDT", "orderbook.200.ETHUSDT"):
            msg = sample_message(topic=topic)
            with self.assertRaises(BybitL2LiveSourceError) as ctx:
                canonicalize_bybit_l2_live_message(msg)
            self.assertEqual(ctx.exception.reason, "unsupported_topic", topic)

    def test_control_frames_are_not_data_and_do_not_move_the_machine(self):
        sm = BybitL2LiveResyncStateMachine()
        for frame in ({"op": "pong", "success": True}, {"success": True, "op": "subscribe"}):
            self.assertFalse(is_bybit_l2_data_frame(frame))
            self.assertEqual(sm.process_message(frame), (None, None, False))
        self.assertEqual(sm.state, L2ResyncState.AWAITING_INITIAL_SNAPSHOT)
        self.assertTrue(is_bybit_l2_data_frame(sample_message()))

    def test_a_data_frame_without_cts_still_fails_closed_in_the_machine(self):
        sm = BybitL2LiveResyncStateMachine()
        with self.assertRaises(BybitL2LiveSourceError):
            sm.process_message(sample_message(msg_type="snapshot", cts=None))

    def test_duplicate_snapshot_in_a_running_stream_is_idempotent(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201))
        accepted, gap, resub = sm.process_message(sample_message(msg_type="snapshot", u=101, seq=201))
        self.assertEqual((accepted, gap, resub), (None, None, False))
        self.assertEqual(sm.state, L2ResyncState.STREAMING)

    def test_snapshot_with_a_u_jump_closes_a_gap_it_cannot_hide(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200, cts=1705276800000))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201, cts=1705276801000))
        accepted, gap, resub = sm.process_message(
            sample_message(msg_type="snapshot", u=900, seq=999, cts=1705276830000)
        )
        self.assertIsNotNone(accepted)
        self.assertFalse(resub)
        self.assertEqual(gap.reason, L2GapReason.SEQUENCE_DISCONTINUITY.value)
        self.assertEqual((gap.last_good_update_id, gap.resync_update_id), (101, 900))
        self.assertEqual(gap.gap_start, millis(1705276801000))
        self.assertEqual(gap.gap_end, millis(1705276830000))
        self.assertEqual(sm.closed_gaps, (gap,))

    def test_snapshot_with_a_continuous_u_is_not_a_gap(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201))
        accepted, gap, _ = sm.process_message(sample_message(msg_type="snapshot", u=102, seq=202))
        self.assertIsNotNone(accepted)
        self.assertIsNone(gap)

    def test_snapshot_regression_is_refused_in_a_stream_and_in_resync(self):
        sm = BybitL2LiveResyncStateMachine()
        sm.process_message(sample_message(msg_type="snapshot", u=100, seq=200))
        sm.process_message(sample_message(msg_type="delta", u=101, seq=201))
        with self.assertRaises(BybitL2LiveIntegrityError) as ctx:
            sm.process_message(sample_message(msg_type="snapshot", u=1, seq=5))  # provider restart shape
        self.assertIn("sequence_regression", str(ctx.exception))

        sm.on_disconnected()
        sm.on_connected()
        with self.assertRaises(BybitL2LiveIntegrityError):
            sm.process_message(sample_message(msg_type="snapshot", u=1, seq=5))

    def test_heartbeat_timeout_before_any_anchor_opens_no_gap(self):
        sm = BybitL2LiveResyncStateMachine()
        self.assertIsNone(sm.on_heartbeat_timeout())
        self.assertIsNone(sm.active_gap)
        self.assertEqual(sm.state, L2ResyncState.AWAITING_INITIAL_SNAPSHOT)


if __name__ == "__main__":
    unittest.main()
