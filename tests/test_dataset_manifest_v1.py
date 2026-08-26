#!/usr/bin/env python3
"""Valida le fixture di dataset-manifest-v1 contro il suo JSON Schema.

Stessa forma del test di trade-v1, deliberatamente autonomo: i due contratti
devono poter evolvere e fallire in modo indipendente.

  * ogni fixture valid-*.json DEVE passare;
  * ogni fixture sotto invalid/ DEVE essere respinta, e viene stampato il
    motivo del rifiuto, cosi' che una fixture respinta per la ragione
    sbagliata non passi per verificata.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    sys.exit(
        "manca la dipendenza 'jsonschema'. Vedi tests/requirements.txt"
    )

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "dataset-manifest-v1.json"
FIXTURES = ROOT / "fixtures" / "dataset-manifest-v1"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def load(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def why(error):
    where = ".".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{error.validator} @ {where}"


def main():
    if not SCHEMA_PATH.exists():
        sys.exit(f"schema non trovato: {SCHEMA_PATH}")

    schema = load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    # Istanza esplicita di FormatChecker: senza, "format": "date-time" sarebbe
    # una semplice annotazione e un 2026-13-45 passerebbe il regex del pattern
    # senza che nessuno verifichi che sia una data esistente.
    format_checker = FormatChecker()
    if "date-time" not in format_checker.checkers:
        sys.exit("FATAL: il format checker non gestisce date-time "
                 "(manca rfc3339-validator): i timestamp impossibili non "
                 "verrebbero rilevati e il test darebbe un falso PASS")
    validator = Draft202012Validator(schema, format_checker=format_checker)

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
