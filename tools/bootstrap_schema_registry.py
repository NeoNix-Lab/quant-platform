#!/usr/bin/env python3
"""Register a frozen repository record schema in ``catalog.schema_registry``.

The repository schema file stays the authority: this tool only establishes its
runtime registry representation so that ``catalog.datasets.schema_id`` — a
foreign key into the registry — can reference it.  PostgreSQL never becomes the
semantic owner of the contract.

The registry row is derived from the schema file, never transcribed:

    json_sha256 = sha256(raw file bytes)
    body        = the parsed JSON document

That hash rule is the one the platform already uses for durable documents (the
S14 manifest loader hashes the raw bytes it validated), and it is what the
existing PostgreSQL certification integration test registers today.

The tool is a bootstrap, not a reconciliation tool:

    absent      -> insert, then verify what was persisted
    identical   -> accept, change nothing
    conflicting -> refuse

An incompatible schema change is a new versioned schema, never an in-place
rewrite of a registered one, so nothing here updates, upserts or deletes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "schemas" / "trade-v1.json"
_SCHEMA_ID = re.compile(r"^(?P<name>[a-z0-9]+(?:[._-][a-z0-9]+)*?)-v(?P<version>[1-9][0-9]*)$")

CREATED = "created"
PRESENT = "present"


class SchemaRegistryError(RuntimeError):
    """A bootstrap refusal: the registry disagrees with the repository."""


@dataclass(frozen=True, slots=True)
class SchemaRegistration:
    """The registry representation derived from one authoritative schema file."""

    schema_id: str
    name: str
    version: int
    json_sha256: str
    body: dict[str, Any]


def read_schema_registration(path: str | Path = DEFAULT_SCHEMA) -> SchemaRegistration:
    """Derive the registry row from the repository schema file."""

    schema_path = Path(path)
    try:
        raw = schema_path.read_bytes()
    except OSError as exc:
        raise SchemaRegistryError(f"schema file is unreadable: {schema_path}") from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaRegistryError(f"schema file is not valid JSON: {schema_path}") from exc
    if not isinstance(body, dict):
        raise SchemaRegistryError(f"schema file must contain a JSON object: {schema_path}")

    schema_id = schema_path.stem
    match = _SCHEMA_ID.fullmatch(schema_id)
    if match is None:
        raise SchemaRegistryError(
            f"schema file name is not a versioned schema id: {schema_path.name}"
        )
    # The document names itself; a file whose title disagrees with its own file
    # name would register an identity nobody can reproduce from the contract.
    title = body.get("title")
    if title is not None and title != schema_id:
        raise SchemaRegistryError(
            f"schema title {title!r} does not match its schema id {schema_id!r}"
        )
    return SchemaRegistration(
        schema_id=schema_id,
        name=match.group("name"),
        version=int(match.group("version")),
        json_sha256=hashlib.sha256(raw).hexdigest(),
        body=body,
    )


def _existing_row(cursor: Any, schema_id: str):
    cursor.execute(
        """
        SELECT name, version, json_sha256, body
          FROM catalog.schema_registry
         WHERE schema_id = %s
        """,
        (schema_id,),
    )
    return cursor.fetchone()


def _conflicts(row: Any, registration: SchemaRegistration) -> tuple[str, ...]:
    """Fields where the registered row disagrees with the repository schema."""

    name, version, json_sha256, body = row[0], row[1], row[2], row[3]
    if isinstance(body, (str, bytes, bytearray)):
        # Some drivers return jsonb as text; compare documents, not encodings.
        try:
            body = json.loads(body)
        except (UnicodeError, json.JSONDecodeError):
            body = None
    differences = []
    if name != registration.name:
        differences.append(f"name: {name!r} != {registration.name!r}")
    if int(version) != registration.version:
        differences.append(f"version: {version!r} != {registration.version!r}")
    if str(json_sha256) != registration.json_sha256:
        differences.append(f"json_sha256: {json_sha256!r} != {registration.json_sha256!r}")
    if body != registration.body:
        differences.append("body differs from the repository schema document")
    return tuple(differences)


def bootstrap_schema_registry(connection: Any, registration: SchemaRegistration) -> str:
    """Establish one schema in the registry.  Transaction stays with the caller.

    Returns ``created`` or ``present``; raises on any disagreement.
    """

    with connection.cursor() as cursor:
        row = _existing_row(cursor, registration.schema_id)
        if row is not None:
            differences = _conflicts(row, registration)
            if differences:
                raise SchemaRegistryError(
                    f"registered {registration.schema_id} conflicts with the repository "
                    "schema; an incompatible schema requires a new version, not an "
                    "in-place rewrite: " + "; ".join(differences)
                )
            return PRESENT

        cursor.execute(
            """
            INSERT INTO catalog.schema_registry (schema_id, name, version, json_sha256, body)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                registration.schema_id,
                registration.name,
                registration.version,
                registration.json_sha256,
                json.dumps(registration.body),
            ),
        )
        persisted = _existing_row(cursor, registration.schema_id)
        if persisted is None:
            raise SchemaRegistryError(
                f"{registration.schema_id} was not persisted by the bootstrap insert"
            )
        differences = _conflicts(persisted, registration)
        if differences:
            raise SchemaRegistryError(
                f"persisted {registration.schema_id} does not match what was inserted: "
                + "; ".join(differences)
            )
        return CREATED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap_schema_registry",
        description=(
            "Register a frozen repository record schema in catalog.schema_registry. "
            "Absent inserts, identical accepts, conflicting refuses."
        ),
    )
    parser.add_argument(
        "--schema", default=str(DEFAULT_SCHEMA),
        help="authoritative repository schema file (default: schemas/trade-v1.json)",
    )
    parser.add_argument("--dsn", help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="derive and print the registry row without connecting to the catalog",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registration = read_schema_registration(args.schema)
    except SchemaRegistryError as exc:
        print(f"SCHEMA REGISTRY BOOTSTRAP: FAIL\nreason: {exc}", file=sys.stderr)
        return 1

    print(f"schema_id:   {registration.schema_id}")
    print(f"name:        {registration.name}")
    print(f"version:     {registration.version}")
    print(f"json_sha256: {registration.json_sha256}")
    if args.dry_run:
        print("SCHEMA REGISTRY BOOTSTRAP: DRY RUN (catalog not contacted)")
        return 0

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment-specific
        print(f"SCHEMA REGISTRY BOOTSTRAP: FAIL\nreason: psycopg is required: {exc}",
              file=sys.stderr)
        return 1
    try:
        connection = psycopg.connect(args.dsn) if args.dsn else psycopg.connect()
    except Exception as exc:
        print(f"SCHEMA REGISTRY BOOTSTRAP: FAIL\nreason: could not connect to the catalog: {exc}",
              file=sys.stderr)
        return 1
    try:
        outcome = bootstrap_schema_registry(connection, registration)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        print(f"SCHEMA REGISTRY BOOTSTRAP: FAIL\nreason: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    print(f"SCHEMA REGISTRY BOOTSTRAP: PASS ({outcome})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
