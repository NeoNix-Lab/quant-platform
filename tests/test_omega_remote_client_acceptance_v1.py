#!/usr/bin/env python3
"""ADR-0067 s5 -- minimum acceptance proof for the J15 Omega remote client.

Runs a real J09-configured WSS/mTLS ``api_transport_server`` listener (real
self-signed certificate chain generated with the system ``openssl`` binary,
real TLS 1.3 mutual-TLS handshake, real ``websockets`` client/server) and
proves, against the real `clients/omega/j15_remote_client.py` adapter:

1. a real ``j02-request-v1`` request's decoded remote result (identity,
   provenance, coverage, data) and typed-error behavior match the same
   server-owned C03 execution run directly, in-process;
2. an intentionally mismatched response family is rejected as
   ``wire_incompatible``, with no downgrade or public-Python fallback;
3. an untrusted, expired, revoked, or insufficiently scoped credential
   cannot create an application session or yield a Consumer API error --
   only ``remote_security_failure``;
4. Omega renders the server-issued identities rather than creating
   canonical data or accepted-artifact identities of its own.

This is the bounded remote-contract proof ADR-0067 asks for, not the later
Golden E2E: item 2 (wire-family mismatch) is proven with a fake connector
over the real response-parsing code path, exactly as
``tests/test_omega_remote_client_v1.py`` already does, because the real J09
server only ever emits well-formed ``j02-response-v1`` envelopes and cannot
itself be made to emit a different wire family without reaching into its
internals.
"""

from __future__ import annotations

import ast
from pathlib import Path
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "clients" / "omega"))

import websockets  # noqa: E402

from quant_platform.application import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    encode_consumer_result,
    execute_market_data_query,
)
from quant_platform.application.api_transport_server import (  # noqa: E402
    RemoteJ02Principal,
    RemoteJ02SecurityConfig,
    RemoteJ02SecurityEvidenceLog,
    certificate_fingerprint,
    handle_api_transport_connection,
)

from test_application_market_data_result_v1 import covered_gateway, query  # noqa: E402
from test_bounded_datagateway_read_v1 import LEFT, RIGHT, trade  # noqa: E402

import j15_remote_client as client  # noqa: E402


def batches():
    return {
        LEFT.rel_path: [(trade("2024-01-01T00:10:00Z", "1"),)],
        RIGHT.rel_path: [(trade("2024-01-01T01:10:00Z", "2"),)],
    }


