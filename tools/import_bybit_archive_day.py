#!/usr/bin/env python3
"""Import one UTC day of Bybit linear trades from a local public-archive file (DG-N T04, ADR-0074 Amendment 1).

The connection string is read from an environment variable (``--dsn-env``, default ``CATALOG_DSN``), never from the command line.
``--code-ref`` defaults to the commit of this checkout and is refused when the tree has uncommitted changes, so the manifests name
the code that really produced them. Exit status 0 when the day is published ``valid``, 1 when it is refused, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_archive_import import (  # noqa: E402
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    ArchiveImportRefused,
    import_bybit_archive_day,
)


def _clean_head() -> str | None:
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    if status.stdout.strip():
        return None
    return head.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True, choices=BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)
    parser.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    parser.add_argument("--archive", required=True, type=Path, help="local <INSTRUMENT><YYYY-MM-DD>.csv.gz")
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--dsn-env", default="CATALOG_DSN", help="environment variable that holds the PostgreSQL DSN")
    parser.add_argument("--code-ref", help="producing code identity; default: the clean HEAD commit of this checkout")
    arguments = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", arguments.dsn_env):
        print("error: --dsn-env names an environment variable, not a connection string", file=sys.stderr)
        return 2
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        print(f"error: the environment variable {arguments.dsn_env} is not set", file=sys.stderr)
        return 2
    code_ref = arguments.code_ref or _clean_head()
    if not code_ref:
        print("error: the checkout has uncommitted changes (or no git); commit them or pass --code-ref", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(dsn) as connection:
        try:
            report = import_bybit_archive_day(
                connection, instrument=arguments.instrument, day=arguments.day, archive_path=arguments.archive,
                storage_root_id=arguments.storage_root_id, code_ref=code_ref,
            )
        except ArchiveImportRefused as exc:
            print(json.dumps({"instrument": arguments.instrument, "day": arguments.day, "refused": exc.phase,
                              "detail": str(exc)}, sort_keys=True))
            return 1
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0 if report.state == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
