"""Production seam tests for the Bybit historical source vertical."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit import materialize_bybit_trade_v1  # noqa: E402
from quant_platform.source_adapters.bybit_historical import (  # noqa: E402
    BYBIT_HISTORICAL_MAPPING_V1,
    BYBIT_HISTORICAL_SOURCE_SEMANTICS_V1,
    BybitHistoricalExtractAccumulator,
    BybitHistoricalSourceError,
    BybitHistoricalSourceIdentity,
    BybitHistoricalTradeRow,
    bybit_historical_legacy_read,
    canonicalize_bybit_historical_trade_v1,
    iter_bybit_historical_trade_rows,
    open_bybit_historical_legacy_source,
    open_bybit_historical_source,
    parse_bybit_historical_utc_to_nanos,
    utc_day_bounds_ms,
)


DDL = """
CREATE TABLE trades (
    category TEXT NOT NULL, symbol TEXT NOT NULL, trade_id TEXT NOT NULL,
    trade_time_ms INTEGER NOT NULL, trade_time_utc TEXT NOT NULL,
    side TEXT NOT NULL, size TEXT NOT NULL, price TEXT NOT NULL,
    tick_direction TEXT, gross_value TEXT, home_notional TEXT,
    foreign_notional TEXT,
    PRIMARY KEY (category, symbol, trade_id));
CREATE INDEX idx_trades_symbol_time ON trades (category, symbol, trade_time_ms);
"""


def source_row(**overrides) -> BybitHistoricalTradeRow:
    values = dict(
        category="linear",
        symbol="BTCUSDT",
        trade_id="tid-1",
        trade_time_ms=1705276800490,
        trade_time_utc="2024-01-15T00:00:00.490000Z",
        side="Buy",
        size="0.00400",
        price="41731.10",
    )
    values.update(overrides)
    return BybitHistoricalTradeRow(**values)


def source_row_at(trade_time_ms: int, **overrides) -> BybitHistoricalTradeRow:
    """A row whose two source timestamps agree, for a chosen millisecond."""

    seconds, millis = divmod(trade_time_ms, 1000)
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return source_row(
        trade_time_ms=trade_time_ms,
        trade_time_utc=f"{moment:%Y-%m-%dT%H:%M:%S}.{millis:03d}000Z",
        **overrides,
    )


def make_db(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(DDL)
    connection.executemany(
        "INSERT INTO trades (category,symbol,trade_id,trade_time_ms,"
        "trade_time_utc,side,size,price) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()


def sqlite_values(row: BybitHistoricalTradeRow) -> tuple:
    return (
        row.category, row.symbol, row.trade_id, row.trade_time_ms,
        row.trade_time_utc, row.side, row.size, row.price,
    )


class FakeCursor:
    """Minimal cursor test double whose empty batch is genuine EOF."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.index = 0
        self.arraysize = 1

    def execute(self, _sql, _parameters) -> None:
        return None

    def fetchmany(self, size: int) -> list[tuple]:
        batch = self.rows[self.index:self.index + size]
        self.index += len(batch)
        return batch

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.rows)


