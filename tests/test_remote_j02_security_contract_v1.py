"""Regression guard for the accepted remote J02 security design contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemoteJ02SecurityContractV1Tests(unittest.TestCase):
    def test_accepted_adr_requires_mtls_and_fails_closed_before_c03(self) -> None:
        adr = (ROOT / "docs/decisions/ADR-0063-remote-j02-security-v1.md").read_text(
            encoding="utf-8"
        )

        for required_text in (
            "**Status:** ACCEPTED",
            "TLS 1.3 or later",
            "`CERT_REQUIRED`",
            "`j02.market_data.read`",
            "before `decode_transport_query`",
            "WebSocket policy close `1008`",
            "no plaintext fallback",
            "generic identity-provider, account, session, or bearer\n"
            "token system",
        ):
            self.assertIn(required_text, adr)

    def test_contract_and_transport_docstring_reference_the_future_security_boundary(self) -> None:
        contract = (ROOT / "docs/contracts/CONSUMER_API.md").read_text(encoding="utf-8")
        transport = (ROOT / "src/quant_platform/application/api_transport_server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("[ADR-0063]", contract)
        self.assertIn("must not reinterpret a `ConsumerErrorCode`", contract)
        self.assertIn("ADR-0063 defines the future non-loopback", transport)
