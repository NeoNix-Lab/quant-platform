"""J13 reconstruction codec and real authenticated WSS carriage, issue #339 (P05a)."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import websockets
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.application import training_transport as codec  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.training_consumer import (  # noqa: E402
    MAX_SYNC_TRAINING_TEST_SAMPLES,
    SupervisedTrainEvaluateRequest,
    execute_supervised_train_evaluate,
)
from quant_platform.experiments import ExperimentRepository, RunState  # noqa: E402
from test_api_transport_families_v1 import _FamilyMtlsFixture  # noqa: E402
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_learning_supervised_training_v1 import projection, run_identity  # noqa: E402
from test_training_consumer_v1 import expanded_projection, policy, request_for  # noqa: E402

EVALUATE = frozenset({"j02.training.evaluate"})
BOTH = frozenset({"j02.training.evaluate", "j02.training.register"})


def wire_body(request, *, register=False):
    """What a peer sends: the encoder's body, through real JSON."""
    return json.loads(json.dumps(codec.encode_train_evaluate_request(request, register=register)))


def with_values(request, convert, *, execution_id="converted"):
    """The same request with every feature value replaced natively (identity may change)."""
    samples = tuple(
        replace(sample, feature_inputs=tuple(
            replace(feature, observation=replace(feature.observation, value=convert(feature.observation.value)))
            for feature in sample.feature_inputs
        ))
        for sample in request.projection.samples
    )
    converted = replace(request.projection, samples=samples)
    return SupervisedTrainEvaluateRequest(
        projection=converted, run_identity=run_identity(converted.identity, execution_id=execution_id),
        policy=request.policy,
    )


def native_types(request):
    return [type(f.observation.value) for s in request.projection.samples for f in s.feature_inputs]


