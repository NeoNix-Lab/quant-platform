#!/usr/bin/env python3
"""Operator entry point for canonical Bybit L2 day ingest (A14d).

Ingests one local <day>_BTCUSDT_ob200.data.zip into canonical partitions,
manifests, and coverage documents under dataset_root. Supports opt-in
fail-closed source archive cleanup via --cleanup-source.

Exit codes:
- 0: day ingested successfully (or already published identically)
- 2: day refused (L2DayRefusal or BybitL2ArchiveError)
- 1: unhandled error
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_l2_day_ingest import (  # noqa: E402
    BybitL2ArchiveError,
    L2DayRefusal,
    ingest_bybit_l2_day,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--archive",
        required=True,
        type=Path,
        help="local <day>_BTCUSDT_ob200.data.zip",
    )
    parser.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    parser.add_argument(
        "--dataset-root",
        required=True,
        type=Path,
        help=".../canonical/l2/bybit/BTCUSDT/l2-book-event-v1",
    )
    parser.add_argument(
        "--cleanup-source",
        action="store_true",
        default=False,
        help="delete the raw source archive after successful day ingest (fail-closed on refusal)",
    )
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--created-at", default="2026-10-17T00:00:00Z")
    parser.add_argument("--closed-at", default="2026-10-17T00:00:01Z")
    parser.add_argument("--producer", default="bybit-l2-day-ingest")
    parser.add_argument("--code-ref", default="bybit-l2-day-ingest")
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        result = ingest_bybit_l2_day(
            args.archive,
            day=args.day,
            dataset_root=args.dataset_root,
            created_at=args.created_at,
            closed_at=args.closed_at,
            producer=args.producer,
            code_ref=args.code_ref,
            storage_root_id=args.storage_root_id,
            cleanup_source=args.cleanup_source,
        )
    except (L2DayRefusal, BybitL2ArchiveError) as refusal:
        code = getattr(refusal, "code", "archive_error")
        json.dump(
            {"ok": False, "refusal": {"code": code, "message": str(refusal)}},
            out,
            sort_keys=True,
        )
        out.write("\n")
        print(f"refused: {refusal}", file=err)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=err)
        return 1

    payload = {
        "ok": True,
        "day": result.day,
        "source_sha256": result.source_sha256,
        "source_events": result.source_events,
        "excluded_events": result.excluded_events,
        "partitions": [p.partition_key for p in result.partitions],
        "gaps": [g.gap_key for g in result.gaps],
        "already_published": result.already_published,
        "cleaned_source": args.cleanup_source,
    }
    json.dump(payload, out, indent=2, sort_keys=True)
    out.write("\n")
    return 0


def main() -> int:  # pragma: no cover - thin process entry
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
