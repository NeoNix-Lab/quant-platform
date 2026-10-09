#!/usr/bin/env python3
"""J15 Omega remote client adapter v1 for the J02 market-data Consumer API.

ADR-0067 freezes this as Omega's *sole* remote boundary: the client sends the
exact ``j02-request-v1`` envelope and accepts only the exact
``j02-response-v1`` envelope for the C03 market-data read admitted by the
``j02.market_data.read`` scope (ADR-0063).  It never imports
``quant_platform``, never falls back to a local package call, and never
retries across a different wire family.  An unexpected schema version or
message family is a local typed ``WireIncompatible`` failure; a TLS, mTLS, or
scope-authorization failure is a local typed ``RemoteSecurityFailure``; a
real ``status: "error"`` envelope is losslessly reconstructed as a
``RemoteConsumerApiError`` and is not reinterpreted as either of the above.
Omega renders the server-issued request identity, provenance, coverage,
result, and error fields; it owns no canonical data, catalog state, or
accepted-artifact identity of its own.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
import ssl
import sys
from typing import Any, TextIO
import uuid

import websockets
from websockets.exceptions import ConnectionClosed, InvalidMessage


J02_REQUEST_SCHEMA_VERSION = "j02-request-v1"
J02_RESPONSE_SCHEMA_VERSION = "j02-response-v1"
J02_MARKET_DATA_READ_SCOPE = "j02.market_data.read"
DEFAULT_URL = "wss://127.0.0.1:8765"

# Must match quant_platform.application.api_transport_server.J02_MAX_WIRE_MESSAGE_BYTES.
# This client cannot import quant_platform at all (ADR-0067 s1), so the literal
# is duplicated by design, same pattern as clients/tui/market_data_tui.py; a
# cross-file consistency test keeps the two from drifting silently.
J02_MAX_WIRE_MESSAGE_BYTES = 16 * 1024 * 1024

# WebSocket policy-denial close code used by the server before JSON decoding
# (ADR-0063 s2): an authenticated principal without the required scope, a
# revoked principal, or an unmapped credential fingerprint is denied here.
_POLICY_DENIAL_CLOSE_CODE = 1008

Connector = Callable[..., Any]


class WireIncompatible(Exception):
    """An unexpected schema version or message family was not a usable J02 v1 reply.

    ADR-0067 s3: this is never retried with a different wire family and never
    reinterpreted as a ``RemoteConsumerApiError``.
    """


class RemoteSecurityFailure(Exception):
    """TLS, mTLS, or scope authorization failed before any Consumer API result existed.

    ADR-0067 s4: this is never converted into a ``RemoteConsumerApiError`` and
    never triggers a plaintext fallback.
    """


@dataclass(frozen=True, slots=True)
class RemoteConsumerApiError(Exception):
    """Lossless local mirror of a server-issued ``ConsumerApiError`` wire payload."""

    code: str
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)
    request_identity: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class RemoteMarketDataResult:
    """Rendered C03 market-data result: a passthrough of the server's wire fields.

    Every field is exactly what the server sent; this adapter neither
    recomputes nor reinterprets any of it.
    """

    request: Mapping[str, Any]
    request_identity: str
    representation: Mapping[str, Any]
    data: tuple[Mapping[str, Any], ...]
    requested_interval: Mapping[str, Any]
    returned_temporal_bounds: Mapping[str, Any] | None
    coverage: Mapping[str, Any]
    provenance: Mapping[str, Any]
    row_count: int


def build_client_ssl_context(
    *,
    client_certificate_path: str,
    client_private_key_path: str,
    server_trust_anchor_path: str,
) -> ssl.SSLContext:
    """Build the strict TLS 1.3 mTLS client context required by ADR-0063."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_verify_locations(cafile=server_trust_anchor_path)
    context.load_cert_chain(client_certificate_path, client_private_key_path)
    return context


def build_market_data_request(
    *,
    venue: str,
    instrument: str,
    start: str,
    end: str,
    request_id: str = "",
) -> dict[str, Any]:
    """Build the exact ``j02-request-v1`` C03 market-data read envelope."""

    return {
        "schema_version": J02_REQUEST_SCHEMA_VERSION,
        "request_id": request_id or f"j15-omega-{uuid.uuid4()}",
        "query": {
            "venue": venue,
            "instrument": instrument,
            "start": start,
            "end": end,
            "representation": {"kind": "trades", "version": 1, "definition": {}},
            "options": {},
        },
    }


async def fetch_market_data(
    url: str,
    request: Mapping[str, Any],
    *,
    ssl_context: ssl.SSLContext,
    connect: Connector = websockets.connect,
) -> RemoteMarketDataResult:
    """Send one C03 market-data request and render its J02 v1 reply.

    Raises ``RemoteSecurityFailure`` for a TLS/mTLS/scope denial, raises
    ``WireIncompatible`` for anything that is not a recognizable
    ``j02-response-v1`` envelope, and raises ``RemoteConsumerApiError`` for a
    well-formed ``status: "error"`` reply.
    """

    raw = await _exchange(url, request, ssl_context=ssl_context, connect=connect)
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WireIncompatible(f"response was not valid JSON: {exc}") from exc
    return _render_response(envelope)


