"""J10 domain reconstruction and real authenticated WSS carriage, issue #337."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.application import strategy_transport as codec  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.strategy_consumer import execute_strategy_compose  # noqa: E402
from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.strategy import Direction, StrategyInput  # noqa: E402
from test_api_transport_families_v1 import _FamilyMtlsFixture  # noqa: E402
from test_strategy_consumer_api_v1 import request  # noqa: E402


def envelope(body, request_id="j10-request"):
    return {
        "message_family": codec.J02_STRATEGY_FAMILY,
        "schema_version": codec.J02_STRATEGY_REQUEST_VERSION,
        "operation": "compose", "request_id": request_id, "request": body,
    }


def invalid_payloads():
    body = request().canonical_payload()
    cases = []
    for key, value in (("path", "/tmp/strategy.py"), ("callable", "module.function"), ("extra", 1)):
        cases.append(dict(body, **{key: value}))
    for field in ("entry_policy", "exit_policy", "position_policy", "signal_combination_policy"):
        altered = deepcopy(body)
        altered["strategy_spec"][field]["path"] = "/tmp/policy"
        cases.append(altered)
    altered = deepcopy(body)
    altered["inputs"][0]["callable"] = "module.function"
    cases.append(altered)
    for field in ("sizing_policy", "risk_policy", "session_policy", "cooldown_policy"):
        altered = deepcopy(body)
        altered["strategy_spec"][field]["identity"] = "wrong"
        cases.append(altered)
        altered = deepcopy(body)
        altered["strategy_spec"][field]["payload"]["extra"] = True
        cases.append(altered)
    altered = deepcopy(body)
    altered["strategy_spec"]["identity_version"] = "2"
    cases.append(altered)
    altered = deepcopy(body)
    altered["inputs"].append(deepcopy(altered["inputs"][0]))
    cases.append(altered)
    altered = deepcopy(body)
    altered["strategy_spec"]["cooldown_policy"]["payload"]["cooldown_blocks_new_entries_only"] = 1
    cases.append(altered)
    return cases


class StrategyCodecTests(unittest.TestCase):
    def test_reconstructed_request_preserves_all_canonical_evidence(self):
        original = request()
        decoded = codec.decode_strategy_compose_request(original.canonical_payload())
        self.assertEqual(original.canonical_payload(), decoded.canonical_payload())
        self.assertEqual(original.request_identity, decoded.request_identity)
        self.assertEqual(original.inputs[0].identity, decoded.inputs[0].identity)
        self.assertEqual(
            execute_strategy_compose(original).stable_dict(),
            execute_strategy_compose(decoded).stable_dict(),
        )

    def test_mutated_strategy_content_is_rejected_before_executor(self):
        body = request().canonical_payload()
        body["strategy_spec"]["strategy_key"] = "different.strategy"
        with patch.object(codec, "execute_strategy_compose") as execute:
            handler = next(iter(codec.strategy_family_handlers().values()))
            with self.assertRaises(ConsumerApiError) as caught:
                handler.decode_request(body)
            self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, caught.exception.code)
            execute.assert_not_called()

    def test_invalid_structural_fields_never_reach_execution(self):
        for body in invalid_payloads():
            with self.subTest(body=body), patch.object(codec, "execute_strategy_compose") as execute:
                handler = next(iter(codec.strategy_family_handlers().values()))
                with self.assertRaises(ConsumerApiError) as caught:
                    handler.decode_request(body)
                self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, caught.exception.code)
                execute.assert_not_called()

    def test_direct_codec_rejects_executable_objects_and_lossy_numeric_values(self):
        for value in (lambda: True, Path("strategy.py"), 1.5, float("nan")):
            body = request().canonical_payload()
            body["inputs"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ConsumerApiError):
                codec.decode_strategy_compose_request(body)

    def test_opaque_execution_policy_is_carried_not_interpreted(self):
        class Rules:
            identity = "consumer-rules-v1:sha256:" + "a" * 64

            def stable_dict(self):
                return {"rule": {"threshold": "0.000000000000000000123"}, "mode": "consumer-owned"}

        original = request()
        spec = replace(original.strategy_spec, execution_policy=Rules())
        original = replace(original, strategy_spec=spec, strategy_identity=spec.strategy_identity)
        decoded = codec.decode_strategy_compose_request(original.canonical_payload())
        self.assertEqual(original.request_identity, decoded.request_identity)
        self.assertEqual(spec.canonical_payload(), decoded.strategy_spec.canonical_payload())
        self.assertEqual(execute_strategy_compose(original).stable_dict(), execute_strategy_compose(decoded).stable_dict())


class StrategyWssTests(_FamilyMtlsFixture):
    async def exchange_bodies(self, bodies, *, scopes=frozenset({"j02.strategy.compose"})):
        responses = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                for index, body in enumerate(bodies):
                    wire = json.dumps(envelope(body, str(index)), separators=(",", ":"))
                    self.assertLess(len(wire.encode("utf-8")), 16 * 1024 * 1024)
                    await socket.send(wire)
                    response_wire = await asyncio.wait_for(socket.recv(), 10)
                    self.assertLess(len(response_wire.encode("utf-8")), 16 * 1024 * 1024)
                    response = json.loads(response_wire)
                    self.assertEqual(str(index), response["request_id"])
                    self.assertEqual(codec.J02_STRATEGY_FAMILY, response["message_family"])
                    self.assertEqual(codec.J02_STRATEGY_RESPONSE_VERSION, response["schema_version"])
                    responses.append(response)

        configuration = await self._run(scopes, codec.strategy_family_handlers(), session)
        self.assertTrue(all(event.tls_version == "TLSv1.3" for event in configuration.evidence_log.events))
        return responses

    async def test_existing_j10_vectors_preserve_result_identity_for_identity(self):
        original = request()
        short_spec = replace(
            original.strategy_spec,
            entry_policy=replace(original.strategy_spec.entry_policy, direction=Direction.SHORT, confidence="0.123456789"),
            position_policy=replace(original.strategy_spec.position_policy, short_target_position="0.00000000123"),
        )
        vectors = [
            original, request(value=False), replace(original, inputs=()),
            replace(original, inputs=original.inputs + (StrategyInput(
                "signal.exit", True, original.decision_time, "feature:exit",
            ),)),
            replace(original, strategy_spec=short_spec, strategy_identity=short_spec.strategy_identity),
            replace(original, decision_time=Instant.parse("2024-01-02T12:00:00.123456789Z"), instrument="BTC-é"),
        ]
        responses = await self.exchange_bodies([item.canonical_payload() for item in vectors])
        for item, response in zip(vectors, responses):
            self.assertEqual("ok", response["status"])
            self.assertEqual(execute_strategy_compose(item).stable_dict(), response["result"])

    async def test_invalid_content_and_fields_fail_before_domain_execution_over_wss(self):
        changed = request().canonical_payload()
        changed["strategy_spec"]["entry_policy"]["confidence"] = "0.5"
        with patch.object(codec, "execute_strategy_compose") as execute:
            responses = await self.exchange_bodies([changed, request(identity="wrong").canonical_payload()] + invalid_payloads())
        execute.assert_not_called()
        self.assertTrue(all(item["status"] == "error" for item in responses))
        self.assertTrue(all(item["error"]["code"] == "invalid_request" for item in responses))

    async def test_future_availability_preserves_owned_no_coverage_error(self):
        original = request()
        original = replace(original, inputs=(replace(
            original.inputs[0], available_at=Instant.parse("2024-01-02T12:00:01Z"),
        ),))
        with self.assertRaises(ConsumerApiError) as caught:
            execute_strategy_compose(original)
        response = (await self.exchange_bodies([original.canonical_payload()]))[0]
        self.assertEqual("error", response["status"])
        self.assertEqual({
            "code": caught.exception.code.value, "message": caught.exception.message,
            "context": dict(caught.exception.context), "request_identity": original.request_identity,
        }, response["error"])

    async def test_market_data_grant_does_not_authorize_strategy_decoder(self):
        outcome = {}

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                await socket.send(json.dumps(envelope(request().canonical_payload())))
                with self.assertRaises(websockets.ConnectionClosed) as caught:
                    await asyncio.wait_for(socket.recv(), 10)
                outcome["code"] = caught.exception.rcvd.code

        with patch.object(codec, "decode_strategy_compose_request") as decode:
            await self._run(frozenset({"j02.market_data.read"}), codec.strategy_family_handlers(), session)
        self.assertEqual(1008, outcome["code"])
        decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
