"""J12 codec, identity refusals and real TLS 1.3/mTLS carriage (#338)."""

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

from quant_platform.application import validation_transport as codec  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError  # noqa: E402
from quant_platform.application.validation_consumer import (  # noqa: E402
    ValidationBuildFoldsRequest, ValidationClassifyCandidateRequest,
    ValidationEvaluateDsrRequest, ValidationEvaluatePboRequest,
    execute_validation_build_folds, execute_validation_classify_candidate,
    execute_validation_evaluate_dsr, execute_validation_evaluate_pbo,
)
from quant_platform.canonical import canonical_bytes  # noqa: E402
from quant_platform.validation import (  # noqa: E402
    ComparableTrialPanel, CoverageInterval, DependencyEvidence,
    EffectiveTrialCountEvidence, Embargo, ValidationCandidate,
)
from test_api_transport_families_v1 import _FamilyMtlsFixture  # noqa: E402
from test_validation_consumer_v1 import panel, schedule  # noqa: E402
from test_validation_robustness_v1 import _panel, _pbo_fixture_panel  # noqa: E402

EXECUTORS = {
    "build_folds": execute_validation_build_folds,
    "classify_candidate": execute_validation_classify_candidate,
    "evaluate_dsr": execute_validation_evaluate_dsr,
    "evaluate_pbo": execute_validation_evaluate_pbo,
}
SCOPE = frozenset({"j02.validation.evaluate"})


def envelope(operation, body, request_id="j12-request"):
    return {
        "message_family": codec.J02_VALIDATION_FAMILY,
        "schema_version": codec.J02_VALIDATION_REQUEST_VERSION,
        "operation": operation, "request_id": request_id, "request": body,
    }


def result_payload(result):
    if type(result) is tuple:
        return [fold.stable_dict() for fold in result]
    return result.canonical_payload() if hasattr(result, "canonical_payload") else result.stable_dict()


def canonical(value):
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False)


def vectors():
    folds = ValidationBuildFoldsRequest(schedule())
    fold = execute_validation_build_folds(folds)[0]
    classification = ValidationClassifyCandidateRequest(fold, Embargo(0), ValidationCandidate(fold.test.start))
    yield "build_folds", folds
    yield "classify_candidate", classification
    yield "classify_candidate", replace(classification, candidate=ValidationCandidate(fold.test.start, (
        DependencyEvidence("feature:one", "decision_time", support=CoverageInterval(fold.train.start, fold.train.end),
                           required_maturity="AVAILABLE", lifecycle="FINAL", causal_available_at=fold.train.end,
                           observed_available_at=fold.train.end, observed_finalized_at=fold.train.end,
                           contemporaneous_version_proven=True, detail="observed"),
        DependencyEvidence("feature:missing", "decision_time", sufficient=False, detail="no support"),
    )))
    yield "evaluate_dsr", ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1"))
    yield "evaluate_pbo", ValidationEvaluatePboRequest(panel(), 4)
    short = panel(trial_count=1, observation_count=3)
    yield "evaluate_dsr", ValidationEvaluateDsrRequest(short, EffectiveTrialCountEvidence(1.0, "keff-v1"))
    typed = ComparableTrialPanel("population:typed", ("binary64",), (1, "1", -2, "é"),
                                 {"binary64": (-0.0, 5e-324, 1e-7, 2.0)}, "returns-v1")
    yield "evaluate_dsr", ValidationEvaluateDsrRequest(typed, EffectiveTrialCountEvidence(1.0, "keff-v1"))
    # Reuse the existing F08 evidence, including the ADR-0037 pinned CSCV vector.
    yield "evaluate_dsr", ValidationEvaluateDsrRequest(_panel(), EffectiveTrialCountEvidence(2.0, "evidence-v1:manual"))
    yield "evaluate_pbo", ValidationEvaluatePboRequest(_pbo_fixture_panel(), 4)


def invalid_bodies():
    original = ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1"))
    body = codec.encode_validation_request(original)
    yield dict(body, request_identity="wrong")
    yield dict(body, path="C:/data/private")
    for path, value in (
        (("identity_domain",), "wrong"),
        (("panel", "identity_domain"), "wrong"),
        (("panel", "population_id"), "changed"),
        (("panel", "observation_ids"), [["int", "+1"], ["str", "b"], ["str", "c"], ["str", "d"]]),
        (("effective_trial_count", "k_eff"), {"binary64": "nan"}),
        (("effective_trial_count", "k_eff"), {"binary64": "inf"}),
        (("effective_trial_count", "k_eff"), {"binary64": "0x1p+1"}),
        (("effective_trial_count", "k_eff"), {"binary64": "0x1p+100000"}),
        (("effective_trial_count", "k_eff"), {"binary64": True}),
        (("effective_trial_count", "k_eff"), {"binary64": None}),
        (("effective_trial_count", "k_eff"), "2.0"),
        (("effective_trial_count", "k_eff"), 2),
        (("effective_trial_count", "k_eff"), True),
    ):
        altered = deepcopy(body)
        parent = altered["payload"]
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
        yield altered
    altered = deepcopy(body)
    altered["payload"]["panel"]["returns"]["trial-0"][0] = {"binary64": (0.5).hex()}
    yield altered
    altered = deepcopy(body)
    altered["payload"]["panel"]["observation_ids"][0] = ["int", "01"]
    # Even an identity computed from a malformed canonical body cannot legitimize
    # constructor normalization. The codec must compare reconstructed bytes.
    altered["request_identity"] = codec._request("evaluate_dsr", codec._unwire(altered["payload"])).request_identity
    yield altered


