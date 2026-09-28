#!/usr/bin/env python3
"""Wave 5 Golden E2E supervised ML proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import run_wave5_golden_supervised_proof  # noqa: E402


class Wave5GoldenSupervisedE2EV1Tests(unittest.TestCase):
    def test_supervised_golden_proof_is_non_vacuous_and_deterministic(self):
        proof = run_wave5_golden_supervised_proof(
            code_ref="test-wave5-golden-supervised",
            execution_id="test-wave5-golden-supervised-e2e",
        )

        self.assertTrue(proof.deterministic)
        self.assertEqual(proof.first.stable_dict(), proof.second.stable_dict())
        self.assertEqual(
            "canonical",
            proof.dataset_identity.layer,
        )
        self.assertEqual(("train", "train", "test", "test"), tuple(sample.side.value for sample in proof.projection.samples))
        self.assertEqual(2, len(proof.projection.samples_for_side("train")))
        self.assertEqual(2, len(proof.projection.samples_for_side("test")))
        self.assertEqual((), proof.projection.rejections)
        self.assertEqual("1", proof.first.metrics.accuracy)
        self.assertEqual(2, proof.first.metrics.row_count)

    def test_supervised_golden_proof_emits_stable_artifact_identities(self):
        proof = run_wave5_golden_supervised_proof(
            code_ref="test-wave5-golden-supervised",
            execution_id="test-wave5-golden-supervised-e2e",
        )
        summary = proof.stable_dict()

        self.assertTrue(summary["first_run_equals_second_run"])
        self.assertTrue(summary["projection_identity"].startswith("supervised-projection-v1:sha256:"))
        self.assertTrue(summary["training_policy_identity"].startswith("supervised-training-policy-v1:sha256:"))
        self.assertTrue(summary["normalizer_identity"].startswith("supervised-fold-normalizer-v1:sha256:"))
        self.assertTrue(summary["model_identity"].startswith("supervised-centroid-model-v1:sha256:"))
        self.assertEqual(
            ("metrics", "model", "normalizer", "predictions"),
            tuple(sorted(summary["artifact_content_identities"])),
        )
        for identity in summary["artifact_content_identities"].values():
            self.assertIn(":sha256:", identity)


if __name__ == "__main__":
    unittest.main()
