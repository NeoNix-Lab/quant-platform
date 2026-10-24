"""DG-O O02 (ADR-0075 sections 1 and 5): the continuous session -- one connection, a bounded buffer, explicit interruptions.

A scripted provider (an object with the ``websockets.connect`` shape) and scripted ports: no network, no catalog. The engine runs in a
worker thread (as in the server) while the test thread drives the provider and waits for conditions. The PostgreSQL proof through the
real publication path is ``tests/integration_live_session_postgres.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.live_session import (  # noqa: E402
    Interruption,
    LiveSessionConfig,
    LiveSessionEngine,
    LiveSessionFailure,
    LiveSessionPorts,
)
from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit_live import SessionState  # noqa: E402

MS = 1_000_000
INSTRUMENT = "ETHUSDT"
T0 = int(Instant.parse("2026-10-21T10:00:00Z").epoch_ns // MS)


class Closed(Exception):
    """The scripted provider's way to close the connection."""


def trade_message(first_ms: int, *ids: str, step: int = 1, instrument: str = INSTRUMENT) -> dict:
    rows = [
        {"i": trade_id, "s": instrument, "T": first_ms + k * step, "S": "Buy", "p": "100.10", "v": "0.5", "seq": 7}
        for k, trade_id in enumerate(ids)
    ]
    return {"topic": f"publicTrade.{instrument}", "type": "snapshot", "ts": first_ms + len(ids), "data": rows}


def record(ms: int, trade_id: str) -> TradeRecord:
    return TradeRecord("bybit", INSTRUMENT, Instant(ms * MS), "100.10", "0.5", "buy", None, trade_id, "7")


class FakeSocket:
    def __init__(self, provider: "FakeProvider") -> None:
        self.provider = provider
        self.inbox: "queue.Queue" = queue.Queue()

    async def send(self, payload: str) -> None:
        import asyncio

        document = json.loads(payload)
        self.provider.sent.append(document)
        self.provider.log.append(document["op"])
        if document["op"] == "subscribe" and self.provider.acknowledge:
            self.inbox.put(json.dumps({"op": "subscribe", "success": True, "conn_id": "c"}))
        if document["op"] == "ping" and self.provider.pong:
            self.inbox.put(json.dumps({"op": "ping", "ret_msg": "pong", "success": True, "conn_id": "c"}))
        await asyncio.sleep(0)

    async def recv(self) -> str:
        import asyncio

        while True:
            try:
                item = self.inbox.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.002)
                continue
            if isinstance(item, BaseException):
                raise item
            return item

    async def __aenter__(self) -> "FakeSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self.provider.closed_sockets += 1
        return False


class FakeProvider:
    def __init__(self) -> None:
        self.sockets: list[FakeSocket] = []
        self.sent: list[dict] = []
        self.log: list[str] = []
        self.connects = 0
        self.closed_sockets = 0
        self.acknowledge = True
        self.pong = True
        self.fail_connects = 0

    def connect(self, url, **kwargs):
        self.connects += 1
        self.log.append("connect")
        if self.fail_connects:
            self.fail_connects -= 1
            raise OSError("connection refused")
        socket = FakeSocket(self)
        self.sockets.append(socket)
        return socket

    @property
    def socket(self) -> FakeSocket:
        return self.sockets[-1]

    def push(self, message: dict) -> None:
        self.socket.inbox.put(json.dumps(message))

    def close(self) -> None:
        self.socket.inbox.put(Closed("the provider closed the connection"))


