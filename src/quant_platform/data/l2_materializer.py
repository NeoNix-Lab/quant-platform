"""Canonical ``l2-book-event-v1`` Parquet materialization (ADR-0070 §3-§4, A14b).

A single-pass, bounded-memory sibling of :func:`materialize_trade_v1`.  The
input must already be in the provider's total order; the materializer verifies
that order while streaming and never sorts or repairs.  Row groups are written
incrementally, so memory is bounded by the row-group size, not by the day.  The
target is replaced atomically only after a complete file and its SHA-256 exist.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from ..ordering import L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1, OrderingProvider
from .l2_parquet import (
    L2_BOOK_EVENT_SCHEMA_ID,
    L2_COLUMNS,
    L2ContentHasher,
    canonical_uint64,
    l2_book_event_arrow_schema,
)
from .materializer import (
    ParquetMaterialization,
    _remove_temporary_file,
    _validate_compression,
    physical_artifact_sha256,
)
from .models import DataIntegrityError, DatasetIdentity, InvalidRequest, L2BookEvent

_VENUE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")


def materialize_l2_book_event_v1(
    path: str | Path,
    events: Iterable[L2BookEvent],
    *,
    dataset_identity: DatasetIdentity,
    ordering_provider: OrderingProvider,
    compression: str | None = "zstd",
    row_group_size: int = 65_536,
) -> ParquetMaterialization:
    """Write one ordered, lossless canonical ``l2-book-event-v1`` Parquet artifact.

    Returns the same :class:`ParquetMaterialization` evidence as the trade path,
    so ``emit_partition_manifest`` accepts it unchanged.  ``canonical_content_hash_v1``
    is the L2 order-sensitive content hash, computed incrementally.
    """

    target = Path(path)
    if target.suffix.lower() != ".parquet":
        raise InvalidRequest("canonical l2-book-event-v1 materialization requires a .parquet path")
    if not isinstance(row_group_size, int) or isinstance(row_group_size, bool) or row_group_size < 1:
        raise InvalidRequest("row_group_size must be a positive integer")
    _validate_identity(dataset_identity)
    if not isinstance(ordering_provider, OrderingProvider):
        raise InvalidRequest("ordering_provider must be an OrderingProvider")
    if not ordering_provider.applies(dataset_identity):
        raise InvalidRequest("ordering provider does not apply to the dataset identity")
    if not ordering_provider.satisfies(L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1):
        raise InvalidRequest(
            "ordering provider does not satisfy the l2-book-event-v1 canonical total order"
        )

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("pyarrow is required for l2-book-event-v1 materialization") from exc

    schema = l2_book_event_arrow_schema(pa)
    _validate_compression(schema.empty_table(), compression)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)

    hasher = L2ContentHasher()
    first_ts = last_ts = None
    previous_key: tuple[Any, ...] | None = None
    buffer: list[L2BookEvent] = []
    writer = None
    try:
        writer = pq.ParquetWriter(
            str(temporary),
            schema,
            compression=compression,
            version="2.6",
            use_dictionary=True,
            write_statistics=True,
            use_deprecated_int96_timestamps=False,
        )

        def flush() -> None:
            if buffer:
                writer.write_table(_table(buffer, schema, pa), row_group_size=row_group_size)
                buffer.clear()

        for event in events:
            _validate_event(event, dataset_identity)
            key = ordering_provider.key(event)
            if previous_key is not None and key <= previous_key:
                raise DataIntegrityError(
                    "l2-book-event-v1 input is not in strictly increasing ordering-key order "
                    f"({'duplicate' if key == previous_key else 'regression'} at {key})"
                )
            previous_key = key
            hasher.update(event)
            if first_ts is None:
                first_ts = event.exchange_ts
            last_ts = event.exchange_ts
            buffer.append(event)
            if len(buffer) >= row_group_size:
                flush()
        flush()
        writer.close()
        writer = None
        artifact_sha256 = physical_artifact_sha256(temporary)
        file_size_bytes = temporary.stat().st_size
        os.replace(temporary, target)
    except BaseException:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # pragma: no cover - best effort before cleanup
                pass
        _remove_temporary_file(temporary)
        raise

    return ParquetMaterialization(
        path=target,
        dataset_identity=dataset_identity,
        file_size_bytes=file_size_bytes,
        row_count=hasher.count,
        sha256=artifact_sha256,
        canonical_content_hash_v1=hasher.hexdigest_identity(),
        first_exchange_ts=first_ts,
        last_exchange_ts=last_ts,
    )


def _validate_identity(identity: DatasetIdentity) -> None:
    if not isinstance(identity, DatasetIdentity):
        raise InvalidRequest("dataset_identity must be DatasetIdentity")
    if (
        identity.layer != "canonical"
        or identity.dataset_kind != "l2"
        or identity.record_schema_id != L2_BOOK_EVENT_SCHEMA_ID
    ):
        raise InvalidRequest("L2 materializer requires a canonical/l2/l2-book-event-v1 identity")


def _validate_event(event: Any, identity: DatasetIdentity) -> None:
    if not isinstance(event, L2BookEvent):
        raise DataIntegrityError("canonical l2-book-event-v1 materialization requires L2BookEvent values")
    if not _VENUE.fullmatch(event.venue):
        raise DataIntegrityError("l2-book-event-v1 venue is not canonical")
    if event.venue != identity.venue or event.instrument != identity.instrument:
        raise DataIntegrityError("l2-book-event-v1 record identity does not match the dataset identity")
    canonical_uint64(event.native_sequence, "native_sequence")
    canonical_uint64(event.native_update_id, "native_update_id")


def _table(events: list[L2BookEvent], schema: Any, pa: Any) -> Any:
    def level(change: Any) -> dict[str, Any]:
        return {
            "price": change.price,
            "size": change.size,
            "action": change.action,
            "order_count": change.order_count,
        }

    def stamp(value: Any) -> int | None:
        return None if value is None else value.epoch_ns

    columns: dict[str, Any] = {}
    for name in L2_COLUMNS:
        field_type = schema.field(name).type
        if name in ("exchange_ts", "provider_ts", "receive_ts"):
            raw = [stamp(getattr(event, name)) for event in events]
            columns[name] = pa.array(raw, type=pa.int64()).cast(field_type)
        elif name in ("native_sequence", "native_update_id"):
            columns[name] = pa.array(
                [int(getattr(event, name)) for event in events], type=field_type
            )
        elif name in ("bids", "asks"):
            columns[name] = pa.array(
                [[level(change) for change in getattr(event, name)] for event in events],
                type=field_type,
            )
        else:
            columns[name] = pa.array([getattr(event, name) for event in events], type=field_type)
    return pa.Table.from_arrays([columns[name] for name in L2_COLUMNS], schema=schema)


__all__ = ["materialize_l2_book_event_v1"]
