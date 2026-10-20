"""Bybit public trade archive: the historical source of the allow-list (ADR-0074 Amendment 1).

One gzip CSV per instrument and UTC day, ``https://public.bybit.com/trading/<INSTRUMENT>/<INSTRUMENT><YYYY-MM-DD>.csv.gz``. This
module reads a **local** copy of one such file and maps it to canonical ``trade-v1``; it makes no network call. Every check is
fail-closed: a header, a row, a timestamp, a side, a symbol or a decimal it does not recognize refuses the whole day.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
from pathlib import Path
import re

from ..data.models import DatasetIdentity, Instant, TradeRecord
from .bybit import BYBIT_ARCHIVE_SOURCE_PROFILE_V1, BYBIT_LINEAR_TRADE_INSTRUMENTS_V1
from .bybit_historical import _contract_patterns


BYBIT_ARCHIVE_SOURCE_SEMANTICS_V1, BYBIT_ARCHIVE_MAPPING_V1 = BYBIT_ARCHIVE_SOURCE_PROFILE_V1
BYBIT_ARCHIVE_URL_TEMPLATE = "https://public.bybit.com/trading/{instrument}/{instrument}{day}.csv.gz"
VENUE = "bybit"

# The two headers observed in the archive (A1.1); any other header is a new format and refuses the day (A1.2 item 3).
_HEADER_V1 = ("timestamp", "symbol", "side", "size", "price", "tickDirection", "trdMatchID", "grossValue", "homeNotional",
              "foreignNotional")
_HEADER_V1_RPI = _HEADER_V1 + ("RPI",)
BYBIT_ARCHIVE_HEADERS_V1 = (_HEADER_V1, _HEADER_V1_RPI)

_TIMESTAMP = re.compile(r"([0-9]+)(?:\.([0-9]{1,9}))?")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SIDES = {"Buy": "buy", "Sell": "sell"}
_DAY_NS = 86_400 * 1_000_000_000


class BybitArchiveSourceError(Exception):
    """The archive file cannot be mapped; the day is refused. ``line`` is the 1-based CSV line when a row is at fault."""

    def __init__(self, message: str, *, line: int | None = None, field: str | None = None):
        self.line = line
        self.field = field
        where = "" if line is None else f" (line {line}" + ("" if field is None else f", {field}") + ")"
        super().__init__(message + where)


@dataclass(frozen=True, slots=True)
class BybitArchiveDayEvidence:
    """What the coverage manifest records about the source file (A1.2 item 6)."""

    instrument: str
    day: str
    url: str
    file_name: str
    file_sha256: str
    file_size_bytes: int
    row_count: int

    @property
    def detail(self) -> str:
        """Stable human-readable detail; it is not a production input parser."""

        return (
            f"source_semantics={BYBIT_ARCHIVE_SOURCE_SEMANTICS_V1};mapping={BYBIT_ARCHIVE_MAPPING_V1};"
            f"url={self.url};file={self.file_name};file_sha256={self.file_sha256};"
            f"file_size_bytes={self.file_size_bytes};rows={self.row_count}"
        )


@dataclass(frozen=True, slots=True)
class BybitArchiveDay:
    records: tuple[TradeRecord, ...]
    evidence: BybitArchiveDayEvidence


def bybit_archive_dataset_identity(instrument: str) -> DatasetIdentity:
    _require_instrument(instrument)
    return DatasetIdentity("canonical", "trades", VENUE, instrument, "trade-v1")


def bybit_archive_file_name(instrument: str, day: str) -> str:
    _require_instrument(instrument)
    _require_day(day)
    return f"{instrument}{day}.csv.gz"


def bybit_archive_url(instrument: str, day: str) -> str:
    _require_instrument(instrument)
    _require_day(day)
    return BYBIT_ARCHIVE_URL_TEMPLATE.format(instrument=instrument, day=day)


def read_bybit_archive_day(path: str | Path, *, instrument: str, day: str) -> BybitArchiveDay:
    """Map one local archive file to the day's canonical records, ordered by ``(exchange_ts, trade_id)``."""

    path = Path(path)
    expected_name = bybit_archive_file_name(instrument, day)
    if path.name != expected_name:
        raise BybitArchiveSourceError(f"archive file is named {path.name!r}, expected {expected_name!r}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BybitArchiveSourceError(f"cannot read the archive file: {exc}") from exc
    try:
        text = gzip.decompress(raw).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise BybitArchiveSourceError(f"archive file is not a gzip UTF-8 CSV: {exc}") from exc

    start_ns = _day_start_ns(day)
    end_ns = start_ns + _DAY_NS
    positive_decimal, _ = _contract_patterns()
    reader = csv.reader(io.StringIO(text, newline=""))
    header = tuple(next(reader, ()))
    if header not in BYBIT_ARCHIVE_HEADERS_V1:
        raise BybitArchiveSourceError(f"unknown archive header {list(header)!r}")
    width = len(header)
    records: list[TradeRecord] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        if len(row) != width:
            raise BybitArchiveSourceError(f"row has {len(row)} fields, header has {width}", line=line)
        timestamp, symbol, side, size, price, trade_id = row[0], row[1], row[2], row[3], row[4], row[6]
        if symbol != instrument:
            raise BybitArchiveSourceError(f"symbol={symbol!r}, expected {instrument!r}", line=line, field="symbol")
        exchange_ns = _exchange_ns(timestamp, line)
        if not start_ns <= exchange_ns < end_ns:
            raise BybitArchiveSourceError(f"timestamp {timestamp!r} is outside the UTC day {day}", line=line, field="timestamp")
        if side not in _SIDES:
            raise BybitArchiveSourceError(f"side={side!r} is not Buy or Sell", line=line, field="side")
        for name, value in (("price", price), ("size", size)):
            if positive_decimal.fullmatch(value) is None:
                raise BybitArchiveSourceError(f"{name}={value!r} is not a canonical positive decimal", line=line, field=name)
        if not trade_id:
            raise BybitArchiveSourceError("trdMatchID is empty", line=line, field="trdMatchID")
        if trade_id in seen:
            raise BybitArchiveSourceError(f"trdMatchID {trade_id!r} repeats within the day", line=line, field="trdMatchID")
        seen.add(trade_id)
        records.append(TradeRecord(VENUE, instrument, Instant(exchange_ns), price, size, _SIDES[side], None, trade_id, None))
    # The file's row order is not relied on (A1.2 item 5): the canonical key orders the day.
    records.sort(key=lambda record: (record.exchange_ts.epoch_ns, record.trade_id))
    evidence = BybitArchiveDayEvidence(
        instrument=instrument,
        day=day,
        url=bybit_archive_url(instrument, day),
        file_name=expected_name,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        file_size_bytes=len(raw),
        row_count=len(records),
    )
    return BybitArchiveDay(tuple(records), evidence)


def _exchange_ns(text: str, line: int) -> int:
    """Decimal seconds, truncated to milliseconds (A1.2 item 4): the precision of the SQLite and live sources."""

    match = _TIMESTAMP.fullmatch(text)
    if match is None:
        raise BybitArchiveSourceError(f"timestamp {text!r} is not decimal seconds", line=line, field="timestamp")
    milliseconds = ((match.group(2) or "") + "000")[:3]
    return (int(match.group(1)) * 1_000 + int(milliseconds)) * 1_000_000


def _day_start_ns(day: str) -> int:
    moment = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    return int(moment.timestamp()) * 1_000_000_000


def _require_instrument(instrument: str) -> None:
    if instrument not in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
        raise BybitArchiveSourceError(f"instrument {instrument!r} is not in the Bybit linear allow-list")


def _require_day(day: str) -> None:
    if not isinstance(day, str) or _DAY.fullmatch(day) is None:
        raise BybitArchiveSourceError(f"day {day!r} is not YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise BybitArchiveSourceError(f"day {day!r} is not a calendar date") from exc


__all__ = [
    "BYBIT_ARCHIVE_HEADERS_V1",
    "BYBIT_ARCHIVE_MAPPING_V1",
    "BYBIT_ARCHIVE_SOURCE_SEMANTICS_V1",
    "BYBIT_ARCHIVE_URL_TEMPLATE",
    "BybitArchiveDay",
    "BybitArchiveDayEvidence",
    "BybitArchiveSourceError",
    "bybit_archive_dataset_identity",
    "bybit_archive_file_name",
    "bybit_archive_url",
    "read_bybit_archive_day",
]
