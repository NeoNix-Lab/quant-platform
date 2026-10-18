#!/usr/bin/env python3
"""Deck handoff client v1 -- unit-level wire behavior (#343, ADR-0069 section 8).

Typed failures, frame and member verification, and the cross-checks that keep
this client's duplicated wire literals identical to the server's. The real
WSS/mTLS round trip lives in ``tests/test_deck_handoff_client_acceptance_v1.py``.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import ssl
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "clients" / "deck"))

from websockets.exceptions import ConnectionClosed  # noqa: E402
from websockets.frames import Close  # noqa: E402

import deck_handoff_client as deck  # noqa: E402
from quant_platform.application import admitted_input_transport as server_admitted  # noqa: E402
from quant_platform.application import api_transport_server as server  # noqa: E402
from quant_platform.application import framed_result_transport as j14  # noqa: E402
from quant_platform.application import result_import_transport as server_import  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputManifestV1  # noqa: E402


class Script:
    """A fake ``websockets.connect``: replies are computed from what was sent."""

    def __init__(self, reply, *, close=None, enter_error=None):
        self.reply = reply
        self.close = close
        self.enter_error = enter_error
        self.sent: list[dict] = []
        self.connections = 0
        self._queue: list[str] = []

    def __call__(self, url, **kwargs):
        self.connections += 1
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_exc):
        return False

    async def send(self, message):
        document = json.loads(message)
        self.sent.append(document)
        if self.reply is None:
            return
        replies = self.reply(document)
        self._queue.extend(replies if isinstance(replies, list) else [replies])

    async def recv(self):
        if self.close is not None:
            raise ConnectionClosed(Close(*self.close), None)
        return self._queue.pop(0)


def strict_context() -> ssl.SSLContext:
    """A client context of the shape the deck client accepts (the transport itself is faked)."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


def ok(request, result, **overrides):
    envelope = {
        "message_family": request["message_family"],
        "schema_version": request["schema_version"].replace("-request-", "-response-"),
        "request_id": request["request_id"], "status": "ok", "result": result,
    }
    envelope.update(overrides)
    return json.dumps(envelope)


def failure(request, error):
    return json.dumps({
        "message_family": request["message_family"],
        "schema_version": request["schema_version"].replace("-request-", "-response-"),
        "request_id": request["request_id"], "status": "error", "error": error,
    })


def manifest_fields() -> dict:
    return AdmittedInputManifestV1(
        logical_input_identities=("dataset-v1:BTCUSDT",),
        natural_partition_identities=("partition-v1:1",),
        schema_identity="trades@1", schema_version="1", schema_hash="schema-sha256:abc",
        manifest_hashes=("manifest-sha256:abc",),
        content_hashes=(hashlib.sha256(b"member-bytes").hexdigest(),),
        declared_coverage={"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"},
        request_identity="request-v1:alpha", result_identity=None,
        definition_identities=("definition-v1:trades",), implementation_identity="impl-v1:abc",
        git_identity="git:abc123", operation_identity="strategy-compose-v1",
        profile_identity="profile-v1:default",
    ).canonical_fields()


def admission_document() -> dict:
    fields = manifest_fields()
    digest = deck.manifest_digest(fields)
    return {
        "admission_id": f"admitted-input-v1:{digest}", "manifest_digest": digest,
        "manifest": fields, "state": "SEALED", "delivery_failure_reason": None,
        "delivery_attempt": 0,
    }


