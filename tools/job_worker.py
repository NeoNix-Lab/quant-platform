#!/usr/bin/env python3
"""The single J03 data-local worker host (ADR-0068 sections 6 and 8, P04b).

This executable owns argv, environment defaults and OS signal handling only.
The start-up order (advisory lock, commit check, one recovery, dispatch loop)
lives in ``quant_platform.application.job_worker_runtime``; the closed handler
registry in ``quant_platform.application.job_dispatch``.

Exit status: 0 after a clean stop; 2 when another worker holds the lock; 3 when
the commit differs from the one the API recorded (or none was recorded); 1 for
any other fatal error. No Job is touched on 2 or 3.

SIGTERM/SIGINT request a stop: the in-flight handler finishes and its outcome
is recorded before the process exits. A handler is never interrupted
mid-effect, and a second signal does not change that; a SIGKILL leaves the Job
for the next worker's restart recovery.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    DATA_LOCAL_JOB_REGISTRY,
    DurableJobStore,
    JobWorker,
    PostgresAdvisoryLock,
    PostgresCommitRecord,
    WorkerCommitMismatch,
    WorkerLockHeld,
    deployment_commit,
    open_runtime_connection,
)

EXIT_LOCK_HELD = 2
EXIT_COMMIT_MISMATCH = 3


def _install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(_signum, _frame) -> None:
        stop.set()

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is not None:
            signal.signal(signum, request_stop)


def run(args: argparse.Namespace, *, stop: threading.Event | None = None) -> int:
    stop = stop if stop is not None else threading.Event()
    _install_signal_handlers(stop)
    commit = args.commit or deployment_commit(ROOT)
    print("=== J03 worker v1 ===", flush=True)
    print(f"commit={commit}", flush=True)
    connection = open_runtime_connection(args.dsn)
    worker = JobWorker(
        store=DurableJobStore(connection),
        registry=DATA_LOCAL_JOB_REGISTRY,
        lock=PostgresAdvisoryLock(connection),
        commit_record=PostgresCommitRecord(connection),
        commit=commit,
        stop=stop,
        poll_interval=args.poll_interval,
        report=lambda line: print(f"J03_WORKER: {line}", flush=True),
    )
    try:
        try:
            worker.start()
        except WorkerLockHeld as refusal:
            print(f"J03_WORKER: REFUSED: {refusal}", file=sys.stderr, flush=True)
            return EXIT_LOCK_HELD
        except WorkerCommitMismatch as refusal:
            print(f"J03_WORKER: REFUSED: {refusal}", file=sys.stderr, flush=True)
            return EXIT_COMMIT_MISMATCH
        print("J03_WORKER: READY", flush=True)
        dispatched = worker.run()
        print(f"J03_WORKER: STOPPED dispatched={dispatched}", flush=True)
        return 0
    finally:
        worker.close()
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn", default=os.environ.get("QP_RUNTIME_DSN", ""),
        help="runtime DSN of the worker login; defaults to QP_RUNTIME_DSN",
    )
    parser.add_argument(
        "--commit", default=os.environ.get("QP_GIT_COMMIT", ""),
        help="Git commit of this deployment; defaults to QP_GIT_COMMIT, then `git rev-parse HEAD`",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=float(os.environ.get("QP_WORKER_POLL_SECONDS", "1.0")),
        help="seconds to wait when no registered Job is QUEUED",
    )
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as error:  # noqa: BLE001 - last-resort host boundary
        print(f"J03_WORKER: FATAL: {type(error).__name__}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