class CodecTests(unittest.TestCase):
    def test_a_request_round_trips_to_an_equal_request_with_the_same_identity(self) -> None:
        request = request_for(projection())
        decoded = codec.decode_train_evaluate_request(wire_body(request))
        self.assertEqual(request, decoded.request)
        self.assertEqual(request.request_identity, decoded.request.request_identity)
        self.assertFalse(decoded.register)
        self.assertTrue(codec.decode_train_evaluate_request(wire_body(request, register=True)).register)

    def test_the_domain_canonical_payload_and_identity_are_unchanged_by_the_transport(self) -> None:
        request = request_for(projection())
        body = codec.encode_train_evaluate_request(request, register=True)
        canonical = request.canonical_payload()
        self.assertEqual(canonical, {key: body[key] for key in canonical})
        self.assertEqual({"register", "reconstruction"}, set(body) - set(canonical))
        self.assertEqual(
            "j13-reconstruction-v1", body["reconstruction"]["version"],
        )
        self.assertEqual(request.request_identity, request_for(projection()).request_identity)

    def test_native_value_types_survive_where_the_canonical_form_is_ambiguous(self) -> None:
        base = request_for(projection())
        cases = (
            ("string", lambda value: str(value), str),
            ("decimal", lambda value: Decimal(str(value)), Decimal),
            ("fraction", lambda value: Fraction(str(value)), Fraction),
            ("integer", lambda value: int(Decimal(str(value))), int),
        )
        identities = {}
        for label, convert, native in cases:
            with self.subTest(label):
                request = with_values(base, convert, execution_id=label)
                decoded = codec.decode_train_evaluate_request(wire_body(request)).request
                self.assertEqual(set(native_types(request)), {native})
                self.assertEqual(native_types(request), native_types(decoded))
                self.assertEqual(request, decoded)
                identities[label] = request.projection.samples[0].feature_inputs[0].stable_dict()["value"]
        # A Decimal and the literal string of the same text have one canonical form
        # (so one identity); the evidence is what keeps them apart on the wire.
        decimal_request = with_values(base, lambda value: Decimal(str(value)), execution_id="same")
        string_request = with_values(base, lambda value: str(value), execution_id="same")
        self.assertEqual(decimal_request.canonical_payload(), string_request.canonical_payload())
        self.assertEqual(decimal_request.request_identity, string_request.request_identity)
        self.assertNotEqual(
            wire_body(decimal_request)["reconstruction"], wire_body(string_request)["reconstruction"],
        )

    def test_a_decimal_keeps_the_text_the_canonical_form_cannot_normalize_away(self) -> None:
        request = with_values(request_for(projection()), lambda value: Decimal("1.50"), execution_id="trailing")
        decoded = codec.decode_train_evaluate_request(wire_body(request)).request
        self.assertEqual("1.50", str(decoded.projection.samples[0].feature_inputs[0].observation.value))

    def test_the_decoded_request_executes_to_the_in_process_result(self) -> None:
        request = request_for(projection())
        decoded = codec.decode_train_evaluate_request(wire_body(request)).request
        self.assertEqual(
            execute_supervised_train_evaluate(request).stable_dict(),
            execute_supervised_train_evaluate(decoded).stable_dict(),
        )

    def test_every_mutated_or_missing_piece_of_evidence_is_refused(self) -> None:
        base = wire_body(request_for(projection()))

        def edit(path_edit):
            def mutated():
                body = deepcopy(base)
                path_edit(body)
                return body
            return mutated

        recon = lambda body: body["reconstruction"]  # noqa: E731
        sample0 = lambda body: body["reconstruction"]["samples"][0]  # noqa: E731
        feature0 = lambda body: body["reconstruction"]["samples"][0]["features"][0]  # noqa: E731
        run_spec = lambda body: body["reconstruction"]["run_identity"]["run_spec"]  # noqa: E731

        def drop(getter, key):
            return edit(lambda body: getter(body).pop(key))

        def put(getter, key, value):
            return edit(lambda body: getter(body).__setitem__(key, value))

        cases = {
            "no reconstruction": edit(lambda body: body.pop("reconstruction")),
            "no register": edit(lambda body: body.pop("register")),
            "register as text": edit(lambda body: body.__setitem__("register", "true")),
            "register as integer": edit(lambda body: body.__setitem__("register", 1)),
            "unknown top-level member": edit(lambda body: body.__setitem__("note", "x")),
            "unknown reconstruction member": put(recon, "extra", 1),
            "other reconstruction version": put(recon, "version", "j13-reconstruction-v2"),
            "other identity domain": edit(lambda body: body.__setitem__("identity_domain", "x")),
            # run identity preimages
            "study version": edit(lambda body: recon(body)["run_identity"]["study"].__setitem__(
                "study_definition_version", "study-definition-v2")),
            "study research identity": edit(lambda body: recon(body)["run_identity"]["study"][
                "research_identity"].__setitem__("identity", "another")),
            "trial assignment": edit(lambda body: recon(body)["run_identity"]["trial_assignments"].__setitem__(
                "model_family", "other")),
            "extra trial assignment": edit(lambda body: recon(body)["run_identity"]["trial_assignments"].__setitem__(
                "extra", 1)),
            "code identity": edit(lambda body: run_spec(body)["code_identity"].__setitem__("identity", "other")),
            "a data identity removed": edit(lambda body: run_spec(body)["data_identities"].clear()),
            "run configuration": put(run_spec, "explicit_run_configuration", {"seed": 7}),
            "environment removed": put(run_spec, "environment_identity", None),
            "run spec hash": edit(lambda body: body["run_identity"].__setitem__("run_spec_identity", "0" * 64)),
            # labels
            "label status": edit(lambda body: sample0(body)["label"].__setitem__("status", "no_value_censored")),
            "label value": edit(lambda body: sample0(body)["label"].__setitem__("value", "1")),
            "label source": edit(lambda body: sample0(body)["label"].__setitem__("source_outcome_id", "outcome-v1:sha256:" + "1" * 64)),
            "label missing member": edit(lambda body: sample0(body)["label"].pop("horizon_end")),
            # support identity preimages and value evidence
            "support offset": edit(lambda body: feature0(body)["support_identity"]["support_reference"].__setitem__("offset", 5)),
            "support kind": edit(lambda body: feature0(body)["support_identity"]["support_reference"].__setitem__("kind", "point")),
            "support contract": edit(lambda body: feature0(body)["support_identity"].__setitem__("input_contract_identity", "x")),
            "support observation": edit(lambda body: feature0(body)["support_identity"].__setitem__("observation_identity", "x")),
            "unknown value type": put(feature0, "value_type", "float"),
            "decimal evidence on non-decimal text": edit(
                lambda body: (body["projection"]["samples"][0]["feature_inputs"][0].__setitem__("value", "abc"),
                              feature0(body).__setitem__("value_type", "decimal"))),
            "decimal evidence on a non-finite value": edit(
                lambda body: (body["projection"]["samples"][0]["feature_inputs"][0].__setitem__("value", "NaN"),
                              feature0(body).__setitem__("value_type", "decimal"))),
            "integer evidence on text": put(feature0, "value_type", "integer"),
            "boolean evidence on text": put(feature0, "value_type", "boolean"),
            "null evidence on a value": put(feature0, "value_type", "null"),
            "fraction evidence on a non-reduced fraction": edit(
                lambda body: (body["projection"]["samples"][0]["feature_inputs"][0].__setitem__("value", "2/4"),
                              feature0(body).__setitem__("value_type", "fraction"))),
            # shape of the evidence
            "fewer sample evidence": edit(lambda body: recon(body)["samples"].pop()),
            "more sample evidence": edit(lambda body: recon(body)["samples"].append(deepcopy(sample0(body)))),
            "fewer feature evidence": edit(lambda body: sample0(body)["features"].pop()),
            "more feature evidence": edit(lambda body: sample0(body)["features"].append(deepcopy(feature0(body)))),
            # evidence the constructors would normalize into the right object is still not canonical
            "padded study version": edit(lambda body: recon(body)["run_identity"]["study"].__setitem__(
                "study_definition_version", " study-definition-v1 ")),
            "padded code identity": edit(lambda body: run_spec(body)["code_identity"].__setitem__(
                "identity", " " + run_spec(body)["code_identity"]["identity"] + " ")),
            "padded support contract": edit(lambda body: feature0(body)["support_identity"].__setitem__(
                "input_contract_identity", " " + feature0(body)["support_identity"]["input_contract_identity"])),
            "padded label definition": edit(lambda body: sample0(body)["label"].__setitem__(
                "label_definition_id", " " + sample0(body)["label"]["label_definition_id"])),
            # the canonical body itself
            "sample weight": edit(lambda body: body["projection"]["samples"][0].__setitem__("sample_uniqueness_weight", "2")),
            "sample order": edit(lambda body: body["projection"]["samples"].reverse()),
            "fold": edit(lambda body: body["projection"]["fold"].__setitem__("fold_index", 3)),
            "projection identity": edit(lambda body: body["projection"].__setitem__("projection_identity", "x")),
            "policy not canonical": edit(lambda body: body["policy"].__setitem__("semantic_version", 1)),
            "policy family": edit(lambda body: body["policy"].__setitem__("model_family", "other")),
            "label value in the body": edit(lambda body: body["projection"]["samples"][0].__setitem__("label_value", "9")),
        }
        for label, build in cases.items():
            with self.subTest(label):
                with self.assertRaises(ConsumerApiError) as caught:
                    codec.decode_train_evaluate_request(build())
                self.assertIs(ConsumerErrorCode.INVALID_REQUEST, caught.exception.code)
                self.assertEqual("the request is not valid", caught.exception.message)
        # The unmutated body still decodes: every refusal above is the mutation's.
        codec.decode_train_evaluate_request(deepcopy(base))

    def test_a_non_mapping_or_garbage_body_is_invalid(self) -> None:
        for body in (None, [], "x", 1, {}):
            with self.subTest(body=body), self.assertRaises(ConsumerApiError):
                codec.decode_train_evaluate_request(body)

    def test_the_encoder_refuses_what_the_wire_cannot_carry_losslessly(self) -> None:
        request = request_for(projection())
        with self.assertRaises(ValueError):
            codec.encode_train_evaluate_request(request, register="yes")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            codec.encode_train_evaluate_request(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):  # a native type with no wire form
            codec.encode_train_evaluate_request(with_values(request, lambda value: (value,)))
        spec = request.run_identity.run_spec_identity
        floaty = replace(request, run_identity=replace(
            request.run_identity, run_spec_identity=replace(spec, run_configuration={"rate": 0.5}),
        ))
        with self.assertRaises(ValueError):
            codec.encode_train_evaluate_request(floaty)

    def test_the_registration_grant_is_named_only_by_a_true_register(self) -> None:
        grant = frozenset({"j02.training.register"})
        self.assertEqual(grant, codec._register_scope_in({"register": True}))
        for body in ({"register": False}, {}, {"register": "true"}, {"register": 1}, {"register": None}):
            with self.subTest(body=body):
                self.assertEqual(frozenset(), codec._register_scope_in(body))
        request = request_for(projection())
        self.assertEqual(
            grant, codec._register_scope_of(codec.decode_train_evaluate_request(wire_body(request, register=True))),
        )
        self.assertEqual(
            frozenset(), codec._register_scope_of(codec.decode_train_evaluate_request(wire_body(request))),
        )

    def test_the_route_declares_both_hooks_and_the_evaluate_scope_only(self) -> None:
        ((key, handler),) = codec.training_family_handlers().items()
        self.assertEqual(("j02-training-v1", "j02-training-v1-request-v1", "train_evaluate"), key)
        self.assertEqual("j02.training.evaluate", handler.required_scope)
        self.assertEqual("j02-training-v1-response-v1", handler.response_schema_version)
        self.assertIsNotNone(handler.additional_scopes)
        self.assertIsNotNone(handler.pre_decode_scopes)
        with self.assertRaises(TypeError):
            codec.training_family_handlers("provider")  # type: ignore[arg-type]