class WireConstantsTests(unittest.TestCase):
    def test_the_literals_are_the_servers(self) -> None:
        self.assertEqual(server.J02_MAX_WIRE_MESSAGE_BYTES, deck.J02_MAX_WIRE_MESSAGE_BYTES)
        self.assertEqual(server_admitted.J02_ADMITTED_INPUT_FAMILY, deck.ADMITTED_INPUT_FAMILY)
        self.assertEqual(server_admitted.J02_ADMITTED_INPUT_REQUEST_VERSION, deck.ADMITTED_INPUT_REQUEST_VERSION)
        self.assertEqual(server_admitted.J02_ADMITTED_INPUT_RESPONSE_VERSION, deck.ADMITTED_INPUT_RESPONSE_VERSION)
        self.assertEqual(server_admitted.J02_ADMITTED_INPUT_SCOPE, deck.ADMITTED_INPUT_SCOPE)
        self.assertEqual(server_admitted.ADMITTED_INPUT_MEMBER_SCHEMA, deck.MEMBER_SCHEMA)
        self.assertEqual(server_import.J02_RESULT_IMPORT_FAMILY, deck.RESULT_IMPORT_FAMILY)
        self.assertEqual(server_import.J02_RESULT_IMPORT_REQUEST_VERSION, deck.RESULT_IMPORT_REQUEST_VERSION)
        self.assertEqual(server_import.J02_RESULT_IMPORT_RESPONSE_VERSION, deck.RESULT_IMPORT_RESPONSE_VERSION)
        self.assertEqual(server_import.J02_RESULT_IMPORT_SCOPE, deck.RESULT_IMPORT_SCOPE)
        self.assertEqual(server_import.BUNDLE_WIRE_VERSION, deck.BUNDLE_WIRE_VERSION)
        self.assertEqual(server_import.OUTPUT_IDENTITY_PREFIX, deck.OUTPUT_IDENTITY_PREFIX)
        self.assertEqual(server_import.CHECKSUM_PREFIX, deck.CHECKSUM_PREFIX)
        self.assertEqual(j14.J14_MESSAGE_FAMILY, deck.J14_FAMILY)
        self.assertEqual(j14.J14_SCHEMA_VERSION, deck.J14_FAMILY)
        self.assertEqual(j14.DEFAULT_J14_FRAME_PAYLOAD_BYTES, deck.DEFAULT_FRAME_PAYLOAD_BYTES)
        # Exactly the two scopes ADR-0069 section 8 names, and no other.
        self.assertEqual(("j02.admitted_input.read", "j02.result.submit"), deck.DECK_SCOPES)

    def test_the_clients_own_identity_arithmetic_is_the_servers(self) -> None:
        manifest = AdmittedInputManifestV1(
            logical_input_identities=("dataset-v1:BTCUSDT",), natural_partition_identities=(),
            schema_identity=None, schema_version=None, schema_hash=None, manifest_hashes=(),
            content_hashes=("a" * 64,), declared_coverage=None, request_identity="r", result_identity=None,
            definition_identities=(), implementation_identity=None, git_identity=None,
            operation_identity="op", profile_identity=None,
        )
        self.assertEqual(manifest.manifest_digest, deck.manifest_digest(manifest.canonical_fields()))
        # transfer_id of a real J14 framing, and of an upload.
        frames = j14.frame_complete_result(
            logical_result_identity="admitted-input-member-v1:x:0", result={"a": [1, 2, 3]},
        )
        self.assertEqual(
            frames[0].transfer_id,
            deck.transfer_id(frames[0].logical_result_identity, frames[0].payload_sha256),
        )
        payload = bytes(range(256)) * 20
        self.assertEqual(
            server_import.upload_chunks(payload, frame_payload_bytes=1000),
            deck.output_chunks(payload, frame_payload_bytes=1000),
        )
        with self.assertRaises(ValueError):
            deck.output_chunks(b"")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_unexpected_family_version_or_request_id_is_wire_incompatible_and_not_retried(self) -> None:
        for label, change in (
            ("other family", {"message_family": "j02-job-v1"}),
            ("other schema version", {"schema_version": "j02-admitted-input-v3-response-v1"}),
            ("other request id", {"request_id": "someone-elses"}),
            ("j02 v1 envelope", {"message_family": None, "schema_version": "j02-response-v1"}),
        ):
            with self.subTest(label):
                script = Script(lambda request, change=change: ok(request, {}, **change))
                async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
                    with self.assertRaises(deck.WireIncompatible):
                        await client.manifest("admitted-input-v1:" + "0" * 64)
                self.assertEqual((1, 1), (script.connections, len(script.sent)))  # no retry, no probing

    async def test_an_error_envelope_is_reconstructed_losslessly_and_is_not_a_wire_failure(self) -> None:
        def reply(request):
            return failure(request, {
                "code": "invalid_request", "message": "the request is not valid",
                "context": {"reason": "admission_not_found"}, "request_identity": "req-9",
            })

        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=Script(reply)) as client:
            with self.assertRaises(deck.RemoteConsumerApiError) as caught:
                await client.manifest("admitted-input-v1:" + "0" * 64)
        error = caught.exception
        self.assertEqual(
            ("invalid_request", "the request is not valid", {"reason": "admission_not_found"}, "req-9"),
            (error.code, error.message, dict(error.context), error.request_identity),
        )

    async def test_security_failures_are_never_consumer_errors(self) -> None:
        for label, script in (
            ("tls handshake", Script(None, enter_error=ssl.SSLError("handshake"))),
            ("policy close 1008 on the first request", Script(None, close=(1008, "policy denied"))),
        ):
            with self.subTest(label):
                with self.assertRaises(deck.RemoteSecurityFailure):
                    async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
                        await client.upload_output(b"abc")
        # Another close code is not a security verdict.
        script = Script(None, close=(1011, "internal"))
        with self.assertRaises(ConnectionClosed):
            async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
                await client.upload_output(b"abc")

    async def test_an_expired_member_transfer_is_acknowledged_as_expired_and_no_bytes_are_returned(self) -> None:
        document = admission_document()
        digest = document["manifest_digest"]
        member = {
            "member_index": 0, "member_identity": "partition-v1:1",
            "content_hash": document["manifest"]["content_hashes"][0], "byte_length": 12,
            "logical_result_identity": f"admitted-input-member-v1:{document['admission_id']}:0",
            "transfer_id": "5" * 64, "payload_sha256": "6" * 64, "chunk_count": 2,
        }

        def reply(request):
            if request.get("message_type") == "resume":
                return json.dumps({
                    "message_family": "j14-framed-result-v1", "schema_version": "j14-framed-result-v1",
                    "message_type": "outcome", "outcome": "transfer_expired",
                    "transfer_id": request["transfer_id"],
                })
            if request["operation"] == "deliver":
                return ok(request, {
                    "admission_id": document["admission_id"], "manifest_digest": digest,
                    "state": "DELIVERY_PENDING", "delivery_attempt": 1, "members": [member],
                })
            return ok(request, {"state": "DELIVERY_FAILED"})

        script = Script(reply)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            admission = deck.Admission(document["admission_id"], digest, document["manifest"], "SEALED")
            with self.assertRaises(deck.DeliveryIntegrityFailure) as caught:
                await client.fetch_inputs(admission)
        self.assertEqual(("transfer_expired", 0), (caught.exception.reason, caught.exception.member_index))
        acknowledgement = script.sent[-1]["request"]
        self.assertEqual(
            ("failed", "transfer_expired", 0),
            (acknowledgement["outcome"], acknowledgement["failure_reason"], acknowledgement["failed_member_index"]),
        )

    async def test_a_manifest_that_does_not_hash_to_its_identity_is_refused(self) -> None:
        document = admission_document()
        for label, change in (
            ("changed coverage", {"manifest": {**document["manifest"], "declared_coverage": None}}),
            ("changed id", {"admission_id": "admitted-input-v1:" + "1" * 64}),
        ):
            with self.subTest(label):
                script = Script(lambda request, change=change: ok(request, {**document, **change}))
                async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
                    with self.assertRaises(deck.DeliveryIntegrityFailure):
                        await client.manifest(document["admission_id"])
        script = Script(lambda request: ok(request, document))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            admission = await client.manifest(document["admission_id"])
        self.assertEqual(document["manifest_digest"], admission.manifest_digest)

    async def test_every_acknowledgement_names_the_attempt_the_delivery_returned(self) -> None:
        admission = sealed_admission()
        for faithful in (True, False):
            with self.subTest(faithful=faithful):
                identities = PARTITIONS if faithful else tuple(reversed(PARTITIONS))
                script = delivery_script(
                    admission, PARTITION_BYTES, descriptor_identities=PARTITIONS,
                    document_identities=identities,
                )
                async with deck.DeckHandoffClient(
                    "wss://x", ssl_context=strict_context(), connect=script,
                ) as client:
                    try:
                        await client.fetch_inputs(admission)
                    except deck.DeliveryIntegrityFailure:
                        pass
                acknowledgement = script.sent[-1]["request"]
                self.assertEqual(1, acknowledgement["delivery_attempt"])
                self.assertEqual("delivered" if faithful else "failed", acknowledgement["outcome"])

    async def test_a_delivery_without_a_valid_attempt_is_wire_incompatible_and_acknowledges_nothing(self) -> None:
        admission = sealed_admission()
        for label, value in (("missing", None), ("zero", 0), ("text", "1"), ("bool", True)):
            with self.subTest(label):
                inner = delivery_script(
                    admission, PARTITION_BYTES, descriptor_identities=PARTITIONS,
                    document_identities=PARTITIONS,
                )

                def reply(request, inner=inner, value=value):
                    response = inner.reply(request)
                    if isinstance(response, str):
                        envelope = json.loads(response)
                        result = envelope.get("result")
                        if isinstance(result, dict) and "members" in result:
                            if value is None:
                                del result["delivery_attempt"]
                            else:
                                result["delivery_attempt"] = value
                        return json.dumps(envelope)
                    return response

                script = Script(reply)
                async with deck.DeckHandoffClient(
                    "wss://x", ssl_context=strict_context(), connect=script,
                ) as client:
                    with self.assertRaises(deck.WireIncompatible):
                        await client.fetch_inputs(admission)
                self.assertEqual(["deliver"], [item["operation"] for item in script.sent])

    async def test_a_lost_delivery_is_ended_with_the_attempt_the_deck_observes(self) -> None:
        admission = sealed_admission()
        state = {"state": "DELIVERY_PENDING", "attempt": 3}

        def reply(request):
            if request["operation"] == "manifest":
                return ok(request, {
                    "admission_id": admission.admission_id, "manifest_digest": admission.manifest_digest,
                    "manifest": dict(admission.manifest), "state": state["state"],
                    "delivery_attempt": state["attempt"],
                })
            state["state"] = "DELIVERY_FAILED"  # the acknowledgement was accepted
            return ok(request, {"state": "DELIVERY_FAILED"})

        script = Script(reply)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            ended = await client.end_pending_attempt(admission.admission_id)
        self.assertEqual("DELIVERY_FAILED", ended.state)
        sent = [item["request"] for item in script.sent if "outcome" in item.get("request", {})]
        self.assertEqual(1, len(sent))
        self.assertEqual(
            (3, "failed", "delivery_interrupted"),
            (sent[0]["delivery_attempt"], sent[0]["outcome"], sent[0]["failure_reason"]),
        )
        # An admission that is not pending is only read: nothing is reported.
        state.update(state="DELIVERED")
        script = Script(reply)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            await client.end_pending_attempt(admission.admission_id)
        self.assertEqual(["manifest"], [item["operation"] for item in script.sent])

    async def test_a_manifest_of_another_admission_is_refused_even_when_it_is_valid(self) -> None:
        # B is a faithful admission with its own correct digest and id; A was asked for.
        asked = admission_document()
        other_fields = {**asked["manifest"], "operation_identity": "other-operation-v1"}
        other_digest = deck.manifest_digest(other_fields)
        other = {
            **asked, "manifest": other_fields, "manifest_digest": other_digest,
            "admission_id": f"admitted-input-v1:{other_digest}",
            "delivery_attempt": 0,
        }
        self.assertNotEqual(asked["admission_id"], other["admission_id"])
        script = Script(lambda request: ok(request, other))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            with self.assertRaises(deck.DeliveryIntegrityFailure):
                await client.manifest(asked["admission_id"])
        self.assertEqual(["manifest"], [item["operation"] for item in script.sent])  # no deliver, no retry

    async def test_an_admission_for_another_operation_or_profile_is_refused(self) -> None:
        # B is a faithful, sealed admission with its own correct digest and id;
        # the request named operation/profile A.
        asked = admission_document()["manifest"]
        operation, profile = asked["operation_identity"], asked["profile_identity"]
        self.assertIsNotNone(profile)
        for label, change in (
            ("another operation", {"operation_identity": "other-operation-v1"}),
            ("another profile", {"profile_identity": "profile-v1:other"}),
            ("the requested profile removed", {"profile_identity": None}),
        ):
            with self.subTest(label):
                fields = {**asked, **change}
                digest = deck.manifest_digest(fields)
                other = {
                    "manifest": fields, "manifest_digest": digest,
                    "admission_id": f"admitted-input-v1:{digest}", "state": "SEALED",
                    "delivery_attempt": 0,
                }
                script = Script(lambda request, other=other: ok(request, other))
                async with deck.DeckHandoffClient(
                    "wss://x", ssl_context=strict_context(), connect=script,
                ) as client:
                    with self.assertRaises(deck.DeliveryIntegrityFailure):
                        await client.admit(
                            dataset={"instrument": "BTCUSDT"}, operation_identity=operation,
                            profile_identity=profile,
                        )
                self.assertEqual(["admit"], [item["operation"] for item in script.sent])
        # A manifest that carries no profile field at all is not "no profile requested".
        fields = {key: value for key, value in asked.items() if key != "profile_identity"}
        digest = deck.manifest_digest(fields)
        script = Script(lambda request: ok(request, {
            "manifest": fields, "manifest_digest": digest,
            "admission_id": f"admitted-input-v1:{digest}", "state": "SEALED",
            "delivery_attempt": 0,
        }))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            with self.assertRaises(deck.DeliveryIntegrityFailure):
                await client.admit(dataset={"instrument": "BTCUSDT"}, operation_identity=operation)
        # A profile that was not requested is not the request's either.
        script = Script(lambda request: ok(request, admission_document()))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            with self.assertRaises(deck.DeliveryIntegrityFailure):
                await client.admit(dataset={"instrument": "BTCUSDT"}, operation_identity=operation)
            self.assertEqual(["admit"], [item["operation"] for item in script.sent])
            admission = await client.admit(
                dataset={"instrument": "BTCUSDT"}, operation_identity=operation, profile_identity=profile,
            )
        self.assertEqual(admission_document()["admission_id"], admission.admission_id)

    async def test_an_upload_learns_the_servers_chunk_size_from_a_layout_refusal_once(self) -> None:
        payload = bytes(range(256)) * 8  # 2048 bytes
        state = {"size": 700}

        def reply(request):
            body = request["request"]
            index, count = body["chunk_index"], body["chunk_count"]
            length = len(base64.b64decode(body["chunk_base64"]))
            if index < count - 1 and length != state["size"] or length > state["size"]:
                return failure(request, {
                    "code": "integrity_failure", "message": "x",
                    "context": {"reason": "chunk_layout_mismatch", "frame_payload_bytes": state["size"]},
                    "request_identity": None,
                })
            complete = index == count - 1
            return ok(request, {
                "transfer_id": body["transfer_id"], "payload_sha256": body["payload_sha256"],
                "chunk_count": count, "complete": complete, "received_chunks": index + 1,
                "next_chunk_index": None if complete else index + 1,
                "checksum": "sha256:" + body["payload_sha256"] if complete else None,
                "byte_length": len(payload) if complete else None,
            })

        script = Script(reply)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            staged = await client.upload_output(payload, frame_payload_bytes=1024)
        self.assertEqual("sha256:" + hashlib.sha256(payload).hexdigest(), staged.checksum)
        sizes = {len(base64.b64decode(item["request"]["chunk_base64"])) for item in script.sent[1:]}
        self.assertEqual({700, 2048 - 2 * 700}, sizes)

    async def test_an_upload_progress_about_another_transfer_is_wire_incompatible(self) -> None:
        def reply(request):
            body = request["request"]
            return ok(request, {
                "transfer_id": "0" * 64, "payload_sha256": body["payload_sha256"],
                "chunk_count": body["chunk_count"], "complete": True, "received_chunks": 1,
                "next_chunk_index": None, "checksum": "sha256:" + body["payload_sha256"],
                "byte_length": 3,
            })

        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=Script(reply)) as client:
            with self.assertRaises(deck.WireIncompatible):
                await client.upload_output(b"abc")

    async def test_submit_takes_only_the_bundle_wire_document(self) -> None:
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=Script(lambda r: ok(r, {}))) as client:
            for bad in ({}, {"version": "governed-result-bundle-wire-v2"}, [1]):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    await client.submit(bad)


