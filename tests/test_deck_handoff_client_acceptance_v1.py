#!/usr/bin/env python3
"""Deck handoff client v1 against a real J09 WSS/mTLS server (#343, ADR-0069 sections 5-8).

A real ``api_transport_server`` listener (a real certificate chain from the
system ``openssl``, real TLS 1.3 mutual TLS, real ``websockets``) composes both
deck families and the J14 transfer store; the deck client runs the whole
handoff against it: admit, deliver, verify, acknowledge, upload, submit.
Tampering is done by a wrapper around the real connector, so what is proven is
the client's own verification, not the server's.
"""

from __future__ import annotations

import ast
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import ssl
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "clients" / "deck"))

import websockets  # noqa: E402

import deck_handoff_client as deck  # noqa: E402
import test_omega_remote_client_acceptance_v1 as pki  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputState, AdmittedInputStore  # noqa: E402
from quant_platform.application.admitted_input_transport import (  # noqa: E402
    AdmittedInputBounds,
    admitted_input_family_handlers,
)
from quant_platform.application.api_transport_server import (  # noqa: E402
    RemoteJ02Principal,
    RemoteJ02SecurityConfig,
    RemoteJ02SecurityEvidenceLog,
    handle_api_transport_connection,
)
from quant_platform.application.framed_result_transport import (  # noqa: E402
    FramedResultConfig,
    FramedResultTransferStore,
    frame_complete_result,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultBundleV1,
    GovernedResultEvidenceStore,
    GovernedResultImportService,
    ResultOutputContractV1,
)
from quant_platform.application.result_import_transport import (  # noqa: E402
    StagedOutputStore,
    StagingBounds,
    bundle_to_wire,
    result_import_family_handlers,
)
from quant_platform.experiments import ExperimentRepository  # noqa: E402
from test_admitted_input_transport_v1 import Authority, admit_body  # noqa: E402
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_governed_result_import_v1 import output, run  # noqa: E402

FRAME = 1024
BOTH = frozenset(deck.DECK_SCOPES)
DATASET = admit_body()["dataset"]


class _Wrapped:
    """A connector that delegates to the real one and can rewrite what the client receives."""

    def __init__(self, real_connect):
        self._real_connect = real_connect
        self._ws = None
        self._context = None

    def __call__(self, url, **kwargs):
        self._context = self._real_connect(url, **kwargs)
        return self

    async def __aenter__(self):
        self._ws = await self._context.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._context.__aexit__(*exc)

    async def send(self, message):
        await self._ws.send(message)

    async def recv(self):
        return await self._ws.recv()


class FlipOneFrameByte(_Wrapped):
    """Corrupts one byte of the first J14 frame, leaving every digest as the server sent it."""

    async def recv(self):
        raw = await self._ws.recv()
        message = json.loads(raw)
        if message.get("message_family") == deck.J14_FAMILY and message.get("message_type") == "frame":
            chunk = bytearray(base64.b64decode(message["chunk_base64"]))
            chunk[0] ^= 0xFF
            message["chunk_base64"] = base64.b64encode(bytes(chunk)).decode()
            return json.dumps(message)
        return raw


