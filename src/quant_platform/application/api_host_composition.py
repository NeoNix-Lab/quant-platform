"""Composition of the API host: TLS/mTLS, principals and the DG-K families (issue #395).

ADR-0072 section 3. ``tools/api_server.py`` stays the host (argv, environment,
signals); this module is the one composition function it calls. It turns
resolved options into the arguments ``run_api_transport_server`` already takes:
the ``RemoteJ02SecurityConfig``, the family routes, the shared J14 transfer
store and the scopes that may resume from it. It opens no database connection
while composing; every provider it builds opens its own connection per request
(the stores are used from worker threads).

Opt-in. ``families`` is empty by default, so an invocation without ``--families``
composes nothing and the host stays the J02 v1 market-data server. A family is
reachable only over WSS with mutual TLS (ADR-0063): the certificate, the private
key, the client trust anchor and the principals file must all be given.

Fail closed. Each of the following is an :class:`ApiHostCompositionError` raised
before anything binds: an unknown family; TLS material or the principals file
given in part; a family without complete material; an unreadable or invalid
principals file (malformed JSON, an unknown field, a duplicate fingerprint, a
fingerprint that is not ``sha256:<lowercase hex>``, a scope outside
``J02_REMOTE_SCOPES``); certificates the TLS stack cannot load; ``jobs``,
``admitted-input`` or ``result-import`` without the runtime DSN; ``admitted-input``
or ``result-import`` without the catalog DSN. A family whose scope no principal
holds is allowed (it is simply unreachable) and is reported in ``notices``.

Versions. ``authorization_policy_version`` is the SHA-256 of the principals file
bytes and ``server_trust_bundle_version`` the SHA-256 of the client trust-anchor
file bytes (``sha256:<hex>``); both are in ``notices`` (the startup output) and
in every security evidence event, which already carries them (ADR-0063 section 3).

Jobs. ``j02-job-v1`` is composed with the closed ``DATA_LOCAL_JOB_REGISTRY``
(empty today: no handler is claimed) and without a result-body resolver, so it
does not frame results; the shared J14 store therefore serves ``admitted-input``
only, and ``framed_resume_scopes`` is that family's scope when it is enabled.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import ssl
from typing import Any

import psycopg

from ..access.catalog import Catalog
from ..access.gateway import DataGateway
from ..source_adapters.bybit import BYBIT_ORDERING_PROVIDER
from quant_platform.experiments.persistence import ExperimentRepository

from .admitted_input import AdmittedInputStore
from .admitted_input_authority import (
    ADMITTED_INPUT_BOUNDS_V1,
    ADMITTED_INPUT_TRANSFER_CONFIG_V1,
    DatasetAdmittedInputAuthority,
)
from .admitted_input_transport import J02_ADMITTED_INPUT_SCOPE, admitted_input_family_handlers
from .api_transport_server import (
    J02_REMOTE_SCOPES,
    FamilyHandlers,
    RemoteJ02Principal,
    RemoteJ02SecurityConfig,
    RemoteJ02SecurityEvidenceLog,
)
from .durable_jobs import DurableJobStore
from .framed_result_transport import FramedResultTransferStore
from .job_dispatch import DATA_LOCAL_JOB_REGISTRY
from .job_transport import J02_JOB_SCOPES, job_family_handlers
from .job_worker_runtime import open_runtime_connection, recorded_deployment_commit
from .result_import_composition import (
    K13_STAGING_BOUNDS_V1,
    governed_result_import_service_provider,
)
from .result_import_transport import J02_RESULT_IMPORT_SCOPE, StagedOutputStore, result_import_family_handlers
from .strategy_transport import strategy_family_handlers
from .training_transport import training_family_handlers
from .validation_transport import validation_family_handlers

FAMILY_NAMES = ("strategy", "validation", "training", "jobs", "admitted-input", "result-import")
FAMILY_SCOPES: Mapping[str, frozenset[str]] = {
    "strategy": frozenset({"j02.strategy.compose"}),
    "validation": frozenset({"j02.validation.evaluate"}),
    "training": frozenset({"j02.training.evaluate", "j02.training.register"}),
    "jobs": frozenset(J02_JOB_SCOPES.values()),
    "admitted-input": frozenset({J02_ADMITTED_INPUT_SCOPE}),
    "result-import": frozenset({J02_RESULT_IMPORT_SCOPE}),
}
PRINCIPALS_FILE_VERSION = 1
MAX_PRINCIPALS_FILE_BYTES = 1024 * 1024
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_PRINCIPAL_KEYS = frozenset({"principal_id", "fingerprint", "scopes"})


class ApiHostCompositionError(ValueError):
    """The host configuration is refused before anything binds; ``reason`` is stable."""

    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ApiHostOptions:
    """Resolved host options; empty text means "not given"."""

    families: tuple[str, ...] = ()
    tls_certificate: str = ""
    tls_private_key: str = ""
    client_trust_anchor: str = ""
    principals_file: str = ""
    catalog_dsn: str = ""
    runtime_dsn: str = ""

    def __post_init__(self) -> None:
        for name in (
            "tls_certificate", "tls_private_key", "client_trust_anchor",
            "principals_file", "catalog_dsn", "runtime_dsn",
        ):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a resolved string")
        if not isinstance(self.families, tuple):
            raise TypeError("families must be a tuple of family names")

    @property
    def tls_material(self) -> tuple[str, str, str, str]:
        return (
            self.tls_certificate.strip(), self.tls_private_key.strip(),
            self.client_trust_anchor.strip(), self.principals_file.strip(),
        )


@dataclass(frozen=True, slots=True)
class ApiHostComposition:
    """What ``run_api_transport_server`` is given, and what the host prints."""

    remote_security: RemoteJ02SecurityConfig | None
    family_handlers: FamilyHandlers
    framed_transfers: FramedResultTransferStore | None
    framed_resume_scopes: frozenset[str]
    notices: tuple[str, ...]


def parse_families(text: str) -> tuple[str, ...]:
    """A comma-separated subset of :data:`FAMILY_NAMES`; empty means none."""

    if not isinstance(text, str):
        raise TypeError("families must be text")
    names = tuple(part.strip() for part in text.split(",") if part.strip())
    unknown = sorted(set(names) - set(FAMILY_NAMES))
    if unknown:
        raise ApiHostCompositionError("unknown_family", ", ".join(unknown))
    return tuple(name for name in FAMILY_NAMES if name in names)


def file_version(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read(path: str, reason: str) -> bytes:
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ApiHostCompositionError(reason, "the file cannot be read") from error
    if len(data) > MAX_PRINCIPALS_FILE_BYTES:
        raise ApiHostCompositionError(reason, "the file is too large")
    return data


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_principals(data: bytes) -> dict[str, RemoteJ02Principal]:
    """The fingerprint-to-principal mapping of a principals file (ADR-0072 section 3)."""

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ApiHostCompositionError("principals_malformed", "not a JSON document") from error
    if (
        not isinstance(document, dict) or set(document) != {"version", "principals"}
        or type(document["version"]) is not int or document["version"] != PRINCIPALS_FILE_VERSION
        or not isinstance(document["principals"], list)
    ):
        raise ApiHostCompositionError(
            "principals_malformed", f'expected {{"version": {PRINCIPALS_FILE_VERSION}, "principals": [...]}}',
        )
    principals: dict[str, RemoteJ02Principal] = {}
    for entry in document["principals"]:
        if not isinstance(entry, dict) or set(entry) != _PRINCIPAL_KEYS:
            raise ApiHostCompositionError("principals_malformed", "a principal has other fields than expected")
        principal_id, fingerprint, scopes = entry["principal_id"], entry["fingerprint"], entry["scopes"]
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ApiHostCompositionError("principals_malformed", "principal_id must be a non-empty string")
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise ApiHostCompositionError("principals_malformed", "fingerprint must be sha256:<lowercase hex>")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            raise ApiHostCompositionError("principals_malformed", "scopes must be a list of strings")
        if len(set(scopes)) != len(scopes):
            raise ApiHostCompositionError("principals_malformed", "scopes must not repeat")
        outside = sorted(set(scopes) - J02_REMOTE_SCOPES)
        if outside:
            raise ApiHostCompositionError("principals_scope_unknown", ", ".join(outside))
        if fingerprint in principals:
            raise ApiHostCompositionError("principals_duplicate_fingerprint", "a fingerprint is mapped twice")
        principals[fingerprint] = RemoteJ02Principal(principal_id, frozenset(scopes))
    return principals


def compose_api_host(options: ApiHostOptions) -> ApiHostComposition:
    """Compose the host's security, routes and shared transfer store, or refuse."""

    if not isinstance(options, ApiHostOptions):
        raise TypeError("options must be ApiHostOptions")
    families = options.families
    unknown = sorted(set(families) - set(FAMILY_NAMES))
    if unknown:
        raise ApiHostCompositionError("unknown_family", ", ".join(unknown))
    certificate, private_key, trust_anchor, principals_file = options.tls_material
    given = [bool(item) for item in (certificate, private_key, trust_anchor, principals_file)]
    if any(given) and not all(given):
        raise ApiHostCompositionError(
            "tls_material_incomplete",
            "--tls-certificate, --tls-private-key, --client-trust-anchor and --principals-file go together",
        )
    if families and not all(given):
        raise ApiHostCompositionError(
            "families_require_tls", "a family is served only over WSS with mutual TLS and a principals file",
        )
    runtime_dsn, catalog_dsn = options.runtime_dsn.strip(), options.catalog_dsn.strip()
    for family in ("jobs", "admitted-input", "result-import"):
        if family in families and not runtime_dsn:
            raise ApiHostCompositionError("runtime_dsn_required", f"{family} needs the runtime DSN")
    for family in ("admitted-input", "result-import"):
        if family in families and not catalog_dsn:
            raise ApiHostCompositionError("catalog_dsn_required", f"{family} needs the catalog DSN")

    remote_security = None
    notices: list[str] = []
    if all(given):
        principals_bytes = _read(principals_file, "principals_unreadable")
        anchor_bytes = _read(trust_anchor, "trust_anchor_unreadable")
        principals = parse_principals(principals_bytes)
        remote_security = RemoteJ02SecurityConfig(
            server_certificate_path=certificate,
            server_private_key_path=private_key,
            client_trust_anchor_path=trust_anchor,
            server_trust_bundle_version=file_version(anchor_bytes),
            authorization_policy_version=file_version(principals_bytes),
            principals_by_fingerprint=principals,
            evidence_log=RemoteJ02SecurityEvidenceLog(),
        )
        # One snapshot: the trust is loaded from the very bytes that were hashed, and
        # the validated context is the one the listener serves with. Nothing reads
        # the anchor path again, so the recorded version names the trust in force.
        try:
            context = remote_security.build_server_ssl_context(
                client_trust_anchor_data=_trust_data(anchor_bytes),
            )
        except (ssl.SSLError, OSError, ValueError) as error:
            raise ApiHostCompositionError("tls_material_invalid", "the TLS stack cannot load it") from error
        remote_security = replace(remote_security, ssl_context=context)
        notices.append(f"authorization_policy_version={remote_security.authorization_policy_version}")
        notices.append(f"server_trust_bundle_version={remote_security.server_trust_bundle_version}")
        notices.append(f"principals={len(principals)}")
    notices.append("families=" + (",".join(families) if families else "none"))

    family_routes: dict[str, dict[tuple[str, str, str], Any]] = {}
    transfers: FramedResultTransferStore | None = None
    resume_scopes: frozenset[str] = frozenset()

    def runtime_connections() -> AbstractContextManager[Any]:
        @contextmanager
        def session():
            connection = open_runtime_connection(runtime_dsn)
            try:
                yield connection
            finally:
                connection.close()
        return session()

    if "strategy" in families:
        family_routes["strategy"] = dict(strategy_family_handlers())
    if "validation" in families:
        family_routes["validation"] = dict(validation_family_handlers())
    if "training" in families:
        family_routes["training"] = dict(training_family_handlers(
            _experiment_repositories(runtime_dsn) if runtime_dsn else None,
        ))
    if "jobs" in families:
        @contextmanager
        def job_stores():
            with runtime_connections() as connection:
                yield DurableJobStore(connection)
        family_routes["jobs"] = dict(job_family_handlers(job_stores, DATA_LOCAL_JOB_REGISTRY))
    if "admitted-input" in families:
        transfers = FramedResultTransferStore(ADMITTED_INPUT_TRANSFER_CONFIG_V1)
        resume_scopes = FAMILY_SCOPES["admitted-input"]
        authority = DatasetAdmittedInputAuthority(
            DataGateway(Catalog(dsn=catalog_dsn), ordering_providers=(BYBIT_ORDERING_PROVIDER,)),
            deployment_commit=_recorded_commit(runtime_dsn),
        )

        @contextmanager
        def admission_stores():
            with runtime_connections() as connection:
                yield AdmittedInputStore(connection)
        family_routes["admitted-input"] = dict(admitted_input_family_handlers(
            admission_stores, authority.resolve, authority.members, authority.read_member,
            transfers=transfers, bounds=ADMITTED_INPUT_BOUNDS_V1,
        ))
    if "result-import" in families:
        staging = StagedOutputStore(K13_STAGING_BOUNDS_V1)
        family_routes["result-import"] = dict(result_import_family_handlers(
            governed_result_import_service_provider(runtime_connections, staging), staging=staging,
        ))

    # A family is reachable when a principal holds the scope one of its routes
    # requires. A grant that is only an additional, body-dependent scope (training
    # registration) opens nothing on its own, so it does not count.
    held = frozenset().union(*(principal.scopes for principal in (
        remote_security.principals_by_fingerprint.values() if remote_security is not None else ()
    )))
    routes: dict[tuple[str, str, str], Any] = {}
    for family in families:
        routes.update(family_routes[family])
        required = frozenset(route.required_scope for route in family_routes[family].values())
        if not required & held:
            notices.append(f"unreachable_family={family} (no principal holds {','.join(sorted(required))})")
    return ApiHostComposition(remote_security, routes, transfers, resume_scopes, tuple(notices))


