#!/usr/bin/env python3
"""J05 terminal UI client for the J02 market-data WebSocket transport.

The client owns only argv, WebSocket exchange and terminal rendering.  It
does not import ``quant_platform`` and must not contain domain behavior: the
request/response shapes are the ADR-0050 J02 wire envelopes.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
import os
import uuid
from typing import Any, TextIO
import sys

import websockets


J02_REQUEST_SCHEMA_VERSION = "j02-request-v1"
J02_RESPONSE_SCHEMA_VERSION = "j02-response-v1"
J05_TUI_SCREEN_SCHEMA_VERSION = "j05-tui-screen-v1"
DEFAULT_URL = "ws://127.0.0.1:8765"

# ADR-0050 Amendment 1 (#247): must match J02's own
# application.api_transport_server.J02_MAX_WIRE_MESSAGE_BYTES exactly. This
# client cannot import that constant (it must not import quant_platform at
# all), so the literal is duplicated here by design; a cross-file consistency
# test (tests/test_api_transport_server_v1.py) keeps the two from drifting.
# Without this, the websockets library's implicit 1 MiB default silently
# truncates the connection with a raw ConnectionClosedError on any response
# over ~3,990 trades.
J02_MAX_WIRE_MESSAGE_BYTES = 16 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("QP_API_URL", DEFAULT_URL))
    parser.add_argument("--venue", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--start", required=True, help="UTC RFC 3339 interval start")
    parser.add_argument("--end", required=True, help="UTC RFC 3339 interval end")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--max-rows", type=int, default=int(os.environ.get("QP_TUI_MAX_ROWS", "20")))
    return parser


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    request_id = args.request_id or f"j05-tui-{uuid.uuid4()}"
    return {
        "schema_version": J02_REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "query": {
            "venue": args.venue,
            "instrument": args.instrument,
            "start": args.start,
            "end": args.end,
            "representation": {"kind": "trades", "version": 1, "definition": {}},
            "options": {},
        },
    }


async def fetch_j02_response(
    url: str,
    request: Mapping[str, Any],
    *,
    connect: Callable[..., Any] = websockets.connect,
) -> dict[str, Any]:
    try:
        async with connect(url, max_size=J02_MAX_WIRE_MESSAGE_BYTES) as websocket:
            await websocket.send(json.dumps(dict(request), sort_keys=True, separators=(",", ":")))
            response = json.loads(await websocket.recv())
    except _connection_exception_types() as exc:
        return _transport_error_response(url, request, exc)
    if not isinstance(response, dict):
        raise ValueError("J02 response must be a JSON object")
    return response


def render_screen(response: Mapping[str, Any], *, max_rows: int = 20) -> str:
    lines = [
        "Quant Platform Market Data TUI",
        f"schema_version={J05_TUI_SCREEN_SCHEMA_VERSION}",
        f"transport_schema_version={response.get('schema_version')}",
        f"request_id={response.get('request_id')}",
        f"status={response.get('status')}",
    ]
    if response.get("schema_version") != J02_RESPONSE_SCHEMA_VERSION:
        lines.append("warning=unexpected transport schema")

    if response.get("status") == "ok":
        result = _mapping(response.get("result"))
        request = _mapping(result.get("request"))
        coverage = _mapping(result.get("coverage"))
        lines.extend([
            "",
            f"venue={request.get('venue')}",
            f"instrument={request.get('instrument')}",
            f"representation={_mapping(request.get('representation')).get('kind')}@{_mapping(request.get('representation')).get('version')}",
            f"interval={request.get('start')}..{request.get('end')}",
            f"row_count={result.get('row_count')}",
            f"coverage_complete={coverage.get('complete')}",
            f"coverage_gaps={len(_sequence(coverage.get('gaps')))}",
            "",
            _table(_sequence(result.get("data")), max_rows=max_rows),
        ])
    elif response.get("status") == "error":
        error = _mapping(response.get("error"))
        lines.extend([
            "",
            f"error_code={error.get('code')}",
            f"message={error.get('message')}",
            f"request_identity={error.get('request_identity')}",
            "context=" + json.dumps(error.get("context", {}), sort_keys=True, separators=(",", ":")),
        ])
    else:
        lines.extend(["", "error_code=invalid_transport_response", "message=unknown response status"])
    return "\n".join(lines) + "\n"


def run_market_data_tui(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[..., Any] = websockets.connect,
    stdout: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    out = stdout or sys.stdout
    response = asyncio.run(fetch_j02_response(args.url, build_request(args), connect=connect))
    out.write(render_screen(response, max_rows=args.max_rows))
    return 0 if response.get("status") == "ok" else 2


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _connection_exception_types() -> tuple[type[BaseException], ...]:
    exception_types: list[type[BaseException]] = [OSError, TimeoutError]
    websockets_exception = getattr(getattr(websockets, "exceptions", None), "WebSocketException", None)
    if isinstance(websockets_exception, type) and issubclass(websockets_exception, BaseException):
        exception_types.append(websockets_exception)
    return tuple(exception_types)


def _transport_error_response(
    url: str,
    request: Mapping[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    return {
        "schema_version": J02_RESPONSE_SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "status": "error",
        "error": {
            "code": "connection_failed",
            "message": "unable to reach J02 transport",
            "context": {
                "url": url,
                "reason": str(exc),
                "exception_type": type(exc).__name__,
            },
            "request_identity": None,
        },
    }


def _table(rows: Sequence[Any], *, max_rows: int) -> str:
    selected = [_mapping(row) for row in rows[:max(0, max_rows)]]
    header = f"{'exchange_ts':<27} {'price':>14} {'size':>14} {'side':<6} {'trade_id'}"
    separator = "-" * len(header)
    body = [
        f"{str(row.get('exchange_ts', '')):<27} {str(row.get('price', '')):>14} "
        f"{str(row.get('size', '')):>14} {str(row.get('aggressor_side', '')):<6} "
        f"{row.get('trade_id', '')}"
        for row in selected
    ]
    if len(rows) > len(selected):
        body.append(f"... {len(rows) - len(selected)} more rows")
    if not body:
        body.append("(no rows)")
    return "\n".join([header, separator, *body])


def main(argv: Sequence[str] | None = None) -> int:
    return run_market_data_tui(argv)


if __name__ == "__main__":
    raise SystemExit(main())