class Harness:
    """The engine with scripted ports, in a worker thread."""

    def __init__(self, provider: FakeProvider, *, config: LiveSessionConfig | None = None, checkpoint: bool = False,
                 reconcile=None, recent=(), floor=None, last_key=None, publish_status="PASS", gate: threading.Event | None = None) -> None:
        self.provider = provider
        self.slices: list = []
        self.chain_starts: list = []
        self.evidences: list = []
        self.persisted: list = []
        self.events: list[tuple[str, dict]] = []
        self.reconciled: list = []
        self.gaps: list = []
        self.checkpoint = checkpoint
        self.publish_status = publish_status
        self.gate = gate
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.summary = None
        self.sleeps: list[float] = []
        self._reconcile = reconcile
        self._recent = recent
        self._floor = floor
        self._last_key = last_key
        self.config = config or LiveSessionConfig(
            slice_seconds=3600, slice_max_trades=3, buffer_messages=50, ping_interval=30.0, pong_timeout=5.0,
            connect_timeout=2.0, reconnect_delay=0.01, reconnect_delay_max=0.02, tick=0.01)
        ports = LiveSessionPorts(
            connect=provider.connect, fetch_recent=self._fetch, publish_slice=self._publish, persist_checkpoint=self.persisted.append,
            reconcile=self._reconcile_call, checkpoint_exists=lambda: self.checkpoint, published_floor=lambda: self._floor,
            last_durable_key=lambda: self._last_key, record_gap=self._gap, connection_errors=(OSError, Closed),
            sleep=lambda s: self.sleeps.append(s) or time.sleep(0.001),
        )
        self.engine = LiveSessionEngine(
            instrument=INSTRUMENT, config=self.config, ports=ports, on_event=lambda name, **f: self.events.append((name, f)))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _fetch(self, limit):
        self.provider.log.append("fetch_recent")
        return self._recent

    def _publish(self, sliced, chain_start, evidence):
        if self.gate is not None:
            self.gate.wait(10)
        self.provider.log.append("publish")
        self.slices.append(sliced)
        self.chain_starts.append(chain_start)
        self.evidences.append(evidence)
        return SimpleNamespace(status=self.publish_status)

    def _reconcile_call(self, recent, buffered):
        self.provider.log.append("reconcile")
        self.reconciled.append((recent, buffered))
        return self._reconcile(recent, buffered)

    def _gap(self, report):
        self.gaps.append(report)
        return "gap-record"

    def _run(self) -> None:
        try:
            self.summary = self.engine.run(self.stop.is_set)
        except BaseException as exc:  # noqa: BLE001
            self.error = exc

    def __enter__(self) -> "Harness":
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop.set()
        if self.gate is not None:
            self.gate.set()
        self.thread.join(15)

    def until(self, condition, what: str, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error is not None:
                raise AssertionError(f"the engine failed: {self.error!r}")
            if condition():
                return
            time.sleep(0.005)
        raise AssertionError(f"timed out waiting for {what}; log={self.provider.log[-12:]} error={self.error!r}")

    def events_named(self, name: str) -> list[dict]:
        return [fields for event, fields in self.events if event == name]


def pass_restart(window_end_ms: int, accepted: list[TradeRecord], slices: int = 1):
    return SimpleNamespace(
        status="PASS", publication=SimpleNamespace(window_end=Instant(window_end_ms * MS).isoformat(), slices=slices),
        restart_outcome=SimpleNamespace(accepted_records=tuple(accepted)))


class ContinuousSessionTests(unittest.TestCase):
    def test_many_slices_are_published_over_one_connection_and_chain_exactly(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.connects == 1 and len(provider.sockets) == 1, "the connection")
            for k in range(8):
                provider.push(trade_message(T0 + k * 10, f"a{k}", f"b{k}"))
                time.sleep(0.03)  # a message at a time: each turn of the publisher closes what is due
            h.until(lambda: len(h.slices) >= 3, "three slices")
            first_socket = provider.socket
        self.assertIsNone(h.error)
        self.assertEqual(1, provider.connects)  # the session was never closed between publications
        self.assertEqual(1, provider.closed_sockets)  # and was closed once, by the stop
        self.assertEqual(
            [("subscribe", [f"publicTrade.{INSTRUMENT}"])],
            [(d["op"], d["args"]) for d in provider.sent if d["op"] == "subscribe"])
        for left, right in zip(h.slices, h.slices[1:]):
            self.assertEqual(left.window_end, right.window_start)  # exact chain
        # every trade the provider delivered is in exactly one slice
        got = [r.trade_id for s in h.slices for r in s.records]
        self.assertEqual(sorted(f"{p}{k}" for k in range(8) for p in "ab"), sorted(got))
        self.assertEqual([None] + [s.window_end for s in h.slices[:-1]], h.chain_starts)
        self.assertEqual(h.chain_starts[1:], [s.window_start for s in h.slices[1:]])
        self.assertIs(first_socket, provider.sockets[0])

    def test_the_checkpoint_advances_only_after_each_slice_is_published(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            for k in range(4):
                provider.push(trade_message(T0 + k * 10, f"a{k}", f"b{k}"))
                time.sleep(0.05)  # one message at a time: each turn of the publisher closes what is due
            h.until(lambda: len(h.persisted) >= 2, "two checkpoint advances")
        log = provider.log
        publishes = [i for i, name in enumerate(log) if name == "publish"]
        self.assertGreaterEqual(len(publishes), 2)
        self.assertEqual(len(h.slices), len(h.persisted))

    def test_the_evidence_of_a_slice_is_an_alive_session_and_claims_completeness(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))
            provider.push(trade_message(T0 + 10, "c", "d"))
            h.until(lambda: h.slices, "a slice")
        evidence = h.evidences[0]
        self.assertTrue(evidence.can_assert_complete_interval)
        self.assertEqual(SessionState.ACQUIRING, evidence.final_state)
        self.assertEqual((), evidence.interruptions)

    def test_a_clean_stop_publishes_what_is_pending_and_closes_the_session(self) -> None:
        provider = FakeProvider()
        config = LiveSessionConfig(slice_seconds=3600, slice_max_trades=1000, buffer_messages=50, tick=0.01, ping_interval=30)
        with Harness(provider, config=config) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b", "c"))
            time.sleep(0.15)
            self.assertEqual([], h.slices)  # nothing is due yet
        self.assertIsNone(h.error)
        (only,) = h.slices
        self.assertEqual(["a", "b", "c"], [r.trade_id for r in only.records])
        self.assertEqual(Instant((T0 + 3) * MS), only.window_end)  # one millisecond after its greatest trade
        self.assertEqual(1, len(h.persisted))
        self.assertEqual(1, provider.closed_sockets)


class WallClockTests(unittest.TestCase):
    def test_slice_seconds_of_wall_clock_close_a_slice_even_when_no_message_arrives(self) -> None:
        provider = FakeProvider()
        clock = {"now": 5000.0}
        config = LiveSessionConfig(
            slice_seconds=300, slice_max_trades=1000, buffer_messages=50, tick=0.01, ping_interval=10_000, pong_timeout=5000.0,
            connect_timeout=5000.0)
        h = Harness(provider, config=config)
        h.engine._ports.clock = lambda: clock["now"]
        with h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))
            provider.push(trade_message(T0 + 5, "c"))
            time.sleep(0.15)
            self.assertEqual([], h.slices)  # 3 trades, far from any bound
            clock["now"] += 299.0
            time.sleep(0.1)
            self.assertEqual([], h.slices)
            clock["now"] += 2.0  # 301 s of wall clock since the window opened
            h.until(lambda: h.slices, "the slice closed by the wall clock")
        (only, *_) = h.slices
        self.assertEqual(["a", "b"], [r.trade_id for r in only.records])  # "c" is the boundary millisecond: it stays
        self.assertEqual(Instant((T0 + 5) * MS), only.window_end)


