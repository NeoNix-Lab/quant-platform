#!/usr/bin/env python3
"""J15 Omega remote client adapter v1 -- unit-level wire behavior.

Covers ADR-0067's typed-failure boundary (``wire_incompatible``,
``RemoteConsumerApiError``) with a fake connector; the real WSS/mTLS
``remote_security_failure`` proof and the full real-server round trip live in
``tests/test_omega_remote_client_acceptance_v1.py``, which needs a real
listening socket and real certificates.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "clients" / "omega"))

from quant_platform.application import (  # noqa: E402
    ConsumerErrorCode,
    encode_consumer_result,
    execute_market_data_query,
)

from test_application_market_data_result_v1 import covered_gateway, query  # noqa: E402
from test_bounded_datagateway_read_v1 import LEFT, RIGHT, trade  # noqa: E402

import j15_remote_client as client  # noqa: E402


def batches():
    return {
        LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "1"),)],
        RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "2"),)],
    }


class _FakeConnection:
    """Minimal async context manager standing in for ``websockets.connect``."""

    def __init__(self, reply: str, *, sent: list[str] | None = None):
        self._reply = reply
        self._sent = sent if sent is not None else []

    async def send(self, message: str) -> None:
        self._sent.append(message)

    async def recv(self) -> str:
        return self._reply

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def fake_connect(reply: str, *, sent: list[str] | None = None):
    def connect(_url: str, **_kwargs: object) -> _FakeConnection:
        return _FakeConnection(reply, sent=sent)

    return connect


class RequestEnvelopeTests(unittest.TestCase):
    def test_request_is_the_exact_j02_request_v1_envelope(self):
        request = client.build_market_data_request(
            venue="bybit", instrument="BTCUSDT", start="2024-01-01T00:00:00Z", end="2024-01-01T02:00:00Z"
        )

        self.assertEqual("j02-request-v1", request["schema_version"])
        self.assertTrue(request["request_id"])
        self.assertEqual(
            {
                "venue": "bybit",
                "instrument": "BTCUSDT",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-01T02:00:00Z",
                "representation": {"kind": "trades", "version": 1, "definition": {}},
                "options": {},
            },
            request["query"],
        )

    def test_explicit_request_id_is_preserved(self):
        request = client.build_market_data_request(
            venue="bybit", instrument="BTCUSDT", start="s", end="e", request_id="fixed-id"
        )
        self.assertEqual("fixed-id", request["request_id"])


class OkResultRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ok_response_is_rendered_losslessly_against_direct_execution(self):
        direct = execute_market_data_query(query(), gateway=covered_gateway(batches()))
        expected = encode_consumer_result(direct)
        reply = json.dumps({
            "schema_version": "j02-response-v1",
            "request_id": "request-1",
            "status": "ok",
            "result": expected,
        })
        request = client.build_market_data_request(
            venue="bybit", instrument="BTCUSDT", start="2024-01-01T00:00:00Z", end="2024-01-01T02:00:00Z",
            request_id="request-1",
        )

        result = await client.fetch_market_data(
            "wss://ignored", request, ssl_context=None, connect=fake_connect(reply)
        )

        self.assertEqual(expected["request_identity"], result.request_identity)
        self.assertEqual(expected["row_count"], result.row_count)
        self.assertEqual(expected["data"], list(result.data))
        self.assertEqual(expected["coverage"], dict(result.coverage))
        self.assertEqual(expected["provenance"], dict(result.provenance))
        self.assertEqual(expected["requested_interval"], dict(result.requested_interval))


class ConsumerApiErrorReconstructionTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_frozen_consumer_error_code_is_reconstructed_losslessly(self):
        for code in ConsumerErrorCode:
            with self.subTest(code=code.value):
                reply = json.dumps({
                    "schema_version": "j02-response-v1",
                    "request_id": "request-1",
                    "status": "error",
                    "error": {
                        "code": code.value,
                        "message": "canonical message",
                        "context": {"field": "value"},
                        "request_identity": "consumer-request-id",
                    },
                })
                request = client.build_market_data_request(
                    venue="bybit", instrument="BTCUSDT", start="s", end="e", request_id="request-1"
                )

                with self.assertRaises(client.RemoteConsumerApiError) as caught:
                    await client.fetch_market_data(
                        "wss://ignored", request, ssl_context=None, connect=fake_connect(reply)
                    )

                error = caught.exception
                self.assertEqual(code.value, error.code)
                self.assertEqual("canonical message", error.message)
                self.assertEqual({"field": "value"}, dict(error.context))
                self.assertEqual("consumer-request-id", error.request_identity)

    async def test_consumer_error_is_never_reinterpreted_as_wire_incompatible(self):
        reply = json.dumps({
            "schema_version": "j02-response-v1",
            "request_id": "request-1",
            "status": "error",
            "error": {"code": "source_not_found", "message": "m", "context": {}, "request_identity": None},
        })
        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")

        with self.assertRaises(client.RemoteConsumerApiError):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=fake_connect(reply))


class WireIncompatibleTests(unittest.IsolatedAsyncioTestCase):
    async def test_j14_framed_envelope_in_place_of_j02_is_wire_incompatible(self):
        """ADR-0067 s3: a J14/new-family envelope in place of J02 v1 is a local
        typed wire_incompatible failure, not a downgrade or schema probe."""
        reply = json.dumps({
            "schema_version": "j14-framed-result-v1",
            "transfer_id": "transfer-1",
            "status": "ok",
            "result": {},
        })
        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")

        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=fake_connect(reply))

    async def test_unsupported_response_schema_version_is_wire_incompatible(self):
        reply = json.dumps({"schema_version": "j02-response-v2", "request_id": "r", "status": "ok", "result": {}})
        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")

        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=fake_connect(reply))

    async def test_malformed_json_is_wire_incompatible(self):
        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")

        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data(
                "wss://ignored", request, ssl_context=None, connect=fake_connect("{not-json")
            )

    async def test_unexpected_status_is_wire_incompatible(self):
        reply = json.dumps({"schema_version": "j02-response-v1", "request_id": "r", "status": "pending"})
        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")

        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=fake_connect(reply))

    async def test_wire_incompatible_is_never_retried_with_a_different_connector_call(self):
        """A single connect() call is made; there is no downgrade/probe retry loop."""
        calls = {"count": 0}
        reply = json.dumps({"schema_version": "j14-framed-result-v1", "status": "ok", "result": {}})

        def counting_connect(_url: str, **_kwargs: object) -> _FakeConnection:
            calls["count"] += 1
            return _FakeConnection(reply)

        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=counting_connect)
        self.assertEqual(1, calls["count"])


class RenderScreenTests(unittest.TestCase):
    def test_renders_each_outcome_kind_distinctly(self):
        ok = client.RemoteMarketDataResult(
            request={}, request_identity="rid", representation={}, data=(),
            requested_interval={}, returned_temporal_bounds=None,
            coverage={"complete": True}, provenance={"dataset_identity": "d"}, row_count=0,
        )
        self.assertIn("rid", client.render_screen(ok))

        error = client.RemoteConsumerApiError("source_not_found", "no venue", context={}, request_identity="rid-2")
        self.assertIn("source_not_found", client.render_screen(error))

        self.assertIn("wire_incompatible", client.render_screen(client.WireIncompatible("bad")))
        self.assertIn("remote_security_failure", client.render_screen(client.RemoteSecurityFailure("bad")))


if __name__ == "__main__":
    unittest.main()
