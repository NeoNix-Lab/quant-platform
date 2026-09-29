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
        self._duration_ns = definition.duration_ns
        self._active_gap_lowers: dict[str, Instant] = {}
        self._closed_gap_intervals: list[CoverageInterval] = []
        self._last_close_watermark_ns: int | None = None
        # Bucket starts (epoch ns) a gap is known to fall inside. D04's
        # IncrementalCandleBuilder has no concept of a gap and no way to
        # "un-merge" a bucket once a trade lands in it (by design -- gaps
        # are a B06 concept D04 must not know about), so once a bucket is
        # poisoned it is permanently excluded here: neither fed further
        # trades nor ever reported PARTIAL or CLOSED again. This is a
        # deliberate, minor, documented limitation rather than a full
        # discard/repair primitive on the builder: in already-acquired
        # canonical data, a live gap landing inside a still-open candle
        # bucket is expected to be rare (A11/ADR-0040 already reconciles
        # ordinary reconnects before B06 ever sees a gap at all).
        self._poisoned_bucket_starts: set[int] = set()

    def handle(self, event: LiveStreamEvent) -> tuple[IncrementalCandleUpdate, ...]:
        """Handle one B06 event and return any safe D04 updates."""

        if isinstance(event, LiveTradeEvent):
            bucket_start_ns = _bucket_start_ns(Instant.parse(event.record.exchange_ts), self._duration_ns)
            if bucket_start_ns in self._poisoned_bucket_starts:
                partial: tuple[IncrementalCandleUpdate, ...] = ()
            else:
                partial = (self._builder.consume(event.record),)
            closed = self._advance_with_trade_cursor(event.cursor)
            return (*partial, *closed)
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
        gap_interval = CoverageInterval(event.lower_bound, event.upper_bound)
        self._closed_gap_intervals.append(gap_interval)
        self._poison_buckets_overlapping(gap_interval)
        # The CLOSED event proves the affected interval's far edge; it is not
        # proof that the affected interval itself contains complete source data.
        return self._advance_watermark(event.lower_bound)

    def _poison_buckets_overlapping(self, gap_interval: CoverageInterval) -> None:
        # gap_interval is half-open [start, end): the bucket the exclusive
        # end instant itself starts is not actually touched by the gap, so
        # poison up to the bucket containing the last instant *inside* it.
        last_gap_instant = _instant_before(gap_interval.end) or gap_interval.start
        start = _bucket_start_ns(gap_interval.start, self._duration_ns)
        end = _bucket_start_ns(last_gap_instant, self._duration_ns)
        bucket_start_ns = start
        while bucket_start_ns <= end:
            self._poisoned_bucket_starts.add(bucket_start_ns)
            bucket_start_ns += self._duration_ns

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


def _bucket_start_ns(value: Instant, duration_ns: int) -> int:
    """Mirror D04's own UTC-epoch bucket alignment (CandleDefinition v1)."""

    return (value.epoch_ns // duration_ns) * duration_ns


__all__ = [
    "DEFAULT_LIVE_CANDLE_BATCH_SIZE",
    "GapSafeLiveCandleComposer",
    "LiveCandleStreamReport",
    "compose_live_candle_stream",
]
