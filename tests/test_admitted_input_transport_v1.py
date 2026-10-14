"""``j02-admitted-input-v1``: K12 delivery to the deck over authenticated J02 (#341, P06).

``AdmittedInputTransportV1Tests`` is backend-neutral: ``provider()`` yields a
*new* store on its own connection per request, as the family composition
requires. SQLite here; tests/integration_runtime_stores_postgres.py subclasses
it for the PostgreSQL runtime schema and its real roles.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import quant_platform.application.api_transport_server as server  # noqa: E402
from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputManifestV1,
    AdmittedInputNotFound,
    AdmittedInputState,
    AdmittedInputStore,
)
from quant_platform.application.admitted_input_transport import (  # noqa: E402
    ADMITTED_INPUT_MEMBER_SCHEMA,
    J02_ADMITTED_INPUT_FAMILY,
    J02_ADMITTED_INPUT_REQUEST_VERSION,
    J02_ADMITTED_INPUT_RESPONSE_VERSION,
    J02_ADMITTED_INPUT_SCOPE,
    AdmittedInputBounds,
    AdmittedInputMember,
    AdmittedInputRequest,
    AdmittedInputTransport,
    admitted_input_family_handlers,
    aligned_partition_members,
)
from quant_platform.application.framed_result_transport import (  # noqa: E402
    J14_MESSAGE_FAMILY,
    J14_SCHEMA_VERSION,
    FramedResultConfig,
    FramedResultReceiver,
    FramedResultTransferStore,
    FramedResultTransportError,
)
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    RepresentationRef,
    resolve_market_data_request,
)
from test_api_transport_families_v1 import Socket  # noqa: E402
from test_remote_j02_security_v1 import security  # noqa: E402

SCOPE = J02_ADMITTED_INPUT_SCOPE
READ_JOBS, SUBMIT_JOBS, MARKET = "j02.jobs.read", "j02.jobs.submit", "j02.market_data.read"
FRAME_BYTES = 1024  # small frames keep the multi-frame case cheap to test
OPERATIONS = ("admit", "manifest", "deliver", "acknowledge")
UNIMPORTANT_DIGEST = "0" * 64


def admit_body(**overrides):
    body = {
        "dataset": {
            "venue": "bybit",
            "instrument": "BTCUSDT",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-03T00:00:00Z",
            "representation": {"kind": "trades", "version": 1},
        },
        "artifact_identities": [],
        "operation_identity": "strategy-compose-v1",
        "profile_identity": "profile-v1:default",
    }
    body.update(overrides)
    return body


class Authority:
    """A server authority test double: resolves a request, declares and reads members."""

    def __init__(self, members=None):
        # The second member is several J14 frames once base64-wrapped.
        self.members_bytes = list(members) if members is not None else [
            b"alpha-partition-bytes", bytes(range(256)) * 12,
        ]
        self.resolve_calls: list[AdmittedInputRequest] = []
        self.read_calls: list[int] = []
        self.tampered: dict[int, bytes] = {}

    def manifest(self, request: AdmittedInputRequest) -> AdmittedInputManifestV1:
        logical = (
            ("dataset-v1:bybit:BTCUSDT",) if request.dataset is not None
            else request.artifact_identities
        )
        return AdmittedInputManifestV1(
            logical_input_identities=logical,
            natural_partition_identities=tuple(
                f"partition-v1:2024-01-0{n + 1}" for n in range(len(self.members_bytes))
            ),
            schema_identity="trades@1", schema_version="1", schema_hash="schema-sha256:abc",
            manifest_hashes=("manifest-sha256:abc",),
            content_hashes=tuple(hashlib.sha256(data).hexdigest() for data in self.members_bytes),
            declared_coverage={"start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"},
            request_identity="request-v1:alpha", result_identity=None,
            definition_identities=("definition-v1:trades",),
            implementation_identity="implementation-v1:abc", git_identity="git:abc123",
            operation_identity=request.operation_identity,
            profile_identity=request.profile_identity,
        )

    def resolve(self, request):
        self.resolve_calls.append(request)
        return self.manifest(request)

    def members(self, manifest):
        return aligned_partition_members(
            manifest, tuple(len(data) for data in self.members_bytes),
        )

    def read_member(self, manifest, index):
        self.read_calls.append(index)
        return self.tampered.get(index, self.members_bytes[index])


class ArtifactAuthority(Authority):
    """An artifact-only manifest: no natural partitions, members declared by the authority."""

    ARTIFACT = "feature-artifact-v1:sha256:" + "ab" * 32

    def manifest(self, request):
        return AdmittedInputManifestV1(
            logical_input_identities=(self.ARTIFACT,),
            natural_partition_identities=(),
            schema_identity=None, schema_version=None, schema_hash=None,
            manifest_hashes=(),
            content_hashes=tuple(hashlib.sha256(data).hexdigest() for data in self.members_bytes),
            declared_coverage=None, request_identity="request-v1:artifact", result_identity=None,
            definition_identities=(), implementation_identity=None, git_identity=None,
            operation_identity=request.operation_identity, profile_identity=None,
        )

    def members(self, manifest):
        # The artifact's own output members, identified by the artifact it names.
        return tuple(
            AdmittedInputMember(self.ARTIFACT, hashlib.sha256(data).hexdigest(), len(data))
            for data in self.members_bytes
        )


class AdmittedInputTransportV1Tests(unittest.IsolatedAsyncioTestCase):
    # -- backend hooks -----------------------------------------------------------

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "admissions.sqlite"
        self.authority = Authority()
        self.transfers = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=FRAME_BYTES))

    def open_store(self) -> AdmittedInputStore:
        connection = sqlite3.connect(self._path)
        self.addCleanup(connection.close)
        return AdmittedInputStore(connection)

    def provider(self):
        @contextmanager
        def session():
            connection = sqlite3.connect(self._path)
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()
        return session

    # -- wire helpers ------------------------------------------------------------

    def routes(self, authority=None, transfers=None, bounds=AdmittedInputBounds()):
        authority = authority or self.authority
        return admitted_input_family_handlers(
            self.provider(), authority.resolve, authority.members, authority.read_member,
            transfers=transfers or self.transfers, bounds=bounds,
        )

    async def exchange(self, operation, request, *, scopes=frozenset({SCOPE}), handlers=None,
                       request_id="req-1"):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 64
        socket = Socket([json.dumps({
            "message_family": J02_ADMITTED_INPUT_FAMILY,
            "schema_version": J02_ADMITTED_INPUT_REQUEST_VERSION,
            "operation": operation, "request_id": request_id, "request": request,
        })])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=handlers or self.routes(),
        )
        return socket, configuration

    async def call(self, operation, request, **options):
        socket, _ = await self.exchange(operation, request, **options)
        self.assertEqual([], socket.close_calls)
        return socket.responses[0]

    def ok(self, response):
        self.assertEqual("ok", response["status"], response)
        self.assertEqual(J02_ADMITTED_INPUT_FAMILY, response["message_family"])
        self.assertEqual(J02_ADMITTED_INPUT_RESPONSE_VERSION, response["schema_version"])
        return response["result"]

    def refused(self, response, reason, code="invalid_request"):
        self.assertEqual("error", response["status"], response)
        self.assertEqual(code, response["error"]["code"])
        self.assertEqual(reason, response["error"]["context"]["reason"])
        return response["error"]

    async def admit(self, **options):
        return self.ok(await self.call("admit", admit_body(), **options))

    async def resume(self, transfer_id, *, scopes=frozenset({SCOPE}),
                     resume_scopes=frozenset({SCOPE})):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 32
        socket = Socket([json.dumps({
            "message_family": J14_MESSAGE_FAMILY, "schema_version": J14_SCHEMA_VERSION,
            "message_type": "resume", "transfer_id": transfer_id, "next_verified_chunk_index": 0,
        })])
        await server.handle_api_transport_connection(
            socket, execute=MagicMock(), remote_security=configuration,
            family_handlers=self.routes(), framed_transfers=self.transfers,
            framed_resume_scopes=resume_scopes,
        )
        return socket, configuration

    async def fetch(self, descriptor):
        """What the deck does: resume, reassemble, and verify; returns the member document."""

        socket, _ = await self.resume(descriptor["transfer_id"])
        self.assertEqual([], socket.close_calls)
        self.assertEqual(descriptor["chunk_count"], len(socket.responses))
        receiver = FramedResultReceiver()
        document = None
        for frame in socket.responses:
            self.assertEqual(descriptor["logical_result_identity"], frame["logical_result_identity"])
            self.assertEqual(descriptor["payload_sha256"], frame["payload_sha256"])
            document = receiver.receive(frame)
        return document

    def state(self, admission_id):
        return self.open_store().get(admission_id).state

    # -- composition -------------------------------------------------------------

    def test_the_family_declares_exactly_four_operations_under_one_scope(self) -> None:
        routes = self.routes()
        self.assertEqual(
            {(J02_ADMITTED_INPUT_FAMILY, J02_ADMITTED_INPUT_REQUEST_VERSION, op) for op in OPERATIONS},
            set(routes),
        )
        for route in routes.values():
            self.assertEqual("j02.admitted_input.read", route.required_scope)
            self.assertIsNone(route.additional_scopes)

    def test_composition_is_explicit_and_validated(self) -> None:
        authority = self.authority
        good = (self.provider(), authority.resolve, authority.members, authority.read_member)
        with self.assertRaises(TypeError):
            admitted_input_family_handlers("provider", *good[1:], transfers=self.transfers)
        with self.assertRaises(TypeError):
            admitted_input_family_handlers(
                good[0], "resolver", good[2], good[3], transfers=self.transfers,
            )
        with self.assertRaises(TypeError):
            admitted_input_family_handlers(
                good[0], good[1], None, good[3], transfers=self.transfers,
            )
        with self.assertRaises(TypeError):
            admitted_input_family_handlers(*good, transfers=None)
        with self.assertRaises(TypeError):
            admitted_input_family_handlers(*good, transfers=self.transfers, bounds=object())
        # A bound that the transfer store could not retain is a composition error,
        # not a promise that admit would make and deliver break.
        for label, bounds in (
            ("more members than in-flight transfers", AdmittedInputBounds(max_members=33)),
            ("more bytes than retained", AdmittedInputBounds(
                max_member_bytes=8 * 1024 * 1024, max_total_bytes=8 * 1024 * 1024)),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                admitted_input_family_handlers(*good, transfers=self.transfers, bounds=bounds)
        with self.assertRaises(ValueError):
            AdmittedInputBounds(max_member_bytes=10, max_total_bytes=5)

    # -- admit (AC1, AC4, AC5) ---------------------------------------------------

    async def test_admit_seals_the_manifest_identical_to_in_process_k12(self) -> None:
        result = await self.admit()
        request = self.authority.resolve_calls[-1]
        # The in-process K12 path for the same input: resolve, admit, seal.
        in_process = AdmittedInputStore(sqlite3.connect(":memory:"))
        expected = self.authority.manifest(request)
        in_process.admit(expected)
        in_process.seal(expected.admission_id)
        self.assertEqual(expected.admission_id, result["admission_id"])
        self.assertEqual(expected.manifest_digest, result["manifest_digest"])
        self.assertEqual(expected.canonical_fields(), result["manifest"])
        self.assertEqual("SEALED", result["state"])
        self.assertIsNone(result["delivery_failure_reason"])
        stored = self.open_store().get(result["admission_id"])
        self.assertEqual(expected, stored.manifest)
        self.assertEqual(AdmittedInputState.SEALED, stored.state)
        # The authority was given meaning, not locators.
        self.assertEqual("bybit", request.dataset.venue)
        self.assertEqual("trades", request.dataset.representation.kind)
        self.assertEqual((), request.artifact_identities)

    async def test_admit_accepts_accepted_artifact_identities(self) -> None:
        body = admit_body(dataset=None, artifact_identities=["artifact-v1:sha256:aa", "artifact-v1:sha256:bb"])
        result = self.ok(await self.call("admit", body))
        request = self.authority.resolve_calls[-1]
        self.assertIsNone(request.dataset)
        self.assertEqual(("artifact-v1:sha256:aa", "artifact-v1:sha256:bb"), request.artifact_identities)
        self.assertEqual(
            ["artifact-v1:sha256:aa", "artifact-v1:sha256:bb"],
            result["manifest"]["logical_input_identities"],
        )

    async def test_an_artifact_only_manifest_is_delivered_with_its_declared_members(self) -> None:
        authority = ArtifactAuthority([b"artifact-output-one", bytes(range(200)) * 9])
        handlers = self.routes(authority=authority)
        body = admit_body(dataset=None, artifact_identities=[ArtifactAuthority.ARTIFACT])
        admitted = self.ok(await self.call("admit", body, handlers=handlers))
        # The in-process K12 API admits and seals it with no natural partition.
        self.assertEqual([], admitted["manifest"]["natural_partition_identities"])
        self.assertEqual("SEALED", admitted["state"])
        delivery = self.ok(await self.call(
            "deliver", {"admission_id": admitted["admission_id"]}, handlers=handlers,
        ))
        self.assertEqual("DELIVERY_PENDING", delivery["state"])
        self.assertEqual(2, len(delivery["members"]))
        for index, descriptor in enumerate(delivery["members"]):
            with self.subTest(member=index):
                self.assertEqual(ArtifactAuthority.ARTIFACT, descriptor["member_identity"])
                self.assertEqual(
                    f"{ADMITTED_INPUT_MEMBER_SCHEMA}:{admitted['admission_id']}:{index}",
                    descriptor["logical_result_identity"],
                )
                document = await self.fetch(descriptor)
                data = base64.b64decode(document["bytes"], validate=True)
                self.assertEqual(authority.members_bytes[index], data)
                self.assertEqual(
                    admitted["manifest"]["content_hashes"][index], hashlib.sha256(data).hexdigest(),
                )

    async def test_readmission_is_idempotent_and_never_moves_a_delivery_back(self) -> None:
        first = await self.admit()
        self.assertEqual(first, await self.admit())
        self.ok(await self.call("deliver", {"admission_id": first["admission_id"]}))
        again = await self.admit()
        self.assertEqual(first["admission_id"], again["admission_id"])
        self.assertEqual("DELIVERY_PENDING", again["state"])  # not resealed, not reset

    async def test_a_separated_symbol_is_the_same_query_on_the_wire_as_in_process(self) -> None:
        dataset = admit_body()["dataset"]
        for instrument in ("BTC/USD", "ETH/USDT", "BTC/USDT:USDT", "BRK.B"):
            with self.subTest(instrument):
                canonical = ConsumerMarketDataQuery(
                    venue="bybit", instrument=instrument,
                    start="2024-01-01T00:00:00Z", end="2024-01-03T00:00:00Z",
                    representation=RepresentationRef(kind="trades", version=1),
                )
                resolve_market_data_request(canonical)  # the Consumer API accepts it
                calls = len(self.authority.resolve_calls)
                self.ok(await self.call(
                    "admit", admit_body(dataset={**dataset, "instrument": instrument}),
                ))
                self.assertEqual(calls + 1, len(self.authority.resolve_calls))
                self.assertEqual(canonical, self.authority.resolve_calls[-1].dataset)

    async def test_a_symbol_is_not_a_way_to_name_a_path(self) -> None:
        dataset = admit_body()["dataset"]
        for instrument in (
            "/srv/storage/BTCUSD", "BTC/USD/EXTRA", "BTC//USD", "BTC/", "/USD", "../BTC",
            "BTC/..", "./BTC", "private-export.parquet", "BTC/USD.parquet", "BTC\\USD",
            "file://BTC/USD", "~/BTC",
        ):
            with self.subTest(instrument):
                self.refused(
                    await self.call("admit", admit_body(dataset={**dataset, "instrument": instrument})),
                    "locator_not_accepted",
                )
        self.assertEqual([], self.authority.resolve_calls)

    async def test_a_locator_named_definition_field_never_reaches_the_authority(self) -> None:
        dataset = admit_body()["dataset"]

        def definition(**fields):
            return admit_body(dataset={
                **dataset, "representation": {"kind": "trades", "version": 1, "definition": fields},
            })

        for label, body in {
            "path": definition(path="private-export.parquet"),
            "path and root": definition(path="private-export.parquet", storage_root="private-root"),
            "camel case": definition(filePath="export"),
            "dashed": definition(**{"catalog-uuid": "x"}),
            "mount": definition(mount="data"),
            "file suffix value": definition(source="private-export.parquet"),
        }.items():
            with self.subTest(label):
                self.refused(await self.call("admit", body), "locator_not_accepted")
        self.assertEqual([], self.authority.resolve_calls)
        # Names that only *contain* a locator word are semantic, and pass.
        self.ok(await self.call("admit", definition(profile="default", direction=1, windowed=True)))
        self.assertEqual(
            {"profile": "default", "direction": 1, "windowed": True},
            self.authority.resolve_calls[-1].dataset.representation.definition,
        )

    async def test_a_locator_in_the_request_is_invalid_before_the_authority_runs(self) -> None:
        dataset = admit_body()["dataset"]

        def with_dataset(**changes):
            return admit_body(dataset={**dataset, **changes})

        cases = {
            "path field": admit_body(path="/srv/storage/trades.parquet"),
            "windows path field": admit_body(file_path="C:\\data\\trades.parquet"),
            "catalog uuid field": admit_body(catalog_uuid="123e4567-e89b-12d3-a456-426614174000"),
            "storage root field": admit_body(storage_root="/srv/storage"),
            "transport url field": admit_body(transport_url="wss://server/export"),
            "locator in dataset": with_dataset(path="/srv/storage"),
            "path as instrument": with_dataset(instrument="../../etc/passwd"),
            "absolute path as instrument": with_dataset(instrument="/srv/storage/BTCUSDT"),
            "path as venue": with_dataset(venue="/srv/storage/bybit"),
            "windows drive as instrument": with_dataset(instrument="C:\\data\\BTCUSDT"),
            "uuid as instrument": with_dataset(instrument="123e4567-e89b-12d3-a456-426614174000"),
            "uuid without dashes": with_dataset(instrument="123e4567e89b12d3a456426614174000"),
            "url as representation kind": with_dataset(
                representation={"kind": "https://x/trades", "version": 1}),
            "locator in definition": with_dataset(
                representation={"kind": "trades", "version": 1,
                                "definition": {"source": "/srv/storage"}}),
            "path as artifact": admit_body(dataset=None, artifact_identities=["/srv/artifacts/a"]),
            "uuid as artifact": admit_body(
                dataset=None, artifact_identities=["123e4567-e89b-12d3-a456-426614174000"]),
            "path as operation": admit_body(operation_identity="../strategy"),
            "uri as profile": admit_body(profile_identity="file:///profiles/default"),
        }
        for label, body in cases.items():
            with self.subTest(label):
                self.refused(await self.call("admit", body), "locator_not_accepted")
        self.assertEqual([], self.authority.resolve_calls)
        self.assertEqual([], self.authority.read_calls)

    async def test_malformed_requests_are_invalid_before_the_authority_runs(self) -> None:
        dataset = admit_body()["dataset"]
        cases = {
            "missing field": {k: v for k, v in admit_body().items() if k != "operation_identity"},
            "unknown field": admit_body(extra=1),
            "neither selector": admit_body(dataset=None, artifact_identities=[]),
            "both selectors": admit_body(artifact_identities=["artifact-v1:sha256:aa"]),
            "duplicate artifacts": admit_body(
                dataset=None, artifact_identities=["artifact-v1:sha256:aa", "artifact-v1:sha256:aa"]),
            "too many artifacts": admit_body(
                dataset=None, artifact_identities=[f"artifact-v1:{n}" for n in range(257)]),
            "artifacts not a list": admit_body(dataset=None, artifact_identities="artifact-v1:aa"),
            "version as bool": admit_body(
                dataset={**dataset, "representation": {"kind": "trades", "version": True}}),
            "version as string": admit_body(
                dataset={**dataset, "representation": {"kind": "trades", "version": "1"}}),
            "interval as number": admit_body(dataset={**dataset, "start": 20240101}),
            "missing interval end": admit_body(
                dataset={k: v for k, v in dataset.items() if k != "end"}),
            "unknown representation field": admit_body(
                dataset={**dataset, "representation": {"kind": "trades", "version": 1, "x": 1}}),
            "nested definition": admit_body(
                dataset={**dataset, "representation": {
                    "kind": "trades", "version": 1, "definition": {"a": {"b": 1}}}}),
            "oversized text": admit_body(operation_identity="x" * 513),
            "empty text": admit_body(operation_identity=""),
            "padded text": admit_body(operation_identity=" strategy-compose-v1"),
            "control character": admit_body(operation_identity="strategy\ncompose"),
        }
        for label, body in cases.items():
            with self.subTest(label):
                self.refused(await self.call("admit", body), "invalid_request_shape")
        # The family envelope refuses these before this codec sees them: a
        # request must be a lossless object (no float, no non-object body).
        for label, body in (("not an object", []), ("a float", admit_body(operation_identity=1.5))):
            with self.subTest(label):
                response = await self.call("admit", body)
                self.assertEqual("error", response["status"])
                self.assertEqual("invalid_request", response["error"]["code"])
        self.assertEqual([], self.authority.resolve_calls)

    async def test_an_oversized_admit_is_refused_before_anything_is_sealed(self) -> None:
        cases = (
            ("member count", AdmittedInputBounds(max_members=1), "member_count_exceeded"),
            ("member bytes", AdmittedInputBounds(max_member_bytes=1000, max_total_bytes=4096),
             "member_bytes_exceeded"),
            ("total bytes", AdmittedInputBounds(max_member_bytes=3072, max_total_bytes=3072),
             "total_bytes_exceeded"),
        )
        for label, bounds, reason in cases:
            with self.subTest(label):
                response = await self.call(
                    "admit", admit_body(), handlers=self.routes(bounds=bounds),
                )
                self.refused(response, reason, code="result_too_large")
        self.assertEqual([], self.authority.read_calls)  # sizes, not bytes, decide
        # Nothing was sealed, so the same request is admitted under honest bounds.
        with self.assertRaises(AdmittedInputNotFound):
            self.open_store().get(
                self.authority.manifest(self.authority.resolve_calls[-1]).admission_id
            )
        self.assertEqual("SEALED", (await self.admit())["state"])

    # -- manifest ----------------------------------------------------------------

    async def test_manifest_returns_the_sealed_manifest_and_refuses_unknown_admissions(self) -> None:
        admitted = await self.admit()
        again = self.ok(await self.call("manifest", {"admission_id": admitted["admission_id"]}))
        self.assertEqual(admitted, again)
        absent = "admitted-input-v1:" + "a" * 64
        self.refused(await self.call("manifest", {"admission_id": absent}), "admission_not_found")
        for bad in ("admitted-input-v1:short", "dataset-v1:bybit", "/srv/admission", 7):
            with self.subTest(bad):
                self.refused(
                    await self.call("manifest", {"admission_id": bad}), "invalid_request_shape",
                )

    # -- deliver (AC2) -----------------------------------------------------------

    async def test_deliver_moves_to_pending_and_every_member_verifies_against_the_manifest(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        delivery = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        self.assertEqual("DELIVERY_PENDING", delivery["state"])
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.state(admission_id))
        self.assertEqual(admitted["manifest_digest"], delivery["manifest_digest"])
        manifest = admitted["manifest"]
        self.assertEqual(len(manifest["content_hashes"]), len(delivery["members"]))
        for index, descriptor in enumerate(delivery["members"]):
            with self.subTest(member=index):
                self.assertEqual(
                    f"{ADMITTED_INPUT_MEMBER_SCHEMA}:{admission_id}:{index}",
                    descriptor["logical_result_identity"],
                )
                document = await self.fetch(descriptor)
                self.assertEqual(
                    {
                        "schema_version": ADMITTED_INPUT_MEMBER_SCHEMA,
                        "admission_id": admission_id,
                        "manifest_digest": admitted["manifest_digest"],
                        "member_index": index,
                        "member_identity": manifest["natural_partition_identities"][index],
                        "content_hash": manifest["content_hashes"][index],
                        "byte_length": len(self.authority.members_bytes[index]),
                        "encoding": "base64",
                        "bytes": base64.b64encode(self.authority.members_bytes[index]).decode(),
                    },
                    document,
                )
                data = base64.b64decode(document["bytes"], validate=True)
                # The deck's own check: the reassembled bytes hash to the manifest.
                self.assertEqual(manifest["content_hashes"][index], hashlib.sha256(data).hexdigest())
                self.assertEqual(descriptor["byte_length"], len(data))
        self.assertGreater(delivery["members"][1]["chunk_count"], 1)  # genuinely framed

    async def test_a_corrupted_chunk_is_an_integrity_failure_and_unusable(self) -> None:
        admitted = await self.admit()
        delivery = self.ok(await self.call("deliver", {"admission_id": admitted["admission_id"]}))
        descriptor = delivery["members"][1]
        socket, _ = await self.resume(descriptor["transfer_id"])
        frames = [dict(frame) for frame in socket.responses]
        raw = bytearray(base64.b64decode(frames[0]["chunk_base64"]))
        raw[0] ^= 0xFF
        frames[0]["chunk_base64"] = base64.b64encode(bytes(raw)).decode()
        completed = []
        receiver = FramedResultReceiver()
        with self.assertRaises(FramedResultTransportError) as caught:
            for frame in frames:
                completed.append(receiver.receive(frame))
        self.assertEqual("integrity_failure", caught.exception.outcome)
        self.assertEqual([], completed)  # nothing was completed from a damaged transfer
        # A chunk damaged *and* re-hashed to look consistent is caught by the
        # whole-payload digest, at the last frame, and still yields nothing.
        frames[0]["chunk_sha256"] = hashlib.sha256(bytes(raw)).hexdigest()
        completed.clear()
        receiver = FramedResultReceiver()
        with self.assertRaises(FramedResultTransportError) as caught:
            for frame in frames:
                completed.append(receiver.receive(frame))
        self.assertEqual("integrity_failure", caught.exception.outcome)
        self.assertEqual([None] * (len(frames) - 1), completed)  # only partials, no result

    async def test_a_member_that_does_not_match_its_manifest_hash_is_never_delivered(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        self.authority.tampered[1] = self.authority.members_bytes[1][:-1] + b"\x00"
        response = await self.call("deliver", {"admission_id": admission_id})
        self.refused(response, "member_content_mismatch", code="integrity_failure")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admission_id))  # unchanged
        self.assertEqual(0, len(self.transfers._transfers))  # nothing retained for a bad input
        # Once the authority is right again, the same sealed admission delivers.
        self.authority.tampered.clear()
        self.ok(await self.call("deliver", {"admission_id": admission_id}))

    async def test_deliver_is_bounded_by_the_bytes_it_actually_reads(self) -> None:
        admitted = await self.admit()
        self.authority.members_bytes[1] = bytes(range(256)) * 20  # grew since it was admitted
        self.authority.tampered[1] = self.authority.members_bytes[1]
        response = await self.call(
            "deliver", {"admission_id": admitted["admission_id"]},
            handlers=self.routes(bounds=AdmittedInputBounds(max_member_bytes=3000, max_total_bytes=4000)),
        )
        self.refused(response, "member_bytes_exceeded", code="result_too_large")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admitted["admission_id"]))

    async def test_a_refused_retention_leaves_the_admission_sealed(self) -> None:
        admitted = await self.admit()
        small = FramedResultTransferStore(FramedResultConfig(
            frame_payload_bytes=FRAME_BYTES, max_in_flight_transfers=2,
        ))
        # Another holder already uses one of the two transfer slots.
        small.admit(logical_result_identity="result:sha256:other", result={"n": 1})
        before = dict(small._transfers)
        response = await self.call(
            "deliver", {"admission_id": admitted["admission_id"]},
            handlers=self.routes(transfers=small, bounds=AdmittedInputBounds(max_members=2)),
        )
        self.refused(response, "transfer_refused", code="result_too_large")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admitted["admission_id"]))
        # Nothing of this delivery was retained: no resumable partial set, and the
        # slot the other holder left free is still free.
        self.assertEqual(before, dict(small._transfers))
        self.assertEqual(1, len(small._transfers))
        small.admit(logical_result_identity="result:sha256:next", result={"n": 2})

    # -- acknowledge (AC3) -------------------------------------------------------

    async def test_acknowledge_success_yields_delivered(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        self.ok(await self.call("deliver", {"admission_id": admission_id}))
        result = self.ok(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "delivered",
            "manifest_digest": admitted["manifest_digest"],
            "failure_reason": None, "failed_member_index": None,
        }))
        self.assertEqual("DELIVERED", result["state"])
        self.assertEqual(AdmittedInputState.DELIVERED, self.state(admission_id))
        # Delivered is final: not delivered again, not acknowledged again, and a
        # refused delivery retains nothing for the deck to fetch.
        self.transfers._transfers.clear()
        self.refused(
            await self.call("deliver", {"admission_id": admission_id}), "delivery_not_available",
        )
        self.assertEqual(0, len(self.transfers._transfers))
        self.assertEqual([], self.authority.read_calls[2:])  # no member was even read again
        self.refused(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "delivered",
            "manifest_digest": admitted["manifest_digest"],
            "failure_reason": None, "failed_member_index": None,
        }), "acknowledgement_not_expected")

    async def test_a_reported_failure_is_delivery_failed_and_a_new_attempt_reuses_the_admission(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        first = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        failed = self.ok(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "failed", "manifest_digest": None,
            "failure_reason": "content_hash_mismatch", "failed_member_index": 1,
        }))
        self.assertEqual("DELIVERY_FAILED", failed["state"])
        self.assertEqual("content_hash_mismatch:member:1", failed["delivery_failure_reason"])
        # The sealed admission is unchanged by a failed attempt.
        self.assertEqual(admitted["manifest"], self.ok(
            await self.call("manifest", {"admission_id": admission_id}))["manifest"])
        second = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        self.assertEqual("DELIVERY_PENDING", second["state"])
        self.assertEqual(first["members"], second["members"])  # same sealed bytes, same transfers
        self.assertIsNone(self.open_store().get(admission_id).delivery_failure_reason)

    async def test_a_digest_the_manifest_does_not_have_fails_the_delivery(self) -> None:
        admitted = await self.admit()
        self.ok(await self.call("deliver", {"admission_id": admitted["admission_id"]}))
        result = self.ok(await self.call("acknowledge", {
            "admission_id": admitted["admission_id"], "outcome": "delivered",
            "manifest_digest": UNIMPORTANT_DIGEST, "failure_reason": None,
            "failed_member_index": None,
        }))
        self.assertEqual("DELIVERY_FAILED", result["state"])
        self.assertEqual("manifest_digest_mismatch", result["delivery_failure_reason"])

    async def test_acknowledge_and_deliver_refuse_what_the_state_does_not_allow(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        ack = {"admission_id": admission_id, "outcome": "failed", "manifest_digest": None,
               "failure_reason": "delivery_interrupted", "failed_member_index": None}
        # Nothing was delivered yet: there is nothing to acknowledge.
        self.refused(await self.call("acknowledge", ack), "acknowledgement_not_expected")
        self.ok(await self.call("deliver", {"admission_id": admission_id}))
        # A pending attempt is ended by its acknowledgement, not restarted.
        self.transfers._transfers.clear()
        reads = len(self.authority.read_calls)
        self.refused(
            await self.call("deliver", {"admission_id": admission_id}), "delivery_not_available",
        )
        self.assertEqual(0, len(self.transfers._transfers))
        self.assertEqual(reads, len(self.authority.read_calls))
        self.refused(
            await self.call("acknowledge", {**ack, "failed_member_index": 2}),
            "member_index_out_of_range",
        )
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.state(admission_id))
        absent = "admitted-input-v1:" + "b" * 64
        self.refused(await self.call("deliver", {"admission_id": absent}), "admission_not_found")
        self.refused(
            await self.call("acknowledge", {**ack, "admission_id": absent}), "admission_not_found",
        )
        # Expiry is final: a new admission is needed, never a revived locator.
        self.open_store().expire(admission_id)
        self.refused(await self.call("deliver", {"admission_id": admission_id}), "admission_expired")
        self.refused(await self.call("acknowledge", ack), "admission_expired")
        self.refused(await self.call("admit", admit_body()), "admission_expired")

    async def test_malformed_acknowledgements_are_invalid_shape(self) -> None:
        admitted = await self.admit()
        base = {"admission_id": admitted["admission_id"], "outcome": "failed", "manifest_digest": None,
                "failure_reason": "delivery_interrupted", "failed_member_index": None}
        cases = {
            "unknown outcome": {**base, "outcome": "ok"},
            "unknown reason": {**base, "failure_reason": "because"},
            "failed with a digest": {**base, "manifest_digest": admitted["manifest_digest"]},
            "negative index": {**base, "failed_member_index": -1},
            "bool index": {**base, "failed_member_index": True},
            "delivered without digest": {**base, "outcome": "delivered", "failure_reason": None},
            "delivered with a reason": {
                **base, "outcome": "delivered", "manifest_digest": admitted["manifest_digest"]},
            "short digest": {**base, "outcome": "delivered", "failure_reason": None,
                             "manifest_digest": "abc"},
            "extra field": {**base, "note": "x"},
        }
        for label, body in cases.items():
            with self.subTest(label):
                self.refused(await self.call("acknowledge", body), "invalid_request_shape")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admitted["admission_id"]))

    # -- authorization (ADR-0063, ADR-0069 section 2) ----------------------------

    async def test_every_operation_is_denied_before_decoding_without_the_scope(self) -> None:
        bodies = {
            "admit": admit_body(),
            "manifest": {"admission_id": "admitted-input-v1:" + "a" * 64},
            "deliver": {"admission_id": "admitted-input-v1:" + "a" * 64},
            "acknowledge": {"admission_id": "admitted-input-v1:" + "a" * 64, "outcome": "failed",
                            "manifest_digest": None, "failure_reason": "transfer_expired",
                            "failed_member_index": None},
        }
        for scopes in (
            frozenset({READ_JOBS, SUBMIT_JOBS}), frozenset({MARKET}), frozenset({"j02.result.submit"}),
        ):
            for operation, body in bodies.items():
                with self.subTest(operation=operation, scopes=sorted(scopes)):
                    with patch.object(AdmittedInputTransport, "decode") as decode:
                        socket, configuration = await self.exchange(operation, body, scopes=scopes)
                    decode.assert_not_called()
                    self.assertEqual([(1008, "policy denied")], socket.close_calls)
                    self.assertEqual([], socket.responses)
                    self.assertEqual(SCOPE, configuration.evidence_log.events[-1].requested_scope)
        self.assertEqual([], self.authority.resolve_calls)

    async def test_member_frames_need_this_familys_scope_and_no_other_scope_has_them(self) -> None:
        admitted = await self.admit()
        delivery = self.ok(await self.call("deliver", {"admission_id": admitted["admission_id"]}))
        transfer_id = delivery["members"][0]["transfer_id"]
        both = frozenset({SCOPE, READ_JOBS})  # one store shared with j02-job-v1 results
        for scopes in (frozenset({READ_JOBS}), frozenset({SUBMIT_JOBS, MARKET})):
            with self.subTest(scopes=sorted(scopes)):
                socket, _ = await self.resume(transfer_id, scopes=scopes, resume_scopes=both)
                self.assertEqual([(1008, "policy denied")], socket.close_calls)
                self.assertEqual([], socket.responses)  # not one frame
        granted, _ = await self.resume(transfer_id, scopes=frozenset({SCOPE}), resume_scopes=both)
        self.assertEqual([], granted.close_calls)
        self.assertEqual(delivery["members"][0]["chunk_count"], len(granted.responses))

    # -- the server's authority and its faults -----------------------------------

    async def test_authority_faults_never_forward_their_text(self) -> None:
        def explode(*_args):
            raise RuntimeError("/srv/secret/path token=abc")

        class Wrong(Authority):
            def resolve(self, request):
                return "not a resolution"

        class Raises(Authority):
            def resolve(self, request):
                explode()

        cases = (
            ("resolver raises", Raises(), "admission_unavailable"),
            ("resolver answers with something else", Wrong(), "admission_unavailable"),
        )
        for label, authority, reason in cases:
            with self.subTest(label):
                response = await self.call(
                    "admit", admit_body(), handlers=self.routes(authority=authority),
                )
                error = self.refused(response, reason, code="integrity_failure")
                self.assertNotIn("secret", json.dumps(error))
                self.assertNotIn("token", json.dumps(error))
        # A member the authority cannot read, or answers with text, is unavailable.
        admitted = await self.admit()
        for label, reader in (
            ("reader raises", explode),
            ("reader answers text", lambda manifest, index: "bytes?"),
        ):
            with self.subTest(label):
                broken = Authority()
                broken.read_member = reader
                response = await self.call(
                    "deliver", {"admission_id": admitted["admission_id"]},
                    handlers=self.routes(authority=broken),
                )
                error = self.refused(response, "member_unavailable", code="integrity_failure")
                self.assertNotIn("secret", json.dumps(error))
        self.assertEqual(AdmittedInputState.SEALED, self.state(admitted["admission_id"]))

    async def test_a_consumer_api_error_from_the_authority_passes_through_unchanged(self) -> None:
        class Missing(Authority):
            def resolve(self, request):
                raise ConsumerApiError(
                    ConsumerErrorCode.NO_COVERAGE, "no coverage", context={"reason": "no_partition"},
                )

        response = await self.call("admit", admit_body(), handlers=self.routes(authority=Missing()))
        self.assertEqual("error", response["status"])
        self.assertEqual("no_coverage", response["error"]["code"])

    async def test_members_that_are_not_exactly_the_manifests_are_refused_before_sealing(self) -> None:
        base = Authority()

        def declared(builder):
            class Declared(Authority):
                def members(self, manifest):
                    return builder(manifest)
            return Declared()

        member = AdmittedInputMember
        hashes = lambda m: list(m.content_hashes)  # noqa: E731
        cases = {
            "none declared": (declared(lambda m: ()), "manifest_not_deliverable"),
            "a hash the manifest does not hold": (declared(lambda m: (
                member("partition-v1:2024-01-01", "0" * 64, 1),
                member("partition-v1:2024-01-02", hashes(m)[1], 3072),
            )), "members_not_in_manifest"),
            "a hash left out": (declared(lambda m: (
                member("partition-v1:2024-01-01", hashes(m)[0], 21),
            )), "members_not_in_manifest"),
            "a different order": (declared(lambda m: (
                member("partition-v1:2024-01-02", hashes(m)[1], 3072),
                member("partition-v1:2024-01-01", hashes(m)[0], 21),
            )), "members_not_in_manifest"),
            "an identity the manifest does not name": (declared(lambda m: (
                member("partition-v1:2099-01-01", hashes(m)[0], 21),
                member("partition-v1:2024-01-02", hashes(m)[1], 3072),
            )), "member_identity_mismatch"),
            "known identities exchanged between partitions": (declared(lambda m: (
                member("partition-v1:2024-01-02", hashes(m)[0], 21),
                member("partition-v1:2024-01-01", hashes(m)[1], 3072),
            )), "member_identity_mismatch"),
            "an identity of the manifest's other fields": (declared(lambda m: (
                member("dataset-v1:bybit:BTCUSDT", hashes(m)[0], 21),
                member("dataset-v1:bybit:BTCUSDT", hashes(m)[1], 3072),
            )), "member_identity_mismatch"),
            "not members": (declared(lambda m: ("a", "b")), "members_unavailable"),
            "an authority that raises": (declared(lambda m: 1 / 0), "members_unavailable"),
        }
        for label, (authority, reason) in cases.items():
            with self.subTest(label):
                response = await self.call(
                    "admit", admit_body(), handlers=self.routes(authority=authority),
                )
                self.refused(response, reason, code="integrity_failure")
        # Nothing was sealed by any of them, and an honest authority still seals.
        self.assertEqual("SEALED", (await self.admit())["state"])
        self.assertEqual([], base.read_calls)

    async def test_exchanged_partition_identities_are_refused_at_delivery_too(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]

        class Exchanged(Authority):
            def members(self, manifest):
                first, second = super().members(manifest)
                return (
                    AdmittedInputMember(second.identity, first.content_hash, first.byte_length),
                    AdmittedInputMember(first.identity, second.content_hash, second.byte_length),
                )

        response = await self.call(
            "deliver", {"admission_id": admission_id}, handlers=self.routes(authority=Exchanged()),
        )
        self.refused(response, "member_identity_mismatch", code="integrity_failure")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admission_id))
        self.assertEqual(0, len(self.transfers._transfers))
        # What the honest authority delivers is each partition with its own bytes.
        delivery = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        for index, descriptor in enumerate(delivery["members"]):
            document = await self.fetch(descriptor)
            self.assertEqual(f"partition-v1:2024-01-0{index + 1}", document["member_identity"])
            self.assertEqual(
                admitted["manifest"]["natural_partition_identities"][index],
                document["member_identity"],
            )
            self.assertEqual(
                admitted["manifest"]["content_hashes"][index], document["content_hash"],
            )

    async def test_artifacts_are_attributed_only_when_the_manifest_can_verify_it(self) -> None:
        class TwoArtifacts(ArtifactAuthority):
            def manifest(self, request):
                good = super().manifest(request)
                return AdmittedInputManifestV1(
                    logical_input_identities=(
                        self.ARTIFACT, "feature-artifact-v1:sha256:" + "cd" * 32,
                    ),
                    natural_partition_identities=(), schema_identity=None, schema_version=None,
                    schema_hash=None, manifest_hashes=(), content_hashes=good.content_hashes,
                    declared_coverage=None, request_identity="request-v1:two", result_identity=None,
                    definition_identities=(), implementation_identity=None, git_identity=None,
                    operation_identity=good.operation_identity, profile_identity=None,
                )

        class Elsewhere(ArtifactAuthority):
            def members(self, manifest):
                return tuple(
                    AdmittedInputMember(
                        "feature-artifact-v1:sha256:" + "ef" * 32, member.content_hash,
                        member.byte_length,
                    )
                    for member in super().members(manifest)
                )

        # Two logical identities, sorted in the stored payload: no pairing exists
        # to check a member against, so none is accepted on the authority's word.
        response = await self.call(
            "admit", admit_body(dataset=None, artifact_identities=[ArtifactAuthority.ARTIFACT]),
            handlers=self.routes(authority=TwoArtifacts()),
        )
        self.refused(response, "member_attribution_unverifiable", code="integrity_failure")
        # One identity: members must name that identity, not another one.
        response = await self.call(
            "admit", admit_body(dataset=None, artifact_identities=[ArtifactAuthority.ARTIFACT]),
            handlers=self.routes(authority=Elsewhere()),
        )
        self.refused(response, "member_attribution_unverifiable", code="integrity_failure")

    async def test_a_delivery_that_loses_the_race_publishes_nothing(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        store_provider = self.provider()
        authority = self.authority
        original = authority.read_member
        fired = []

        def another_operation_starts_and_expires_it(manifest, index):
            data = original(manifest, index)
            if index == 1 and not fired:
                fired.append(True)
                with store_provider() as store:
                    store.begin_delivery(admission_id)
                    store.expire(admission_id)
            return data

        authority.read_member = another_operation_starts_and_expires_it
        response = await self.call("deliver", {"admission_id": admission_id})
        self.refused(response, "admission_expired")
        self.assertEqual(AdmittedInputState.EXPIRED, self.state(admission_id))
        self.assertEqual({}, dict(self.transfers._transfers))  # nothing was published
        self.assertEqual({}, dict(self.transfers._pending))

    async def test_a_lost_race_leaves_earlier_and_concurrent_transfers_alone(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        authority = self.authority
        # An earlier attempt published these same members and then failed.
        first = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        self.ok(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "failed", "manifest_digest": None,
            "failure_reason": "delivery_interrupted", "failed_member_index": None,
        }))
        earlier = dict(self.transfers._transfers)
        self.assertEqual(2, len(earlier))

        # A second delivery of the same admission starts while this one reads,
        # and wins. The loser must withdraw nothing the winner published.
        inner = AdmittedInputTransport(
            self.provider(), authority.resolve, authority.members, authority.read_member,
            transfers=self.transfers,
        )
        original = authority.read_member
        concurrent = []

        def concurrent_winner(manifest, index):
            data = original(manifest, index)
            if index == 1 and not concurrent:
                concurrent.append(inner.execute("deliver", admission_id))
            return data

        authority.read_member = concurrent_winner
        response = await self.call("deliver", {"admission_id": admission_id})
        self.refused(response, "delivery_not_available")
        (winner,) = concurrent
        self.assertEqual("DELIVERY_PENDING", winner["state"])
        self.assertEqual(first["members"], winner["members"])  # the same transfers
        self.assertEqual(earlier, dict(self.transfers._transfers))  # none added or removed
        for descriptor in winner["members"]:  # and every one still resumable
            document = await self.fetch(descriptor)
            self.assertEqual(descriptor["content_hash"], document["content_hash"])

    async def test_a_reservation_that_expires_while_the_transition_waits_publishes_nothing(self) -> None:
        now = [0.0]
        self.transfers = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=FRAME_BYTES, retention_seconds=10),
            clock=lambda: now[0],
        )
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        begin = AdmittedInputStore.begin_delivery
        waited = []

        def slow_transition(store, admitted_id):
            if not waited:
                waited.append(True)
                now[0] += 60.0  # the store lock was held for longer than retention
            return begin(store, admitted_id)

        with patch.object(AdmittedInputStore, "begin_delivery", slow_transition):
            response = await self.call("deliver", {"admission_id": admission_id})
        # Never "ok" with transfers that are already gone.
        self.refused(response, "transfer_expired", code="integrity_failure")
        self.assertEqual({}, dict(self.transfers._transfers))
        self.assertEqual({}, dict(self.transfers._pending))
        # The server does not end the attempt by itself: it is pending, like one
        # whose response was lost, and the deck ends it with its own failure.
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.state(admission_id))
        self.refused(
            await self.call("deliver", {"admission_id": admission_id}), "delivery_not_available",
        )
        self.ok(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "failed", "manifest_digest": None,
            "failure_reason": "transfer_expired", "failed_member_index": None,
        }))
        # It is then a failed attempt like any other: the sealed admission delivers again.
        delivery = self.ok(await self.call("deliver", {"admission_id": admission_id}))
        self.assertEqual("DELIVERY_PENDING", delivery["state"])
        for descriptor in delivery["members"]:
            document = await self.fetch(descriptor)
            self.assertEqual(descriptor["content_hash"], document["content_hash"])

    async def test_a_late_failure_of_one_attempt_never_ends_the_retry_that_succeeded(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        inner = AdmittedInputTransport(
            self.provider(), self.authority.resolve, self.authority.members,
            self.authority.read_member, transfers=self.transfers,
        )
        begin = AdmittedInputStore.begin_delivery
        retry = []
        started = []

        def retry_succeeds_before_the_first_attempt_publishes(store, admitted_id):
            record = begin(store, admitted_id)
            if not started:
                started.append(True)
                # The deck gives up on this attempt and the retry delivers...
                failure = inner.decode("acknowledge", {
                    "admission_id": admitted_id, "outcome": "failed", "manifest_digest": None,
                    "failure_reason": "delivery_interrupted", "failed_member_index": None,
                })
                inner.execute("acknowledge", failure)
                retry.append(inner.execute("deliver", admitted_id))
                # ...and then this first attempt loses its transfers before it commits.
                self.transfers._transfers.clear()
                self.transfers._pending.clear()
                self.transfers._generations.clear()
            return record

        with patch.object(AdmittedInputStore, "begin_delivery", retry_succeeds_before_the_first_attempt_publishes):
            response = await self.call("deliver", {"admission_id": admission_id})
        # The first attempt fails, and says so...
        self.refused(response, "transfer_expired", code="integrity_failure")
        # ...without touching the retry's state: its own acknowledgement still works.
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.state(admission_id))
        result = self.ok(await self.call("acknowledge", {
            "admission_id": admission_id, "outcome": "delivered",
            "manifest_digest": admitted["manifest_digest"],
            "failure_reason": None, "failed_member_index": None,
        }))
        self.assertEqual("DELIVERED", result["state"])
        self.assertEqual(AdmittedInputState.DELIVERED, self.state(admission_id))

    async def test_a_loser_does_not_withdraw_what_the_winner_published_from_the_same_bytes(self) -> None:
        admitted = await self.admit()
        admission_id = admitted["admission_id"]
        # No earlier attempt: the loser's own reservation is what *created* the
        # transfers the winner then reuses and publishes, between the loser's
        # reservation and its state transition.
        inner = AdmittedInputTransport(
            self.provider(), self.authority.resolve, self.authority.members,
            self.authority.read_member, transfers=self.transfers,
        )
        begin = AdmittedInputStore.begin_delivery
        concurrent = []

        started = []

        def winner_first(store, admitted_id):
            if not started:
                started.append(True)
                concurrent.append(inner.execute("deliver", admitted_id))
            return begin(store, admitted_id)

        with patch.object(AdmittedInputStore, "begin_delivery", winner_first):
            response = await self.call("deliver", {"admission_id": admission_id})
        self.refused(response, "delivery_not_available")
        (winner,) = concurrent
        self.assertEqual(2, len(self.transfers._transfers))
        self.assertEqual({}, dict(self.transfers._pending))  # all committed, none held
        for descriptor in winner["members"]:
            document = await self.fetch(descriptor)
            self.assertEqual(descriptor["content_hash"], document["content_hash"])
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.state(admission_id))

    async def test_a_member_whose_length_differs_from_its_declaration_is_never_delivered(self) -> None:
        admitted = await self.admit()
        self.authority.tampered[0] = self.authority.members_bytes[0] + b"!"
        # Even with a matching hash the declared length is the authority's own
        # statement, and the bytes read are held to it.
        class Declared(Authority):
            def members(self, manifest):
                return aligned_partition_members(manifest, (len(b"alpha-partition-bytes"), 3072))

        declared = Declared()
        declared.tampered = {0: self.authority.tampered[0]}
        response = await self.call(
            "deliver", {"admission_id": admitted["admission_id"]},
            handlers=self.routes(authority=declared),
        )
        self.refused(response, "member_length_mismatch", code="integrity_failure")
        self.assertEqual(AdmittedInputState.SEALED, self.state(admitted["admission_id"]))
        self.assertEqual(0, len(self.transfers._transfers))


if __name__ == "__main__":
    unittest.main()
