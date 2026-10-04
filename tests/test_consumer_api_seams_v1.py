"""Regression guard for the accepted J10-J13 Consumer API seam decision."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ConsumerApiSeamsV1Tests(unittest.TestCase):
    def test_accepted_adr_maps_existing_operations_and_defers_replay(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0065-consumer-api-seams-v1.md").read_text(
            encoding="utf-8"
        )

        for required_text in (
            "**Status:** ACCEPTED",
            "`strategy-compose-v1` exposes exactly the existing `compose_decision`",
            "J12 exposes only the already accepted finite Validation operations",
            "`supervised-train-evaluate-v1`",
            "J11 has no accepted consumer request yet",
            "requires a\n`feature_provider` callable",
            "path, module name, opaque executable, or client callback",
        ):
            self.assertIn(required_text, adr)

    def test_consumer_contract_preserves_application_ownership_and_replay_trigger(self) -> None:
        contract = (ROOT / "docs/contracts/CONSUMER_API.md").read_text(encoding="utf-8")

        self.assertIn("ADR-0065 defines", contract)
        self.assertIn("callable import names", contract)
        self.assertIn("Application owns normalization", contract)
        self.assertIn("J11 Replay remains deferred", contract)
