#!/usr/bin/env python3
"""J05 TUI client remains a thin J02 remote client."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "tui"))

import market_data_tui  # noqa: E402


def args():
    return [
        "--url", "ws://127.0.0.1:8765",
        "--venue", "bybit",
        "--instrument", "BTCUSDT",
        "--start", "2024-01-01T00:00:00Z",
        "--end", "2024-01-01T01:00:00Z",
        "--request-id", "request-1",
        "--max-rows", "1",
    ]


def success_response():
    return {
        "schema_version": "j02-response-v1",
        "request_id": "request-1",
        "status": "ok",
        "result": {
            "request": {
                "venue": "bybit",
                "instrument": "BTCUSDT",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-01T01:00:00Z",
                "representation": {"kind": "trades", "version": 1, "definition": {}},
                "options": {},
            },
            "row_count": 2,
            "coverage": {"complete": True, "gaps": []},
            "data": [
                {
                    "exchange_ts": "2024-01-01T00:10:00Z",
                    "price": "42000.00",
                    "size": "0.01",
                    "aggressor_side": "buy",
                    "trade_id": "trade-1",
                },
                {
                    "exchange_ts": "2024-01-01T00:20:00Z",
                    "price": "42001.00",
                    "size": "0.02",
                    "aggressor_side": "sell",
                    "trade_id": "trade-2",
                },
            ],
        },
    }


class FakeConnect:
    def __init__(self, response):
        self.response = response
        self.url = None
        self.sent = []
        self.kwargs = None

    def __call__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(self.response)


class FailingConnect:
    def __init__(self, exc):
        self.exc = exc
        self.url = None

    def __call__(self, url, **kwargs):
        self.url = url
        raise self.exc


class MarketDataTuiTests(unittest.TestCase):
    def test_build_request_is_the_j02_consumer_query_shape(self):
        request = market_data_tui.build_request(market_data_tui.build_parser().parse_args(args()))

        self.assertEqual("j02-request-v1", request["schema_version"])
        self.assertEqual("request-1", request["request_id"])
        self.assertEqual({
            "venue": "bybit",
            "instrument": "BTCUSDT",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-01T01:00:00Z",
            "representation": {"kind": "trades", "version": 1, "definition": {}},
            "options": {},
        }, request["query"])

    def test_run_communicates_over_websocket_and_renders_success_dashboard(self):
        connector = FakeConnect(success_response())
        stdout = io.StringIO()

        code = market_data_tui.run_market_data_tui(args(), connect=connector, stdout=stdout)

        self.assertEqual(0, code)
        self.assertEqual("ws://127.0.0.1:8765", connector.url)
        self.assertEqual(
            market_data_tui.J02_MAX_WIRE_MESSAGE_BYTES, connector.kwargs.get("max_size")
        )
        self.assertEqual("j02-request-v1", connector.sent[0]["schema_version"])
        screen = stdout.getvalue()
        self.assertIn("Quant Platform Market Data TUI", screen)
        self.assertIn("schema_version=j05-tui-screen-v1", screen)
        self.assertIn("status=ok", screen)
        self.assertIn("row_count=2", screen)
        self.assertIn("coverage_complete=True", screen)
        self.assertIn("trade-1", screen)
        self.assertIn("... 1 more rows", screen)

    def test_error_response_is_rendered_without_code_translation(self):
        response = {
            "schema_version": "j02-response-v1",
            "request_id": "request-1",
            "status": "error",
            "error": {
                "code": "no_coverage",
                "message": "the requested interval is not fully covered",
                "context": {"interval": "missing"},
                "request_identity": "consumer-request-id",
            },
        }
        stdout = io.StringIO()

        code = market_data_tui.run_market_data_tui(args(), connect=FakeConnect(response), stdout=stdout)

        self.assertEqual(2, code)
        screen = stdout.getvalue()
        self.assertIn("status=error", screen)
        self.assertIn("error_code=no_coverage", screen)
        self.assertIn("message=the requested interval is not fully covered", screen)
        self.assertIn('context={"interval":"missing"}', screen)

    def test_unreachable_transport_renders_clean_operational_error(self):
        connector = FailingConnect(ConnectionRefusedError("server refused connection"))
        stdout = io.StringIO()

        code = market_data_tui.run_market_data_tui(args(), connect=connector, stdout=stdout)

        self.assertEqual(2, code)
        self.assertEqual("ws://127.0.0.1:8765", connector.url)
        screen = stdout.getvalue()
        self.assertIn("status=error", screen)
        self.assertIn("error_code=connection_failed", screen)
        self.assertIn("message=unable to reach J02 transport", screen)
        self.assertIn('"exception_type":"ConnectionRefusedError"', screen)
        self.assertIn('"url":"ws://127.0.0.1:8765"', screen)


if __name__ == "__main__":
    unittest.main()
