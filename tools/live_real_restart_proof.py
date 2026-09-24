#!/usr/bin/env python3
"""K10 (#109) bounded real-server restart proof CLI.

This script is a thin executable wrapper around
``application.bybit_live.run_real_server_restart_proof``. It can run publish
and restart as separate OS-process phases over the same checkpoint path; it
does not own daemon orchestration or server mutation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows development shell
    grp = None
    pwd = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_live import run_real_server_restart_proof  # noqa: E402


def _identity_evidence() -> dict[str, object]:
    if pwd is None or grp is None or not hasattr(os, "getuid"):
        return {
            "platform": os.name,
            "user": os.environ.get("USERNAME") or os.environ.get("USER"),
            "is_root": False,
        }
    uid = os.getuid()
    gid = os.getgid()
    groups = os.getgroups()
    return {
        "uid": uid,
        "user": pwd.getpwuid(uid).pw_name,
        "gid": gid,
        "primary_group": grp.getgrgid(gid).gr_name,
        "supplementary_groups": sorted(grp.getgrgid(g).gr_name for g in groups),
        "is_root": uid == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-messages", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=20.0)
    parser.add_argument("--phase", choices=("publish", "restart", "both"), default="both")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument("--storage-root", help="Absolute path matching --storage-root-id in catalog.storage_roots")
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--checkpoint-path", required=True, help="K10 checkpoint JSON path owned by the live-ingest identity")
    parser.add_argument("--recent-limit", type=int, default=1000)
    parser.add_argument("--code-ref", default="k10-real-restart-proof-v1")
    parser.add_argument("--producer", default="k10-real-restart-proof-v1")
    args = parser.parse_args(argv)
    if args.phase in {"publish", "both"} and args.storage_root is None:
        parser.error("--storage-root is required for --phase publish and --phase both")

    print("=== effective runtime identity ===")
    for key, value in _identity_evidence().items():
        print(f"{key}={value}")

    print("=== K10 real-server restart proof ===")
    result = run_real_server_restart_proof(
        max_messages=args.max_messages,
        max_seconds=args.max_seconds,
        storage_root=args.storage_root or "",
        storage_root_id=args.storage_root_id,
        dsn=args.dsn,
        checkpoint_path=args.checkpoint_path,
        producer=args.producer,
        code_ref=args.code_ref,
        recent_limit=args.recent_limit,
        phase=args.phase,
    )

    publication = result.publication
    if publication is not None:
        acquisition = publication.acquisition
        print(f"publication_status={publication.status}")
        print(f"acquisition_status={acquisition.status} messages={acquisition.messages} "
              f"records={acquisition.records} final_state={acquisition.final_state}")
        for error in acquisition.errors:
            print(f"acquisition_error={error}")

        if publication.status != "PASS":
            print(f"K10_REAL_RESTART_PROOF: {publication.status}")
            return 2

        print(f"partition_key={publication.partition_key}")
        print(f"artifact_path={publication.artifact_path}")
        print(f"coverage_status={publication.coverage_status}")
        print(f"certification_status={publication.certification_status}")
        print(f"eligibility_published={publication.eligibility_published}")
        print(f"datagateway_read_record_count={publication.datagateway_read_record_count}")
        print(f"datagateway_read_matches_published={publication.datagateway_read_matches_published}")

    print(f"checkpoint_path={result.checkpoint_path}")
    print(f"checkpoint_identity={result.checkpoint_identity}")
    print(f"recent_records={result.recent_records}")
    if result.restart_outcome is not None:
        print(f"restart_status={result.restart_outcome.status}")
        print(f"restart_accepted_records={result.restart_accepted_records}")
        if result.restart_outcome.reconcile_result is not None:
            print(f"restart_reconcile_status={result.restart_outcome.reconcile_result.status.value}")
            print(f"restart_reconcile_reason={result.restart_outcome.reconcile_result.evidence.get('reason')}")

    if result.status == "PASS":
        print("K10_REAL_RESTART_PROOF: PASS")
        return 0
    if result.status == "CHECKPOINT_PERSISTED":
        print("K10_REAL_RESTART_PROOF: CHECKPOINT_PERSISTED")
        return 0
    if result.status == "GAP_DETECTED":
        print("K10_REAL_RESTART_PROOF: GAP_DETECTED")
        return 5
    if result.status == "NO_CHECKPOINT_TO_RESTART_FROM":
        print("K10_REAL_RESTART_PROOF: NO_CHECKPOINT_TO_RESTART_FROM")
        return 6
    print(f"K10_REAL_RESTART_PROOF: {result.status}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