class ValidationCodecTests(unittest.TestCase):
    def test_each_codec_preserves_canonical_request_bytes_identity_and_result(self):
        for operation, original in vectors():
            with self.subTest(operation=operation):
                body = json.loads(json.dumps(codec.encode_validation_request(original)))
                before = deepcopy(body)
                decoded = codec.decode_validation_request(operation, body)
                self.assertEqual(before, body)
                self.assertEqual(canonical(original.canonical_payload()), canonical(decoded.canonical_payload()))
                self.assertEqual(original.request_identity, decoded.request_identity)
                result = EXECUTORS[operation](original)
                decoded_result = EXECUTORS[operation](decoded)
                self.assertEqual(canonical(result_payload(result)), canonical(result_payload(decoded_result)))
                wire_result = json.loads(json.dumps(codec.encode_validation_result(result)))
                self.assertEqual(canonical(result_payload(result)), canonical(codec.decode_validation_result(wire_result)))
                self.assertEqual(getattr(result, "result_id", None), wire_result["result_identity"])

    def test_binary64_encoding_preserves_signed_zero_subnormal_and_large_values(self):
        numbers = [-0.0, 0.0, 5e-324, -5e-324, 1.7976931348623157e308, 1e-7, 2.0]
        p = ComparableTrialPanel("population:numbers", ("binary64",), tuple(range(len(numbers))),
                                 {"binary64": tuple(numbers)}, "returns-v1")
        original = ValidationEvaluatePboRequest(p, 4)
        body = codec.encode_validation_request(original)
        decoded = codec.decode_validation_request("evaluate_pbo", json.loads(json.dumps(body)))
        self.assertEqual([number.hex() for number in numbers], [number.hex() for number in decoded.panel.returns["binary64"]])
        self.assertEqual(original.request_identity, decoded.request_identity)
        self.assertEqual(original.panel.content_digest, decoded.panel.content_digest)
        self.assertEqual("str", codec._unwire("str"))
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                codec._wire(invalid)

    def test_invalid_identity_or_representation_never_reaches_executor(self):
        for body in invalid_bodies():
            with self.subTest(body=body), patch.object(codec, "execute_validation_evaluate_dsr") as execute:
                execute.return_value = execute_validation_evaluate_dsr(
                    ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1")),
                )
                handler = codec.validation_family_handlers()[(codec.J02_VALIDATION_FAMILY, codec.J02_VALIDATION_REQUEST_VERSION, "evaluate_dsr")]
                with self.assertRaises(ConsumerApiError) as caught:
                    handler.execute(handler.decode_request(body))
                self.assertEqual("invalid_request", caught.exception.code.value)
                execute.assert_not_called()

    def test_temporal_and_integer_fields_are_not_coerced(self):
        for operation, original in list(vectors())[:2]:
            body = codec.encode_validation_request(original)
            cases = []
            altered = deepcopy(body)
            altered["payload"]["identity_domain"] = "wrong"
            cases.append(altered)
            if operation == "build_folds":
                for value in (True, "1", {"binary64": (1.0).hex()}):
                    altered = deepcopy(body)
                    altered["payload"]["schedule"]["fold_count"] = value
                    cases.append(altered)
            else:
                altered = deepcopy(body)
                altered["payload"]["fold"]["test"]["start"] = "2025-01-01T00:00:00Z"
                cases.append(altered)
            for altered in cases:
                with self.subTest(operation=operation, body=altered), self.assertRaises(ConsumerApiError):
                    codec.decode_validation_request(operation, altered)

    def test_result_identity_mismatch_is_rejected(self):
        for operation, original in list(vectors())[3:5]:
            body = codec.encode_validation_result(EXECUTORS[operation](original))
            body["result_identity"] = "wrong"
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                codec.decode_validation_result(body)