class RequestSizeTests(unittest.TestCase):
    def test_an_oversized_request_is_refused_locally_and_nothing_is_built_to_send(self) -> None:
        big_policy = replace(projection().selection_policy, notes="x" * (17 * 1024 * 1024))
        huge_projection = replace(projection(), selection_policy=big_policy)
        request = SupervisedTrainEvaluateRequest(
            huge_projection, run_identity(huge_projection.identity, execution_id="huge"), policy(),
        )
        with self.assertRaises(codec.RequestTooLarge) as caught:
            codec.encode_train_evaluate_message(request, request_id="big")
        self.assertEqual("request_too_large", caught.exception.code)

    def test_the_bound_is_the_j02_message_bound_and_is_inclusive(self) -> None:
        request = request_for(projection())
        wire = codec.encode_train_evaluate_message(request, request_id="fits")
        size = len(wire.encode("utf-8"))
        with patch.object(codec, "J02_MAX_WIRE_MESSAGE_BYTES", size):
            self.assertEqual(wire, codec.encode_train_evaluate_message(request, request_id="fits"))
        with patch.object(codec, "J02_MAX_WIRE_MESSAGE_BYTES", size - 1), self.assertRaises(codec.RequestTooLarge):
            codec.encode_train_evaluate_message(request, request_id="fits")
        from quant_platform.application.api_transport_server import J02_MAX_WIRE_MESSAGE_BYTES
        self.assertEqual(16 * 1024 * 1024, J02_MAX_WIRE_MESSAGE_BYTES)

    def test_the_message_is_the_family_envelope_around_the_encoded_request(self) -> None:
        request = request_for(projection())
        message = json.loads(codec.encode_train_evaluate_message(request, register=True, request_id="r-1"))
        self.assertEqual(
            {"message_family": "j02-training-v1", "schema_version": "j02-training-v1-request-v1",
             "operation": "train_evaluate", "request_id": "r-1"},
            {key: message[key] for key in ("message_family", "schema_version", "operation", "request_id")},
        )
        self.assertEqual(wire_body(request, register=True), message["request"])


