"""``j02-result-import-v1``: K13 result submission from the deck over authenticated J02 (#342, P06).

``ResultImportTransportV1Tests`` is backend-neutral: ``build_backend`` composes
the K13 service (SQLite here; tests/integration_runtime_stores_postgres.py
overrides the backend hooks for the PostgreSQL runtime schema).
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import importlib
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
from quant_platform.application.admitted_input import AdmittedInputStore  # noqa: E402
from quant_platform.application.feature_provider_identity import (  # noqa: E402
    REPLAY_FEATURE_PROVIDER_KEY,
    ReplayOutputContractV1,
    feature_provider_identity,
    feature_provider_payload,
    replay_feature_provider_entry,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultBundleV1,
    GovernedResultEvidenceStore,
    GovernedResultImportService,
    ResultOutputContractV1,
)
from quant_platform.application.result_import_transport import (  # noqa: E402
    J02_RESULT_IMPORT_FAMILY,
    J02_RESULT_IMPORT_REQUEST_VERSION,
    J02_RESULT_IMPORT_RESPONSE_VERSION,
    J02_RESULT_IMPORT_SCOPE,
    ResultImportTransport,
    StagedOutputStore,
    StagingBounds,
    bundle_from_wire,
    bundle_to_wire,
    output_identity,
    result_import_family_handlers,
    upload_chunks,
)
from quant_platform.experiments import ExperimentRepository, RunState  # noqa: E402
from test_api_transport_families_v1 import Socket  # noqa: E402
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_governed_result_import_v1 import manifest, output, run  # noqa: E402
from test_remote_j02_security_v1 import security  # noqa: E402

SCOPE = J02_RESULT_IMPORT_SCOPE
FRAME = 1024  # small chunks keep the multi-chunk cases cheap
OPERATIONS = ("upload_output", "submit")
CONTRACTS = (
    ResultOutputContractV1("metrics", "metrics", "metrics-v1"),
    ResultOutputContractV1("extra", "metrics", "metrics-v1"),
    ReplayOutputContractV1("replay_scores", "scores", "scores-v1"),
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def bounds(**overrides) -> StagingBounds:
    values = dict(
        frame_payload_bytes=FRAME, max_output_bytes=8 * FRAME, max_staged_bytes=20 * FRAME,
        max_in_flight_uploads=3, retention_seconds=100,
    )
    values.update(overrides)
    return StagingBounds(**values)


class ResultImportTransportV1Tests(unittest.IsolatedAsyncioTestCase):
    # -- backend hooks -----------------------------------------------------------

    def build_backend(self) -> None:
        # Files, not ``:memory:``: the server runs ``execute`` on a worker thread,
        # and a SQLite connection is not shared across threads, so each request
        # opens its own (as the family composition requires).
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self._admissions_path = Path(directory.name) / "admissions.sqlite"
        self._evidence_path = Path(directory.name) / "evidence.sqlite"
        self.admissions = AdmittedInputStore(self._connect(self._admissions_path))
        self.evidence = GovernedResultEvidenceStore(self._connect(self._evidence_path))
        self.experiments = ExperimentRepository(FakeConnection())

    def _connect(self, path):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        return connection

    def service_session(self, verify=None):
        """A provider of one K13 service per request, verifying with ``verify``."""

        verifier = self.staging.verify_output if verify is None else verify

        @contextmanager
        def session():
            admissions = sqlite3.connect(self._admissions_path)
            evidence = sqlite3.connect(self._evidence_path)
            try:
                yield GovernedResultImportService(
                    AdmittedInputStore(admissions), self.experiments,
                    GovernedResultEvidenceStore(evidence), verifier, CONTRACTS,
                )
            finally:
                admissions.close()
                evidence.close()
        return session

    def bundle_rows(self) -> int:
        return self.evidence._db.execute("SELECT count(*) FROM governed_result_bundles").fetchone()[0]

    def stored_payload(self, bundle_digest: str) -> str:
        return self.evidence._db.execute(
            "SELECT payload FROM governed_result_bundles WHERE bundle_digest = ?", (bundle_digest,),
        ).fetchone()[0]

    # -- fixtures ----------------------------------------------------------------

    def setUp(self) -> None:
        self.now = 0.0
        self.use_staging(bounds())

    def use_staging(self, staging_bounds: StagingBounds) -> None:
        self.staging = StagedOutputStore(staging_bounds, clock=lambda: self.now)
        self._delivered = {}
        self.build_backend()

    def provider(self):
        return self.service_session()

    def routes(self):
        return result_import_family_handlers(self.provider(), staging=self.staging)

    def delivered(self, **overrides):
        """A DELIVERED admission (the same one on every call with the same overrides)."""

        key = tuple(sorted(overrides.items()))
        if key not in self._delivered:
            admitted = self.admissions.admit(manifest(**overrides))
            sealed = self.admissions.seal(admitted.admission_id)
            self.admissions.begin_delivery(sealed.admission_id)
            self.admissions.record_delivery(sealed.admission_id, sealed.manifest.manifest_digest)
            self._delivered[key] = sealed.manifest
        return self._delivered[key]

    def bundle(self, outputs=None, *, identity=None, consumed=None, configuration=None, **overrides):
        admitted = self.delivered() if consumed is None else consumed
        identity = run() if identity is None else identity
        if outputs is None:
            outputs = (output(identity, checksum(b"metrics-bytes")),)
        values = dict(
            admission_id=admitted.admission_id, manifest_digest=admitted.manifest_digest,
            consumed_manifest=admitted, deck_code_identity=admitted.git_identity or "",
            runtime_environment={"python": "3.13"},
            configuration={"strategy": "breakout"} if configuration is None else configuration,
            seeds={"seed": 7}, run_identity=identity, outputs=tuple(outputs),
            metrics_evidence={"sharpe": "1.2"},
        )
        values.update(overrides)
        return GovernedResultBundleV1(**values)

    # -- wire helpers ------------------------------------------------------------

    async def exchange(self, operation, request, *, scopes=frozenset({SCOPE}), handlers=None):
        configuration = security(principal=server.RemoteJ02Principal("deck", frozenset(scopes)))
        configuration.evidence_log._max_events = 64
        socket = Socket([json.dumps({
            "message_family": J02_RESULT_IMPORT_FAMILY,
            "schema_version": J02_RESULT_IMPORT_REQUEST_VERSION,
            "operation": operation, "request_id": "req-1", "request": request,
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
        self.assertEqual(J02_RESULT_IMPORT_FAMILY, response["message_family"])
        self.assertEqual(J02_RESULT_IMPORT_RESPONSE_VERSION, response["schema_version"])
        return response["result"]

    def refused(self, response, reason, code="invalid_request"):
        self.assertEqual("error", response["status"], response)
        self.assertEqual(code, response["error"]["code"])
        self.assertEqual(reason, response["error"]["context"]["reason"])
        return response["error"]

    async def upload(self, data: bytes, **options):
        """What the deck does: every chunk in order. Returns the last progress document."""
        result = None
        for body in upload_chunks(data, frame_payload_bytes=self.staging.bounds.frame_payload_bytes):
            result = self.ok(await self.call("upload_output", body, **options))
        return result

    async def submit(self, bundle, **options):
        return await self.call("submit", bundle_to_wire(bundle), **options)

    def runs(self, identity):
        return self.experiments.list_runs_for_run_spec(identity.run_spec_identity)

    def assertNothingRegistered(self, identity=None) -> None:
        self.assertEqual(0, self.bundle_rows())
        if identity is not None:
            self.assertEqual((), self.runs(identity))

    # -- composition -------------------------------------------------------------

    def test_the_family_declares_exactly_two_operations_under_one_scope(self) -> None:
        routes = self.routes()
        self.assertEqual(
            {(J02_RESULT_IMPORT_FAMILY, J02_RESULT_IMPORT_REQUEST_VERSION, op) for op in OPERATIONS},
            set(routes),
        )
        for route in routes.values():
            self.assertEqual("j02.result.submit", route.required_scope)
            self.assertIsNone(route.additional_scopes)

    def test_composition_is_explicit_and_validated(self) -> None:
        with self.assertRaises(TypeError):
            result_import_family_handlers("provider", staging=self.staging)
        with self.assertRaises(TypeError):
            result_import_family_handlers(self.provider(), staging=object())
        with self.assertRaises(ValueError):
            StagingBounds(max_output_bytes=10, max_staged_bytes=5)
        with self.assertRaises(ValueError):
            StagingBounds(retention_seconds=0)
        with self.assertRaises(TypeError):
            StagedOutputStore(object())

    async def test_a_service_that_does_not_verify_against_the_staging_store_is_refused(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(b"never uploaded")),))
        # A service that would accept anything (not the staged bytes).
        response = await self.submit(bundle, handlers=result_import_family_handlers(
            self.service_session(lambda evidence: evidence.content_checksum), staging=self.staging,
        ))
        self.refused(response, "import_service_not_staged", code="integrity_failure")
        self.assertNothingRegistered(bundle.run_identity)

    # -- upload_output (AC1, AC6) ------------------------------------------------

    async def test_an_upload_is_staged_by_its_checksum_and_never_canonical(self) -> None:
        data = bytes(range(256)) * 10  # three chunks: 1024, 1024, 512
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.assertEqual(3, len(bodies))
        first = self.ok(await self.call("upload_output", bodies[0]))
        self.assertEqual(
            (False, 1, 1, None, None), (
                first["complete"], first["received_chunks"], first["next_chunk_index"],
                first["checksum"], first["byte_length"],
            ),
        )
        self.assertEqual(output_identity(hashlib.sha256(data).hexdigest()), first["logical_result_identity"])
        self.assertEqual(FRAME, first["frame_payload_bytes"])  # the chunk size is the server's
        self.assertEqual((), self.staging.staged_checksums())
        self.ok(await self.call("upload_output", bodies[1]))
        done = self.ok(await self.call("upload_output", bodies[2]))
        self.assertEqual(
            (True, 3, None, checksum(data), len(data)),
            (done["complete"], done["received_chunks"], done["next_chunk_index"],
             done["checksum"], done["byte_length"]),
        )
        self.assertEqual((checksum(data),), self.staging.staged_checksums())
        # Staged is evidence only: no Experiment, no Artifact, no bundle row, and
        # the response never carries bytes back.
        self.assertEqual(0, self.bundle_rows())
        self.assertNotIn("chunk_base64", json.dumps(done))

    async def test_a_corrupted_chunk_is_an_integrity_failure_and_stages_nothing(self) -> None:
        data = bytes(range(256)) * 10
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        flipped = bytearray(base64.b64decode(bodies[1]["chunk_base64"]))
        flipped[0] ^= 0xFF
        cases = {
            "chunk_digest_mismatch": {**bodies[1], "chunk_base64": base64.b64encode(bytes(flipped)).decode()},
            "chunk_not_base64": {**bodies[1], "chunk_base64": "!!not base64!!"},
            "chunk_empty": {**bodies[1], "chunk_base64": ""},
            "transfer_identity_mismatch": {**bodies[1], "transfer_id": "0" * 64},
        }
        for reason, body in cases.items():
            with self.subTest(reason):
                self.refused(await self.call("upload_output", body), reason, code="integrity_failure")
        # Chunk hashes that are right for a payload whose digest is not.
        lying = [{**body, "payload_sha256": "1" * 64} for body in bodies]
        for body in lying:
            body["transfer_id"] = bodies[0]["transfer_id"]
        self.refused(
            await self.call("upload_output", lying[0]), "transfer_identity_mismatch",
            code="integrity_failure",
        )
        self.assertEqual((), self.staging.staged_checksums())
        self.assertEqual(0, self.staging.in_flight())

    async def test_every_integrity_failure_retires_the_partial_transfer(self) -> None:
        data = bytes(range(256)) * 10  # three chunks: 1024, 1024, 512
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        flipped = bytearray(base64.b64decode(bodies[1]["chunk_base64"]))
        flipped[0] ^= 0xFF
        short = base64.b64decode(bodies[1]["chunk_base64"])[:-1]
        cases = {
            "chunk_digest_mismatch": {
                **bodies[1], "chunk_base64": base64.b64encode(bytes(flipped)).decode(),
            },
            "chunk_not_base64": {**bodies[1], "chunk_base64": "!!not base64!!"},
            "chunk_empty": {**bodies[1], "chunk_base64": ""},
            "transfer_identity_mismatch": {**bodies[1], "logical_result_identity": "other"},
            "wrong transfer id (derived one is retired)": {**bodies[1], "transfer_id": "0" * 64},
            "payload digest changed (claimed one is retired)": {
                **bodies[1], "payload_sha256": "1" * 64,
            },
            "chunk_layout_mismatch": {
                **bodies[1], "chunk_base64": base64.b64encode(short).decode(),
                "chunk_sha256": hashlib.sha256(short).hexdigest(),
            },
        }
        for label, bad in cases.items():
            with self.subTest(label):
                self.use_staging(bounds())
                self.ok(await self.call("upload_output", bodies[0]))
                self.assertEqual((1, FRAME), (self.staging.in_flight(), self.staging._held()))
                error = await self.call("upload_output", bad)
                self.assertEqual("integrity_failure", error["error"]["code"], error)
                # Nothing of the failed transfer is held, and it is not resumable...
                self.assertEqual((0, 0), (self.staging.in_flight(), self.staging._held()))
                self.ok(await self.call("upload_output", bodies[1]))
                progress = self.ok(await self.call("upload_output", bodies[2]))
                self.assertFalse(progress["complete"])  # ...chunk 0 has to be sent again
                self.assertEqual(0, progress["next_chunk_index"])
                self.assertEqual((), self.staging.staged_checksums())
                self.ok(await self.call("upload_output", bodies[0]))
                self.assertEqual((checksum(data),), self.staging.staged_checksums())

    async def test_a_conflicting_oversized_claim_retires_the_partial_transfer(self) -> None:
        data = bytes(range(256)) * 10
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", bodies[0]))
        # The same transfer announced with a chunk count the bounds cannot hold.
        claim = {**bodies[1], "chunk_count": 100}
        self.refused(
            await self.call("upload_output", claim), "output_too_large", code="result_too_large",
        )
        self.assertEqual((0, 0), (self.staging.in_flight(), self.staging._held()))

    async def test_a_corrupted_request_never_retires_a_completed_or_another_payload(self) -> None:
        data = bytes(range(256)) * 10
        await self.upload(data)
        other = upload_chunks(b"o" * (2 * FRAME), frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", other[0]))  # an unrelated partial upload
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        flipped = bytearray(base64.b64decode(bodies[1]["chunk_base64"]))
        flipped[0] ^= 0xFF
        for label, bad in (
            ("digest", {**bodies[1], "chunk_base64": base64.b64encode(bytes(flipped)).decode()}),
            ("layout", {**bodies[1], "chunk_count": 2}),
        ):
            with self.subTest(label):
                response = await self.call("upload_output", bad)
                self.assertEqual("integrity_failure", response["error"]["code"])
                self.assertEqual((checksum(data),), self.staging.staged_checksums())
                self.assertEqual(1, self.staging.in_flight())  # the unrelated partial one
        bundle = self.bundle((output(run(), checksum(data)),))
        self.ok(await self.submit(bundle))  # and the completed payload still verifies

    async def test_a_payload_whose_digest_is_wrong_is_discarded_at_completion(self) -> None:
        data = bytes(range(256)) * 6  # 1536 bytes, two chunks
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        # Re-derive a coherent transfer around a digest the bytes do not have.
        wrong = "2" * 64
        from quant_platform.application.result_import_transport import upload_transfer_id
        forged = [
            {**body, "payload_sha256": wrong, "transfer_id": upload_transfer_id(wrong),
             "logical_result_identity": output_identity(wrong)}
            for body in bodies
        ]
        self.ok(await self.call("upload_output", forged[0]))
        self.assertEqual(1, self.staging.in_flight())
        self.refused(
            await self.call("upload_output", forged[1]), "payload_digest_mismatch",
            code="integrity_failure",
        )
        self.assertEqual((), self.staging.staged_checksums())
        self.assertEqual(0, self.staging.in_flight())

    async def test_resume_is_transfer_local_and_chunks_are_idempotent(self) -> None:
        data = bytes(range(256)) * 10
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", bodies[0]))
        gap = self.ok(await self.call("upload_output", bodies[2]))  # out of order
        self.assertEqual((False, 2, 1), (gap["complete"], gap["received_chunks"], gap["next_chunk_index"]))
        again = self.ok(await self.call("upload_output", bodies[0]))  # a lost response, resent
        self.assertEqual((False, 2, 1), (again["complete"], again["received_chunks"], again["next_chunk_index"]))
        done = self.ok(await self.call("upload_output", bodies[1]))
        self.assertTrue(done["complete"])
        # The same payload again is the same staged bytes, not a second copy.
        repeat = self.ok(await self.call("upload_output", bodies[1]))
        self.assertTrue(repeat["complete"])
        self.assertEqual((checksum(data),), self.staging.staged_checksums())

    async def test_a_conflicting_chunk_makes_the_transfer_unusable(self) -> None:
        data = bytes(range(256)) * 10
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", bodies[0]))
        other = bytes(reversed(base64.b64decode(bodies[0]["chunk_base64"])))
        conflicting = {
            **bodies[0], "chunk_base64": base64.b64encode(other).decode(),
            "chunk_sha256": hashlib.sha256(other).hexdigest(),
        }
        self.refused(
            await self.call("upload_output", conflicting), "chunk_conflict", code="integrity_failure",
        )
        self.assertEqual(0, self.staging.in_flight())
        # Complete, then a chunk that differs from the staged bytes.
        await self.upload(data)
        self.refused(
            await self.call("upload_output", conflicting), "chunk_conflict", code="integrity_failure",
        )
        self.assertEqual((checksum(data),), self.staging.staged_checksums())

    async def test_chunk_offsets_are_deterministic(self) -> None:
        data = bytes(range(256)) * 10
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        short = base64.b64decode(bodies[0]["chunk_base64"])[:-1]
        body = {
            **bodies[0], "chunk_base64": base64.b64encode(short).decode(),
            "chunk_sha256": hashlib.sha256(short).hexdigest(),
        }
        error = self.refused(
            await self.call("upload_output", body), "chunk_layout_mismatch", code="integrity_failure",
        )
        self.assertEqual(FRAME, error["context"]["frame_payload_bytes"])
        # An upload in larger chunks than the server's frame bound is refused too.
        bigger = upload_chunks(data, frame_payload_bytes=2 * FRAME)
        self.refused(
            await self.call("upload_output", bigger[0]), "chunk_layout_mismatch",
            code="integrity_failure",
        )
        self.assertEqual(0, self.staging.in_flight())

    async def test_oversized_outputs_are_refused_with_a_stable_reason(self) -> None:
        limit = self.staging.bounds.max_output_bytes
        exact = bytes(range(251)) * (limit // 251) + bytes(limit % 251)
        self.assertEqual(limit, len(exact))
        self.assertTrue((await self.upload(exact))["complete"])
        over = b"x" * (limit + 1)
        first = upload_chunks(over, frame_payload_bytes=FRAME)[0]
        error = self.refused(
            await self.call("upload_output", first), "output_too_large", code="result_too_large",
        )
        self.assertNotIn("x" * 8, json.dumps(error))
        self.assertNotIn(checksum(over), self.staging.staged_checksums())
        self.assertEqual(0, self.staging.in_flight())

    async def test_the_assembled_size_is_bounded_not_only_the_chunk_count(self) -> None:
        # 2.5 frames: three chunks pass the count check (2 * FRAME + 1 <= bound)
        # but the assembled payload, 3 frames, does not.
        self.use_staging(bounds(max_output_bytes=2 * FRAME + FRAME // 2))
        data = b"z" * (3 * FRAME)
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", bodies[0]))
        self.ok(await self.call("upload_output", bodies[1]))
        self.refused(
            await self.call("upload_output", bodies[2]), "output_too_large", code="result_too_large",
        )
        self.assertEqual((), self.staging.staged_checksums())
        self.assertEqual(0, self.staging.in_flight())

    async def test_one_transfer_cannot_change_its_chunk_count(self) -> None:
        data = bytes(range(256)) * 10  # three chunks
        bodies = upload_chunks(data, frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", bodies[0]))
        # The transfer id binds the logical identity and the payload digest, not
        # the count: the same payload announced in two chunks is another layout.
        relabelled = {**bodies[1], "chunk_count": 2}
        self.refused(
            await self.call("upload_output", relabelled), "transfer_metadata_mismatch",
            code="integrity_failure",
        )
        self.assertEqual(0, self.staging.in_flight())

    async def test_staging_is_bounded_in_uploads_and_bytes(self) -> None:
        # In-flight uploads: the 4th distinct open transfer is refused.
        for number in range(3):
            body = upload_chunks(bytes([number]) * (2 * FRAME), frame_payload_bytes=FRAME)[0]
            self.ok(await self.call("upload_output", body))
        fourth = upload_chunks(b"\x09" * (2 * FRAME), frame_payload_bytes=FRAME)[0]
        self.refused(
            await self.call("upload_output", fourth), "too_many_uploads", code="result_too_large",
        )
        # Bytes: a store that holds 5 frames refuses the chunk that would exceed it.
        self.use_staging(bounds(max_output_bytes=4 * FRAME, max_staged_bytes=5 * FRAME))
        self.assertTrue((await self.upload(b"a" * (4 * FRAME)))["complete"])
        two = upload_chunks(b"b" * (2 * FRAME), frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", two[0]))
        self.refused(await self.call("upload_output", two[1]), "staging_full", code="result_too_large")
        self.assertEqual(5 * FRAME, self.staging._held())

    async def test_unreferenced_staged_bytes_and_open_uploads_expire(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        partial = upload_chunks(b"p" * (2 * FRAME), frame_payload_bytes=FRAME)
        self.ok(await self.call("upload_output", partial[0]))
        self.assertEqual((checksum(data),), self.staging.staged_checksums())
        self.assertEqual(1, self.staging.in_flight())
        self.now = 101.0
        self.assertEqual((), self.staging.staged_checksums())
        self.assertEqual(0, self.staging.in_flight())
        # The bundle that needs the expired bytes is refused, with nothing written.
        bundle = self.bundle((output(run(), checksum(data)),))
        self.refused(await self.submit(bundle), "output_not_staged")
        self.assertNothingRegistered(bundle.run_identity)

    # -- submit (AC2, AC3) ---------------------------------------------------------

    async def test_a_staged_output_is_registered_by_one_submit_and_resubmission_is_the_same(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),))
        self.assertEqual(0, self.bundle_rows())
        first = self.ok(await self.submit(bundle))
        self.assertEqual(bundle.bundle_digest, first["bundle_digest"])
        self.assertEqual(
            {"run_spec_fingerprint": bundle.run_identity.run_spec_identity.fingerprint,
             "execution_id": "deck-run-1", "state": "SUCCEEDED", "failure_details": None},
            first["run"],
        )
        self.assertEqual(
            [{"artifact_role": "metrics", "content_checksum": checksum(data)}], first["outputs"],
        )
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual(RunState.SUCCEEDED, self.runs(bundle.run_identity)[0].state)
        again = self.ok(await self.submit(bundle))
        self.assertEqual(first, again)
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual(1, len(self.runs(bundle.run_identity)))

    async def test_registration_equals_the_in_process_k13_registration(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),))
        wire = self.ok(await self.submit(bundle))
        # The same bundle through the in-process service on an independent backend.
        twin_admissions = AdmittedInputStore(self._connect(":memory:"))
        admitted = twin_admissions.admit(bundle.consumed_manifest)
        twin_admissions.seal(admitted.admission_id)
        twin_admissions.begin_delivery(admitted.admission_id)
        twin_admissions.record_delivery(admitted.admission_id, admitted.manifest.manifest_digest)
        twin = GovernedResultImportService(
            twin_admissions, ExperimentRepository(FakeConnection()),
            GovernedResultEvidenceStore(self._connect(":memory:")),
            lambda evidence: evidence.content_checksum, CONTRACTS,
        )
        direct = twin.import_result(bundle)
        self.assertEqual(direct.bundle_digest, wire["bundle_digest"])
        self.assertEqual(direct.run_record.run_spec_fingerprint, wire["run"]["run_spec_fingerprint"])
        self.assertEqual(direct.run_record.state.value, wire["run"]["state"])

    async def test_a_checksum_with_no_staged_bytes_refuses_the_whole_bundle(self) -> None:
        staged = b"staged-bytes"
        await self.upload(staged)
        identity = run()
        for label, outputs in (
            ("nothing staged", (output(identity, checksum(b"never uploaded")),)),
            ("one of two", (
                output(identity, checksum(staged), role="metrics"),
                output(identity, checksum(b"never uploaded"), role="extra", kind="metrics", schema="metrics-v1"),
            )),
        ):
            with self.subTest(label):
                bundle = self.bundle(outputs, identity=identity)
                self.refused(await self.submit(bundle), "output_not_staged")
                self.assertNothingRegistered(identity)
        # A payload that is staged under a different checksum is not that checksum's bytes.
        wrong = output(identity, checksum(b"other bytes"))
        self.refused(await self.submit(self.bundle((wrong,), identity=identity)), "output_not_staged")
        self.assertNothingRegistered(identity)

    async def test_staged_bytes_are_hashed_again_not_trusted_by_their_key(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        # A staged entry whose bytes no longer hash to its key (memory corruption).
        self.staging._staged[checksum(data)].data = b"corrupted-bytes"
        bundle = self.bundle((output(run(), checksum(data)),))
        self.refused(await self.submit(bundle), "output_checksum_mismatch")
        self.assertNothingRegistered(bundle.run_identity)

    async def test_an_accepted_bundle_is_resubmitted_without_staged_bytes(self) -> None:
        data = b"a" * 2500  # several chunks
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),))
        first = self.ok(await self.submit(bundle))
        # (1) the staged bytes expired; the registration and the admission did not.
        self.now = 1001.0
        self.assertEqual((), self.staging.staged_checksums())
        self.assertEqual(first, self.ok(await self.submit(bundle)))
        # (2) a restart: a new, empty staging store on the same durable state.
        self.staging = StagedOutputStore(bounds(), clock=lambda: self.now)
        self.assertEqual(first, self.ok(await self.submit(bundle)))
        self.assertEqual((), self.staging.staged_checksums())  # nothing was staged to do it
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual(1, len(self.runs(bundle.run_identity)))
        # The relaxation is exactly the recorded bundle's checksums, for that call:
        # a bundle that is not the recorded one needs staged bytes again.
        other_run = run("deck-run-2")
        for label, candidate in (
            ("a new run with the same bytes", self.bundle(
                (output(other_run, checksum(data)),), identity=other_run,
                consumed=bundle.consumed_manifest,
            )),
            ("the same run, other evidence", self.bundle(
                (output(bundle.run_identity, checksum(data)),), identity=bundle.run_identity,
                consumed=bundle.consumed_manifest, metrics_evidence={"sharpe": "9.9"},
            )),
        ):
            with self.subTest(label):
                self.refused(await self.submit(candidate), "output_not_staged")
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual((), self.staging.staged_checksums())

    async def test_an_accepted_bundle_still_goes_through_the_owners_checks(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),))
        first = self.ok(await self.submit(bundle))
        self.now = 1001.0  # the staged bytes are gone
        with patch.object(
            GovernedResultImportService, "_validate_admission",
            autospec=True, side_effect=GovernedResultImportService._validate_admission,
        ) as admission, patch.object(
            GovernedResultEvidenceStore, "assert_admissible",
            autospec=True, side_effect=GovernedResultEvidenceStore.assert_admissible,
        ) as conflicts:
            self.assertEqual(first, self.ok(await self.submit(bundle)))
        # Recovery is the owner's idempotent registration, not a shortcut around it.
        self.assertEqual(1, admission.call_count)
        self.assertEqual(1, conflicts.call_count)

    async def test_recording_is_compared_by_the_whole_payload(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),))
        self.assertFalse(self.evidence.is_recorded(bundle))
        self.ok(await self.submit(bundle))
        self.assertTrue(self.evidence.is_recorded(bundle))
        # Same digest, another stored payload: a conflict, never a match.
        from quant_platform.application.governed_result_import import GovernedResultRefused
        different = self.bundle(
            (output(run(), checksum(data)),), metrics_evidence={"sharpe": "9.9"},
        )
        self.assertNotEqual(bundle.canonical_payload_v1, different.canonical_payload_v1)
        recorded_digest = bundle.bundle_digest
        with patch.object(
            GovernedResultBundleV1, "bundle_digest", new=property(lambda self: recorded_digest),
        ):
            with self.assertRaises(GovernedResultRefused):
                self.evidence.is_recorded(different)

    async def test_every_k13_refusal_has_a_stable_reason_and_changes_nothing(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        good = self.delivered()
        identity = run()
        sealed_only = self.admissions.seal(
            self.admissions.admit(manifest(request="request-v1:sealed")).admission_id,
        ).manifest
        other_manifest = manifest(request="request-v1:other")

        def make(run_identity=identity, **overrides):
            return self.bundle(
                (output(run_identity, checksum(data)),), identity=run_identity, consumed=good, **overrides,
            )

        cases = {
            "admission_not_found": make(admission_id="admitted-input-v1:missing"),
            "admission_not_delivered": self.bundle(
                (output(identity, checksum(data)),), identity=identity, consumed=sealed_only,
            ),
            "manifest_digest_mismatch": make(manifest_digest="0" * 64),
            "consumed_manifest_mismatch": make(consumed_manifest=other_manifest),
            "code_identity_mismatch": make(deck_code_identity="git:other"),
            "run_code_identity_mismatch": make(run_identity=run(code_identity="git:other")),
            "run_data_identity_mismatch": make(run_identity=run(data_identity="dataset-v1:OTHER")),
            "run_definition_mismatch": make(run_identity=run(definition_identity="definition-v1:other")),
            "output_not_admitted": self.bundle(
                (output(identity, checksum(data), role="unknown-role"),), identity=identity, consumed=good,
            ),
        }
        for reason, bundle in cases.items():
            with self.subTest(reason):
                response = await self.submit(bundle)
                error = self.refused(response, reason)
                self.assertEqual("the request is not valid", error["message"])
                self.assertNothingRegistered(bundle.run_identity)

    async def test_a_conflicting_bundle_for_the_same_run_and_role_is_refused(self) -> None:
        first_data, second_data = b"metrics-bytes", b"different-bytes"
        await self.upload(first_data)
        await self.upload(second_data)
        identity = run()
        admitted = self.delivered()
        self.ok(await self.submit(self.bundle(
            (output(identity, checksum(first_data)),), identity=identity, consumed=admitted,
        )))
        conflicting = self.bundle(
            (output(identity, checksum(second_data)),), identity=identity, consumed=admitted,
        )
        self.refused(await self.submit(conflicting), "conflicting_bundle")
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual(1, len(self.runs(identity)))

    async def test_a_new_computation_with_a_distinct_run_registers_separately(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        admitted = self.delivered()
        for execution in ("deck-run-1", "deck-run-2"):
            identity = run(execution)
            self.ok(await self.submit(self.bundle(
                (output(identity, checksum(data)),), identity=identity, consumed=admitted,
            )))
        self.assertEqual(2, self.bundle_rows())

    # -- the wire document -------------------------------------------------------

    def test_the_bundle_document_is_lossless(self) -> None:
        identity = run()
        bundle = self.bundle((
            output(identity, checksum(b"a"), role="metrics"),
            output(identity, checksum(b"b"), role="replay_scores", kind="scores", schema="scores-v1"),
        ), identity=identity)
        document = bundle_to_wire(bundle)
        restored = bundle_from_wire(json.loads(json.dumps(document)))
        self.assertEqual(bundle, restored)
        self.assertEqual(bundle.bundle_digest, restored.bundle_digest)
        self.assertEqual(bundle.canonical_payload_v1, restored.canonical_payload_v1)

    async def test_malformed_submissions_are_invalid_request_before_any_owner_call(self) -> None:
        identity = run()
        document = bundle_to_wire(self.bundle((output(identity, checksum(b"a")),), identity=identity))
        broken = {
            "unknown field": {**document, "path": "/srv/results/out.bin"},
            "a missing field": {key: value for key, value in document.items() if key != "seeds"},
            "wire version": {**document, "version": "governed-result-bundle-wire-v2"},
            "no outputs": {**document, "outputs": []},
            "bare checksum": {**document, "outputs": [{**document["outputs"][0], "content_checksum": "ab" * 32}]},
            "output extra field": {**document, "outputs": [{**document["outputs"][0], "bytes": "AA=="}]},
            "duplicate roles": {**document, "outputs": [document["outputs"][0], document["outputs"][0]]},
            "manifest not sealed": {
                **document, "consumed_manifest": {**document["consumed_manifest"], "state": "DELIVERED"},
            },
        }
        with patch.object(GovernedResultImportService, "import_result") as owner:
            for label, body in broken.items():
                with self.subTest(label):
                    self.refused(await self.call("submit", body), "invalid_request_shape")
            tampered = json.loads(json.dumps(document))
            tampered["run_identity"]["run_spec_fingerprint"] = "0" * 64
            self.refused(await self.call("submit", tampered), "run_identity_mismatch")
            # A request that is not an object never reaches the family's decoder.
            response = await self.call("submit", [1, 2])
            self.assertEqual("invalid_request", response["error"]["code"])
        owner.assert_not_called()
        self.assertNothingRegistered(identity)

    async def test_an_upload_request_names_no_locator_and_no_extra_field(self) -> None:
        body = upload_chunks(b"abc", frame_payload_bytes=FRAME)[0]
        for extra in ({"path": "/srv/out.bin"}, {"artifact_role": "metrics"}, {"locator": {"uri": "x"}}):
            with self.subTest(extra=sorted(extra)):
                self.refused(await self.call("upload_output", {**body, **extra}), "invalid_request_shape")
        for name in ("transfer_id", "chunk_base64"):
            with self.subTest(missing=name):
                partial = {key: value for key, value in body.items() if key != name}
                self.refused(await self.call("upload_output", partial), "invalid_request_shape")
        self.assertEqual((), self.staging.staged_checksums())

    # -- replay outputs: feature-provider identity (AC4, AC5) -----------------------

    def provider_payload(self, **overrides):
        values = dict(
            repository="NeoNix-Lab/quant-platform", commit=COMMIT,
            qualified_name="deck_features_probe_342:build", source_sha256="ab" * 32,
            parameters={"window": 20}, tree_is_clean=True,
        )
        values.update(overrides)
        return feature_provider_payload(**values)

    async def replay_bundle(self, configuration):
        data = b"replay-score-bytes"
        await self.upload(data)
        identity = run()
        return self.bundle(
            (output(identity, checksum(data), role="replay_scores", kind="scores", schema="scores-v1"),),
            identity=identity, configuration=configuration,
        )

    async def test_a_replay_output_without_the_provider_identity_is_refused(self) -> None:
        for label, configuration, reason in (
            ("absent", {"strategy": "breakout"}, "replay_provider_missing"),
            ("malformed", {REPLAY_FEATURE_PROVIDER_KEY: {"identity": "x"}}, "replay_provider_malformed"),
            ("identity of another payload", {REPLAY_FEATURE_PROVIDER_KEY: {
                "identity": feature_provider_identity(self.provider_payload(parameters={"window": 1})),
                "payload": self.provider_payload(),
            }}, "replay_provider_identity_mismatch"),
            ("payload not admissible", {REPLAY_FEATURE_PROVIDER_KEY: {
                "identity": "feature-provider-v1:sha256:" + "0" * 64,
                "payload": {**self.provider_payload(), "repository": "https://u:t@host/r"},
            }}, "replay_payload_repository"),
        ):
            with self.subTest(label):
                bundle = await self.replay_bundle(configuration)
                self.refused(await self.submit(bundle), reason)
                self.assertNothingRegistered(bundle.run_identity)

    async def test_a_replay_output_with_the_provider_identity_stores_the_recomputed_identity(self) -> None:
        payload = self.provider_payload()
        entry = replay_feature_provider_entry(payload)
        bundle = await self.replay_bundle({REPLAY_FEATURE_PROVIDER_KEY: entry})
        result = self.ok(await self.submit(bundle))
        stored = json.loads(self.stored_payload(result["bundle_digest"]))
        provider = stored["configuration"][REPLAY_FEATURE_PROVIDER_KEY]
        recomputed = "feature-provider-v1:sha256:" + hashlib.sha256(json.dumps(
            provider["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        self.assertEqual(recomputed, provider["identity"])
        self.assertEqual(feature_provider_identity(payload), provider["identity"])
        self.assertEqual(payload, provider["payload"])

    async def test_a_non_replay_role_needs_no_provider(self) -> None:
        data = b"metrics-bytes"
        await self.upload(data)
        bundle = self.bundle((output(run(), checksum(data)),), configuration={"strategy": "x"})
        self.ok(await self.submit(bundle))

    async def test_the_server_never_imports_the_providers_qualified_name(self) -> None:
        module = "deck_features_probe_342"
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "imported.marker"
            (Path(directory) / f"{module}.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
                "def build():\n    raise SystemExit('executed')\n",
                encoding="utf-8",
            )
            sys.path.insert(0, directory)
            self.addCleanup(lambda: sys.path.remove(directory))
            self.addCleanup(lambda: sys.modules.pop(module, None))
            real_import = importlib.import_module
            requested: list[str] = []

            def spy(name, package=None):
                requested.append(name)
                return real_import(name, package)

            entry = replay_feature_provider_entry(self.provider_payload())
            bundle = await self.replay_bundle({REPLAY_FEATURE_PROVIDER_KEY: entry})
            with patch("importlib.import_module", spy):
                self.ok(await self.submit(bundle))
                self.refused(
                    await self.submit(await self.replay_bundle({})), "replay_provider_missing",
                )
            self.assertNotIn(module, sys.modules)
            self.assertFalse(marker.exists())
            self.assertFalse(any(name.split(".")[0] == module for name in requested))
        # Neither the family nor the identity module even *has* an import path for it.
        source = "\n".join(
            (ROOT / "src/quant_platform/application" / name).read_text(encoding="utf-8")
            for name in ("result_import_transport.py", "feature_provider_identity.py")
        )
        for forbidden in ("import_module(", "__import__(", "runpy", "exec(", "eval("):
            self.assertNotIn(forbidden, source)

    # -- authorization (ADR-0069 section 2) ----------------------------------------

    async def test_the_family_needs_its_own_scope_before_anything_is_decoded(self) -> None:
        bodies = {
            "upload_output": upload_chunks(b"abc", frame_payload_bytes=FRAME)[0],
            "submit": bundle_to_wire(self.bundle()),
        }
        for scopes in (
            frozenset({"j02.admitted_input.read"}), frozenset({"j02.jobs.read", "j02.jobs.submit"}),
            frozenset({"j02.market_data.read"}), frozenset({"j02.training.register"}),
        ):
            for operation, body in bodies.items():
                with self.subTest(operation=operation, scopes=sorted(scopes)):
                    with patch.object(ResultImportTransport, "decode") as decode:
                        socket, configuration = await self.exchange(operation, body, scopes=scopes)
                    decode.assert_not_called()
                    self.assertEqual([(1008, "policy denied")], socket.close_calls)
                    self.assertEqual([], socket.responses)
                    self.assertEqual(SCOPE, configuration.evidence_log.events[-1].requested_scope)
        self.assertEqual((), self.staging.staged_checksums())

    async def test_the_scope_grants_only_this_family(self) -> None:
        # Holding j02.result.submit is not holding any other family's scope.
        socket, _ = await self.exchange(
            "admit", {}, scopes=frozenset({SCOPE}),
            handlers=self.routes(),
        )
        self.assertEqual([], socket.close_calls)  # the family exists for this scope...
        self.assertEqual("error", socket.responses[0]["status"])  # ...and "admit" is not an operation of it


if __name__ == "__main__":
    unittest.main()
