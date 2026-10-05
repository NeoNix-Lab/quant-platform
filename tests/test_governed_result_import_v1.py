from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.admitted_input import AdmittedInputManifestV1, AdmittedInputStore  # noqa: E402
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultBundleV1,
    GovernedResultEvidenceStore,
    GovernedResultImportService,
    GovernedResultRefused,
    ResultOutputEvidenceV1,
)
from quant_platform.experiments import (  # noqa: E402
    ArtifactContentIdentity,
    ArtifactIdentity,
    ArtifactRegistration,
    ComparisonProtocolIdentity,
    ExperimentRepository,
    IdentityReference,
    RunIdentity,
    RunState,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from test_experiment_persistence_v1 import FakeConnection  # noqa: E402


def manifest(*, request: str = "request-v1:alpha") -> AdmittedInputManifestV1:
    return AdmittedInputManifestV1(
        logical_input_identities=("dataset-v1:BTCUSDT",),
        natural_partition_identities=("partition-v1:2024-01-01",),
        schema_identity="trades@1",
        schema_version="1",
        schema_hash="schema-sha256:abc",
        manifest_hashes=("manifest-sha256:abc",),
        content_hashes=("content-sha256:abc",),
        declared_coverage={"end": "2024-01-02T00:00:00Z", "start": "2024-01-01T00:00:00Z"},
        request_identity=request,
        result_identity=None,
        definition_identities=("definition-v1:trades",),
        implementation_identity="implementation-v1:abc",
        git_identity="git:abc123",
        operation_identity="strategy-compose-v1",
        profile_identity="profile-v1:default",
    )


def run(execution_id: str = "deck-run-1") -> RunIdentity:
    study = StudyIdentity(
        "study-v1",
        IdentityReference("research", "research-v1:alpha"),
        IdentityReference("objective", "objective-v1:alpha"),
        ComparisonProtocolIdentity(IdentityReference("protocol", "protocol-v1:alpha")),
    )
    trial = TrialIdentity(study, {})
    return RunIdentity(
        RunSpecIdentity(
            trial,
            IdentityReference("code", "git:abc123"),
            data_identities=(IdentityReference("dataset", "dataset-v1:BTCUSDT"),),
            run_configuration={"seed": 7},
        ),
        execution_id,
    )


def output(identity: RunIdentity, checksum: str = "sha256:" + "a" * 64) -> ResultOutputEvidenceV1:
    content = ArtifactContentIdentity("metrics", "metrics-v1", checksum)
    return ResultOutputEvidenceV1(
        ArtifactRegistration(ArtifactIdentity(identity, "metrics", content), {"uri": "deck://result/metrics"}),
        checksum,
    )


class GovernedResultImportV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.admissions = AdmittedInputStore(sqlite3.connect(":memory:"))
        self.evidence = GovernedResultEvidenceStore(sqlite3.connect(":memory:"))
        self.experiments = ExperimentRepository(FakeConnection())
        self.service = GovernedResultImportService(
            self.admissions,
            self.experiments,
            self.evidence,
            lambda evidence: evidence.content_checksum,
        )

    def delivered_manifest(self) -> AdmittedInputManifestV1:
        admitted = self.admissions.admit(manifest())
        sealed = self.admissions.seal(admitted.admission_id)
        self.admissions.begin_delivery(sealed.admission_id)
        self.admissions.record_delivery(sealed.admission_id, sealed.manifest.manifest_digest)
        return sealed.manifest

    def bundle(self, identity: RunIdentity | None = None, *, consumed: AdmittedInputManifestV1 | None = None) -> GovernedResultBundleV1:
        admitted = self.delivered_manifest() if consumed is None else consumed
        identity = run() if identity is None else identity
        return GovernedResultBundleV1(
            admitted.admission_id,
            admitted.manifest_digest,
            admitted,
            admitted.git_identity or "",
            {"python": "3.13"},
            {"strategy": "breakout"},
            {"seed": 7},
            identity,
            (output(identity),),
            metrics_evidence={"sharpe": "1.2"},
        )

    def test_refuses_unsealed_partial_and_mismatching_input_before_experiment_mutation(self) -> None:
        admitted = self.admissions.admit(manifest())
        # Construct a run-consistent malformed delivery candidate separately.
        identity = run("partial")
        candidate = GovernedResultBundleV1(
            admitted.admission_id, admitted.manifest.manifest_digest, admitted.manifest, admitted.manifest.git_identity or "",
            {}, {}, None, identity, (output(identity),),
        )
        with self.assertRaisesRegex(GovernedResultRefused, "complete delivered"):
            self.service.import_result(candidate)
        self.assertEqual((), self.experiments.list_runs_for_run_spec(identity.run_spec_identity))

        sealed = self.delivered_manifest()
        different = manifest(request="request-v1:other")
        mismatch = GovernedResultBundleV1(
            sealed.admission_id, sealed.manifest_digest, different, sealed.git_identity or "",
            {}, {}, None, identity, (output(identity),),
        )
        with self.assertRaisesRegex(GovernedResultRefused, "declared input evidence"):
            self.service.import_result(mismatch)
        self.assertEqual((), self.experiments.list_runs_for_run_spec(identity.run_spec_identity))

    def test_refuses_missing_and_expired_admissions_before_experiment_mutation(self) -> None:
        identity = run("missing")
        consumed = manifest()
        missing = GovernedResultBundleV1(
            "admitted-input-v1:missing", consumed.manifest_digest, consumed, consumed.git_identity or "",
            {}, {}, None, identity, (output(identity),),
        )
        with self.assertRaisesRegex(GovernedResultRefused, "missing or malformed"):
            self.service.import_result(missing)
        self.assertEqual((), self.experiments.list_runs_for_run_spec(identity.run_spec_identity))

        expiring = self.admissions.admit(manifest(request="request-v1:expiring"))
        sealed = self.admissions.seal(expiring.admission_id)
        self.admissions.begin_delivery(sealed.admission_id)
        self.admissions.expire(sealed.admission_id)
        expired_identity = run("expired")
        expired = GovernedResultBundleV1(
            sealed.admission_id, sealed.manifest.manifest_digest, sealed.manifest, sealed.manifest.git_identity or "",
            {}, {}, None, expired_identity, (output(expired_identity),),
        )
        with self.assertRaisesRegex(GovernedResultRefused, "complete delivered"):
            self.service.import_result(expired)
        self.assertEqual((), self.experiments.list_runs_for_run_spec(expired_identity.run_spec_identity))

    def test_exact_resubmission_is_idempotent_and_conflicting_role_is_refused(self) -> None:
        bundle = self.bundle()
        first = self.service.import_result(bundle)
        second = self.service.import_result(bundle)
        self.assertEqual(first, second)
        self.assertEqual(RunState.SUCCEEDED, first.run_record.state)
        self.assertEqual(("metrics",), tuple(item.artifact_role for item in self.experiments.list_artifacts(bundle.run_identity)))

        conflicting = ResultOutputEvidenceV1(
            ArtifactRegistration(
                ArtifactIdentity(bundle.run_identity, "metrics", ArtifactContentIdentity("metrics", "metrics-v1", "sha256:" + "b" * 64))
            ),
            "sha256:" + "b" * 64,
        )
        candidate = GovernedResultBundleV1(
            bundle.admission_id, bundle.manifest_digest, bundle.consumed_manifest, bundle.deck_code_identity,
            bundle.runtime_environment, bundle.configuration, bundle.seeds, bundle.run_identity, (conflicting,),
        )
        with self.assertRaisesRegex(GovernedResultRefused, "same Run/output role"):
            self.service.import_result(candidate)

    def test_refuses_output_checksum_mismatch_before_experiment_mutation(self) -> None:
        bundle = self.bundle(run("checksum-mismatch"))
        refusing_service = GovernedResultImportService(
            self.admissions,
            self.experiments,
            self.evidence,
            lambda evidence: "sha256:" + "f" * 64,
        )
        with self.assertRaisesRegex(GovernedResultRefused, "output checksum"):
            refusing_service.import_result(bundle)
        self.assertEqual((), self.experiments.list_runs_for_run_spec(bundle.run_identity.run_spec_identity))

    def test_new_computation_requires_and_registers_a_distinct_run_identity(self) -> None:
        consumed = self.delivered_manifest()
        first = self.bundle(run("deck-run-1"), consumed=consumed)
        second = self.bundle(run("deck-run-2"), consumed=consumed)
        self.service.import_result(first)
        self.service.import_result(second)
        self.assertNotEqual(first.run_identity.stable_dict(), second.run_identity.stable_dict())
        self.assertEqual(RunState.SUCCEEDED, self.experiments.get_run(second.run_identity).state)


if __name__ == "__main__":
    unittest.main()
