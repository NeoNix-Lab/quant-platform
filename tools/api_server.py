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
    ApiTransportServerConfig,
    run_api_transport_server,
)


def _default_dsn() -> str:
    return os.environ.get("CATALOG_DSN", "")


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
        allow_non_loopback=args.allow_non_loopback,
    )
    print("=== J02 API transport server v1 ===")
    print(f"host={config.host}")
    print(f"port={config.port}")
    print(f"batch_size={config.batch_size}")
    await run_api_transport_server(config, stop_event=stop_event)
    print("J02_API_TRANSPORT_SERVER: STOPPED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("QP_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QP_API_PORT", "8765")))
    parser.add_argument("--dsn", default=_default_dsn(), help="Catalog DSN; defaults to CATALOG_DSN")
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("QP_API_BATCH_SIZE", "65536")))
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        default=os.environ.get("QP_API_ALLOW_NON_LOOPBACK", "").lower() in {"1", "true", "yes"},
        help="Permit binding to a non-loopback interface; authentication remains out of scope for J02 v1",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