class InterruptionTests(unittest.TestCase):
    def test_a_disconnect_publishes_what_was_listened_to_then_reconnects_and_reconciles(self) -> None:
        provider = FakeProvider()
        anchor = record(T0 + 2, "c")

        def reconcile(recent, buffered):
            return pass_restart(T0 + 40, [record(T0 + 20, "x"), record(T0 + 30, "y")])

        with Harness(provider, checkpoint=True, reconcile=reconcile, recent=(anchor,)) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0 + 100, "a", "b", "c"))  # the first trade of the subscription: the reconciliation follows it
            h.until(lambda: "reconcile" in provider.log, "the first reconciliation")
            provider.push(trade_message(T0 + 110, "d"))
            h.until(lambda: len(h.slices) >= 1, "a slice")
            provider.close()
            h.until(lambda: provider.connects == 2 and len(h.events_named("interruption")) == 1, "the reconnect")
            provider.push(trade_message(T0 + 200, "e"))  # the second subscription's first trade
            h.until(lambda: h.provider.log.count("reconcile") == 2, "the second reconciliation")
        (event,) = h.events_named("interruption")
        self.assertEqual(Interruption.DISCONNECT.value, event["reason"])
        self.assertEqual(1, h.summary.reconnects)
        # the trades received before the disconnect are all published (the last window closes 1 ms after its greatest trade)
        published = sorted(r.trade_id for s in h.slices for r in s.records)
        self.assertEqual(["a", "b", "c", "d", "e"], published)
        self.assertTrue(all(e.can_assert_complete_interval for e in h.evidences))  # taken before the interruption was recorded

    def test_the_session_subscribes_before_it_fetches_the_recent_trades(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(T0, [record(T0, "x")])) as h:
            h.until(lambda: provider.sockets, "the connection")
            self.assertNotIn("fetch_recent", provider.log)  # nothing is fetched before a trade of this subscription arrives
            provider.push(trade_message(T0 + 3, "a"))
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
        log = provider.log
        self.assertLess(log.index("subscribe"), log.index("fetch_recent"))
        self.assertLess(log.index("fetch_recent"), log.index("reconcile"))

    def test_the_buffered_trades_reach_the_reconciliation_and_their_duplicates_are_dropped_afterwards(self) -> None:
        provider = FakeProvider()
        seen = {}

        def reconcile(recent, buffered):
            seen["buffered"] = [r.trade_id for r in buffered]
            return pass_restart(T0 + 6, [record(T0 + 1, "p"), record(T0 + 5, "q")])

        gate = threading.Event()
        original = provider.connect

        def connect(url, **kwargs):
            socket = original(url, **kwargs)
            socket.inbox.put(json.dumps(trade_message(T0 + 5, "q")))  # already delivered before the subscription ack is read
            return socket

        provider.connect = connect
        config = LiveSessionConfig(slice_seconds=3600, slice_max_trades=1000, buffer_messages=50, tick=0.01, ping_interval=30)
        with Harness(provider, config=config, checkpoint=True, reconcile=reconcile) as h:
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            provider.push(trade_message(T0 + 5, "q"))  # a duplicate of a reconciled trade
            provider.push(trade_message(T0 + 8, "r"))
            time.sleep(0.2)
            gate.set()
        self.assertIn("q", seen["buffered"])
        flushed = [r.trade_id for s in h.slices for r in s.records]
        self.assertEqual(["r"], flushed)  # "q" was published by the reconciliation, not twice
        self.assertEqual(0, h.engine.summary.ordering_violations)

    def test_a_full_buffer_ends_the_session_as_an_interruption_and_publishes_what_was_buffered(self) -> None:
        provider = FakeProvider()
        gate = threading.Event()
        config = LiveSessionConfig(
            slice_seconds=3600, slice_max_trades=2, buffer_messages=2, tick=0.01, ping_interval=30, reconnect_delay=0.01,
            reconnect_delay_max=0.02)
        with Harness(provider, config=config, gate=gate) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))  # a slice is due, and publishing blocks on the gate
            h.until(lambda: "publish" not in provider.log and h.engine.summary.slices_published == 0 and
                    provider.socket.inbox.empty(), "the publisher to take the first message")
            time.sleep(0.05)
            for k in range(6):  # the publisher is busy: the reader fills the buffer of 2 and must stop
                provider.push(trade_message(T0 + 10 + k, f"m{k}"))
            h.until(lambda: provider.closed_sockets >= 1, "the reader to end the session")
            gate.set()
            h.until(lambda: h.events_named("interruption"), "the interruption")
        (event, *_) = h.events_named("interruption")
        self.assertEqual(Interruption.BUFFER_OVERFLOW.value, event["reason"])
        self.assertEqual(2, event["buffer_high_water"])
        self.assertEqual(2, event["buffer_capacity"])
        self.assertGreaterEqual(h.summary.interruptions.get("buffer_overflow", 0), 1)
        buffered = {r.trade_id for s in h.slices for r in s.records}
        self.assertTrue({"a", "b", "m0", "m1"} <= buffered)  # what had been buffered is published, none of it silently dropped

    def test_a_missing_pong_is_a_heartbeat_timeout_and_an_idle_instrument_with_pongs_is_not(self) -> None:
        config = LiveSessionConfig(
            slice_seconds=3600, slice_max_trades=10, buffer_messages=50, tick=0.01, ping_interval=0.05, pong_timeout=0.1,
            reconnect_delay=0.01, reconnect_delay_max=0.02)
        quiet = FakeProvider()  # answers every ping, sends no trade: an idle instrument
        with Harness(quiet, config=config) as h:
            h.until(lambda: quiet.log.count("ping") >= 3, "three pings")
            self.assertEqual([], h.events_named("interruption"))
        self.assertEqual(1, quiet.connects)
        dead = FakeProvider()
        dead.pong = False
        with Harness(dead, config=config) as h:
            h.until(lambda: h.events_named("interruption"), "the heartbeat timeout")
        self.assertEqual(Interruption.HEARTBEAT_TIMEOUT.value, h.events_named("interruption")[0]["reason"])

    def test_a_trade_below_the_open_window_is_an_ordering_violation_counted_and_never_accepted(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))
            provider.push(trade_message(T0 + 10, "c", "d"))  # 4 trades: a slice closes, the window now starts at T0 + 10
            h.until(lambda: h.slices, "a slice")
            provider.push(trade_message(T0 + 3, "late"))
            h.until(lambda: h.events_named("interruption"), "the interruption")
        self.assertEqual(Interruption.ORDERING_VIOLATION.value, h.events_named("interruption")[0]["reason"])
        self.assertEqual(1, h.summary.ordering_violations)
        self.assertNotIn("late", [r.trade_id for s in h.slices for r in s.records])

    def test_a_refused_connection_is_retried_with_a_bounded_backoff(self) -> None:
        provider = FakeProvider()
        provider.fail_connects = 3
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection after the refusals")
        self.assertEqual(4, provider.connects)
        reasons = [e["reason"] for e in h.events_named("interruption")]
        self.assertEqual([Interruption.DISCONNECT.value] * 3, reasons[:3])
        self.assertTrue(all(delay <= h.config.reconnect_delay_max for delay in h.sleeps))
        self.assertGreater(max(h.sleeps), h.config.reconnect_delay)  # it grows after a refusal, up to the bound
        self.assertEqual(1, len(provider.sockets))

    def test_a_message_that_cannot_be_canonicalized_stops_the_loop_after_publishing_what_was_valid(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))
            provider.push(trade_message(T0 + 5, "x", instrument="BTCUSDT") | {"topic": f"publicTrade.{INSTRUMENT}"})
            h.thread.join(5)
        self.assertIsInstance(h.error, LiveSessionFailure)
        self.assertEqual(["a", "b"], [r.trade_id for s in h.slices for r in s.records])


