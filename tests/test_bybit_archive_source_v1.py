"""Bybit public trade archive as the historical source (DG-N T04, ADR-0074 Amendment 1).

The mapping, its fail-closed refusals and the certification profile's source check. When ``QP_BYBIT_ARCHIVE_SAMPLES`` names a
directory holding ``BTCUSDT2024-01-15.csv.gz`` (not in Git: bulk data), the golden equivalence with the SQLite source is checked too.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ARCHIVE_SOURCE_PROFILE_V1,
    BYBIT_SQLITE_SOURCE_PROFILE_V1,
    BybitTradeV1CertificationProfile,
    BybitTradeV1EligibilityError,
    build_bybit_trade_v1_source_extract_coverage,
)
from quant_platform.source_adapters.bybit_archive import (  # noqa: E402
    BYBIT_ARCHIVE_HEADERS_V1,
    BybitArchiveSourceError,
    bybit_archive_url,
    read_bybit_archive_day,
)

HEADER = ",".join(BYBIT_ARCHIVE_HEADERS_V1[0])
HEADER_RPI = ",".join(BYBIT_ARCHIVE_HEADERS_V1[1])
DAY = "2024-01-15"
DAY_START = 1705276800  # 2024-01-15T00:00:00Z
ETH = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")


def row(ts: str, side: str = "Buy", size: str = "0.01", price: str = "2500.10", trade_id: str = "a", symbol: str = "ETHUSDT",
        rpi: str | None = None) -> str:
    fields = [ts, symbol, side, size, price, "PlusTick", trade_id, "1e+09", size, "25.001"]
    if rpi is not None:
        fields.append(rpi)
    return ",".join(fields)


class ArchiveFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.root = Path(self.holder.name)

    def tearDown(self) -> None:
        self.holder.cleanup()

    def write(self, lines: list[str], *, name: str = f"ETHUSDT{DAY}.csv.gz") -> Path:
        path = self.root / name
        path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
        return path

    def read(self, lines: list[str], **kwargs):
        return read_bybit_archive_day(self.write(lines), instrument="ETHUSDT", day=DAY, **kwargs)


class MappingTests(ArchiveFiles):
    def test_maps_rows_to_trade_v1_in_canonical_order_truncated_to_milliseconds(self) -> None:
        # Descending file order, as in the 2020 archive; a timestamp tie broken by trade_id; 0 to 4 fraction digits.
        path = self.write([
            HEADER,
            row(f"{DAY_START + 86399}.9999", side="Sell", trade_id="z", price="2501"),
            row(f"{DAY_START + 10}.4923", trade_id="m"),
            row(f"{DAY_START + 10}.4929", trade_id="b"),
            row(f"{DAY_START + 5}.9", trade_id="c"),
            row(f"{DAY_START}", trade_id="d"),
        ])
        day = read_bybit_archive_day(path, instrument="ETHUSDT", day=DAY)
        stamps = [(Instant.parse(record.exchange_ts).epoch_ns, record.trade_id) for record in day.records]
        ms = 1_000_000
        start = DAY_START * 1_000 * ms
        self.assertEqual(stamps, [
            (start, "d"),
            (start + 5_900 * ms, "c"),
            (start + 10_492 * ms, "b"),  # .4929 and .4923 are the same millisecond: trade_id orders them
            (start + 10_492 * ms, "m"),
            (start + 86_399_999 * ms, "z"),
        ])
        last = day.records[-1]
        self.assertEqual((last.venue, last.instrument, last.price, last.size, last.aggressor_side), ("bybit", "ETHUSDT", "2501", "0.01", "sell"))
        self.assertIsNone(last.receive_ts)
        self.assertIsNone(last.sequence)
        self.assertEqual(day.records[0].aggressor_side, "buy")
        raw = path.read_bytes()
        evidence = day.evidence
        self.assertEqual(evidence.file_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(evidence.file_size_bytes, len(raw))
        self.assertEqual(evidence.row_count, 5)
        self.assertEqual(evidence.url, "https://public.bybit.com/trading/ETHUSDT/ETHUSDT2024-01-15.csv.gz")
        self.assertIn("source_semantics=bybit-public-trade-archive-v1;mapping=bybit-archive-day-extract-v1;", evidence.detail)
        self.assertIn(f"file_sha256={evidence.file_sha256}", evidence.detail)

    def test_the_rpi_header_is_accepted_and_every_row_is_imported(self) -> None:
        day = self.read([HEADER_RPI, row(f"{DAY_START}.1", trade_id="a", rpi="0"), row(f"{DAY_START}.2", trade_id="b", rpi="1")])
        self.assertEqual([record.trade_id for record in day.records], ["a", "b"])

    def test_an_empty_day_maps_to_no_records(self) -> None:
        self.assertEqual(self.read([HEADER]).records, ())

    def test_url_names_the_archive_file(self) -> None:
        self.assertEqual(bybit_archive_url("DOGEUSDT", "2021-06-02"),
                         "https://public.bybit.com/trading/DOGEUSDT/DOGEUSDT2021-06-02.csv.gz")


class RefusalTests(ArchiveFiles):
    def assertRefused(self, lines: list[str], fragment: str) -> None:
        with self.assertRaises(BybitArchiveSourceError) as caught:
            self.read(lines)
        self.assertIn(fragment, str(caught.exception))

    def test_unknown_header(self) -> None:
        self.assertRefused([HEADER + ",extra", row(f"{DAY_START}") + ",x"], "unknown archive header")
        self.assertRefused([HEADER.replace("trdMatchID", "tradeId"), row(f"{DAY_START}")], "unknown archive header")

    def test_row_width(self) -> None:
        self.assertRefused([HEADER, row(f"{DAY_START}", rpi="0")], "fields, header has 10")

    def test_symbol(self) -> None:
        self.assertRefused([HEADER, row(f"{DAY_START}", symbol="BTCUSDT")], "symbol='BTCUSDT'")

    def test_day_bounds(self) -> None:
        self.assertRefused([HEADER, row(f"{DAY_START - 1}.9999")], "outside the UTC day")
        self.assertRefused([HEADER, row(f"{DAY_START + 86400}")], "outside the UTC day")

    def test_side(self) -> None:
        self.assertRefused([HEADER, row(f"{DAY_START}", side="buy")], "is not Buy or Sell")

    def test_decimals(self) -> None:
        for value in ("1e-05", "-1", "0", "0.0", ".5", ""):
            with self.subTest(value=value):
                self.assertRefused([HEADER, row(f"{DAY_START}", size=value)], "not a canonical positive decimal")
        self.assertRefused([HEADER, row(f"{DAY_START}", price="2500,1")], "row has 11 fields")

    def test_timestamp(self) -> None:
        for value in ("abc", "1705276800.", "1705276800.1234567890", "-1705276800", "1.7e9"):
            with self.subTest(value=value):
                self.assertRefused([HEADER, row(value)], "is not decimal seconds")

    def test_trade_id(self) -> None:
        self.assertRefused([HEADER, row(f"{DAY_START}", trade_id="")], "trdMatchID is empty")
        self.assertRefused([HEADER, row(f"{DAY_START}.1", trade_id="x"), row(f"{DAY_START}.5", trade_id="x")], "repeats within the day")

    def test_file_name_instrument_and_day(self) -> None:
        path = self.write([HEADER], name=f"SOLUSDT{DAY}.csv.gz")
        with self.assertRaisesRegex(BybitArchiveSourceError, "expected 'ETHUSDT2024-01-15.csv.gz'"):
            read_bybit_archive_day(path, instrument="ETHUSDT", day=DAY)
        with self.assertRaisesRegex(BybitArchiveSourceError, "not in the Bybit linear allow-list"):
            read_bybit_archive_day(path, instrument="ADAUSDT", day=DAY)
        with self.assertRaisesRegex(BybitArchiveSourceError, "not a calendar date"):
            read_bybit_archive_day(path, instrument="SOLUSDT", day="2024-02-30")

    def test_not_gzip(self) -> None:
        path = self.root / f"ETHUSDT{DAY}.csv.gz"
        path.write_text(HEADER + "\n", encoding="utf-8")
        with self.assertRaisesRegex(BybitArchiveSourceError, "not a gzip UTF-8 CSV"):
            read_bybit_archive_day(path, instrument="ETHUSDT", day=DAY)


def coverage(profile=BYBIT_ARCHIVE_SOURCE_PROFILE_V1) -> dict:
    return build_bybit_trade_v1_source_extract_coverage(
        dataset_identity=ETH, coverage_id="c-v1", intent_start="2024-01-15T00:00:00Z", intent_end="2024-01-16T00:00:00Z",
        assertion_id="a-v1", assertion_start="2024-01-15T00:00:00Z", assertion_end="2024-01-16T00:00:00Z",
        partition_key="dt=2024-01-15", revision=1, source_extract_detail="detail", created_at="2024-01-16T00:00:00Z",
        producer="p", code_ref="c", source_profile=profile,
    )


class ProfileTests(unittest.TestCase):
    profile = BybitTradeV1CertificationProfile("test")

    def test_the_sqlite_builder_output_is_unchanged(self) -> None:
        document = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=ETH, coverage_id="c-v1", intent_start="x", intent_end="y", assertion_id="a-v1",
            assertion_start="x", assertion_end="y", partition_key="dt=2024-01-15", revision=1, source_extract_detail="d",
            created_at="t", producer="p", code_ref="c",
        )
        self.assertEqual((document["acquisition"]["source_semantics"], document["acquisition"]["mapping"]),
                         ("bybit-public-trades-sqlite-v1", "bybit-sqlite-day-extract-v1"))

    def test_certification_accepts_the_archive_and_the_sqlite_profiles(self) -> None:
        for profile in (BYBIT_ARCHIVE_SOURCE_PROFILE_V1, BYBIT_SQLITE_SOURCE_PROFILE_V1):
            with self.subTest(profile=profile):
                result = self.profile.validate_source(ETH, [coverage(profile)])
                self.assertEqual((result["source_semantics"], result["mapping"]), profile)
                self.assertEqual(result["documents"], 1)

    def test_certification_refuses_the_unversioned_csv_profile_and_mixtures(self) -> None:
        unversioned = coverage()
        unversioned["acquisition"] = dict(unversioned["acquisition"], source_semantics="bybit-public-trades-csv-v1",
                                          mapping="bybit-csv-day-extract-v1")
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "source_semantics"):
            self.profile.validate_source(ETH, [unversioned])
        crossed = coverage()
        crossed["acquisition"] = dict(crossed["acquisition"], mapping="bybit-sqlite-day-extract-v1")
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "mapping"):
            self.profile.validate_source(ETH, [crossed])
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "different source profiles"):
            self.profile.validate_source(ETH, [coverage(), coverage(BYBIT_SQLITE_SOURCE_PROFILE_V1)])

    def test_the_builder_refuses_an_unknown_profile(self) -> None:
        with self.assertRaisesRegex(BybitTradeV1EligibilityError, "unknown Bybit source profile"):
            coverage(("bybit-public-trades-csv-v1", "bybit-csv-day-extract-v1"))


SAMPLES = os.environ.get("QP_BYBIT_ARCHIVE_SAMPLES")


@unittest.skipUnless(SAMPLES and (Path(SAMPLES) / "BTCUSDT2024-01-15.csv.gz").is_file(),
                     "QP_BYBIT_ARCHIVE_SAMPLES does not hold BTCUSDT2024-01-15.csv.gz")
class GoldenEquivalenceTests(unittest.TestCase):
    def test_the_archive_day_equals_the_sqlite_golden(self) -> None:
        golden = json.loads((ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json").read_text(encoding="utf-8"))
        day = read_bybit_archive_day(Path(SAMPLES) / "BTCUSDT2024-01-15.csv.gz", instrument="BTCUSDT", day="2024-01-15")
        records = day.records
        self.assertEqual(len(records), golden["row_count"])
        self.assertEqual(sum(record.aggressor_side == "buy" for record in records), golden["buy"])
        self.assertEqual(sum(record.aggressor_side == "sell" for record in records), golden["sell"])
        self.assertEqual(Instant.parse(records[0].exchange_ts), Instant.parse(golden["first_exchange_ts"]))
        self.assertEqual(Instant.parse(records[-1].exchange_ts), Instant.parse(golden["last_exchange_ts"]))
        self.assertEqual(day.evidence.file_sha256, "b126988e2d9f0226138c0273d14a427e316b42b10a9a884ff9f520bcf44a30b9")


if __name__ == "__main__":
    unittest.main()
