#!/usr/bin/env python3
"""Tests for the catalog schema-registry bootstrap.

The registry double stores what PostgreSQL would store: ``body`` is kept as a
parsed document, the way a ``jsonb`` column round-trips it, so body comparison
is document equality rather than byte equality.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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
TRADE_V1_LF_SHA256 = "53b5d37e7613d1130157aa1390b20e44a5cb236423dc1267df01e62dd280b55b"


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
        self.assertIn(b"\n", raw)
        self.assertNotIn(b"\r", raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), TRADE_V1_LF_SHA256)
        self.assertEqual(registration.schema_id, "trade-v1")
        self.assertEqual(registration.name, "trade")
        self.assertEqual(registration.version, 1)
        self.assertEqual(registration.json_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(registration.body, json.loads(raw.decode("utf-8")))

    def test_git_checkout_preserves_lf_bytes_across_eol_settings(self):
        # Exercise the real repository policy in a disposable Git index. No
        # developer/global config, attributes, templates or Git env may leak in.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_ATTR_NOSYSTEM="1")
        raw = TRADE_V1.read_bytes()
        for autocrlf in ("false", "true", "input"):
            for eol in ("lf", "crlf"):
                with self.subTest(autocrlf=autocrlf, eol=eol), \
                        tempfile.TemporaryDirectory() as holder:
                    checkout = Path(holder)
                    schema = checkout / "schemas" / "trade-v1.json"
                    schema.parent.mkdir()
                    schema.write_bytes(raw)
                    shutil.copyfile(ROOT / ".gitattributes", checkout / ".gitattributes")

                    def git(*args):
                        return subprocess.run(
                            ["git", "-c", f"core.attributesFile={os.devnull}",
                             "-c", f"core.autocrlf={autocrlf}",
                             "-c", f"core.eol={eol}", *args],
                            cwd=checkout, env=env, check=True, capture_output=True,
                        ).stdout

                    git("init", "--template=")
                    git("add", "--", ".gitattributes", "schemas/trade-v1.json")
                    blob = git("show", ":schemas/trade-v1.json")
                    self.assertEqual(blob, raw)
                    schema.unlink()
                    git("checkout-index", "--", "schemas/trade-v1.json")
                    self.assertEqual(schema.read_bytes(), blob)
                    self.assertNotIn(b"\r", schema.read_bytes())
                    self.assertEqual(
                        read_schema_registration(schema).json_sha256, TRADE_V1_LF_SHA256,
                    )

    def test_crlf_changes_raw_fingerprint_and_refuses_despite_equal_json(self):
        original = read_schema_registration(TRADE_V1)
        with tempfile.TemporaryDirectory() as holder:
            schema = Path(holder) / "trade-v1.json"
            raw = TRADE_V1.read_bytes().replace(b"\n", b"\r\n")
            schema.write_bytes(raw)
            variant = read_schema_registration(schema)
        self.assertEqual(variant.body, original.body)
        self.assertEqual(variant.json_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            variant.json_sha256,
            "0cfc249dcbe6fb9600f01ff438c8c9252c7fc5b4af4213a6d4b3296401ed4f08",
        )
        self.assertNotEqual(variant.json_sha256, original.json_sha256)
        connection = FakeConnection()
        self.assertEqual(bootstrap_schema_registry(connection, original), CREATED)
        before = dict(connection.store)
        connection.journal.clear()
        with self.assertRaisesRegex(SchemaRegistryError, "json_sha256"):
            bootstrap_schema_registry(connection, variant)
        self.assertEqual(connection.store, before)
        self.assertEqual(len(connection.journal), 1)
        self.assertTrue(connection.journal[0].startswith("SELECT "))

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
