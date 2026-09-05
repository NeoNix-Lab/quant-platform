#!/usr/bin/env python3
"""Optional real-source Golden E2E for the production Bybit historical seam.

The SQLite source is intentionally external and must be supplied by the
operator. This script never downloads, creates, or modifies source data.

Golden facts are never restated here: they come from the canonical fixture
through ``golden_conformity_support.load_golden_expectation()``, which also
enforces the fixture's own invariants.

Temporal facts are compared as INSTANTS, never as rendered strings. A string
comparison would silently depend on how a timestamp is formatted -- and
``Instant.isoformat()`` strips trailing zeros, so a day whose first or last
trade landed on a trailing-zero millisecond would report a false failure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from golden_conformity_support import load_golden_expectation  # noqa: E402
from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.source_adapters.bybit_historical import (  # noqa: E402
    BybitHistoricalExtractAccumulator,
    canonicalize_bybit_historical_trade_v1,
    iter_bybit_historical_trade_rows,
    open_bybit_historical_source,
    utc_day_bounds_ms,
)


class Observation:
    """O(1) state over the whole extract: counters and boundary instants."""

    __slots__ = ("rows", "buy", "sell", "first", "last",
                 "receive_ts_non_null", "sequence_non_null")

    def __init__(self) -> None:
        self.rows = self.buy = self.sell = 0
        self.receive_ts_non_null = self.sequence_non_null = 0
        self.first = self.last = None

    def observe(self, record) -> None:
        self.rows += 1
        if record.aggressor_side == "buy":
            self.buy += 1
        elif record.aggressor_side == "sell":
            self.sell += 1
        if self.first is None:
            self.first = record.exchange_ts
        self.last = record.exchange_ts
        if record.receive_ts is not None:
            self.receive_ts_non_null += 1
        if record.sequence is not None:
            self.sequence_non_null += 1


def extract(sqlite_path: Path, start_ms: int, end_ms: int):
    """One complete streaming pass. Never materializes the record stream."""

    observation = Observation()
    accumulator = BybitHistoricalExtractAccumulator()
    connection = open_bybit_historical_source(sqlite_path)
    try:
        for row in iter_bybit_historical_trade_rows(
                connection, start_ms, end_ms, accumulator=accumulator):
            observation.observe(canonicalize_bybit_historical_trade_v1(row))
    finally:
        connection.close()
    return observation, accumulator


def run(sqlite_path: Path) -> int:
    golden = load_golden_expectation()
    day = golden.interval_start[:10]
    start_ms, end_ms = utc_day_bounds_ms(day)

    observation, accumulator = extract(sqlite_path, start_ms, end_ms)
    evidence = accumulator.evidence(day, start_ms, end_ms)

    failures = []

    def check(condition, what, detail=None):
        print(f"  {'PASS' if condition else 'FAIL'} {what}"
              + (f"   {detail}" if detail else ""))
        if not condition:
            failures.append(what)

    check(observation.rows == golden.row_count,
          f"row_count == {golden.row_count}", observation.rows)
    check(observation.buy == golden.buy, f"buy == {golden.buy}", observation.buy)
    check(observation.sell == golden.sell, f"sell == {golden.sell}", observation.sell)
    check(observation.buy + observation.sell == observation.rows,
          "buy + sell cover every row: no 'unknown'")

    # Semantic instant equality, not string equality.
    expected_first = Instant.parse(golden.first_exchange_ts)
    expected_last = Instant.parse(golden.last_exchange_ts)
    check(observation.first is not None
          and observation.first.epoch_ns == expected_first.epoch_ns,
          f"first exchange_ts == {golden.first_exchange_ts} (as an instant)",
          None if observation.first is None else observation.first.epoch_ns)
    check(observation.last is not None
          and observation.last.epoch_ns == expected_last.epoch_ns,
          f"last exchange_ts == {golden.last_exchange_ts} (as an instant)",
          None if observation.last is None else observation.last.epoch_ns)

    check(observation.receive_ts_non_null == 0,
          "receive_ts non-null == 0", observation.receive_ts_non_null)
    check(observation.sequence_non_null == 0,
          "sequence non-null == 0", observation.sequence_non_null)

    check(evidence.source_row_count == golden.row_count,
          "source evidence row count equals the Golden row count",
          evidence.source_row_count)

    # A second complete pass: the extract fingerprint must be reproducible.
    # Two source passes are acceptable here because this is verification,
    # not the normal runtime path.
    second_observation, second_accumulator = extract(sqlite_path, start_ms, end_ms)
    second_evidence = second_accumulator.evidence(day, start_ms, end_ms)
    check(second_observation.rows == observation.rows,
          "two complete extractions observe the same row count")
    check(second_evidence.source_fingerprint_sha256
          == evidence.source_fingerprint_sha256,
          "two complete extractions produce the same source fingerprint",
          evidence.source_fingerprint_sha256)

    print(json.dumps({
        "venue": golden.venue,
        "instrument": golden.instrument,
        "day": day,
        "rows": observation.rows,
        "buy": observation.buy,
        "sell": observation.sell,
        "first_exchange_ts_epoch_ns": observation.first.epoch_ns if observation.first else None,
        "last_exchange_ts_epoch_ns": observation.last.epoch_ns if observation.last else None,
        "extract_evidence": evidence.detail,
    }, indent=2))

    if failures:
        print(f"FAIL: {len(failures)} checks did not pass")
        for name in failures:
            print(f"  - {name}")
        return 1
    print(f"PASS: Golden day {day} reproduced through the production seam")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path)
    args = parser.parse_args(argv)
    if args.sqlite is None:
        print("NOT EXECUTED — INFRASTRUCTURE PRECONDITION: --sqlite was not supplied")
        return 0
    if not args.sqlite.is_file():
        print(f"NOT EXECUTED — INFRASTRUCTURE PRECONDITION: missing {args.sqlite}")
        return 0
    return run(args.sqlite)


if __name__ == "__main__":
    raise SystemExit(main())
