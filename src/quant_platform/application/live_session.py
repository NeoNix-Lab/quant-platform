"""ADR-0075 slice 9.2: one continuous Bybit WebSocket session per instance, a bounded buffer and explicit interruptions.

The reader (a thread with its own asyncio loop) stays connected: it subscribes, pings, canonicalizes every message and puts it in a
BOUNDED buffer. The publisher (the caller's thread) drains the buffer into slices (``SliceAssembler``) and publishes them while the
reader keeps listening; a slice is published, and only then the checkpoint advances. The session ends only by an *interruption* --
a disconnect, a missing pong, a full buffer, an ordering violation -- or by the caller's stop; an interruption is counted, signalled
and followed by the bounded reconciliation of ADR-0040 section 7 and ADR-0042 section 5 (continuity proven from the last durable key,
else an explicit gap, ADR-0044 unchanged). It is never turned into a ``complete`` claim over the unlistened span.

This module owns the loop and holds no catalog, storage or provider code: everything that touches them is a *port* the caller gives
(``LiveSessionPorts``), so the same engine runs against a scripted provider in the tests and against the real publication path in the
server (``live_ingest_server``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import queue
import threading
import time
from typing import Any

from quant_platform.data.models import Instant, TradeRecord
from quant_platform.source_adapters.bybit import bybit_trade_v1_ordering_key
from quant_platform.source_adapters.bybit_live import (
    BYBIT_LIVE_MAPPING_V1,
    BYBIT_LIVE_SOURCE_SEMANTICS_V1,
    BybitLiveSessionEvidence,
    LiveSessionTracker,
    LiveTradeBatch,
    SessionEvent,
    SessionState,
    bybit_live_topic,
    canonicalize_bybit_live_message,
)
from quant_platform.source_adapters.bybit_live_assembler import SliceAssembler
from quant_platform.source_adapters.bybit_live_slices import SLICE_MAX_TRADES_DEFAULT, SLICE_SECONDS_DEFAULT, LiveSlice

BYBIT_PUBLIC_LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BUFFER_MESSAGES_DEFAULT = 20_000


class Interruption(str, Enum):
    """Why a session ended (ADR-0075 section 5). Each one is counted, signalled and reconciled."""

    DISCONNECT = "disconnect"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    BUFFER_OVERFLOW = "buffer_overflow"
    ORDERING_VIOLATION = "ordering_violation"


class LiveSessionFailure(RuntimeError):
    """Not an interruption: a publication, reconciliation or source failure that stops the loop (fail closed)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveSessionConfig:
    slice_seconds: int = SLICE_SECONDS_DEFAULT
    slice_max_trades: int = SLICE_MAX_TRADES_DEFAULT
    buffer_messages: int = BUFFER_MESSAGES_DEFAULT
    ping_interval: float = 20.0
    pong_timeout: float = 10.0
    connect_timeout: float = 30.0
    recent_limit: int = 1000
    reconnect_delay: float = 1.0
    reconnect_delay_max: float = 30.0
    tick: float = 0.2

    def __post_init__(self) -> None:
        for name in ("slice_seconds", "slice_max_trades", "buffer_messages", "recent_limit"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= self.recent_limit <= 1000:
            raise ValueError("recent_limit must be in [1, 1000]")
        for name in ("ping_interval", "pong_timeout", "connect_timeout", "reconnect_delay", "reconnect_delay_max", "tick"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        if self.reconnect_delay_max < self.reconnect_delay:
            raise ValueError("reconnect_delay_max must not be below reconnect_delay")


@dataclass(slots=True)
class LiveSessionPorts:
    """Everything the engine needs from the outside world. The server builds the real ones; the tests script them."""

    connect: Callable[..., Any]  # websockets.connect-like: connect(url, ping_interval=None, close_timeout=5) -> async context manager
    fetch_recent: Callable[[int], Sequence[TradeRecord]]
    publish_slice: Callable[[LiveSlice, Instant | None, BybitLiveSessionEvidence], Any]  # -> report with .status
    persist_checkpoint: Callable[[Any], Any]  # the publication report -> advances the checkpoint
    reconcile: Callable[[Sequence[TradeRecord], Sequence[TradeRecord]], Any]  # (recent, buffered) -> restart report
    checkpoint_exists: Callable[[], bool]
    published_floor: Callable[[], Instant | None]
    last_durable_key: Callable[[], tuple[Instant, str] | None]
    record_gap: Callable[[Any], Any]
    connection_errors: tuple[type[BaseException], ...] = (OSError,)
    clock: Callable[[], float] = time.monotonic
    wall_ns: Callable[[], int] = time.time_ns
    sleep: Callable[[float], None] = time.sleep


@dataclass(slots=True)
class LiveSessionSummary:
    slices_published: int = 0
    sessions: int = 0
    reconnects: int = 0
    reconciliations: int = 0
    gaps: int = 0
    ordering_violations: int = 0
    buffer_high_water: int = 0
    interruptions: dict[str, int] = field(default_factory=dict)


class _Reader(threading.Thread):
    """The connected session: one connection, kept open until the session ends."""

    def __init__(
        self, *, connect: Callable[..., Any], instrument: str, tracker: LiveSessionTracker, lock: threading.Lock,
        out: "queue.Queue[LiveTradeBatch]", config: LiveSessionConfig, stop: threading.Event,
        connection_errors: tuple[type[BaseException], ...], clock: Callable[[], float],
    ) -> None:
        super().__init__(name=f"live-session-reader-{instrument}", daemon=True)
        self._connect = connect
        self._instrument = instrument
        self._topic = bybit_live_topic(instrument)
        self._tracker = tracker
        self._lock = lock
        self._out = out
        self._config = config
        self._stop_event = stop
        self._connection_errors = connection_errors
        self._clock = clock
        self.subscribed = threading.Event()
        self.finished = threading.Event()
        self.interruption: Interruption | None = None
        self.detail = ""
        self.error: BaseException | None = None
        self.high_water = 0

    def run(self) -> None:
        try:
            asyncio.run(self._read())
        except BaseException as exc:  # noqa: BLE001 - surfaced to the publisher, which fails closed
            self.error = exc
        finally:
            self.finished.set()

    async def _read(self) -> None:
        config = self._config
        try:
            async with self._connect(BYBIT_PUBLIC_LINEAR_WS_URL, ping_interval=None, close_timeout=5) as socket:
                with self._lock:
                    self._tracker.connected()
                await socket.send('{"op": "subscribe", "args": ["%s"]}' % self._topic)  # the topic is an allow-listed symbol: no escaping needed
                next_ping = self._clock() + config.ping_interval
                ping_sent_at: float | None = None
                while not self._stop_event.is_set():
                    now = self._clock()
                    if ping_sent_at is not None and now - ping_sent_at > config.pong_timeout:
                        self.interruption, self.detail = Interruption.HEARTBEAT_TIMEOUT, "no pong within the timeout"
                        return
                    if ping_sent_at is None and now >= next_ping:
                        await socket.send('{"op": "ping"}')
                        ping_sent_at = now
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=config.tick)
                    except asyncio.TimeoutError:
                        continue
                    document = json.loads(raw)
                    op = document.get("op")
                    if op == "subscribe":
                        if document.get("success") is not True:
                            raise LiveSessionFailure(f"subscription refused: {document!r}")
                        with self._lock:
                            self._tracker.subscribed(topic=self._topic, conn_id=document.get("conn_id"))
                        self.subscribed.set()
                        continue
                    if op in {"ping", "pong"}:
                        with self._lock:
                            self._tracker.heartbeat(conn_id=document.get("conn_id"))
                        ping_sent_at, next_ping = None, self._clock() + config.ping_interval
                        continue
                    if document.get("topic") != self._topic:
                        continue
                    batch = canonicalize_bybit_live_message(document, instrument=self._instrument)
                    with self._lock:
                        self._tracker.observed_message(batch)
                    self.subscribed.set()  # a topic message is itself the acknowledgement (ADR-0040 section 6)
                    try:
                        self._out.put_nowait(batch)
                    except queue.Full:
                        # never a silent drop: the session ends here, the buffered messages are published, the rest is reconciled
                        self.interruption, self.detail = Interruption.BUFFER_OVERFLOW, "the buffer is full"
                        return
                    self.high_water = max(self.high_water, self._out.qsize())
        except self._connection_errors as exc:
            self.interruption, self.detail = Interruption.DISCONNECT, f"{type(exc).__name__}: {exc}"


class LiveSessionEngine:
    """The loop of one instance: sessions, slices, interruptions, reconciliation (ADR-0075 sections 1, 2 and 5)."""

    def __init__(
        self,
        *,
        instrument: str,
        config: LiveSessionConfig,
        ports: LiveSessionPorts,
        on_event: Callable[..., None] = lambda name, **fields: None,
    ) -> None:
        bybit_live_topic(instrument)  # validates the instrument against the allow-list
        self._instrument = instrument
        self._config = config
        self._ports = ports
        self._on_event = on_event
        self.summary = LiveSessionSummary()
        self._chain_end: Instant | None = None
        self._messages = 0
        self._records = 0

    # ------------------------------------------------------------------------------------------------ loop

    def run(self, stop_requested: Callable[[], bool] = lambda: False) -> LiveSessionSummary:
        delay = self._config.reconnect_delay
        while not stop_requested():
            published_before = self.summary.slices_published
            interruption = self._session(stop_requested)
            if interruption is None:  # the caller's stop
                break
            self.summary.interruptions[interruption.value] = self.summary.interruptions.get(interruption.value, 0) + 1
            delay = self._config.reconnect_delay if self.summary.slices_published > published_before else min(
                self._config.reconnect_delay_max, delay * 2)
            self._wait(delay, stop_requested)
        return self.summary

    def _wait(self, seconds: float, stop_requested: Callable[[], bool]) -> None:
        remaining = seconds
        while remaining > 0 and not stop_requested():
            step = min(remaining, 1.0)
            self._ports.sleep(step)
            remaining -= step

    # ------------------------------------------------------------------------------------------------ one session

    def _session(self, stop_requested: Callable[[], bool]) -> Interruption | None:
        ports, config = self._ports, self._config
        self.summary.sessions += 1
        if self.summary.sessions > 1:
            self.summary.reconnects += 1
        tracker = LiveSessionTracker(self._instrument)
        lock = threading.Lock()
        buffer: "queue.Queue[LiveTradeBatch]" = queue.Queue(maxsize=config.buffer_messages)
        stop_reader = threading.Event()
        reader = _Reader(
            connect=ports.connect, instrument=self._instrument, tracker=tracker, lock=lock, out=buffer, config=config,
            stop=stop_reader, connection_errors=ports.connection_errors, clock=ports.clock,
        )
        assembler = SliceAssembler(max_trades=config.slice_max_trades, slice_seconds=config.slice_seconds, clock=ports.clock)
        reason: Interruption | None = None
        failure: BaseException | None = None
        stopped = False
        self._messages = self._records = 0
        reader.start()
        try:
            established = self._await_subscription(reader, stop_requested)
            if not established:
                stopped = stop_requested()
                reason = reader.interruption or Interruption.DISCONNECT
                failure = reader.error
            else:
                staged = self._reconcile(reader, buffer, assembler)
                for batch in staged:  # message by message, as in the steady state: a burst is cut at the bounds too
                    self._take(assembler, batch)
                    if assembler.ordering_violations:
                        reason = Interruption.ORDERING_VIOLATION
                        break
                    for sliced in assembler.take_due():
                        self._publish(sliced, tracker)
                while reason is None and not stopped and failure is None:
                    reason, stopped, failure = self._step(reader, buffer, assembler, tracker, stop_requested)
        finally:
            stop_reader.set()
            reader.join(timeout=10.0)
        # what was listened to is published BEFORE the interruption is recorded: the evidence of those windows is the session's
        # alive state, and a disconnect that comes after them must not turn their claim into a gap
        try:
            while True:
                try:
                    self._take(assembler, buffer.get_nowait())
                except queue.Empty:
                    break
            for sliced in assembler.flush():
                self._publish(sliced, tracker)
        finally:
            self.summary.buffer_high_water = max(self.summary.buffer_high_water, reader.high_water)
        if assembler.ordering_violations and reason is None:
            reason = Interruption.ORDERING_VIOLATION
        self.summary.ordering_violations += assembler.ordering_violations
        failure = failure or reader.error
        if failure is not None:
            raise (failure if isinstance(failure, LiveSessionFailure) else LiveSessionFailure(
                f"source failure: {type(failure).__name__}: {failure}"))
        if stopped and reason is None:
            return None
        reason = reason or reader.interruption or Interruption.DISCONNECT
        self._on_event(
            "interruption", reason=reason.value, detail=reader.detail, reconnects=self.summary.reconnects,
            ordering_violations=self.summary.ordering_violations, buffer_high_water=self.summary.buffer_high_water,
            buffer_capacity=config.buffer_messages,
        )
        return reason

    def _await_subscription(self, reader: _Reader, stop_requested: Callable[[], bool]) -> bool:
        deadline = self._ports.clock() + self._config.connect_timeout
        while not reader.subscribed.is_set():
            if reader.finished.is_set() or stop_requested() or self._ports.clock() > deadline:
                return False
            reader.finished.wait(self._config.tick)
        return True

    def _step(self, reader, buffer, assembler, tracker, stop_requested):
        """One turn of the publisher: a message, the slices that are due. Returns ``(reason, stopped, failure)``."""

        if stop_requested():
            return None, True, None
        try:
            batch = buffer.get(timeout=self._config.tick)
        except queue.Empty:
            batch = None
        if batch is not None:
            self._take(assembler, batch)
            if assembler.ordering_violations:
                return Interruption.ORDERING_VIOLATION, False, None
        elif reader.finished.is_set():
            return reader.interruption or Interruption.DISCONNECT, False, reader.error
        for sliced in assembler.take_due():
            self._publish(sliced, tracker)
        return None, False, None

    def _take(self, assembler: SliceAssembler, batch: LiveTradeBatch) -> None:
        self._messages += 1
        self._records += len(batch.records)
        assembler.add(batch.records)

    # ------------------------------------------------------------------------------------------------ reconciliation

    def _drain_available(self, buffer: "queue.Queue[LiveTradeBatch]") -> list[LiveTradeBatch]:
        drained: list[LiveTradeBatch] = []
        while True:
            try:
                drained.append(buffer.get_nowait())
            except queue.Empty:
                return drained

    def _reconcile(self, reader: _Reader, buffer: "queue.Queue[LiveTradeBatch]", assembler: SliceAssembler) -> list[LiveTradeBatch]:
        """Subscribed first, then the recent trades (ADR-0042 section 5): the overlap of the two is the continuity proof."""

        ports = self._ports
        staged = self._drain_available(buffer)
        if not ports.checkpoint_exists():
            assembler.start_window(None)
            self._chain_end = None
            return staged
        recent = tuple(ports.fetch_recent(self._config.recent_limit))
        staged += self._drain_available(buffer)
        buffered = tuple(record for batch in staged for record in batch.records)
        report = ports.reconcile(recent, buffered)
        status = getattr(report, "status", None)
        if status == "PASS":
            publication = report.publication
            accepted = report.restart_outcome.accepted_records
            end = Instant.parse(publication.window_end)
            assembler.start_window(end, skip_through=bybit_trade_v1_ordering_key(accepted[-1]))
            self._chain_end = end
            self.summary.reconciliations += 1
            self.summary.slices_published += publication.slices
            self._on_event("reconciled", records=len(accepted), slices=publication.slices, window_end=publication.window_end)
        elif status == "REAL_RESTART_PROOF_PENDING":
            floor = ports.published_floor()
            assembler.start_window(floor, skip_through=ports.last_durable_key())
            self._chain_end = floor
            self.summary.reconciliations += 1
            self._on_event("reconciled", records=0, slices=0, window_end=None if floor is None else floor.isoformat())
        elif status == "GAP_DETECTED":
            record = ports.record_gap(report)
            assembler.start_window(None)
            self._chain_end = None
            self.summary.gaps += 1
            self._on_event("gap", record=record, reason=getattr(report.restart_outcome.reconcile_result, "evidence", {}).get("reason"))
        else:
            raise LiveSessionFailure(f"reconciliation failed: {status}")
        return staged

    # ------------------------------------------------------------------------------------------------ publication

    def _evidence(self, tracker: LiveSessionTracker) -> BybitLiveSessionEvidence:
        """The evidence of the window being published: the session was subscribed and acquiring through it, nothing else is claimed.

        Built from the segment since the last slice, not from the tracker's whole history (which grows with every heartbeat)."""

        if tracker.state is not SessionState.ACQUIRING:
            raise LiveSessionFailure(f"a slice cannot be published from a session in state {tracker.state.value}")
        events = tuple(
            SessionEvent(state, "session_continuing", {"messages_in_window": self._messages})
            for state in (SessionState.CONNECTED, SessionState.SUBSCRIBED, SessionState.ACQUIRING)
        )
        return BybitLiveSessionEvidence(
            source_semantics_id=BYBIT_LIVE_SOURCE_SEMANTICS_V1, mapping_id=BYBIT_LIVE_MAPPING_V1,
            final_state=SessionState.ACQUIRING, events=events, interruptions=(), integrity_conflicts=(),
            validated_messages=self._messages, accepted_records=self._records,
        )

    def _publish(self, sliced: LiveSlice, tracker: LiveSessionTracker) -> None:
        ports = self._ports
        evidence = self._evidence(tracker)
        chain_start = self._chain_end
        if chain_start is not None and chain_start != sliced.window_start:
            # the window moved past a UTC midnight that held no trade: that span has no slice and is not claimed
            self._on_event("unclaimed_span", start=chain_start.isoformat(), end=sliced.window_start.isoformat())
            chain_start = None
        report = ports.publish_slice(sliced, chain_start, evidence)
        if getattr(report, "status", None) != "PASS":
            raise LiveSessionFailure(f"slice publication failed: {getattr(report, 'status', None)}")
        ports.persist_checkpoint(report)  # only after the slice is durable (ADR-0042 section 2)
        self._chain_end = sliced.window_end
        self._messages = self._records = 0
        self.summary.slices_published += 1
        lag_ms = max(0, (ports.wall_ns() - sliced.window_end.epoch_ns) // 1_000_000)  # signals carry integers, not floats
        self._on_event(
            "slice_published", partition_key=sliced.partition_key, trades=len(sliced.records),
            window_end=sliced.window_end.isoformat(), slice_lag_ms=lag_ms, reconnects=self.summary.reconnects,
            ordering_violations=self.summary.ordering_violations, buffer_high_water=self.summary.buffer_high_water,
            buffer_capacity=self._config.buffer_messages, report=report,
        )


__all__ = [
    "BUFFER_MESSAGES_DEFAULT",
    "BYBIT_PUBLIC_LINEAR_WS_URL",
    "Interruption",
    "LiveSessionConfig",
    "LiveSessionEngine",
    "LiveSessionFailure",
    "LiveSessionPorts",
    "LiveSessionSummary",
]
