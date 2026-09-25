#!/usr/bin/env python3
"""ADR-0043 bounded live-ingest server v1 CLI.

This script is a thin executable wrapper around
``application.live_ingest_server.run_live_ingest_server``: it owns argv/
environment parsing and OS signal handling only (DG-D/C05 boundary), and
never contains acquisition/publication/checkpoint logic itself. It runs the
same bounded restart/reconcile composition PR #122 proved as two separate
OS-process invocations, now as one continuous process.

SIGTERM/SIGINT flip a stop flag checked between cycles -- never a hard kill
of an in-flight publish -- so a stop always lands on ADR-0042 S3's ordinary
"crash after checkpoint advance" case, not a new invariant.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import os
from pathlib import Path
import signal
import sys

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows development shell
    grp = None
    pwd = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.live_ingest_server import (  # noqa: E402
    LiveIngestServerConfigV1,
    PressurePolicyDefinition,
    SERVER_FAILED,
    SERVER_GAP_DETECTED_AWAITING_REMEDIATION,
    SERVER_STOPPED,
    run_live_ingest_server,
)


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


def _print_signal(signal_evidence) -> None:
    payload = ",".join(f"{k}={v}" for k, v in sorted(signal_evidence.payload.items()))
    print(
        f"signal kind={signal_evidence.kind.value} capability={signal_evidence.capability_id} "
        f"signal_id={signal_evidence.signal_id} {payload}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument("--storage-root", required=True, help="Absolute path matching --storage-root-id in catalog.storage_roots")
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--checkpoint-path", required=True, help="K10 checkpoint JSON path owned by the live-ingest identity")
    parser.add_argument("--recent-limit", type=int, default=1000)
    parser.add_argument("--code-ref", default="live-ingest-server-v1")
    parser.add_argument("--producer", default="live-ingest-server-v1")
    parser.add_argument("--max-messages-per-cycle", type=int, default=1000)
    parser.add_argument("--max-seconds-per-cycle", type=float, default=20.0)
    parser.add_argument("--cycle-interval-seconds", type=float, default=5.0,
                         help="Pause between completed cycles so a fast/empty cycle cannot spin (ADR-0043 S4)")
    parser.add_argument("--max-cycles", type=int, default=None,
                         help="Stop cleanly after this many completed health-snapshot cycles; intended for bounded proofs")
    parser.add_argument("--pressure-available-bytes", type=int, default=50 * 1024**3,
                         help="K05 PRESSURE threshold on available bytes (deployment-local; default 50 GiB)")
    parser.add_argument("--pressure-critical-available-bytes", type=int, default=20 * 1024**3,
                         help="K05 CRITICAL threshold on available bytes (default 20 GiB)")
    parser.add_argument("--pressure-exhausted-available-bytes", type=int, default=5 * 1024**3,
                         help="K05 EXHAUSTED threshold on available bytes (default 5 GiB)")
    parser.add_argument("--pressure-max-capacity-age-seconds", type=float, default=300.0,
                         help="Maximum age of a capacity observation before K05 treats it as stale")
    args = parser.parse_args(argv)
    if args.max_cycles is not None and args.max_cycles < 1:
        parser.error("--max-cycles must be >= 1")

    print("=== effective runtime identity ===")
    for key, value in _identity_evidence().items():
        print(f"{key}={value}")

    pressure_policy = PressurePolicyDefinition(
        max_capacity_observation_age=timedelta(seconds=args.pressure_max_capacity_age_seconds),
        pressure_available_bytes=args.pressure_available_bytes,
        critical_available_bytes=args.pressure_critical_available_bytes,
        exhausted_available_bytes=args.pressure_exhausted_available_bytes,
    )
    config = LiveIngestServerConfigV1(
        storage_root=args.storage_root,
        storage_root_id=args.storage_root_id,
        checkpoint_path=args.checkpoint_path,
        pressure_policy=pressure_policy,
        dsn=args.dsn,
        producer=args.producer,
        code_ref=args.code_ref,
        max_messages_per_cycle=args.max_messages_per_cycle,
        max_seconds_per_cycle=args.max_seconds_per_cycle,
        recent_limit=args.recent_limit,
        cycle_interval_seconds=args.cycle_interval_seconds,
    )

    stop_state = {"stop": False}

    def _request_stop(signum, _frame):
        print(f"received signal {signum}; stopping after the current cycle completes")
        stop_state["stop"] = True

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    completed_cycles = {"count": 0}

    def _emit_and_maybe_stop(signal_evidence) -> None:
        _print_signal(signal_evidence)
        if signal_evidence.kind.value == "HEALTH_SNAPSHOT":
            completed_cycles["count"] += 1
            if args.max_cycles is not None and completed_cycles["count"] >= args.max_cycles:
                print(f"max cycles reached ({args.max_cycles}); stopping after the current cycle")
                stop_state["stop"] = True

    print("=== live-ingest server v1 ===")
    report = run_live_ingest_server(
        config,
        stop_requested=lambda: stop_state["stop"],
        on_signal=_emit_and_maybe_stop,
    )

    print(f"cycles={report.cycles}")
    print(f"LIVE_INGEST_SERVER: {report.status}")
    if report.status == SERVER_STOPPED:
        return 0
    if report.status == SERVER_GAP_DETECTED_AWAITING_REMEDIATION:
        return 5
    if report.status == SERVER_FAILED:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
