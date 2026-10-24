"""Read-only catalog check for the Bybit linear allow-list (issue #485, ADR-0074 section 5)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import instrument_catalog_check as check  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1  # noqa: E402


class FakeCursor:
    def __init__(self, rows, statements):
        self.rows = rows
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def cursor(self):
        return FakeCursor(self.rows, self.statements)


def tool():
    spec = importlib.util.spec_from_file_location("check_trades_catalog", ROOT / "tools" / "check_trades_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstrumentCatalogCheckV1Tests(unittest.TestCase):
    ROWS = [
        ("BTCUSDT", "valid", 2118), ("BTCUSDT", "superseded", 73689),
        ("ETHUSDT", None, 0),  # registered, no partition yet
        ("XRPUSDT", "building", 4),
    ]

    def test_it_reports_each_allow_list_instrument_in_order(self) -> None:
        statuses = check.check_trades_catalog(FakeConnection(self.ROWS))
        self.assertEqual(list(BYBIT_LINEAR_TRADE_INSTRUMENTS_V1), [item.instrument for item in statuses])
        by_name = {item.instrument: item for item in statuses}
        self.assertEqual({"superseded": 73689, "valid": 2118}, dict(by_name["BTCUSDT"].partitions_by_state))
        self.assertTrue(by_name["BTCUSDT"].readable)
        self.assertTrue(by_name["ETHUSDT"].registered)
        self.assertFalse(by_name["ETHUSDT"].readable)
        self.assertTrue(by_name["XRPUSDT"].registered)
        self.assertFalse(by_name["XRPUSDT"].readable)
        for name in ("SOLUSDT", "DOGEUSDT"):
            self.assertFalse(by_name[name].registered)
            self.assertEqual({}, dict(by_name[name].partitions_by_state))

    def test_the_report_names_what_a_registration_slice_acts_on(self) -> None:
        document = check.report(check.check_trades_catalog(FakeConnection(self.ROWS)))
        self.assertEqual(["SOLUSDT", "DOGEUSDT"], document["missing"])
        self.assertEqual(["ETHUSDT", "XRPUSDT"], document["registered_without_valid_partitions"])
        everything_missing = check.report(check.check_trades_catalog(FakeConnection([])))
        self.assertEqual(list(BYBIT_LINEAR_TRADE_INSTRUMENTS_V1), everything_missing["missing"])

    def test_it_issues_one_select_and_nothing_that_writes(self) -> None:
        connection = FakeConnection(self.ROWS)
        check.check_trades_catalog(connection)
        ((statement, parameters),) = connection.statements
        self.assertTrue(statement.lstrip().upper().startswith("SELECT"))
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT"):
            self.assertNotIn(forbidden, statement.upper())
        self.assertEqual((list(BYBIT_LINEAR_TRADE_INSTRUMENTS_V1),), parameters)
        self.assertIn("d.layer = 'canonical' AND d.kind = 'trades' AND d.venue = 'bybit' AND d.schema_id = 'trade-v1'", statement)

    def test_the_tool_takes_the_dsn_from_the_environment_and_never_from_the_command_line(self) -> None:
        module = tool()
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(2, module.main([]))
        with self.assertRaises(SystemExit):  # not an abbreviation of --dsn-env
            module.main(["--dsn", "postgresql://user:secret@host/db"])
        # A connection string given as the variable name is refused without being looked up or echoed.
        with mock.patch("sys.stderr") as stderr:
            self.assertEqual(2, module.main(["--dsn-env", "postgresql://user:secret@host/db"]))
        self.assertNotIn("secret", "".join(str(call.args[0]) for call in stderr.write.call_args_list))
        source = (ROOT / "tools" / "check_trades_catalog.py").read_text(encoding="utf-8")
        self.assertIn("default_transaction_read_only=on", source)
        self.assertNotIn('"--dsn"', source)


if __name__ == "__main__":
    unittest.main()
