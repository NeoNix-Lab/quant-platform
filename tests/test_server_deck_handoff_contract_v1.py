"""Regression guard for the accepted server/deck handoff design contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ServerDeckHandoffContractV1Tests(unittest.TestCase):
    def test_accepted_adr_freezes_deck_placement_and_sealed_input(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0064-server-deck-admitted-handoff-v1.md").read_text(
            encoding="utf-8"
        )

        for required_text in (
            "**Status:** ACCEPTED",
            "consumer machine is the existing ADR-0057 deck",
            "`AdmittedInputManifestV1`",
            "`admission_id`",
            "excludes absolute paths, storage-root ids, catalog UUIDs",
            "Only `SEALED` evidence is admissible",
            "refuses the entire\n"
            "bundle with no catalog or Experiment mutation",
            "Re-submission of the exact same bundle\n"
            "is idempotent",
        ):
            self.assertIn(required_text, adr)

    def test_data_gateway_contract_preserves_handoff_identity_and_refusal_boundary(self) -> None:
        contract = (ROOT / "docs/contracts/DATA_GATEWAY.md").read_text(encoding="utf-8")

        self.assertIn("ADR-0064 governs", contract)
        self.assertIn("never an admitted-input\nidentity", contract)
        self.assertIn("without catalog or Experiment mutation", contract)
