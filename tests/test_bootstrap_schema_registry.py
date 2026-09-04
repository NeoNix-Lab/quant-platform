#!/usr/bin/env python3
"""Tests for the catalog schema-registry bootstrap.

The registry double stores what PostgreSQL would store: ``body`` is kept as a
parsed document, the way a ``jsonb`` column round-trips it, so body comparison
is document equality rather than byte equality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from bootstrap_schema_registry import (  # noqa: E402
    CREATED,
    PRESENT,
    SchemaRegistryError,
    bootstrap_schema_registry,
    read_schema_registration,
)

TRADE_V1 = ROOT / "schemas" / "trade-v1.json"


class FakeCursor:
    def __init__(self, store: dict, journal: list):
        self.store = store
        self.journal = journal
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, parameters=()):
        self.journal.append(" ".join(sql.split()))
        if "SELECT" in sql:
            row = self.store.get(parameters[0])
            self._result = None if row is None else tuple(row)
            return
        if "INSERT" in sql:
            schema_id, name, version, digest, body = parameters
            if schema_id in self.store:
                raise AssertionError("primary key violation: row already exists")
            # jsonb: stored as a parsed document, key order not preserved.
            self.store[schema_id] = (name, version, digest, json.loads(body))
            self._result = None
            return
        raise AssertionError(f"unexpected statement: {sql}")

    def fetchone(self):
        return self._result


class FakeConnection:
    def __init__(self, store=None):
        self.store = {} if store is None else store
        self.journal: list[str] = []

    def cursor(self):
        return FakeCursor(self.store, self.journal)


class SchemaRegistryBootstrapTests(unittest.TestCase):

    # -- 13.6 the repository schema file is the only authority ---------------

    def test_registration_is_derived_from_the_authoritative_file(self):
        registration = read_schema_registration(TRADE_V1)
        raw = TRADE_V1.read_bytes()
        self.assertEqual(registration.schema_id, "trade-v1")
        self.assertEqual(registration.name, "trade")
        self.assertEqual(registration.version, 1)
        self.assertEqual(registration.json_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(registration.body, json.loads(raw.decode("utf-8")))

    def test_changing_the_schema_bytes_changes_the_registration(self):
        with tempfile.TemporaryDirectory() as holder:
            altered = Path(holder) / "trade-v1.json"
            document = json.loads(TRADE_V1.read_text(encoding="utf-8"))
            document["description"] = document["description"] + " (test variant)"
            altered.write_text(json.dumps(document), encoding="utf-8")

            original = read_schema_registration(TRADE_V1)
            variant = read_schema_registration(altered)
            self.assertNotEqual(variant.json_sha256, original.json_sha256)
            self.assertNotEqual(variant.body, original.body)
            self.assertEqual(variant.schema_id, original.schema_id)
            self.assertEqual(
                variant.json_sha256,
                hashlib.sha256(altered.read_bytes()).hexdigest(),
            )

    def test_identity_comes_from_the_versioned_schema_id(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            other = root / "candle-v2.json"
            other.write_text(json.dumps({"title": "candle-v2"}), encoding="utf-8")
            registration = read_schema_registration(other)
            self.assertEqual(
                (registration.schema_id, registration.name, registration.version),
                ("candle-v2", "candle", 2),
            )

            unversioned = root / "trade.json"
            unversioned.write_text("{}", encoding="utf-8")
            with self.assertRaises(SchemaRegistryError):
                read_schema_registration(unversioned)

            mislabelled = root / "trade-v1.json"
            mislabelled.write_text(json.dumps({"title": "trade-v2"}), encoding="utf-8")
            with self.assertRaises(SchemaRegistryError) as caught:
                read_schema_registration(mislabelled)
            self.assertIn("does not match its schema id", str(caught.exception))

    # -- 13.1 fresh registry -------------------------------------------------

    def test_absent_schema_is_created_exactly_once(self):
        registration = read_schema_registration(TRADE_V1)
        connection = FakeConnection()
        self.assertEqual(bootstrap_schema_registry(connection, registration), CREATED)
        self.assertEqual(list(connection.store), ["trade-v1"])
        name, version, digest, body = connection.store["trade-v1"]
        self.assertEqual((name, version, digest), ("trade", 1, registration.json_sha256))
        self.assertEqual(body, registration.body)
        self.assertEqual(sum("INSERT" in item for item in connection.journal), 1)
        # The insert is verified by reading back what was persisted.
        self.assertEqual(sum("SELECT" in item for item in connection.journal), 2)

    # -- 13.2 idempotent retry ----------------------------------------------

    def test_second_bootstrap_is_a_no_op(self):
        registration = read_schema_registration(TRADE_V1)
        connection = FakeConnection()
        bootstrap_schema_registry(connection, registration)
        before = dict(connection.store)

        second = FakeConnection(connection.store)
        self.assertEqual(bootstrap_schema_registry(second, registration), PRESENT)
        self.assertEqual(connection.store, before)
        self.assertEqual([item for item in second.journal if "INSERT" in item], [])

    # -- 13.3 / 13.4 / 13.5 conflicts refuse without overwriting -------------

    def _refuses(self, row, message_fragment):
        registration = read_schema_registration(TRADE_V1)
        connection = FakeConnection({"trade-v1": row})
        with self.assertRaises(SchemaRegistryError) as caught:
            bootstrap_schema_registry(connection, registration)
        self.assertIn(message_fragment, str(caught.exception))
        # The conflicting row is left exactly as it was found.
        self.assertEqual(connection.store["trade-v1"], row)
        self.assertEqual([item for item in connection.journal if "INSERT" in item], [])
        return caught.exception

    def test_conflicting_body_refuses(self):
        registration = read_schema_registration(TRADE_V1)
        self._refuses(("trade", 1, registration.json_sha256, {"title": "trade-v1"}), "body differs")

    def test_conflicting_hash_refuses(self):
        self._refuses(("trade", 1, "0" * 64, read_schema_registration(TRADE_V1).body), "json_sha256")

    def test_conflicting_name_or_version_refuses(self):
        registration = read_schema_registration(TRADE_V1)
        self._refuses(("trades", 1, registration.json_sha256, registration.body), "name")
        self._refuses(("trade", 2, registration.json_sha256, registration.body), "version")

    def test_refusal_names_the_versioning_rule(self):
        registration = read_schema_registration(TRADE_V1)
        exc = self._refuses(("trade", 1, "b" * 64, registration.body), "json_sha256")
        self.assertIn("requires a new version", str(exc))

    # -- fail-closed shape ---------------------------------------------------

    def test_bootstrap_never_overwrites_or_deletes(self):
        source = (ROOT / "tools" / "bootstrap_schema_registry.py").read_text(encoding="utf-8")
        statements = source.upper()
        for forbidden in ("ON CONFLICT", "DO UPDATE", "UPDATE CATALOG", "DELETE FROM", "TRUNCATE"):
            self.assertNotIn(forbidden, statements, f"bootstrap must not {forbidden}")
        # The schema body is never transcribed into the tool.
        self.assertNotIn("aggressor_side", source)
        self.assertNotIn("exchange_ts", source)

    def test_bootstrap_is_not_vertical_specific(self):
        source = (ROOT / "tools" / "bootstrap_schema_registry.py").read_text(encoding="utf-8")
        for forbidden in ("bybit", "BTCUSDT", "sqlite", "legacy", "conformity_e2e"):
            self.assertNotIn(forbidden, source)

    def test_body_returned_as_text_is_compared_as_a_document(self):
        registration = read_schema_registration(TRADE_V1)
        # A driver that hands jsonb back as text must not look like a conflict.
        row = ("trade", 1, registration.json_sha256, json.dumps(registration.body))
        connection = FakeConnection({"trade-v1": row})
        self.assertEqual(bootstrap_schema_registry(connection, registration), PRESENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
