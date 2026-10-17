"""K13 composition: the output-role registry and the import-service factory (issue #392).

``K13CompositionV1Tests`` is backend-neutral: ``build_backend`` provides the
admission store, the Experiment repository and the service provider (SQLite and
``FakeConnection`` here; tests/integration_runtime_stores_postgres.py overrides
the hooks to compose the service with the factory under test over the runtime
schema as the API login). Everything goes through ``ResultImportTransport``, the
family's own decode/execute, with the registry's contracts.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from psycopg import Connection  # noqa: E402

from quant_platform.application.admitted_input import AdmittedInputStore  # noqa: E402
from quant_platform.application.feature_provider_identity import (  # noqa: E402
    REPLAY_FEATURE_PROVIDER_KEY,
    ReplayOutputContractV1,
    feature_provider_payload,
    replay_feature_provider_entry,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultBundleV1,
    GovernedResultEvidenceStore,
    GovernedResultImportService,
    ResultOutputContractV1,
)
from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.result_import_composition import (  # noqa: E402
    K13_OUTPUT_ROLES_V1,
    K13_STAGING_BOUNDS_V1,
    governed_result_import_service,
    governed_result_import_service_provider,
)
from quant_platform.application.result_import_transport import (  # noqa: E402
    ResultImportTransport,
    StagedOutputStore,
    bundle_to_wire,
    upload_chunks,
)
from quant_platform.experiments import ExperimentRepository, RunState  # noqa: E402
from quant_platform.learning import training  # noqa: E402
from quant_platform.replay import REPLAY_RESULT_IDENTITY_DOMAIN  # noqa: E402
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_governed_result_import_v1 import manifest, output, run  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
MIB = 1024 * 1024
EXPECTED_ROLES = {
    # role: (kind, schema, contract class)
    "normalizer": ("supervised-normalizer", "supervised-normalizer-v1", ResultOutputContractV1),
    "model": ("supervised-model", "supervised-model-v1", ResultOutputContractV1),
    "predictions": ("supervised-predictions", "supervised-predictions-v1", ResultOutputContractV1),
    "metrics": ("supervised-metrics", "supervised-metrics-v1", ResultOutputContractV1),
    "replay-result": ("replay-result", "replay-result-v1", ReplayOutputContractV1),
    "replay-summary": ("replay-summary", "replay-summary-v1", ReplayOutputContractV1),
}


def checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def provider_entry() -> dict:
    return replay_feature_provider_entry(feature_provider_payload(
        repository="NeoNix-Lab/quant-platform", commit=COMMIT,
        qualified_name="deck_features_probe_392:build", source_sha256="ab" * 32,
        parameters={"window": 20}, tree_is_clean=True,
    ))


class K13CompositionV1Tests(unittest.TestCase):
    # -- backend hooks -----------------------------------------------------------

    def build_backend(self) -> None:
        self.admissions = AdmittedInputStore(sqlite3.connect(":memory:"))
        self.evidence = GovernedResultEvidenceStore(sqlite3.connect(":memory:"))
        self.experiments = ExperimentRepository(FakeConnection())

        @contextmanager
        def session():
            yield GovernedResultImportService(
                self.admissions, self.experiments, self.evidence,
                self.staging.verify_output, K13_OUTPUT_ROLES_V1,
            )
        self.service_provider = session

    def bundle_rows(self) -> int:
        return self.evidence._db.execute("SELECT count(*) FROM governed_result_bundles").fetchone()[0]

    def stored_payload(self, bundle_digest: str) -> str:
        return self.evidence._db.execute(
            "SELECT payload FROM governed_result_bundles WHERE bundle_digest = ?", (bundle_digest,),
        ).fetchone()[0]

    # -- helpers -----------------------------------------------------------------

    def setUp(self) -> None:
        self.staging = StagedOutputStore(K13_STAGING_BOUNDS_V1)
        self.build_backend()
        self.transport = ResultImportTransport(self.service_provider, self.staging)
        self._delivered = None

    def delivered(self):
        if self._delivered is None:
            admitted = self.admissions.admit(manifest())
            sealed = self.admissions.seal(admitted.admission_id)
            self.admissions.record_delivery(
                sealed.admission_id, sealed.manifest.manifest_digest,
                delivery_attempt=self.admissions.begin_delivery(sealed.admission_id).delivery_attempt,
            )
            self._delivered = sealed.manifest
        return self._delivered

    def bundle(self, outputs, identity, *, configuration=None) -> GovernedResultBundleV1:
        admitted = self.delivered()
        return GovernedResultBundleV1(
            admission_id=admitted.admission_id, manifest_digest=admitted.manifest_digest,
            consumed_manifest=admitted, deck_code_identity=admitted.git_identity or "",
            runtime_environment={"python": "3.13"},
            configuration={"strategy": "breakout"} if configuration is None else configuration,
            seeds={"seed": 7}, run_identity=identity, outputs=tuple(outputs),
            metrics_evidence={"sharpe": "1.2"},
        )

    def stage(self, data: bytes) -> None:
        frame = self.staging.bounds.frame_payload_bytes
        for body in upload_chunks(data, frame_payload_bytes=frame):
            self.transport.execute("upload_output", self.transport.decode("upload_output", body))

    def submit(self, bundle: GovernedResultBundleV1) -> dict:
        return self.transport.execute("submit", self.transport.decode("submit", bundle_to_wire(bundle)))

    def refusal(self, bundle, reason, code=ConsumerErrorCode.INVALID_REQUEST):
        with self.assertRaises(ConsumerApiError) as raised:
            self.submit(bundle)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(reason, raised.exception.context["reason"])

    def runs(self, identity):
        # Executions of one run spec are listed together; this execution's records only.
        return tuple(
            record for record in self.experiments.list_runs_for_run_spec(identity.run_spec_identity)
            if record.execution_id == identity.execution_id
        )

    def role_bundle(self, role, execution_id, data=None, *, kind=None, schema=None):
        expected_kind, expected_schema, _ = EXPECTED_ROLES[role]
        data = data if data is not None else f"{role}-bytes-{execution_id}".encode()
        identity = run(execution_id)
        evidence = output(
            identity, checksum(data), role=role, kind=kind or expected_kind, schema=schema or expected_schema,
        )
        configuration = (
            {REPLAY_FEATURE_PROVIDER_KEY: provider_entry()} if role.startswith("replay-") else None
        )
        return data, self.bundle((evidence,), identity, configuration=configuration)

    # -- the registry (ADR-0072 section 2) -----------------------------------------

    def test_the_registry_is_exactly_the_six_roles_of_the_adr(self) -> None:
        self.assertEqual(set(EXPECTED_ROLES), {contract.artifact_role for contract in K13_OUTPUT_ROLES_V1})
        self.assertEqual(6, len(K13_OUTPUT_ROLES_V1))
        for contract in K13_OUTPUT_ROLES_V1:
            kind, schema, cls = EXPECTED_ROLES[contract.artifact_role]
            self.assertEqual((kind, schema), (contract.artifact_kind, contract.artifact_schema_version))
            self.assertIs(cls, type(contract))

    def test_the_registry_reuses_the_existing_identity_domains(self) -> None:
        by_role = {contract.artifact_role: contract for contract in K13_OUTPUT_ROLES_V1}
        self.assertEqual(training.SUPERVISED_NORMALIZER_ARTIFACT_SCHEMA, by_role["normalizer"].artifact_schema_version)
        self.assertEqual(training.SUPERVISED_MODEL_ARTIFACT_SCHEMA, by_role["model"].artifact_schema_version)
        self.assertEqual(training.SUPERVISED_PREDICTION_ARTIFACT_SCHEMA, by_role["predictions"].artifact_schema_version)
        self.assertEqual(training.SUPERVISED_METRIC_ARTIFACT_SCHEMA, by_role["metrics"].artifact_schema_version)
        self.assertEqual(REPLAY_RESULT_IDENTITY_DOMAIN, by_role["replay-result"].artifact_schema_version)

    def test_the_staging_bounds_are_the_adr_values(self) -> None:
        bounds = K13_STAGING_BOUNDS_V1
        self.assertEqual(
            (64 * 1024, 8 * MIB, 32 * MIB, 8, 900),
            (bounds.frame_payload_bytes, bounds.max_output_bytes, bounds.max_staged_bytes,
             bounds.max_in_flight_uploads, bounds.retention_seconds),
        )

    def test_every_registered_role_registers_once_staged(self) -> None:
        for number, role in enumerate(sorted(EXPECTED_ROLES)):
            with self.subTest(role):
                data, bundle = self.role_bundle(role, f"deck-run-{number}")
                self.stage(data)
                result = self.submit(bundle)
                self.assertEqual(
                    [{"artifact_role": role, "content_checksum": checksum(data)}], result["outputs"],
                )
                (record,) = self.runs(bundle.run_identity)
                self.assertEqual(RunState.SUCCEEDED, record.state)
        self.assertEqual(6, self.bundle_rows())

    def test_a_role_outside_the_registry_is_refused_with_no_mutation(self) -> None:
        for number, role in enumerate(("extra", "Metrics", "replay_result", "model-2", "replay-result ".upper())):
            with self.subTest(role):
                data = f"bytes-{role}".encode()
                identity = run(f"deck-run-{number}")
                evidence = output(identity, checksum(data), role=role, kind="supervised-metrics",
                                  schema="supervised-metrics-v1")
                self.stage(data)
                self.refusal(self.bundle((evidence,), identity), "output_not_admitted")
                self.assertEqual((), self.runs(identity))
        self.assertEqual(0, self.bundle_rows())

    def test_a_registered_role_with_another_kind_or_schema_is_refused(self) -> None:
        for number, (role, kind, schema) in enumerate((
            ("metrics", "supervised-model", None),
            ("metrics", None, "supervised-metrics-v2"),
            ("model", "supervised-metrics", "supervised-model-v1"),
            ("replay-summary", "replay-result", None),
            ("replay-result", None, "replay-summary-v1"),
        )):
            with self.subTest((role, kind, schema)):
                data, bundle = self.role_bundle(role, f"deck-run-{number}", kind=kind, schema=schema)
                self.stage(data)
                self.refusal(bundle, "output_not_admitted")
                self.assertEqual((), self.runs(bundle.run_identity))
        self.assertEqual(0, self.bundle_rows())

    # -- the content identity is the checksum of the uploaded bytes ----------------

    def test_the_content_identity_is_the_checksum_of_the_staged_bytes(self) -> None:
        data, bundle = self.role_bundle("metrics", "deck-run-1")
        self.stage(data)
        result = self.submit(bundle)
        stored = json.loads(self.stored_payload(result["bundle_digest"]))
        (entry,) = stored["outputs"]
        self.assertEqual(checksum(data), entry["content_checksum"])
        self.assertEqual(
            checksum(data), bundle.outputs[0].registration.identity.artifact_content_identity.content_identity,
        )

    def test_an_output_whose_bytes_were_not_staged_or_differ_is_refused_with_no_mutation(self) -> None:
        data, bundle = self.role_bundle("metrics", "deck-run-1")
        self.refusal(bundle, "output_not_staged")  # nothing uploaded
        self.stage(b"other bytes")
        self.refusal(bundle, "output_not_staged")  # other bytes are staged under their own checksum
        self.assertEqual((), self.runs(bundle.run_identity))
        self.assertEqual(0, self.bundle_rows())

    def test_a_registration_whose_content_identity_differs_from_the_checksum_cannot_be_expressed(self) -> None:
        identity = run("deck-run-1")
        genuine = output(identity, checksum(b"a"), role="metrics", kind="supervised-metrics",
                         schema="supervised-metrics-v1")
        from quant_platform.application.governed_result_import import ResultOutputEvidenceV1

        with self.assertRaises(ValueError):
            ResultOutputEvidenceV1(genuine.registration, checksum(b"b"))

    def test_an_identical_resubmission_returns_the_same_registration(self) -> None:
        data, bundle = self.role_bundle("predictions", "deck-run-1")
        self.stage(data)
        first = self.submit(bundle)
        again = self.submit(bundle)
        self.assertEqual(first, again)
        self.assertEqual(1, self.bundle_rows())
        self.assertEqual(1, len(self.runs(bundle.run_identity)))

    # -- replay roles --------------------------------------------------------------

    def test_replay_roles_require_the_feature_provider_identity(self) -> None:
        for number, role in enumerate(("replay-result", "replay-summary")):
            with self.subTest(role):
                data = f"{role}-bytes".encode()
                identity = run(f"deck-run-{number}")
                evidence = output(identity, checksum(data), role=role, kind=role, schema=f"{role}-v1")
                self.stage(data)
                self.refusal(self.bundle((evidence,), identity), "replay_provider_missing")
                self.assertEqual((), self.runs(identity))
        self.assertEqual(0, self.bundle_rows())

    def test_supervised_roles_need_no_provider(self) -> None:
        data, bundle = self.role_bundle("normalizer", "deck-run-1")
        self.assertNotIn(REPLAY_FEATURE_PROVIDER_KEY, bundle.configuration)
        self.stage(data)
        self.submit(bundle)

    # -- bounds ----------------------------------------------------------------------

    def test_an_output_above_the_bound_is_not_registrable(self) -> None:
        big = b"\x01" * (8 * MIB + 1)
        body = upload_chunks(big, frame_payload_bytes=K13_STAGING_BOUNDS_V1.frame_payload_bytes)[0]
        with self.assertRaises(ConsumerApiError) as raised:
            self.transport.execute("upload_output", self.transport.decode("upload_output", body))
        self.assertEqual(ConsumerErrorCode.RESULT_TOO_LARGE, raised.exception.code)
        self.assertEqual((), self.staging.staged_checksums())

    # -- the factory -----------------------------------------------------------------

    def test_the_factory_builds_every_store_on_the_one_connection_it_is_given(self) -> None:
        connection = MagicMock(spec=Connection)
        connection.autocommit = True
        service = governed_result_import_service(connection, self.staging)
        self.assertIs(connection, service.admitted_inputs.connection)
        self.assertIs(connection, service.experiments.connection)
        self.assertIs(connection, service.evidence_store.connection)
        self.assertEqual(self.staging.verify_output, service.verify_output_checksum)
        self.assertEqual({contract.artifact_role for contract in K13_OUTPUT_ROLES_V1}, set(service.output_contracts))

    def test_the_factory_refuses_anything_but_an_autocommit_psycopg_connection(self) -> None:
        with self.assertRaises(TypeError):
            governed_result_import_service(sqlite3.connect(":memory:"), self.staging)
        manual = MagicMock(spec=Connection)
        manual.autocommit = False
        with self.assertRaises(ValueError):
            governed_result_import_service(manual, self.staging)
        auto = MagicMock(spec=Connection)
        auto.autocommit = True
        with self.assertRaises(TypeError):
            governed_result_import_service(auto, object())

    def test_the_provider_opens_one_connection_per_request_and_closes_it(self) -> None:
        opened = []

        @contextmanager
        def connections():
            connection = MagicMock(spec=Connection)
            connection.autocommit = True
            opened.append(connection)
            try:
                yield connection
            finally:
                connection.close()

        provider = governed_result_import_service_provider(connections, self.staging)
        for _ in range(2):
            with provider() as service:
                self.assertIs(opened[-1], service.experiments.connection)
                opened[-1].close.assert_not_called()  # open for the whole request
        self.assertEqual(2, len(opened))
        for connection in opened:
            connection.close.assert_called_once()
        with self.assertRaises(TypeError):
            governed_result_import_service_provider("connections", self.staging)
        with self.assertRaises(TypeError):
            governed_result_import_service_provider(connections, object())


if __name__ == "__main__":
    unittest.main()