PARTITIONS = ("partition-v1:a", "partition-v1:b")
PARTITION_BYTES = (b"partition-a-bytes", b"partition-b-bytes")


def sealed_admission(*, partitions=PARTITIONS, logical=("dataset-v1:BTCUSDT",), members=PARTITION_BYTES):
    fields = {
        **manifest_fields(), "natural_partition_identities": list(partitions),
        "logical_input_identities": list(logical),
        "content_hashes": [hashlib.sha256(data).hexdigest() for data in members],
    }
    digest = deck.manifest_digest(fields)
    return deck.Admission(f"admitted-input-v1:{digest}", digest, fields, "SEALED")


def delivery_script(admission, members, *, descriptor_identities, document_identities):
    """A server that is faithful in every digest and frame, and says what it is told about identities."""

    transfers: dict[str, list[str]] = {}
    descriptors = []
    for index, data in enumerate(members):
        content_hash = hashlib.sha256(data).hexdigest()
        document = {
            "schema_version": "admitted-input-member-v1", "admission_id": admission.admission_id,
            "manifest_digest": admission.manifest_digest, "member_index": index,
            "member_identity": document_identities[index], "content_hash": content_hash,
            "byte_length": len(data), "encoding": "base64",
            "bytes": base64.b64encode(data).decode(),
        }
        frames = j14.frame_complete_result(
            logical_result_identity=f"admitted-input-member-v1:{admission.admission_id}:{index}",
            result=document, config=j14.FramedResultConfig(frame_payload_bytes=96),
        )
        first = frames[0]
        transfers[first.transfer_id] = [json.dumps(frame.message()) for frame in frames]
        descriptors.append({
            "member_index": index, "member_identity": descriptor_identities[index],
            "content_hash": content_hash, "byte_length": len(data),
            "logical_result_identity": first.logical_result_identity, "transfer_id": first.transfer_id,
            "payload_sha256": first.payload_sha256, "chunk_count": first.chunk_count,
        })

    def reply(request):
        if request.get("message_type") == "resume":
            return transfers[request["transfer_id"]]
        if request["operation"] == "deliver":
            return ok(request, {
                "admission_id": admission.admission_id, "manifest_digest": admission.manifest_digest,
                "state": "DELIVERY_PENDING", "delivery_attempt": 1, "members": descriptors,
            })
        outcome = request["request"]["outcome"]
        return ok(request, {"state": "DELIVERED" if outcome == "delivered" else "DELIVERY_FAILED"})

    return Script(reply)


