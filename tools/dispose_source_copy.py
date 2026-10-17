#!/usr/bin/env python3
"""Operator entry point: dispose of ONE physical copy of a raw source archive.

ADR-0049 Amendment 1 (issue #423).  The copy named by ``--candidate-*`` is removed
only if the other copy named by ``--surviving-*``, in a different storage root, is
freshly re-hashed equal, every canonical partition named by ``--partition`` is
``valid`` in the catalog with its installed bytes re-hashed equal and bound to this
archive, and a K06 assessment over the surviving copy alone is ``PROTECTED``.  The
tool never guesses a second copy, never removes the last copy and has no default for
the accountable authority, the decision time or the provenance.

Exit codes: 0 removed or already absent; 2 refused (nothing touched); 3 permitted but
not removed (failed, nothing lost); 1 any other error.
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

from quant_platform.application.retention_deletion import PostgresRetentionDeletionAuditStore  # noqa: E402
from quant_platform.application.source_copy_disposal import (  # noqa: E402
    SourceCopyDisposalApplicationError,
    SourceCopyDisposalOutcome,
    SourceCopyLocation,
    dispose_source_copy,
    l2_canonical_dataset_identity,
    l2_raw_dataset_identity,
    read_catalog_canonical_partitions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True, help="catalog PostgreSQL DSN (audit and partition state)")
    parser.add_argument("--venue", default="bybit")
    parser.add_argument("--instrument", default="BTCUSDT")
    parser.add_argument("--candidate-root-id", required=True)
    parser.add_argument("--candidate-root", required=True, type=Path, help="directory of the candidate's storage root")
    parser.add_argument("--candidate-rel-path", required=True)
    parser.add_argument("--surviving-root-id", required=True)
    parser.add_argument("--surviving-root", required=True, type=Path, help="directory of the surviving copy's storage root")
    parser.add_argument("--surviving-rel-path", required=True)
    parser.add_argument("--source-sha256", required=True, help="pinned SHA-256 of the archive")
    parser.add_argument("--source-bytes", required=True, type=int, help="pinned size of the archive in bytes")
    parser.add_argument("--partition", required=True, action="append", dest="partitions",
                        help="canonical partition fed by the archive, e.g. dt=2025-12-01/seg=001 (repeat)")
    parser.add_argument("--canonical-dataset-root", type=Path, default=None,
                        help="resolve partition files under this directory instead of the catalog storage root")
    parser.add_argument("--authority", required=True, help="accountable authority recorded in the audit")
    parser.add_argument("--decision-time", required=True, help="explicit decision instant, ISO-8601 UTC")
    parser.add_argument("--finalized-at", required=True, help="instant the archive was acquired/pinned, ISO-8601 UTC")
    parser.add_argument("--producer", required=True)
    parser.add_argument("--code-ref", required=True)
    return parser


def run(argv: Sequence[str] | None = None, *, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        import psycopg

        connection = psycopg.connect(args.dsn, autocommit=True)  # every audit write commits at once
        try:
            resolver = None
            if args.canonical_dataset_root is not None:
                base = args.canonical_dataset_root

                def resolver(storage_root, dataset_rel_root, rel_path):  # noqa: ARG001
                    return base / Path(*rel_path.split("/"))

            kwargs = {} if resolver is None else {"path_resolver": resolver}
            partitions = read_catalog_canonical_partitions(
                connection, l2_canonical_dataset_identity(args.instrument, args.venue), args.partitions, **kwargs
            )
            result = dispose_source_copy(
                dataset_identity=l2_raw_dataset_identity(args.instrument, args.venue),
                candidate=SourceCopyLocation(args.candidate_root_id, args.candidate_root, args.candidate_rel_path),
                surviving=SourceCopyLocation(args.surviving_root_id, args.surviving_root, args.surviving_rel_path),
                candidate_sha256=args.source_sha256,
                candidate_byte_size=args.source_bytes,
                canonical_partitions=partitions,
                authority=args.authority,
                decision_time=args.decision_time,
                producer=args.producer,
                code_ref=args.code_ref,
                finalized_at=args.finalized_at,
                audit_store=PostgresRetentionDeletionAuditStore(connection),
            )
        finally:
            connection.close()
    except SourceCopyDisposalApplicationError as exc:
        print(f"error: {exc}", file=err)
        json.dump({"ok": False, "error": str(exc)}, out, sort_keys=True)
        out.write("\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - operator tool: report, never raise a traceback away
        print(f"error: {type(exc).__name__}: {exc}", file=err)
        json.dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, out, sort_keys=True)
        out.write("\n")
        return 1

    payload = {
        "ok": result.outcome in (SourceCopyDisposalOutcome.REMOVED, SourceCopyDisposalOutcome.ALREADY_ABSENT),
        "outcome": result.outcome.value,
        "decision_id": result.decision_identity,
        "refusal_reasons": list(result.refusal_reasons),
        "deleted": result.deleted,
        "deleted_paths": list(result.deleted_paths),
        "failure_reason": result.failure_reason,
        "detail": result.detail,
    }
    json.dump(payload, out, indent=2, sort_keys=True)
    out.write("\n")
    if result.outcome == SourceCopyDisposalOutcome.REFUSED:
        return 2
    if result.outcome == SourceCopyDisposalOutcome.FAILED:
        return 3
    return 0


def main() -> int:  # pragma: no cover - thin process entry
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
