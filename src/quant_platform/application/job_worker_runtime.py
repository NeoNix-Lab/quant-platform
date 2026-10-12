"""The single J03 worker's lifecycle and the API's deployment-commit record (P04b).

ADR-0068 sections 6 and 8 over ADR-0062 (unchanged). Exactly one worker runs.
Its lifecycle is a fixed order, so that nothing touches a Job before the
worker is entitled to:

1. take a PostgreSQL *session-level* advisory lock on a fixed key, or refuse;
2. compare its own Git commit with the one the API recorded, or refuse;
3. call ``DurableJobStore.recover_after_restart`` exactly once;
4. loop on :meth:`JobDispatcher.dispatch_next`.

Why the lock makes step 3 correct: it is released only when the previous
worker's database session ends, so every ``RUNNING`` Job visible after the lock
is held belongs to a dead process, and the store's *global* recovery cannot
mark another worker's live work. No per-process attempt ownership is needed and
ADR-0062 section 4's attempt fields are unchanged.

A stop request lets the in-flight handler finish and records its outcome; a
handler is never interrupted mid-effect. If the process is killed instead, the
Job stays ``RUNNING`` and the next worker's step 3 marks it
``RECOVERY_REQUIRED``; it is never redispatched automatically.

This module owns the lifecycle only. argv, environment and signal handling
belong to the ``tools/job_worker.py`` host; handlers belong to their own issues.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import threading
from typing import Any, Protocol

from psycopg import Connection, connect

from .durable_jobs import DurableJobStore
from .job_dispatch import JobDispatcher, JobHandlerRegistry

# Fixed session-level advisory-lock key of the one J03 worker. It shares the
# database-wide advisory key space with the store transaction locks, which use
# 33101 (jobs), 33102 (admitted inputs) and 33103 (K13 evidence).
WORKER_ADVISORY_LOCK_KEY = 33110
_MAX_COMMIT_LENGTH = 128


class WorkerStartRefused(RuntimeError):
    """The worker is not entitled to start; no Job has been touched."""


class WorkerLockHeld(WorkerStartRefused):
    """Another worker holds the lock (or this process cannot take it)."""


class WorkerCommitMismatch(WorkerStartRefused):
    """The worker's commit differs from, or is not recorded by, the API's."""


class WorkerLock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class CommitRecord(Protocol):
    def recorded(self) -> str | None: ...


class PostgresAdvisoryLock:
    """Session-level advisory lock held for as long as ``connection`` lives."""

    def __init__(self, connection: Connection, key: int = WORKER_ADVISORY_LOCK_KEY):
        _require_autocommit(connection)
        self.connection = connection
        self.key = key

    def acquire(self) -> bool:
        row = self.connection.execute("SELECT pg_try_advisory_lock(%s)", (self.key,)).fetchone()
        return bool(row[0])

    def release(self) -> None:
        self.connection.execute("SELECT pg_advisory_unlock(%s)", (self.key,))


class PostgresCommitRecord:
    """Read-only view of the commit the API recorded."""

    def __init__(self, connection: Connection):
        _require_autocommit(connection)
        self.connection = connection

    def recorded(self) -> str | None:
        return recorded_deployment_commit(self.connection)


class JobWorker:
    """One worker's start-up sequence and dispatch loop."""

    def __init__(
        self,
        *,
        store: DurableJobStore,
        registry: JobHandlerRegistry,
        lock: WorkerLock,
        commit_record: CommitRecord,
        commit: str,
        stop: threading.Event,
        poll_interval: float = 1.0,
        report: Callable[[str], None] | None = None,
    ):
        if not isinstance(store, DurableJobStore):
            raise TypeError("store must be a DurableJobStore")
        if not isinstance(registry, JobHandlerRegistry):
            raise TypeError("registry must be a JobHandlerRegistry")
        if not isinstance(stop, threading.Event):
            raise TypeError("stop must be a threading.Event")
        if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or poll_interval <= 0:
            raise ValueError("poll_interval must be a positive number")
        self.commit = _commit_text(commit)
        self.dispatcher = JobDispatcher(store, registry)
        self.store = store
        self.lock = lock
        self.commit_record = commit_record
        self.stop = stop
        self.poll_interval = float(poll_interval)
        self._report = report or (lambda _line: None)
        self._holding = False
        self._started = False

    def start(self) -> tuple[str, ...]:
        """Steps 1-3. Returns the Job ids recovery marked ``RECOVERY_REQUIRED``."""

        if self._started:
            raise RuntimeError("a worker starts once")
        self._started = True
        if not self.lock.acquire():
            raise WorkerLockHeld("another J03 worker holds the advisory lock")
        self._holding = True
        try:
            recorded = self.commit_record.recorded()
            if recorded is None:
                raise WorkerCommitMismatch("the API has not recorded a deployment commit")
            if recorded != self.commit:
                raise WorkerCommitMismatch(
                    "worker commit differs from the commit recorded by the API"
                )
            self._report("lock held; commit matches the API record")
            recovered = tuple(job.job_id for job in self.store.recover_after_restart())
        except BaseException:
            self.close()
            raise
        self._report(f"recovered {len(recovered)} interrupted job(s) as RECOVERY_REQUIRED")
        return recovered

    def run(self) -> int:
        """Step 4. Returns how many Jobs reached a durable outcome."""

        if not self._holding:
            raise RuntimeError("run() requires a started worker")
        dispatched = 0
        while not self.stop.is_set():
            result = self.dispatcher.dispatch_next()
            if result.job is not None:
                dispatched += 1
                # A stop request is honoured only here, after the outcome of the
                # in-flight handler is durable: nothing is interrupted mid-effect.
                continue
            self.stop.wait(self.poll_interval)
        return dispatched

    def close(self) -> None:
        """Release the lock (idempotent). The session ending releases it too."""

        if not self._holding:
            return
        self._holding = False
        try:
            self.lock.release()
        except Exception:  # Connection already gone: the lock went with it.
            pass

    def serve(self) -> int:
        """start(), run(), then always close()."""

        try:
            self.start()
            return self.run()
        finally:
            self.close()