class MemberAttributionTests(unittest.IsolatedAsyncioTestCase):
    """The identity of a member is the sealed manifest's, never the transport's own word."""

    async def fetch(self, admission, **identities):
        script = delivery_script(admission, PARTITION_BYTES, **identities)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            try:
                return script, await client.fetch_inputs(admission), None
            except deck.DeliveryIntegrityFailure as failure:
                return script, None, failure

    async def test_partitions_are_returned_under_the_identities_the_manifest_pairs_with_them(self) -> None:
        script, delivered, failure = await self.fetch(
            sealed_admission(), descriptor_identities=PARTITIONS, document_identities=PARTITIONS,
        )
        self.assertIsNone(failure)
        self.assertEqual((PARTITION_BYTES, PARTITIONS), (delivered.members, delivered.member_identities))
        self.assertEqual("delivered", script.sent[-1]["request"]["outcome"])

    async def test_a_partition_under_another_partitions_identity_is_refused_and_acknowledged(self) -> None:
        swapped = tuple(reversed(PARTITIONS))
        for label, identities in (
            # The frames are re-built consistently in each case; every digest is right.
            ("document and descriptor swapped", {"descriptor_identities": swapped, "document_identities": swapped}),
            ("only the document swapped", {"descriptor_identities": PARTITIONS, "document_identities": swapped}),
            ("only the descriptor swapped", {"descriptor_identities": swapped, "document_identities": PARTITIONS}),
            (
                "the dataset's identity in place of the partition",
                {"descriptor_identities": ("dataset-v1:BTCUSDT",) * 2, "document_identities": ("dataset-v1:BTCUSDT",) * 2},
            ),
            (
                "an identity the manifest does not know",
                {"descriptor_identities": ("partition-v1:z",) * 2, "document_identities": ("partition-v1:z",) * 2},
            ),
        ):
            with self.subTest(label):
                script, delivered, failure = await self.fetch(sealed_admission(), **identities)
                self.assertIsNone(delivered)
                self.assertEqual(("transfer_integrity_failure", 0), (failure.reason, failure.member_index))
                acknowledgement = script.sent[-1]["request"]
                self.assertEqual(
                    ("failed", "transfer_integrity_failure", 0),
                    (acknowledgement["outcome"], acknowledgement["failure_reason"], acknowledgement["failed_member_index"]),
                )
                self.assertNotIn("delivered", [item.get("request", {}).get("outcome") for item in script.sent])

    async def test_an_artifact_only_manifest_is_attributed_to_its_single_logical_identity(self) -> None:
        artifact = "artifact-v1:model"
        admission = sealed_admission(partitions=(), logical=(artifact,))
        script, delivered, failure = await self.fetch(
            admission, descriptor_identities=(artifact,) * 2, document_identities=(artifact,) * 2,
        )
        self.assertIsNone(failure)
        self.assertEqual((artifact, artifact), delivered.member_identities)
        # An identity that is not the manifest's one is still refused.
        _, delivered, failure = await self.fetch(
            admission, descriptor_identities=("artifact-v1:other",) * 2,
            document_identities=("artifact-v1:other",) * 2,
        )
        self.assertIsNone(delivered)
        self.assertEqual(0, failure.member_index)

    async def test_a_manifest_without_a_verifiable_attribution_is_refused_before_delivery(self) -> None:
        for label, admission in (
            ("two logical identities, no partitions", sealed_admission(partitions=(), logical=("a:1", "b:2"))),
            ("partitions that do not align", sealed_admission(partitions=("partition-v1:a",))),
        ):
            with self.subTest(label):
                script = delivery_script(
                    admission, PARTITION_BYTES, descriptor_identities=PARTITIONS, document_identities=PARTITIONS,
                )
                async with deck.DeckHandoffClient(
                    "wss://x", ssl_context=strict_context(), connect=script,
                ) as client:
                    with self.assertRaises(deck.DeliveryIntegrityFailure):
                        await client.fetch_inputs(admission)
                self.assertEqual([], script.sent)  # nothing was delivered, so nothing is acknowledged


