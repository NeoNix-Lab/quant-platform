"""Regression guard for ADR-0067's narrow J15 remote-client decision."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADR = (ROOT / "docs" / "decisions" / "ADR-0067-omega-remote-client-contract-v1.md").read_text(
    encoding="utf-8"
)
CONTRACT = (ROOT / "docs" / "contracts" / "CONSUMER_API.md").read_text(encoding="utf-8")
PUBLIC_PYTHON_API = (ROOT / "docs" / "contracts" / "PUBLIC_PYTHON_API.md").read_text(
    encoding="utf-8"
)


class OmegaRemoteClientContractV1Tests(unittest.TestCase):
    def test_j15_has_one_remote_boundary_and_no_package_fallback(self) -> None:
        self.assertIn("J02 is the sole J15 remote boundary", ADR)
        self.assertIn("`j02-request-v1`", ADR)
        self.assertIn("`j02-response-v1`", ADR)
        self.assertIn("must not select between a local package", ADR)
        self.assertIn("call and J02", ADR)
        self.assertIn("package-consumer contract, not the", PUBLIC_PYTHON_API)
        self.assertIn("Consumer API/transport contract", PUBLIC_PYTHON_API)

    def test_compatibility_and_security_failures_are_not_consumer_errors(self) -> None:
        self.assertIn("typed `wire_incompatible` failure", ADR)
        self.assertIn("must not be retried with a", ADR)
        self.assertIn("different wire family", ADR)
        self.assertIn("`quant_platform.__version__`", ADR)
        self.assertIn("typed `remote_security_failure`", ADR)
        self.assertIn("must not convert them into `ConsumerApiError`", ADR)

    def test_j15_remains_read_only_and_server_authoritative(self) -> None:
        self.assertIn("`j02.market_data.read`", ADR)
        self.assertIn("never becomes an authority for canonical data", ADR)
        self.assertIn("real WSS connection", ADR)
        self.assertIn("Omega J15 remote client contract", CONTRACT)
        self.assertIn("no local fallback, mixed execution", CONTRACT)


if __name__ == "__main__":
    unittest.main()