class ValidationWssTests(_FamilyMtlsFixture):
    async def exchange(self, requests):
        responses = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl(), max_size=16 * 1024 * 1024) as socket:
                for index, (operation, body) in enumerate(requests):
                    wire = json.dumps(envelope(operation, body, str(index)), separators=(",", ":"))
                    self.assertLess(len(wire.encode("utf-8")), 16 * 1024 * 1024)
                    await socket.send(wire)
                    response_wire = await asyncio.wait_for(socket.recv(), 30)
                    self.assertLess(len(response_wire.encode("utf-8")), 16 * 1024 * 1024)
                    response = json.loads(response_wire)
                    self.assertEqual(str(index), response["request_id"])
                    self.assertEqual(codec.J02_VALIDATION_FAMILY, response["message_family"])
                    self.assertEqual(codec.J02_VALIDATION_RESPONSE_VERSION, response["schema_version"])
                    responses.append(response)

        configuration = await self._run(SCOPE, codec.validation_family_handlers(), session)
        self.assertTrue(all(event.tls_version == "TLSv1.3" for event in configuration.evidence_log.events))
        self.assertTrue(all(event.requested_scope == "j02.validation.evaluate" for event in configuration.evidence_log.events))
        return responses

    async def test_four_operations_match_local_results_over_real_wss(self):
        requests = list(vectors())
        responses = await self.exchange([(op, codec.encode_validation_request(req)) for op, req in requests])
        for (operation, original), response in zip(requests, responses):
            with self.subTest(operation=operation):
                self.assertEqual("ok", response["status"])
                self.assertEqual(canonical(result_payload(EXECUTORS[operation](original))),
                                 canonical(codec.decode_validation_result(response["result"])))

    async def test_16_block_boundary_round_trips_under_16_mib(self):
        request = ValidationEvaluatePboRequest(panel(trial_count=10, observation_count=16), 16)
        result = execute_validation_evaluate_pbo(request)
        self.assertEqual(12870, len(result.splits))
        response = (await self.exchange([("evaluate_pbo", codec.encode_validation_request(request))]))[0]
        self.assertEqual("ok", response["status"])
        self.assertEqual(result.result_id, response["result"]["result_identity"])
        self.assertEqual(canonical(result.canonical_payload()), canonical(codec.decode_validation_result(response["result"])))

    async def test_over_budget_keeps_owned_reason_identity_and_never_evaluates_or_admits_job(self):
        requests = [ValidationEvaluatePboRequest(panel(observation_count=18), 18),
                    ValidationEvaluatePboRequest(panel(trial_count=100, observation_count=1200), 12)]
        with patch("quant_platform.application.validation_consumer.evaluate_pbo_v1") as evaluator, \
             patch("quant_platform.application.durable_jobs.DurableJobStore.admit") as admit:
            responses = await self.exchange([("evaluate_pbo", codec.encode_validation_request(req)) for req in requests])
        evaluator.assert_not_called()
        admit.assert_not_called()
        for request, response in zip(requests, responses):
            self.assertEqual("error", response["status"])
            self.assertEqual("invalid_request", response["error"]["code"])
            self.assertEqual({"reason": "pbo_sync_budget_exceeded"}, response["error"]["context"])
            self.assertEqual(request.request_identity, response["error"]["request_identity"])

    async def test_identity_and_numeric_mutations_are_refused_before_executor(self):
        with patch.object(codec, "execute_validation_evaluate_dsr") as execute:
            responses = await self.exchange([("evaluate_dsr", body) for body in invalid_bodies()])
        execute.assert_not_called()
        self.assertTrue(all(response["status"] == "error" for response in responses))
        self.assertTrue(all(response["error"]["code"] == "invalid_request" for response in responses))

    async def test_other_family_scopes_cannot_decode_j12_even_malformed_numeric_evidence(self):
        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                await socket.send(json.dumps(envelope("evaluate_dsr", {"payload": {"binary64": "nan"}})))
                with self.assertRaises(websockets.ConnectionClosed) as caught:
                    await asyncio.wait_for(socket.recv(), 10)
                self.assertEqual(1008, caught.exception.rcvd.code)

        for scope in ("j02.market_data.read", "j02.strategy.compose", "j02.training.evaluate"):
            with self.subTest(scope=scope), patch.object(codec, "decode_validation_request") as decode, \
                 patch.object(codec, "execute_validation_evaluate_dsr") as execute:
                await self._run(frozenset({scope}), codec.validation_family_handlers(), session)
            decode.assert_not_called()
            execute.assert_not_called()

    async def test_raw_floats_nonfinite_and_duplicate_fields_still_fail_before_decoder(self):
        original = ValidationEvaluateDsrRequest(panel(), EffectiveTrialCountEvidence(2.0, "keff-v1"))
        body = codec.encode_validation_request(original)
        messages = []
        for numeric in (2.0, float("nan"), float("inf")):
            changed = deepcopy(body)
            changed["payload"]["effective_trial_count"]["k_eff"] = numeric
            messages.append(json.dumps(envelope("evaluate_dsr", changed)))
        valid = json.dumps(envelope("evaluate_dsr", body))
        messages.append(valid.replace('"request_identity":', '"request_identity": "duplicate", "request_identity":', 1))

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                for message in messages:
                    await socket.send(message)
                    response = json.loads(await asyncio.wait_for(socket.recv(), 10))
                    self.assertEqual("invalid_request", response["error"]["code"])

        with patch.object(codec, "decode_validation_request") as decode:
            await self._run(SCOPE, codec.validation_family_handlers(), session)
        decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
