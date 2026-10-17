"""Regression guard for the DG-K2 server/deck deployment composition design contract (ADR-0072)."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/decisions/ADR-0072-server-deck-deployment-composition-v1.md"


def adr_text() -> str:
    return ADR.read_text(encoding="utf-8")


class DeckDeploymentCompositionContractV1Tests(unittest.TestCase):
    def test_the_adr_is_accepted_and_amends_no_accepted_adr(self) -> None:
        text = adr_text()
        for required_text in (
            "# ADR-0072 - Server/deck deployment composition v1 (DG-K2)",
            "**Status:** ACCEPTED",
            "Amends no ADR",
            "Governance issues #381, #382; design gate #383; acceptance #387.",
        ):
            self.assertIn(required_text, text)
        for forbidden_text in ("[TBD]", "[DA CONFERMARE]"):
            self.assertNotIn(forbidden_text, text)

    def test_k12_authority_is_dataset_only_and_built_without_reading_records(self) -> None:
        text = adr_text()
        for required_text in (
            "### 1. K12 server authority (S01)",
            "`resolve_market_data_request`",
            "`DataScanOpenMetadata`",
            "without reading a record",
            "`artifact_inputs_unavailable`",
            "**Operation and profile identities are not a closed set.**",
            "v1 has **no time-based expiry**",
            "`max_member_bytes` 32 MiB",
            "`max_total_bytes` 64 MiB",
            "`max_retained_payload_bytes` 96 MiB",
            "no real partition size was measured",
        ):
            self.assertIn(required_text, text)

    def test_amendment_1_raises_the_delivery_bounds_to_the_measured_history(self) -> None:
        text = adr_text()
        for required_text in (
            "## Amendment 1 (issue #431)",
            "| `max_member_bytes` | 32 MiB | **512 MiB** |",
            "| `max_total_bytes` | 64 MiB | **1 GiB** |",
            "| `max_retained_payload_bytes` (J14) | 96 MiB | **1.5 GiB** |",
            "| `retention_seconds` (J14) | 300 | **600** |",
            "**719 partitions (34 %) exceed",
            "| 2 members of 512 MiB (1 GiB, the new total) |",
            "concurrent deliveries and a real network between deck and server were",
            "A delivery of the largest real day (252.8 MiB) on the real server",
        ):
            self.assertIn(required_text, text)

    def test_k13_registry_is_closed_and_uses_the_checksum_as_content_identity(self) -> None:
        text = adr_text()
        for required_text in (
            "**Output roles (closed registry).**",
            "| `normalizer` | `supervised-normalizer` | `supervised-normalizer-v1` |",
            "| `replay-result` | `replay-result` | `replay-result-v1` | `ReplayOutputContractV1` |",
            "| `replay-summary` | `replay-summary` | `replay-summary-v1` | `ReplayOutputContractV1` |",
            "content identity of an output is the K13 checksum of the uploaded bytes",
            "`max_output_bytes` 8 MiB",
            "The API login registers; the deck holds no database login",
        ):
            self.assertIn(required_text, text)

    def test_host_composition_is_opt_in_and_fail_closed(self) -> None:
        text = adr_text()
        for required_text in (
            "`--principals-file`",
            "**default is none**",
            "SHA-256 of the principals file bytes",
            "**Fail-closed startup**",
            "One `FramedResultTransferStore` is shared",
        ):
            self.assertIn(required_text, text)

    def test_credentials_are_files_and_rotation_is_restart_based(self) -> None:
        text = adr_text()
        for required_text in (
            "no credential service",
            "private keys never enter Git, an environment variable or a unit file",
            "**Rotation and revocation are restart-based and atomic.**",
            "There is no CRL or OCSP",
            "CA 3650 days, server certificate 397 days, client certificate 90 days",
        ):
            self.assertIn(required_text, text)

    def test_the_code_identity_conflict_is_recorded_and_resolved_in_k13_direction(self) -> None:
        text = adr_text()
        for required_text in (
            "One finding of this gate",
            "`GovernedResultImportService._validate_admission`",
            "v1 pins the deck to the server's commit.",
            "ADR-0068 §8's allowance for a deck on a different commit",
            "is **not exercised** in v1",
            "a deck workload that must run a commit other than the server's",
        ):
            self.assertIn(required_text, text)

    def test_the_proof_has_two_parts_and_claims_no_handler(self) -> None:
        text = adr_text()
        for required_text in (
            "**Hermetic topology proof**",
            "**Supervised real-environment proof**",
            "the topology may be called\ndeployable only after both",
            "no handler is claimed: the registry is empty",
            "rotation drill",
        ):
            self.assertIn(required_text, text)

    def test_strategy_testing_under_dataset_only_inputs_is_stated(self) -> None:
        text = adr_text()
        for required_text in (
            "### 11. How a strategy is tested while only dataset selectors are admissible",
            "changes no decision above",
            "do not use `admit` or `deliver`",
            "The server never executes the strategy or the provider.",
            "a precomputed, server-side feature artifact as an input",
            "is proved by `S05` and `S07`, not by\nthis ADR",
        ):
            self.assertIn(required_text, text)

    def test_omega_and_handlers_stay_out_of_scope(self) -> None:
        text = adr_text()
        for required_text in (
            "### 8. Omega",
            "Out of scope.",
            "needs its own ADR-0067 amendment",
            "### 9. Data-local handlers",
            "`S08` is deferable",
            "this ADR authorizes none",
        ):
            self.assertIn(required_text, text)


if __name__ == "__main__":
    unittest.main()
