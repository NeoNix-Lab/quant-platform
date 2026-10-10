"""P05a dispatch proof using only a test-owned family, not a concrete seam codec."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import quant_platform.application.api_transport_server as transport  # noqa: E402
from test_api_transport_server_v1 import request_payload  # noqa: E402
from test_remote_j02_security_v1 import (  # noqa: E402
    CERTIFICATE_DER, _Socket, security,
)


FAMILY = "test-only-family-v1"
VERSION = FAMILY + "-request-v1"
RESPONSE_VERSION = FAMILY + "-response-v1"
SCOPE = "j02.strategy.compose"
SCOPES = frozenset({
    "j02.market_data.read", "j02.strategy.compose", "j02.validation.evaluate",
    "j02.training.evaluate", "j02.training.register", "j02.jobs.submit",
    "j02.jobs.read", "j02.admitted_input.read", "j02.result.submit",
})


def envelope(**overrides):
    payload = {
        "message_family": FAMILY,
        "schema_version": VERSION,
        "operation": "echo",
        "request_id": "opaque-request-id",
        "request": {"price": "0.000000000123", "identity": "unaltered"},
    }
    payload.update(overrides)
    return payload


def route(scope=SCOPE):
    decoder = MagicMock(side_effect=lambda body: dict(body))
    executor = MagicMock(side_effect=lambda request: request)
    handler = transport.J02FamilyHandler(scope, RESPONSE_VERSION, decoder, executor)
    return {(FAMILY, VERSION, "echo"): handler}, decoder, executor


class Socket(_Socket):
    def __init__(self, messages, *, on_message=None):
        super().__init__(CERTIFICATE_DER)
        self.messages = iter(messages)
        self.responses = []
        self.on_message = on_message

    async def __anext__(self):
        try:
            message = next(self.messages)
        except StopIteration:
            raise StopAsyncIteration from None
        if self.on_message is not None:
            self.on_message()
        return message

    async def send(self, message):
        self.responses.append(json.loads(message))


class FamilyAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, scopes, payload, handlers=None, *, on_message=None):
        configuration = security(principal=transport.RemoteJ02Principal("family-peer", scopes))
        socket = Socket([json.dumps(payload)], on_message=on_message)
        legacy = MagicMock()
        await transport.handle_api_transport_connection(
            socket, execute=legacy, remote_security=configuration, family_handlers=handlers,
        )
        return socket, configuration, legacy

    async def test_registered_scopes_never_imply_another_scope_before_body_decode(self):
        self.assertEqual(SCOPES, transport.J02_REMOTE_SCOPES)
        for granted in SCOPES:
            for required in SCOPES:
                if required == granted:
                    continue
                with self.subTest(granted=granted, required=required):
                    handlers, decoder, executor = route(required)
                    # Even an invalid semantic body must be denied before decoding.
                    socket, configuration, legacy = await self.exchange(
                        frozenset({granted}), envelope(request=["not a request"]), handlers,
                    )
                    self.assertEqual([(1008, "policy denied")], socket.close_calls)
                    self.assertEqual([], socket.responses)
                    decoder.assert_not_called()
                    executor.assert_not_called()
                    legacy.assert_not_called()
                    evidence = configuration.evidence_log.events[-1]
                    self.assertEqual(required, evidence.requested_scope)
                    self.assertEqual("deny", evidence.decision)

    async def test_family_only_principal_can_route_without_market_data_scope(self):
        handlers, decoder, executor = route()
        socket, configuration, legacy = await self.exchange(frozenset({SCOPE}), envelope(), handlers)
        self.assertEqual([], socket.close_calls)
        self.assertEqual([{
            "message_family": FAMILY, "schema_version": RESPONSE_VERSION,
            "request_id": "opaque-request-id", "status": "ok", "result": envelope()["request"],
        }], socket.responses)
        decoder.assert_called_once_with(envelope()["request"])
        executor.assert_called_once_with(envelope()["request"])
        legacy.assert_not_called()
        self.assertEqual(SCOPE, configuration.evidence_log.events[-1].requested_scope)

    async def test_valid_body_decoder_is_never_called_without_family_scope(self):
        handlers, decoder, executor = route()
        socket, _, legacy = await self.exchange(
            frozenset({"j02.market_data.read"}), envelope(), handlers,
        )
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        self.assertEqual([], socket.responses)
        decoder.assert_not_called()
        executor.assert_not_called()
        legacy.assert_not_called()

    async def test_family_only_scope_cannot_decode_a_legacy_query(self):
        with patch.object(transport, "decode_transport_query") as decoder:
            socket, _, legacy = await self.exchange(frozenset({SCOPE}), request_payload())
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        decoder.assert_not_called()
        legacy.assert_not_called()

    async def test_unknown_header_never_falls_back_or_decodes_request(self):
        handlers, decoder, executor = route()
        cases = (
            {"message_family": "unknown-family"}, {"schema_version": "unknown-version"},
            {"operation": "unknown-operation"}, {"message_family": None},
            {"message_family": []}, {"schema_version": 1}, {"operation": {}},
        )
        for header in cases:
            with self.subTest(header=header), patch.object(transport, "decode_transport_query") as legacy_decode:
                socket, _, legacy = await self.exchange(
                    SCOPES, envelope(query=request_payload()["query"], **header), handlers,
                )
                self.assertEqual([], socket.close_calls)
                response = socket.responses[0]
                self.assertEqual("error", response["status"])
                self.assertEqual("invalid_request", response["error"]["code"])
                self.assertIn("unsupported", response["error"]["message"])
                self.assertNotEqual(transport.J02_RESPONSE_SCHEMA_VERSION, response["schema_version"])
                decoder.assert_not_called()
                executor.assert_not_called()
                legacy_decode.assert_not_called()
                legacy.assert_not_called()

    async def test_concrete_families_are_not_routable_by_default(self):
        for family, operation in (
            ("j02-strategy-v1", "compose"), ("j02-validation-v1", "build_folds"),
            ("j02-training-v1", "train_evaluate"), ("j02-job-v1", "submit"),
            ("j02-admitted-input-v1", "admit"), ("j02-result-import-v1", "submit"),
        ):
            with self.subTest(family=family):
                socket, _, legacy = await self.exchange(SCOPES, envelope(
                    message_family=family, schema_version=family + "-request-v1", operation=operation,
                ))
                self.assertEqual("error", socket.responses[0]["status"])
                self.assertIn("unsupported", socket.responses[0]["error"]["message"])
                legacy.assert_not_called()

    async def test_declared_family_scope_is_enforced_even_without_a_codec(self):
        cases = (
            ("j02-strategy-v1", "compose", "j02.strategy.compose"),
            ("j02-validation-v1", "evaluate_pbo", "j02.validation.evaluate"),
            ("j02-training-v1", "train_evaluate", "j02.training.evaluate"),
            ("j02-job-v1", "submit", "j02.jobs.submit"),
            ("j02-job-v1", "cancel", "j02.jobs.submit"),
            ("j02-job-v1", "status", "j02.jobs.read"),
            ("j02-job-v1", "result", "j02.jobs.read"),
            ("j02-admitted-input-v1", "deliver", "j02.admitted_input.read"),
            ("j02-result-import-v1", "submit", "j02.result.submit"),
        )
        for family, operation, required in cases:
            with self.subTest(family=family, operation=operation):
                socket, configuration, legacy = await self.exchange(
                    SCOPES - {required}, envelope(
                        message_family=family, schema_version=family + "-request-v1",
                        operation=operation,
                    ),
                )
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)
                self.assertEqual(required, configuration.evidence_log.events[-1].requested_scope)
                legacy.assert_not_called()

    async def test_composition_cannot_reassign_a_declared_family_to_market_data_scope(self):
        handlers, _, _ = route("j02.market_data.read")
        route_handler = next(iter(handlers.values()))
        with self.assertRaisesRegex(ValueError, "declared header scope"):
            await self.exchange(SCOPES, envelope(), {
                ("j02-strategy-v1", "j02-strategy-v1-request-v1", "compose"): route_handler,
            })

    async def test_scope_removal_on_active_connection_denies_before_decoder(self):
        handlers, decoder, executor = route()
        configuration = security(principal=transport.RemoteJ02Principal("family-peer", frozenset({SCOPE})))
        fingerprint = transport.certificate_fingerprint(CERTIFICATE_DER)
        socket = Socket([json.dumps(envelope())], on_message=lambda: configuration.principals_by_fingerprint.__setitem__(
            fingerprint, transport.RemoteJ02Principal("family-peer", frozenset({"j02.jobs.read"})),
        ))
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        decoder.assert_not_called()
        executor.assert_not_called()

    async def test_authorized_error_carries_frozen_consumer_error_fields(self):
        handlers, decoder, executor = route()
        for code in transport.ConsumerErrorCode:
            with self.subTest(code=code):
                executor.side_effect = transport.ConsumerApiError(
                    code, "canonical error", context={"reason": "unchanged"}, request_identity="identity",
                )
                socket, _, _ = await self.exchange(frozenset({SCOPE}), envelope(), handlers)
                self.assertEqual({
                    "code": code.value, "message": "canonical error",
                    "context": {"reason": "unchanged"}, "request_identity": "identity",
                }, socket.responses[0]["error"])


class FamilyDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_parser_preserves_v1_invalid_request_for_excessive_json_nesting(self):
        socket = Socket(["[" * 2000 + "]" * 2000])
        execute = MagicMock()
        await transport.handle_api_transport_connection(socket, execute=execute)
        self.assertEqual("invalid_request", socket.responses[0]["error"]["code"])
        self.assertEqual(transport.J02_RESPONSE_SCHEMA_VERSION, socket.responses[0]["schema_version"])
        execute.assert_not_called()

    async def test_server_composition_forwards_test_route_over_real_websocket(self):
        handlers, decoder, executor = route()
        stop = asyncio.Event()
        # Capture only the server's ephemeral port; use its real serve context.
        serve = transport.websockets.serve
        captured = {}

        class ServerContext:
            async def __aenter__(self):
                self.context = serve(captured["handler"], "127.0.0.1", 0, max_size=transport.J02_MAX_WIRE_MESSAGE_BYTES)
                server = await self.context.__aenter__()
                captured["port"] = server.sockets[0].getsockname()[1]
                captured["ready"].set()
                return server

            async def __aexit__(self, *args):
                return await self.context.__aexit__(*args)

        captured["ready"] = asyncio.Event()

        def listener(handler, _host, _port, **_kwargs):
            captured["handler"] = handler
            return ServerContext()

        with patch.object(transport.websockets, "serve", listener):
            task = asyncio.create_task(transport.run_api_transport_server(
                transport.ApiTransportServerConfig(), stop_event=stop,
                execute=MagicMock(), family_handlers=handlers,
            ))
            try:
                await captured["ready"].wait()
                async with websockets.connect(f"ws://127.0.0.1:{captured['port']}") as socket:
                    for request_id in ("first", "second"):
                        await socket.send(json.dumps(envelope(request_id=request_id)))
                        response = json.loads(await socket.recv())
                        self.assertEqual(request_id, response["request_id"])
                        self.assertEqual(envelope()["request"], response["result"])
            finally:
                stop.set()
                await task
        self.assertEqual(2, decoder.call_count)
        self.assertEqual(2, executor.call_count)

    def test_direct_legacy_entrypoint_cannot_coerce_family_into_valid_query(self):
        execute = MagicMock(return_value=None)
        response = json.loads(transport.handle_api_transport_message(
            json.dumps(envelope(
                query=request_payload()["query"], schema_version=transport.J02_REQUEST_SCHEMA_VERSION,
            )), execute=execute,
        ))
        self.assertIn("unsupported", response["error"]["message"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