def record_deployment_commit(connection: Connection, commit: str) -> None:
    """The API's single startup write: the commit every unit must run."""

    _require_autocommit(connection)
    connection.execute(
        "INSERT INTO runtime.deployment_commit (singleton, git_commit) VALUES (true, %s) "
        "ON CONFLICT (singleton) DO UPDATE "
        "SET git_commit = EXCLUDED.git_commit, recorded_at = now()",
        (_commit_text(commit),),
    )


def recorded_deployment_commit(connection: Connection) -> str | None:
    row = connection.execute("SELECT git_commit FROM runtime.deployment_commit").fetchone()
    return None if row is None else str(row[0])


def open_runtime_connection(dsn: str) -> Connection:
    """An autocommit connection for a unit's own runtime login."""

    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("a runtime DSN is required")
    return connect(dsn, autocommit=True)


def deployment_commit(root: Path) -> str:
    """The Git commit this checkout runs.

    ``QP_GIT_COMMIT`` takes precedence, for deployments that carry no ``.git``
    (a release tarball sets it from release metadata); otherwise ``HEAD``.
    """

    configured = os.environ.get("QP_GIT_COMMIT", "").strip()
    if configured:
        return _commit_text(configured)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("deployment commit unavailable: set QP_GIT_COMMIT")
    return _commit_text(result.stdout.strip())


def _commit_text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_COMMIT_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError("commit must be a non-empty token of at most 128 characters")
    return value


def _require_autocommit(connection: Connection) -> None:
    if not isinstance(connection, Connection) or not connection.autocommit:
        raise ValueError("an autocommit psycopg connection is required")


__all__ = [
    "CommitRecord",
    "JobWorker",
    "PostgresAdvisoryLock",
    "PostgresCommitRecord",
    "WORKER_ADVISORY_LOCK_KEY",
    "WorkerCommitMismatch",
    "WorkerLock",
    "WorkerLockHeld",
    "WorkerStartRefused",
    "deployment_commit",
    "open_runtime_connection",
    "record_deployment_commit",
    "recorded_deployment_commit",
]
