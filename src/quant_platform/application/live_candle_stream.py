"""Application composition for B06 live streams into D04 live candles.

This module owns wiring only: B06 remains the source of live cursor/gap
semantics, and D04 remains the owner of CandleDefinition v1 aggregation. The
composer prevents live gap evidence from being silently converted into candle
completeness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..access import (
    LiveGapEvent,
    LiveGapStatus,
    LiveSessionEvent,
    LiveStreamCursorV1,
    LiveStreamEvent,
    LiveStreamRequest,
    LiveTradeEvent,
)
from ..data.models import CoverageInterval, Instant
from ..representation import (
    CandleDefinitionV1,
    CandleRecord,
    IncrementalCandleBuilder,
    IncrementalCandleUpdate,
    closed_candle_records,
)


DEFAULT_LIVE_CANDLE_BATCH_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class LiveCandleStreamReport:
    """Result of draining one B06 ``live_stream`` call through D04 candles."""

    updates: tuple[IncrementalCandleUpdate, ...]
    final_cursor: LiveStreamCursorV1 | None
    gap_events: tuple[LiveGapEvent, ...]
    session_events: tuple[LiveSessionEvent, ...]

    @property
    def closed_records(self) -> tuple[CandleRecord, ...]:
        return closed_candle_records(self.updates)


class GapSafeLiveCandleComposer:
    """Drive ``IncrementalCandleBuilder`` from B06 events without gap leakage."""

    def __init__(self, definition: CandleDefinitionV1):
        self._builder = IncrementalCandleBuilder(definition)
        self._active_gap_lowers: dict[str, Instant] = {}
        self._closed_gap_intervals: list[CoverageInterval] = []
        self._last_close_watermark_ns: int | None = None

    def handle(self, event: LiveStreamEvent) -> tuple[IncrementalCandleUpdate, ...]:
        """Handle one B06 event and return any safe D04 updates."""

        if isinstance(event, LiveTradeEvent):
            partial = self._builder.consume(event.record)
            closed = self._advance_with_trade_cursor(event.cursor)
            return (partial, *closed)
        if isinstance(event, LiveGapEvent):
            return self._handle_gap(event)
        if isinstance(event, LiveSessionEvent):
            return ()
        raise TypeError(f"unsupported live stream event: {type(event).__name__}")

    def _handle_gap(self, event: LiveGapEvent) -> tuple[IncrementalCandleUpdate, ...]:
        if event.status is LiveGapStatus.OPEN:
            self._active_gap_lowers[event.gap_id] = event.lower_bound
            return self._advance_watermark(_instant_before(event.lower_bound))
        self._active_gap_lowers.pop(event.gap_id, None)
        assert event.upper_bound is not None  # guaranteed by LiveGapEvent
        self._closed_gap_intervals.append(CoverageInterval(event.lower_bound, event.upper_bound))
        # The CLOSED event proves the affected interval's far edge; it is not
        # proof that the affected interval itself contains complete source data.
        return self._advance_watermark(event.lower_bound)

    def _advance_with_trade_cursor(
        self, cursor: LiveStreamCursorV1
    ) -> tuple[IncrementalCandleUpdate, ...]:
        candidate = cursor.last_canonical_exchange_ts
        if candidate is None:
            return ()
        if self._active_gap_lowers:
            first_open_gap = min(self._active_gap_lowers.values(), key=lambda item: item.epoch_ns)
            if candidate >= first_open_gap:
                candidate = _instant_before(first_open_gap)
        for interval in self._closed_gap_intervals:
            if candidate is not None and candidate <= interval.end:
                return ()
        return self._advance_watermark(candidate)

    def _advance_watermark(
        self, watermark: Instant | None
    ) -> tuple[IncrementalCandleUpdate, ...]:
        if watermark is None:
            return ()
        if (
            self._last_close_watermark_ns is not None
            and watermark.epoch_ns <= self._last_close_watermark_ns
        ):
            return ()
        self._last_close_watermark_ns = watermark.epoch_ns
        closed = self._builder.close_through(watermark)
        return tuple(update for update in closed if not self._intersects_closed_gap(update))

    def _intersects_closed_gap(self, update: IncrementalCandleUpdate) -> bool:
        bucket = CoverageInterval(update.bucket_start, update.bucket_end)
        return any(_overlaps(bucket, gap) for gap in self._closed_gap_intervals)


def compose_live_candle_stream(
    gateway: Any,
    request: LiveStreamRequest,
    definition: CandleDefinitionV1,
    *,
    cursor: LiveStreamCursorV1 | None = None,
    batch_size: int = DEFAULT_LIVE_CANDLE_BATCH_SIZE,
    composer: GapSafeLiveCandleComposer | None = None,
) -> LiveCandleStreamReport:
    """Drain one B06 live stream call into safe D04 candle updates."""

    active = composer or GapSafeLiveCandleComposer(definition)
    updates: list[IncrementalCandleUpdate] = []
    gaps: list[LiveGapEvent] = []
    sessions: list[LiveSessionEvent] = []
    final_cursor = cursor
    for event in gateway.live_stream(request, cursor=cursor, batch_size=batch_size):
        if isinstance(event, LiveTradeEvent):
            final_cursor = event.cursor
        elif isinstance(event, LiveSessionEvent):
            final_cursor = event.cursor
            sessions.append(event)
        elif isinstance(event, LiveGapEvent):
            gaps.append(event)
            if event.resumed_cursor is not None:
                final_cursor = event.resumed_cursor
        updates.extend(active.handle(event))
    return LiveCandleStreamReport(
        updates=tuple(updates),
        final_cursor=final_cursor,
        gap_events=tuple(gaps),
        session_events=tuple(sessions),
    )


def _instant_before(value: Instant) -> Instant | None:
    return Instant(value.epoch_ns - 1) if value.epoch_ns > 0 else None


def _overlaps(left: CoverageInterval, right: CoverageInterval) -> bool:
    return left.start < right.end and right.start < left.end


__all__ = [
    "DEFAULT_LIVE_CANDLE_BATCH_SIZE",
    "GapSafeLiveCandleComposer",
    "LiveCandleStreamReport",
    "compose_live_candle_stream",
]
