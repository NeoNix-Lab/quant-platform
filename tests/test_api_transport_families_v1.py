"""P05a dispatch proof using only a test-owned family, not a concrete seam codec."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import quant_platform.application.api_transport_server as transport  # noqa: E402
from test_api_transport_server_v1 import request_payload  # noqa: E402
from test_omega_remote_client_acceptance_v1 import (  # noqa: E402
    _certificate_fingerprint, _generate_ec_key, _self_signed_ca, _signed_certificate,
)
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
        self.wire_responses = []
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
        self.wire_responses.append(message)
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

    def test_direct_legacy_entrypoint_cannot_coerce_family_into_valid_query(self):
        execute = MagicMock(return_value=None)
        response = json.loads(transport.handle_api_transport_message(
            json.dumps(envelope(
                query=request_payload()["query"], schema_version=transport.J02_REQUEST_SCHEMA_VERSION,
            )), execute=execute,
        ))
        self.assertIn("unsupported", response["error"]["message"])
        execute.assert_not_called()


class FamilyResponseAndBodyTests(unittest.IsolatedAsyncioTestCase):
    """Review findings on the response path, the lossless body and the scope interface."""

    async def exchange_text(self, text, handlers, scopes=frozenset({SCOPE})):
        configuration = security(principal=transport.RemoteJ02Principal("family-peer", scopes))
        socket = Socket([text])
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        return socket, configuration

    async def exchange(self, handlers, **overrides):
        return await self.exchange_text(json.dumps(envelope(**overrides)), handlers)

    async def test_non_finite_result_and_error_context_are_typed_integrity_failures(self):
        def reject_constant(value):
            raise ValueError(f"non-JSON constant: {value}")

        for value in (float("nan"), float("inf"), -float("inf")):
            for location in ("result", "executor error", "decoder error"):
                with self.subTest(value=value, location=location):
                    handlers, decoder, executor = route()
                    if location == "result":
                        executor.side_effect = lambda request: {"nested": [{"value": value}]}
                    else:
                        error = transport.ConsumerApiError(
                            transport.ConsumerErrorCode.INVALID_REQUEST, "error",
                            context={"nested": [{"value": value}]},
                        )
                        (decoder if location == "decoder error" else executor).side_effect = error
                    socket, _ = await self.exchange(handlers)
                    response = json.loads(socket.wire_responses[0], parse_constant=reject_constant)
                    self.assertEqual("integrity_failure", response["error"]["code"])
                    self.assertEqual("opaque-request-id", response["request_id"])

    async def test_reflected_unknown_family_cannot_exceed_the_wire_bound(self):
        payload = envelope(message_family="x" * (transport.J02_MAX_WIRE_MESSAGE_BYTES // 2 + 1))
        text = json.dumps(payload)
        self.assertLess(len(text.encode("utf-8")), transport.J02_MAX_WIRE_MESSAGE_BYTES)
        socket, _ = await self.exchange_text(text, {})
        self.assertLessEqual(
            len(socket.wire_responses[0].encode("utf-8")), transport.J02_MAX_WIRE_MESSAGE_BYTES,
        )
        self.assertEqual("result_too_large", socket.responses[0]["error"]["code"])

    async def test_oversized_failure_envelope_is_also_bounded(self):
        handlers, _, executor = route()
        executor.side_effect = lambda request: {"blob": "x" * transport.J02_MAX_WIRE_MESSAGE_BYTES}
        # UTF-8 input fits, but ASCII canonical escaping expands the echoed ID.
        text = json.dumps(envelope(request_id="é" * (transport.J02_MAX_WIRE_MESSAGE_BYTES // 3)), ensure_ascii=False)
        self.assertLess(len(text.encode("utf-8")), transport.J02_MAX_WIRE_MESSAGE_BYTES)
        socket, _ = await self.exchange_text(text, handlers)
        self.assertLessEqual(
            len(socket.wire_responses[0].encode("utf-8")), transport.J02_MAX_WIRE_MESSAGE_BYTES,
        )
        self.assertEqual("result_too_large", socket.responses[0]["error"]["code"])
        self.assertEqual(FAMILY, socket.responses[0]["message_family"])
        self.assertEqual(RESPONSE_VERSION, socket.responses[0]["schema_version"])
        self.assertIsNone(socket.responses[0]["request_id"])

    async def test_unsupported_headers_have_bounded_payload_free_security_evidence(self):
        configuration = security(principal=transport.RemoteJ02Principal("peer", frozenset({SCOPE})))
        configuration.evidence_log._max_events = 2
        handlers, decoder, executor = route()
        messages = [json.dumps(envelope(**header)) for header in (
            {"message_family": "secret-unknown"}, {"schema_version": "secret-version"},
            {"operation": []},
        )]
        socket = Socket(messages)
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        self.assertEqual(3, len(socket.responses))
        self.assertEqual(2, len(configuration.evidence_log.events))
        for event in configuration.evidence_log.events:
            self.assertEqual("authenticated_connection", event.requested_scope)
            self.assertEqual("unsupported_message_header", event.terminal_close_reason)
            self.assertEqual("allow", event.decision)
            self.assertNotIn("secret", repr(event))
        decoder.assert_not_called()
        executor.assert_not_called()

    async def test_training_registration_requires_both_grants_with_the_hook(self):
        family = "j02-training-v1"
        handlers, decoder, executor, hook = self.additional_route()
        handler = next(iter(handlers.values()))
        handler = transport.J02FamilyHandler(
            "j02.training.evaluate", family + "-response-v1", decoder, executor,
            additional_scopes=hook,
        )
        handlers = {(family, family + "-request-v1", "train_evaluate"): handler}
        text = json.dumps(envelope(
            message_family=family, schema_version=family + "-request-v1",
            operation="train_evaluate", request={"register": True},
        ))
        socket, _ = await self.exchange_text(text, handlers, frozenset({"j02.training.evaluate"}))
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        executor.assert_not_called()
        socket, _ = await self.exchange_text(
            text, handlers, frozenset({"j02.training.evaluate", "j02.training.register"}),
        )
        self.assertEqual("ok", socket.responses[0]["status"])
        executor.assert_called_once()

    # -- P2: nothing escapes after the handler ---------------------------------

    async def test_unserializable_result_is_a_typed_integrity_failure_with_request_id(self):
        handlers, _, executor = route()
        for bad in (Decimal("1.5"), b"bytes", object()):
            with self.subTest(bad=type(bad).__name__):
                executor.side_effect = lambda _request, bad=bad: {"value": bad}
                socket, _ = await self.exchange(handlers)
                response = socket.responses[0]
                self.assertEqual("error", response["status"])
                self.assertEqual("integrity_failure", response["error"]["code"])
                self.assertEqual("opaque-request-id", response["request_id"])
                self.assertEqual(FAMILY, response["message_family"])
                self.assertEqual(RESPONSE_VERSION, response["schema_version"])

    async def test_executor_fault_is_integrity_failure_without_raw_exception_text(self):
        handlers, _, executor = route()
        executor.side_effect = RuntimeError("db down: password=hunter2")
        socket, _ = await self.exchange(handlers)
        error = socket.responses[0]["error"]
        self.assertEqual("integrity_failure", error["code"])
        self.assertNotIn("db down", json.dumps(socket.responses[0]))
        self.assertEqual("opaque-request-id", socket.responses[0]["request_id"])

    async def test_decoder_fault_stays_a_client_invalid_request(self):
        handlers, decoder, executor = route()
        decoder.side_effect = KeyError("missing")
        socket, _ = await self.exchange(handlers)
        self.assertEqual("invalid_request", socket.responses[0]["error"]["code"])
        executor.assert_not_called()

    async def test_oversized_result_is_a_typed_result_too_large_not_a_1009_close(self):
        handlers, _, executor = route()
        executor.side_effect = lambda _request: {"blob": "x" * transport.J02_MAX_WIRE_MESSAGE_BYTES}
        socket, _ = await self.exchange(handlers)
        response = socket.responses[0]
        self.assertEqual("result_too_large", response["error"]["code"])
        self.assertEqual("opaque-request-id", response["request_id"])
        self.assertLess(len(json.dumps(response)), 1024)

    def test_integrity_message_matches_the_consumer_api_vocabulary(self):
        from quant_platform.application import market_data
        self.assertEqual(
            market_data._MESSAGES[transport.ConsumerErrorCode.INTEGRITY_FAILURE],
            transport._INTEGRITY_FAILURE_MESSAGE,
        )

    # -- P2: the family body is lossless ----------------------------------------

    async def test_family_body_rejects_floats_nan_and_duplicate_keys_before_decode(self):
        prefix = (
            '{"message_family":"%s","schema_version":"%s","operation":"echo",'
            '"request_id":"opaque-request-id","request":' % (FAMILY, VERSION)
        )
        cases = {
            "float": prefix + '{"price":0.1000000000000000055511151231257827}}',
            "exponent": prefix + '{"price":1e2}}',
            "nan": prefix + '{"price":NaN}}',
            "infinity": prefix + '{"price":-Infinity}}',
            "duplicate": prefix + '{"price":"1","price":"2"}}',
            "nested duplicate": prefix + '{"a":{"b":1,"b":2}}}',
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                handlers, decoder, executor = route()
                socket, _ = await self.exchange_text(text, handlers)
                self.assertEqual("invalid_request", socket.responses[0]["error"]["code"])
                self.assertEqual("opaque-request-id", socket.responses[0]["request_id"])
                decoder.assert_not_called()
                executor.assert_not_called()

    async def test_family_body_preserves_large_integers_and_decimal_strings_exactly(self):
        handlers, decoder, _ = route()
        text = (
            '{"message_family":"%s","schema_version":"%s","operation":"echo",'
            '"request_id":"r","request":{"n":12345678901234567890,'
            '"price":"12345678901234567890.123456789"}}' % (FAMILY, VERSION)
        )
        socket, _ = await self.exchange_text(text, handlers)
        self.assertEqual("ok", socket.responses[0]["status"])
        decoder.assert_called_once_with({
            "n": 12345678901234567890, "price": "12345678901234567890.123456789",
        })

    async def test_legacy_v1_body_keeps_existing_json_semantics(self):
        socket = Socket([json.dumps(request_payload()).replace('"venue"', '"venue":"x","venue"', 1)])
        # A v1 message with a duplicate key is not rejected by the family rule.
        execute = MagicMock(side_effect=ValueError("reached the v1 decoder path"))
        await transport.handle_api_transport_connection(socket, execute=execute)
        self.assertEqual(transport.J02_RESPONSE_SCHEMA_VERSION, socket.responses[0]["schema_version"])

    async def test_each_message_is_parsed_exactly_once(self):
        handlers, _, _ = route()
        with patch.object(transport, "_parse_message", wraps=transport._parse_message) as parse:
            await self.exchange(handlers)
            self.assertEqual(1, parse.call_count)
            parse.reset_mock()
            socket = Socket([json.dumps(request_payload())])
            await transport.handle_api_transport_connection(
                socket, execute=MagicMock(return_value=None),
            )
            self.assertEqual(1, parse.call_count)

    # -- P2: body-dependent second scope (ADR-0069 section 3) -------------------

    def additional_route(self, extra=("j02.training.register",)):
        decoder = MagicMock(side_effect=lambda body: dict(body))
        executor = MagicMock(side_effect=lambda request: request)
        hook = MagicMock(side_effect=lambda decoded: extra if decoded.get("register") else ())
        handler = transport.J02FamilyHandler(
            SCOPE, RESPONSE_VERSION, decoder, executor, additional_scopes=hook,
        )
        return {(FAMILY, VERSION, "echo"): handler}, decoder, executor, hook

    async def test_additional_scope_is_denied_after_decode_and_before_execute(self):
        handlers, decoder, executor, hook = self.additional_route()
        socket, configuration = await self.exchange(
            handlers, request={"register": True},
        )
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        self.assertEqual([], socket.responses)
        decoder.assert_called_once()
        hook.assert_called_once()
        executor.assert_not_called()
        evidence = configuration.evidence_log.events[-1]
        self.assertEqual(("j02.training.register", "deny"), (evidence.requested_scope, evidence.decision))

    async def test_additional_scope_granted_runs_executor_and_records_each_scope(self):
        handlers, _, executor, _ = self.additional_route()
        configuration = security(principal=transport.RemoteJ02Principal(
            "family-peer", frozenset({SCOPE, "j02.training.register"}),
        ))
        configuration.evidence_log._max_events = 8
        socket = Socket([json.dumps(envelope(request={"register": True}))])
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        self.assertEqual("ok", socket.responses[0]["status"])
        executor.assert_called_once()
        self.assertEqual(
            [SCOPE, "j02.training.register"],
            [event.requested_scope for event in configuration.evidence_log.events],
        )

    async def test_request_without_the_flag_needs_no_additional_scope(self):
        handlers, _, executor, hook = self.additional_route()
        socket, _ = await self.exchange(handlers, request={"register": False})
        self.assertEqual("ok", socket.responses[0]["status"])
        hook.assert_called_once()
        executor.assert_called_once()

    async def test_unregistered_or_faulty_additional_scope_hook_is_integrity_failure(self):
        for extra in (("j02.not.registered",), None):
            with self.subTest(extra=extra):
                handlers, _, executor, hook = self.additional_route(extra)
                if extra is None:
                    hook.side_effect = RuntimeError("hook fault")
                socket, _ = await self.exchange(handlers, request={"register": True})
                self.assertEqual("integrity_failure", socket.responses[0]["error"]["code"])
                executor.assert_not_called()

    def test_direct_entry_cannot_authorize_additional_scopes_and_fails_closed(self):
        handlers, _, executor, _ = self.additional_route()
        response = json.loads(transport.handle_api_transport_message(
            json.dumps(envelope(request={"register": True})), execute=MagicMock(),
            family_handler=next(iter(handlers.values())),
        ))
        self.assertEqual("error", response["status"])
        executor.assert_not_called()

    def test_additional_scopes_must_be_callable(self):
        with self.assertRaises(TypeError):
            transport.J02FamilyHandler(
                SCOPE, RESPONSE_VERSION, MagicMock(), MagicMock(), additional_scopes="x",
            )

    # -- P3: evidence ------------------------------------------------------------

    async def test_family_only_session_records_one_real_scope_row_and_no_empty_scope(self):
        handlers, _, _ = route()
        configuration = security(principal=transport.RemoteJ02Principal(
            "family-peer", frozenset({SCOPE}),
        ))
        configuration.evidence_log._max_events = 16
        socket = Socket([json.dumps(envelope()), json.dumps(envelope(request_id="second"))])
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        self.assertEqual(2, len(socket.responses))
        self.assertEqual(
            [SCOPE, SCOPE], [event.requested_scope for event in configuration.evidence_log.events],
        )
        self.assertNotIn("", [event.requested_scope for event in configuration.evidence_log.events])

    async def test_revoked_family_only_principal_is_denied_with_a_non_empty_label(self):
        handlers, _, _ = route()
        configuration = security(principal=transport.RemoteJ02Principal(
            "family-peer", frozenset({SCOPE}), revoked=True,
        ))
        socket = Socket([json.dumps(envelope())])
        await transport.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
        )
        self.assertEqual([(1008, "policy denied")], socket.close_calls)
        self.assertTrue(configuration.evidence_log.events[-1].requested_scope)


class FamilyCompositionTests(unittest.IsolatedAsyncioTestCase):
    """P1 and the startup-time composition findings."""

    async def test_training_route_without_additional_scope_hook_fails_closed(self):
        family = "j02-training-v1"
        decoder = MagicMock(side_effect=lambda body: dict(body))
        executor = MagicMock(side_effect=lambda request: request)
        handler = transport.J02FamilyHandler(
            "j02.training.evaluate", family + "-response-v1", decoder, executor,
        )
        handlers = {(family, family + "-request-v1", "train_evaluate"): handler}
        configuration = security(principal=transport.RemoteJ02Principal(
            "peer", frozenset({"j02.training.evaluate"}),
        ))
        socket = Socket([json.dumps(envelope(
            message_family=family, schema_version=family + "-request-v1",
            operation="train_evaluate", request={"register": True},
        ))])
        with self.assertRaisesRegex(ValueError, "additional_scopes"):
            await transport.handle_api_transport_connection(
                socket, execute=MagicMock(), remote_security=configuration, family_handlers=handlers,
            )
        with patch.object(transport.websockets, "serve") as serve:
            with self.assertRaisesRegex(ValueError, "additional_scopes"):
                await transport.run_api_transport_server(
                    transport.ApiTransportServerConfig(remote_security=configuration),
                    execute=MagicMock(), family_handlers=handlers,
                )
            serve.assert_not_called()
        decoder.assert_not_called()
        executor.assert_not_called()

    async def test_family_routes_are_refused_without_remote_security(self):
        handlers, decoder, executor = route()
        with self.assertRaisesRegex(ValueError, "remote_security"):
            await transport.handle_api_transport_connection(
                Socket([json.dumps(envelope())]), execute=MagicMock(), family_handlers=handlers,
            )
        decoder.assert_not_called()
        executor.assert_not_called()

    async def test_server_refuses_to_start_family_routes_on_the_loopback_listener(self):
        handlers, _, _ = route()
        with patch.object(transport.websockets, "serve") as serve:
            with self.assertRaisesRegex(ValueError, "remote_security"):
                await transport.run_api_transport_server(
                    transport.ApiTransportServerConfig(), execute=MagicMock(),
                    family_handlers=handlers,
                )
        serve.assert_not_called()

    async def test_invalid_composition_fails_at_startup_before_any_listener_exists(self):
        decoder, executor = MagicMock(), MagicMock()
        cases = {
            "scope reassigned": {
                ("j02-strategy-v1", "j02-strategy-v1-request-v1", "compose"): transport.J02FamilyHandler(
                    "j02.market_data.read", "j02-strategy-v1-response-v1", decoder, executor,
                ),
            },
            "response version": {
                ("j02-strategy-v1", "j02-strategy-v1-request-v1", "compose"): transport.J02FamilyHandler(
                    SCOPE, "whatever-v9", decoder, executor,
                ),
            },
            "key shape": {("j02-strategy-v1", "compose"): transport.J02FamilyHandler(
                SCOPE, "j02-strategy-v1-response-v1", decoder, executor,
            )},
        }
        configuration = security()
        for name, handlers in cases.items():
            with self.subTest(case=name), patch.object(transport.websockets, "serve") as serve:
                config = transport.ApiTransportServerConfig(remote_security=configuration)
                with self.assertRaises(ValueError):
                    await transport.run_api_transport_server(
                        config, execute=MagicMock(), family_handlers=handlers,
                    )
                serve.assert_not_called()

    async def test_response_schema_version_is_validated_per_family(self):
        decoder, executor = MagicMock(), MagicMock()
        handler = transport.J02FamilyHandler(SCOPE, "whatever-v9", decoder, executor)
        configuration = security(principal=transport.RemoteJ02Principal("p", frozenset({SCOPE})))
        with self.assertRaisesRegex(ValueError, "response_schema_version"):
            await transport.handle_api_transport_connection(
                Socket([]), execute=MagicMock(), remote_security=configuration,
                family_handlers={(FAMILY, VERSION, "echo"): handler},
            )


@unittest.skipUnless(shutil.which("openssl"), "openssl binary is required to generate a real mTLS fixture")
class FamilyRealMtlsTests(unittest.IsolatedAsyncioTestCase):
    """Families over a real TLS 1.3 / mTLS WebSocket, through server composition."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="p05a_mtls_")
        cwd = Path(cls._tmp.name)
        cls.cwd = cwd
        _generate_ec_key(cwd, "ca.key")
        _self_signed_ca(cwd, key="ca.key", out="ca.crt", cn="P05a Test CA")
        _generate_ec_key(cwd, "server.key")
        (cwd / "server_ext.cnf").write_text("subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        _signed_certificate(
            cwd, key="server.key", csr="server.csr", cn="127.0.0.1",
            ca_cert="ca.crt", ca_key="ca.key", out="server.crt", extfile="server_ext.cnf",
        )
        _generate_ec_key(cwd, "client.key")
        _signed_certificate(
            cwd, key="client.key", csr="client.csr", cn="p05a-client",
            ca_cert="ca.crt", ca_key="ca.key", out="client.crt",
        )
        cls.fingerprint = _certificate_fingerprint(cwd, "client.crt")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _security(self, scopes):
        return transport.RemoteJ02SecurityConfig(
            server_certificate_path=str(self.cwd / "server.crt"),
            server_private_key_path=str(self.cwd / "server.key"),
            client_trust_anchor_path=str(self.cwd / "ca.crt"),
            server_trust_bundle_version="p05a-bundle-v1",
            authorization_policy_version="p05a-policy-v1",
            principals_by_fingerprint={
                self.fingerprint: transport.RemoteJ02Principal("p05a-peer", scopes),
            },
            evidence_log=transport.RemoteJ02SecurityEvidenceLog(max_events=64),
        )

    def _client_ssl(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_verify_locations(cafile=str(self.cwd / "ca.crt"))
        context.load_cert_chain(str(self.cwd / "client.crt"), str(self.cwd / "client.key"))
        return context

    async def _run(self, scopes, handlers, session):
        """Start the real server composition on an ephemeral TLS port and run ``session(url)``."""
        stop = asyncio.Event()
        ready = asyncio.Event()
        captured = {}
        real_serve = transport.websockets.serve

        class Listener:
            def __init__(self, handler, host, kwargs):
                self.context = real_serve(handler, host, 0, **kwargs)

            async def __aenter__(self):
                server = await self.context.__aenter__()
                captured["port"] = server.sockets[0].getsockname()[1]
                ready.set()
                return server

            async def __aexit__(self, *args):
                return await self.context.__aexit__(*args)

        def listener(handler, host, _port, **kwargs):
            return Listener(handler, host, kwargs)

        configuration = self._security(scopes)
        with patch.object(transport.websockets, "serve", listener):
            task = asyncio.create_task(transport.run_api_transport_server(
                transport.ApiTransportServerConfig(remote_security=configuration),
                stop_event=stop, execute=MagicMock(), family_handlers=handlers,
            ))
            try:
                await asyncio.wait_for(ready.wait(), 10)
                await session(f"wss://127.0.0.1:{captured['port']}")
            finally:
                stop.set()
                await task
        return configuration

    async def test_family_only_principal_exchanges_over_real_wss_mtls(self):
        handlers, decoder, executor = route()
        responses = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                for request_id in ("first", "second"):
                    await socket.send(json.dumps(envelope(request_id=request_id)))
                    responses.append(json.loads(await asyncio.wait_for(socket.recv(), 10)))

        configuration = await self._run(frozenset({SCOPE}), handlers, session)
        self.assertEqual(["first", "second"], [item["request_id"] for item in responses])
        self.assertEqual([envelope()["request"]] * 2, [item["result"] for item in responses])
        self.assertEqual(2, decoder.call_count)
        self.assertEqual(2, executor.call_count)
        events = configuration.evidence_log.events
        self.assertEqual([SCOPE, SCOPE], [event.requested_scope for event in events])
        self.assertTrue(all(event.tls_version == "TLSv1.3" for event in events))

    async def test_missing_family_scope_is_policy_closed_over_real_wss_mtls(self):
        handlers, decoder, executor = route()
        outcome = {}

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                await socket.send(json.dumps(envelope(request=["not a request"])))
                try:
                    await asyncio.wait_for(socket.recv(), 10)
                except websockets.ConnectionClosed as closed:
                    outcome["code"] = closed.rcvd.code

        configuration = await self._run(frozenset({"j02.jobs.read"}), handlers, session)
        self.assertEqual(1008, outcome["code"])
        decoder.assert_not_called()
        executor.assert_not_called()
        self.assertEqual(SCOPE, configuration.evidence_log.events[-1].requested_scope)
        self.assertEqual("deny", configuration.evidence_log.events[-1].decision)


if __name__ == "__main__":
    unittest.main()
