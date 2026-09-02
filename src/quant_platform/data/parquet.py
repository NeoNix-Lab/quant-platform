"""Safe physical resolution and canonical ``trade-v1`` Parquet reads."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
from typing import Any, Iterator

from .models import DataIntegrityError, Instant, InvalidRequest, StorageResolutionError, TradeRecord

_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9._=%-]+(?:/[A-Za-z0-9._=%-]+)*$")
_REQUIRED = ("venue", "instrument", "exchange_ts", "price", "size", "aggressor_side")
_OPTIONAL = ("receive_ts", "trade_id", "sequence")
_COLUMNS = _REQUIRED + _OPTIONAL
_POSITIVE_DECIMAL = re.compile(r"^([1-9][0-9]*(\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$")
_DIGITS = re.compile(r"^(0|[1-9][0-9]*)$")


def resolve_partition_path(storage_root: str, dataset_rel_root: str, rel_path: str) -> Path:
    """Resolve catalog path components and reject traversal/absolute input."""
    root = Path(storage_root)
    if not root.is_absolute():
        raise StorageResolutionError("catalog storage root is not absolute")
    _validate_relative(dataset_rel_root, "dataset rel_root", allow_percent=True)
    _validate_relative(rel_path, "partition rel_path", allow_percent=False)
    root = root.resolve()
    candidate = (root / Path(*dataset_rel_root.split("/")) / Path(*rel_path.split("/"))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StorageResolutionError("resolved partition path escapes storage root") from exc
    if candidate.suffix.lower() != ".parquet":
        raise StorageResolutionError("DataGateway v1 requires a Parquet partition")
    if not candidate.is_file():
        raise StorageResolutionError(f"catalogued partition file is not readable: {candidate}")
    return candidate


def _scan_trade_v1(
    path: str | Path,
    start: Instant | None,
    end: Instant | None,
    batch_size: int = 65_536,
) -> Iterator[tuple[TradeRecord, ...]]:
    """Stream one canonical trade-v1 file as bounded logical record batches.

    Arrow row batches are consumed incrementally; the whole Parquet file is
    never converted to one in-memory table.  Python filtering remains
    authoritative for parsed timestamp comparison and half-open boundaries.
    Physical ordering is preserved exactly as stored and validated by the
    DataGateway scan path rather than repaired with a global sort.
    """

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
        raise RuntimeError("pyarrow is required for trade-v1 reads") from exc

    try:
        dataset = ds.dataset(str(file_path), format="parquet")
        names = set(dataset.schema.names)
        if any(column not in names for column in _REQUIRED):
            missing = sorted(set(_REQUIRED) - names)
            raise DataIntegrityError(f"trade-v1 Parquet is missing columns: {missing}")
        if any(column not in _COLUMNS for column in names):
            extra = sorted(names - set(_COLUMNS))
            raise DataIntegrityError(f"trade-v1 Parquet has unsupported columns: {extra}")

        columns = [column for column in _COLUMNS if column in names]
        filter_expression = None
        field = dataset.schema.field("exchange_ts")
        if (
            start is not None
            and end is not None
            and pa.types.is_timestamp(field.type)
            and _arrow_boundary_safe(start, end, field.type, pa)
        ):
            arrow_start = pa.scalar(start.to_datetime(), type=field.type)
            arrow_end = pa.scalar(end.to_datetime(), type=field.type)
            exchange_field = ds.field("exchange_ts")
            filter_expression = (
                (exchange_field >= arrow_start) & (exchange_field < arrow_end)
            ) | exchange_field.is_null()
        # The bounded read contract consumes the producer's physical order.
        # Keep this one-file scan sequential and cap Arrow read-ahead so
        # parallel scheduling cannot become an incidental ordering source or
        # multiply the caller-selected batch memory bound.
        scanner = dataset.scanner(
            columns=columns,
            filter=filter_expression,
            batch_size=batch_size,
            batch_readahead=1,
            fragment_readahead=1,
            use_threads=False,
        )
        for arrow_batch in scanner.to_batches():
            records = _trade_records_from_batch(
                arrow_batch,
                names=names,
                exchange_type=field.type,
                start=start,
                end=end,
                pa=pa,
                pc=pc,
            )
            if records:
                yield records
    except DataIntegrityError:
        raise
    except Exception as exc:
        raise DataIntegrityError(f"cannot read trade-v1 Parquet: {file_path.name}") from exc


def scan_trade_v1(
    path: str | Path,
    start: Instant,
    end: Instant,
    batch_size: int = 65_536,
) -> Iterator[tuple[TradeRecord, ...]]:
    """Stream one bounded canonical trade-v1 file read."""

    yield from _scan_trade_v1(path, start, end, batch_size)


def read_trade_v1(
    path: str | Path,
    start: Instant,
    end: Instant,
) -> list[TradeRecord]:
    """Materialize one trade-v1 file by draining :func:`scan_trade_v1`."""

    records: list[TradeRecord] = []
    for batch in scan_trade_v1(path, start, end):
        records.extend(batch)
    return records


def scan_trade_v1_all(
    path: str | Path,
    *,
    batch_size: int = 65_536,
) -> Iterator[tuple[TradeRecord, ...]]:
    """Stream every canonical row without applying a temporal filter.

    Certification uses this path to inspect the complete physical artifact.
    It preserves row order and the same bounded Arrow read-ahead as the
    consumer scan; it never sorts or repairs rows.
    """

    # The internal reader uses ``None`` boundaries only for this full-artifact
    # inspection path.  Existing bounded-read callers retain their original
    # required start/end contract.
    yield from _scan_trade_v1(path, None, None, batch_size)


def _trade_records_from_batch(
    batch: Any,
    *,
    names: set[str],
    exchange_type: Any,
    start: Instant | None,
    end: Instant | None,
    pa: Any,
    pc: Any,
) -> tuple[TradeRecord, ...]:
    timestamp_ns = _timestamp_values(_batch_column(batch, "exchange_ts"), exchange_type, pa, pc)
    values = {
        column: _column_values(batch, column, names=names, pa=pa, pc=pc)
        for column in _COLUMNS
        if column != "exchange_ts"
    }
    receive_values = values["receive_ts"]

    records: list[TradeRecord] = []
    for index in range(batch.num_rows):
        exchange_value = timestamp_ns[index]
        if exchange_value is None:
            raise DataIntegrityError("trade-v1 exchange_ts is null")
        exchange = Instant(exchange_value)

        if start is not None and end is not None and not (start <= exchange < end):
            continue
        try:
            receive = None if receive_values[index] is None else Instant.parse(receive_values[index])
            aggressor_side = _required_text(values["aggressor_side"][index], "aggressor_side")
            if aggressor_side not in {"buy", "sell", "unknown"}:
                raise DataIntegrityError("trade-v1 aggressor_side is outside its enum")
            sequence = _optional_text(values["sequence"][index])
            if sequence is not None and not _DIGITS.fullmatch(sequence):
                raise DataIntegrityError("trade-v1 sequence is not canonical digits")
            records.append(
                TradeRecord(
                    venue=_required_text(values["venue"][index], "venue"),
                    instrument=_required_text(values["instrument"][index], "instrument"),
                    exchange_ts=exchange,
                    price=_decimal_text(values["price"][index], "price"),
                    size=_decimal_text(values["size"][index], "size"),
                    aggressor_side=aggressor_side,
                    receive_ts=receive,
                    trade_id=_optional_text(values["trade_id"][index]),
                    sequence=sequence,
                )
            )
        except InvalidRequest as exc:
            raise DataIntegrityError("trade-v1 timestamp is invalid") from exc
    return tuple(records)


def _batch_column(batch: Any, name: str) -> Any:
    index = batch.schema.get_field_index(name)
    if index < 0:
        raise DataIntegrityError(f"trade-v1 Parquet batch is missing column: {name}")
    return batch.column(index)


def _column_values(batch: Any, name: str, *, names: set[str], pa: Any, pc: Any) -> list[Any]:
    if name not in names:
        return [None] * batch.num_rows
    column = _batch_column(batch, name)
    if name == "receive_ts" and pa.types.is_timestamp(column.type):
        return [
            None if value is None else Instant(value).isoformat()
            for value in _timestamp_values(column, column.type, pa, pc)
        ]
    # exchange_ts is handled through _timestamp_values above.  Avoiding
    # Array.to_pylist() for it is essential for nanosecond Arrow timestamps,
    # which PyArrow cannot always convert to Python datetime values.
    return column.to_pylist()


def _validate_relative(value: str, label: str, *, allow_percent: bool = True) -> None:
    if not isinstance(value, str) or not _SAFE_RELATIVE.fullmatch(value):
        raise StorageResolutionError(f"{label} must be a safe relative path")
    if any(component in {".", ".."} for component in value.split("/")):
        raise StorageResolutionError(f"{label} contains traversal")
    if "%2E" in value.upper():
        raise StorageResolutionError(f"{label} contains encoded traversal")
    if re.search(r"%(?![0-9A-F]{2})", value):
        raise StorageResolutionError(f"{label} contains a non-canonical escape")
    if not allow_percent and "%" in value:
        raise StorageResolutionError(f"{label} must not contain percent escapes")


def _timestamp_values(array: Any, data_type: Any, pa: Any, pc: Any) -> list[int | None]:
    if pa.types.is_timestamp(data_type):
        unit = data_type.unit
        raw = pc.cast(array, pa.int64()).to_pylist()
        factor = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}[unit]
        return [None if value is None else int(value) * factor for value in raw]
    return [None if value is None else Instant.parse(value).epoch_ns for value in array.to_pylist()]


def _arrow_boundary_safe(start: Instant, end: Instant, data_type: Any, pa: Any) -> bool:
    unit = data_type.unit
    factor = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}[unit]
    # Arrow pushdown must not round a nanosecond boundary inward: Python
    # filtering below is authoritative, so an unsafe pushdown could hide rows
    # before they can be checked.  Instant.to_datetime() is microsecond-
    # precision, so even a nanosecond Parquet field needs microsecond-aligned
    # boundaries before it can safely be converted to an Arrow scalar here.
    safe_factor = max(factor, 1_000)
    return start.epoch_ns % safe_factor == 0 and end.epoch_ns % safe_factor == 0


def _required_text(value: Any, field: str) -> str:
    if value is None or not isinstance(value, str) or not value:
        raise DataIntegrityError(f"trade-v1 {field} is null or not a string")
    return value


def _decimal_text(value: Any, field: str) -> str:
    if value is None or not isinstance(value, (str, Decimal)):
        raise DataIntegrityError(f"trade-v1 {field} is null")
    text = str(value)
    if not _POSITIVE_DECIMAL.fullmatch(text):
        raise DataIntegrityError(f"trade-v1 {field} is not a canonical positive decimal")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataIntegrityError("trade-v1 optional identifier is not a string")
    if value == "":
        raise DataIntegrityError("trade-v1 optional identifier is empty")
    return value


__all__ = ["read_trade_v1", "resolve_partition_path", "scan_trade_v1", "scan_trade_v1_all"]
