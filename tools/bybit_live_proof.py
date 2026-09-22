#!/usr/bin/env python3
"""Bounded real-provider proof for A11 Bybit live trades v1."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_live import (  # noqa: E402
    LiveProviderProofPending,
    build_arg_parser,
    run_bounded_live_provider_proof_sync,
)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = run_bounded_live_provider_proof_sync(
            max_messages=args.max_messages,
            max_seconds=args.max_seconds,
        )
    except LiveProviderProofPending as exc:
        print(str(exc))
        print(
            "COMMAND: python tools/bybit_live_proof.py "
            f"--max-messages {args.max_messages} --max-seconds {args.max_seconds:g}"
        )
        return 2
    print(f"A11_LIVE_PROVIDER_PROOF: {report.status}")
    print(f"topic={report.topic}")
    print(f"messages={report.messages}")
    print(f"records={report.records}")
    print(f"duplicates_removed={report.duplicates_removed}")
    print(f"final_state={report.final_state}")
    for error in report.errors:
        print(f"error={error}")
    return 0 if report.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
