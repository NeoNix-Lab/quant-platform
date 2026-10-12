"""The single J03 worker's lifecycle and the API's deployment-commit record (P04b).

ADR-0068 sections 6 and 8 over ADR-0062 (unchanged). Exactly one worker runs.
Its lifecycle is a fixed order, so that nothing touches a Job before the
worker is entitled to:

1. take a PostgreSQL *session-level* advisory lock on a fixed key, or refuse;
2. compare its own Git commit with the one the API recorded, or refuse;
3. call ``DurableJobStore.recover_after_restart`` exactly once;
4. loop on :meth:`JobDispatcher.dispatch_next`.

Why step 3 is correct, and what it depends on. The store's *global* recovery is
safe only if every ``RUNNING`` Job visible after the lock is held belongs to a
dead *process*. A PostgreSQL session lock alone does not give that: it is
released when the *session* ends, and a session can end (network loss, a
terminated backend, a server restart) while the process is alive and its
handler still producing effects. The worker therefore also holds an
operating-system lock on a file, which the kernel releases exactly when the
process dies, so a live process always excludes a successor even after its
database session is gone. The two are one composite lock; either being
unavailable refuses the start.

A worker also verifies that it still holds the composite lock before every
dispatch and stops, fatally, when it does not. An in-flight handler cannot be
preempted, so it runs to its end; its outcome cannot be recorded (the session
is gone), the Job stays ``RUNNING``, the process exits, and only then does the
operating-system lock let the next worker recover it. ADR-0068 section 6
states the session-only argument; the process-lifetime fence is an addition to
it that the ADR should record (a design amendment, not made here). It is a
single-host guarantee: the platform's server units share one machine
(ADR-0068 section 3); two machines would need a lease the handlers can be
fenced by, which is not designed.

No per-process attempt ownership is needed and ADR-0062 section 4's attempt
fields are unchanged.

A stop request lets the in-flight handler finish and records its outcome; a
handler is never interrupted mid-effect. If the process is killed instead, the
Job stays ``RUNNING`` and the next worker's step 3 marks it
``RECOVERY_REQUIRED``; it is never redispatched automatically.

This module owns the lifecycle only. argv, environment and signal handling
belong to the ``tools/job_worker.py`` host; handlers belong to their own issues.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Any, Protocol

from psycopg import Connection, connect
from psycopg.conninfo import conninfo_to_dict

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


class WorkerLockLost(RuntimeError):
    """A started worker no longer holds its lock: it must not dispatch again."""


class WorkerLock(Protocol):
    def acquire(self) -> bool: ...

    def held(self) -> bool: ...

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

    def held(self) -> bool:
        """Whether *this session* still holds the lock, asked of the server."""
        # A bigint advisory key is reported as classid (high 32 bits), objid
        # (low 32 bits) and objsubid 1.
        row = self.connection.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND pid = pg_backend_pid() AND classid = %s AND objid = %s AND objsubid = 1",
            (self.key >> 32, self.key & 0xFFFFFFFF),
        ).fetchone()
        return bool(row[0])

    def release(self) -> None:
        self.connection.execute("SELECT pg_advisory_unlock(%s)", (self.key,))


class ProcessLifetimeLock:
    """An operating-system lock that lives exactly as long as this process.

    Taken on a file with ``fcntl.flock`` (POSIX) or ``msvcrt.locking``
    (Windows). The kernel drops it when the process ends, however it ends, and
    not before: unlike a database session it cannot disappear under a live
    process, so it is what makes "the previous worker is dead" true.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        if self._descriptor is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def held(self) -> bool:
        if self._descriptor is None:
            return False
        if os.name == "nt":
            return True  # An open, locked file cannot be unlinked on Windows.
        try:
            # The path must still name the locked file: a cleaner that unlinked
            # it would let a successor lock a new file at the same path.
            return os.fstat(self._descriptor).st_ino == os.stat(self.path).st_ino
        except OSError:
            return False

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)  # POSIX: closing drops the flock


class CompositeWorkerLock:
    """Several locks that are one: acquired in order, released in reverse."""

    def __init__(self, locks: Sequence[WorkerLock]):
        self.locks = tuple(locks)
        if not self.locks:
            raise ValueError("a composite lock needs at least one lock")
        self._acquired: list[WorkerLock] = []

    def acquire(self) -> bool:
        for lock in self.locks:
            if not lock.acquire():
                self.release()
                return False
            self._acquired.append(lock)
        return True

    def held(self) -> bool:
        return bool(self._acquired) and all(lock.held() for lock in self._acquired)

    def release(self) -> None:
        while self._acquired:
            try:
                self._acquired.pop().release()
            except Exception:  # A dead session's lock went with it.
                pass


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
            try:
                still_held = self.lock.held()
            except Exception as error:  # A dead session cannot confirm anything.
                raise WorkerLockLost("the worker cannot confirm that it holds its lock") from error
            if not still_held:
                # A lost session must not become a second active worker: no
                # further handler starts. (An in-flight one cannot be recalled.)
                raise WorkerLockLost("the worker no longer holds its lock")
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


def default_process_lock_path(dsn: str) -> Path:
    """A per-database lock-file path, so one database has one worker per host.

    Keyed by the DSN's server identity (never its credentials). Deployments
    should set ``--lock-file`` to a path under a runtime directory that no
    temporary-file cleaner touches; the held-check detects an unlinked file.
    """

    parts = conninfo_to_dict(dsn)
    identity = "|".join(
        str(parts.get(key, "")) for key in ("host", "hostaddr", "port", "dbname")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"quant-platform-j03-worker-{digest}.lock"


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
    "CompositeWorkerLock",
    "JobWorker",
    "PostgresAdvisoryLock",
    "PostgresCommitRecord",
    "ProcessLifetimeLock",
    "WORKER_ADVISORY_LOCK_KEY",
    "WorkerCommitMismatch",
    "WorkerLock",
    "WorkerLockHeld",
    "WorkerLockLost",
    "WorkerStartRefused",
    "default_process_lock_path",
    "deployment_commit",
    "open_runtime_connection",
    "record_deployment_commit",
    "recorded_deployment_commit",
]
