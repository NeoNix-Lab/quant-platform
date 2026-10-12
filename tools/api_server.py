#!/usr/bin/env python3
"""J02 canonical API transport server CLI.

This executable owns argv, environment defaults and OS signal handling only.
The WebSocket transport and Consumer API mapping live in
``quant_platform.application.api_transport_server``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    DEFAULT_MAX_RESULT_ROWS,
    ApiTransportServerConfig,
    deployment_commit,
    open_runtime_connection,
    record_deployment_commit,
    run_api_transport_server,
)


EXIT_RUNTIME_NOT_CONFIGURED = 2


def _default_dsn() -> str:
    return os.environ.get("CATALOG_DSN", "")


def _record_commit(dsn: str) -> None:
    """ADR-0068 section 8: record this deployment's commit in ``runtime``.

    Mandatory and fail-closed: the API does not start without a runtime DSN,
    and a failed write stops it. Otherwise a previous deployment's record would
    stay in place and a worker on the *old* commit would be accepted against it.
    """
    commit = deployment_commit(ROOT)
    connection = open_runtime_connection(dsn)
    try:
        record_deployment_commit(connection, commit)
    finally:
        connection.close()
    print(f"deployment_commit={commit}")


async def _run(args: argparse.Namespace) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, _request_stop)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - Windows shell fallback
            signal.signal(signum, lambda _signum, _frame: loop.call_soon_threadsafe(_request_stop))

    config = ApiTransportServerConfig(
        host=args.host,
        port=args.port,
        catalog_dsn=args.dsn,
        batch_size=args.batch_size,
        max_result_rows=args.max_result_rows,
        allow_non_loopback=args.allow_non_loopback,
    )
    print("=== J02 API transport server v1 ===")
    print(f"host={config.host}")
    print(f"port={config.port}")
    print(f"batch_size={config.batch_size}")
    print(f"max_result_rows={config.max_result_rows}")
    _record_commit(args.runtime_dsn)
    await run_api_transport_server(config, stop_event=stop_event)
    print("J02_API_TRANSPORT_SERVER: STOPPED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("QP_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QP_API_PORT", "8765")))
    parser.add_argument("--dsn", default=_default_dsn(), help="Catalog DSN; defaults to CATALOG_DSN")
    parser.add_argument(
        "--runtime-dsn",
        default=os.environ.get("QP_RUNTIME_DSN", ""),
        help="runtime DSN of the API login (required): the deployment commit is recorded at startup",
    )
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("QP_API_BATCH_SIZE", "65536")))
    parser.add_argument(
        "--max-result-rows",
        type=int,
        default=int(os.environ.get("QP_API_MAX_RESULT_ROWS", str(DEFAULT_MAX_RESULT_ROWS))),
        help="refuse a query whose result would exceed this many rows (ADR-0050 Amendment 1, #247)",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        default=os.environ.get("QP_API_ALLOW_NON_LOOPBACK", "").lower() in {"1", "true", "yes"},
        help="Permit binding to a non-loopback interface; authentication remains out of scope for J02 v1",
    )
    args = parser.parse_args(argv)
    if not args.runtime_dsn.strip():
        # Checked before anything binds or connects: no transport may start
        # while the deployment record could still name a previous commit.
        print(
            "api_server: FATAL: a runtime DSN is required (--runtime-dsn or QP_RUNTIME_DSN); "
            "the API records its deployment commit at startup (ADR-0068 section 8)",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_NOT_CONFIGURED
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