async def _exchange(
    url: str,
    request: Mapping[str, Any],
    *,
    ssl_context: ssl.SSLContext,
    connect: Connector,
) -> str:
    try:
        async with connect(
            url,
            ssl=ssl_context,
            max_size=J02_MAX_WIRE_MESSAGE_BYTES,
        ) as websocket:
            await websocket.send(json.dumps(dict(request), sort_keys=True, separators=(",", ":")))
            return await websocket.recv()
    except ssl.SSLError as exc:
        raise RemoteSecurityFailure(f"TLS/mTLS handshake failed: {exc}") from exc
    except InvalidMessage as exc:
        # websockets surfaces an aborted TLS handshake (untrusted chain, expired
        # or revoked peer certificate, hostname mismatch) as a failure to read
        # an HTTP response rather than the underlying ssl.SSLError (the
        # asyncio TLS transport reports it to the loop's exception handler
        # instead of the awaiting coroutine); this client only ever speaks
        # wss to a J09-configured listener, so a failed opening handshake here
        # is a TLS/mTLS failure, not an application-level condition.
        raise RemoteSecurityFailure(f"TLS/mTLS handshake failed: {exc.__cause__ or exc}") from exc
    except ConnectionClosed as exc:
        close = exc.rcvd or exc.sent
        if close is not None and close.code == _POLICY_DENIAL_CLOSE_CODE:
            raise RemoteSecurityFailure(
                f"remote policy denied the connection: {close.reason or 'policy denied'}"
            ) from exc
        raise


def _render_response(envelope: Any) -> RemoteMarketDataResult:
    if not isinstance(envelope, Mapping) or envelope.get("schema_version") != J02_RESPONSE_SCHEMA_VERSION:
        raise WireIncompatible(
            "unexpected response schema_version "
            f"{envelope.get('schema_version') if isinstance(envelope, Mapping) else envelope!r}"
        )
    status = envelope.get("status")
    if status == "error":
        error = envelope.get("error")
        if not isinstance(error, Mapping):
            raise WireIncompatible("error envelope missing a mapping 'error' field")
        raise RemoteConsumerApiError(
            code=error.get("code"),
            message=error.get("message"),
            context=dict(error.get("context") or {}),
            request_identity=error.get("request_identity"),
        )
    if status != "ok":
        raise WireIncompatible(f"unexpected response status {status!r}")
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        raise WireIncompatible("ok envelope missing a mapping 'result' field")
    return RemoteMarketDataResult(
        request=result.get("request", {}),
        request_identity=result.get("request_identity"),
        representation=result.get("representation", {}),
        data=tuple(result.get("data") or ()),
        requested_interval=result.get("requested_interval", {}),
        returned_temporal_bounds=result.get("returned_temporal_bounds"),
        coverage=result.get("coverage", {}),
        provenance=result.get("provenance", {}),
        row_count=result.get("row_count"),
    )


def render_screen(outcome: RemoteMarketDataResult | RemoteConsumerApiError | WireIncompatible | RemoteSecurityFailure) -> str:
    """Render a terminal-friendly summary of a J15 fetch outcome."""

    if isinstance(outcome, RemoteMarketDataResult):
        return "\n".join([
            "Omega J15 remote market-data result",
            f"request_identity={outcome.request_identity}",
            f"row_count={outcome.row_count}",
            f"coverage_complete={_mapping(outcome.coverage).get('complete')}",
            f"dataset_identity={_mapping(outcome.provenance).get('dataset_identity')}",
        ]) + "\n"
    if isinstance(outcome, RemoteConsumerApiError):
        return "\n".join([
            "Omega J15 remote consumer error",
            f"error_code={outcome.code}",
            f"message={outcome.message}",
            f"request_identity={outcome.request_identity}",
        ]) + "\n"
    if isinstance(outcome, WireIncompatible):
        return f"Omega J15 wire_incompatible: {outcome}\n"
    return f"Omega J15 remote_security_failure: {outcome}\n"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("QP_J15_URL", DEFAULT_URL))
    parser.add_argument("--venue", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--start", required=True, help="UTC RFC 3339 interval start")
    parser.add_argument("--end", required=True, help="UTC RFC 3339 interval end")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--client-certificate", required=True)
    parser.add_argument("--client-private-key", required=True)
    parser.add_argument("--server-trust-anchor", required=True)
    return parser


async def run_once(args: argparse.Namespace) -> RemoteMarketDataResult | RemoteConsumerApiError:
    request = build_market_data_request(
        venue=args.venue,
        instrument=args.instrument,
        start=args.start,
        end=args.end,
        request_id=args.request_id,
    )
    ssl_context = build_client_ssl_context(
        client_certificate_path=args.client_certificate,
        client_private_key_path=args.client_private_key,
        server_trust_anchor_path=args.server_trust_anchor,
    )
    try:
        return await fetch_market_data(args.url, request, ssl_context=ssl_context)
    except RemoteConsumerApiError as error:
        return error


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    import asyncio

    args = build_parser().parse_args(argv)
    out = stdout or sys.stdout
    try:
        outcome = asyncio.run(run_once(args))
    except (WireIncompatible, RemoteSecurityFailure) as outcome_error:
        out.write(render_screen(outcome_error))
        return 2
    out.write(render_screen(outcome))
    return 0 if isinstance(outcome, RemoteMarketDataResult) else 2


if __name__ == "__main__":
    raise SystemExit(main())
