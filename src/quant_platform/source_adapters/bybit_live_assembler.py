"""Bybit live slice assembler v1 (ADR-0075 sections 2 and 5): the open window of a continuous session.

The reader of a session hands the trades of each message to :meth:`SliceAssembler.add`; the publisher asks :meth:`take_due` which slices
are ready and :meth:`flush` for the rest when the session ends. A slice closes after ``slice_seconds`` of wall clock or
``slice_max_trades`` trades, at a message boundary; the boundary is the greatest ``exchange_ts`` received and the trades equal to it stay
for the next slice; a window never crosses a UTC midnight; a trade below the open window's start is an ordering violation (counted, never
accepted). No clock, network or catalog is read except the injected clock.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import time

from ..data.models import Instant, TradeRecord
from .bybit import bybit_trade_v1_ordering_key
from .bybit_live import deduplicate_live_records
from .bybit_live_slices import (
    SLICE_MAX_TRADES_DEFAULT,
    SLICE_SECONDS_DEFAULT,
    LiveSlice,
    LiveSliceError,
    utc_day_start_ns,
)

_NS_PER_MS = 1_000_000
_NS_PER_DAY = 86_400 * 1_000_000_000

Key = tuple[Instant, str]


class SliceAssembler:
    """The records received since the last boundary, and the rules that cut them into slices."""

    def __init__(
        self,
        *,
        max_trades: int = SLICE_MAX_TRADES_DEFAULT,
        slice_seconds: int = SLICE_SECONDS_DEFAULT,
        window_start: Instant | None = None,
        skip_through: Key | None = None,
        skip_keys: Iterable[Key] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(max_trades, int) or isinstance(max_trades, bool) or max_trades < 1:
            raise LiveSliceError("max_trades must be a positive integer")
        if not isinstance(slice_seconds, int) or isinstance(slice_seconds, bool) or slice_seconds < 1:
            raise LiveSliceError("slice_seconds must be a positive integer")
        self._max_trades = max_trades
        self._slice_seconds = slice_seconds
        self._clock = clock
        self._window_start_ns: int | None = None if window_start is None else window_start.epoch_ns
        self._skip_through = skip_through
        self._skip_keys = frozenset(skip_keys)
        self._pending: dict[Key, TradeRecord] = {}
        self._opened_at: float | None = None
        self.ordering_violations = 0
        self.duplicates_skipped = 0

    @property
    def window_start(self) -> Instant | None:
        return None if self._window_start_ns is None else Instant(self._window_start_ns)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def start_window(
        self, start: Instant | None, *, skip_through: Key | None = None, skip_keys: Iterable[Key] = (),
    ) -> None:
        """A new chain after a reconciliation (``start`` = what it published up to) or after an explicit gap (``None``).

        The duplicates of what is already published are the keys at or before ``skip_through`` (the last durable key before the
        reconciliation) and the keys the reconciliation itself accepted (``skip_keys``): a trade the reconciliation never saw is not
        a duplicate of anything, however low its key, and is judged like any other."""

        if self._pending:
            raise LiveSliceError("a window cannot be restarted while records are pending")
        self._window_start_ns = None if start is None else start.epoch_ns
        self._skip_through = skip_through
        self._skip_keys = frozenset(skip_keys)
        self._opened_at = None

    def add(self, records: Iterable[TradeRecord]) -> int:
        """Take the trades of one message; returns how many were ordering violations (not accepted)."""

        accepted: list[tuple[Key, TradeRecord]] = []
        violations = 0
        for record in records:
            if record.exchange_ts.epoch_ns % _NS_PER_MS:
                raise LiveSliceError(f"record exchange_ts must be a whole millisecond: {record.exchange_ts.isoformat()}")
            key = bybit_trade_v1_ordering_key(record)
            if (self._skip_through is not None and key <= self._skip_through) or key in self._skip_keys:
                self.duplicates_skipped += 1
                continue
            if self._window_start_ns is not None and record.exchange_ts.epoch_ns < self._window_start_ns:
                violations += 1
                continue
            accepted.append((key, record))
        if self._window_start_ns is None and accepted:
            self._window_start_ns = min(record.exchange_ts.epoch_ns for _key, record in accepted)
        for key, record in accepted:
            previous = self._pending.get(key)
            # an equivalent duplicate is idempotent; a conflicting payload for one key fails closed (ADR-0040 section 4)
            self._pending[key] = record if previous is None else deduplicate_live_records((previous, record))[0]
        if accepted and self._opened_at is None:
            self._opened_at = self._clock()
        self.ordering_violations += violations
        return violations

    def due(self) -> bool:
        if not self._pending or self._window_start_ns is None:
            return False
        if self._pending_max_ns() >= utc_day_start_ns(self._window_start_ns) + _NS_PER_DAY:
            return True
        if len(self._pending) >= self._max_trades:
            return True
        return self._opened_at is not None and self._clock() - self._opened_at >= self._slice_seconds

    def take_due(self) -> list[LiveSlice]:
        """The slices ready now: a UTC midnight is cut at once, the size and wall-clock bounds cut at the greatest ``exchange_ts``."""

        slices = self._cut_midnights()
        if self._pending and (len(self._pending) >= self._max_trades or self._time_is_up()):
            boundary = self._pending_max_ns()
            before = [key for key in self._pending if key[0].epoch_ns < boundary]
            if before:
                slices.append(self._take(before, boundary))
                self._opened_at = self._clock()  # the carried trades open the next window
            # nothing before the boundary: the whole window is one millisecond, which cannot be cut; it waits
        return slices

    def flush(self) -> list[LiveSlice]:
        """Everything received, when the session ends: the last window closes one millisecond after its greatest ``exchange_ts``."""

        slices = self._cut_midnights()
        if self._pending:
            end = self._pending_max_ns() + _NS_PER_MS
            slices.append(self._take(list(self._pending), end))
        self._window_start_ns = None
        self._opened_at = None
        return slices

    # ------------------------------------------------------------------------------------------------------------

    def _pending_max_ns(self) -> int:
        return max(key[0].epoch_ns for key in self._pending)

    def _time_is_up(self) -> bool:
        return self._opened_at is not None and self._clock() - self._opened_at >= self._slice_seconds

    def _cut_midnights(self) -> list[LiveSlice]:
        slices: list[LiveSlice] = []
        while self._pending and self._window_start_ns is not None:
            midnight = utc_day_start_ns(self._window_start_ns) + _NS_PER_DAY
            if self._pending_max_ns() < midnight:
                break
            before = [key for key in self._pending if key[0].epoch_ns < midnight]
            if before:
                slices.append(self._take(before, midnight))
                if self._pending:
                    self._opened_at = self._clock()  # the trades after the midnight open the next window
            else:
                self._window_start_ns = midnight  # a window that holds no trade is not published
        return slices

    def _take(self, keys: list[Key], end_ns: int) -> LiveSlice:
        assert self._window_start_ns is not None
        records = tuple(self._pending.pop(key) for key in sorted(keys))
        slice_ = LiveSlice(Instant(self._window_start_ns), Instant(end_ns), records)
        self._window_start_ns = end_ns
        if not self._pending:
            self._opened_at = None
        return slice_


__all__ = ["SliceAssembler"]
