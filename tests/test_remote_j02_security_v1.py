"""Implementation proof for ADR-0063's remote J02 TLS/mTLS security seam."""

from __future__ import annotations

from pathlib import Path
import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.api_transport_server as transport  # noqa: E402
from quant_platform.application.api_transport_server import (  # noqa: E402
    ApiTransportServerConfig,
    J02_MARKET_DATA_READ_SCOPE,
    RemoteJ02Principal,
    RemoteJ02SecurityConfig,
    RemoteJ02SecurityEvidenceLog,
    certificate_fingerprint,
    handle_api_transport_connection,
)


CERTIFICATE_DER = b"client-certificate-der-for-test"


def security(*, principal: RemoteJ02Principal | None = None) -> RemoteJ02SecurityConfig:
    fingerprint = certificate_fingerprint(CERTIFICATE_DER)
    return RemoteJ02SecurityConfig(
        server_certificate_path="server.pem",
        server_private_key_path="server-key.pem",
        client_trust_anchor_path="client-ca.pem",
        server_trust_bundle_version="trust-v1",
        authorization_policy_version="policy-v1",
        principals_by_fingerprint={
            fingerprint: principal
            or RemoteJ02Principal("deck-a", frozenset({J02_MARKET_DATA_READ_SCOPE}))
        },
        evidence_log=RemoteJ02SecurityEvidenceLog(max_events=2),
    )


class _SslObject:
    def __init__(self, certificate_der: bytes | None):
        self.certificate_der = certificate_der

    def getpeercert(self, *, binary_form: bool = False):
        assert binary_form
        return self.certificate_der

    def version(self) -> str:
        return "TLSv1.3"

    def cipher(self):
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)


class _Transport:
    def __init__(self, certificate_der: bytes | None):
        self.ssl_object = _SslObject(certificate_der)

    def get_extra_info(self, key: str):
        return self.ssl_object if key == "ssl_object" else None


class _Socket:
    def __init__(self, certificate_der: bytes | None, *, on_next=None):
        self.transport = _Transport(certificate_der)
        self.close_calls: list[tuple[int, str]] = []
        self.on_next = on_next
        self._yielded = False

    async def close(self, *, code: int, reason: str) -> None:
        self.close_calls.append((code, reason))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded and self.on_next is not None:
            self._yielded = True
            self.on_next()
            return "ignored-after-revocation"
        raise StopAsyncIteration


