#!/usr/bin/env python3
"""Compare one Bybit archive day with an existing trade-v1 Parquet file of that day (ADR-0074 Amendment 1, A1.2 item 8).

Read-only: it reads the local archive file and the Parquet file and writes nothing; no catalog is involved. Use it on an old
file before it is replaced, or on a published BTCUSDT day for the equivalence proof. Prints one JSON report; exit status 0 when
the two hold the same trades (same ``trade_id`` set, same millisecond timestamp, side, price and size values), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_archive_import import (  # noqa: E402
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    compare_archive_day_with_parquet,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True, choices=BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)
    parser.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    parser.add_argument("--archive", required=True, type=Path, help="local <INSTRUMENT><YYYY-MM-DD>.csv.gz")
    parser.add_argument("--parquet", required=True, type=Path, help="existing trade-v1 Parquet file of the same day")
    arguments = parser.parse_args(argv)
    report = compare_archive_day_with_parquet(
        arguments.archive, arguments.parquet, instrument=arguments.instrument, day=arguments.day,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["same_trades"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