class TrainingWssTests(_FamilyMtlsFixture):
    """Real TLS 1.3 / mTLS, the real J02 server and the J13 family handler."""

    async def exchange_messages(self, messages, handlers, *, scopes=BOTH):
        responses = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                for wire in messages:
                    self.assertLess(len(wire.encode("utf-8")), 16 * 1024 * 1024)
                    await socket.send(wire)
                    responses.append(json.loads(await asyncio.wait_for(socket.recv(), 30)))

        configuration = await self._run(scopes, handlers, session)
        return responses, configuration

    def provider(self, repository):
        @contextmanager
        def session():
            yield repository
        return session

    async def test_train_evaluate_over_wss_equals_the_in_process_result(self) -> None:
        request = request_for(projection())
        expected = json.loads(json.dumps(execute_supervised_train_evaluate(request).stable_dict()))
        responses, configuration = await self.exchange_messages(
            [codec.encode_train_evaluate_message(request, request_id="a")],
            codec.training_family_handlers(), scopes=EVALUATE,
        )
        self.assertEqual("ok", responses[0]["status"])
        self.assertEqual("j02-training-v1", responses[0]["message_family"])
        self.assertEqual("j02-training-v1-response-v1", responses[0]["schema_version"])
        self.assertEqual("a", responses[0]["request_id"])
        self.assertEqual(expected, responses[0]["result"])
        self.assertTrue(all(event.tls_version == "TLSv1.3" for event in configuration.evidence_log.events))

    async def test_the_decimal_and_the_literal_string_requests_give_the_same_result_over_wss(self) -> None:
        base = request_for(projection())
        decimal_request = with_values(base, lambda value: Decimal(str(value)), execution_id="same")
        string_request = with_values(base, lambda value: str(value), execution_id="same")
        responses, _ = await self.exchange_messages(
            [
                codec.encode_train_evaluate_message(decimal_request, request_id="d"),
                codec.encode_train_evaluate_message(string_request, request_id="s"),
            ],
            codec.training_family_handlers(), scopes=EVALUATE,
        )
        for item, response in zip((decimal_request, string_request), responses):
            self.assertEqual("ok", response["status"])
            self.assertEqual(
                json.loads(json.dumps(execute_supervised_train_evaluate(item).stable_dict())), response["result"],
            )

    async def test_register_true_without_the_register_scope_closes_1008_before_decode(self) -> None:
        request = request_for(projection())
        with patch.object(codec, "decode_train_evaluate_request", wraps=codec.decode_train_evaluate_request) as decode:
            handlers = codec.training_family_handlers(self.provider(ExperimentRepository(FakeConnection())))
            closed = []

            async def session(url):
                async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                    await socket.send(codec.encode_train_evaluate_message(request, register=True, request_id="r"))
                    try:
                        await asyncio.wait_for(socket.recv(), 10)
                    except ConnectionClosed as error:
                        closed.append(error.rcvd.code if error.rcvd else None)

            configuration = await self._run(EVALUATE, handlers, session)
        self.assertEqual([1008], closed)
        decode.assert_not_called()
        denials = [event for event in configuration.evidence_log.events if event.decision == "deny"]
        self.assertEqual(["j02.training.register"], [event.requested_scope for event in denials])

    async def test_a_malformed_register_flag_is_refused_without_the_register_grant_being_implied(self) -> None:
        request = request_for(projection())
        body = wire_body(request)
        body["register"] = "true"
        message = json.dumps({
            "message_family": codec.J02_TRAINING_FAMILY, "schema_version": codec.J02_TRAINING_REQUEST_VERSION,
            "operation": "train_evaluate", "request_id": "m", "request": body,
        })
        responses, _ = await self.exchange_messages([message], codec.training_family_handlers(), scopes=EVALUATE)
        self.assertEqual("invalid_request", responses[0]["error"]["code"])

    async def test_registration_is_idempotent_on_resend(self) -> None:
        request = request_for(projection())
        repository = ExperimentRepository(FakeConnection())
        responses, configuration = await self.exchange_messages(
            [
                codec.encode_train_evaluate_message(request, register=True, request_id="1"),
                codec.encode_train_evaluate_message(request, register=True, request_id="2"),
            ],
            codec.training_family_handlers(self.provider(repository)), scopes=BOTH,
        )
        self.assertEqual(["ok", "ok"], [response["status"] for response in responses])
        self.assertEqual(responses[0]["result"], responses[1]["result"])
        self.assertEqual(RunState.SUCCEEDED, repository.get_run(request.run_identity).state)
        # Both grants were authorized once per request, never twice for one.
        scopes = [event.requested_scope for event in configuration.evidence_log.events if event.decision == "allow"]
        self.assertEqual(2, scopes.count("j02.training.register"))
        # A non-registering request on the same server writes nothing more.
        plain, _ = await self.exchange_messages(
            [codec.encode_train_evaluate_message(request_for(projection(), execution_id="plain"), request_id="p")],
            codec.training_family_handlers(self.provider(repository)), scopes=EVALUATE,
        )
        self.assertEqual("ok", plain[0]["status"])

    async def test_a_registering_request_to_a_server_without_a_repository_is_refused(self) -> None:
        request = request_for(projection())
        responses, _ = await self.exchange_messages(
            [codec.encode_train_evaluate_message(request, register=True, request_id="n")],
            codec.training_family_handlers(), scopes=BOTH,
        )
        error = responses[0]["error"]
        self.assertEqual(("invalid_request", "registration_unavailable"), (error["code"], error["context"]["reason"]))

    async def test_an_over_budget_request_is_invalid_with_the_budget_reason_and_never_trains(self) -> None:
        request = request_for(
            expanded_projection(train_count=2, test_count=MAX_SYNC_TRAINING_TEST_SAMPLES + 1), execution_id="over",
        )
        with patch("quant_platform.application.training_consumer.train_evaluate_supervised_baseline") as trainer:
            responses, _ = await self.exchange_messages(
                [codec.encode_train_evaluate_message(request, request_id="o")],
                codec.training_family_handlers(), scopes=EVALUATE,
            )
        trainer.assert_not_called()
        error = responses[0]["error"]
        self.assertEqual(("invalid_request", "training_sync_budget_exceeded"), (error["code"], error["context"]["reason"]))
        self.assertEqual(request.request_identity, error["request_identity"])

    async def test_mutated_evidence_over_wss_is_refused_before_any_training(self) -> None:
        request = request_for(projection())
        bodies = []
        for mutate in (
            lambda body: body["reconstruction"]["run_identity"]["study"].__setitem__("study_definition_version", "x"),
            lambda body: body["reconstruction"]["samples"][0]["label"].__setitem__("value", "1"),
            lambda body: body["reconstruction"]["samples"][0]["features"][0].__setitem__("value_type", "integer"),
        ):
            body = wire_body(request)
            mutate(body)
            bodies.append(json.dumps({
                "message_family": codec.J02_TRAINING_FAMILY,
                "schema_version": codec.J02_TRAINING_REQUEST_VERSION,
                "operation": "train_evaluate", "request_id": str(len(bodies)), "request": body,
            }))
        with patch("quant_platform.application.training_consumer.train_evaluate_supervised_baseline") as trainer:
            responses, _ = await self.exchange_messages(bodies, codec.training_family_handlers(), scopes=BOTH)
        trainer.assert_not_called()
        self.assertEqual(["invalid_request"] * 3, [item["error"]["code"] for item in responses])

    async def test_an_oversized_request_is_refused_before_it_is_sent(self) -> None:
        sent = []
        big_policy = replace(projection().selection_policy, notes="x" * (17 * 1024 * 1024))
        huge_projection = replace(projection(), selection_policy=big_policy)
        request = SupervisedTrainEvaluateRequest(
            huge_projection, run_identity(huge_projection.identity, execution_id="huge"), policy(),
        )
        refused = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                try:
                    wire = codec.encode_train_evaluate_message(request, request_id="big")
                except codec.RequestTooLarge as error:
                    refused.append(error.code)
                else:
                    sent.append(wire)
                    await socket.send(wire)

        with patch("quant_platform.application.training_consumer.train_evaluate_supervised_baseline") as trainer:
            configuration = await self._run(EVALUATE, codec.training_family_handlers(), session)
        self.assertEqual(["request_too_large"], refused)
        self.assertEqual([], sent)
        trainer.assert_not_called()
        # The connection was authenticated, but no operation was ever requested.
        self.assertEqual(
            [], [event for event in configuration.evidence_log.events if event.requested_scope == "j02.training.evaluate"],
        )

    async def test_the_market_data_grant_does_not_open_the_training_decoder(self) -> None:
        request = request_for(projection())
        closed = []

        async def session(url):
            async with websockets.connect(url, ssl=self._client_ssl()) as socket:
                await socket.send(codec.encode_train_evaluate_message(request, request_id="x"))
                try:
                    await asyncio.wait_for(socket.recv(), 10)
                except ConnectionClosed as error:
                    closed.append(error.rcvd.code if error.rcvd else None)

        with patch.object(codec, "decode_train_evaluate_request") as decode:
            await self._run(frozenset({"j02.market_data.read"}), codec.training_family_handlers(), session)
        self.assertEqual([1008], closed)
        decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
