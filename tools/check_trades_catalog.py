#!/usr/bin/env python3
"""Report which Bybit linear instruments the catalog holds canonical trades for (issue #485, ADR-0074 section 5).

Read-only: the session is opened with ``default_transaction_read_only=on`` and only a ``SELECT`` is issued. The connection string
is read from an environment variable (``--dsn-env``, default ``CATALOG_DSN``), never from the command line, so no password reaches
the process list or a shell history. Use a read-only role.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.instrument_catalog_check import check_trades_catalog, report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    # No abbreviations: ``--dsn`` must not be read as ``--dsn-env`` and its value taken for a variable name.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--dsn-env", default="CATALOG_DSN", help="environment variable that holds the PostgreSQL DSN")
    arguments = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", arguments.dsn_env):
        print("error: --dsn-env names an environment variable, not a connection string", file=sys.stderr)
        return 2
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        print(f"error: the environment variable {arguments.dsn_env} is not set", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as connection:
        statuses = check_trades_catalog(connection)
    print(json.dumps(report(statuses), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