class ReconciliationTests(unittest.TestCase):
    def test_a_resumed_reconciliation_chains_the_next_slice_to_what_it_published(self) -> None:
        provider = FakeProvider()
        end = T0 + 51
        accepted = [record(T0 + 50, "last")]
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(end, accepted)) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(end, "n1", "n2", "n3"))
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            h.until(lambda: h.slices, "a slice")
        self.assertEqual(Instant(end * MS), h.slices[0].window_start)
        self.assertEqual(Instant(end * MS), h.chain_starts[0])
        self.assertEqual(1, h.summary.reconciliations)

    def test_a_buffered_trade_at_or_before_the_last_durable_key_is_a_duplicate_not_a_violation(self) -> None:
        provider = FakeProvider()
        end = T0 + 51
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(end, [record(T0 + 60, "new")]),
                     last_key=(Instant((T0 + 50) * MS), "anchor")) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0 + 48, "old1", "old2"))  # T0 + 48 and T0 + 49: already durable, the anchor is T0 + 50
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            time.sleep(0.2)
        self.assertEqual((0, []), (h.summary.ordering_violations, h.events_named("interruption")))

    def test_a_reconciliation_with_nothing_new_continues_from_the_floor(self) -> None:
        provider = FakeProvider()
        floor = Instant((T0 + 9) * MS)
        pending = SimpleNamespace(status="REAL_RESTART_PROOF_PENDING")
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pending, floor=floor,
                     last_key=(Instant((T0 + 8) * MS), "k")) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0 + 8, "dup"))  # at or before the last durable key: a duplicate
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            provider.push(trade_message(T0 + 9, "n1", "n2", "n3"))
            h.until(lambda: h.slices, "a slice")
        self.assertEqual(floor, h.slices[0].window_start)
        self.assertNotIn("dup", [r.trade_id for r in h.slices[0].records])
        self.assertEqual((0, []), (h.summary.ordering_violations, h.events_named("interruption")))  # a duplicate is not a violation

    def test_an_unresolved_gap_is_recorded_and_a_new_chain_starts_without_claiming_the_gap(self) -> None:
        provider = FakeProvider()
        gap = SimpleNamespace(
            status="GAP_DETECTED", restart_outcome=SimpleNamespace(reconcile_result=SimpleNamespace(evidence={"reason": "anchor absent"})))
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: gap) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0 + 500, "n1", "n2", "n3"))
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            h.until(lambda: h.slices, "a slice")
        self.assertEqual(1, len(h.gaps))
        self.assertEqual(Instant((T0 + 500) * MS), h.slices[0].window_start)  # a new chain at its first trade
        self.assertIsNone(h.chain_starts[0])  # nothing is chained across the gap
        self.assertEqual(1, h.summary.gaps)
        self.assertEqual("anchor absent", h.events_named("gap")[0]["reason"])

    def test_the_chain_of_an_earlier_session_is_not_carried_over_a_gap(self) -> None:
        provider = FakeProvider()
        gap = SimpleNamespace(
            status="GAP_DETECTED", restart_outcome=SimpleNamespace(reconcile_result=SimpleNamespace(evidence={"reason": "anchor absent"})))
        with Harness(provider, reconcile=lambda r, b: gap) as h:
            h.until(lambda: provider.sockets, "the first connection")
            provider.push(trade_message(T0, "a", "b", "c"))
            h.until(lambda: h.slices, "a slice of the first session")
            h.checkpoint = True  # a checkpoint exists from now on: the next session reconciles
            provider.close()
            h.until(lambda: provider.connects == 2 and len(provider.sockets) == 2, "the second connection")
            provider.push(trade_message(T0 + 5000, "n1", "n2", "n3"))
            h.until(lambda: "reconcile" in provider.log, "the reconciliation of the second session")
            h.until(lambda: any(sl.window_start == Instant((T0 + 5000) * MS) for sl in h.slices), "a slice after the gap")
        after = [i for i, sl in enumerate(h.slices) if sl.window_start == Instant((T0 + 5000) * MS)][0]
        self.assertGreater(after, 0)  # the first session published before the gap
        self.assertIsNone(h.chain_starts[after])  # nothing is chained across the gap
        self.assertEqual([], h.events_named("unclaimed_span"))  # the gap is the explanation, not an idle midnight

    def test_a_reconciliation_that_fails_stops_the_loop(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: SimpleNamespace(status="CERTIFICATION_FAILED")) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a"))
            h.thread.join(5)
        self.assertIsInstance(h.error, LiveSessionFailure)
        self.assertEqual([], h.slices)

    def test_with_no_checkpoint_there_is_nothing_to_reconcile(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=False) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b", "c"))
            h.until(lambda: h.slices, "a slice")
        self.assertNotIn("reconcile", provider.log)
        self.assertNotIn("fetch_recent", provider.log)


