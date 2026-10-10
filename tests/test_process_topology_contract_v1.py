"""Regression guard for the accepted DG-K process topology design contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProcessTopologyContractV1Tests(unittest.TestCase):
    def test_accepted_adr_freezes_units_and_server_boundary(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0068-process-topology-v1.md").read_text(encoding="utf-8")

        for required_text in (
            "**Status:** ACCEPTED",
            "### 1. A modular monolith deployed as five units",
            "No unit is created per domain package.",
            "The server never pushes, schedules or delegates work to the deck",
            "no remote worker pulls from the server's J03 queue",
            "it is deck work, not server J03 work",
            "Hosts remain in `tools/` (no `apps/`)",
        ):
            self.assertIn(required_text, adr)

    def test_accepted_adr_preserves_identity_bytes(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0068-process-topology-v1.md").read_text(encoding="utf-8")

        for required_text in (
            "`sorted-compact-ascii-v1`",
            "`sorted-compact-utf8-v1`",
            "`ordered-compact-ascii-line-v1`",
            "`rfc8785-v1`",
            "P01 replaces the call, never the\n   bytes.",
            "No existing site moves to it.",
            "two different `PYTHONHASHSEED` values",
        ):
            self.assertIn(required_text, adr)

    def test_accepted_adr_keeps_one_worker_without_amending_adr_0062(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0068-process-topology-v1.md").read_text(encoding="utf-8")

        for required_text in (
            "Exactly one worker process runs.",
            "session-level\nadvisory lock",
            "ADR-0062 §4's attempt fields are unchanged",
            "There is no\ngeneric or caller-supplied handler",
            "**establishes** atomic K13 registration",
        ):
            self.assertIn(required_text, adr)


if __name__ == "__main__":
    unittest.main()
