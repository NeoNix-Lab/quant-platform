"""Credential and trust operations under ``infra/pki`` (issue #393, ADR-0072 section 4).

The scripts are run for real (``bash`` and ``openssl``) in a scratch directory outside the repository; what is
checked is the chain they issue, the fingerprint they print, the validity and key properties the ADR fixes,
and that mutual TLS 1.3 over those credentials behaves as the principals file expects.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import websockets  # noqa: E402

from quant_platform.application.api_transport_server import (  # noqa: E402
    J02_REMOTE_SCOPES,
    RemoteJ02Principal,
    RemoteJ02SecurityConfig,
    RemoteJ02SecurityEvidenceLog,
    _validate_certificate_fingerprint,
    certificate_fingerprint,
    handle_api_transport_connection,
)
from quant_platform.application.strategy_transport import strategy_family_handlers  # noqa: E402

PKI = ROOT / "infra" / "pki"
FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


def find_bash() -> str | None:
    """Git's or the system's bash, never the WSL launcher."""
    candidates = [os.environ.get("GIT_BASH", ""), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and "system32" not in candidate.lower():
            return candidate
    return None


BASH = find_bash()
OPENSSL = shutil.which("openssl")


def posix(path: Path) -> str:
    return path.as_posix()


def run(script: str, *arguments, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [BASH, posix(PKI / script), *map(str, arguments)], capture_output=True, text=True, env=env,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{script} {arguments} failed: {result.stderr}")
    return result


def fields(result: subprocess.CompletedProcess) -> dict[str, str]:
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def openssl(*arguments) -> str:
    return subprocess.run([OPENSSL, *map(str, arguments)], capture_output=True, text=True, check=True).stdout


def validity_days(certificate: Path) -> int:
    dates = dict(line.split("=", 1) for line in openssl("x509", "-in", certificate, "-noout", "-dates").splitlines())
    seconds = ssl.cert_time_to_seconds(dates["notAfter"]) - ssl.cert_time_to_seconds(dates["notBefore"])
    return round(seconds / 86400)


@unittest.skipUnless(BASH and OPENSSL, "bash and openssl are required to run the PKI scripts")
class PkiOperationsV1Tests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="pki_ops_")
        cls.pki = Path(cls._tmp.name) / "pki"
        cls.pki.mkdir()
        cls.other = Path(cls._tmp.name) / "other"
        cls.other.mkdir()
        cls.ca = fields(run("ca-init.sh", posix(cls.pki)))
        cls.server = fields(run("issue-server.sh", posix(cls.pki), "api", "DNS:localhost,IP:127.0.0.1", "t1"))
        cls.deck_a = fields(run("issue-client.sh", posix(cls.pki), "deck-a", "t1"))
        cls.deck_b = fields(run("issue-client.sh", posix(cls.pki), "deck-b", "t1"))
        run("ca-init.sh", posix(cls.other), "other-ca")
        run("issue-server.sh", posix(cls.other), "api", "DNS:localhost,IP:127.0.0.1", "t1")
        run("issue-client.sh", posix(cls.other), "deck-x", "t1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # paths by layout (the printed paths use the shell's own notation)
    def ca_crt(self, base=None) -> Path:
        return (base or self.pki) / "ca" / "ca.crt"

    def server_files(self, base=None):
        directory = (base or self.pki) / "servers" / "api" / "t1"
        return directory / "server.crt", directory / "server.key"

    def client_files(self, principal, base=None):
        directory = (base or self.pki) / "clients" / principal / "t1"
        return directory / "client.crt", directory / "client.key"

    # -- the chain, the fingerprint, the validity -----------------------------------------

    def test_the_issued_credentials_chain_to_the_ca(self) -> None:
        for certificate in (self.server_files()[0], self.client_files("deck-a")[0], self.client_files("deck-b")[0]):
            out = openssl("verify", "-CAfile", self.ca_crt(), certificate)
            self.assertIn("OK", out)

    def test_a_certificate_of_another_ca_does_not_chain(self) -> None:
        for certificate in (self.client_files("deck-x", self.other)[0], self.server_files(self.other)[0]):
            result = subprocess.run(
                [OPENSSL, "verify", "-CAfile", str(self.ca_crt()), str(certificate)], capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)

    def test_the_printed_fingerprint_is_the_sha256_of_the_der_certificate(self) -> None:
        certificate = self.client_files("deck-a")[0]
        printed = self.deck_a["fingerprint"]
        self.assertRegex(printed, FINGERPRINT.pattern)
        der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
        self.assertEqual(certificate_fingerprint(der), printed)
        self.assertEqual(printed, run("fingerprint.sh", posix(certificate)).stdout.strip())
        _validate_certificate_fingerprint(printed)  # the format the API accepts
        self.assertNotEqual(printed, self.deck_b["fingerprint"])

    def test_the_validities_are_the_adr_values(self) -> None:
        self.assertEqual(3650, validity_days(self.ca_crt()))
        self.assertEqual(397, validity_days(self.server_files()[0]))
        self.assertEqual(90, validity_days(self.client_files("deck-a")[0]))
        self.assertEqual(
            ("3650", "397", "90"), (self.ca["ca_valid_days"], self.server["server_valid_days"], self.deck_a["client_valid_days"]),
        )

    def test_every_key_is_ec_p256(self) -> None:
        for key in (self.pki / "ca" / "ca.key", self.server_files()[1], self.client_files("deck-a")[1]):
            self.assertRegex(openssl("ec", "-in", key, "-noout", "-text"), r"prime256v1|P-256")
        for certificate in (self.ca_crt(), self.server_files()[0], self.client_files("deck-a")[0]):
            self.assertRegex(openssl("x509", "-in", certificate, "-noout", "-text"), r"prime256v1|P-256")

    def test_the_certificates_carry_the_usages_the_roles_need(self) -> None:
        ca = openssl("x509", "-in", self.ca_crt(), "-noout", "-text")
        self.assertIn("CA:TRUE", ca)
        self.assertIn("Certificate Sign", ca)
        server = openssl("x509", "-in", self.server_files()[0], "-noout", "-text")
        self.assertIn("CA:FALSE", server)
        self.assertIn("TLS Web Server Authentication", server)
        self.assertNotIn("TLS Web Client Authentication", server)
        self.assertIn("IP Address:127.0.0.1", server)
        self.assertIn("DNS:localhost", server)
        client = openssl("x509", "-in", self.client_files("deck-a")[0], "-noout", "-text")
        self.assertIn("CA:FALSE", client)
        self.assertIn("TLS Web Client Authentication", client)
        self.assertNotIn("TLS Web Server Authentication", client)
        self.assertNotIn("Subject Alternative Name", client)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not enforced on this filesystem")
    def test_private_keys_are_readable_by_their_owner_only(self) -> None:
        for key in (self.pki / "ca" / "ca.key", self.server_files()[1], self.client_files("deck-a")[1]):
            self.assertEqual(0o600, key.stat().st_mode & 0o777, key)

    def test_every_certificate_has_its_own_random_128_bit_serial(self) -> None:
        serials = []
        for certificate in (
            self.ca_crt(), self.server_files()[0], self.client_files("deck-a")[0], self.client_files("deck-b")[0],
        ):
            serial = openssl("x509", "-in", certificate, "-noout", "-serial").strip().split("=", 1)[1]
            self.assertGreaterEqual(len(serial.lstrip("0")), 28, serial)  # not a counter
            serials.append(serial)
        self.assertEqual(len(serials), len(set(serials)))

    # -- refusals -----------------------------------------------------------------------

    def test_the_scripts_refuse_a_directory_inside_a_git_work_tree(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as inside:
            result = run("ca-init.sh", posix(Path(inside)), check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Git work tree", result.stderr)
            self.assertEqual([], list(Path(inside).iterdir()))

    def test_concurrent_issues_with_one_label_leave_exactly_one_intact_credential(self) -> None:
        base = Path(self._tmp.name) / "race"
        base.mkdir()
        run("ca-init.sh", posix(base))
        for script, arguments, files in (
            ("issue-client.sh", ("deck-r", "same"), ("client.crt", "client.key")),
            ("issue-server.sh", ("api-r", "DNS:localhost", "same"), ("server.crt", "server.key")),
        ):
            with self.subTest(script):
                processes = [
                    subprocess.Popen(
                        [BASH, posix(PKI / script), posix(base), *arguments], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                    )
                    for _ in range(8)
                ]
                results = [(process.wait(), *process.communicate()) for process in processes]
                winners = [stdout for code, stdout, _ in results if code == 0]
                self.assertEqual(1, len(winners), [r[0] for r in results])
                for code, _, stderr in results:
                    if code != 0:
                        self.assertIn("refusing to overwrite", stderr)
                kind = "clients" if script == "issue-client.sh" else "servers"
                directory = base / kind / arguments[0] / arguments[-1]
                certificate, key = directory / files[0], directory / files[1]
                derived = openssl("ec", "-in", key, "-pubout")
                self.assertEqual(derived, openssl("x509", "-in", certificate, "-noout", "-pubkey"))
                self.assertIn("OK", openssl("verify", "-CAfile", self.ca_crt(base), certificate))
                if script == "issue-client.sh":
                    printed = dict(line.split("=", 1) for line in winners[0].splitlines() if "=" in line)
                    self.assertEqual(printed["fingerprint"], run("fingerprint.sh", posix(certificate)).stdout.strip())

    def symlink_to(self, link: Path, target: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links cannot be created here")
        if not link.is_symlink():
            self.skipTest("symbolic links cannot be created here")

    def test_a_link_inside_the_pki_directory_cannot_lead_the_keys_into_git(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as inside:
            tracked = Path(inside)
            for script, arguments, kind, name in (
                ("issue-client.sh", ("deck-l", "t1"), "clients", "deck-l"),
                ("issue-server.sh", ("api-l", "DNS:localhost", "t1"), "servers", "api-l"),
            ):
                for level in ("kind", "name"):
                    with self.subTest(script=script, level=level):
                        base = Path(self._tmp.name) / f"link_{kind}_{level}"
                        base.mkdir()
                        run("ca-init.sh", posix(base))
                        if level == "kind":
                            self.symlink_to(base / kind, tracked)
                        else:
                            (base / kind).mkdir()
                            self.symlink_to(base / kind / name, tracked)
                        result = run(script, posix(base), *arguments, check=False)
                        self.assertNotEqual(0, result.returncode)
                        self.assertIn("symbolic link", result.stderr)
                        self.assertEqual([], list(tracked.iterdir()))

    def test_a_pki_directory_that_is_a_link_into_git_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as inside:
            link = Path(self._tmp.name) / "pki_link"
            self.symlink_to(link, Path(inside))
            result = run("ca-init.sh", posix(link), check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Git work tree", result.stderr)
            self.assertEqual([], list(Path(inside).iterdir()))

    def test_an_ancestor_with_a_git_entry_is_refused_even_when_git_does_not_recognise_it(self) -> None:
        outer = Path(self._tmp.name) / "damaged"
        (outer / ".git").mkdir(parents=True)  # not a valid repository: git itself says "not a git repository"
        base = outer / "pki"
        base.mkdir()
        result = run("ca-init.sh", posix(base), check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Git work tree", result.stderr)
        self.assertEqual([], list(base.iterdir()))

    def fake_git(self, body: str) -> dict:
        directory = Path(self._tmp.name) / f"fakegit_{abs(hash(body))}"
        directory.mkdir(exist_ok=True)
        script = directory / "git"
        script.write_bytes(("#!/bin/sh\n" + body + "\n").encode("utf-8"))
        script.chmod(0o755)
        return {**os.environ, "PKI_GIT": posix(script)}

    def test_a_git_that_cannot_answer_is_a_refusal_not_a_pass(self) -> None:
        for body in ("echo 'fatal: something else broke' >&2; exit 128", "echo boom >&2; exit 2", "exit 0"):
            with self.subTest(body):
                base = Path(self._tmp.name) / f"gitfail_{abs(hash(body))}"
                base.mkdir()
                result = run("ca-init.sh", posix(base), check=False, env=self.fake_git(body))
                self.assertNotEqual(0, result.returncode)
                self.assertTrue("cannot establish" in result.stderr or "Git work tree" in result.stderr, result.stderr)
                self.assertEqual([], list(base.iterdir()))

    def test_a_git_that_says_not_a_repository_lets_a_plain_directory_through(self) -> None:
        base = Path(self._tmp.name) / "gitplain"
        base.mkdir()
        env = self.fake_git("echo 'fatal: not a git repository (or any of the parent directories): .git' >&2; exit 128")
        self.assertEqual(0, run("ca-init.sh", posix(base), env=env).returncode)

    def test_the_scripts_never_overwrite_a_credential(self) -> None:
        for arguments in (
            ("ca-init.sh", posix(self.pki)),
            ("issue-server.sh", posix(self.pki), "api", "DNS:localhost", "t1"),
            ("issue-client.sh", posix(self.pki), "deck-a", "t1"),
        ):
            result = run(*arguments, check=False)
            self.assertNotEqual(0, result.returncode, arguments)
            self.assertIn("refusing to overwrite", result.stderr)

    def test_the_scripts_refuse_bad_names_addresses_and_missing_parts(self) -> None:
        cases = [
            ("issue-client.sh", posix(self.pki), "../escape", "t2"),
            ("issue-client.sh", posix(self.pki), "deck c", "t2"),
            ("issue-client.sh", posix(self.pki), "deck-c", "../t"),
            ("issue-server.sh", posix(self.pki), "api", "localhost", "t2"),
            ("issue-server.sh", posix(self.pki), "api", "DNS:a;rm", "t2"),
            ("issue-client.sh", posix(Path(self._tmp.name) / "absent"), "deck-c", "t2"),
            ("issue-client.sh", posix(self.pki)),
            ("fingerprint.sh", posix(self.pki / "absent.crt")),
        ]
        for arguments in cases:
            with self.subTest(arguments):
                self.assertNotEqual(0, run(*arguments, check=False).returncode)
        absent = run("issue-client.sh", posix(Path(self._tmp.name) / "absent"), "deck-c", "t2", check=False)
        self.assertIn("does not exist", absent.stderr)
        empty = Path(self._tmp.name) / "no_ca"
        empty.mkdir()
        for arguments, created in (
            (("issue-client.sh", posix(empty), "deck-c", "t2"), empty / "clients"),
            (("issue-server.sh", posix(empty), "api", "DNS:localhost", "t2"), empty / "servers"),
        ):
            result = run(*arguments, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("no CA", result.stderr)
            self.assertFalse(created.exists(), created)  # nothing is left behind by a refusal

    def test_a_subject_alt_name_cannot_inject_extensions(self) -> None:
        injected = "DNS:a\nextendedKeyUsage = clientAuth\nbasicConstraints = critical,CA:true"
        result = run("issue-server.sh", posix(self.pki), "api2", injected, "t1", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("subject-alt-names", result.stderr)
        self.assertFalse((self.pki / "servers" / "api2").exists())

    # -- the repository holds no key and no real fingerprint ---------------------------------

    def test_no_private_key_material_is_tracked_by_git(self) -> None:
        listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        if listing.returncode != 0:
            self.skipTest("not a Git checkout")
        marker = "PRIVATE " + "KEY-----"
        offenders = []
        for name in listing.stdout.splitlines():
            path = ROOT / name
            if path.is_file() and path.stat().st_size < 2_000_000:
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if marker in text:
                    offenders.append(name)
        self.assertEqual([], offenders)

    def test_the_example_principals_file_has_no_fingerprints_and_is_not_loadable_as_is(self) -> None:
        text = (PKI / "principals.example.json").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[0-9a-f]{64}", text))
        document = json.loads(text)
        self.assertEqual({"version", "principals"}, set(document))
        self.assertEqual(1, document["version"])
        for principal in document["principals"]:
            self.assertEqual({"principal_id", "fingerprint", "scopes"}, set(principal))
            self.assertTrue(set(principal["scopes"]) <= J02_REMOTE_SCOPES)
            with self.assertRaises(ValueError):
                _validate_certificate_fingerprint(principal["fingerprint"])

    # -- mutual TLS 1.3 over the issued credentials ------------------------------------------

    def server_context(self, base=None) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        certificate, key = self.server_files(base)
        context.load_cert_chain(str(certificate), str(key))
        context.load_verify_locations(cafile=str(self.ca_crt()))
        return context

    def client_context(self, principal="deck-a", *, trust=None, base=None) -> ssl.SSLContext:
        # The default context is strict (Python 3.13+ also checks the CA's key usage).
        context = ssl.create_default_context(cafile=str(trust or self.ca_crt()))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        certificate, key = self.client_files(principal, base)
        context.load_cert_chain(str(certificate), str(key))
        return context

    @contextmanager
    def tls_server(self, context):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        outcome = {}

        def serve():
            try:
                connection, _ = listener.accept()
                tls = context.wrap_socket(connection, server_side=True)
            except (ssl.SSLError, OSError) as error:
                outcome["error"] = error  # the handshake was refused
                return
            with tls:
                outcome["peer"] = tls.getpeercert(binary_form=True)
                outcome["version"] = tls.version()
                try:
                    tls.send(b"k")  # the client waits for it: the handshake is complete on both sides
                    tls.recv(1)
                except OSError:
                    pass  # the peer went away after the handshake: not a refusal

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], outcome
        finally:
            listener.close()
            thread.join(10)

    def test_mutual_tls_1_3_works_and_the_server_sees_the_printed_fingerprint(self) -> None:
        with self.tls_server(self.server_context()) as (port, outcome):
            with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
                with self.client_context().wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                    self.assertEqual(b"k", tls.recv(1))
                    tls.send(b"x")
        self.assertNotIn("error", outcome)
        self.assertEqual("TLSv1.3", outcome["version"])
        self.assertEqual(self.deck_a["fingerprint"], certificate_fingerprint(outcome["peer"]))

    def test_a_client_certificate_of_another_ca_is_not_accepted_by_the_server(self) -> None:
        with self.tls_server(self.server_context()) as (port, outcome):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
                    with self.client_context("deck-x", base=self.other).wrap_socket(
                        raw, server_hostname="127.0.0.1",
                    ) as tls:
                        tls.send(b"x")
                        tls.recv(1)
            except (ssl.SSLError, OSError):
                pass
        self.assertIn("error", outcome)

    def test_a_server_certificate_of_another_ca_is_not_accepted_by_the_client(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        certificate, key = self.server_files(self.other)
        context.load_cert_chain(str(certificate), str(key))
        with self.tls_server(context) as (port, _outcome):
            with self.assertRaises(ssl.SSLCertVerificationError):
                with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
                    self.client_context().wrap_socket(raw, server_hostname="127.0.0.1")

    # -- the issued credentials under the host's authorization -------------------------------

    async def probe(self, server, principal):
        port = server.sockets[0].getsockname()[1]
        try:
            async with websockets.connect(f"wss://127.0.0.1:{port}", ssl=self.client_context(principal)) as connection:
                await connection.send(json.dumps({
                    "message_family": "j02-strategy-v1", "schema_version": "j02-strategy-v1-request-v1",
                    "operation": "compose", "request_id": "r1", "request": {},
                }))
                reply = json.loads(await asyncio.wait_for(connection.recv(), 10))
        except websockets.ConnectionClosed as closed:
            return ("closed", closed.rcvd.code if closed.rcvd else None)
        return ("reply", reply.get("status"))

    async def test_only_the_mapped_fingerprint_is_authorized_though_both_chain_to_the_ca(self) -> None:
        certificate, key = self.server_files()
        security = RemoteJ02SecurityConfig(
            server_certificate_path=str(certificate), server_private_key_path=str(key),
            client_trust_anchor_path=str(self.ca_crt()),
            server_trust_bundle_version="test-bundle", authorization_policy_version="test-policy",
            principals_by_fingerprint={
                self.deck_a["fingerprint"]: RemoteJ02Principal("deck-a", frozenset({"j02.strategy.compose"})),
            },
            evidence_log=RemoteJ02SecurityEvidenceLog(),
        )
        handlers = strategy_family_handlers()
        server = websockets.serve(
            lambda websocket: handle_api_transport_connection(
                websocket, execute=MagicMock(), remote_security=security, family_handlers=handlers,
            ),
            "127.0.0.1", 0, ssl=security.build_server_ssl_context(),
        )
        async with server as running:
            self.assertEqual(("reply", "error"), await self.probe(running, "deck-a"))
            self.assertEqual(("closed", 1008), await self.probe(running, "deck-b"))
        decisions = {(item.principal_id, item.decision) for item in security.evidence_log.events}
        self.assertIn(("deck-a", "allow"), decisions)
        self.assertIn((None, "deny"), decisions)


if __name__ == "__main__":
    unittest.main()
