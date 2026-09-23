#!/usr/bin/env python3
"""K02 (#108) bounded real-server proof CLI.

Thin CLI wrapper around application.bybit_live.run_real_server_publish_proof,
which composes #107's bounded acquisition seam with the existing canonical
S13/S14 publication path and a DataGateway read-back. All orchestration
lives in the application layer; this script only handles CLI/environment
acquisition and result presentation (C05).
"""

from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import pwd
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_live import run_real_server_publish_proof  # noqa: E402


def _identity_evidence() -> dict[str, object]:
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
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument("--storage-root", required=True, help="Absolute path matching --storage-root-id in catalog.storage_roots")
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--code-ref", default="k02-real-server-proof-v1")
    parser.add_argument("--producer", default="k02-real-server-proof-v1")
    args = parser.parse_args(argv)

    print("=== effective runtime identity ===")
    for key, value in _identity_evidence().items():
        print(f"{key}={value}")

    print("=== K02 real-server publish proof ===")
    result = run_real_server_publish_proof(
        max_messages=args.max_messages, max_seconds=args.max_seconds,
        storage_root=args.storage_root, storage_root_id=args.storage_root_id,
        dsn=args.dsn, producer=args.producer, code_ref=args.code_ref,
    )

    acquisition = result.acquisition
    print(f"acquisition_status={acquisition.status} messages={acquisition.messages} "
          f"records={acquisition.records} final_state={acquisition.final_state}")
    for error in acquisition.errors:
        print(f"acquisition_error={error}")

    if result.status == "LIVE_PROVIDER_PROOF_PENDING":
        print("K02_REAL_SERVER_PROOF: LIVE_PROVIDER_PROOF_PENDING (no accepted records; nothing published)")
        return 2

    print(f"partition_key={result.partition_key}")
    print(f"artifact_path={result.artifact_path}")
    print(f"coverage_status={result.coverage_status}")
    print(f"certification_status={result.certification_status}")
    for category, status in result.certification_categories:
        print(f"  category={category} status={status}")

    if result.status == "CERTIFICATION_FAILED":
        print("K02_REAL_SERVER_PROOF: CERTIFICATION_FAILED")
        return 3

    print(f"eligibility_published={result.eligibility_published}")
    print(f"datagateway_read_record_count={result.datagateway_read_record_count}")
    print(f"datagateway_read_matches_published={result.datagateway_read_matches_published}")
    print("K02_REAL_SERVER_PROOF: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
