"""Regression guard for the accepted J03 durable-job design contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DurableJobRuntimeContractV1Tests(unittest.TestCase):
    def test_accepted_adr_freezes_recoverable_idempotent_job_semantics(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0062-durable-job-runtime-v1.md").read_text(
            encoding="utf-8"
        )

        for required_text in (
            "**Status:** ACCEPTED",
            "deterministic semantic fingerprint",
            "Submitting the same `job_id` with different immutable fields\n"
            "fails closed",
            "`RECOVERY_REQUIRED`",
            "Automatic retry is prohibited.",
            "does not make\n`RUNNING` liveness evidence",
            "A JobIdentity is never a RunIdentity.",
            "remote-execution framework.",
        ):
            self.assertIn(required_text, adr)

    def test_core_contract_and_application_boundary_reference_the_accepted_adr(self) -> None:
        contract = (ROOT / "docs/contracts/CORE_CONTRACTS.md").read_text(encoding="utf-8")
        application_init = (ROOT / "src/quant_platform/application/__init__.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("[ADR-0062]", contract)
        self.assertIn("Automatic retry is prohibited.", contract)
        self.assertIn("never silently redispatches", contract)
        self.assertIn("ADR-0062 reserves future bounded J03 runtime composition", application_init)
