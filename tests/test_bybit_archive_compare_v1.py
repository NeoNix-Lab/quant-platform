"""Comparison of an archive day with an existing trade-v1 Parquet file (ADR-0074 Amendment 1, A1.2 item 8)."""

from __future__ import annotations

import gzip
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from quant_platform.application.bybit_archive_import import compare_archive_day_with_parquet  # noqa: E402
from quant_platform.data import Instant, TradeRecord  # noqa: E402

HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"
ROWS = [  # (archive timestamp, side, size, price, trade_id)
    ("1705276800.4923", "Buy", "0.5", "2500.10", "a"),
    ("1705276801.1", "Sell", "1", "2499", "b"),
    ("1705363199.9999", "Sell", "2.25", "2501", "c"),
]


def stored(ts: str, side: str, size: str, price: str, trade_id: str, *, keep_sub_ms: bool = False, venue: str = "bybit",
           receive_ts: Instant | None = None, sequence: str | None = None) -> TradeRecord:
    whole, _, fraction = ts.partition(".")
    digits = (fraction + "0" * 9)[: 9 if keep_sub_ms else 3]
    ns = int(whole) * 1_000_000_000 + int(digits.ljust(9, "0"))
    return TradeRecord(venue, "ETHUSDT", Instant(ns), price, size, side.lower(), receive_ts, trade_id, sequence)


def write_parquet(path: Path, records: list[TradeRecord]) -> None:
    """A trade-v1 Parquet file written by hand: the comparator must judge any existing file, not only ones our materializer makes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {
        "venue": [r.venue for r in records],
        "instrument": [r.instrument for r in records],
        "exchange_ts": pa.array([r.exchange_ts.epoch_ns for r in records], type=pa.timestamp("ns", tz="UTC")),
        "price": [r.price for r in records],
        "size": [r.size for r in records],
        "aggressor_side": [r.aggressor_side for r in records],
        "receive_ts": [None if r.receive_ts is None else str(r.receive_ts) for r in records],
        "trade_id": [r.trade_id for r in records],
        "sequence": [r.sequence for r in records],
    }
    pq.write_table(pa.table(columns), path)


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.root = Path(self.holder.name)
        self.archive = self.root / "ETHUSDT2024-01-15.csv.gz"
        lines = [HEADER] + [f"{ts},ETHUSDT,{side},{size},{price},PlusTick,{tid},1,{size},1" for ts, side, size, price, tid in ROWS]
        self.archive.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))

    def tearDown(self) -> None:
        self.holder.cleanup()

    def compare(self, records: list[TradeRecord]) -> dict:
        parquet = self.root / f"old-{len(list(self.root.iterdir()))}" / "part-001.parquet"
        write_parquet(parquet, sorted(records, key=lambda r: (r.exchange_ts.epoch_ns, r.trade_id or "")))
        return compare_archive_day_with_parquet(self.archive, parquet, instrument="ETHUSDT", day="2024-01-15")

    def test_identical_day(self) -> None:
        report = self.compare([stored(*row) for row in ROWS])
        self.assertTrue(report["identical"])
        self.assertTrue(report["same_trades"])
        self.assertEqual((report["archive_rows"], report["parquet_rows"]), (3, 3))

    def test_sub_millisecond_digits_in_the_old_file_are_the_same_trade(self) -> None:
        report = self.compare([stored(*row, keep_sub_ms=True) for row in ROWS])
        self.assertTrue(report["identical"], report)

    def test_trailing_zero_is_a_text_difference_not_a_value_difference(self) -> None:
        records = [stored(*row) for row in ROWS]
        records[0] = stored("1705276800.4923", "Buy", "0.5", "2500.1", "a")
        report = self.compare(records)
        self.assertTrue(report["same_trades"])
        self.assertFalse(report["identical"])
        self.assertEqual(report["field_differences"], {"price_text": 1})

    def test_value_and_membership_differences(self) -> None:
        records = [stored(*row) for row in ROWS[:2]] + [stored("1705363199.9999", "Buy", "2.25", "2502", "x")]
        report = self.compare(records)
        self.assertFalse(report["same_trades"])
        self.assertEqual((report["only_in_archive"], report["only_in_parquet"]), (1, 1))
        self.assertEqual((report["examples_only_in_archive"], report["examples_only_in_parquet"]), (["c"], ["x"]))
        late = [stored(*row) for row in ROWS]
        late[1] = stored("1705276801.2", "Sell", "1", "2499", "b")
        report = self.compare(late)
        self.assertEqual(report["field_differences"], {"exchange_ts_ms": 1})
        self.assertFalse(report["same_trades"])

    def test_every_field_of_the_record_is_compared(self) -> None:
        # Each of these changes one field of one trade and nothing else; none may be reported as the same trade.
        cases = {
            "venue": dict(venue="binance"),
            "receive_ts": dict(receive_ts=Instant.parse("2024-01-15T00:00:01Z")),
            "sequence": dict(sequence="7"),
        }
        for field, change in cases.items():
            with self.subTest(field=field):
                records = [stored(*row) for row in ROWS]
                records[1] = stored(*ROWS[1], **change)
                report = self.compare(records)
                self.assertFalse(report["same_trades"], report)
                self.assertFalse(report["identical"], report)
                self.assertEqual(report["field_differences"], {field: 1})

    def test_a_null_trade_id_in_the_old_file_is_a_difference_not_a_crash(self) -> None:
        records = [stored(*row) for row in ROWS]
        records[2] = TradeRecord("bybit", "ETHUSDT", records[2].exchange_ts, "2501", "2.25", "sell", None, None, None)
        report = self.compare(records)
        self.assertFalse(report["same_trades"], report)
        self.assertEqual(report["parquet_null_trade_ids"], 1)
        self.assertEqual(report["only_in_archive"], 1)


if __name__ == "__main__":
    unittest.main()