class PublicationTests(unittest.TestCase):
    def test_a_failed_publication_stops_the_loop_and_the_checkpoint_never_advances(self) -> None:
        provider = FakeProvider()
        with Harness(provider, publish_status="CERTIFICATION_FAILED") as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b", "c"))
            h.thread.join(5)
        self.assertIsInstance(h.error, LiveSessionFailure)
        self.assertEqual([], h.persisted)

    def test_a_window_that_moved_past_an_idle_midnight_is_reported_not_chained(self) -> None:
        provider = FakeProvider()
        midnight = int(Instant.parse("2026-10-22T00:00:00Z").epoch_ns // MS)
        floor = Instant((midnight - 5000) * MS)
        pending = SimpleNamespace(status="REAL_RESTART_PROOF_PENDING")
        config = LiveSessionConfig(slice_seconds=3600, slice_max_trades=3, buffer_messages=50, tick=0.01, ping_interval=30)
        with Harness(provider, config=config, checkpoint=True, reconcile=lambda r, b: pending, floor=floor) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(midnight + 1000, "n1", "n2", "n3"))
            h.until(lambda: "reconcile" in provider.log, "the reconciliation")
            h.until(lambda: h.slices, "a slice")
        self.assertEqual(Instant(midnight * MS), h.slices[0].window_start)
        self.assertIsNone(h.chain_starts[0])
        self.assertEqual(1, len(h.events_named("unclaimed_span")))

    def test_the_signal_fields_of_a_published_slice(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b", "c"))
            h.until(lambda: h.events_named("slice_published"), "a slice")
        (event, *_) = h.events_named("slice_published")
        self.assertEqual(2, event["trades"])  # the third trade is the boundary millisecond: it stays for the next slice
        self.assertEqual(50, event["buffer_capacity"])
        self.assertGreaterEqual(event["slice_lag_ms"], 0)
        self.assertIsInstance(event["slice_lag_ms"], int)
        for name in ("reconnects", "ordering_violations", "buffer_high_water", "partition_key", "window_end"):
            self.assertIn(name, event)


class ContinuityProofTests(unittest.TestCase):
    """ADR-0042 section 5: the recent trades prove continuity only when they meet the trades of the subscription."""

    def test_an_idle_subscription_neither_fetches_nor_reconciles_until_a_trade_arrives(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(T0, [record(T0, "x")])) as h:
            h.until(lambda: provider.sockets, "the connection")
            time.sleep(0.3)
            self.assertNotIn("fetch_recent", provider.log)
            self.assertNotIn("reconcile", provider.log)
            self.assertEqual([], h.events_named("reconciled"))  # an empty buffer is never read as continuity restored
        self.assertIsNone(h.error)

    def test_the_snapshot_is_taken_after_the_first_trade_and_the_reconciliation_receives_it(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(T0 + 2, [record(T0 + 1, "x")])) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0 + 5, "w1", "w2"))
            h.until(lambda: h.reconciled, "the reconciliation")
        recent, buffered = h.reconciled[0]
        self.assertEqual(["w1", "w2"], [r.trade_id for r in buffered])  # never an empty buffer
        log = provider.log
        self.assertLess(log.index("subscribe"), log.index("fetch_recent"))

    def test_a_session_that_ends_before_any_trade_reconciles_nothing_and_is_an_interruption(self) -> None:
        provider = FakeProvider()
        with Harness(provider, checkpoint=True, reconcile=lambda r, b: pass_restart(T0, [record(T0, "x")])) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.close()
            h.until(lambda: h.events_named("interruption"), "the interruption")
        self.assertEqual("disconnect", h.events_named("interruption")[0]["reason"])
        self.assertNotIn("reconcile", provider.log)
        self.assertNotIn("fetch_recent", provider.log)


    def test_a_subscription_that_is_never_acknowledged_ends_the_session_instead_of_waiting_for_a_trade(self) -> None:
        provider = FakeProvider()
        provider.acknowledge = False
        config = LiveSessionConfig(
            slice_seconds=3600, slice_max_trades=3, buffer_messages=50, ping_interval=30.0, pong_timeout=5.0,
            connect_timeout=0.2, reconnect_delay=5.0, reconnect_delay_max=5.0, tick=0.01)
        with Harness(provider, config=config, checkpoint=True,
                     reconcile=lambda r, b: pass_restart(T0, [record(T0, "x")])) as h:
            h.until(lambda: h.events_named("interruption"), "the interruption after the connect timeout")
        self.assertNotIn("reconcile", provider.log)


