#!/usr/bin/env python3
"""I01 Study/Trial/Run/Artifact semantic identity proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.experiments import (  # noqa: E402
    ArtifactContentIdentity,
    ArtifactIdentity,
    ComparisonProtocolIdentity,
    ExperimentIdentityError,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)


def ref(kind: str, identity: str) -> IdentityReference:
    return IdentityReference(kind, identity)


def protocol(*dimensions: str, identity: str = "absorption-comparison-v1") -> ComparisonProtocolIdentity:
    return ComparisonProtocolIdentity(
        ref("comparison-protocol", identity),
        dimensions,
    )


def study(*dimensions: str, protocol_identity: str = "absorption-comparison-v1") -> StudyIdentity:
    return StudyIdentity(
        study_definition_version="study-definition-v1",
        research_identity=ref("hypothesis-spec", "absorption-hypothesis-v3"),
        evaluation_objective_identity=ref("metric-set", "oos-profit-factor-v2"),
        comparison_protocol_identity=protocol(*dimensions, identity=protocol_identity),
    )


def trial(**assignments) -> TrialIdentity:
    return TrialIdentity(study("lookback", "threshold"), assignments)


def run_spec(
    trial_identity: TrialIdentity | None = None,
    *,
    data_identity: str = "dataset-snapshot:2024-01",
    code_identity: str = "git:abc123",
    config=None,
) -> RunSpecIdentity:
    return RunSpecIdentity(
        trial_identity=trial_identity or trial(lookback=20, threshold={"z": 2.5, "side": "buy"}),
        data_identities=(ref("dataset-snapshot", data_identity),),
        feature_identities=(ref("feature-artifact", "features:v7"),),
        research_label_identities=(ref("label-semantics", "triple-barrier-v1"),),
        validation_identity=ref("validation-schedule", "wf-5-fold-v1"),
        strategy_policy_execution_identities=(ref("strategy-spec", "entry-policy-v4"),),
        code_identity=ref("code", code_identity),
        environment_identity=ref("environment", "python-3.11-lock-v2"),
        run_configuration=config or {"seed": 11, "trainer": {"epochs": 5, "batch_size": 64}},
    )


class ExperimentIdentityV1Tests(unittest.TestCase):
    def test_study_identity_changes_only_for_study_semantic_inputs(self):
        base = study("lookback", "threshold")

        same = StudyIdentity(
            study_definition_version="study-definition-v1",
            research_identity=ref("hypothesis-spec", "absorption-hypothesis-v3"),
            evaluation_objective_identity=ref("metric-set", "oos-profit-factor-v2"),
            comparison_protocol_identity=protocol("lookback", "threshold"),
        )
        changed_research = StudyIdentity(
            study_definition_version="study-definition-v1",
            research_identity=ref("hypothesis-spec", "breakout-hypothesis-v1"),
            evaluation_objective_identity=base.evaluation_objective_identity,
            comparison_protocol_identity=base.comparison_protocol_identity,
        )
        changed_protocol = study("lookback", "seed", protocol_identity="seed-comparison-v1")

        self.assertEqual(base.fingerprint, same.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_research.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_protocol.fingerprint)
        self.assertNotIn("dataset", json.dumps(base.fingerprint_payload()))
        self.assertNotIn("code", json.dumps(base.fingerprint_payload()))

    def test_trial_identity_requires_exact_declared_dimensions(self):
        base = trial(lookback=20, threshold={"side": "buy", "z": 2.5})
        reordered_mapping = trial(threshold={"z": 2.5, "side": "buy"}, lookback=20)
        changed_assignment = trial(lookback=30, threshold={"side": "buy", "z": 2.5})

        self.assertEqual(base.fingerprint, reordered_mapping.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_assignment.fingerprint)
        with self.assertRaisesRegex(ExperimentIdentityError, "missing"):
            TrialIdentity(study("lookback", "threshold"), {"lookback": 20})
        with self.assertRaisesRegex(ExperimentIdentityError, "undeclared"):
            TrialIdentity(study("lookback"), {"lookback": 20, "seed": 7})
        with self.assertRaisesRegex(ExperimentIdentityError, "duplicate"):
            TrialIdentity(study("lookback"), [("lookback", 20), ("lookback", 30)])

    def test_seed_is_trial_level_only_when_declared_by_the_study(self):
        run_seed_a = run_spec(config={"seed": 1})
        run_seed_b = run_spec(config={"seed": 2})
        seed_trial_a = TrialIdentity(study("seed"), {"seed": 1})
        seed_trial_b = TrialIdentity(study("seed"), {"seed": 2})

        self.assertEqual(run_seed_a.trial_identity.fingerprint, run_seed_b.trial_identity.fingerprint)
        self.assertNotEqual(run_seed_a.fingerprint, run_seed_b.fingerprint)
        self.assertNotEqual(seed_trial_a.fingerprint, seed_trial_b.fingerprint)

    def test_fingerprints_use_canonical_json_type_and_version_discrimination(self):
        candidate = trial(threshold={"side": "buy", "z": 2.5}, lookback=20)
        expected = hashlib.sha256(
            json.dumps(
                candidate.fingerprint_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(expected, candidate.fingerprint)
        self.assertEqual("trial", candidate.fingerprint_payload()["identity_type"])
        self.assertEqual("1", candidate.fingerprint_payload()["identity_version"])
        self.assertNotEqual(candidate.fingerprint, candidate.study_identity.fingerprint)
        self.assertNotEqual(
            ArtifactContentIdentity("metrics", "metrics-v1", candidate.fingerprint).fingerprint,
            candidate.fingerprint,
        )

    def test_run_spec_changes_for_bound_reproducibility_inputs_only(self):
        base = run_spec(config={"trainer": {"batch_size": 64, "epochs": 5}, "seed": 11})
        reordered_config = run_spec(config={"seed": 11, "trainer": {"epochs": 5, "batch_size": 64}})
        changed_data = run_spec(data_identity="dataset-snapshot:2024-02")
        changed_code = run_spec(code_identity="git:def456")

        self.assertEqual(base.fingerprint, reordered_config.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_data.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_code.fingerprint)
        payload = base.fingerprint_payload()
        for forbidden in ("execution_id", "attempt", "timestamp", "hostname", "pid", "path", "database_id"):
            self.assertNotIn(forbidden, payload)

    def test_multiple_runs_share_run_spec_but_have_distinct_execution_ids(self):
        spec = run_spec()
        first = RunIdentity(spec, "runtime-execution-001")
        second = RunIdentity(spec, "runtime-execution-002")

        self.assertEqual(first.run_spec_identity.fingerprint, second.run_spec_identity.fingerprint)
        self.assertNotEqual(first.execution_id, second.execution_id)
        self.assertNotIn("identity_type", first.stable_dict())
        with self.assertRaisesRegex(ExperimentIdentityError, "execution_id"):
            RunIdentity(spec, "")

    def test_artifact_content_equivalence_is_separate_from_run_provenance(self):
        spec = run_spec()
        first_run = RunIdentity(spec, "runtime-execution-001")
        second_run = RunIdentity(spec, "runtime-execution-002")
        content = ArtifactContentIdentity(
            "metrics",
            "metrics-v1",
            "canonical-content-hash-v1:sha256:" + "a" * 64,
        )
        first_artifact = ArtifactIdentity(first_run, "evaluation-metrics", content)
        second_artifact = ArtifactIdentity(second_run, "evaluation-metrics", content)
        changed_role = ArtifactIdentity(first_run, "lockbox-metrics", content)

        self.assertEqual(content.fingerprint, first_artifact.artifact_content_identity.fingerprint)
        self.assertEqual(content.fingerprint, second_artifact.artifact_content_identity.fingerprint)
        self.assertNotEqual(first_artifact.stable_dict(), second_artifact.stable_dict())
        self.assertNotEqual(first_artifact.stable_dict(), changed_role.stable_dict())
        self.assertNotIn("path", first_artifact.stable_dict())
        self.assertNotIn("created_at", first_artifact.stable_dict())

    def test_invalid_content_and_protocol_values_fail_explicitly(self):
        with self.assertRaisesRegex(ExperimentIdentityError, "duplicate"):
            protocol("lookback", "lookback")
        with self.assertRaisesRegex(ExperimentIdentityError, "JSON-compatible"):
            TrialIdentity(study("lookback"), {"lookback": object()})
        with self.assertRaisesRegex(ExperimentIdentityError, "finite JSON"):
            run_spec(config={"learning_rate": float("nan")})
        with self.assertRaisesRegex(ExperimentIdentityError, "runtime locator"):
            run_spec(config={"path": "C:/tmp/run-output"})


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
