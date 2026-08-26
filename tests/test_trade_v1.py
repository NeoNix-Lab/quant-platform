#!/usr/bin/env python3
"""Valida le fixture di trade-v1 contro il suo JSON Schema.

Due asserzioni simmetriche:
  * ogni fixture valid-*.json DEVE passare;
  * ogni fixture sotto invalid/ DEVE essere respinta.

Per ogni fixture negativa viene stampato il motivo del rifiuto (keyword dello
schema + percorso del campo). Serve a smascherare la fixture che viene respinta
per la ragione sbagliata: un file pensato per testare 'price = 0' ma rifiutato
per un refuso nel timestamp darebbe un PASS senza aver provato nulla.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit(
        "manca la dipendenza 'jsonschema'. Installala con:\n"
        "  python3 -m venv ~/.venvs/market-platform\n"
        "  ~/.venvs/market-platform/bin/pip install -r tests/requirements.txt"
    )

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "trade-v1.json"
FIXTURES = ROOT / "fixtures" / "trade-v1"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def load(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def why(error):
    """Keyword dello schema e percorso del campo che ha causato il rifiuto."""
    where = ".".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{error.validator} @ {where}"


def main():
    if not SCHEMA_PATH.exists():
        sys.exit(f"schema non trovato: {SCHEMA_PATH}")

    schema = load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    valid_files = sorted(FIXTURES.glob("valid-*.json"))
    invalid_files = sorted((FIXTURES / "invalid").glob("*.json"))

    if not valid_files:
        sys.exit(f"nessuna fixture valid-*.json in {FIXTURES}")
    if not invalid_files:
        sys.exit(f"nessuna fixture in {FIXTURES / 'invalid'}")

    failures = []

    print(f"\nschema: {SCHEMA_PATH.relative_to(ROOT)}")
    print(f"\n{len(valid_files)} fixture che DEVONO passare:")
    for path in valid_files:
        errors = sorted(validator.iter_errors(load(path)), key=str)
        if errors:
            print(f"  {RED}FAIL{OFF} {path.name}")
            for err in errors:
                print(f"       respinta da {why(err)}: {err.message}")
            failures.append(path.name)
        else:
            print(f"  {GREEN}PASS{OFF} {path.name}")

    print(f"\n{len(invalid_files)} fixture che DEVONO essere respinte:")
    for path in invalid_files:
        errors = sorted(validator.iter_errors(load(path)), key=str)
        if errors:
            reasons = ", ".join(sorted({why(e) for e in errors}))
            print(f"  {GREEN}PASS{OFF} {path.name}")
            print(f"       {DIM}respinta da {reasons}{OFF}")
        else:
            print(f"  {RED}FAIL{OFF} {path.name} — ACCETTATA, doveva essere respinta")
            failures.append(f"invalid/{path.name}")

    total = len(valid_files) + len(invalid_files)
    print()
    if failures:
        print(f"{RED}FAIL{OFF}: {len(failures)}/{total} fixture non conformi")
        for name in failures:
            print(f"  - {name}")
        return 1

    print(f"{GREEN}PASS{OFF}: {total}/{total} fixture conformi "
          f"({len(valid_files)} accettate, {len(invalid_files)} respinte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
