#!/usr/bin/env python3
"""J04 CLI client stays thin and renders canonical Application payloads."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from test_application_market_data_result_v1 import covered_gateway, query  # noqa: E402
from test_bounded_datagateway_read_v1 import LEFT, RIGHT, trade  # noqa: E402

import market_data_cli  # noqa: E402
from quant_platform.application import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    encode_consumer_error,
    encode_consumer_result,
    execute_market_data_query,
)


def cli_args():
    return [
        "--venue", "bybit",
        "--instrument", "BTCUSDT",
        "--start", "2024-01-01T00:00:00Z",
        "--end", "2024-01-01T02:00:00Z",
    ]


def result():
    gateway = covered_gateway({
        LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "1"),)],
        RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "2"),)],
    })
    return execute_market_data_query(query(), gateway=gateway)


class MarketDataCliTests(unittest.TestCase):
    def test_success_renders_canonical_consumer_result(self):
        expected = result()
        stdout = io.StringIO()

        code = market_data_cli.run_market_data_cli(
            cli_args(),
            execute=lambda consumer_query: expected,
            stdout=stdout,
        )

        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("j04-cli-output-v1", payload["schema_version"])
        self.assertEqual("ok", payload["status"])
        self.assertEqual(encode_consumer_result(expected), payload["result"])

    def test_consumer_error_renders_canonical_error_and_nonzero_exit(self):
        error = ConsumerApiError(
            ConsumerErrorCode.NO_COVERAGE,
            "the requested interval is not fully covered",
            context={"requested_interval": {"start": "a", "end": "b"}},
            request_identity="consumer-request-id",
        )
        stdout = io.StringIO()

        def execute(_query):
            raise error

        code = market_data_cli.run_market_data_cli(cli_args(), execute=execute, stdout=stdout)

        self.assertEqual(2, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("error", payload["status"])
        self.assertEqual(encode_consumer_error(error), payload["error"])

    def test_cli_constructs_the_canonical_trades_v1_query(self):
        captured = []

        def execute(consumer_query):
            captured.append(consumer_query)
            return result()

        code = market_data_cli.run_market_data_cli(cli_args(), execute=execute, stdout=io.StringIO())

        self.assertEqual(0, code)
        self.assertEqual(1, len(captured))
        request = captured[0]
        self.assertEqual("bybit", request.venue)
        self.assertEqual("BTCUSDT", request.instrument)
        self.assertEqual("trades", request.representation.kind)
        self.assertEqual(1, request.representation.version)
        self.assertEqual({}, request.options)


if __name__ == "__main__":
    unittest.main()
