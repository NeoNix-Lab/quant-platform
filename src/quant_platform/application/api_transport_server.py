"""J02 WebSocket transport for the canonical market-data Consumer API.

This module owns transport composition only.  It carries the already-frozen
``ConsumerMarketDataQuery`` / ``ConsumerMarketDataResult`` / ``ConsumerApiError``
surface over JSON WebSocket messages, and delegates all business behavior to
``quant_platform.application.market_data``.

The current listener is local-only. ADR-0063 defines the future non-loopback
requirement (TLS 1.3 mutual TLS plus principal/scope authorization before JSON
decoding); this module does not implement that remote-security contract yet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import hashlib
import ipaddress
import json
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


def handle_api_transport_message(message: str | bytes, *, execute: MarketDataExecutor) -> str:
    """Handle one JSON request message and return one JSON response message."""

    request_id: str | None = None
    try:
        payload = json.loads(_message_text(message))
        if not isinstance(payload, Mapping):
            raise ValueError("request payload must be a JSON object")
        request_id, query = decode_transport_query(payload)
        result = execute(query)
        response = {
            "schema_version": J02_RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "status": "ok",
            "result": encode_consumer_result(result),
        }
    except ConsumerApiError as exc:
        response = {
            "schema_version": J02_RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "status": "error",
            "error": encode_consumer_error(exc),
        }
    except Exception:
        response = {
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
    return canonical_bytes(response, profile="sorted-compact-ascii-v1", allow_nan=True).decode("utf-8")


async def handle_api_transport_connection(
    websocket: Any,
    *,
    execute: MarketDataExecutor,
    remote_security: RemoteJ02SecurityConfig | None = None,
) -> None:
    """Serve sequential J02 request/response exchanges on one WebSocket."""

    session_id = secrets.token_hex(16)
    if remote_security is not None and not await _authorize_remote_connection(
        websocket, remote_security, session_id=session_id
    ):
        return

    async for message in websocket:
        # Revocation and policy removal take effect at every request boundary;
        # an already-open socket must not retain a prior authorization grant.
        if remote_security is not None and not await _authorize_remote_connection(
            websocket, remote_security, session_id=session_id
        ):
            return
        response = await asyncio.to_thread(handle_api_transport_message, message, execute=execute)
        await websocket.send(response)


async def run_api_transport_server(
    config: ApiTransportServerConfig,
    *,
    stop_event: asyncio.Event | None = None,
    execute: MarketDataExecutor | None = None,
) -> None:
    """Run the J02 server until ``stop_event`` is set."""

    executor = execute or compose_market_data_executor(config)
    remote_security = config.remote_security
    ssl_context = remote_security.build_server_ssl_context() if remote_security is not None else None
    async with websockets.serve(
        lambda websocket: handle_api_transport_connection(
            websocket, execute=executor, remote_security=remote_security
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


async def _authorize_remote_connection(
    websocket: Any, security: RemoteJ02SecurityConfig, *, session_id: str
) -> bool:
    """Authorize an already TLS-authenticated peer before JSON decoding starts."""
    ssl_object = websocket.transport.get_extra_info("ssl_object")
    certificate_der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    fingerprint = certificate_fingerprint(certificate_der) if certificate_der else None
    principal = security.principals_by_fingerprint.get(fingerprint) if fingerprint is not None else None
    allowed = (
        principal is not None
        and not principal.revoked
        and J02_MARKET_DATA_READ_SCOPE in principal.scopes
    )
    tls_version = ssl_object.version() if ssl_object is not None else None
    cipher = ssl_object.cipher() if ssl_object is not None else None
    cipher_name = cipher[0] if cipher is not None else None
    if not allowed:
        await websocket.close(code=1008, reason="policy denied")
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
            requested_scope=J02_MARKET_DATA_READ_SCOPE,
            decision="allow" if allowed else "deny",
            terminal_close_reason="application_session_authorized" if allowed else "policy_denied",
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
