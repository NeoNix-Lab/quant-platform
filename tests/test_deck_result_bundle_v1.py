"""The deck workload's producer of the K13 bundle (issue #394, ADR-0072 section 5).

The documents are built by ``deck_result_bundle`` and submitted through ``ResultImportTransport`` over a
``GovernedResultImportService`` composed with the registry of ADR-0072 section 2, so what is proved is that
the service accepts them, not that the module agrees with itself. The work tree checks run against a real
Git repository in a scratch directory.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import copy
import dataclasses
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.application.admitted_input import AdmittedInputStore  # noqa: E402
from quant_platform.application.deck_result_bundle import (  # noqa: E402
    DECK_OUTPUT_ROLES_V1,
    DeckAdmission,
    DeckOutput,
    DeckResultError,
    DeckRunDeclaration,
    FeatureProviderSource,
    WorkloadTree,
    build_deck_result,
    content_checksum,
    inspect_workload_tree,
)
from quant_platform.application.feature_provider_identity import (  # noqa: E402
    REPLAY_FEATURE_PROVIDER_KEY,
    check_replay_feature_provider,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultEvidenceStore,
    GovernedResultImportService,
)
from quant_platform.application.market_data import ConsumerApiError  # noqa: E402
from quant_platform.application.result_import_composition import (  # noqa: E402
    K13_OUTPUT_ROLES_V1,
    K13_STAGING_BOUNDS_V1,
)
from quant_platform.application.result_import_transport import (  # noqa: E402
    ResultImportTransport,
    StagedOutputStore,
    bundle_from_wire,
    upload_chunks,
)
from quant_platform.experiments import ExperimentRepository, RunState  # noqa: E402
from quant_platform.experiments.identities import (  # noqa: E402
    ComparisonProtocolIdentity,
    IdentityReference,
    StudyIdentity,
)
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402
from test_governed_result_import_v1 import manifest  # noqa: E402

DECK_DIRECTORY = ROOT / "clients" / "deck"
MODULE = ROOT / "src" / "quant_platform" / "application" / "deck_result_bundle.py"
PROVIDER = FeatureProviderSource(
    repository="NeoNix-Lab/quant-platform", qualified_name="deck_features_probe_394:build",
    source_sha256="cd" * 32, parameters={"window": 20},
)


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=directory, capture_output=True, text=True, check=True,
    ).stdout.strip()


def make_repository(directory: Path) -> str:
    git(directory, "init", "-q")
    git(directory, "config", "user.email", "deck@example.invalid")
    git(directory, "config", "user.name", "deck")
    git(directory, "config", "commit.gpgsign", "false")
    (directory / "workload.py").write_text("RESULT = 1\n", encoding="utf-8")
    git(directory, "add", "workload.py")
    git(directory, "commit", "-q", "-m", "workload")
    return git(directory, "rev-parse", "HEAD")


def declaration(execution_id: str, **extra) -> DeckRunDeclaration:
    study = StudyIdentity(
        "study-v1",
        IdentityReference("research", "research-v1:alpha"),
        IdentityReference("objective", "objective-v1:alpha"),
        ComparisonProtocolIdentity(IdentityReference("protocol", "protocol-v1:alpha")),
    )
    return DeckRunDeclaration(study=study, execution_id=execution_id, run_configuration={"seed": 7}, **extra)


class DeckResultBundleV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._scratch = tempfile.TemporaryDirectory(prefix="deck_bundle_")
        cls.repository = Path(cls._scratch.name) / "workload"
        cls.repository.mkdir()
        cls.commit = make_repository(cls.repository)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scratch.cleanup()

    def setUp(self) -> None:
        self.staging = StagedOutputStore(K13_STAGING_BOUNDS_V1)
        self.admissions = AdmittedInputStore(sqlite3.connect(":memory:"))
        self.evidence = GovernedResultEvidenceStore(sqlite3.connect(":memory:"))
        self.experiments = ExperimentRepository(FakeConnection())

        @contextmanager
        def session():
            yield GovernedResultImportService(
                self.admissions, self.experiments, self.evidence, self.staging.verify_output, K13_OUTPUT_ROLES_V1,
            )
        self.transport = ResultImportTransport(session, self.staging)
        self.tree = WorkloadTree(self.commit, True)
        self._admission = None

    # -- helpers -----------------------------------------------------------------

    def admission(self, **replace) -> DeckAdmission:
        """A delivered, sealed admission at the workload's commit, as the client hands it over."""
        if self._admission is None:
            sealed_manifest = dataclasses.replace(manifest(), git_identity=self.commit, **replace)
            admitted = self.admissions.admit(sealed_manifest)
            sealed = self.admissions.seal(admitted.admission_id)
            self.admissions.record_delivery(
                sealed.admission_id, sealed.manifest.manifest_digest,
                delivery_attempt=self.admissions.begin_delivery(sealed.admission_id).delivery_attempt,
            )
            self._admission = DeckAdmission(
                sealed.admission_id, sealed.manifest.manifest_digest, sealed.manifest.canonical_fields(),
            )
        return self._admission

    def build(self, role="metrics", execution_id="deck-run-394", data=None, **extra):
        data = data if data is not None else f"{role}-bytes-{execution_id}".encode()
        replay = role.startswith("replay-")
        return build_deck_result(
            self.admission(), self.tree, declaration(execution_id), (DeckOutput(role, data),),
            runtime_environment={"python": "3.13"}, seeds={"seed": 7},
            replay_provider=PROVIDER if replay else None, **extra,
        )

    def stage(self, data: bytes) -> None:
        for body in upload_chunks(data, frame_payload_bytes=self.staging.bounds.frame_payload_bytes):
            self.transport.execute("upload_output", self.transport.decode("upload_output", body))

    def submit(self, wire) -> dict:
        return self.transport.execute("submit", self.transport.decode("submit", wire))

    def registered(self, result) -> bool:
        identity = result.bundle.run_identity
        return any(
            record.execution_id == identity.execution_id and record.state is RunState.SUCCEEDED
            for record in self.experiments.list_runs_for_run_spec(identity.run_spec_identity)
        )

    # -- the document passes the service (acceptance 5) ---------------------------------

    def test_every_role_builds_a_document_the_service_registers(self) -> None:
        for number, role in enumerate(sorted(DECK_OUTPUT_ROLES_V1)):
            with self.subTest(role):
                result = self.build(role, f"deck-run-{number}")
                for upload in result.uploads:
                    self.stage(upload.payload)
                self.submit(result.wire)
                self.assertTrue(self.registered(result))

    def test_the_wire_is_the_bundle_encoded_and_is_plain_json(self) -> None:
        result = self.build("replay-result")
        self.assertEqual(result.bundle, bundle_from_wire(json.loads(json.dumps(result.wire))))
        self.assertEqual(json.loads(json.dumps(result.wire)), result.wire)

    def test_a_wrong_output_checksum_is_refused_by_the_service(self) -> None:
        result = self.build("metrics")
        self.stage(result.uploads[0].payload)
        wire = copy.deepcopy(result.wire)
        wire["outputs"][0]["content_checksum"] = content_checksum(b"something else")
        with self.assertRaises(ConsumerApiError):
            self.submit(wire)
        self.assertFalse(self.registered(result))

    def test_bytes_other_than_the_declared_ones_are_refused_by_the_service(self) -> None:
        result = self.build("metrics")
        self.stage(b"not the declared bytes")
        with self.assertRaises(ConsumerApiError):
            self.submit(result.wire)
        self.assertFalse(self.registered(result))

    def test_a_document_pinned_to_another_commit_is_refused_by_the_service(self) -> None:
        result = self.build("metrics")
        self.stage(result.uploads[0].payload)
        wire = copy.deepcopy(result.wire)
        wire["deck_code_identity"] = "f" * 40
        with self.assertRaises(ConsumerApiError):
            self.submit(wire)
        self.assertFalse(self.registered(result))

    def test_a_replay_document_without_its_provider_is_refused_by_the_service(self) -> None:
        result = self.build("replay-summary")
        self.stage(result.uploads[0].payload)
        wire = copy.deepcopy(result.wire)
        del wire["configuration"][REPLAY_FEATURE_PROVIDER_KEY]
        with self.assertRaises(ConsumerApiError):
            self.submit(wire)
        self.assertFalse(self.registered(result))

    # -- what the document carries (acceptance 2, 3, 4) ---------------------------------

    def test_the_run_identity_carries_the_manifest_code_and_covers_its_inputs_and_definitions(self) -> None:
        admission = self.admission()
        result = self.build("metrics")
        spec = result.bundle.run_identity.run_spec_identity
        self.assertEqual(self.commit, spec.code_identity.identity)
        self.assertEqual(self.commit, result.bundle.deck_code_identity)
        self.assertEqual(admission.manifest["git_identity"], result.bundle.deck_code_identity)
        self.assertTrue(
            set(admission.manifest["logical_input_identities"]) <= {r.identity for r in spec.data_identities}
        )
        self.assertTrue(
            set(admission.manifest["definition_identities"]) <= {r.identity for r in spec.feature_identities}
        )

    def test_a_workload_that_already_names_an_identity_is_not_given_a_second_reference(self) -> None:
        admission = self.admission()
        named = IdentityReference("dataset", admission.manifest["logical_input_identities"][0])
        own = IdentityReference("feature-definition", "definition-v1:own")
        result = build_deck_result(
            admission, self.tree, declaration("deck-run-named", data_identities=(named,), feature_identities=(own,)),
            (DeckOutput("metrics", b"m"),), runtime_environment={"python": "3.13"},
        )
        spec = result.bundle.run_identity.run_spec_identity
        identities = [reference.identity for reference in spec.data_identities]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertIn(named, spec.data_identities)
        self.assertIn(own, spec.feature_identities)

    def test_each_output_is_registered_by_the_checksum_of_its_uploaded_bytes_under_its_registry_role(self) -> None:
        admission = self.admission()
        outputs = [DeckOutput(role, f"bytes of {role}".encode()) for role in sorted(DECK_OUTPUT_ROLES_V1)]
        result = build_deck_result(
            admission, self.tree, declaration("deck-run-all"), outputs,
            runtime_environment={"python": "3.13"}, replay_provider=PROVIDER,
        )
        self.assertEqual(len(outputs), len(result.uploads))
        for upload in result.uploads:
            self.assertEqual(content_checksum(upload.payload), upload.content_checksum)
        by_role = {output.registration.identity.artifact_role: output for output in result.bundle.outputs}
        for output in outputs:
            evidence = by_role[output.role]
            content = evidence.registration.identity.artifact_content_identity
            self.assertEqual(content_checksum(output.payload), content.content_identity)
            self.assertEqual(content_checksum(output.payload), evidence.content_checksum)
            self.assertEqual(DECK_OUTPUT_ROLES_V1[output.role], (content.artifact_kind, content.artifact_schema_version))

    def test_the_registry_is_the_one_the_server_admits(self) -> None:
        self.assertEqual(
            {contract.artifact_role: (contract.artifact_kind, contract.artifact_schema_version)
             for contract in K13_OUTPUT_ROLES_V1},
            dict(DECK_OUTPUT_ROLES_V1),
        )

    def test_a_replay_output_carries_a_provider_identity_at_the_workload_commit(self) -> None:
        result = self.build("replay-result")
        entry = result.bundle.configuration[REPLAY_FEATURE_PROVIDER_KEY]
        self.assertEqual(self.commit, entry["payload"]["commit"])
        self.assertEqual(PROVIDER.qualified_name, entry["payload"]["qualified_name"])
        self.assertEqual(entry["identity"], check_replay_feature_provider(result.bundle.configuration))

    def test_a_supervised_output_carries_no_provider(self) -> None:
        self.assertNotIn(REPLAY_FEATURE_PROVIDER_KEY, self.build("model").bundle.configuration)

    # -- the module's own refusals (acceptance 1, 5) -------------------------------------

    def refused(self, reason, **override):
        arguments = dict(
            admission=self.admission(), tree=self.tree, run=declaration("deck-run-refused"),
            outputs=(DeckOutput("metrics", b"m"),), runtime_environment={"python": "3.13"},
        )
        arguments.update(override)
        with self.assertRaises(DeckResultError) as raised:
            build_deck_result(
                arguments.pop("admission"), arguments.pop("tree"), arguments.pop("run"), arguments.pop("outputs"),
                **arguments,
            )
        self.assertEqual(reason, raised.exception.reason)

    def test_a_dirty_tree_builds_nothing(self) -> None:
        self.refused("dirty_tree", tree=WorkloadTree(self.commit, False))

    def test_a_commit_other_than_the_manifests_builds_nothing(self) -> None:
        self.refused("commit_mismatch", tree=WorkloadTree("0" * 40, True))

    def test_an_admission_whose_manifest_is_not_the_one_it_names_builds_nothing(self) -> None:
        admission = self.admission()
        altered = dict(admission.manifest, operation_identity="strategy-compose-v2")
        self.refused("manifest_digest_mismatch", admission=dataclasses.replace(admission, manifest=altered))
        self.refused("manifest_digest_mismatch", admission=dataclasses.replace(admission, admission_id="other"))
        self.refused("manifest_malformed", admission=dataclasses.replace(admission, manifest={"version": "x"}))

    def test_a_manifest_without_a_code_identity_builds_nothing(self) -> None:
        self._admission = None
        sealed = dataclasses.replace(manifest(), git_identity=None)
        admission = DeckAdmission(sealed.admission_id, sealed.manifest_digest, sealed.canonical_fields())
        self.refused("manifest_without_code", admission=admission)

    def test_outputs_are_a_closed_non_empty_set_of_registry_roles_with_bytes(self) -> None:
        self.refused("outputs_empty", outputs=())
        self.refused("role_unknown", outputs=(DeckOutput("report", b"x"),))
        self.refused("role_duplicate", outputs=(DeckOutput("metrics", b"x"), DeckOutput("metrics", b"y")))
        self.refused("output_malformed", outputs=(DeckOutput("metrics", b""),))
        self.refused("output_malformed", outputs=(DeckOutput("metrics", "text"),))

    def test_the_provider_is_required_for_replay_roles_and_only_for_them(self) -> None:
        self.refused("provider_missing", outputs=(DeckOutput("replay-result", b"r"),))
        self.refused("provider_without_replay", replay_provider=PROVIDER)
        self.refused(
            "provider_preset", outputs=(DeckOutput("replay-result", b"r"),), replay_provider=PROVIDER,
            configuration={REPLAY_FEATURE_PROVIDER_KEY: {"identity": "x", "payload": {}}},
        )

    # -- the work tree --------------------------------------------------------------------

    def test_the_tree_of_a_clean_checkout_is_its_head_commit(self) -> None:
        tree = inspect_workload_tree(self.repository)
        self.assertEqual(WorkloadTree(self.commit, True), tree)

    def test_a_modified_or_untracked_file_makes_the_tree_dirty(self) -> None:
        for change in ("modified", "untracked"):
            with self.subTest(change), tempfile.TemporaryDirectory(dir=self._scratch.name) as directory:
                path = Path(directory)
                commit = make_repository(path)
                if change == "modified":
                    (path / "workload.py").write_text("RESULT = 2\n", encoding="utf-8")
                else:
                    (path / "extra.py").write_text("X = 1\n", encoding="utf-8")
                self.assertEqual(WorkloadTree(commit, False), inspect_workload_tree(path))

    def test_a_directory_that_is_not_a_git_tree_is_a_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DeckResultError) as raised:
                inspect_workload_tree(directory)
            self.assertEqual("tree_unavailable", raised.exception.reason)

    # -- the boundary (acceptance 6) --------------------------------------------------------

    @staticmethod
    def imported_modules(path: Path) -> set[str]:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(("." * node.level) + (node.module or ""))
        return modules

    def test_the_deck_client_still_imports_nothing_of_the_platform(self) -> None:
        sources = sorted(DECK_DIRECTORY.glob("*.py"))
        self.assertTrue(sources)
        for source in sources:
            offenders = [m for m in self.imported_modules(source) if m.split(".")[0] == "quant_platform"]
            self.assertEqual([], offenders, source.name)

    def test_the_module_imports_no_client_and_no_transport(self) -> None:
        forbidden = {"clients", "websockets", "socket", "ssl", "asyncio", "http", "urllib"}
        offenders = [
            module for module in self.imported_modules(MODULE)
            if module.split(".")[0] in forbidden or module.endswith("deck_handoff_client")
        ]
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
