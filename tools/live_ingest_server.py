#!/usr/bin/env python3
"""ADR-0043 bounded live-ingest server v1 CLI.

This script is a thin executable wrapper around
``application.live_ingest_server.run_live_ingest_server``: it owns argv/
environment parsing and OS signal handling only (DG-D/C05 boundary), and
never contains acquisition/publication/checkpoint logic itself. It runs the
same bounded restart/reconcile composition PR #122 proved as two separate
OS-process invocations, now as one continuous process.

``--mode continuous`` (ADR-0075, DG-O) runs ONE WebSocket session instead of the bounded cycles; the default, ``cycles``, is the
existing behaviour. The flags only build the typed configuration; the session itself is ``application.live_session``.

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
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    LiveIngestServerConfigV1,
    LiveSessionConfig,
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


MODE_CYCLES = "cycles"
MODE_CONTINUOUS = "continuous"
# The three bounds of the continuous session (ADR-0075 section 2). An empty value means "not given": a systemd unit renders an
# unset ``${QP_LIVE_INGEST_...}`` as an empty string, so an environment file that predates the option still yields a valid command.
_SESSION_BOUNDS = (
    ("--slice-seconds", "slice_seconds"),
    ("--slice-max-trades", "slice_max_trades"),
    ("--buffer-messages", "buffer_messages"),
)


def _session_config(parser: argparse.ArgumentParser, args: argparse.Namespace) -> LiveSessionConfig | None:
    """The continuous-session configuration the flags ask for; ``None`` for the bounded cycles (the existing behaviour)."""

    mode = args.mode or MODE_CYCLES
    if mode not in (MODE_CYCLES, MODE_CONTINUOUS):
        parser.error(f"--mode must be {MODE_CYCLES} or {MODE_CONTINUOUS}, not {mode!r}")
    given: dict[str, int] = {}
    for flag, name in _SESSION_BOUNDS:
        raw = getattr(args, name)
        if raw is None or raw == "":
            continue
        try:
            given[name] = int(raw)
        except ValueError:
            parser.error(f"{flag} must be a positive integer, not {raw!r}")
    if mode == MODE_CYCLES:
        if given:
            parser.error(f"{', '.join(flag for flag, name in _SESSION_BOUNDS if name in given)} can only be used with --mode {MODE_CONTINUOUS}")
        return None
    try:
        return LiveSessionConfig(recent_limit=args.recent_limit, **given)
    except ValueError as error:
        parser.error(str(error))


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
    parser.add_argument("--instrument", default="BTCUSDT", choices=BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
                        help="The one Bybit linear instrument this process ingests (ADR-0074); each instrument needs "
                             "its own checkpoint path")
    parser.add_argument("--checkpoint-path", required=True, help="K10 checkpoint JSON path owned by the live-ingest identity")
    parser.add_argument("--recent-limit", type=int, default=1000)
    parser.add_argument("--code-ref", default="live-ingest-server-v1")
    parser.add_argument("--producer", default="live-ingest-server-v1")
    parser.add_argument("--max-messages-per-cycle", type=int, default=1000)
    parser.add_argument("--max-seconds-per-cycle", type=float, default=20.0)
    parser.add_argument("--cycle-interval-seconds", type=float, default=5.0,
                         help="Pause between completed cycles so a fast/empty cycle cannot spin (ADR-0043 S4)")
    parser.add_argument("--max-cycles", type=int, default=None,
                         help="Stop cleanly after this many completed health-snapshot cycles (in continuous mode, one per published slice); "
                              "intended for bounded proofs")
    parser.add_argument("--mode", default=MODE_CYCLES,
                        help="cycles (default): the bounded cycle loop of ADR-0043; continuous: one WebSocket session that is not "
                             "closed between publications (ADR-0075). An empty value means cycles.")
    parser.add_argument("--slice-seconds", default=None,
                        help="continuous only: wall-clock seconds after which an open slice is closed (default 300)")
    parser.add_argument("--slice-max-trades", default=None,
                        help="continuous only: trades after which an open slice is closed (default 50000)")
    parser.add_argument("--buffer-messages", default=None,
                        help="continuous only: bound of the reader's message buffer; a full buffer ends the session (default 20000)")
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
    session = _session_config(parser, args)

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
        instrument=args.instrument,
        continuous_session=session,
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
    print(f"instrument={args.instrument}")
    print(f"mode={MODE_CYCLES if session is None else MODE_CONTINUOUS}")
    if session is not None:
        print(f"slice_seconds={session.slice_seconds} slice_max_trades={session.slice_max_trades} "
              f"buffer_messages={session.buffer_messages}")
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
