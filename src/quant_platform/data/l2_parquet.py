"""Canonical ``l2-book-event-v1`` Parquet layout, content identity and reads.

ADR-0070 §3-§4 (A14b).  This module is the *physical* side of the L2 path: the
column layout, the order-sensitive content hash and the bounded scanner.  The
write path lives in :mod:`quant_platform.data.l2_materializer`.  It is a
sibling of the ``trade-v1`` reader in :mod:`quant_platform.data.parquet`; no
``trade-v1`` code path is shared or modified.

One row is one source event (snapshot or delta), never a reconstructed book.
Level order and exact decimal text are preserved.  ``native_sequence`` and
``native_update_id`` are stored as ``uint64`` so the order is numeric, and are
returned as the canonical decimal strings of :class:`L2BookEvent`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path
import re
from typing import Any

from .models import (
    DataIntegrityError,
    Instant,
    InvalidRequest,
    L2BookEvent,
    L2LevelChange,
    StorageResolutionError,
    l2_book_event_identity_v1,
)
from .parquet import _arrow_boundary_safe, _batch_column, _timestamp_values

L2_BOOK_EVENT_SCHEMA_ID = "l2-book-event-v1"
L2_CONTENT_HASH_IDENTITY = "canonical-content-hash-v1"
L2_CONTENT_HASH_DOMAIN_TAG = b"quant-platform/canonical-content-hash-v1/l2-book-event-v1\x00"
UINT64_MAX = 2**64 - 1

#: Physical column order.  The nullable subset is :data:`L2_NULLABLE_COLUMNS`.
L2_COLUMNS = (
    "venue",
    "market_type",
    "instrument",
    "native_symbol",
    "source_channel",
    "acquisition_mode",
    "event_type",
    "exchange_ts",
    "provider_ts",
    "receive_ts",
    "source_depth_limit",
    "native_sequence",
    "native_prev_sequence",
    "native_update_id",
    "continuity_token",
    "bids",
    "asks",
)
L2_NULLABLE_COLUMNS = frozenset(
    {"provider_ts", "receive_ts", "source_depth_limit", "native_prev_sequence", "continuity_token"}
)
L2_LEVEL_FIELDS = ("price", "size", "action", "order_count")

_DIGITS = re.compile(r"^(0|[1-9][0-9]*)$")
_STRING_COLUMNS = (
    "venue",
    "market_type",
    "instrument",
    "native_symbol",
    "source_channel",
    "acquisition_mode",
    "event_type",
    "native_prev_sequence",
    "continuity_token",
)


def l2_book_event_arrow_schema(pa: Any) -> Any:
    """The exact Arrow schema of one canonical L2 partition file."""

    timestamp = pa.timestamp("ns", tz="UTC")
    level = pa.struct(
        [
            pa.field("price", pa.string(), nullable=False),
            pa.field("size", pa.string(), nullable=False),
            pa.field("action", pa.string(), nullable=False),
            pa.field("order_count", pa.string(), nullable=True),
        ]
    )
    types = {
        "exchange_ts": timestamp,
        "provider_ts": timestamp,
        "receive_ts": timestamp,
        "source_depth_limit": pa.int32(),
        "native_sequence": pa.uint64(),
        "native_update_id": pa.uint64(),
        "bids": pa.list_(level),
        "asks": pa.list_(level),
    }
    return pa.schema(
        [
            pa.field(
                name,
                types.get(name, pa.string()),
                nullable=name in L2_NULLABLE_COLUMNS,
            )
            for name in L2_COLUMNS
        ]
    )


def canonical_uint64(value: str | None, field: str) -> int:
    """Return the integer of a canonical decimal string that fits ``uint64``."""

    if not isinstance(value, str) or not _DIGITS.fullmatch(value):
        raise DataIntegrityError(f"l2-book-event-v1 {field} must be canonical decimal digits")
    number = int(value)
    if number > UINT64_MAX:
        raise DataIntegrityError(f"l2-book-event-v1 {field} does not fit uint64")
    return number


class L2ContentHasher:
    """Incremental, order-sensitive, domain-separated content hash.

    Frame: ``tag || event_identity_digest(32 bytes)* || count(uint64, big
    endian)``.  The count is a trailer so a single-pass writer can stream; every
    event contributes the digest of its frozen ``l2_book_event_identity_v1``,
    so the hash covers every field and the exact text of every level.
    """

    __slots__ = ("_digest", "_count")

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(L2_CONTENT_HASH_DOMAIN_TAG)
        self._count = 0

    def update(self, event: L2BookEvent) -> None:
        identity = l2_book_event_identity_v1(event)
        self._digest.update(bytes.fromhex(identity.rsplit(":", 1)[1]))
        self._count += 1

    @property
    def count(self) -> int:
        return self._count

    def hexdigest_identity(self) -> str:
        final = self._digest.copy()
        final.update(self._count.to_bytes(8, "big", signed=False))
        return f"{L2_CONTENT_HASH_IDENTITY}:sha256:{final.hexdigest()}"


def l2_canonical_content_hash_v1(events: Iterable[L2BookEvent]) -> str:
    """Hash ordered canonical L2 events.  Input order is consumed as supplied."""

    hasher = L2ContentHasher()
    for event in events:
        if not isinstance(event, L2BookEvent):
            raise InvalidRequest("l2 canonical content hash requires L2BookEvent values")
        hasher.update(event)
    return hasher.hexdigest_identity()


def _check_schema(schema: Any, pa: Any, name: str) -> None:
    names = list(schema.names)
    if names != list(L2_COLUMNS):
        missing = sorted(set(L2_COLUMNS) - set(names))
        extra = sorted(set(names) - set(L2_COLUMNS))
        raise DataIntegrityError(
            f"l2-book-event-v1 Parquet layout mismatch in {name}: "
            f"missing={missing} extra={extra} order_ok={sorted(names) == sorted(L2_COLUMNS)}"
        )
    for field in schema:
        column = field.name
        type_ = field.type
        if column in ("exchange_ts", "provider_ts", "receive_ts"):
            ok = pa.types.is_timestamp(type_) and type_.unit == "ns" and type_.tz == "UTC"
        elif column in ("native_sequence", "native_update_id"):
            ok = pa.types.is_uint64(type_)
        elif column == "source_depth_limit":
            ok = pa.types.is_int32(type_)
        elif column in ("bids", "asks"):
            ok = pa.types.is_list(type_) and pa.types.is_struct(type_.value_type)
            if ok:
                level = type_.value_type
                ok = [level.field(i).name for i in range(level.num_fields)] == list(
                    L2_LEVEL_FIELDS
                ) and all(pa.types.is_string(level.field(i).type) for i in range(level.num_fields))
        else:
            ok = pa.types.is_string(type_)
        if not ok:
            raise DataIntegrityError(
                f"l2-book-event-v1 Parquet column {column!r} has unsupported type {type_}"
            )
        if field.nullable != (column in L2_NULLABLE_COLUMNS):
            raise DataIntegrityError(
                f"l2-book-event-v1 Parquet column {column!r} has the wrong nullability"
            )


def _levels(values: list[dict[str, Any]] | None, side: str, column: str) -> tuple[L2LevelChange, ...]:
    if values is None:
        raise DataIntegrityError(f"l2-book-event-v1 {column} is null")
    return tuple(
        L2LevelChange(
            side=side,
            price=item["price"],
            size=item["size"],
            action=item["action"],
            order_count=item["order_count"],
        )
        for item in values
    )


def _events_from_batch(
    batch: Any,
    *,
    start: Instant | None,
    end: Instant | None,
    pa: Any,
    pc: Any,
) -> tuple[L2BookEvent, ...]:
    exchange_field = batch.schema.field("exchange_ts")
    exchange_ns = _timestamp_values(_batch_column(batch, "exchange_ts"), exchange_field.type, pa, pc)
    provider_ns = _timestamp_values(_batch_column(batch, "provider_ts"), exchange_field.type, pa, pc)
    receive_ns = _timestamp_values(_batch_column(batch, "receive_ts"), exchange_field.type, pa, pc)
    plain = {
        name: _batch_column(batch, name).to_pylist()
        for name in _STRING_COLUMNS + ("source_depth_limit", "native_sequence", "native_update_id", "bids", "asks")
    }
    events: list[L2BookEvent] = []
    for index in range(batch.num_rows):
        stamp = exchange_ns[index]
        if stamp is None:
            raise DataIntegrityError("l2-book-event-v1 exchange_ts is null")
        exchange = Instant(stamp)
        if start is not None and end is not None and not (start <= exchange < end):
            continue
        sequence = plain["native_sequence"][index]
        update_id = plain["native_update_id"][index]
        if sequence is None or update_id is None:
            raise DataIntegrityError("l2-book-event-v1 native_sequence/native_update_id is null")
        try:
            events.append(
                L2BookEvent(
                    venue=plain["venue"][index],
                    market_type=plain["market_type"][index],
                    instrument=plain["instrument"][index],
                    native_symbol=plain["native_symbol"][index],
                    source_channel=plain["source_channel"][index],
                    acquisition_mode=plain["acquisition_mode"][index],
                    event_type=plain["event_type"][index],
                    exchange_ts=exchange,
                    provider_ts=None if provider_ns[index] is None else Instant(provider_ns[index]),
                    receive_ts=None if receive_ns[index] is None else Instant(receive_ns[index]),
                    source_depth_limit=plain["source_depth_limit"][index],
                    native_sequence=str(sequence),
                    native_prev_sequence=plain["native_prev_sequence"][index],
                    native_update_id=str(update_id),
                    continuity_token=plain["continuity_token"][index],
                    bids=_levels(plain["bids"][index], "bid", "bids"),
                    asks=_levels(plain["asks"][index], "ask", "asks"),
                )
            )
        except InvalidRequest as exc:
            raise DataIntegrityError(f"l2-book-event-v1 row is not a valid event: {exc}") from exc
    return tuple(events)


def _scan_l2_book_event_v1(
    path: str | Path,
    start: Instant | None,
    end: Instant | None,
    batch_size: int,
) -> Iterator[tuple[L2BookEvent, ...]]:
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    file_path = Path(path)
    if not file_path.is_file():
        raise StorageResolutionError("Parquet file does not exist")
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("pyarrow is required for l2-book-event-v1 reads") from exc

    try:
        dataset = ds.dataset(str(file_path), format="parquet")
        _check_schema(dataset.schema, pa, file_path.name)
        field = dataset.schema.field("exchange_ts")
        filter_expression = None
        if start is not None and end is not None and _arrow_boundary_safe(start, end, field.type, pa):
            exchange_field = ds.field("exchange_ts")
            filter_expression = (exchange_field >= pa.scalar(start.to_datetime(), type=field.type)) & (
                exchange_field < pa.scalar(end.to_datetime(), type=field.type)
            )
        # Sequential, one-file, one-batch read-ahead: physical order is the
        # producer's order and must never become an incidental scheduling
        # artifact (same rule as the trade-v1 scan).
        scanner = dataset.scanner(
            columns=list(L2_COLUMNS),
            filter=filter_expression,
            batch_size=batch_size,
            batch_readahead=1,
            fragment_readahead=1,
            use_threads=False,
        )
        for arrow_batch in scanner.to_batches():
            events = _events_from_batch(arrow_batch, start=start, end=end, pa=pa, pc=pc)
            if events:
                yield events
    except (DataIntegrityError, StorageResolutionError):
        raise
    except Exception as exc:
        raise DataIntegrityError(f"cannot read l2-book-event-v1 Parquet: {file_path.name}") from exc


def scan_l2_book_event_v1(
    path: str | Path,
    start: Instant,
    end: Instant,
    batch_size: int = 65_536,
) -> Iterator[tuple[L2BookEvent, ...]]:
    """Stream one bounded read of a canonical L2 file, ``[start, end)`` on ``exchange_ts``."""

    yield from _scan_l2_book_event_v1(path, start, end, batch_size)


def scan_l2_book_event_v1_all(
    path: str | Path,
    *,
    batch_size: int = 65_536,
) -> Iterator[tuple[L2BookEvent, ...]]:
    """Stream every event in physical order, with no temporal filter."""

    yield from _scan_l2_book_event_v1(path, None, None, batch_size)


def read_l2_book_event_v1(path: str | Path, start: Instant, end: Instant) -> list[L2BookEvent]:
    """Materialize one bounded read by draining :func:`scan_l2_book_event_v1`."""

    events: list[L2BookEvent] = []
    for batch in scan_l2_book_event_v1(path, start, end):
        events.extend(batch)
    return events


__all__ = [
    "L2_BOOK_EVENT_SCHEMA_ID",
    "L2_COLUMNS",
    "L2_CONTENT_HASH_DOMAIN_TAG",
    "L2_CONTENT_HASH_IDENTITY",
    "L2_NULLABLE_COLUMNS",
    "L2ContentHasher",
    "UINT64_MAX",
    "canonical_uint64",
    "l2_book_event_arrow_schema",
    "l2_canonical_content_hash_v1",
    "read_l2_book_event_v1",
    "scan_l2_book_event_v1",
    "scan_l2_book_event_v1_all",
]