class BoundedMemoryAndPressureTests(unittest.TestCase):
    def test_a_long_session_does_not_accumulate_heartbeat_history(self) -> None:
        provider = FakeProvider()
        config = LiveSessionConfig(
            slice_seconds=3600, slice_max_trades=3, buffer_messages=50, tick=0.005, ping_interval=0.005, pong_timeout=5.0,
            connect_timeout=5.0)
        with Harness(provider, config=config) as h:
            h.until(lambda: provider.sockets, "the connection")
            h.until(lambda: provider.log.count("ping") >= 120, "a hundred and twenty pings and their pongs")
            self.assertLessEqual(len(h.engine.tracker._events), 6)  # the states of the session, not one event per pong
        self.assertEqual(1, provider.connects)

    def test_the_high_water_mark_is_reported_while_the_first_session_is_still_open(self) -> None:
        provider = FakeProvider()
        with Harness(provider) as h:
            h.until(lambda: provider.sockets, "the connection")
            provider.push(trade_message(T0, "a", "b"))
            provider.push(trade_message(T0 + 10, "c", "d"))
            h.until(lambda: h.events_named("slice_published"), "a slice")
            (event, *_) = h.events_named("slice_published")
            self.assertGreater(event["buffer_high_water"], 0)  # the session has not ended
            self.assertEqual(0, provider.closed_sockets)


class ConfigTests(unittest.TestCase):
    def test_bad_configuration_is_refused(self) -> None:
        for kwargs in (
            {"slice_seconds": 0}, {"slice_max_trades": -1}, {"buffer_messages": True}, {"recent_limit": 1001},
            {"ping_interval": 0}, {"tick": -1.0}, {"reconnect_delay": 5.0, "reconnect_delay_max": 1.0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LiveSessionConfig(**kwargs)

    def test_the_accepted_defaults(self) -> None:
        config = LiveSessionConfig()
        self.assertEqual((300, 50_000, 20_000, 1000), (config.slice_seconds, config.slice_max_trades, config.buffer_messages, config.recent_limit))

    def test_an_instrument_outside_the_allow_list_is_refused(self) -> None:
        with self.assertRaises(Exception):
            LiveSessionEngine(instrument="ADAUSDT", config=LiveSessionConfig(), ports=SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
