"""Bybit live trade slices v1 (ADR-0075 sections 2 and 3): pure windowing and partition identity.

A *slice* is the trades of one instrument whose ``exchange_ts`` lies in a half-open window ``[start, end)`` and is published as
revision 1 of its own partition ``dt=<UTC day of start>/seg=<HHMMSS.mmm of start>``. Nothing here reads a clock, a catalog or a
provider: the planner turns the ordered records of one acquisition into slices, and the key helpers translate a window to its
partition key and back, so a certification profile can check a coverage document against the key it names.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from ..data.models import DataIntegrityError, Instant, TradeRecord
from .bybit import bybit_trade_v1_ordering_key


SLICE_SECONDS_DEFAULT = 300
SLICE_MAX_TRADES_DEFAULT = 50_000

_NS_PER_MS = 1_000_000
_NS_PER_SECOND = 1_000_000_000
_NS_PER_DAY = 86_400 * _NS_PER_SECOND
_SLICE_KEY = re.compile(r"^dt=([0-9]{4}-[0-9]{2}-[0-9]{2})/seg=([0-9]{6})\.([0-9]{3})$")
_LEGACY_DAY_KEY = re.compile(r"^dt=[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class LiveSliceError(DataIntegrityError):
    """A window, a partition key or a record set that ADR-0075 does not allow."""


@dataclass(frozen=True, slots=True)
class LiveSlice:
    window_start: Instant
    window_end: Instant
    records: tuple[TradeRecord, ...]

    @property
    def partition_key(self) -> str:
        return slice_partition_key(self.window_start)

    @property
    def token(self) -> str:
        return slice_token(self.window_start)


@dataclass(frozen=True, slots=True)
class SlicePlan:
    slices: tuple[LiveSlice, ...]
    # Records strictly before the floor (the end of what is already published): a replay of a published slice or, on a live
    # acquisition, an ordering violation. They are never part of a slice.
    below_floor: tuple[TradeRecord, ...] = ()


def utc_day_start_ns(epoch_ns: int) -> int:
    return epoch_ns - epoch_ns % _NS_PER_DAY


def next_utc_midnight(instant: Instant) -> Instant:
    return Instant(utc_day_start_ns(instant.epoch_ns) + _NS_PER_DAY)


def _require_millisecond(instant: Instant, field: str) -> None:
    if instant.epoch_ns % _NS_PER_MS:
        raise LiveSliceError(f"{field} must be a whole millisecond: {instant.isoformat()}")


def slice_partition_key(window_start: Instant) -> str:
    """``dt=<UTC day>/seg=<HHMMSS.mmm>`` of a window start (whole milliseconds only)."""

    _require_millisecond(window_start, "window start")
    text = window_start.isoformat()  # YYYY-MM-DDTHH:MM:SS[.fff]Z
    day, clock = text[:-1].split("T")
    seconds, _, fraction = clock.partition(".")
    return f"dt={day}/seg={seconds.replace(':', '')}.{fraction.ljust(3, '0')}"


def slice_token(window_start: Instant) -> str:
    """Lowercase, file-name and identifier safe name of a window (``YYYYMMDD-HHMMSS.mmm``)."""

    key = slice_partition_key(window_start)
    match = _SLICE_KEY.fullmatch(key)
    assert match is not None
    return f"{match.group(1).replace('-', '')}-{match.group(2)}.{match.group(3)}"


def is_legacy_day_partition_key(key: str) -> bool:
    return isinstance(key, str) and _LEGACY_DAY_KEY.fullmatch(key) is not None


def parse_slice_partition_key(key: str) -> Instant:
    """The window start a slice key names. Strict: any other text, or an impossible time, is refused."""

    match = _SLICE_KEY.fullmatch(key) if isinstance(key, str) else None
    if match is None:
        raise LiveSliceError(f"not a live slice partition key: {key!r}")
    day, clock, millis = match.groups()
    try:
        return Instant.parse(f"{day}T{clock[:2]}:{clock[2:4]}:{clock[4:]}.{millis}Z")
    except Exception as exc:  # noqa: BLE001 - any impossible date/time is a refused key
        raise LiveSliceError(f"live slice partition key names an impossible time: {key!r}") from exc


def plan_slices(
    records: Iterable[TradeRecord],
    *,
    floor: Instant | None = None,
    start_at_floor: bool = False,
    max_trades: int = SLICE_MAX_TRADES_DEFAULT,
    slice_seconds: int = SLICE_SECONDS_DEFAULT,
) -> SlicePlan:
    """Cut the records of one acquisition into slices (ADR-0075 section 2).

    The records are taken in the canonical order ``(exchange_ts, trade_id)`` and grouped by ``exchange_ts``: a group is never
    split. A slice closes, at the boundary of the next group, when it holds ``max_trades`` trades, when the next group is
    ``slice_seconds`` or more after the slice's first trade, or at a UTC midnight (forced to the midnight instant). The
    acquisition's last window ends one millisecond after its greatest ``exchange_ts``: the session closes at a message
    boundary, so no acquired trade is dropped. Windows chain exactly; the first starts at the first trade, or at ``floor`` when
    ``start_at_floor`` (continuity proven by reconciliation). A window that would hold no trade is not produced.
    """

    if not isinstance(max_trades, int) or isinstance(max_trades, bool) or max_trades < 1:
        raise LiveSliceError("max_trades must be a positive integer")
    if not isinstance(slice_seconds, int) or isinstance(slice_seconds, bool) or slice_seconds < 1:
        raise LiveSliceError("slice_seconds must be a positive integer")
    if start_at_floor and floor is None:
        raise LiveSliceError("start_at_floor requires a floor")

    ordered = sorted(records, key=bybit_trade_v1_ordering_key)
    keys = [bybit_trade_v1_ordering_key(record) for record in ordered]
    if len(set(keys)) != len(keys):
        raise LiveSliceError("a slice cannot hold two records with the same (exchange_ts, trade_id)")
    for record in ordered:
        _require_millisecond(record.exchange_ts, "record exchange_ts")

    floor_ns = floor.epoch_ns if floor is not None else None
    below = tuple(r for r in ordered if floor_ns is not None and r.exchange_ts.epoch_ns < floor_ns)
    pending = ordered[len(below):]
    if not pending:
        return SlicePlan((), below)

    groups: list[list[TradeRecord]] = []
    for record in pending:
        if groups and groups[-1][0].exchange_ts == record.exchange_ts:
            groups[-1].append(record)
        else:
            groups.append([record])

    slices: list[LiveSlice] = []
    window_start_ns = floor_ns if start_at_floor else groups[0][0].exchange_ts.epoch_ns
    current: list[TradeRecord] = []
    width_ns = slice_seconds * _NS_PER_SECOND

    def close(end_ns: int) -> None:
        nonlocal current, window_start_ns
        if current:
            slices.append(LiveSlice(Instant(window_start_ns), Instant(end_ns), tuple(current)))
            current = []
        window_start_ns = end_ns

    for group in groups:
        group_ns = group[0].exchange_ts.epoch_ns
        # a midnight the window would cross: forced boundary, the later trades open the next slice
        while group_ns >= utc_day_start_ns(window_start_ns) + _NS_PER_DAY:
            midnight = utc_day_start_ns(window_start_ns) + _NS_PER_DAY
            if current:
                close(midnight)
            else:
                # nothing was acquired in this window: it is not published; the loop moves on a day at a time
                window_start_ns = midnight
        if current:
            first_ns = current[0].exchange_ts.epoch_ns
            if len(current) + len(group) > max_trades or group_ns - first_ns >= width_ns:
                close(group_ns)
        current.extend(group)

    last_ns = current[-1].exchange_ts.epoch_ns
    close(last_ns + _NS_PER_MS)
    return SlicePlan(tuple(slices), below)


__all__ = [
    "LiveSlice",
    "LiveSliceError",
    "SLICE_MAX_TRADES_DEFAULT",
    "SLICE_SECONDS_DEFAULT",
    "SlicePlan",
    "is_legacy_day_partition_key",
    "next_utc_midnight",
    "parse_slice_partition_key",
    "plan_slices",
    "slice_partition_key",
    "slice_token",
    "utc_day_start_ns",
]