class SecureEndpointTests(unittest.IsolatedAsyncioTestCase):
    """ADR-0063 section 1: WSS, a verifying chain and name, TLS 1.3 -- before any connection."""

    def refuse(self, url, context):
        script = Script(lambda request: ok(request, admission_document()))
        with self.assertRaises(deck.InsecureTransportConfiguration):
            deck.DeckHandoffClient(url, ssl_context=context, connect=script)
        self.assertEqual((0, []), (script.connections, script.sent))

    def test_only_wss_to_a_named_endpoint_is_accepted(self) -> None:
        for url in (
            "ws://127.0.0.1:9", "http://127.0.0.1:9", "wss://", "wss://:443", "wss://user:secret@deck.example",
            "127.0.0.1:9", "", None,
        ):
            with self.subTest(url=url):
                self.refuse(url, strict_context())

    def test_a_missing_or_non_verifying_or_old_context_is_refused(self) -> None:
        def unverified_chain():
            context = strict_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context

        def no_hostname_check():
            context = strict_context()
            context.check_hostname = False
            return context

        def optional_chain():
            context = strict_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_OPTIONAL
            return context

        def optional_with_hostname():
            context = strict_context()
            context.verify_mode = ssl.CERT_OPTIONAL
            return context

        def server_side_otherwise_strict():
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True  # every other constraint is met: only the role is wrong
            return context

        def tls12():
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return context

        for label, context in (
            ("no context", None),
            ("a server-side context", ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)),
            ("a server-side context with TLS 1.3 and CERT_REQUIRED", server_side_otherwise_strict()),
            ("chain verification off", unverified_chain()),
            ("hostname verification off", no_hostname_check()),
            ("chain verification optional", optional_chain()),
            ("chain verification optional, hostname checked", optional_with_hostname()),
            ("TLS 1.2 allowed", tls12()),
        ):
            with self.subTest(label):
                self.refuse("wss://deck.example:443", context)

    async def test_a_context_weakened_after_construction_is_refused_on_entry(self) -> None:
        script = Script(lambda request: ok(request, admission_document()))
        context = strict_context()
        client = deck.DeckHandoffClient("wss://x", ssl_context=context, connect=script)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with self.assertRaises(deck.InsecureTransportConfiguration):
            async with client:
                pass
        self.assertEqual(0, script.connections)

    def test_a_verifying_tls13_client_context_is_accepted(self) -> None:
        deck.require_secure_endpoint("wss://deck.example:443/path", strict_context())


class VerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = b"member-bytes"
        self.admission = deck.Admission(
            "admitted-input-v1:" + "a" * 64, "a" * 64, manifest_fields(), "SEALED",
        )
        self.document = {
            "schema_version": "admitted-input-member-v1", "admission_id": self.admission.admission_id,
            "manifest_digest": self.admission.manifest_digest, "member_index": 0,
            "member_identity": "partition-v1:1", "content_hash": hashlib.sha256(self.data).hexdigest(),
            "byte_length": len(self.data), "encoding": "base64",
            "bytes": base64.b64encode(self.data).decode(),
        }
        self.frames = j14.frame_complete_result(
            logical_result_identity="admitted-input-member-v1:" + self.admission.admission_id + ":0",
            result=self.document, config=j14.FramedResultConfig(frame_payload_bytes=64),
        )
        first = self.frames[0]
        self.descriptor = {
            "logical_result_identity": first.logical_result_identity, "transfer_id": first.transfer_id,
            "payload_sha256": first.payload_sha256, "chunk_count": first.chunk_count,
        }

    def messages(self):
        return [frame.message() for frame in self.frames]

    def test_a_faithful_transfer_reassembles_and_verifies(self) -> None:
        self.assertGreater(len(self.frames), 1)
        payload = deck.reassemble(self.messages(), descriptor=self.descriptor)
        self.assertEqual(self.document, json.loads(payload))
        data, identity = deck.verify_member_document(
            self.document, admission=self.admission, index=0,
            content_hash=hashlib.sha256(self.data).hexdigest(), member_identity="partition-v1:1",
        )
        self.assertEqual((self.data, "partition-v1:1"), (data, identity))

    def test_every_frame_tamper_is_a_transfer_integrity_failure(self) -> None:
        def flipped(messages):
            chunk = bytearray(base64.b64decode(messages[0]["chunk_base64"]))
            chunk[0] ^= 0xFF
            messages[0] = {**messages[0], "chunk_base64": base64.b64encode(bytes(chunk)).decode()}
            return messages

        def dropped(messages):
            return messages[:-1]

        def duplicated(messages):
            return messages[:-1] + [messages[0]]

        def other_transfer(messages):
            return [{**messages[0], "transfer_id": "0" * 64}] + messages[1:]

        def other_payload_digest(messages):
            return [{**message, "payload_sha256": "1" * 64} for message in messages]

        def bad_count(messages):
            return [{**message, "chunk_count": len(messages) + 1} for message in messages]

        def not_base64(messages):
            return [{**messages[0], "chunk_base64": "!!"}] + messages[1:]

        def only_the_chunk_digest(messages):
            # Right bytes, a wrong per-chunk digest: nothing else is off.
            return [{**messages[0], "chunk_sha256": "2" * 64}] + messages[1:]

        def a_consistent_chunk_swap(messages):
            # Other bytes with their own correct chunk digest: every per-chunk
            # check passes; only the whole-payload digest can tell.
            other = bytes(reversed(base64.b64decode(messages[0]["chunk_base64"])))
            return [{
                **messages[0], "chunk_base64": base64.b64encode(other).decode(),
                "chunk_sha256": hashlib.sha256(other).hexdigest(),
            }] + messages[1:]

        for tamper in (
            flipped, dropped, duplicated, other_transfer, other_payload_digest, bad_count,
            not_base64, only_the_chunk_digest, a_consistent_chunk_swap,
        ):
            with self.subTest(tamper=tamper.__name__):
                with self.assertRaises(deck._FrameFailure) as caught:
                    deck.reassemble(tamper(self.messages()), descriptor=self.descriptor)
                self.assertEqual("transfer_integrity_failure", caught.exception.reason)

    def test_a_descriptor_that_disagrees_with_self_consistent_frames_is_refused(self) -> None:
        # The frames are a faithful J14 transfer; the *descriptor* the server
        # announced for it is the one that is wrong, in each of its four fields.
        for field, value in (
            ("transfer_id", "3" * 64),
            ("logical_result_identity", "admitted-input-member-v1:other:0"),
            ("payload_sha256", "4" * 64),
            ("chunk_count", self.descriptor["chunk_count"] + 1),
        ):
            with self.subTest(field=field), self.assertRaises(deck._FrameFailure):
                deck.reassemble(self.messages(), descriptor={**self.descriptor, field: value})

    def test_a_member_whose_bytes_are_not_the_manifests_is_a_content_hash_mismatch(self) -> None:
        wrong = b"member-bytez"
        document = {
            **self.document, "bytes": base64.b64encode(wrong).decode(),
            "content_hash": hashlib.sha256(wrong).hexdigest(),  # an attacker's consistent claim
        }
        with self.assertRaises(deck._FrameFailure) as caught:
            deck.verify_member_document(
                document, admission=self.admission, index=0,
                content_hash=hashlib.sha256(self.data).hexdigest(), member_identity="partition-v1:1",
            )
        self.assertEqual("content_hash_mismatch", caught.exception.reason)

    def test_a_member_document_about_another_admission_index_or_length_is_refused(self) -> None:
        good = hashlib.sha256(self.data).hexdigest()
        for label, change in (
            ("another admission", {"admission_id": "admitted-input-v1:" + "b" * 64}),
            ("another manifest digest", {"manifest_digest": "b" * 64}),
            ("another index", {"member_index": 1}),
            ("another length", {"byte_length": 3}),
            ("another encoding", {"encoding": "hex"}),
            ("another schema", {"schema_version": "admitted-input-member-v2"}),
            ("not base64", {"bytes": "!!"}),
            ("another partition's identity", {"member_identity": "partition-v1:2"}),
            ("the dataset's identity", {"member_identity": "dataset-v1:BTCUSDT"}),
            ("no identity", {"member_identity": None}),
        ):
            with self.subTest(label), self.assertRaises(deck._FrameFailure):
                deck.verify_member_document(
                    {**self.document, **change}, admission=self.admission, index=0, content_hash=good,
                    member_identity="partition-v1:1",
                )
        with self.assertRaises(deck._FrameFailure):
            deck.verify_member_document(
                [], admission=self.admission, index=0, content_hash=good, member_identity="partition-v1:1",
            )

    def test_the_transfer_id_derivation_is_checked_on_its_own(self) -> None:
        # Every frame *and* the descriptor carry the same, non-derived id; the
        # payload digest, chunk digests, count, identity and indexes are all
        # right. Only the derivation of the id from (identity, payload digest)
        # can refuse this.
        forged = "0" * 64
        frames = [{**message, "transfer_id": forged} for message in self.messages()]
        with self.assertRaises(deck._FrameFailure) as caught:
            deck.reassemble(frames, descriptor={**self.descriptor, "transfer_id": forged})
        self.assertEqual("transfer_integrity_failure", caught.exception.reason)
        self.assertIn("inconsistent", str(caught.exception))


