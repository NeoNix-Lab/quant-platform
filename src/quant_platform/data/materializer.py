"""Canonical ``trade-v1`` Parquet materialization for conformity v1."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import gc
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from ..ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1
from .models import (
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    InvalidRequest,
    StorageResolutionError,
    TradeRecord,
    canonical_content_hash_v1,
)
from .parquet import _COLUMNS, _DIGITS, _OPTIONAL, _POSITIVE_DECIMAL

_VENUE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
EligibilityValidator = Callable[[DatasetIdentity, tuple[TradeRecord, ...]], None]
_TEMPORARY_CLEANUP_RETRIES = 10
_TEMPORARY_CLEANUP_DELAY_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class ParquetMaterialization:
    """Evidence produced when one canonical partition is sealed to Parquet."""

    path: Path
    dataset_identity: DatasetIdentity
    file_size_bytes: int
    row_count: int
    sha256: str
    canonical_content_hash_v1: str
    first_exchange_ts: Instant | None
    last_exchange_ts: Instant | None

    @property
    def physical_artifact_hash(self) -> str:
        return self.sha256

    @property
    def content_sha256(self) -> str:
        return self.sha256


def materialize_trade_v1(
    path: str | Path,
    records: Iterable[TradeRecord],
    *,
    dataset_identity: DatasetIdentity,
    ordering_provider: OrderingProvider,
    eligibility_validator: EligibilityValidator | None = None,
    compression: str | None = "zstd",
    row_group_size: int = 65_536,
) -> ParquetMaterialization:
    """Write one ordered, lossless canonical ``trade-v1`` Parquet artifact.

    Source-specific publication eligibility is injected; this shared layer
    owns only generic canonical validation, physical ordering and evidence.
    The final path is replaced atomically only after a complete Parquet file
    and its physical hash exist.
    """

    target = Path(path)
    if target.suffix.lower() != ".parquet":
        raise InvalidRequest("canonical trade-v1 materialization requires a .parquet path")
    if not isinstance(row_group_size, int) or isinstance(row_group_size, bool) or row_group_size < 1:
        raise InvalidRequest("row_group_size must be a positive integer")
    _validate_identity(dataset_identity)
    if not isinstance(ordering_provider, OrderingProvider):
        raise InvalidRequest("ordering_provider must be an OrderingProvider")
    if not ordering_provider.applies(dataset_identity):
        raise InvalidRequest("ordering provider does not apply to the dataset identity")
    if not ordering_provider.satisfies(TRADES_CANONICAL_TOTAL_ORDER_V1):
        raise InvalidRequest("ordering provider does not satisfy the trade-v1 canonical total order")

    canonical = tuple(_validate_record(record, dataset_identity) for record in records)
    if eligibility_validator is not None:
        eligibility_validator(dataset_identity, canonical)

    keyed = [(ordering_provider.key(record), record) for record in canonical]
    keyed.sort(key=lambda item: item[0])
    if any(keyed[index - 1][0] == keyed[index][0] for index in range(1, len(keyed))):
        raise DataIntegrityError("trade-v1 ordering provider produced a duplicate ordering key")
    ordered = tuple(record for _, record in keyed)

    semantic_hash = canonical_content_hash_v1(ordered)
    table = _table(ordered)
    _validate_compression(table, compression)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        import pyarrow.parquet as pq

        pq.write_table(
            table,
            str(temporary),
            compression=compression,
            row_group_size=row_group_size,
            version="2.6",
            coerce_timestamps=None,
            allow_truncated_timestamps=False,
            use_deprecated_int96_timestamps=False,
            use_dictionary=True,
            write_statistics=True,
        )
        artifact_sha256 = physical_artifact_sha256(temporary)
        file_size_bytes = temporary.stat().st_size
        os.replace(temporary, target)
    except Exception:
        _remove_temporary_file(temporary)
        raise

    return ParquetMaterialization(
        path=target,
        dataset_identity=dataset_identity,
        file_size_bytes=file_size_bytes,
        row_count=len(ordered),
        sha256=artifact_sha256,
        canonical_content_hash_v1=semantic_hash,
        first_exchange_ts=ordered[0].exchange_ts if ordered else None,
        last_exchange_ts=ordered[-1].exchange_ts if ordered else None,
    )


def physical_artifact_sha256(path: str | Path) -> str:
    """Return PhysicalArtifactHash: SHA-256 of the exact artifact bytes."""

    file_path = Path(path)
    if not file_path.is_file():
        raise StorageResolutionError("physical artifact does not exist")
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_temporary_file(path: Path) -> None:
    """Best-effort bounded cleanup for a failed write temporary."""

    for attempt in range(_TEMPORARY_CLEANUP_RETRIES + 1):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            # Pyarrow can release its native handle slightly after raising,
            # especially on Windows.  A bounded retry window lets that handle
            # close without ever replacing the original materialization error.
            gc.collect()
            if attempt == _TEMPORARY_CLEANUP_RETRIES:
                return
            time.sleep(_TEMPORARY_CLEANUP_DELAY_SECONDS)


def _validate_compression(table: Any, compression: str | None) -> None:
    """Reject unsupported codecs before creating a temporary artifact."""

    if compression is None:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    # ParquetWriter otherwise creates the target-side temporary file before
    # rejecting an unsupported codec.  Preflight keeps that known failure from
    # leaking a native file handle on Windows, while retaining ParquetWriter's
    # original exception type and message.
    sink = pa.BufferOutputStream()
    writer = None
    try:
        writer = pq.ParquetWriter(
            sink,
            table.schema,
            compression=compression,
            version="2.6",
            use_dictionary=True,
            write_statistics=True,
            use_deprecated_int96_timestamps=False,
        )
    finally:
        if writer is not None:
            writer.close()


def _validate_identity(identity: DatasetIdentity) -> None:
    if not isinstance(identity, DatasetIdentity):
        raise InvalidRequest("dataset_identity must be DatasetIdentity")
    if (
        identity.layer != "canonical"
        or identity.dataset_kind != "trades"
        or identity.record_schema_id != "trade-v1"
    ):
        raise InvalidRequest("materializer requires canonical/trades/trade-v1 identity")


def _validate_record(record: TradeRecord, identity: DatasetIdentity) -> TradeRecord:
    if not isinstance(record, TradeRecord):
        raise DataIntegrityError("canonical trade-v1 materialization requires TradeRecord values")
    if not isinstance(record.venue, str) or not _VENUE.fullmatch(record.venue):
        raise DataIntegrityError("trade-v1 venue is not canonical")
    if not isinstance(record.instrument, str) or not re.search(r"\S", record.instrument):
        raise DataIntegrityError("trade-v1 instrument is invalid")
    if record.venue != identity.venue or record.instrument != identity.instrument:
        raise DataIntegrityError("trade-v1 record identity does not match the dataset identity")
    try:
        exchange_ts = Instant.parse(record.exchange_ts)
        receive_ts = None if record.receive_ts is None else Instant.parse(record.receive_ts)
    except (InvalidRequest, TypeError) as exc:
        raise DataIntegrityError("trade-v1 timestamp is invalid") from exc
    price = _decimal(record.price, "price")
    size = _decimal(record.size, "size")
    if record.aggressor_side not in {"buy", "sell", "unknown"}:
        raise DataIntegrityError("trade-v1 aggressor_side is outside its enum")
    trade_id = _optional(record.trade_id, "trade_id")
    sequence = _optional(record.sequence, "sequence")
    if sequence is not None and not _DIGITS.fullmatch(sequence):
        raise DataIntegrityError("trade-v1 sequence is not canonical digits")
    return TradeRecord(
        venue=record.venue,
        instrument=record.instrument,
        exchange_ts=exchange_ts,
        price=price,
        size=size,
        aggressor_side=record.aggressor_side,
        receive_ts=receive_ts,
        trade_id=trade_id,
        sequence=sequence,
    )


def _decimal(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _POSITIVE_DECIMAL.fullmatch(value):
        raise DataIntegrityError(f"trade-v1 {field} is not a canonical positive decimal string")
    return value


def _optional(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise DataIntegrityError(f"trade-v1 {field} must be null or a non-empty string")
    return value


def _table(records: tuple[TradeRecord, ...]) -> Any:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("pyarrow is required for trade-v1 materialization") from exc

    timestamp = pa.timestamp("ns", tz="UTC")
    field_types = {
        "venue": pa.string(),
        "instrument": pa.string(),
        "exchange_ts": timestamp,
        "price": pa.string(),
        "size": pa.string(),
        "aggressor_side": pa.string(),
        # String fallback is contract-valid and preserves nanoseconds through
        # the existing reader, whose Python datetime path is microsecond-only.
        "receive_ts": pa.string(),
        "trade_id": pa.string(),
        "sequence": pa.string(),
    }
    schema = pa.schema(
        [pa.field(name, field_types[name], nullable=name in _OPTIONAL) for name in _COLUMNS]
    )
    values = {
        "venue": [record.venue for record in records],
        "instrument": [record.instrument for record in records],
        "price": [record.price for record in records],
        "size": [record.size for record in records],
        "aggressor_side": [record.aggressor_side for record in records],
        "receive_ts": [
            None if record.receive_ts is None else record.receive_ts.isoformat()
            for record in records
        ],
        "trade_id": [record.trade_id for record in records],
        "sequence": [record.sequence for record in records],
    }
    raw_exchange = pa.array([record.exchange_ts.epoch_ns for record in records], type=pa.int64())
    exchange = pc.cast(raw_exchange, timestamp)
    arrays = [
        exchange if name == "exchange_ts" else pa.array(values[name], type=field_types[name])
        for name in _COLUMNS
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


__all__ = [
    "EligibilityValidator",
    "ParquetMaterialization",
    "materialize_trade_v1",
    "physical_artifact_sha256",
]