class ConsistentRewrite(_Wrapped):
    """An attacker who changes a member's bytes and re-frames it *consistently*.

    Every J14 digest, ``transfer_id`` and descriptor the client sees is
    self-consistent; the only thing it cannot fake is the manifest's content
    hash, which the client holds from the sealed admission.
    """

    def __init__(self, real_connect, *, member_index=0):
        super().__init__(real_connect)
        self._member_index = member_index
        self._forged: dict[str, list[dict]] = {}
        self._pending: list[dict] = []

    async def send(self, message):
        document = json.loads(message)
        if (
            document.get("message_family") == deck.J14_FAMILY
            and document.get("message_type") == "resume"
            and document["transfer_id"] in self._forged
        ):
            self._pending = list(self._forged[document["transfer_id"]])
            return
        await self._ws.send(message)

    async def recv(self):
        if self._pending:
            return json.dumps(self._pending.pop(0))
        raw = await self._ws.recv()
        envelope = json.loads(raw)
        result = envelope.get("result")
        if isinstance(result, dict) and isinstance(result.get("members"), list):
            await self._forge(result["members"][self._member_index])
            return json.dumps(envelope)
        return raw

    async def _forge(self, descriptor):
        await self._ws.send(json.dumps({
            "message_family": deck.J14_FAMILY, "schema_version": deck.J14_FAMILY,
            "message_type": "resume", "transfer_id": descriptor["transfer_id"],
            "next_verified_chunk_index": 0,
        }))
        frames = [json.loads(await self._ws.recv()) for _ in range(descriptor["chunk_count"])]
        document = json.loads(b"".join(base64.b64decode(frame["chunk_base64"]) for frame in frames))
        data = bytearray(base64.b64decode(document["bytes"]))
        data[0] ^= 0x01
        document["bytes"] = base64.b64encode(bytes(data)).decode()  # hash and length claims untouched
        forged = frame_complete_result(
            logical_result_identity=descriptor["logical_result_identity"], result=document,
            config=FramedResultConfig(frame_payload_bytes=FRAME),
        )
        descriptor.update(
            transfer_id=forged[0].transfer_id, payload_sha256=forged[0].payload_sha256,
            chunk_count=forged[0].chunk_count,
        )
        self._forged[forged[0].transfer_id] = [frame.message() for frame in forged]


class ConsistentIdentityRewrite(ConsistentRewrite):
    """Re-attributes member 0 to the other partition, with every digest and id re-derived."""

    async def _forge(self, descriptor):
        await self._ws.send(json.dumps({
            "message_family": deck.J14_FAMILY, "schema_version": deck.J14_FAMILY,
            "message_type": "resume", "transfer_id": descriptor["transfer_id"],
            "next_verified_chunk_index": 0,
        }))
        frames = [json.loads(await self._ws.recv()) for _ in range(descriptor["chunk_count"])]
        document = json.loads(b"".join(base64.b64decode(frame["chunk_base64"]) for frame in frames))
        document["member_identity"] = "partition-v1:2024-01-02"
        descriptor["member_identity"] = document["member_identity"]
        forged = frame_complete_result(
            logical_result_identity=descriptor["logical_result_identity"], result=document,
            config=FramedResultConfig(frame_payload_bytes=FRAME),
        )
        descriptor.update(
            transfer_id=forged[0].transfer_id, payload_sha256=forged[0].payload_sha256,
            chunk_count=forged[0].chunk_count,
        )
        self._forged[forged[0].transfer_id] = [frame.message() for frame in forged]


