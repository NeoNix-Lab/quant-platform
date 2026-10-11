"""Private SQL dialect/transaction boundary for the three DG-K runtime stores.

PostgreSQL schemas are bootstrapped by db/init, never by service logins.
Connections are caller-owned and autocommit: each mutation below owns a short
transaction. Transaction-level advisory locks serialize read/check/write of
one store, including absent-key admission; they are NOT the worker lifetime
lock or a dispatcher. SQLite remains the hermetic implementation.
"""

from __future__ import annotations

from contextlib import contextmanager
import re
import sqlite3
from typing import Any

from psycopg import Connection


_TABLES = re.compile(
    r"\b(durable_jobs|durable_job_attempts|admitted_inputs|"
    r"governed_result_bundles|governed_result_outputs)\b"
)


class RuntimeStoreConnection:
    """Adapt only the fixed SQL statements owned by J03/K12/K13."""

    def __init__(self, connection: sqlite3.Connection | Connection, lock_key: int):
        if not isinstance(connection, (sqlite3.Connection, Connection)):
            raise TypeError("connection must be a sqlite3.Connection or psycopg.Connection")
        self.postgres = isinstance(connection, Connection)
        if self.postgres and not connection.autocommit:
            raise ValueError("PostgreSQL runtime connection must use autocommit=True")
        self.connection = connection
        self.lock_key = lock_key

    @contextmanager
    def transaction(self):
        if not self.postgres:
            with self.connection:
                yield
            return
        with self.connection.transaction():
            self.connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_key,))
            yield

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()):
        return self.connection.execute(self._sql(statement), parameters)

    def executemany(self, statement: str, parameters):
        if not self.postgres:
            return self.connection.executemany(statement, parameters)
        with self.connection.cursor() as cursor:
            cursor.executemany(self._sql(statement), parameters)

    def _sql(self, statement: str) -> str:
        if not self.postgres:
            return statement
        # Only internal, parameterized store SQL enters here. Identity payloads
        # remain bound parameters, never interpolated or normalized by the DB.
        return _TABLES.sub(r"runtime.\1", statement).replace("?", "%s")