def _trust_data(anchor: bytes) -> str | bytes:
    """The exact anchor bytes in the form ``load_verify_locations(cadata=)`` takes."""

    try:
        return anchor.decode("ascii")  # PEM
    except UnicodeDecodeError:
        return anchor  # DER


def _recorded_commit(runtime_dsn: str) -> Callable[[], str | None]:
    def read() -> str | None:
        connection = open_runtime_connection(runtime_dsn)
        try:
            return recorded_deployment_commit(connection)
        finally:
            connection.close()
    return read


def _experiment_repositories(runtime_dsn: str) -> Callable[[], AbstractContextManager[ExperimentRepository]]:
    """An Experiment repository per registering request, on its own connection.

    The repository's default mode commits each mutation itself, which assumes a
    connection that is not autocommit.
    """

    @contextmanager
    def session():
        connection = psycopg.connect(runtime_dsn)
        try:
            yield ExperimentRepository(connection)
        finally:
            connection.close()
    return session


__all__ = [
    "FAMILY_NAMES",
    "FAMILY_SCOPES",
    "PRINCIPALS_FILE_VERSION",
    "ApiHostComposition",
    "ApiHostCompositionError",
    "ApiHostOptions",
    "compose_api_host",
    "file_version",
    "parse_families",
    "parse_principals",
]
