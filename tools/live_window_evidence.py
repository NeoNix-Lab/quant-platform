#!/usr/bin/env python3
"""Evidence of a continuous live run against the Bybit archive (ADR-0075 slice 9.3, DG-O ``O03``; the ``T02`` one-hour proof).

Read-only: it reads the catalog and files through the ``DataGateway``, the local archive files of the days the run touched and,
optionally, the captured stdout of ``tools/live_ingest_server.py``; it writes nothing. It prints one JSON report: the archive
trades in the window the run claimed, the trades a ``strict`` gateway read returns, the missing and the extra ones (a missing one
is classified as inside a claimed window, an undeclared loss, or inside an unclaimed span, the declared cost of an interruption),
the holes, the slice sizes and the counters of the session (ordering violations, reconnects, buffer high-water mark, slice lag).

Exit status 0 when no stop condition holds, 1 when one does (the report lists them), 2 on a usage error. A shortfall against the
archive that falls in an unclaimed span is reported, not hidden, and is not a stop condition.

Run it after the archive of the last day of the window is published (Bybit publishes a day after it ends).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.live_window_evidence import (  # noqa: E402
    archive_days_missing,
    build_window_evidence,
    gateway_strict_reader,
    load_archive_days,
    parse_window,
    read_live_slices,
)
from quant_platform.application.live_ingest_server import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True, choices=BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument("--archive", required=True, nargs="+", type=Path,
                        help="the local <INSTRUMENT><YYYY-MM-DD>.csv.gz file of every UTC day the window touches")
    parser.add_argument("--start", default=None, help="window start (UTC, ISO); default: the first valid live slice")
    parser.add_argument("--end", default=None, help="window end, exclusive (UTC, ISO); default: the end of the last valid live slice")
    parser.add_argument("--signals-log", type=Path, default=None, help="captured stdout of tools/live_ingest_server.py")
    parser.add_argument("--code-ref", default=None, help="the commit the run executed (recorded in the report)")
    parser.add_argument("--mode", default=None, help="the mode the run used (recorded in the report)")
    parser.add_argument("--slice-seconds", type=int, default=None, help="recorded in the report")
    parser.add_argument("--slice-max-trades", type=int, default=None, help="recorded in the report")
    parser.add_argument("--buffer-messages", type=int, default=None, help="recorded in the report")
    arguments = parser.parse_args(argv)
    try:
        window = parse_window(arguments.start, arguments.end)
        archive_days = load_archive_days(arguments.archive, instrument=arguments.instrument)
    except Exception as error:  # noqa: BLE001 - a usage problem, reported as such
        parser.error(str(error))

    slices = read_live_slices(arguments.dsn, arguments.instrument)
    needed, missing = archive_days_missing(slices, window, archive_days)
    if missing:
        parser.error(f"the window touches {', '.join(needed)}; no archive file for {', '.join(missing)}")
    signals_text = None if arguments.signals_log is None else arguments.signals_log.read_text(encoding="utf-8", errors="replace")
    read_segment, connection = gateway_strict_reader(arguments.dsn, arguments.instrument)
    try:
        report = build_window_evidence(
            instrument=arguments.instrument,
            archive=(record for records in archive_days.values() for record in records),
            slices=slices, read_segment=read_segment, window=window, signals_text=signals_text,
            configuration={"code_ref": arguments.code_ref, "mode": arguments.mode, "slice_seconds": arguments.slice_seconds,
                           "slice_max_trades": arguments.slice_max_trades, "buffer_messages": arguments.buffer_messages},
        )
    finally:
        connection.close()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
