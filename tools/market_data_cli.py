#!/usr/bin/env python3
"""J04 canonical CLI client for the market-data Consumer API.

The CLI uses ADR-0050's bounded in-process C03 path: it submits a
``ConsumerMarketDataQuery`` to the Application service and renders the
canonical result/error payload.  It owns argv and presentation only.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
import os
from pathlib import Path
import sys
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    DEFAULT_MAX_RESULT_ROWS,
    ConsumerApiError,
    ConsumerMarketDataQuery,
    ConsumerMarketDataResult,
    MarketDataApplicationConfig,
    RepresentationRef,
    compose_market_data_application,
    encode_consumer_error,
    encode_consumer_result,
)


J04_CLI_OUTPUT_SCHEMA_VERSION = "j04-cli-output-v1"
Executor = Callable[[ConsumerMarketDataQuery], ConsumerMarketDataResult]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--start", required=True, help="UTC RFC 3339 interval start")
    parser.add_argument("--end", required=True, help="UTC RFC 3339 interval end")
    parser.add_argument("--dsn", default=os.environ.get("CATALOG_DSN", ""), help="Catalog DSN; defaults to CATALOG_DSN")
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("QP_CLI_BATCH_SIZE", "65536")))
    parser.add_argument(
        "--max-result-rows",
        type=int,
        default=int(os.environ.get("QP_CLI_MAX_RESULT_ROWS", str(DEFAULT_MAX_RESULT_ROWS))),
        help="refuse a query whose result would exceed this many rows (ADR-0050 Amendment 1, #247)",
    )
    return parser


def query_from_args(args: argparse.Namespace) -> ConsumerMarketDataQuery:
    return ConsumerMarketDataQuery(
        venue=args.venue,
        instrument=args.instrument,
        start=args.start,
        end=args.end,
        representation=RepresentationRef("trades", 1),
        options={},
    )


def render_success(result: ConsumerMarketDataResult) -> dict[str, object]:
    return {
        "schema_version": J04_CLI_OUTPUT_SCHEMA_VERSION,
        "status": "ok",
        "result": encode_consumer_result(result),
    }


def render_error(error: ConsumerApiError) -> dict[str, object]:
    return {
        "schema_version": J04_CLI_OUTPUT_SCHEMA_VERSION,
        "status": "error",
        "error": encode_consumer_error(error),
    }


def run_market_data_cli(
    argv: Sequence[str] | None = None,
    *,
    execute: Executor | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    executor = execute
    if executor is None:
        application = compose_market_data_application(
            MarketDataApplicationConfig(
                catalog_dsn=args.dsn,
                batch_size=args.batch_size,
                max_result_rows=args.max_result_rows,
            )
        )
        executor = application.execute

    try:
        payload = render_success(executor(query_from_args(args)))
        exit_code = 0
    except ConsumerApiError as exc:
        payload = render_error(exc)
        exit_code = 2

    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), file=out)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    return run_market_data_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