@unittest.skipUnless(shutil.which("openssl"), "openssl binary is required to generate a real mTLS fixture")
class DeckHandoffClientAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="deck_acceptance_")
        cwd = Path(cls._tmp.name)
        cls.cwd = cwd
        pki._generate_ec_key(cwd, "ca.key")
        pki._self_signed_ca(cwd, key="ca.key", out="ca.crt", cn="Test CA")
        pki._generate_ec_key(cwd, "server.key")
        (cwd / "server_ext.cnf").write_text("subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        pki._signed_certificate(
            cwd, key="server.key", csr="server.csr", cn="127.0.0.1",
            ca_cert="ca.crt", ca_key="ca.key", out="server.crt", extfile="server_ext.cnf",
        )
        pki._generate_ec_key(cwd, "deck.key")
        pki._signed_certificate(
            cwd, key="deck.key", csr="deck.csr", cn="deck-test-client",
            ca_cert="ca.crt", ca_key="ca.key", out="deck.crt",
        )
        pki._generate_ec_key(cwd, "other_ca.key")
        pki._self_signed_ca(cwd, key="other_ca.key", out="other_ca.crt", cn="Other CA")
        pki._generate_ec_key(cwd, "untrusted.key")
        pki._signed_certificate(
            cwd, key="untrusted.key", csr="untrusted.csr", cn="deck-untrusted",
            ca_cert="other_ca.crt", ca_key="other_ca.key", out="untrusted.crt",
        )
        cls.fingerprint = pki._certificate_fingerprint(cwd, "deck.crt")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # -- the server side ---------------------------------------------------------

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self._admissions = Path(directory.name) / "admissions.sqlite"
        self._evidence = Path(directory.name) / "evidence.sqlite"
        self.experiments = ExperimentRepository(FakeConnection())
        self.authority = Authority()
        self.transfers = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=FRAME))
        self.staging = StagedOutputStore(StagingBounds(frame_payload_bytes=FRAME, max_output_bytes=8 * FRAME))
        # Open once so the K13 tables exist before the first request.
        for factory, path in ((AdmittedInputStore, self._admissions), (GovernedResultEvidenceStore, self._evidence)):
            connection = sqlite3.connect(path)
            factory(connection)
            connection.close()

    def admissions(self):
        @contextmanager
        def session():
            connection = sqlite3.connect(self._admissions)
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()
        return session

    def service(self):
        @contextmanager
        def session():
            admissions = sqlite3.connect(self._admissions)
            evidence = sqlite3.connect(self._evidence)
            try:
                yield GovernedResultImportService(
                    AdmittedInputStore(admissions), self.experiments,
                    GovernedResultEvidenceStore(evidence), self.staging.verify_output,
                    (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
                )
            finally:
                admissions.close()
                evidence.close()
        return session

    def security(self, scopes) -> RemoteJ02SecurityConfig:
        return RemoteJ02SecurityConfig(
            server_certificate_path=str(self.cwd / "server.crt"),
            server_private_key_path=str(self.cwd / "server.key"),
            client_trust_anchor_path=str(self.cwd / "ca.crt"),
            server_trust_bundle_version="test-bundle-v1",
            authorization_policy_version="test-policy-v1",
            principals_by_fingerprint={
                self.fingerprint: RemoteJ02Principal("deck-acceptance", frozenset(scopes)),
            },
            evidence_log=RemoteJ02SecurityEvidenceLog(),
        )

    def serve(self, scopes=BOTH):
        security = self.security(scopes)
        handlers = {
            **admitted_input_family_handlers(
                self.admissions(), self.authority.resolve, self.authority.members,
                self.authority.read_member, transfers=self.transfers, bounds=AdmittedInputBounds(),
            ),
            **result_import_family_handlers(self.service(), staging=self.staging),
        }
        return websockets.serve(
            lambda websocket: handle_api_transport_connection(
                websocket, execute=MagicMock(), remote_security=security,
                family_handlers=handlers, framed_transfers=self.transfers,
                framed_resume_scopes={deck.ADMITTED_INPUT_SCOPE},
            ),
            "127.0.0.1", 0, ssl=security.build_server_ssl_context(),
        )

    def ssl_context(self, certificate="deck.crt", key="deck.key"):
        return deck.build_client_ssl_context(
            client_certificate_path=str(self.cwd / certificate),
            client_private_key_path=str(self.cwd / key),
            server_trust_anchor_path=str(self.cwd / "ca.crt"),
        )

    def client(self, server, *, connect=websockets.connect, **options):
        port = server.sockets[0].getsockname()[1]
        return deck.DeckHandoffClient(
            f"wss://127.0.0.1:{port}", ssl_context=options.get("ssl_context", self.ssl_context()),
            connect=connect,
        )

    def stored_state(self, admission_id):
        connection = sqlite3.connect(self._admissions)
        try:
            return AdmittedInputStore(connection).get(admission_id)
        finally:
            connection.close()

    def bundle(self, checksum: str) -> GovernedResultBundleV1:
        manifest = self.authority.manifest(self.authority.resolve_calls[-1])
        identity = run(
            "deck-run-1", code_identity="git:abc123",
            data_identity="dataset-v1:bybit:BTCUSDT", definition_identity="definition-v1:trades",
        )
        return GovernedResultBundleV1(
            manifest.admission_id, manifest.manifest_digest, manifest, manifest.git_identity or "",
            {"python": "3.14"}, {"strategy": "breakout"}, {"seed": 7}, identity,
            (output(identity, checksum),), metrics_evidence={"sharpe": "1.2"},
        )

    # -- AC1: the whole handoff --------------------------------------------------

    async def test_admit_deliver_verify_acknowledge_then_upload_and_submit(self) -> None:
        async with self.serve() as server:
            async with self.client(server) as client:
                admission = await client.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                self.assertEqual("SEALED", admission.state)
                # The client's own recomputation agrees with the server's admission.
                self.assertEqual(
                    self.authority.manifest(self.authority.resolve_calls[-1]).manifest_digest,
                    admission.manifest_digest,
                )
                delivered = await client.fetch_inputs(admission)
                self.assertEqual(tuple(self.authority.members_bytes), delivered.members)
                self.assertEqual(
                    ("partition-v1:2024-01-01", "partition-v1:2024-01-02"), delivered.member_identities,
                )
                self.assertEqual(
                    AdmittedInputState.DELIVERED, self.stored_state(admission.admission_id).state,
                )
                # The workload produces an output; the deck uploads it and submits the bundle.
                result_bytes = bytes(range(256)) * 12  # several server-sized chunks
                staged = await client.upload_output(result_bytes)  # learns the server's chunk size
                self.assertEqual(len(result_bytes), staged.byte_length)
                bundle = self.bundle(staged.checksum)
                registration = await client.submit(bundle_to_wire(bundle))
        self.assertEqual(bundle.bundle_digest, registration.bundle_digest)
        self.assertEqual("SUCCEEDED", registration.run["state"])
        self.assertEqual(
            [{"artifact_role": "metrics", "content_checksum": staged.checksum}],
            list(registration.outputs),
        )
        # The registration is the in-process one: the same bundle, the same digest.
        connection = sqlite3.connect(self._evidence)
        try:
            self.assertTrue(GovernedResultEvidenceStore(connection).is_recorded(bundle))
        finally:
            connection.close()

    # -- AC2: a tampered member is detected before use, and reported --------------

    async def test_a_corrupted_frame_is_detected_before_use_and_acknowledged_as_failed(self) -> None:
        async with self.serve() as server:
            async with self.client(server, connect=FlipOneFrameByte(websockets.connect)) as client:
                admission = await client.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                with self.assertRaises(deck.DeliveryIntegrityFailure) as caught:
                    await client.fetch_inputs(admission)
        self.assertEqual(("transfer_integrity_failure", 0), (caught.exception.reason, caught.exception.member_index))
        record = self.stored_state(admission.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, record.state)
        self.assertEqual("transfer_integrity_failure:member:0", record.delivery_failure_reason)

    async def test_a_consistently_rewritten_member_fails_the_manifest_content_hash(self) -> None:
        async with self.serve() as server:
            async with self.client(
                server, connect=ConsistentRewrite(websockets.connect, member_index=1),
            ) as client:
                admission = await client.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                with self.assertRaises(deck.DeliveryIntegrityFailure) as caught:
                    await client.fetch_inputs(admission)
        # Member 0 was faithful and verified; member 1 is the one named.
        self.assertEqual(("content_hash_mismatch", 1), (caught.exception.reason, caught.exception.member_index))
        record = self.stored_state(admission.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, record.state)
        self.assertEqual("content_hash_mismatch:member:1", record.delivery_failure_reason)

    async def test_a_failed_delivery_is_retried_as_a_new_attempt(self) -> None:
        async with self.serve() as server:
            async with self.client(server, connect=FlipOneFrameByte(websockets.connect)) as bad:
                admission = await bad.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                with self.assertRaises(deck.DeliveryIntegrityFailure):
                    await bad.fetch_inputs(admission)
            async with self.client(server) as client:
                delivered = await client.fetch_inputs(admission)
        self.assertEqual(tuple(self.authority.members_bytes), delivered.members)
        self.assertEqual(AdmittedInputState.DELIVERED, self.stored_state(admission.admission_id).state)

    async def test_a_partition_cannot_be_delivered_under_another_partitions_identity(self) -> None:
        async with self.serve() as server:
            async with self.client(
                server, connect=ConsistentIdentityRewrite(websockets.connect, member_index=0),
            ) as client:
                admission = await client.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                with self.assertRaises(deck.DeliveryIntegrityFailure) as caught:
                    await client.fetch_inputs(admission)
        self.assertEqual(("transfer_integrity_failure", 0), (caught.exception.reason, caught.exception.member_index))
        record = self.stored_state(admission.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, record.state)
        self.assertEqual("transfer_integrity_failure:member:0", record.delivery_failure_reason)

    async def test_the_manifest_of_another_valid_admission_is_not_accepted_for_the_one_asked(self) -> None:
        async with self.serve() as server:
            async with self.client(server) as client:
                first = await client.admit(
                    dataset=DATASET, operation_identity="operation:A", profile_identity="profile:A",
                )
                second = await client.admit(
                    dataset=DATASET, operation_identity="operation:B", profile_identity="profile:B",
                )
            self.assertNotEqual(first.admission_id, second.admission_id)

            class Substitute(_Wrapped):
                async def recv(self):
                    envelope = json.loads(await self._ws.recv())
                    result = envelope.get("result")
                    if isinstance(result, dict) and "manifest" in result:
                        envelope["result"] = {
                            "admission_id": second.admission_id, "manifest_digest": second.manifest_digest,
                            "manifest": dict(second.manifest), "state": second.state,
                        }
                    return json.dumps(envelope)

            async with self.client(server, connect=Substitute(websockets.connect)) as client:
                with self.assertRaises(deck.DeliveryIntegrityFailure):
                    await client.manifest(first.admission_id)
        for admission in (first, second):  # nothing was delivered
            self.assertEqual(AdmittedInputState.SEALED, self.stored_state(admission.admission_id).state)

    # -- ADR-0063 section 1: no plaintext, no non-verifying TLS ------------------------

    async def test_plaintext_and_a_non_verifying_context_are_refused_before_any_connection(self) -> None:
        connections = []

        async def plaintext_server(websocket):
            connections.append(websocket)

        async with websockets.serve(plaintext_server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            with self.assertRaises(deck.InsecureTransportConfiguration):
                deck.DeckHandoffClient(f"ws://127.0.0.1:{port}", ssl_context=self.ssl_context())
        async with self.serve() as server:
            port = server.sockets[0].getsockname()[1]
            context = self.ssl_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with self.assertRaises(deck.InsecureTransportConfiguration):
                deck.DeckHandoffClient(f"wss://127.0.0.1:{port}", ssl_context=context)
        self.assertEqual([], connections)
        self.assertEqual([], self.authority.resolve_calls)

    async def test_an_admission_for_another_operation_or_profile_is_not_the_one_requested(self) -> None:
        async with self.serve() as server:
            async with self.client(server) as client:
                other = await client.admit(
                    dataset=DATASET, operation_identity="operation:B", profile_identity="profile:A",
                )
            for label, change in (
                ("another operation", {"operation_identity": "operation:B", "profile_identity": "profile:A"}),
                ("another profile", {"operation_identity": "operation:A", "profile_identity": "profile:B"}),
                ("a profile that was not requested", {"operation_identity": "operation:A", "profile_identity": None}),
            ):
                with self.subTest(label):
                    returned = await self._admit_as_returned(server, change)
                    self.assertEqual(
                        AdmittedInputState.SEALED, self.stored_state(returned.admission_id).state,
                    )
        self.assertEqual(AdmittedInputState.SEALED, self.stored_state(other.admission_id).state)

    async def _admit_as_returned(self, server, other):
        """Admit A, while the wire carries the sealed admission ``other`` (a real one) instead."""

        async with self.client(server) as client:
            real = await client.admit(dataset=DATASET, **other)

        class Substitute(_Wrapped):
            async def recv(self):
                envelope = json.loads(await self._ws.recv())
                result = envelope.get("result")
                if isinstance(result, dict) and "manifest" in result:
                    envelope["result"] = {
                        "admission_id": real.admission_id, "manifest_digest": real.manifest_digest,
                        "manifest": dict(real.manifest), "state": real.state,
                    }
                return json.dumps(envelope)

        async with self.client(server, connect=Substitute(websockets.connect)) as client:
            with self.assertRaises(deck.DeliveryIntegrityFailure):
                await client.admit(
                    dataset=DATASET, operation_identity="operation:A", profile_identity="profile:A",
                )
        return real

    async def test_a_wrong_authority_or_name_is_still_a_remote_security_failure(self) -> None:
        async with self.serve() as server:
            port = server.sockets[0].getsockname()[1]
            wrong_ca = deck.build_client_ssl_context(
                client_certificate_path=str(self.cwd / "deck.crt"),
                client_private_key_path=str(self.cwd / "deck.key"),
                server_trust_anchor_path=str(self.cwd / "other_ca.crt"),
            )
            with self.assertRaises(deck.RemoteSecurityFailure):
                async with deck.DeckHandoffClient(f"wss://127.0.0.1:{port}", ssl_context=wrong_ca) as client:
                    await client.admit(dataset=DATASET, operation_identity="strategy-compose-v1")
            # The certificate names 127.0.0.1 only; another name is not the endpoint.
            with self.assertRaises(deck.RemoteSecurityFailure):
                async with deck.DeckHandoffClient(
                    f"wss://localhost:{port}", ssl_context=self.ssl_context(),
                ) as client:
                    await client.admit(dataset=DATASET, operation_identity="strategy-compose-v1")
        self.assertEqual([], self.authority.resolve_calls)

    # -- AC3: typed failures, never a Consumer error -------------------------------

    async def test_a_response_of_another_family_or_version_is_wire_incompatible(self) -> None:
        class Reply(_Wrapped):
            async def recv(self):
                envelope = json.loads(await self._ws.recv())
                envelope["schema_version"] = "j02-admitted-input-v2-response-v1"
                return json.dumps(envelope)

        async with self.serve() as server:
            async with self.client(server, connect=Reply(websockets.connect)) as client:
                with self.assertRaises(deck.WireIncompatible):
                    await client.admit(
                        dataset=DATASET, operation_identity="strategy-compose-v1",
                        profile_identity="profile-v1:default",
                    )

    async def test_an_untrusted_certificate_is_a_remote_security_failure(self) -> None:
        async with self.serve() as server:
            with self.assertRaises(deck.RemoteSecurityFailure):
                async with self.client(
                    server, ssl_context=self.ssl_context("untrusted.crt", "untrusted.key"),
                ) as client:
                    await client.admit(dataset=DATASET, operation_identity="strategy-compose-v1")
        self.assertEqual([], self.authority.resolve_calls)

    async def test_a_missing_scope_is_a_remote_security_failure_on_that_family_only(self) -> None:
        async with self.serve(scopes={deck.ADMITTED_INPUT_SCOPE}) as server:
            async with self.client(server) as client:
                admission = await client.admit(
                    dataset=DATASET, operation_identity="strategy-compose-v1",
                    profile_identity="profile-v1:default",
                )
                self.assertEqual("SEALED", admission.state)
            async with self.client(server) as client:
                with self.assertRaises(deck.RemoteSecurityFailure):
                    await client.upload_output(b"output bytes")
        self.assertEqual((), self.staging.staged_checksums())
        async with self.serve(scopes=frozenset()) as server:
            with self.assertRaises(deck.RemoteSecurityFailure):
                async with self.client(server) as client:
                    await client.admit(dataset=DATASET, operation_identity="strategy-compose-v1")

    async def test_a_server_refusal_is_a_consumer_error_not_a_security_failure(self) -> None:
        async with self.serve() as server:
            async with self.client(server) as client:
                with self.assertRaises(deck.RemoteConsumerApiError) as caught:
                    await client.manifest("admitted-input-v1:" + "0" * 64)
        self.assertEqual("invalid_request", caught.exception.code)

    # -- AC4: the deck client imports nothing of the platform ---------------------

    def test_the_client_imports_no_platform_package(self) -> None:
        source = (ROOT / "clients" / "deck" / "deck_handoff_client.py").read_text(encoding="utf-8")
        roots = {
            (alias.name if isinstance(node, ast.Import) else node.module or "").split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertEqual(set(), roots & {"quant_platform", "sqlite3", "psycopg", "pyarrow", "importlib"})
        # No second transport and no local fallback is even importable by name.
        self.assertEqual(set(), roots & {"socket", "urllib", "http", "subprocess", "requests", "aiohttp"})


if __name__ == "__main__":
    unittest.main()