class BybitHistoricalSourceV1Tests(unittest.TestCase):
    def test_mapping_is_actual_trade_record_and_preserves_semantics(self):
        record = canonicalize_bybit_historical_trade_v1(source_row())
        self.assertIsInstance(record, TradeRecord)
        self.assertEqual(record.venue, "bybit")
        self.assertEqual(record.instrument, "BTCUSDT")
        self.assertEqual(record.exchange_ts.epoch_ns, 1705276800490 * 1_000_000)
        self.assertEqual(record.price, "41731.10")
        self.assertEqual(record.size, "0.00400")
        self.assertEqual(record.aggressor_side, "buy")
        self.assertEqual(record.trade_id, "tid-1")
        self.assertIsNone(record.receive_ts)
        self.assertIsNone(record.sequence)
        self.assertEqual(
            parse_bybit_historical_utc_to_nanos("2024-01-15T00:00:00.490Z"),
            parse_bybit_historical_utc_to_nanos("2024-01-15T00:00:00.490000Z"),
        )

    def test_timestamp_disagreement_and_whitespace_are_source_errors(self):
        with self.assertRaises(BybitHistoricalSourceError) as disagreement:
            canonicalize_bybit_historical_trade_v1(
                source_row(trade_time_utc="2024-01-15T00:00:00.491000Z")
            )
        self.assertEqual(disagreement.exception.trade_id, "tid-1")
        self.assertEqual(disagreement.exception.field, "trade_time_utc")

        with self.assertRaises(BybitHistoricalSourceError) as whitespace:
            canonicalize_bybit_historical_trade_v1(
                source_row(trade_time_utc=" 2024-01-15T00:00:00.490000Z")
            )
        self.assertEqual(whitespace.exception.field, "trade_time_utc")

    def test_invalid_fields_preserve_identity_and_diagnostics(self):
        cases = (
            ("side", source_row(side="Buy ")),
            ("price", source_row(price=" 1")),
            ("size", source_row(size="01.0")),
            ("trade_id", source_row(trade_id="")),
            ("category", source_row(category="spot")),
            ("symbol", source_row(symbol="ETHUSDT")),
        )
        for field, row in cases:
            with self.subTest(field=field):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(row)
                self.assertEqual(caught.exception.field, field)
                if field != "trade_id":
                    self.assertEqual(caught.exception.trade_id, "tid-1")

    # -- source semantics owned here, not by the reference importer ---------

    def test_aggressor_side_maps_only_the_two_native_values(self):
        self.assertEqual(
            canonicalize_bybit_historical_trade_v1(source_row(side="Buy")).aggressor_side,
            "buy")
        self.assertEqual(
            canonicalize_bybit_historical_trade_v1(source_row(side="Sell")).aggressor_side,
            "sell")
        for bad in ("buy", "BUY", "Bid", "Ask", "", None, "Buy ", "SELL"):
            with self.subTest(side=bad):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(side=bad))
                self.assertEqual(caught.exception.field, "side")

    def test_decimals_are_preserved_byte_for_byte_and_never_touch_float(self):
        for field, value in (
            ("price", "41731.10"), ("price", "41731.1"), ("price", "1"),
            ("size", "0.004"), ("size", "0.00400"),
            ("size", "123456.789012345678901234567890"),
            # Exact decimal expansion of the float 0.1: a single IEEE-754
            # round trip would collapse it back to '0.1'.
            ("size", "0.1000000000000000055511151231257827"),
            # Beyond 2^53 with a fractional part: float would lose it.
            ("price", "9007199254740993.1"),
        ):
            with self.subTest(field=field, value=value):
                record = canonicalize_bybit_historical_trade_v1(source_row(**{field: value}))
                self.assertEqual(getattr(record, field), value)
                self.assertIsInstance(getattr(record, field), str)
                self.assertNotIn("e", getattr(record, field).lower())

    def test_non_canonical_decimals_are_refused(self):
        for bad in ("0", "0.0", "-1", "-0.5", "01.5", "1.", ".5", "1e5", "abc", "", " 1"):
            with self.subTest(price=bad):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(price=bad))
                self.assertEqual(caught.exception.field, "price")
        for bad in ("0", "0.000", "-0.004", 0.004):
            with self.subTest(size=bad):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(size=bad))
                self.assertEqual(caught.exception.field, "size")

    def test_source_identity_and_timestamp_authority(self):
        for category in ("spot", "inverse", "", None):
            with self.subTest(category=category):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(category=category))
                self.assertEqual(caught.exception.field, "category")
        for symbol in ("ETHUSDT", "btcusdt", ""):
            with self.subTest(symbol=symbol):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(symbol=symbol))
                self.assertEqual(caught.exception.field, "symbol")
        for trade_id in ("", None, 7):
            with self.subTest(trade_id=trade_id):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(source_row(trade_id=trade_id))
                self.assertEqual(caught.exception.field, "trade_id")

        # trade_time_ms is authoritative; trade_time_utc only corroborates it.
        # Writing the same instant with three or six decimals is accepted.
        for utc in ("2024-01-15T00:00:00.490Z", "2024-01-15T00:00:00.490000Z"):
            with self.subTest(utc=utc):
                record = canonicalize_bybit_historical_trade_v1(
                    source_row(trade_time_utc=utc))
                self.assertEqual(record.exchange_ts.epoch_ns, 1705276800490 * 1_000_000)
        for bad_utc in ("2024-01-15T00:00:00.491000Z",   # one millisecond
                        "2024-01-15T00:00:00.490001Z",   # one microsecond
                        "2024-01-15T01:00:00.490000Z",   # one hour
                        "2024-01-15T00:00:00.490000",    # no timezone
                        "2024-13-45T00:00:00.490000Z",   # impossible date
                        ""):
            with self.subTest(utc=bad_utc):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(
                        source_row(trade_time_utc=bad_utc))
                self.assertEqual(caught.exception.field, "trade_time_utc")
        for bad_ms in (None, True, "1705276800490"):
            with self.subTest(ms=bad_ms):
                with self.assertRaises(BybitHistoricalSourceError) as caught:
                    canonicalize_bybit_historical_trade_v1(
                        source_row(trade_time_ms=bad_ms))
                self.assertEqual(caught.exception.field, "trade_time_ms")

    def test_mapping_is_deterministic_and_day_bounds_are_half_open(self):
        self.assertEqual(
            canonicalize_bybit_historical_trade_v1(source_row()),
            canonicalize_bybit_historical_trade_v1(source_row()),
        )
        self.assertEqual(utc_day_bounds_ms("2024-01-15"),
                         (1705276800000, 1705363200000))
        for bad_day in ("2024-13-45", "15-01-2024", "2024-01-15T00:00:00Z", ""):
            with self.subTest(day=bad_day):
                with self.assertRaises(BybitHistoricalSourceError):
                    utc_day_bounds_ms(bad_day)

    def test_read_only_bounds_filter_and_deterministic_order(self):
        start, end = utc_day_bounds_ms("2024-01-15")
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "source.sqlite"
            rows = [
                ("linear", "BTCUSDT", "before", start - 1, "2024-01-14T23:59:59.999Z", "Buy", "1", "100"),
                ("linear", "BTCUSDT", "last", end - 1, "2024-01-15T23:59:59.999Z", "Sell", "1", "100"),
                ("linear", "BTCUSDT", "z", start + 5, "2024-01-15T00:00:00.005Z", "Buy", "1", "100"),
                ("linear", "BTCUSDT", "a", start + 5, "2024-01-15T00:00:00.005Z", "Sell", "1", "100"),
                ("linear", "BTCUSDT", "end", end, "2024-01-16T00:00:00.000Z", "Buy", "1", "100"),
                ("spot", "BTCUSDT", "other-cat", start + 1, "2024-01-15T00:00:00.001Z", "Buy", "1", "100"),
            ]
            make_db(db, rows)
            connection = open_bybit_historical_source(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE should_not_exist (x INTEGER)")
                extracted = list(iter_bybit_historical_trade_rows(connection, start, end))
            finally:
                connection.close()
        self.assertEqual([row.trade_id for row in extracted], ["a", "z", "last"])
        self.assertTrue(
            all(isinstance(row, BybitHistoricalTradeRow) for row in extracted),
            "the reader must yield the source-owned row type, not raw tuples")

    def test_streaming_contract_has_no_fetchall_and_accumulator_is_bounded(self):
        module_text = (
            ROOT / "src" / "quant_platform" / "source_adapters" /
            "bybit_historical.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("fetchall", module_text)
        accumulator = BybitHistoricalExtractAccumulator()
        self.assertEqual(
            set(accumulator.__slots__), {"_digest", "_row_count", "_exhausted"}
        )
        accumulator.observe(source_row())
        self.assertFalse(accumulator.exhausted)
        with self.assertRaises(BybitHistoricalSourceError):
            accumulator.evidence("2024-01-15", 1, 2)
        # The old public completion API no longer exists. Completion can only
        # be reached by the production source iterator reaching EOF.
        self.assertFalse(hasattr(accumulator, "mark_source_exhausted"))
        with self.assertRaises(AttributeError):
            accumulator.mark_source_exhausted()  # type: ignore[attr-defined]

    # -- extract completion: partial evidence must be impossible -----------

    def _extract_db(self, directory: Path) -> tuple[Path, int, int]:
        start, end = utc_day_bounds_ms("2024-01-15")
        database = directory / "extract.sqlite"
        make_db(database, [
            ("linear", "BTCUSDT", f"id-{index}", start + index,
             f"2024-01-15T00:00:00.{index:03d}000Z", "Buy", "1", "100")
            for index in range(6)
        ])
        return database, start, end

    def test_evidence_is_refused_until_natural_source_exhaustion(self):
        with tempfile.TemporaryDirectory() as temporary:
            database, start, end = self._extract_db(Path(temporary))

            # 1. stream opened but never advanced
            accumulator = BybitHistoricalExtractAccumulator()
            connection = open_bybit_historical_source(database)
            try:
                stream = iter_bybit_historical_trade_rows(
                    connection, start, end, accumulator=accumulator)
                self.assertFalse(accumulator.exhausted)
                with self.assertRaises(BybitHistoricalSourceError):
                    accumulator.evidence("2024-01-15", start, end)

                # 2. partially consumed, then abandoned with break
                for index, _row in enumerate(stream):
                    if index == 2:
                        break
                stream.close()
                self.assertFalse(accumulator.exhausted)
                with self.assertRaises(BybitHistoricalSourceError) as refused:
                    accumulator.evidence("2024-01-15", start, end)
                self.assertIn("exhaustion", str(refused.exception))
                self.assertEqual(accumulator.row_count, 3)
            finally:
                connection.close()

            # 3. consumer raises mid-iteration
            accumulator = BybitHistoricalExtractAccumulator()
            connection = open_bybit_historical_source(database)
            try:
                with self.assertRaises(ZeroDivisionError):
                    for index, _row in enumerate(iter_bybit_historical_trade_rows(
                            connection, start, end, accumulator=accumulator)):
                        if index == 1:
                            raise ZeroDivisionError("consumer failure")
                self.assertFalse(accumulator.exhausted)
                with self.assertRaises(BybitHistoricalSourceError):
                    accumulator.evidence("2024-01-15", start, end)
            finally:
                connection.close()

    def test_full_exhaustion_yields_final_immutable_deterministic_evidence(self):
        def drain(database, start, end):
            accumulator = BybitHistoricalExtractAccumulator()
            connection = open_bybit_historical_source(database)
            try:
                rows = 0
                for _row in iter_bybit_historical_trade_rows(
                        connection, start, end, accumulator=accumulator):
                    rows += 1
            finally:
                connection.close()
            return accumulator, rows

        with tempfile.TemporaryDirectory() as temporary:
            database, start, end = self._extract_db(Path(temporary))
            first, rows = drain(database, start, end)
            second, _ = drain(database, start, end)

            self.assertTrue(first.exhausted)
            evidence = first.evidence("2024-01-15", start, end)
            self.assertEqual(evidence.source_row_count, rows)
            self.assertEqual(evidence.source_row_count, 6)
            self.assertEqual(
                evidence.source_fingerprint_sha256,
                second.evidence("2024-01-15", start, end).source_fingerprint_sha256,
            )
            self.assertEqual(evidence.source_semantics_id,
                             BYBIT_HISTORICAL_SOURCE_SEMANTICS_V1)
            self.assertEqual(evidence.mapping_id, BYBIT_HISTORICAL_MAPPING_V1)
            self.assertEqual(evidence.requested_day, "2024-01-15")
            self.assertEqual(evidence.requested_start_ms, start)
            self.assertEqual(evidence.requested_end_ms, end)
            self.assertEqual(len(evidence.source_fingerprint_sha256), 64)
            # Evidence is final: a sealed extract cannot absorb further rows,
            # so the published count and fingerprint cannot drift afterwards.
            with self.assertRaises(BybitHistoricalSourceError):
                first.observe(source_row())
            self.assertEqual(
                first.evidence("2024-01-15", start, end), evidence)

    def test_sqlite_row_reaches_the_materializer_without_a_conversion_bridge(self):
        identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database, start, end = self._extract_db(directory)
            accumulator = BybitHistoricalExtractAccumulator()
            connection = open_bybit_historical_source(database)
            try:
                # The generator handed to the materializer yields exactly what
                # the production canonicalizer returns: no dict, no JSON, no
                # second mapping between the source seam and materialization.
                def records():
                    for row in iter_bybit_historical_trade_rows(
                            connection, start, end, accumulator=accumulator):
                        record = canonicalize_bybit_historical_trade_v1(row)
                        self.assertIsInstance(record, TradeRecord)
                        yield record

                result = materialize_bybit_trade_v1(
                    directory / "part-001.parquet", records(),
                    dataset_identity=identity, compression=None, row_group_size=8,
                )
            finally:
                connection.close()

        self.assertEqual(result.row_count, 6)
        self.assertTrue(accumulator.exhausted)
        self.assertEqual(
            accumulator.evidence("2024-01-15", start, end).source_row_count, 6)
        self.assertEqual(result.first_exchange_ts, Instant(start * 1_000_000))
        self.assertEqual(result.last_exchange_ts, Instant((start + 5) * 1_000_000))
        self.assertEqual(len(result.canonical_content_hash_v1.rsplit(":", 1)[-1]), 64)

    def test_fingerprint_is_deterministic_and_sensitive_to_values_timestamp_and_order(self):
        first = source_row()
        second = source_row(trade_id="tid-2", trade_time_ms=1705276800491,
                            trade_time_utc="2024-01-15T00:00:00.491000Z")

        def fingerprint(rows):
            accumulator = BybitHistoricalExtractAccumulator()
            connection = FakeConnection([sqlite_values(row) for row in rows])
            for _row in iter_bybit_historical_trade_rows(
                    connection, 1, 2, accumulator=accumulator):
                pass
            return accumulator.evidence("2024-01-15", 1, 2).source_fingerprint_sha256

        self.assertEqual(fingerprint([first, second]), fingerprint([first, second]))
        self.assertNotEqual(fingerprint([first, second]), fingerprint([second, first]))
        self.assertNotEqual(
            fingerprint([first]),
            fingerprint([replace(first, trade_time_utc="2024-01-15T00:00:00.490001Z")]),
        )
        self.assertNotEqual(
            fingerprint([first]), fingerprint([replace(first, trade_time_ms=1705276800491)])
        )

    def test_materializer_accepts_production_record(self):
        identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
        record = canonicalize_bybit_historical_trade_v1(source_row())
        with tempfile.TemporaryDirectory() as temporary:
            result = materialize_bybit_trade_v1(
                Path(temporary) / "part.parquet", [record],
                dataset_identity=identity, compression=None, row_group_size=10,
            )
        self.assertEqual(result.row_count, 1)

    def test_reference_serializer_keeps_exact_three_digit_fraction(self):
        from import_bybit_trades import encode_record  # noqa: PLC0415

        for milliseconds, expected in (
            (1705276800490, b'"exchange_ts":"2024-01-15T00:00:00.490Z"'),
            (1705276800000, b'"exchange_ts":"2024-01-15T00:00:00.000Z"'),
        ):
            record = canonicalize_bybit_historical_trade_v1(
                source_row(trade_time_ms=milliseconds,
                           trade_time_utc=f"2024-01-15T00:00:00.{milliseconds % 1000:03d}000Z")
            )
            self.assertIn(expected, encode_record(record))

    def test_architecture_keeps_source_ownership_in_production(self):
        importer = (ROOT / "tools" / "import_bybit_trades.py").read_text(encoding="utf-8")
        importer_tree = ast.parse(importer)
        function_names = {node.name for node in ast.walk(importer_tree) if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("canonicalize_trade", function_names)
        self.assertIn("quant_platform.source_adapters.bybit_historical", importer)

        source_root = ROOT / "src"
        for path in source_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("tools/import_bybit_trades", text, str(path))
            self.assertNotIn("tools.import_bybit_trades", text, str(path))


class BybitHistoricalLegacySourceAccessTest(unittest.TestCase):
    """Access-mechanics tests for the legacy archive entry points.

    These prove connection behaviour only.  Nothing here claims anything about
    archive completeness, preservation, or Golden acceptance.
    """

    def _legacy_db(self, directory: Path, *, wal: bool = False) -> tuple[Path, int, int]:
        start, end = utc_day_bounds_ms("2024-01-15")
        database = directory / "legacy.sqlite"
        make_db(database, [
            ("linear", "BTCUSDT", f"id-{index}", start + index,
             f"2024-01-15T00:00:00.{index:03d}000Z",
             "Buy" if index % 2 == 0 else "Sell", "1", "100")
            for index in range(6)
        ])
        if wal:
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.commit()
            finally:
                connection.close()
        return database, start, end

    def _block_shm(self, database: Path) -> None:
        """Place a sentinel that an immutable reader must not touch."""

        Path(str(database) + "-shm").mkdir()

    def test_ordinary_access_never_becomes_immutable(self):
        source = Path(__file__).resolve().parents[1].joinpath(
            "src", "quant_platform", "source_adapters", "bybit_historical.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bodies = {
            node.name: ast.get_source_segment(source, node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        # The default reader keeps plain mode=ro; only the explicitly named
        # legacy opener carries the immutable access mode.
        self.assertIn("mode=ro", bodies["open_bybit_historical_source"])
        self.assertNotIn("immutable", bodies["open_bybit_historical_source"])
        self.assertIn("mode=ro&immutable=1", bodies["open_bybit_historical_legacy_source"])
        immutable_owners = {
            name for name, body in bodies.items() if "immutable=1" in (body or "")
        }
        self.assertEqual(immutable_owners, {"open_bybit_historical_legacy_source"})

    @unittest.skipIf(
        os.name == "nt",
        "models deployed POSIX directory permissions; Windows is development-only",
    )
    def test_ordinary_reader_fails_where_the_legacy_reader_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database, start, end = self._legacy_db(directory, wal=True)
            # Closing the WAL-setting connection leaves no writer. Remove any
            # cleanly disposable sidecars before making their directory
            # non-writable, matching the deployed legacy archive condition.
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(database) + suffix)
                if sidecar.exists():
                    self.assertTrue(sidecar.is_file(), str(sidecar))
                    sidecar.unlink()

            original_mode = stat.S_IMODE(directory.stat().st_mode)
            directory.chmod(original_mode & ~0o222)
            try:
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode) & 0o222, 0)
                with self.assertRaises(BybitHistoricalSourceError) as ordinary_failure:
                    connection = open_bybit_historical_source(database)
                    try:
                        next(iter_bybit_historical_trade_rows(
                            connection, start, end), None)
                    finally:
                        connection.close()
                self.assertIsInstance(
                    ordinary_failure.exception.__cause__, sqlite3.OperationalError)

                connection = open_bybit_historical_legacy_source(database)
                try:
                    rows = list(iter_bybit_historical_trade_rows(
                        connection, start, end))
                finally:
                    connection.close()
            finally:
                directory.chmod(original_mode)

            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), original_mode)
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0].trade_id, "id-0")

    def test_legacy_read_never_modifies_the_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database, start, end = self._legacy_db(directory, wal=True)
            before_bytes = database.read_bytes()
            before_identity = BybitHistoricalSourceIdentity.capture(database)

            accumulator = BybitHistoricalExtractAccumulator()
            with bybit_historical_legacy_read(database) as connection:
                consumed = [
                    canonicalize_bybit_historical_trade_v1(row)
                    for row in iter_bybit_historical_trade_rows(
                        connection, start, end, accumulator=accumulator)
                ]

            self.assertEqual(len(consumed), 6)
            self.assertTrue(accumulator.exhausted)
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(
                BybitHistoricalSourceIdentity.capture(database), before_identity)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(
                    Path(str(database) + suffix).exists(),
                    f"the legacy read created a {suffix} beside the source")

    def test_unchanged_source_passes_and_observable_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database, start, end = self._legacy_db(directory)

            with bybit_historical_legacy_read(database) as connection:
                stable = list(iter_bybit_historical_trade_rows(connection, start, end))
            self.assertEqual(len(stable), 6)

            # A source rewritten under the reader must not yield a usable
            # result, even though the rows themselves were read without error.
            produced = []
            with self.assertRaises(BybitHistoricalSourceError) as caught:
                with bybit_historical_legacy_read(database) as connection:
                    produced.extend(
                        iter_bybit_historical_trade_rows(connection, start, end))
                    touched = database.stat().st_mtime_ns + 1_000_000_000
                    os.utime(database, ns=(touched, touched))
            self.assertEqual(len(produced), 6)
            self.assertIn("changed during the finite historical read",
                          str(caught.exception))
            self.assertIn("mtime_ns", str(caught.exception))

    def test_a_failing_read_is_not_masked_by_the_identity_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            database, _start, _end = self._legacy_db(Path(temporary))
            with self.assertRaises(ZeroDivisionError):
                with bybit_historical_legacy_read(database):
                    raise ZeroDivisionError("consumer failure must propagate")

    def test_identity_drift_reports_only_observable_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            database, _start, _end = self._legacy_db(Path(temporary))
            identity = BybitHistoricalSourceIdentity.capture(database)
            self.assertEqual(identity.drift_against(identity), ())
            self.assertEqual(
                replace(identity, size=identity.size + 1).drift_against(identity),
                (f"size: {identity.size + 1!r} -> {identity.size!r}",))
            # Unknown device/inode on either side is not reported as drift.
            unknown = replace(identity, device=None, inode=None)
            self.assertEqual(unknown.drift_against(identity), ())

    def test_legacy_source_reaches_the_canonical_materializer_unchanged(self):
        identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            directory = Path(temporary)
            database, start, end = self._legacy_db(directory, wal=True)
            self._block_shm(database)
            accumulator = BybitHistoricalExtractAccumulator()

            # legacy SQLite -> production adapter -> TradeRecord stream ->
            # the existing canonical materializer.  No bridge, no copy, no
            # legacy-specific materialization path.
            with bybit_historical_legacy_read(database) as connection:
                def records():
                    for row in iter_bybit_historical_trade_rows(
                            connection, start, end, accumulator=accumulator):
                        record = canonicalize_bybit_historical_trade_v1(row)
                        self.assertIsInstance(record, TradeRecord)
                        yield record

                result = materialize_bybit_trade_v1(
                    directory / "part-001.parquet", records(),
                    dataset_identity=identity, compression=None, row_group_size=8,
                )

            self.assertEqual(result.row_count, 6)
            self.assertTrue(accumulator.exhausted)
            self.assertEqual(result.first_exchange_ts, Instant(start * 1_000_000))
            self.assertEqual(
                accumulator.evidence("2024-01-15", start, end).source_row_count, 6)
            self.assertEqual(len(result.canonical_content_hash_v1.rsplit(":", 1)[-1]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
