#!/usr/bin/env python3
"""CLI wrapper for the Application-owned Bybit trade importer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    CANONICAL_FIELD_ORDER,
    ImportError_,
    ImportReport,
    RECORD_SCHEMA_ID,
    SUPPORTED_CATEGORY,
    SUPPORTED_INSTRUMENT,
    SUPPORTED_VENUE,
    TradeStats,
    WriteResult,
    encode_record,
    format_exchange_ts,
    run_import,
    write_jsonl_atomic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_bybit_trades",
        description="Reference importer: trades Bybit da SQLite a trade-v1 JSONL.",
    )
    parser.add_argument("--sqlite", required=True, help="percorso del database SQLite sorgente")
    parser.add_argument("--date", required=True, help="giornata UTC da importare, YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="percorso dell'artifact JSON Lines da produrre")
    parser.add_argument("--venue", default=SUPPORTED_VENUE)
    parser.add_argument("--category", default=SUPPORTED_CATEGORY)
    parser.add_argument("--instrument", default=SUPPORTED_INSTRUMENT)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "sostituisce un artifact gia' esistente; senza questo flag un output "
            "esistente fa fallire l'import"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_import(
            args.sqlite,
            args.date,
            args.output,
            venue=args.venue,
            category=args.category,
            instrument=args.instrument,
            overwrite=args.overwrite,
        )
    except ImportError_ as exc:
        print(f"IMPORT FALLITO: {exc}", file=sys.stderr)
        print("nessun artifact finale e' stato creato.", file=sys.stderr)
        return 1

    print(f"venue/instrument     {args.venue} / {args.instrument} ({args.category})")
    print(f"giornata UTC         {args.date}")
    print(f"righe sorgente       {report.source_rows:,}")
    print(f"righe canoniche      {report.canonical_rows:,}")
    print(f"  aggressor buy      {report.buy:,}")
    print(f"  aggressor sell     {report.sell:,}")
    print(f"first exchange_ts    {report.first_exchange_ts}")
    print(f"last  exchange_ts    {report.last_exchange_ts}")
    print(f"receive_ts non-null  {report.receive_ts_non_null}")
    print(f"sequence non-null    {report.sequence_non_null}")
    print(f"byte scritti         {report.bytes_written:,}")
    print(f"sha256               {report.sha256}")
    print(f"output               {report.output}")
    return 0


__all__ = [
    "CANONICAL_FIELD_ORDER",
    "ImportError_",
    "ImportReport",
    "RECORD_SCHEMA_ID",
    "SUPPORTED_CATEGORY",
    "SUPPORTED_INSTRUMENT",
    "SUPPORTED_VENUE",
    "TradeStats",
    "WriteResult",
    "build_parser",
    "encode_record",
    "format_exchange_ts",
    "main",
    "run_import",
    "write_jsonl_atomic",
]


if __name__ == "__main__":
    sys.exit(main())
