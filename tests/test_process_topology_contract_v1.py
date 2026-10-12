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

    def test_amendment_1_binds_worker_exclusion_to_the_process_lifetime(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0068-process-topology-v1.md").read_text(encoding="utf-8")
        original, _, amendment = adr.partition("## Amendment 1 (issue #335)")
        self.assertTrue(amendment, "Amendment 1 must be recorded in the ADR")
        # The superseded argument stays visible in section 6; the amendment owns the fix.
        self.assertIn("released only when the previous\nworker's session ends", original)

        for required_text in (
            "A session can end while the process lives",
            "**Two locks, one entitlement.**",
            "operating-system lock on a file",
            "released only when the worker *process* ends",
            "**The file is named by the database, not by the DSN.**",
            "It is never taken from `TMPDIR`",
            "there is no fallback",
            "**Verified before every dispatch.**",
            "the Job stays\n   `RUNNING`",
            "**Accepted constraint: one host, one filesystem view.**",
            "requires its own ADR with a fencing lease",
            "**Section 8 is clarified.**",
            "does not start\nwhen it has no runtime DSN or the write fails",
            "ADR-0062 section 4 is unchanged.",
        ):
            self.assertIn(" ".join(required_text.split()), " ".join(amendment.split()))

    def test_amendment_1_does_not_widen_the_topology(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0068-process-topology-v1.md").read_text(encoding="utf-8")
        _, _, amendment = adr.partition("## Amendment 1 (issue #335)")
        flat = " ".join(amendment.split())
        # It strengthens how one worker is enforced; it adds no unit, no second
        # worker and no effect fencing for handlers.
        for required_text in (
            "The units, the closed registry, ADR-0062",
            "Running J03 workers from more than one host is therefore not supported in v1",
            "that remains each handler's responsibility under ADR-0062 section 4",
        ):
            self.assertIn(required_text, flat)


if __name__ == "__main__":
    unittest.main()
