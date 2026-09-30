#!/usr/bin/env python3
"""J02 WebSocket transport preserves the frozen Consumer API surface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_application_market_data_result_v1 import (  # noqa: E402
    covered_gateway,
    query,
)
from test_bounded_datagateway_read_v1 import LEFT, RIGHT, trade  # noqa: E402

from quant_platform.application import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    J02_REQUEST_SCHEMA_VERSION,
    J02_RESPONSE_SCHEMA_VERSION,
    encode_consumer_result,
    execute_market_data_query,
    handle_api_transport_connection,
    handle_api_transport_message,
)


def request_payload(**overrides):
    payload = {
        "schema_version": J02_REQUEST_SCHEMA_VERSION,
        "request_id": "request-1",
        "query": {
            "venue": "bybit",
            "instrument": "BTCUSDT",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-01T02:00:00Z",
            "representation": {"kind": "trades", "version": 1, "definition": {}},
            "options": {},
        },
    }
    payload.update(overrides)
    return payload


def batches():
    return {
        LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "1"),)],
        RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "2"),)],
    }


class ApiTransportEncodingTests(unittest.TestCase):
    def test_success_response_is_lossless_consumer_result_payload(self):
        gateway = covered_gateway(batches())
        direct = execute_market_data_query(query(), gateway=gateway)
        response = json.loads(
            handle_api_transport_message(
                json.dumps(request_payload()),
                execute=lambda _query: direct,
            )
        )

        self.assertEqual(J02_RESPONSE_SCHEMA_VERSION, response["schema_version"])
        self.assertEqual("request-1", response["request_id"])
        self.assertEqual("ok", response["status"])
        self.assertEqual(encode_consumer_result(direct), response["result"])

    def test_all_frozen_consumer_error_codes_cross_the_wire_unchanged(self):
        for code in ConsumerErrorCode:
            with self.subTest(code=code.value):
                def execute(_query, *, _code=code):
                    raise ConsumerApiError(
                        _code,
                        "canonical message",
                        context={"field": "value"},
                        request_identity="consumer-request-id",
                    )

                response = json.loads(
                    handle_api_transport_message(json.dumps(request_payload()), execute=execute)
                )

                self.assertEqual("error", response["status"])
                self.assertEqual(code.value, response["error"]["code"])
                self.assertEqual("canonical message", response["error"]["message"])
                self.assertEqual({"field": "value"}, response["error"]["context"])
                self.assertEqual("consumer-request-id", response["error"]["request_identity"])

    def test_malformed_transport_payload_is_invalid_request_without_raw_detail(self):
        response = json.loads(handle_api_transport_message("{not-json", execute=lambda _query: None))

        self.assertEqual("error", response["status"])
        self.assertEqual("invalid_request", response["error"]["code"])
        self.assertEqual("the request is not valid", response["error"]["message"])
        self.assertEqual({}, response["error"]["context"])
        self.assertIsNone(response["error"]["request_identity"])


class ApiTransportWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_websocket_round_trip_matches_direct_application_call(self):
        direct_gateway = covered_gateway(batches())
        direct = execute_market_data_query(query(), gateway=direct_gateway)

        async with websockets.serve(
            lambda websocket: handle_api_transport_connection(websocket, execute=lambda _query: direct),
            "127.0.0.1",
            0,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(json.dumps(request_payload()))
                response = json.loads(await websocket.recv())

        self.assertEqual("ok", response["status"])
        self.assertEqual(encode_consumer_result(direct), response["result"])

    async def test_connection_supports_sequential_request_response_exchanges(self):
        first = execute_market_data_query(query(), gateway=covered_gateway(batches()))
        second = execute_market_data_query(query(end="2024-01-01T01:00:00Z"), gateway=covered_gateway({
            LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "1"),)],
        }))
        results = iter((first, second))

        async with websockets.serve(
            lambda websocket: handle_api_transport_connection(websocket, execute=lambda _query: next(results)),
            "127.0.0.1",
            0,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(json.dumps(request_payload()))
                response_one = json.loads(await websocket.recv())
                await websocket.send(json.dumps(request_payload(request_id="request-2")))
                response_two = json.loads(await websocket.recv())

        self.assertEqual("request-1", response_one["request_id"])
        self.assertEqual("request-2", response_two["request_id"])
        self.assertEqual(first.request_identity, response_one["result"]["request_identity"])
        self.assertEqual(second.request_identity, response_two["result"]["request_identity"])


if __name__ == "__main__":
    unittest.main()
