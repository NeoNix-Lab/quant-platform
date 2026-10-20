"""Comparison of an archive day with an existing trade-v1 Parquet file (ADR-0074 Amendment 1, A1.2 item 8)."""

from __future__ import annotations

import gzip
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_archive_import import compare_archive_day_with_parquet  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit import materialize_bybit_trade_v1  # noqa: E402

HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"
ETH = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
ROWS = [  # (archive timestamp, side, size, price, trade_id)
    ("1705276800.4923", "Buy", "0.5", "2500.10", "a"),
    ("1705276801.1", "Sell", "1", "2499", "b"),
    ("1705363199.9999", "Sell", "2.25", "2501", "c"),
]


def stored(ts: str, side: str, size: str, price: str, trade_id: str, *, keep_sub_ms: bool = False) -> TradeRecord:
    whole, _, fraction = ts.partition(".")
    digits = (fraction + "0" * 9)[: 9 if keep_sub_ms else 3]
    ns = int(whole) * 1_000_000_000 + int(digits.ljust(9, "0"))
    return TradeRecord("bybit", "ETHUSDT", Instant(ns), price, size, side.lower(), None, trade_id, None)


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
        materialize_bybit_trade_v1(parquet, sorted(records, key=lambda r: (r.exchange_ts.epoch_ns, r.trade_id)), dataset_identity=ETH)
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


if __name__ == "__main__":
    unittest.main()
