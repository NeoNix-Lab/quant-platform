"""Regression guard for the accepted J14 transport-evolution decision."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TransportEvolutionContractV1Tests(unittest.TestCase):
    def test_accepted_adr_preserves_j02_and_separates_frames_from_pages(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0066-transport-evolution-v1.md").read_text(
            encoding="utf-8"
        )

        for required_text in (
            "**Status:** ACCEPTED",
            "`j02-request-v1` and `j02-response-v1` remain a single complete exchange",
            "`RESULT_TOO_LARGE` refusal are unchanged",
            "`j14-framed-result-v1`",
            "RFC 8785\nJSON Canonicalization Scheme and encodes that serialization as UTF-8 bytes",
            "preimage object",
            '"logical_result_identity": "<existing application identity>"',
            "lowercase hexadecimal SHA-256 digest",
            "It must not expose a partial frame set\nas a partial page",
            "A J14 transfer\ncursor is never a `LiveStreamCursorV1`",
        ):
            self.assertIn(required_text, adr)

    def test_consumer_contract_keeps_framing_and_live_cursor_distinct(self) -> None:
        contract = (ROOT / "docs/contracts/CONSUMER_API.md").read_text(encoding="utf-8")

        self.assertIn("ADR-0066 keeps", contract)
        self.assertIn("RFC 8785 JSON Canonicalization Scheme encoded as\nUTF-8", contract)
        self.assertIn("SHA-256 digests are lowercase hexadecimal", contract)
        self.assertIn("Frames are never\napplication-level pages", contract)
        self.assertIn("finite transfer id must never be treated as a B06 cursor", contract)
