#!/usr/bin/env python3
"""Operator CLI: Wave 4 Golden End-to-End deterministic replay proof (issue #146).

Executes two independent replay runs against the real canonical Bybit
BTCUSDT ``trade-v1`` dataset and proves their ``trace_fingerprint`` is
bitwise identical -- the SCOPE.md Milestone V7 acceptance requirement
("Deterministic Replay Reproducibility").

All domain composition (DataGateway, strategy, replay spec) lives in
``quant_platform.application.golden_replay``; this script only parses CLI
input and reports the result, per ADR-0024 (executable orchestration must
not reach directly into domain packages).

This requires a real, already-ingested canonical dataset reachable through a
live DataGateway catalog; it cannot run against synthetic test fixtures.
Point --dsn (or GOLDEN_REPLAY_E2E_DSN / DATA_GATEWAY_TEST_DSN) at your real
catalog connection string.

Example:
    python tools/golden_replay_e2e.py \\
        --dsn postgresql://user:pass@host/db \\
        --start 2024-01-15T00:00:00Z --end 2024-01-16T00:00:00Z
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import run_golden_replay_proof  # noqa: E402

_MISSING = object()


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolved_input(cli_value: Any, *, env_names: tuple[str, ...] = (), default: Any = _MISSING, field: str) -> Any:
    if cli_value is not None:
        return cli_value
    value = _env_value(*env_names)
    if value is not None:
        return value
    if default is not _MISSING:
        return default
    flag = "--" + field.replace("_", "-")
    raise SystemExit(f"error: {field} is not configured (pass {flag} or set one of {env_names})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dsn", default=None, help="catalog DSN (Postgres connection string)")
    parser.add_argument("--start", required=True, help="replay interval start, UTC RFC3339 (e.g. 2024-01-15T00:00:00Z)")
    parser.add_argument("--end", required=True, help="replay interval end, UTC RFC3339")
    parser.add_argument("--initial-capital", default="10000", help="starting cash (default: 10000)")
    parser.add_argument("--lookback", type=int, default=20, help="breakout channel lookback in trades (default: 20)")
    parser.add_argument("--batch-size", type=int, default=65_536, help="DataGateway.scan() batch size")
    args = parser.parse_args()

    dsn = _resolved_input(
        args.dsn, env_names=("GOLDEN_REPLAY_E2E_DSN", "DATA_GATEWAY_TEST_DSN"), field="dsn"
    )

    print(f"Interval: {args.start} .. {args.end}")
    proof = run_golden_replay_proof(
        dsn=dsn,
        start=args.start,
        end=args.end,
        initial_capital=args.initial_capital,
        lookback=args.lookback,
        batch_size=args.batch_size,
    )

    first, second = proof.first, proof.second
    print(f"Spec identity: {proof.spec_identity}")
    print()
    print(f"Run 1 trace_fingerprint: {first.trace_fingerprint}")
    print(f"Run 2 trace_fingerprint: {second.trace_fingerprint}")
    print(f"Identical:               {proof.deterministic}")
    print()
    print(f"Orders:                   {len(first.orders)}")
    print(f"Fills:                    {len(first.fills)}")
    print(f"Final ledger book_equity: {first.final_ledger.book_equity}")
    print(f"Final ledger identity:    {first.final_ledger.identity}")

    if not proof.deterministic:
        print("FAIL: replay is not bitwise-deterministic across independent runs.", file=sys.stderr)
        raise SystemExit(1)
    if len(first.orders) == 0:
        print(
            "WARNING: zero orders were generated over this interval -- the proof is "
            "vacuously deterministic. Widen --start/--end or lower --lookback so the "
            "engine is genuinely exercised.",
            file=sys.stderr,
        )
    print("PASS: replay is bitwise-deterministic across independent runs.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