def _run_openssl(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(["openssl", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args)} failed: {result.stderr}")


def _generate_ec_key(cwd: Path, name: str) -> None:
    _run_openssl(["ecparam", "-genkey", "-name", "prime256v1", "-out", name], cwd=cwd)


def _self_signed_ca(cwd: Path, *, key: str, out: str, cn: str) -> None:
    _run_openssl(["req", "-x509", "-new", "-key", key, "-days", "3", "-out", out, "-subj", f"/CN={cn}"], cwd=cwd)


def _signed_certificate(
    cwd: Path,
    *,
    key: str,
    csr: str,
    cn: str,
    ca_cert: str,
    ca_key: str,
    out: str,
    extfile: str | None = None,
    not_before: str | None = None,
    not_after: str | None = None,
) -> None:
    _run_openssl(["req", "-new", "-key", key, "-out", csr, "-subj", f"/CN={cn}"], cwd=cwd)
    args = ["x509", "-req", "-in", csr, "-CA", ca_cert, "-CAkey", ca_key, "-CAcreateserial", "-out", out]
    if not_before is not None and not_after is not None:
        args += ["-not_before", not_before, "-not_after", not_after]
    else:
        args += ["-days", "3"]
    if extfile is not None:
        args += ["-extfile", extfile]
    _run_openssl(args, cwd=cwd)


def _certificate_fingerprint(cwd: Path, certificate_file: str) -> str:
    der = subprocess.run(
        ["openssl", "x509", "-in", str(cwd / certificate_file), "-outform", "DER"],
        capture_output=True,
        check=True,
    ).stdout
    return certificate_fingerprint(der)


@unittest.skipUnless(shutil.which("openssl"), "openssl binary is required to generate a real mTLS fixture")
class OmegaRemoteClientAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="j15_acceptance_")
        cwd = Path(cls._tmp.name)
        cls.cwd = cwd

        _generate_ec_key(cwd, "ca.key")
        _self_signed_ca(cwd, key="ca.key", out="ca.crt", cn="Test CA")

        _generate_ec_key(cwd, "server.key")
        (cwd / "server_ext.cnf").write_text("subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        _signed_certificate(
            cwd, key="server.key", csr="server.csr", cn="127.0.0.1",
            ca_cert="ca.crt", ca_key="ca.key", out="server.crt", extfile="server_ext.cnf",
        )

        _generate_ec_key(cwd, "client_trusted.key")
        _signed_certificate(
            cwd, key="client_trusted.key", csr="client_trusted.csr", cn="omega-test-client-trusted",
            ca_cert="ca.crt", ca_key="ca.key", out="client_trusted.crt",
        )

        _generate_ec_key(cwd, "client_expired.key")
        _signed_certificate(
            cwd, key="client_expired.key", csr="client_expired.csr", cn="omega-test-client-expired",
            ca_cert="ca.crt", ca_key="ca.key", out="client_expired.crt",
            not_before="20200101000000Z", not_after="20200102000000Z",
        )

        _generate_ec_key(cwd, "other_ca.key")
        _self_signed_ca(cwd, key="other_ca.key", out="other_ca.crt", cn="Other CA")
        _generate_ec_key(cwd, "client_untrusted.key")
        _signed_certificate(
            cwd, key="client_untrusted.key", csr="client_untrusted.csr", cn="omega-test-client-untrusted",
            ca_cert="other_ca.crt", ca_key="other_ca.key", out="client_untrusted.crt",
        )

        cls.trusted_fingerprint = _certificate_fingerprint(cwd, "client_trusted.crt")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _path(self, name: str) -> str:
        return str(self.cwd / name)

    def _security(self, *, scopes: frozenset[str], revoked: bool = False) -> RemoteJ02SecurityConfig:
        return RemoteJ02SecurityConfig(
            server_certificate_path=self._path("server.crt"),
            server_private_key_path=self._path("server.key"),
            client_trust_anchor_path=self._path("ca.crt"),
            server_trust_bundle_version="test-bundle-v1",
            authorization_policy_version="test-policy-v1",
            principals_by_fingerprint={
                self.trusted_fingerprint: RemoteJ02Principal(
                    principal_id="omega-acceptance-test", scopes=scopes, revoked=revoked
                ),
            },
            evidence_log=RemoteJ02SecurityEvidenceLog(),
        )

    def _client_ssl(self, *, certificate: str, key: str) -> ssl.SSLContext:
        return client.build_client_ssl_context(
            client_certificate_path=self._path(certificate),
            client_private_key_path=self._path(key),
            server_trust_anchor_path=self._path("ca.crt"),
        )

    async def _serve(self, security: RemoteJ02SecurityConfig, execute):
        return websockets.serve(
            lambda websocket: handle_api_transport_connection(websocket, execute=execute, remote_security=security),
            "127.0.0.1",
            0,
            ssl=security.build_server_ssl_context(),
        )

    # -- 1. real round trip matches direct in-process C03 execution --------

    async def test_real_wss_mtls_round_trip_matches_direct_c03_execution(self):
        direct = execute_market_data_query(query(), gateway=covered_gateway(batches()))
        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}))
        trusted_ssl = self._client_ssl(certificate="client_trusted.crt", key="client_trusted.key")

        async with (await self._serve(security, lambda _query: direct)) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(
                venue="bybit", instrument="BTCUSDT",
                start="2024-01-01T00:00:00Z", end="2024-01-01T02:00:00Z",
            )
            result = await client.fetch_market_data(
                f"wss://127.0.0.1:{port}", request, ssl_context=trusted_ssl
            )

        expected = encode_consumer_result(direct)
        self.assertEqual(expected["request_identity"], result.request_identity)
        self.assertEqual(expected["row_count"], result.row_count)
        self.assertEqual(expected["data"], list(result.data))
        self.assertEqual(expected["coverage"], dict(result.coverage))
        self.assertEqual(expected["provenance"], dict(result.provenance))
        self.assertEqual(expected["requested_interval"], dict(result.requested_interval))

    async def test_real_wss_mtls_round_trip_carries_consumer_api_error_losslessly(self):
        def execute(_query):
            raise ConsumerApiError(
                ConsumerErrorCode.SOURCE_NOT_FOUND, "no such venue",
                context={"venue": "nowhere"}, request_identity="server-request-id",
            )

        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}))
        trusted_ssl = self._client_ssl(certificate="client_trusted.crt", key="client_trusted.key")

        async with (await self._serve(security, execute)) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
            with self.assertRaises(client.RemoteConsumerApiError) as caught:
                await client.fetch_market_data(f"wss://127.0.0.1:{port}", request, ssl_context=trusted_ssl)

        error = caught.exception
        self.assertEqual("source_not_found", error.code)
        self.assertEqual("no such venue", error.message)
        self.assertEqual({"venue": "nowhere"}, dict(error.context))
        self.assertEqual("server-request-id", error.request_identity)

    # -- 2. mismatched wire family: no downgrade, no probing ----------------

    async def test_mismatched_response_family_is_rejected_as_wire_incompatible(self):
        class _FakeConnection:
            def __init__(self, reply: str):
                self._reply = reply

            async def send(self, _message: str) -> None:
                return None

            async def recv(self) -> str:
                return self._reply

            async def __aenter__(self) -> "_FakeConnection":
                return self

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        calls = {"count": 0}
        reply = json.dumps({
            "schema_version": "j14-framed-result-v1",
            "transfer_id": "transfer-1",
            "status": "ok",
            "result": {},
        })

        def connect(_url: str, **_kwargs: object) -> _FakeConnection:
            calls["count"] += 1
            return _FakeConnection(reply)

        request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
        with self.assertRaises(client.WireIncompatible):
            await client.fetch_market_data("wss://ignored", request, ssl_context=None, connect=connect)
        self.assertEqual(1, calls["count"], "a wire-family mismatch must not be retried")

    # -- 3. untrusted / expired / revoked / insufficiently scoped -----------

    async def test_untrusted_certificate_authority_cannot_create_a_session(self):
        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}))
        untrusted_ssl = self._client_ssl(certificate="client_untrusted.crt", key="client_untrusted.key")

        async with (await self._serve(security, lambda _query: (_ for _ in ()).throw(AssertionError("no session")))) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
            with self.assertRaises(client.RemoteSecurityFailure):
                await client.fetch_market_data(f"wss://127.0.0.1:{port}", request, ssl_context=untrusted_ssl)

    async def test_expired_certificate_cannot_create_a_session(self):
        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}))
        expired_ssl = self._client_ssl(certificate="client_expired.crt", key="client_expired.key")

        async with (await self._serve(security, lambda _query: (_ for _ in ()).throw(AssertionError("no session")))) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
            with self.assertRaises(client.RemoteSecurityFailure):
                await client.fetch_market_data(f"wss://127.0.0.1:{port}", request, ssl_context=expired_ssl)

    async def test_revoked_principal_cannot_create_a_session(self):
        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}), revoked=True)
        trusted_ssl = self._client_ssl(certificate="client_trusted.crt", key="client_trusted.key")

        async with (await self._serve(security, lambda _query: (_ for _ in ()).throw(AssertionError("no session")))) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
            with self.assertRaises(client.RemoteSecurityFailure):
                await client.fetch_market_data(f"wss://127.0.0.1:{port}", request, ssl_context=trusted_ssl)

    async def test_insufficiently_scoped_principal_cannot_create_a_session(self):
        security = self._security(scopes=frozenset())
        trusted_ssl = self._client_ssl(certificate="client_trusted.crt", key="client_trusted.key")

        async with (await self._serve(security, lambda _query: (_ for _ in ()).throw(AssertionError("no session")))) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(venue="x", instrument="y", start="s", end="e")
            with self.assertRaises(client.RemoteSecurityFailure):
                await client.fetch_market_data(f"wss://127.0.0.1:{port}", request, ssl_context=trusted_ssl)

    # -- 4. Omega renders identities; it owns none of its own ---------------

    async def test_omega_renders_server_identities_and_owns_no_canonical_state(self):
        direct = execute_market_data_query(query(), gateway=covered_gateway(batches()))
        security = self._security(scopes=frozenset({client.J02_MARKET_DATA_READ_SCOPE}))
        trusted_ssl = self._client_ssl(certificate="client_trusted.crt", key="client_trusted.key")

        async with (await self._serve(security, lambda _query: direct)) as server:
            port = server.sockets[0].getsockname()[1]
            request = client.build_market_data_request(
                venue="bybit", instrument="BTCUSDT",
                start="2024-01-01T00:00:00Z", end="2024-01-01T02:00:00Z",
            )
            result = await client.fetch_market_data(
                f"wss://127.0.0.1:{port}", request, ssl_context=trusted_ssl
            )

        self.assertEqual(direct.request_identity, result.request_identity)
        self.assertEqual(
            direct.provenance.dataset_identity.stable_dict(),
            dict(result.provenance)["dataset_identity"],
        )

        source = (ROOT / "clients" / "omega" / "j15_remote_client.py").read_text(encoding="utf-8")
        imported_roots = {
            (alias.name if isinstance(node, ast.Import) else node.module or "").split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertEqual(set(), imported_roots & {"quant_platform", "sqlite3", "psycopg", "pyarrow"})


if __name__ == "__main__":
    unittest.main()