class AdmitDefinitionsTests(unittest.IsolatedAsyncioTestCase):
    """``admit`` carries the optional ``definitions`` of ADR-0073 section 5 (issue #477)."""

    OPERATION = "strategy-compose-v1"
    PROFILE = "profile-v1:default"
    SELECTOR = {"instrument": "BTCUSDT"}
    DEFINITIONS = [{
        "feature_key": "order_flow.failed_aggression", "semantic_version": "1", "bar_duration": "1m",
        "parameters": {"imbalance_threshold": "0.3", "wick_ratio": "0.5"},
    }]
    IDENTITIES = (
        "bar-flow-definition-v1:sha256:" + "a" * 64,
        "feature-definition-v1:sha256:" + "b" * 64,
    )

    @staticmethod
    def document(identities):
        fields = AdmittedInputManifestV1(
            logical_input_identities=("dataset-v1:BTCUSDT",), natural_partition_identities=("partition-v1:1",),
            schema_identity="trades@1", schema_version="1", schema_hash="schema-sha256:abc",
            manifest_hashes=("manifest-sha256:abc",), content_hashes=(hashlib.sha256(b"m").hexdigest(),),
            declared_coverage={"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"},
            request_identity="request-v1:alpha", result_identity=None, definition_identities=tuple(identities),
            implementation_identity=None, git_identity="git:abc123", operation_identity="strategy-compose-v1",
            profile_identity="profile-v1:default",
        ).canonical_fields()
        digest = deck.manifest_digest(fields)
        return {
            "admission_id": f"admitted-input-v1:{digest}", "manifest_digest": digest, "manifest": fields,
            "state": "SEALED", "delivery_failure_reason": None, "delivery_attempt": 0,
        }

    async def admit(self, identities, **extra):
        script = Script(lambda request: ok(request, self.document(identities)))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            try:
                admission = await client.admit(
                    dataset=dict(self.SELECTOR), operation_identity=self.OPERATION, profile_identity=self.PROFILE,
                    **extra,
                )
            except Exception as error:  # the caller asserts on the type
                return script, error
        return script, admission

    async def test_without_definitions_the_message_is_exactly_what_it_always_was(self) -> None:
        recorded = {
            "dataset": self.SELECTOR, "artifact_identities": [],
            "operation_identity": self.OPERATION, "profile_identity": self.PROFILE,
        }
        for extra in ({}, {"definitions": ()}, {"definitions": []}):
            script, admission = await self.admit((), **extra)
            self.assertNotIsInstance(admission, Exception, extra)
            (message,) = script.sent
            self.assertEqual(recorded, message["request"], extra)
            self.assertEqual("admit", message["operation"])

    async def test_definitions_are_sent_verbatim_and_nothing_else_in_the_body_changes(self) -> None:
        script, admission = await self.admit(self.IDENTITIES, definitions=self.DEFINITIONS)
        self.assertNotIsInstance(admission, Exception)
        (message,) = script.sent
        self.assertEqual(
            {
                "dataset": self.SELECTOR, "artifact_identities": [], "operation_identity": self.OPERATION,
                "profile_identity": self.PROFILE, "definitions": self.DEFINITIONS,
            },
            message["request"],
        )
        # What the deck wrote is what is sent: the client does not canonicalize or complete it, even an entry
        # the server would refuse (the refusal is the server's).
        odd = [{"feature_key": "x", "semantic_version": "9", "parameters": {"b": "2", "a": "1"}}]
        script, _ = await self.admit(self.IDENTITIES, definitions=odd)
        self.assertEqual(odd, script.sent[0]["request"]["definitions"])

    async def test_a_servers_refusal_reaches_the_caller_with_its_reason_and_is_not_retried(self) -> None:
        def reply(request):
            return failure(request, {
                "code": "invalid_request", "message": "the request is not valid",
                "context": {"reason": "definition_bar_duration_mixed"}, "request_identity": None,
            })

        script = Script(reply)
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            with self.assertRaises(deck.RemoteConsumerApiError) as caught:
                await client.admit(
                    dataset=dict(self.SELECTOR), operation_identity=self.OPERATION, definitions=self.DEFINITIONS,
                )
        self.assertEqual("definition_bar_duration_mixed", caught.exception.context["reason"])
        self.assertEqual((1, 1), (script.connections, len(script.sent)))

    async def test_a_sealed_admission_without_identities_is_not_the_answer_to_requested_definitions(self) -> None:
        _, outcome = await self.admit((), definitions=self.DEFINITIONS)
        self.assertIsInstance(outcome, deck.DeliveryIntegrityFailure)
        _, outcome = await self.admit(self.IDENTITIES, definitions=self.DEFINITIONS)
        self.assertEqual(sorted(self.IDENTITIES), list(outcome.manifest["definition_identities"]))
        # A request that named none is not subject to the check, whatever the manifest holds.
        for identities in ((), self.IDENTITIES):
            _, outcome = await self.admit(identities)
            self.assertNotIsInstance(outcome, Exception)

    async def test_definitions_is_a_sequence_of_entries_and_nothing_is_sent_otherwise(self) -> None:
        script = Script(lambda request: ok(request, self.document(self.IDENTITIES)))
        async with deck.DeckHandoffClient("wss://x", ssl_context=strict_context(), connect=script) as client:
            for bad in ("feature", b"feature", dict(self.DEFINITIONS[0])):
                with self.assertRaises(TypeError, msg=repr(bad)):
                    await client.admit(dataset=dict(self.SELECTOR), operation_identity=self.OPERATION, definitions=bad)
        self.assertEqual([], script.sent)


if __name__ == "__main__":
    unittest.main()
