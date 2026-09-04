"""Production seam for the first Bybit historical trade source vertical.

This module owns only the source-facing semantics: read-only SQLite access,
the Bybit day extract, strict source validation, and mapping to ``TradeRecord``.
Artifact serialization remains a tool concern.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from ..data.models import Instant, TradeRecord


BYBIT_HISTORICAL_SOURCE_SEMANTICS_V1 = "bybit-public-trades-sqlite-v1"
BYBIT_HISTORICAL_MAPPING_V1 = "bybit-sqlite-day-extract-v1"
SUPPORTED_CATEGORY = "linear"
SUPPORTED_SYMBOL = "BTCUSDT"
SUPPORTED_VENUE = "bybit"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UTC_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?Z$"
)
_SELECT_SQL = """
SELECT category, symbol, trade_id, trade_time_ms, trade_time_utc, side, size, price
  FROM trades
 WHERE category = ?
   AND symbol = ?
   AND trade_time_ms >= ?
   AND trade_time_ms <  ?
 ORDER BY trade_time_ms, trade_id
"""
_FINGERPRINT_DOMAIN = b"quant-platform/bybit-historical-extract-v1\x00"


class BybitHistoricalSourceError(Exception):
    """A source or mapping failure with row identity and field diagnostics."""

    def __init__(self, message: str, *, trade_id=None, field: str | None = None):
        self.trade_id = trade_id
        self.field = field
        context = []
        if trade_id is not None:
            context.append(f"trade_id={trade_id!r}")
        if field is not None:
            context.append(f"field={field!r}")
        suffix = f"  [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)


@dataclass(frozen=True, slots=True)
class BybitHistoricalTradeRow:
    """The eight source-native values used by the historical mapping."""

    category: str
    symbol: str
    trade_id: str
    trade_time_ms: int
    trade_time_utc: str
    side: str
    size: str
    price: str


@dataclass(frozen=True, slots=True)
class BybitHistoricalExtractEvidence:
    """Bounded evidence for one deterministic source extraction."""

    source_semantics_id: str
    mapping_id: str
    requested_day: str
    requested_start_ms: int
    requested_end_ms: int
    source_row_count: int
    source_fingerprint_sha256: str

    @property
    def detail(self) -> str:
        """Stable human-readable detail; it is not a production input parser."""

        return (
            f"source_semantics={self.source_semantics_id};"
            f"mapping={self.mapping_id};day={self.requested_day};"
            f"start_ms={self.requested_start_ms};end_ms={self.requested_end_ms};"
            f"rows={self.source_row_count};"
            f"fingerprint_sha256={self.source_fingerprint_sha256}"
        )


class BybitHistoricalExtractAccumulator:
    """O(1)-state observer for the source-row count and ordered fingerprint.

    The accumulator is a small state machine: ``READING`` until the extract
    iterator reaches genuine source exhaustion, ``EXHAUSTED`` afterwards.
    Evidence exists only in the second state, because a partially consumed
    extract cannot support a ``deterministic_source_extract`` claim: its row
    count and fingerprint would describe a prefix while asserting an
    exhaustive read.
    """

    __slots__ = ("_digest", "_row_count", "_exhausted")

    def __init__(self) -> None:
        self._digest = hashlib.sha256(_FINGERPRINT_DOMAIN)
        self._row_count = 0
        self._exhausted = False

    def observe(self, row: BybitHistoricalTradeRow) -> None:
        if self._exhausted:
            raise BybitHistoricalSourceError(
                "source extract is already exhausted; its evidence is final"
            )
        self._digest.update(_frame_row(row))
        self._row_count += 1

    def _mark_source_exhausted(self) -> None:
        """Record that the source cursor genuinely returned no further rows.

        Only :func:`iter_bybit_historical_trade_rows` calls this, and only on
        the normal end-of-cursor path.  An early ``break``, an abandoned
        generator or a consumer exception never reaches it, so a truncated
        read can never present itself as a complete extract.
        """

        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    @property
    def row_count(self) -> int:
        return self._row_count

    def evidence(self, requested_day: str, start_ms: int, end_ms: int) -> BybitHistoricalExtractEvidence:
        if not self._exhausted:
            raise BybitHistoricalSourceError(
                "source extract evidence requires natural exhaustion of the "
                "extract iterator; the current read is still incomplete"
            )
        return BybitHistoricalExtractEvidence(
            source_semantics_id=BYBIT_HISTORICAL_SOURCE_SEMANTICS_V1,
            mapping_id=BYBIT_HISTORICAL_MAPPING_V1,
            requested_day=requested_day,
            requested_start_ms=start_ms,
            requested_end_ms=end_ms,
            source_row_count=self._row_count,
            source_fingerprint_sha256=self._digest.hexdigest(),
        )


def _frame_bytes(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _frame_value(value) -> bytes:
    if isinstance(value, str):
        kind, payload = b"s", value.encode("utf-8")
    elif isinstance(value, int) and not isinstance(value, bool):
        kind, payload = b"i", str(value).encode("ascii")
    elif value is None:
        kind, payload = b"n", b""
    else:
        kind, payload = b"t", type(value).__name__.encode("utf-8")
    return _frame_bytes(kind) + _frame_bytes(payload)


def _frame_row(row: BybitHistoricalTradeRow) -> bytes:
    values = (
        row.category, row.symbol, row.trade_id, row.trade_time_ms,
        row.trade_time_utc, row.side, row.size, row.price,
    )
    return b"R" + _frame_bytes(len(values).to_bytes(4, "big")) + b"".join(
        _frame_value(value) for value in values
    )


def utc_day_bounds_ms(date_text: str) -> tuple[int, int]:
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise BybitHistoricalSourceError(
            f"invalid UTC date: {date_text!r}"
        ) from exc
    start_ms = (day - _EPOCH) // timedelta(milliseconds=1)
    end_ms = (day + timedelta(days=1) - _EPOCH) // timedelta(milliseconds=1)
    return start_ms, end_ms


def open_bybit_historical_source(sqlite_path) -> sqlite3.Connection:
    """Open the source SQLite database in read-only mode."""

    path = Path(sqlite_path)
    if not path.is_file():
        raise BybitHistoricalSourceError(f"SQLite source not found: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BybitHistoricalSourceError(
            f"could not open SQLite source read-only: {path}"
        ) from exc


# ---------------------------------------------------------------------------
# Legacy archive access
#
# The first vertical's legacy archive carries a WAL header and sits in a
# directory the reader cannot write.  The ordinary opener above cannot read it:
# SQLite wants to create the -wal/-shm sidecars and the first query fails with
# "attempt to write a readonly database".  ``immutable=1`` tells SQLite the
# file will not change underneath it, so it reads the main database file alone
# and creates nothing.
#
# That option deliberately does NOT exist on ``open_bybit_historical_source``.
# It is only sound while the file really is unchanging, which is an accepted
# operational precondition of *this* legacy archive for the lifetime of *one*
# finite read -- never a general property of historical sources.  Keeping it
# behind separate, explicitly named entry points is what stops it becoming
# default SQLite behaviour somewhere else.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BybitHistoricalSourceIdentity:
    """What the platform can portably observe about a legacy source file.

    This is a cheap change detector for one finite read.  It certifies
    nothing about the archive: it says only whether the file the read started
    on is still the file the read finished on.
    """

    path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int | None
    inode: int | None

    @classmethod
    def capture(cls, sqlite_path) -> "BybitHistoricalSourceIdentity":
        path = Path(sqlite_path)
        try:
            stat = path.stat()
        except OSError as exc:
            raise BybitHistoricalSourceError(
                f"could not read the legacy source identity: {path}"
            ) from exc
        return cls(
            path=str(path),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
            device=stat.st_dev or None,
            inode=stat.st_ino or None,
        )

    def drift_against(self, other: "BybitHistoricalSourceIdentity") -> tuple[str, ...]:
        """Observable differences; device and inode only where both are known."""

        drift = []
        for field in ("path", "size", "mtime_ns", "ctime_ns"):
            mine, theirs = getattr(self, field), getattr(other, field)
            if mine != theirs:
                drift.append(f"{field}: {mine!r} -> {theirs!r}")
        for field in ("device", "inode"):
            mine, theirs = getattr(self, field), getattr(other, field)
            if mine is not None and theirs is not None and mine != theirs:
                drift.append(f"{field}: {mine!r} -> {theirs!r}")
        return tuple(drift)


def open_bybit_historical_legacy_source(sqlite_path) -> sqlite3.Connection:
    """Open the legacy Bybit archive read-only under the immutable access mode.

    Use this only for the legacy first-vertical archive.  Every other caller
    keeps :func:`open_bybit_historical_source` and its ordinary ``mode=ro``.
    """

    path = Path(sqlite_path)
    if not path.is_file():
        raise BybitHistoricalSourceError(f"SQLite source not found: {path}")
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BybitHistoricalSourceError(
            f"could not open the legacy SQLite source: {path}"
        ) from exc


@contextmanager
def bybit_historical_legacy_read(sqlite_path) -> Iterator[sqlite3.Connection]:
    """One finite legacy read that fails closed if the source changed.

    The identity check brackets the read because a source replaced or rewritten
    mid-read is exactly what would invalidate the immutable access precondition.
    A failure inside the read propagates unchanged: the drift check must not
    mask the original error, so it runs only once the read itself succeeded.
    """

    before = BybitHistoricalSourceIdentity.capture(sqlite_path)
    connection = open_bybit_historical_legacy_source(sqlite_path)
    try:
        yield connection
    finally:
        connection.close()
    drift = before.drift_against(BybitHistoricalSourceIdentity.capture(sqlite_path))
    if drift:
        raise BybitHistoricalSourceError(
            "the legacy source changed during the finite historical read: "
            + "; ".join(drift)
        )


def iter_bybit_historical_trade_rows(
    connection: sqlite3.Connection,
    start_ms: int,
    end_ms: int,
    *,
    category: str = SUPPORTED_CATEGORY,
    symbol: str = SUPPORTED_SYMBOL,
    batch_size: int = 10_000,
    accumulator: "BybitHistoricalExtractAccumulator | None" = None,
) -> Iterator[BybitHistoricalTradeRow]:
    """Stream one UTC half-open extract in deterministic source order.

    When an accumulator is supplied it observes every row and is marked
    exhausted only where the cursor itself reports no further rows.  The
    marking deliberately does not live in a ``finally``: closing or
    abandoning this generator must not make a partial read look complete.
    """

    if category != SUPPORTED_CATEGORY or symbol != SUPPORTED_SYMBOL:
        raise BybitHistoricalSourceError(
            "only category='linear' and symbol='BTCUSDT' are supported"
        )
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise BybitHistoricalSourceError("batch_size must be a positive integer")
    cursor = connection.cursor()
    cursor.arraysize = batch_size
    try:
        cursor.execute(_SELECT_SQL, (category, symbol, start_ms, end_ms))
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                # The single point where an extract becomes complete: the
                # source cursor, not a cleanup path, reported end of rows.
                if accumulator is not None:
                    accumulator._mark_source_exhausted()
                break
            for values in batch:
                row = BybitHistoricalTradeRow(*values)
                if accumulator is not None:
                    accumulator.observe(row)
                yield row
    except sqlite3.Error as exc:
        raise BybitHistoricalSourceError("Bybit historical source query failed") from exc
    finally:
        try:
            cursor.close()
        except sqlite3.Error:
            # The caller may close the read-only connection while a failed
            # consumer is unwinding the generator; no source error is hidden.
            pass


def _load_contract_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    schema_path = Path(__file__).resolve().parents[3] / "schemas" / "trade-v1.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        defs = schema["$defs"]
        return (
            re.compile(defs["positive_decimal"]["pattern"]),
            re.compile(schema["properties"]["venue"]["pattern"]),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BybitHistoricalSourceError(
            f"could not load frozen trade-v1 schema: {schema_path}"
        ) from exc


_CONTRACT_PATTERNS: tuple[re.Pattern[str], re.Pattern[str]] | None = None


def _contract_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    global _CONTRACT_PATTERNS
    if _CONTRACT_PATTERNS is None:
        _CONTRACT_PATTERNS = _load_contract_patterns()
    return _CONTRACT_PATTERNS


def _parse_source_utc(value, *, trade_id) -> int:
    if not isinstance(value, str):
        raise BybitHistoricalSourceError(
            f"trade_time_utc is not a string: {value!r}",
            trade_id=trade_id, field="trade_time_utc")
    match = _UTC_RE.fullmatch(value)
    if match is None:
        raise BybitHistoricalSourceError(
            f"trade_time_utc is not strict UTC RFC 3339: {value!r}",
            trade_id=trade_id, field="trade_time_utc")
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    fraction = match.group(7) or ""
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError as exc:
        raise BybitHistoricalSourceError(
            f"trade_time_utc is not a real date: {value!r}",
            trade_id=trade_id, field="trade_time_utc") from exc
    seconds_since_epoch = (moment - _EPOCH) // timedelta(seconds=1)
    return seconds_since_epoch * 1_000_000_000 + int(fraction.ljust(9, "0") or 0)


def parse_bybit_historical_utc_to_nanos(value: str) -> int:
    """Strict source timestamp parser exposed for seam-level verification."""

    return _parse_source_utc(value, trade_id=None)


def _validate_decimal(value, field: str, trade_id) -> str:
    if not isinstance(value, str):
        raise BybitHistoricalSourceError(
            f"{field} is not a decimal string: {value!r}",
            trade_id=trade_id, field=field)
    positive_decimal, _ = _contract_patterns()
    if positive_decimal.fullmatch(value) is None:
        raise BybitHistoricalSourceError(
            f"{field}={value!r} is not a canonical positive decimal",
            trade_id=trade_id, field=field)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise BybitHistoricalSourceError(
            f"{field}={value!r} is not a decimal",
            trade_id=trade_id, field=field) from exc
    if not amount.is_finite() or amount <= 0:
        raise BybitHistoricalSourceError(
            f"{field}={value!r} must be > 0", trade_id=trade_id, field=field)
    return value


def canonicalize_bybit_historical_trade_v1(
    row: BybitHistoricalTradeRow,
) -> TradeRecord:
    """Map one validated source row to the actual canonical domain model."""

    trade_id = row.trade_id
    if row.category != SUPPORTED_CATEGORY:
        raise BybitHistoricalSourceError(
            f"category={row.category!r}, expected {SUPPORTED_CATEGORY!r}",
            trade_id=trade_id, field="category")
    if row.symbol != SUPPORTED_SYMBOL:
        raise BybitHistoricalSourceError(
            f"symbol={row.symbol!r}, expected {SUPPORTED_SYMBOL!r}",
            trade_id=trade_id, field="symbol")
    if not isinstance(trade_id, str) or not trade_id:
        raise BybitHistoricalSourceError(
            f"trade_id missing or not a string: {trade_id!r}", field="trade_id")
    if not isinstance(row.trade_time_ms, int) or isinstance(row.trade_time_ms, bool):
        raise BybitHistoricalSourceError(
            f"trade_time_ms is not an integer: {row.trade_time_ms!r}",
            trade_id=trade_id, field="trade_time_ms")
    if not row.trade_time_utc:
        raise BybitHistoricalSourceError(
            "trade_time_utc missing", trade_id=trade_id, field="trade_time_utc")
    utc_nanos = _parse_source_utc(row.trade_time_utc, trade_id=trade_id)
    exchange_nanos = row.trade_time_ms * 1_000_000
    if utc_nanos != exchange_nanos:
        raise BybitHistoricalSourceError(
            "trade_time_ms and trade_time_utc represent different instants",
            trade_id=trade_id, field="trade_time_utc")
    try:
        aggressor_side = {"Buy": "buy", "Sell": "sell"}[row.side]
    except (KeyError, TypeError):
        raise BybitHistoricalSourceError(
            f"side={row.side!r} is not Buy or Sell",
            trade_id=trade_id, field="side") from None
    price = _validate_decimal(row.price, "price", trade_id)
    size = _validate_decimal(row.size, "size", trade_id)
    _, venue_pattern = _contract_patterns()
    if venue_pattern.fullmatch(SUPPORTED_VENUE) is None:
        raise BybitHistoricalSourceError(
            f"venue={SUPPORTED_VENUE!r} violates trade-v1", field="venue")
    return TradeRecord(
        venue=SUPPORTED_VENUE,
        instrument=row.symbol,
        exchange_ts=Instant(exchange_nanos),
        receive_ts=None,
        price=price,
        size=size,
        aggressor_side=aggressor_side,
        trade_id=trade_id,
        sequence=None,
    )
