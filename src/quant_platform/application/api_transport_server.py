"""J02 WebSocket transport for the canonical market-data Consumer API.

This module owns transport composition only.  It carries the already-frozen
``ConsumerMarketDataQuery`` / ``ConsumerMarketDataResult`` / ``ConsumerApiError``
surface over JSON WebSocket messages, and delegates all business behavior to
``quant_platform.application.market_data``.

For original J02 v1, ADR-0063 defines the future non-loopback requirement;
this listener now enforces its TLS/mTLS gate. ADR-0069 family dispatch
authorizes the header's scope before invoking any semantic request decoder.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import hashlib
import ipaddress
import json
import math
import secrets
import ssl
from typing import Any

import websockets

from .composition import (
    DEFAULT_MARKET_DATA_BATCH_SIZE,
    DEFAULT_MAX_RESULT_ROWS,
    MarketDataApplicationConfig,
    compose_market_data_application,
)
from .market_data import (
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    ConsumerMarketDataResult,
    RepresentationRef,
)
from quant_platform.canonical import canonical_bytes


J02_REQUEST_SCHEMA_VERSION = "j02-request-v1"
J02_RESPONSE_SCHEMA_VERSION = "j02-response-v1"
J02_INVALID_REQUEST_MESSAGE = "the request is not valid"
J02_MARKET_DATA_READ_SCOPE = "j02.market_data.read"
J02_REMOTE_SCOPES = frozenset({
    J02_MARKET_DATA_READ_SCOPE,
    "j02.strategy.compose",
    "j02.validation.evaluate",
    "j02.training.evaluate",
    "j02.training.register",
    "j02.jobs.submit",
    "j02.jobs.read",
    "j02.admitted_input.read",
    "j02.result.submit",
})
# ADR-0069 declares these header-level grants independently of whether a
# concrete seam handler has been installed. Training registration's additional
# body-dependent grant belongs to the J13 codec (#339), not this dispatcher.
_FAMILY_OPERATION_SCOPES = {
    "j02-strategy-v1": {"compose": "j02.strategy.compose"},
    "j02-validation-v1": dict.fromkeys(
        ("build_folds", "classify_candidate", "evaluate_dsr", "evaluate_pbo"),
        "j02.validation.evaluate",
    ),
    "j02-training-v1": {"train_evaluate": "j02.training.evaluate"},
    "j02-job-v1": {
        "submit": "j02.jobs.submit", "cancel": "j02.jobs.submit",
        "status": "j02.jobs.read", "result": "j02.jobs.read",
    },
    "j02-admitted-input-v1": dict.fromkeys(
        ("admit", "manifest", "deliver", "acknowledge"), "j02.admitted_input.read",
    ),
    "j02-result-import-v1": dict.fromkeys(("upload_output", "submit"), "j02.result.submit"),
}

# ADR-0050 Amendment 1 (#247): the wire-level message-size ceiling, set
# explicitly on both this server (websockets.serve) and the J05 TUI client
# (clients/tui/market_data_tui.py, which cannot import this constant across
# the client/quant_platform boundary and must keep its own literal in sync --
# see tests/test_api_transport_server_v1.py's cross-file consistency check).
# Sized with headroom above DEFAULT_MAX_RESULT_ROWS's ~12.5 MiB data-array
# estimate for envelope overhead (coverage/provenance/request fields). This is
# a defense-in-depth transport ceiling, not the primary guard -- the
# RESULT_TOO_LARGE refusal in market_data.py is what actually stops an
# oversized result from ever being built.
J02_MAX_WIRE_MESSAGE_BYTES = 16 * 1024 * 1024

MarketDataExecutor = Callable[[ConsumerMarketDataQuery], ConsumerMarketDataResult]


@dataclass(frozen=True, slots=True)
class J02FamilyHandler:
    """One server-composed family/version/operation route; none is installed by default.

    The scope is resolved from the header route, never from the request body.
    ``additional_scopes`` lets a codec declare grants that depend on the decoded
    request (for example ADR-0069 section 3's ``register`` flag): the server
    authorizes each returned scope, with evidence, after decoding and before
    ``execute`` can run. Concrete seam codecs and handlers belong to subsequent
    implementation atoms.
    """

    required_scope: str
    response_schema_version: str
    decode_request: Callable[[Mapping[str, Any]], Any]
    execute: Callable[[Any], Mapping[str, Any]]
    additional_scopes: Callable[[Any], Iterable[str]] | None = None

    def __post_init__(self) -> None:
        if self.required_scope not in J02_REMOTE_SCOPES:
            raise ValueError("required_scope must be a registered J02 scope")
        if not isinstance(self.response_schema_version, str) or not self.response_schema_version:
            raise ValueError("response_schema_version must be a non-empty string")
        if not callable(self.decode_request) or not callable(self.execute):
            raise TypeError("family decoder and executor must be callable")
        if self.additional_scopes is not None and not callable(self.additional_scopes):
            raise TypeError("additional_scopes must be callable")


FamilyHandlers = Mapping[tuple[str, str, str], J02FamilyHandler]


@dataclass(frozen=True, slots=True)
class RemoteJ02Principal:
    """One server-configured mTLS credential authorization rule."""

    principal_id: str
    scopes: frozenset[str]
    revoked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id.strip():
            raise ValueError("principal_id must be a non-empty string")
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, str) or not scope.strip() for scope in self.scopes
        ):
            raise ValueError("scopes must be a frozenset of non-empty strings")
        if not isinstance(self.revoked, bool):
            raise TypeError("revoked must be a boolean")


@dataclass(frozen=True, slots=True)
class RemoteJ02SecurityEvidence:
    """Bounded, payload-free remote J02 authorization evidence."""

    timestamp: str
    session_id: str
    tls_version: str | None
    tls_cipher: str | None
    server_trust_bundle_version: str
    credential_fingerprint: str | None
    principal_id: str | None
    authorization_policy_version: str
    requested_scope: str
    decision: str
    terminal_close_reason: str


class RemoteJ02SecurityEvidenceLog:
    """In-memory bounded audit sink; deployment may snapshot it externally."""

    def __init__(self, *, max_events: int = 1024):
        if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._max_events = max_events
        self._events: list[RemoteJ02SecurityEvidence] = []

    @property
    def events(self) -> tuple[RemoteJ02SecurityEvidence, ...]:
        return tuple(self._events)

    def record(self, evidence: RemoteJ02SecurityEvidence) -> None:
        if not isinstance(evidence, RemoteJ02SecurityEvidence):
            raise TypeError("evidence must be RemoteJ02SecurityEvidence")
        self._events.append(evidence)
        del self._events[:-self._max_events]


@dataclass(frozen=True, slots=True)
class RemoteJ02SecurityConfig:
    """Server-owned TLS/mTLS trust and exact-fingerprint policy configuration."""

    server_certificate_path: str
    server_private_key_path: str
    client_trust_anchor_path: str
    server_trust_bundle_version: str
    authorization_policy_version: str
    principals_by_fingerprint: Mapping[str, RemoteJ02Principal]
    evidence_log: RemoteJ02SecurityEvidenceLog

    def __post_init__(self) -> None:
        for name in (
            "server_certificate_path",
            "server_private_key_path",
            "client_trust_anchor_path",
            "server_trust_bundle_version",
            "authorization_policy_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.principals_by_fingerprint, Mapping):
            raise TypeError("principals_by_fingerprint must be a mapping")
        for fingerprint, principal in self.principals_by_fingerprint.items():
            _validate_certificate_fingerprint(fingerprint)
            if not isinstance(principal, RemoteJ02Principal):
                raise TypeError("principals_by_fingerprint values must be RemoteJ02Principal")
        if not isinstance(self.evidence_log, RemoteJ02SecurityEvidenceLog):
            raise TypeError("evidence_log must be RemoteJ02SecurityEvidenceLog")

    def build_server_ssl_context(self) -> ssl.SSLContext:
        """Build strict TLS 1.3 mTLS context before a non-loopback listener starts."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(self.server_certificate_path, self.server_private_key_path)
        context.load_verify_locations(cafile=self.client_trust_anchor_path)
        return context


@dataclass(frozen=True, slots=True)
class ApiTransportServerConfig:
    """Resolved J02 server configuration.

    The caller owns environment/argv resolution.  This module only receives the
    already-resolved process and Application-service values.
    """

    host: str = "127.0.0.1"
    port: int = 8765
    catalog_dsn: str = ""
    batch_size: int = DEFAULT_MARKET_DATA_BATCH_SIZE
    max_result_rows: int = DEFAULT_MAX_RESULT_ROWS
    allow_non_loopback: bool = False
    remote_security: RemoteJ02SecurityConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not (0 <= self.port <= 65535):
            raise ValueError("port must be an integer in [0, 65535]")
        if not isinstance(self.catalog_dsn, str):
            raise TypeError("catalog_dsn must be a resolved string")
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or self.batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")
        if (
            not isinstance(self.max_result_rows, int)
            or isinstance(self.max_result_rows, bool)
            or self.max_result_rows < 1
        ):
            raise ValueError("max_result_rows must be a positive integer")
        if not isinstance(self.allow_non_loopback, bool):
            raise TypeError("allow_non_loopback must be a boolean")
        if self.remote_security is not None and not isinstance(self.remote_security, RemoteJ02SecurityConfig):
            raise TypeError("remote_security must be a RemoteJ02SecurityConfig or None")
        if not _is_loopback_bind_host(self.host) and (
            not self.allow_non_loopback or self.remote_security is None
        ):
            raise ValueError("non-loopback bind requires complete TLS/mTLS remote_security configuration")


def compose_market_data_executor(config: ApiTransportServerConfig) -> MarketDataExecutor:
    """Compose the Application service executor used by the J02 server."""

    application = compose_market_data_application(
        MarketDataApplicationConfig(
            catalog_dsn=config.catalog_dsn,
            batch_size=config.batch_size,
            max_result_rows=config.max_result_rows,
        )
    )
    return application.execute


def decode_transport_query(payload: Mapping[str, Any]) -> tuple[str, ConsumerMarketDataQuery]:
    """Decode one J02 request envelope into the canonical Consumer API query."""

    if payload.get("schema_version") != J02_REQUEST_SCHEMA_VERSION:
        raise ValueError("unsupported request schema_version")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
    query = payload.get("query")
    if not isinstance(query, Mapping):
        raise ValueError("query must be a mapping")
    representation = query.get("representation")
    if not isinstance(representation, Mapping):
        raise ValueError("query.representation must be a mapping")
    if "definition" in representation and representation["definition"] is not None:
        definition = representation["definition"]
    else:
        definition = {}
    options = query["options"] if "options" in query and query["options"] is not None else {}
    return request_id, ConsumerMarketDataQuery(
        venue=query["venue"],
        instrument=query["instrument"],
        start=query["start"],
        end=query["end"],
        representation=RepresentationRef(
            kind=representation["kind"],
            version=representation["version"],
            definition=definition,
        ),
        options=options,
    )


def encode_consumer_result(result: ConsumerMarketDataResult) -> dict[str, Any]:
    """Encode a Consumer API result without adding transport semantics."""

    return {
        "request": result.request.stable_dict(),
        "request_identity": result.request_identity,
        "representation": {
            "kind": result.representation.kind,
            "version": result.representation.version,
        },
        "data": [_trade_record_dict(record) for record in result.data],
        "requested_interval": result.requested_interval.stable_dict(),
        "returned_temporal_bounds": (
            result.returned_temporal_bounds.stable_dict()
            if result.returned_temporal_bounds is not None
            else None
        ),
        "coverage": {
            "covered_intervals": [item.stable_dict() for item in result.coverage.covered_intervals],
            "gaps": [item.stable_dict() for item in result.coverage.gaps],
            "complete": result.coverage.complete,
        },
        "provenance": {
            "dataset_identity": result.provenance.dataset_identity.stable_dict(),
            "record_schema_id": result.provenance.record_schema_id,
            "schema_version": result.provenance.schema_version,
            "schema_hash": result.provenance.schema_hash,
            "natural_partitions": [
                item.stable_dict() for item in result.provenance.natural_partitions
            ],
            "manifest_hashes": list(result.provenance.manifest_hashes),
            "content_hashes": list(result.provenance.content_hashes),
        },
        "row_count": result.row_count,
    }


def encode_consumer_error(error: ConsumerApiError) -> dict[str, Any]:
    """Encode a frozen Consumer API error for the wire."""

    return {
        "code": error.code.value,
        "message": error.message,
        "context": dict(error.context),
        "request_identity": error.request_identity,
    }


@dataclass(frozen=True, slots=True)
class _ParsedMessage:
    """One JSON syntax parse, shared by header selection and body handling."""

    payload: Any
    failed: bool
    # False when the text carried a float, NaN/Infinity or a duplicate key, i.e.
    # something ``json.loads`` would silently round, coerce or overwrite.
    lossless: bool

    @property
    def family(self) -> bool:
        return (not self.failed) and isinstance(self.payload, Mapping) and "message_family" in self.payload


@dataclass(frozen=True, slots=True)
class _PreparedFamilyRequest:
    envelope: dict[str, Any]
    decoded: Any
    additional_scopes: frozenset[str]


def handle_api_transport_message(
    message: str | bytes, *, execute: MarketDataExecutor,
    family_handler: J02FamilyHandler | None = None,
    _parsed: _ParsedMessage | None = None,
    _family_response: dict[str, Any] | None = None,
) -> str:
    """Handle one JSON request message and return one JSON response message.

    This direct entry point has no principal, so a family handler that declares
    ``additional_scopes`` cannot be authorized here and is treated as
    unsupported; the connection handler authorizes those scopes itself.
    ``_parsed`` and ``_family_response`` let the connection handler hand over
    its single parse and an already-built family response, so that every
    response keeps going through this one canonical serialization site.
    """

    family = _family_response is not None
    if _family_response is not None:
        response = _family_response
    else:
        parsed = _parsed if _parsed is not None else _parse_message(message)
        family = parsed.family
        if family:
            response = _build_direct_family_response(parsed, family_handler)
        else:
            response = _build_v1_response(parsed, execute)
    original = response
    while True:
        try:
            if family:
                _require_finite_family_json(response)
            encoded = canonical_bytes(response, profile="sorted-compact-ascii-v1", allow_nan=True)
            if family and len(encoded) > J02_MAX_WIRE_MESSAGE_BYTES:
                raise _ResponseTooLarge
            return encoded.decode("utf-8")
        except _ResponseTooLarge:
            # J02 v1 has RESULT_TOO_LARGE for this; a 1009 close is not typed.
            reflected = dict(response)
            if response is not original:
                if response.get("request_id") is not None:
                    reflected["request_id"] = None
                else:
                    reflected = {}
            response = _family_failure(
                reflected,
                ConsumerErrorCode.RESULT_TOO_LARGE, _RESULT_TOO_LARGE_MESSAGE,
            )
            # If reflected headers overflow the failure, drop the ID first,
            # retaining the family/version when they fit, then other headers.
        except Exception:
            # Only a family result is server-composed arbitrary data; v1 keeps
            # its existing behavior for frozen Consumer API types.
            if not family or response is not original:
                raise
            response = _family_failure(
                original, ConsumerErrorCode.INTEGRITY_FAILURE, _INTEGRITY_FAILURE_MESSAGE,
            )


class _ResponseTooLarge(Exception):
    pass


def _require_finite_family_json(value: Any) -> None:
    """Reject non-JSON numbers without changing the frozen v1 encoder profile."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("family responses require finite JSON numbers")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _require_finite_family_json(key)
            _require_finite_family_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_finite_family_json(item)


def _build_direct_family_response(
    parsed: _ParsedMessage, family_handler: J02FamilyHandler | None,
) -> dict[str, Any]:
    # An additive envelope must never be coerced into a v1 query, even when it
    # also carries a perfectly valid legacy query.
    prepared, response = _prepare_family_request(parsed, family_handler)
    if prepared is None:
        return response
    if prepared.additional_scopes:
        return _family_failure(
            prepared.envelope, ConsumerErrorCode.INVALID_REQUEST, _FAMILY_UNSUPPORTED_MESSAGE,
        )
    return _execute_family_request(prepared, family_handler)


def _build_v1_response(parsed: _ParsedMessage, execute: MarketDataExecutor) -> dict[str, Any]:
    request_id: str | None = None
    try:
        if parsed.failed:
            raise ValueError("request is not valid JSON")
        payload = parsed.payload
        if not isinstance(payload, Mapping):
            raise ValueError("request payload must be a JSON object")
        request_id, query = decode_transport_query(payload)
        result = execute(query)
        return {
            "schema_version": J02_RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "status": "ok",
            "result": encode_consumer_result(result),
        }
    except ConsumerApiError as exc:
        return {
            "schema_version": J02_RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "status": "error",
            "error": encode_consumer_error(exc),
        }
    except Exception:
        return {
            "schema_version": J02_RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "status": "error",
            "error": {
                "code": ConsumerErrorCode.INVALID_REQUEST.value,
                "message": J02_INVALID_REQUEST_MESSAGE,
                "context": {},
                "request_identity": None,
            },
        }


def _validate_family_routes(
    routes: Mapping[tuple[str, str, str], J02FamilyHandler],
    remote_security: RemoteJ02SecurityConfig | None,
) -> None:
    """Fail composition early: routes are checked at startup, not per connection."""

    if routes and remote_security is None:
        # ADR-0069 section 1 places families on the WSS/mTLS endpoint; the
        # eight family scopes have no meaning on an unauthenticated listener.
        raise ValueError("family routes require remote_security (TLS/mTLS authorization)")
    for key, route in routes.items():
        if (
            not isinstance(key, tuple) or len(key) != 3
            or not all(isinstance(part, str) and part for part in key)
            or not isinstance(route, J02FamilyHandler)
        ):
            raise ValueError("family routes must map (family, version, operation) to J02FamilyHandler")
        family, version, operation = key
        declared_scope = _declared_family_scope(family, version, operation)
        if family in _FAMILY_OPERATION_SCOPES and (
            declared_scope is None or route.required_scope != declared_scope
        ):
            raise ValueError("family route must preserve its declared header scope and version")
        if route.response_schema_version != f"{family}-response-v1":
            raise ValueError("family route response_schema_version must be <family>-response-v1")
        if family == "j02-training-v1" and route.additional_scopes is None:
            # ADR-0069 section 3: the codec must declare the body-dependent
            # register grant. Missing that hook must not silently grant it.
            raise ValueError("training routes require an additional_scopes hook")


async def handle_api_transport_connection(
    websocket: Any,
    *,
    execute: MarketDataExecutor,
    remote_security: RemoteJ02SecurityConfig | None = None,
    family_handlers: FamilyHandlers | None = None,
) -> None:
    """Serve sequential J02 request/response exchanges on one WebSocket."""

    session_id = secrets.token_hex(16)
    # Snapshot trusted composition, not a request-controlled route registry.
    routes = dict(family_handlers or {})
    _validate_family_routes(routes, remote_security)
    if remote_security is not None and not await _authorize_remote_connection(
        websocket, remote_security, session_id=session_id, required_scope=None
    ):
        return

    async for message in websocket:
        # Revocation and policy removal take effect at every request boundary;
        # an already-open socket must not retain a prior authorization grant.
        # An allowed recheck records nothing: the scope check below writes the
        # single evidence row for this message.
        if remote_security is not None and not await _authorize_remote_connection(
            websocket, remote_security, session_id=session_id, required_scope=None,
            record_allow=False,
        ):
            return
        parsed = await asyncio.to_thread(_parse_message, message)
        handler = None
        if parsed.family:
            header = tuple(parsed.payload.get(name) for name in (
                "message_family", "schema_version", "operation"
            ))
            well_formed = all(isinstance(value, str) and value for value in header)
            handler = routes.get(header) if well_formed else None
            required_scope = _declared_family_scope(*header) if well_formed else None
            if required_scope is None and handler is not None:
                required_scope = handler.required_scope
        else:
            required_scope = J02_MARKET_DATA_READ_SCOPE
        if required_scope is not None and remote_security is not None:
            if not await _authorize_remote_connection(
                websocket, remote_security, session_id=session_id, required_scope=required_scope
            ):
                return
        elif parsed.family and remote_security is not None:
            # An unsupported header has no operation grant to audit. Record
            # only authenticated connection evidence, never request payload.
            if not await _authorize_remote_connection(
                websocket, remote_security, session_id=session_id, required_scope=None,
                unsupported_header=True,
            ):
                return
        if not parsed.family:
            response = await asyncio.to_thread(
                handle_api_transport_message, message, execute=execute, _parsed=parsed,
            )
        else:
            prepared, early = await asyncio.to_thread(_prepare_family_request, parsed, handler)
            if prepared is None:
                response = await asyncio.to_thread(
                    handle_api_transport_message, message, execute=execute,
                    _family_response=early,
                )
            else:
                # Body-dependent grants (ADR-0069 section 3) are authorized
                # after decode but strictly before the executor can run.
                for extra_scope in sorted(prepared.additional_scopes):
                    if not await _authorize_remote_connection(
                        websocket, remote_security, session_id=session_id,
                        required_scope=extra_scope,
                    ):
                        return
                response = await asyncio.to_thread(
                    _execute_family_wire, message, prepared, handler, execute,
                )
        await websocket.send(response)


async def run_api_transport_server(
    config: ApiTransportServerConfig,
    *,
    stop_event: asyncio.Event | None = None,
    execute: MarketDataExecutor | None = None,
    family_handlers: FamilyHandlers | None = None,
) -> None:
    """Run the J02 server until ``stop_event`` is set."""

    remote_security = config.remote_security
    # Invalid composition must stop the process at startup, not surface as a
    # ValueError inside every connection handler.
    _validate_family_routes(dict(family_handlers or {}), remote_security)
    executor = execute or compose_market_data_executor(config)
    ssl_context = remote_security.build_server_ssl_context() if remote_security is not None else None
    async with websockets.serve(
        lambda websocket: handle_api_transport_connection(
            websocket, execute=executor, remote_security=remote_security,
            family_handlers=family_handlers,
        ),
        config.host,
        config.port,
        max_size=J02_MAX_WIRE_MESSAGE_BYTES,
        ssl=ssl_context,
    ):
        if stop_event is None:
            await asyncio.Future()
        else:
            await stop_event.wait()


def _message_text(message: str | bytes) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, bytes):
        return message.decode("utf-8")
    raise TypeError("WebSocket messages must be text or UTF-8 bytes")


def _parse_message(message: str | bytes) -> _ParsedMessage:
    """Parse JSON syntax once; never decodes a semantic body and never raises."""

    lossless = True

    def lossy_float(text: str) -> float:
        nonlocal lossless
        lossless = False
        return float(text)

    def lossy_constant(name: str) -> float:
        nonlocal lossless
        lossless = False
        return float(name)

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal lossless
        mapping = dict(pairs)
        if len(mapping) != len(pairs):
            lossless = False
        return mapping

    try:
        payload = json.loads(
            _message_text(message), parse_float=lossy_float,
            parse_constant=lossy_constant, object_pairs_hook=unique_pairs,
        )
    except Exception:
        return _ParsedMessage(None, True, False)  # Preserve v1 malformed-message handling.
    return _ParsedMessage(payload, False, lossless)


def _declared_family_scope(family: str, version: str, operation: str) -> str | None:
    if version != f"{family}-request-v1":
        return None
    return _FAMILY_OPERATION_SCOPES.get(family, {}).get(operation)


_FAMILY_UNSUPPORTED_MESSAGE = "unsupported message family, schema_version or operation"
_INTEGRITY_FAILURE_MESSAGE = "the platform cannot produce a trustworthy result"
_RESULT_TOO_LARGE_MESSAGE = "the result would exceed the maximum J02 response size"


def _family_failure(
    envelope: Mapping[str, Any], code: ConsumerErrorCode, message: str,
) -> dict[str, Any]:
    response = {
        name: envelope.get(name) for name in ("message_family", "schema_version", "request_id")
    }
    for name, value in response.items():
        if not isinstance(value, str):
            response[name] = None
    response.update(status="error", error={
        "code": code.value, "message": message, "context": {}, "request_identity": None,
    })
    return response


def _prepare_family_request(
    parsed: _ParsedMessage, handler: J02FamilyHandler | None,
) -> tuple[_PreparedFamilyRequest | None, dict[str, Any] | None]:
    """Decode a family body after header authorization.

    Returns a prepared request, or a finished error response in its place.
    """

    payload = parsed.payload
    family = payload.get("message_family")
    family = family if isinstance(family, str) else None
    envelope = {
        "message_family": family,
        "schema_version": handler.response_schema_version if handler else (
            f"{family}-response-v1" if family else None
        ),
        "request_id": (
            payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
        ),
    }
    try:
        if handler is None:
            raise ValueError(_FAMILY_UNSUPPORTED_MESSAGE)
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        envelope["request_id"] = request_id
        if not parsed.lossless:
            raise ValueError("request must be lossless canonical JSON")
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("request must be a mapping")
        decoded = handler.decode_request(request)
    except ConsumerApiError as exc:
        response = dict(envelope)
        response.update(status="error", error=encode_consumer_error(exc))
        return None, response
    except Exception:
        return None, _family_failure(
            envelope, ConsumerErrorCode.INVALID_REQUEST,
            _FAMILY_UNSUPPORTED_MESSAGE if handler is None else J02_INVALID_REQUEST_MESSAGE,
        )
    try:
        extra = (
            frozenset(handler.additional_scopes(decoded))
            if handler.additional_scopes is not None else frozenset()
        )
        if not extra <= J02_REMOTE_SCOPES:
            raise ValueError("additional scope is not a registered J02 scope")
    except Exception:
        # A broken server-side hook is a platform fault, not a client error.
        return None, _family_failure(
            envelope, ConsumerErrorCode.INTEGRITY_FAILURE, _INTEGRITY_FAILURE_MESSAGE,
        )
    return _PreparedFamilyRequest(envelope, decoded, extra), None


def _execute_family_request(
    prepared: _PreparedFamilyRequest, handler: J02FamilyHandler,
) -> dict[str, Any]:
    response = dict(prepared.envelope)
    try:
        result = handler.execute(prepared.decoded)
    except ConsumerApiError as exc:
        response.update(status="error", error=encode_consumer_error(exc))
    except Exception:
        # The request already decoded: an executor fault is the server's, and
        # no raw exception text may reach the wire (ADR-0065 section 1).
        return _family_failure(
            prepared.envelope, ConsumerErrorCode.INTEGRITY_FAILURE, _INTEGRITY_FAILURE_MESSAGE,
        )
    else:
        response.update(status="ok", result=result)
    return response


def _execute_family_wire(
    message: str | bytes, prepared: _PreparedFamilyRequest, handler: J02FamilyHandler,
    execute: MarketDataExecutor,
) -> str:
    return handle_api_transport_message(
        message, execute=execute, _family_response=_execute_family_request(prepared, handler),
    )


async def _authorize_remote_connection(
    websocket: Any, security: RemoteJ02SecurityConfig, *, session_id: str,
    required_scope: str | None = J02_MARKET_DATA_READ_SCOPE,
    record_allow: bool = True,
    unsupported_header: bool = False,
) -> bool:
    """Authorize an already TLS-authenticated peer before JSON decoding starts."""
    ssl_object = websocket.transport.get_extra_info("ssl_object")
    certificate_der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    fingerprint = certificate_fingerprint(certificate_der) if certificate_der else None
    principal = security.principals_by_fingerprint.get(fingerprint) if fingerprint is not None else None
    allowed = (
        principal is not None
        and not principal.revoked
        and (
            required_scope in principal.scopes if required_scope is not None
            else bool(principal.scopes & J02_REMOTE_SCOPES)
        )
    )
    tls_version = ssl_object.version() if ssl_object is not None else None
    cipher = ssl_object.cipher() if ssl_object is not None else None
    cipher_name = cipher[0] if cipher is not None else None
    if not allowed:
        await websocket.close(code=1008, reason="policy denied")
    elif not record_allow or (
        required_scope is None and J02_MARKET_DATA_READ_SCOPE not in principal.scopes
        and not unsupported_header
    ):
        # An allowed recheck, or the scope-less admission of a family-only
        # principal, grants no operation: the per-request scope row is the
        # evidence, never an empty or invented scope.
        return True
    security.evidence_log.record(
        RemoteJ02SecurityEvidence(
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            tls_version=tls_version,
            tls_cipher=cipher_name,
            server_trust_bundle_version=security.server_trust_bundle_version,
            credential_fingerprint=fingerprint,
            principal_id=principal.principal_id if principal is not None else None,
            authorization_policy_version=security.authorization_policy_version,
            # A connection admission is not an operation grant: it keeps the
            # legacy admission label, and family-only peers record their real
            # requested scope at the message boundary.
            requested_scope=(
                "authenticated_connection" if unsupported_header
                else required_scope or J02_MARKET_DATA_READ_SCOPE
            ),
            decision="allow" if allowed else "deny",
            terminal_close_reason=(
                "unsupported_message_header" if allowed and unsupported_header
                else "application_session_authorized" if allowed else "policy_denied"
            ),
        )
    )
    return allowed


def certificate_fingerprint(certificate_der: bytes) -> str:
    """Return the exact versioned SHA-256 fingerprint of an mTLS credential."""
    if not isinstance(certificate_der, bytes) or not certificate_der:
        raise ValueError("certificate_der must be non-empty bytes")
    return "sha256:" + hashlib.sha256(certificate_der).hexdigest()


def _validate_certificate_fingerprint(fingerprint: object) -> None:
    if (
        not isinstance(fingerprint, str)
        or not fingerprint.startswith("sha256:")
        or len(fingerprint) != len("sha256:") + 64
        or any(character not in "0123456789abcdef" for character in fingerprint[len("sha256:"):])
    ):
        raise ValueError("credential fingerprint must be a sha256:<lowercase-hex> value")


def _is_loopback_bind_host(host: str) -> bool:
    text = host.strip().lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _trade_record_dict(record: Any) -> dict[str, Any]:
    return {
        "venue": record.venue,
        "instrument": record.instrument,
        "exchange_ts": record.exchange_ts.isoformat(),
        "price": record.price,
        "size": record.size,
        "aggressor_side": record.aggressor_side,
        "receive_ts": record.receive_ts.isoformat() if record.receive_ts is not None else None,
        "trade_id": record.trade_id,
        "sequence": record.sequence,
    }


__all__ = [
    "ApiTransportServerConfig",
    "J02_MAX_WIRE_MESSAGE_BYTES",
    "J02_MARKET_DATA_READ_SCOPE",
    "J02_REMOTE_SCOPES",
    "J02FamilyHandler",
    "J02_REQUEST_SCHEMA_VERSION",
    "J02_RESPONSE_SCHEMA_VERSION",
    "decode_transport_query",
    "certificate_fingerprint",
    "encode_consumer_error",
    "encode_consumer_result",
    "handle_api_transport_connection",
    "handle_api_transport_message",
    "run_api_transport_server",
    "RemoteJ02Principal",
    "RemoteJ02SecurityConfig",
    "RemoteJ02SecurityEvidence",
    "RemoteJ02SecurityEvidenceLog",
]