class RemoteJ02SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_unmapped_or_insufficient_credential_is_policy_closed_before_decode(self) -> None:
        unmapped = _Socket(b"unmapped-certificate")
        execute = MagicMock()
        configuration = security()

        await handle_api_transport_connection(unmapped, execute=execute, remote_security=configuration)

        self.assertEqual([(1008, "policy denied")], unmapped.close_calls)
        execute.assert_not_called()
        evidence = configuration.evidence_log.events[0]
        self.assertEqual("deny", evidence.decision)
        self.assertEqual(J02_MARKET_DATA_READ_SCOPE, evidence.requested_scope)
        self.assertEqual("policy_denied", evidence.terminal_close_reason)
        self.assertNotIn("unmapped-certificate", str(evidence))

        insufficient = _Socket(CERTIFICATE_DER)
        insufficient_configuration = security(principal=RemoteJ02Principal("deck-a", frozenset()))
        await handle_api_transport_connection(
            insufficient, execute=MagicMock(), remote_security=insufficient_configuration
        )
        self.assertEqual([(1008, "policy denied")], insufficient.close_calls)
        self.assertEqual("deny", insufficient_configuration.evidence_log.events[0].decision)

    async def test_revoked_credential_is_policy_closed_and_authorized_credential_is_recorded(self) -> None:
        revoked = _Socket(CERTIFICATE_DER)
        revoked_configuration = security(
            principal=RemoteJ02Principal("deck-a", frozenset({J02_MARKET_DATA_READ_SCOPE}), revoked=True)
        )
        await handle_api_transport_connection(revoked, execute=MagicMock(), remote_security=revoked_configuration)
        self.assertEqual([(1008, "policy denied")], revoked.close_calls)

        permitted = _Socket(CERTIFICATE_DER)
        permitted_configuration = security()
        await handle_api_transport_connection(permitted, execute=MagicMock(), remote_security=permitted_configuration)
        self.assertEqual([], permitted.close_calls)
        evidence = permitted_configuration.evidence_log.events[0]
        self.assertEqual("allow", evidence.decision)
        self.assertEqual("deck-a", evidence.principal_id)
        self.assertEqual("TLSv1.3", evidence.tls_version)
        self.assertEqual("TLS_AES_256_GCM_SHA384", evidence.tls_cipher)

    async def test_active_session_revocation_closes_before_any_subsequent_decode(self) -> None:
        principals = {certificate_fingerprint(CERTIFICATE_DER): RemoteJ02Principal(
            "deck-a", frozenset({J02_MARKET_DATA_READ_SCOPE})
        )}
        configuration = RemoteJ02SecurityConfig(
            "server.pem", "server-key.pem", "client-ca.pem", "trust-v1", "policy-v1",
            principals, RemoteJ02SecurityEvidenceLog(),
        )
        socket = _Socket(
            CERTIFICATE_DER,
            on_next=lambda: principals.__setitem__(
                certificate_fingerprint(CERTIFICATE_DER),
                RemoteJ02Principal("deck-a", frozenset({J02_MARKET_DATA_READ_SCOPE}), revoked=True),
            ),
        )
        with patch.object(transport, "handle_api_transport_message") as decode:
            await handle_api_transport_connection(socket, execute=MagicMock(), remote_security=configuration)

        decode.assert_not_called()
        self.assertEqual([(1008, "policy denied")], socket.close_calls)


class RemoteJ02TlsConfigurationTests(unittest.TestCase):
    def test_non_loopback_requires_complete_remote_security_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "remote_security"):
            ApiTransportServerConfig(host="0.0.0.0", allow_non_loopback=True)

        configured = ApiTransportServerConfig(
            host="0.0.0.0", allow_non_loopback=True, remote_security=security()
        )
        self.assertEqual("0.0.0.0", configured.host)

    def test_tls_context_requires_tls13_server_chain_and_client_certificate_verification(self) -> None:
        context = MagicMock()
        with patch.object(transport.ssl, "SSLContext", return_value=context) as ssl_context:
            built = security().build_server_ssl_context()

        self.assertIs(context, built)
        ssl_context.assert_called_once_with(transport.ssl.PROTOCOL_TLS_SERVER)
        self.assertEqual(transport.ssl.TLSVersion.TLSv1_3, context.minimum_version)
        self.assertEqual(transport.ssl.CERT_REQUIRED, context.verify_mode)
        context.load_cert_chain.assert_called_once_with("server.pem", "server-key.pem")
        context.load_verify_locations.assert_called_once_with(cafile="client-ca.pem")

    def test_remote_server_passes_tls_context_to_websocket_listener_before_sessions_exist(self) -> None:
        captured: dict[str, object] = {}

        class _ServerContext:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_args):
                return False

        def fake_serve(_handler, _host, _port, **kwargs):
            captured.update(kwargs)
            return _ServerContext()

        stop_event = asyncio.Event()
        stop_event.set()
        ssl_context = object()
        configuration = security()
        server_configuration = ApiTransportServerConfig(
            host="0.0.0.0", allow_non_loopback=True, remote_security=configuration
        )
        with (
            patch.object(
                RemoteJ02SecurityConfig, "build_server_ssl_context", return_value=ssl_context
            ),
            patch.object(transport.websockets, "serve", fake_serve),
        ):
            asyncio.run(
                transport.run_api_transport_server(
                    server_configuration, stop_event=stop_event, execute=MagicMock()
                )
            )

        self.assertIs(ssl_context, captured["ssl"])


if __name__ == "__main__":
    unittest.main()
