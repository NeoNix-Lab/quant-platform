"""Regression guard for the accepted DG-K J02 seam carriage and deck handoff contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/decisions/ADR-0069-j02-seam-carriage-and-deck-handoff-v1.md"


class J02SeamCarriageContractV1Tests(unittest.TestCase):
    def test_accepted_adr_keeps_j02_v1_and_authorizes_before_decode(self) -> None:
        adr = ADR.read_text(encoding="utf-8")

        for required_text in (
            "**Status:** ACCEPTED",
            "A message without `message_family` remains a J02 v1\nmarket-data message, unchanged.",
            "with close `1008` before decoding `request`",
            "Scopes never imply one another",
            "it is not converted into a\nJob",
        ):
            self.assertIn(required_text, adr)

    def test_accepted_adr_defines_one_scope_per_family(self) -> None:
        adr = ADR.read_text(encoding="utf-8")

        for scope in (
            "`j02.strategy.compose`",
            "`j02.validation.evaluate`",
            "`j02.training.evaluate`",
            "`j02.training.register`",
            "`j02.jobs.submit`",
            "`j02.jobs.read`",
            "`j02.admitted_input.read`",
            "`j02.result.submit`",
        ):
            self.assertIn(scope, adr)

    def test_accepted_adr_defines_deck_handoff_and_provider_identity(self) -> None:
        adr = ADR.read_text(encoding="utf-8")

        for required_text in (
            "all initiated by the deck",
            "`admitted-input-member-v1:<admission_id>:<member index>`",
            "`feature-provider-v1:sha256:<hex>`",
            "`GovernedResultBundleV1`'s fields and digest\nrules are unchanged.",
            "The server never resolves,\nimports or executes it",
            "does not import `quant_platform`",
        ):
            self.assertIn(required_text, adr)


if __name__ == "__main__":
    unittest.main()
