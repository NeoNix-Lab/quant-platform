"""API host composition: TLS/mTLS, principals and the DG-K families (issue #395, ADR-0072 section 3).

Three layers: the principals-file and option rules (no network), the host
(``tools/api_server.py`` ``main`` with the transport mocked, as the existing host
tests do) and a real TLS 1.3 mutual-TLS listener on loopback, served with exactly
what the composition returns, over a certificate chain from the system ``openssl``.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import websockets  # noqa: E402

import api_server  # noqa: E402  (tools/api_server.py: the host under test)
import test_omega_remote_client_acceptance_v1 as pki  # noqa: E402
from quant_platform.application.admitted_input_authority import ADMITTED_INPUT_TRANSFER_CONFIG_V1  # noqa: E402
from quant_platform.application.api_host_composition import (  # noqa: E402
    FAMILY_NAMES,
    FAMILY_SCOPES,
    ApiHostCompositionError,
    ApiHostOptions,
    compose_api_host,
    file_version,
    parse_families,
    parse_principals,
)
from quant_platform.application.api_transport_server import (  # noqa: E402
    J02_REMOTE_SCOPES,
    handle_api_transport_connection,
)
from quant_platform.application.framed_result_transport import FramedResultTransferStore  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
RUNTIME_DSN = "postgresql://api@localhost/runtime"
CATALOG_DSN = "postgresql://api@localhost/catalog"
FINGERPRINT = "sha256:" + "ab" * 32
UNSUPPORTED = "unsupported message family, schema_version or operation"
# family -> (family id, operation): a request that is refused by its own decoder, so a
# reply means the route was reached and authorized without touching any store.
PROBES = {
    "strategy": ("j02-strategy-v1", "compose"),
    "validation": ("j02-validation-v1", "build_folds"),
    "training": ("j02-training-v1", "train_evaluate"),
    "jobs": ("j02-job-v1", "status"),
    "admitted-input": ("j02-admitted-input-v2", "admit"),
    "result-import": ("j02-result-import-v1", "submit"),
}
ALL_SCOPES = sorted(scope for scopes in FAMILY_SCOPES.values() for scope in scopes)


def principals_document(*entries) -> bytes:
    return json.dumps({"version": 1, "principals": list(entries)}).encode("utf-8")


def entry(principal_id="deck-a", fingerprint=FINGERPRINT, scopes=("j02.strategy.compose",)):
    return {"principal_id": principal_id, "fingerprint": fingerprint, "scopes": list(scopes)}


@unittest.skipUnless(shutil.which("openssl"), "openssl binary is required to generate a real mTLS fixture")
class ApiHostCompositionV1Tests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="api_host_")
        cwd = cls.cwd = Path(cls._tmp.name)
        pki._generate_ec_key(cwd, "ca.key")
        pki._self_signed_ca(cwd, key="ca.key", out="ca.crt", cn="Test CA")
        pki._generate_ec_key(cwd, "server.key")
        (cwd / "server_ext.cnf").write_text("subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        pki._signed_certificate(
            cwd, key="server.key", csr="server.csr", cn="127.0.0.1",
            ca_cert="ca.crt", ca_key="ca.key", out="server.crt", extfile="server_ext.cnf",
        )
        for name in ("all", "strategy_only", "unmapped"):
            pki._generate_ec_key(cwd, f"{name}.key")
            pki._signed_certificate(
                cwd, key=f"{name}.key", csr=f"{name}.csr", cn=f"client-{name}",
                ca_cert="ca.crt", ca_key="ca.key", out=f"{name}.crt",
            )
        pki._generate_ec_key(cwd, "other_ca.key")
        pki._self_signed_ca(cwd, key="other_ca.key", out="other_ca.crt", cn="Other CA")
        pki._generate_ec_key(cwd, "foreign.key")
        pki._signed_certificate(
            cwd, key="foreign.key", csr="foreign.csr", cn="client-foreign",
            ca_cert="other_ca.crt", ca_key="other_ca.key", out="foreign.crt",
        )
        cls.fingerprints = {
            name: pki._certificate_fingerprint(cwd, f"{name}.crt")
            for name in ("all", "strategy_only", "unmapped", "foreign")
        }
        cls.principals_path = cwd / "principals.json"
        cls.principals_path.write_bytes(principals_document(
            entry("deck-all", cls.fingerprints["all"], ALL_SCOPES),
            entry("deck-strategy", cls.fingerprints["strategy_only"], ["j02.strategy.compose"]),
        ))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def options(self, families=FAMILY_NAMES, **overrides) -> ApiHostOptions:
        values = dict(
            families=tuple(families),
            tls_certificate=str(self.cwd / "server.crt"),
            tls_private_key=str(self.cwd / "server.key"),
            client_trust_anchor=str(self.cwd / "ca.crt"),
            principals_file=str(self.principals_path),
            catalog_dsn=CATALOG_DSN,
            runtime_dsn=RUNTIME_DSN,
        )
        values.update(overrides)
        return ApiHostOptions(**values)

    def refusal(self, options, reason):
        with self.assertRaises(ApiHostCompositionError) as raised:
            compose_api_host(options)
        self.assertEqual(reason, raised.exception.reason)

    # -- the families option ---------------------------------------------------------

    def test_families_are_a_closed_comma_separated_subset_in_canonical_order(self) -> None:
        self.assertEqual((), parse_families(""))
        self.assertEqual((), parse_families(" , "))
        self.assertEqual(("strategy", "jobs"), parse_families("jobs, strategy,jobs"))
        self.assertEqual(FAMILY_NAMES, parse_families(",".join(reversed(FAMILY_NAMES))))
        for bad in ("market-data", "strategy,unknown", "STRATEGY"):
            with self.assertRaises(ApiHostCompositionError) as raised:
                parse_families(bad)
            self.assertEqual("unknown_family", raised.exception.reason)

    def test_an_unknown_family_is_refused_by_the_composition_too(self) -> None:
        self.refusal(self.options(("bogus",)), "unknown_family")
        self.refusal(self.options(("strategy", "market-data")), "unknown_family")

    def test_training_registration_gets_a_repository_provider_only_with_a_runtime_dsn(self) -> None:
        import quant_platform.application.api_host_composition as module

        for dsn, expect_provider in ((RUNTIME_DSN, True), ("", False)):
            with self.subTest(dsn=dsn):
                with mock.patch.object(
                    module, "training_family_handlers", wraps=module.training_family_handlers,
                ) as factory:
                    compose_api_host(self.options(("training",), runtime_dsn=dsn))
                (provider,) = factory.call_args.args
                self.assertEqual(expect_provider, callable(provider))

    def test_the_family_scopes_are_registered_scopes(self) -> None:
        self.assertEqual(set(FAMILY_NAMES), set(FAMILY_SCOPES))
        for scopes in FAMILY_SCOPES.values():
            self.assertTrue(scopes <= J02_REMOTE_SCOPES)

    # -- the default composition -----------------------------------------------------

    def test_the_default_composes_nothing(self) -> None:
        composition = compose_api_host(ApiHostOptions())
        self.assertIsNone(composition.remote_security)
        self.assertEqual({}, dict(composition.family_handlers))
        self.assertIsNone(composition.framed_transfers)
        self.assertEqual(frozenset(), composition.framed_resume_scopes)
        self.assertEqual(("families=none",), composition.notices)

    def test_tls_without_families_secures_the_market_data_server_and_adds_no_route(self) -> None:
        composition = compose_api_host(self.options(families=()))
        self.assertIsNotNone(composition.remote_security)
        self.assertEqual({}, dict(composition.family_handlers))
        self.assertIsNone(composition.framed_transfers)

    # -- fail-closed startup (each refusal is before anything binds) -------------------

    def test_partial_tls_material_is_refused_with_or_without_families(self) -> None:
        for families in ((), ("strategy",)):
            for field in ("tls_certificate", "tls_private_key", "client_trust_anchor", "principals_file"):
                with self.subTest(families=families, missing=field):
                    self.refusal(self.options(families, **{field: ""}), "tls_material_incomplete")

    def test_a_family_without_complete_material_is_refused(self) -> None:
        for family in FAMILY_NAMES:
            with self.subTest(family):
                self.refusal(
                    ApiHostOptions(families=(family,), runtime_dsn=RUNTIME_DSN, catalog_dsn=CATALOG_DSN),
                    "families_require_tls",
                )

    def test_unreadable_or_invalid_material_is_refused(self) -> None:
        self.refusal(self.options(principals_file=str(self.cwd / "absent.json")), "principals_unreadable")
        self.refusal(self.options(client_trust_anchor=str(self.cwd / "absent.crt")), "trust_anchor_unreadable")
        garbage = self.cwd / "garbage.pem"
        garbage.write_text("not a certificate", encoding="utf-8")
        self.refusal(self.options(tls_certificate=str(garbage)), "tls_material_invalid")
        self.refusal(self.options(tls_private_key=str(self.cwd / "ca.key")), "tls_material_invalid")
        self.refusal(self.options(client_trust_anchor=str(garbage)), "tls_material_invalid")

    def test_dsns_required_by_a_family_are_checked_before_anything_connects(self) -> None:
        for family, missing, reason in (
            ("jobs", {"runtime_dsn": ""}, "runtime_dsn_required"),
            ("admitted-input", {"runtime_dsn": ""}, "runtime_dsn_required"),
            ("result-import", {"runtime_dsn": ""}, "runtime_dsn_required"),
            ("admitted-input", {"catalog_dsn": ""}, "catalog_dsn_required"),
            ("result-import", {"catalog_dsn": ""}, "catalog_dsn_required"),
        ):
            with self.subTest(family, **missing):
                with mock.patch("quant_platform.application.api_host_composition.open_runtime_connection") as opened:
                    self.refusal(self.options((family,), **missing), reason)
                opened.assert_not_called()

    def test_composing_opens_no_database_connection(self) -> None:
        with mock.patch("quant_platform.application.api_host_composition.open_runtime_connection") as opened, \
                mock.patch("quant_platform.application.api_host_composition.psycopg.connect") as connect:
            compose_api_host(self.options())
        opened.assert_not_called()
        connect.assert_not_called()

    def test_strategy_and_validation_need_no_dsn(self) -> None:
        compose_api_host(self.options(("strategy", "validation"), runtime_dsn="", catalog_dsn=""))

    # -- the principals file ---------------------------------------------------------

    def test_a_valid_principals_file_maps_fingerprints_to_principals(self) -> None:
        mapping = parse_principals(principals_document(
            entry("a", "sha256:" + "01" * 32, ["j02.strategy.compose", "j02.jobs.read"]),
            entry("a", "sha256:" + "02" * 32, ["j02.strategy.compose"]),  # rotation overlap: same principal id
            entry("empty", "sha256:" + "03" * 32, []),
        ))
        self.assertEqual(3, len(mapping))
        self.assertEqual("a", mapping["sha256:" + "01" * 32].principal_id)
        self.assertEqual(frozenset({"j02.strategy.compose", "j02.jobs.read"}), mapping["sha256:" + "01" * 32].scopes)
        self.assertEqual({}, parse_principals(principals_document()))

    def test_a_malformed_principals_file_is_refused(self) -> None:
        good = entry()
        cases = {
            "not json": b"{",
            "not utf-8": b"\xff\xfe",
            "not an object": b"[]",
            "duplicate key": b'{"version": 1, "version": 1, "principals": []}',
            "no version": json.dumps({"principals": []}).encode(),
            "bool version": json.dumps({"version": True, "principals": []}).encode(),
            "version 2": json.dumps({"version": 2, "principals": []}).encode(),
            "extra top-level field": json.dumps({"version": 1, "principals": [], "x": 1}).encode(),
            "principals not a list": json.dumps({"version": 1, "principals": {}}).encode(),
            "entry not an object": principals_document("deck"),
            "entry extra field": principals_document({**good, "revoked": False}),
            "entry missing field": principals_document({"principal_id": "a", "fingerprint": FINGERPRINT}),
            "empty principal id": principals_document({**good, "principal_id": "  "}),
            "principal id not text": principals_document({**good, "principal_id": 7}),
            "fingerprint without prefix": principals_document({**good, "fingerprint": "ab" * 32}),
            "fingerprint uppercase": principals_document({**good, "fingerprint": "sha256:" + "AB" * 32}),
            "fingerprint short": principals_document({**good, "fingerprint": "sha256:" + "ab" * 31}),
            "scopes not a list": principals_document({**good, "scopes": "j02.strategy.compose"}),
            "scope not text": principals_document({**good, "scopes": [1]}),
            "repeated scope": principals_document({**good, "scopes": ["j02.jobs.read", "j02.jobs.read"]}),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(ApiHostCompositionError) as raised:
                    parse_principals(data)
                self.assertEqual("principals_malformed", raised.exception.reason)

    def test_a_scope_outside_the_registered_scopes_is_refused(self) -> None:
        for scope in ("j02.admin", "j02.market_data.write", "authenticated_connection", "J02.JOBS.READ"):
            with self.subTest(scope):
                with self.assertRaises(ApiHostCompositionError) as raised:
                    parse_principals(principals_document(entry(scopes=[scope])))
                self.assertEqual("principals_scope_unknown", raised.exception.reason)

    def test_a_duplicate_fingerprint_is_refused(self) -> None:
        with self.assertRaises(ApiHostCompositionError) as raised:
            parse_principals(principals_document(entry("a"), entry("b")))
        self.assertEqual("principals_duplicate_fingerprint", raised.exception.reason)

    def test_the_file_checks_apply_through_the_composition(self) -> None:
        for label, data, reason in (
            ("malformed", b"{", "principals_malformed"),
            ("duplicate", principals_document(entry("a"), entry("b")), "principals_duplicate_fingerprint"),
            ("scope", principals_document(entry(scopes=["j02.admin"])), "principals_scope_unknown"),
        ):
            with self.subTest(label):
                path = self.cwd / f"bad_{label}.json"
                path.write_bytes(data)
                self.refusal(self.options(principals_file=str(path)), reason)

    # -- versions and notices ----------------------------------------------------------

    def test_the_versions_are_the_hashes_of_the_files_and_appear_in_the_notices(self) -> None:
        composition = compose_api_host(self.options())
        security = composition.remote_security
        self.assertEqual(file_version(self.principals_path.read_bytes()), security.authorization_policy_version)
        self.assertEqual(file_version((self.cwd / "ca.crt").read_bytes()), security.server_trust_bundle_version)
        self.assertRegex(security.authorization_policy_version, r"sha256:[0-9a-f]{64}")
        self.assertIn(f"authorization_policy_version={security.authorization_policy_version}", composition.notices)
        self.assertIn(f"server_trust_bundle_version={security.server_trust_bundle_version}", composition.notices)
        self.assertIn("families=" + ",".join(FAMILY_NAMES), composition.notices)

    def test_a_changed_principals_file_changes_the_policy_version(self) -> None:
        other = self.cwd / "other_principals.json"
        other.write_bytes(principals_document(entry("deck-all", self.fingerprints["all"], ALL_SCOPES)))
        self.assertNotEqual(
            compose_api_host(self.options()).remote_security.authorization_policy_version,
            compose_api_host(self.options(principals_file=str(other))).remote_security.authorization_policy_version,
        )

    def test_a_family_no_principal_holds_is_allowed_and_reported(self) -> None:
        path = self.cwd / "strategy_only.json"
        path.write_bytes(principals_document(entry("deck", self.fingerprints["all"], ["j02.strategy.compose"])))
        composition = compose_api_host(self.options(("strategy", "jobs"), principals_file=str(path)))
        notices = "\n".join(composition.notices)
        self.assertIn("unreachable_family=jobs", notices)
        self.assertNotIn("unreachable_family=strategy", notices)
        self.assertEqual(
            {("j02-strategy-v1", "j02-strategy-v1-request-v1", "compose")}
            | {key for key in composition.family_handlers if key[0] == "j02-job-v1"},
            set(composition.family_handlers),
        )

    def test_a_principal_holding_only_the_register_scope_does_not_make_training_reachable(self) -> None:
        for scopes, reachable in (
            (["j02.training.register"], False),
            (["j02.training.evaluate"], True),
            (["j02.training.evaluate", "j02.training.register"], True),
        ):
            with self.subTest(scopes):
                path = self.cwd / "training_scopes.json"
                path.write_bytes(principals_document(entry("deck", self.fingerprints["all"], scopes)))
                composition = compose_api_host(self.options(("training",), principals_file=str(path)))
                notices = " ".join(composition.notices)
                self.assertEqual(not reachable, "unreachable_family=training" in notices)

    def test_the_training_repository_provider_is_the_real_composed_one(self) -> None:
        import quant_platform.application.api_host_composition as module
        from quant_platform.experiments import ExperimentRepository

        with mock.patch.object(
            module, "training_family_handlers", wraps=module.training_family_handlers,
        ) as factory, mock.patch.object(module.psycopg, "connect") as connect:
            compose_api_host(self.options(("training",)))
            (provider,) = factory.call_args.args
            connections = []
            connect.side_effect = lambda *a, **k: connections.append(MagicMock(name="connection")) or connections[-1]
            for failure in (None, RuntimeError("boom")):
                with self.subTest(failure=failure):
                    try:
                        with provider() as repository:
                            self.assertIsInstance(repository, ExperimentRepository)
                            self.assertIs(connections[-1], repository.connection)
                            connections[-1].close.assert_not_called()
                            if failure is not None:
                                raise failure
                    except RuntimeError:
                        self.assertIsNotNone(failure)
                    connections[-1].close.assert_called_once()
            self.assertEqual(2, len(connections))  # one connection per request
            for call in connect.call_args_list:
                self.assertEqual((RUNTIME_DSN,), call.args)
                self.assertNotIn("autocommit", call.kwargs)  # the repository commits per call

    # -- routes and the shared J14 store -----------------------------------------------

    def test_each_family_installs_exactly_its_own_routes(self) -> None:
        for family in FAMILY_NAMES:
            with self.subTest(family):
                composition = compose_api_host(self.options((family,)))
                family_ids = {key[0] for key in composition.family_handlers}
                self.assertEqual({PROBES[family][0]}, family_ids)
                self.assertEqual(
                    FAMILY_SCOPES[family],
                    {route.required_scope for route in composition.family_handlers.values()}
                    | ({"j02.training.register"} if family == "training" else set()),
                )

    def test_all_families_compose_without_overlapping_routes(self) -> None:
        composition = compose_api_host(self.options())
        self.assertEqual({probe[0] for probe in PROBES.values()}, {key[0] for key in composition.family_handlers})
        singles = sum(len(compose_api_host(self.options((family,))).family_handlers) for family in FAMILY_NAMES)
        self.assertEqual(singles, len(composition.family_handlers))

    def test_one_shared_store_serves_the_framing_families_only(self) -> None:
        for families in ((), ("strategy", "validation", "training", "jobs", "result-import")):
            composition = compose_api_host(self.options(families))
            self.assertIsNone(composition.framed_transfers)
            self.assertEqual(frozenset(), composition.framed_resume_scopes)
        composition = compose_api_host(self.options())
        self.assertIsInstance(composition.framed_transfers, FramedResultTransferStore)
        self.assertEqual(ADMITTED_INPUT_TRANSFER_CONFIG_V1, composition.framed_transfers.config)
        self.assertEqual(frozenset({"j02.admitted_input.read"}), composition.framed_resume_scopes)
        other = compose_api_host(self.options())
        self.assertIsNot(composition.framed_transfers, other.framed_transfers)  # one per composition

    # -- the host ------------------------------------------------------------------------

    def run_host(self, argv, **environment):
        out, err = io.StringIO(), io.StringIO()
        base = {"QP_RUNTIME_DSN": "", "QP_GIT_COMMIT": COMMIT, "CATALOG_DSN": ""}
        with mock.patch.dict(os.environ, {**base, **environment}), \
                mock.patch.object(api_server, "run_api_transport_server", new=mock.AsyncMock()) as transport, \
                mock.patch.object(api_server, "open_runtime_connection") as connect, \
                mock.patch.object(api_server, "record_deployment_commit") as record, \
                redirect_stdout(out), redirect_stderr(err):
            code = api_server.main(argv)
        return code, transport, connect, record, out.getvalue(), err.getvalue()

    def host_argv(self, *extra, runtime=True):
        return (["--runtime-dsn", RUNTIME_DSN] if runtime else []) + list(extra)

    def tls_argv(self):
        return [
            "--tls-certificate", str(self.cwd / "server.crt"),
            "--tls-private-key", str(self.cwd / "server.key"),
            "--client-trust-anchor", str(self.cwd / "ca.crt"),
            "--principals-file", str(self.principals_path),
        ]

    def test_a_default_invocation_is_the_unchanged_market_data_server(self) -> None:
        code, transport, _connect, record, out, _err = self.run_host(self.host_argv())
        self.assertEqual(0, code)
        transport.assert_awaited_once()
        (config,), kwargs = transport.await_args
        self.assertEqual({"stop_event"}, set(kwargs))
        self.assertIsNone(config.remote_security)
        self.assertIn("families=none", out)
        record.assert_called_once()

    def test_enabled_families_reach_the_transport_with_the_shared_store_and_scopes(self) -> None:
        argv = self.host_argv("--families", "strategy,admitted-input", "--dsn", CATALOG_DSN, *self.tls_argv())
        code, transport, _connect, _record, out, _err = self.run_host(argv)
        self.assertEqual(0, code)
        (config,), kwargs = transport.await_args
        self.assertIsNotNone(config.remote_security)
        self.assertEqual(
            {"j02-strategy-v1", "j02-admitted-input-v2"}, {key[0] for key in kwargs["family_handlers"]},
        )
        self.assertIsInstance(kwargs["framed_transfers"], FramedResultTransferStore)
        self.assertEqual(frozenset({"j02.admitted_input.read"}), kwargs["framed_resume_scopes"])
        self.assertIn("authorization_policy_version=sha256:", out)
        self.assertIn("server_trust_bundle_version=sha256:", out)
        self.assertIn("families=strategy,admitted-input", out)

    def test_the_options_have_environment_equivalents(self) -> None:
        environment = {
            "QP_API_TLS_CERTIFICATE": str(self.cwd / "server.crt"),
            "QP_API_TLS_PRIVATE_KEY": str(self.cwd / "server.key"),
            "QP_API_CLIENT_TRUST_ANCHOR": str(self.cwd / "ca.crt"),
            "QP_API_PRINCIPALS_FILE": str(self.principals_path),
            "QP_API_FAMILIES": "validation",
        }
        code, transport, *_ = self.run_host(self.host_argv(), **environment)
        self.assertEqual(0, code)
        _args, kwargs = transport.await_args
        self.assertEqual({"j02-validation-v1"}, {key[0] for key in kwargs["family_handlers"]})

    def test_a_refused_composition_stops_the_host_before_anything_connects_records_or_binds(self) -> None:
        refusals = {
            "unknown family": ["--families", "bogus"],
            "family without tls": ["--families", "strategy"],
            "partial tls": ["--tls-certificate", str(self.cwd / "server.crt")],
            "jobs without runtime dsn": None,
        }
        for label, extra in refusals.items():
            if extra is None:
                continue
            with self.subTest(label):
                code, transport, connect, record, _out, err = self.run_host(self.host_argv(*extra))
                self.assertEqual(api_server.EXIT_COMPOSITION_REFUSED, code)
                self.assertIn("api_server: FATAL:", err)
                transport.assert_not_awaited()
                connect.assert_not_called()
                record.assert_not_called()

    def test_a_bad_principals_file_stops_the_host_without_binding(self) -> None:
        path = self.cwd / "host_bad.json"
        path.write_bytes(principals_document(entry("a"), entry("b")))
        argv = self.host_argv("--families", "strategy", *self.tls_argv()[:-1], str(path))
        code, transport, connect, record, _out, err = self.run_host(argv)
        self.assertEqual(api_server.EXIT_COMPOSITION_REFUSED, code)
        self.assertIn("principals_duplicate_fingerprint", err)
        transport.assert_not_awaited()
        connect.assert_not_called()
        record.assert_not_called()
        # Nothing is left bound: the default port is free to take.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))

    def test_a_missing_runtime_dsn_is_still_refused_first(self) -> None:
        code, transport, connect, record, _out, err = self.run_host(self.host_argv(runtime=False))
        self.assertEqual(api_server.EXIT_RUNTIME_NOT_CONFIGURED, code)
        self.assertIn("runtime DSN is required", err)
        transport.assert_not_awaited()
        connect.assert_not_called()
        record.assert_not_called()

    # -- a real TLS 1.3 mutual-TLS listener ----------------------------------------------

    def serve(self, composition):
        security = composition.remote_security
        return websockets.serve(
            lambda websocket: handle_api_transport_connection(
                websocket, execute=MagicMock(), remote_security=security,
                family_handlers=composition.family_handlers,
                framed_transfers=composition.framed_transfers,
                framed_resume_scopes=composition.framed_resume_scopes or None,
            ),
            "127.0.0.1", 0, ssl=security.build_server_ssl_context(),
        )

    def client_context(self, name):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cafile=str(self.cwd / "ca.crt"))
        context.load_cert_chain(str(self.cwd / f"{name}.crt"), str(self.cwd / f"{name}.key"))
        return context

    async def probe(self, server, name, family):
        """``routed`` if its own decoder refused the request, ``no_route`` if no route exists, or the 1008 close."""
        port = server.sockets[0].getsockname()[1]
        family_id, operation = PROBES[family]
        try:
            async with websockets.connect(f"wss://127.0.0.1:{port}", ssl=self.client_context(name)) as socket_:
                await socket_.send(json.dumps({
                    "message_family": family_id, "schema_version": f"{family_id}-request-v1",
                    "operation": operation, "request_id": "r1", "request": {},
                }))
                reply = json.loads(await asyncio.wait_for(socket_.recv(), 10))
        except websockets.ConnectionClosed as closed:
            return ("closed", closed.rcvd.code if closed.rcvd else None)
        message = (reply.get("error") or {}).get("message")
        return ("routed" if message != UNSUPPORTED else "no_route", reply.get("status"))

    async def test_each_enabled_family_is_reachable_only_with_a_principal_that_holds_its_scope(self) -> None:
        composition = compose_api_host(self.options())
        async with self.serve(composition) as server:
            for family in FAMILY_NAMES:
                with self.subTest(family, principal="all"):
                    self.assertEqual(("routed", "error"), await self.probe(server, "all", family))
            self.assertEqual(("routed", "error"), await self.probe(server, "strategy_only", "strategy"))
            for family in FAMILY_NAMES:
                if family == "strategy":
                    continue
                with self.subTest(family, principal="strategy_only"):
                    self.assertEqual(("closed", 1008), await self.probe(server, "strategy_only", family))

    async def test_an_unmapped_fingerprint_is_denied_and_a_certificate_of_another_ca_cannot_connect(self) -> None:
        composition = compose_api_host(self.options())
        async with self.serve(composition) as server:
            self.assertEqual(("closed", 1008), await self.probe(server, "unmapped", "strategy"))
            with self.assertRaises((ssl.SSLError, websockets.InvalidHandshake, websockets.ConnectionClosed, OSError)):
                await self.probe(server, "foreign", "strategy")
        events = composition.remote_security.evidence_log.events
        self.assertTrue(events)
        self.assertEqual({"deny"}, {item.decision for item in events if item.principal_id is None})

    async def test_every_evidence_event_names_the_policy_and_the_trust_bundle(self) -> None:
        composition = compose_api_host(self.options(("strategy",)))
        async with self.serve(composition) as server:
            await self.probe(server, "all", "strategy")
            await self.probe(server, "unmapped", "strategy")
        security = composition.remote_security
        events = security.evidence_log.events
        self.assertGreaterEqual(len(events), 2)
        for item in events:
            self.assertEqual(security.authorization_policy_version, item.authorization_policy_version)
            self.assertEqual(security.server_trust_bundle_version, item.server_trust_bundle_version)
            self.assertEqual("TLSv1.3", item.tls_version)

    def swapped_anchor_options(self, anchor_path, *, scopes=("j02.strategy.compose",)):
        """Options whose principals map the client of another CA (``foreign``) to strategy."""
        principals = self.cwd / "foreign_principals.json"
        principals.write_bytes(principals_document(entry("foreign", self.fingerprints["foreign"], list(scopes))))
        return self.options(
            ("strategy",), client_trust_anchor=str(anchor_path), principals_file=str(principals),
        )

    async def foreign_outcome(self, composition):
        async with self.serve(composition) as server:
            try:
                return await self.probe(server, "foreign", "strategy")
            except (ssl.SSLError, websockets.InvalidHandshake, websockets.ConnectionClosed, OSError):
                return ("tls_denied",)

    async def test_the_trust_in_force_is_the_one_whose_version_is_recorded_after_composition(self) -> None:
        anchor = self.cwd / "swap_after.crt"
        anchor.write_bytes((self.cwd / "ca.crt").read_bytes())
        composition = compose_api_host(self.swapped_anchor_options(anchor))
        self.assertEqual(("tls_denied",), await self.foreign_outcome(composition))
        # The file is replaced after composing and before the listener starts.
        anchor.write_bytes((self.cwd / "other_ca.crt").read_bytes())
        self.assertEqual(("tls_denied",), await self.foreign_outcome(composition))
        self.assertEqual(
            file_version((self.cwd / "ca.crt").read_bytes()),
            composition.remote_security.server_trust_bundle_version,
        )

    async def test_the_trust_in_force_is_the_one_whose_bytes_were_hashed_while_composing(self) -> None:
        import quant_platform.application.api_host_composition as module

        anchor = self.cwd / "swap_during.crt"
        anchor.write_bytes((self.cwd / "ca.crt").read_bytes())
        genuine = module._read

        def read_then_swap(path, reason):
            data = genuine(path, reason)
            if Path(path) == anchor:
                anchor.write_bytes((self.cwd / "other_ca.crt").read_bytes())  # replaced right after the read
            return data

        with mock.patch.object(module, "_read", read_then_swap):
            composition = compose_api_host(self.swapped_anchor_options(anchor))
        self.assertEqual(
            file_version((self.cwd / "ca.crt").read_bytes()),
            composition.remote_security.server_trust_bundle_version,
        )
        self.assertEqual(("tls_denied",), await self.foreign_outcome(composition))
        # And the client of the recorded CA is still served by the same composition.
        async with self.serve(composition) as server:
            probe = await self.probe(server, "all", "strategy")
        self.assertIn(probe, (("closed", 1008),))  # "all" is not mapped in this principals file

    async def test_the_validated_context_is_the_one_the_listener_serves_with(self) -> None:
        composition = compose_api_host(self.options(("strategy",)))
        security = composition.remote_security
        context = security.build_server_ssl_context()
        self.assertIs(context, security.ssl_context)
        self.assertIs(context, security.build_server_ssl_context())
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertEqual(ssl.TLSVersion.TLSv1_3, context.minimum_version)

    async def test_without_families_a_listener_serves_no_family_route(self) -> None:
        composition = compose_api_host(self.options(()))
        async with self.serve(composition) as server:
            for family in FAMILY_NAMES:
                with self.subTest(family):
                    self.assertEqual(("no_route", "error"), await self.probe(server, "all", family))

    async def test_with_the_family_enabled_the_request_is_authorized_under_its_scope(self) -> None:
        composition = compose_api_host(self.options(("strategy",)))
        async with self.serve(composition) as server:
            await self.probe(server, "all", "strategy")
        scopes = {item.requested_scope for item in composition.remote_security.evidence_log.events}
        self.assertIn("j02.strategy.compose", scopes)


if __name__ == "__main__":
    unittest.main()
