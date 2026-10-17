#!/usr/bin/env python3
"""Operator entry point for the canonical L2 ``v1`` golden (A14 closeout evidence).

Runs, on one pinned local ``<day>_BTCUSDT_ob200.data.zip``, the chain measurement
-> ingest -> reproducibility and, with ``--dsn``, catalog publication and read-back,
and prints one evidence JSON document.  Exit codes: ``0`` every claim holds, ``1`` a
claim failed, ``2`` the day was refused (typed ``code``).

Prerequisites for the catalog leg: the ``l2-book-event-v1`` schema registered
(``tools/bootstrap_schema_registry.py``), a ``catalog.storage_roots`` row whose
``abs_path`` is the parent of ``canonical/l2/bybit/BTCUSDT/l2-book-event-v1`` (the
``--dataset-root``), and the raw L2 parent dataset row.  The tool reaches repository
code only through ``quant_platform.application``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.l2_golden import (  # noqa: E402
    BybitL2ArchiveError,
    L2DayRefusal,
    run_l2_golden,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path, help="local <day>_BTCUSDT_ob200.data.zip")
    parser.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    parser.add_argument("--dataset-root", required=True, type=Path,
                        help=".../canonical/l2/bybit/BTCUSDT/l2-book-event-v1")
    parser.add_argument("--dsn", default=os.environ.get("CATALOG_DSN", ""),
                        help="catalog DSN for the publication and read-back leg; defaults to CATALOG_DSN")
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--created-at", default="2026-10-17T00:00:00Z")
    parser.add_argument("--closed-at", default="2026-10-17T00:00:01Z")
    parser.add_argument("--producer", default="l2-golden-e2e")
    parser.add_argument("--code-ref", default="l2-golden-e2e")
    parser.add_argument("--no-reproduce", action="store_true", help="skip the second byte-identical ingest")
    return parser


def run(argv: Sequence[str] | None = None, *, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        evidence = run_l2_golden(
            args.archive, day=args.day, dataset_root=args.dataset_root,
            created_at=args.created_at, closed_at=args.closed_at, producer=args.producer,
            code_ref=args.code_ref, dsn=args.dsn or None, storage_root_id=args.storage_root_id,
            reproduce=not args.no_reproduce,
        )
    except (L2DayRefusal, BybitL2ArchiveError) as refusal:
        json.dump({"ok": False, "refusal": {"code": refusal.code, "message": str(refusal)}}, out, sort_keys=True)
        out.write("\n")
        print(f"refused: {refusal}", file=err)
        return 2
    json.dump(evidence, out, indent=2, sort_keys=True)
    out.write("\n")
    if not evidence["ok"]:
        failed = [c["claim"] for c in evidence["claims"] if not c["ok"]]
        print(f"failed claims: {', '.join(failed)}", file=err)
        return 1
    return 0


def main() -> int:  # pragma: no cover - thin process entry
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
