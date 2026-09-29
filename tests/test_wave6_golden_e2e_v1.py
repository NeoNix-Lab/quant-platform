#!/usr/bin/env python3
"""Wave 6 Golden E2E bounded proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import run_wave6_golden_e2e_proof  # noqa: E402
from quant_platform.operations.relocation import RelocationPhase  # noqa: E402
from quant_platform.operations.retention import (  # noqa: E402
    RetentionDeletionDecision,
    RetentionRefusalReason,
)


class Wave6GoldenE2EV1Tests(unittest.TestCase):
    def test_wave6_golden_proof_is_non_vacuous_and_deterministic(self):
        proof = run_wave6_golden_e2e_proof(code_ref="test-wave6-golden-e2e")

        self.assertTrue(proof.pass_)
        self.assertTrue(proof.deterministic)
        self.assertTrue(proof.candle_equivalence)
        self.assertTrue(proof.no_consumer_visible_disappearance)
        self.assertEqual(proof.live_closed_records, proof.historical_closed_records)
        self.assertEqual(3, len(proof.live_closed_records))
        self.assertNotEqual(
            proof.first_cursor.last_canonical_trade_id,
            proof.final_cursor.last_canonical_trade_id,
        )
        self.assertEqual("t-4", proof.first_cursor.last_canonical_trade_id)
        self.assertEqual("t-7", proof.final_cursor.last_canonical_trade_id)

    def test_wave6_golden_proof_exercises_k07_and_k09_outcomes(self):
        proof = run_wave6_golden_e2e_proof(code_ref="test-wave6-golden-e2e")

        self.assertEqual(RelocationPhase.CLEANED_UP, proof.relocation_record.phase)
        self.assertEqual(
            RetentionDeletionDecision.REFUSED,
            proof.protected_retention_decision.decision,
        )
        self.assertIn(
            RetentionRefusalReason.K06_PROTECTED_EVIDENCE,
            proof.protected_retention_decision.refusal_reasons,
        )
        self.assertTrue(proof.protected_candidate_still_exists)
        self.assertEqual(
            RetentionDeletionDecision.PERMITTED,
            proof.permitted_retention_decision.decision,
        )
        self.assertTrue(proof.permitted_deletion_result.deleted)
        self.assertFalse(proof.deleted_candidate_exists_after_k09)

    def test_wave6_golden_proof_emits_stable_identity_shape(self):
        proof = run_wave6_golden_e2e_proof(code_ref="test-wave6-golden-e2e")
        summary = proof.stable_dict()

        self.assertTrue(summary["proof_identity"].startswith("wave6-golden-e2e-proof-v1:sha256:"))
        self.assertTrue(summary["pass"])
        self.assertTrue(summary["deterministic"])
        self.assertIsNotNone(proof.second)
        self.assertEqual(summary["live_closed_records"], summary["historical_closed_records"])


if __name__ == "__main__":
    unittest.main()
