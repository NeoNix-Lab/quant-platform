#!/usr/bin/env python3
"""Valida le fixture di coverage-manifest-v1 contro il suo JSON Schema.

Stessa forma dei test di trade-v1 e dei due manifest, deliberatamente
autonomo: i contratti devono poter evolvere e fallire in modo indipendente.

  * ogni fixture valid-*.json DEVE passare;
  * ogni fixture sotto invalid/ DEVE essere respinta PER LA RAGIONE PREVISTA,
    non semplicemente "respinta da qualcosa".

Perche' un errore qualsiasi non basta: una fixture pensata per dimostrare una
regola puo' rompersi per un'altra ragione (un typo altrove nel documento, o
una regola piu' generale che si attiva per prima) e continuare a "passare"
respinta, senza aver mai davvero esercitato la regola che dichiara di
provare. EXPECTED_REJECTIONS sotto e' il registro esplicito, mantenuto a
mano, di quale rifiuto ogni fixture DEVE produrre: la coppia
(validator, instance_path). Sono entrambe proprieta' STRUTTURALI dello
schema JSON — non stringhe di messaggio dipendenti dalla versione della
libreria jsonschema, che possono cambiare formulazione fra release senza che
la regola sottostante sia cambiata.

Le invarianti che JSON Schema NON puo' esprimere — intervallo half-open non
degenere, asserzioni dentro l'intent di acquisizione, contraddizioni fra
stato ed evidenza, supersessione che restringe, event bounds fuori dalla
copertura dichiarata — stanno in tests/test_declared_coverage_semantics.py.

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
SCHEMA_PATH = ROOT / "schemas" / "coverage-manifest-v1.json"
FIXTURES = ROOT / "fixtures" / "coverage-manifest-v1"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

# Registro esplicito: per ogni fixture sotto invalid/, l'insieme ESATTO (con
# molteplicita') di (validator, instance_path) che deve comparire fra gli
# errori dello schema. `validator` e' None per un rifiuto da sotto-schema
# booleano `false` (es. "properties": {"campo": false}), che in jsonschema
# non porta un nome di keyword. `instance_path` e' la tupla di stringhe del
# percorso, mai il messaggio umano.
EXPECTED_REJECTIONS = {
    "acquisition-missing": [("required", ())],
    "acquisition-unknown-field": [("additionalProperties", ("acquisition",))],
    "assertion-end-nine-fractional-digits": [
        ("pattern", ("assertions", "0", "end"))],
    "assertion-id-missing": [("required", ("assertions", "0"))],
    "assertion-id-uppercase": [
        ("pattern", ("assertions", "0", "assertion_id"))],
    "assertion-partitions-missing": [("required", ("assertions", "0"))],
    "assertion-start-seven-fractional-digits": [
        ("pattern", ("assertions", "0", "start"))],
    "assertion-status-unknown": [("enum", ("assertions", "0", "status"))],
    "assertion-ts-month-13": [("format", ("assertions", "0", "start"))],
    "assertion-ts-no-timezone": [
        ("pattern", ("assertions", "0", "end")),
        ("format", ("assertions", "0", "end")),
    ],
    "assertion-unknown-field": [
        ("additionalProperties", ("assertions", "0"))],
    "assertions-empty": [("minItems", ("assertions",))],
    "assertions-missing": [("required", ())],
    "basis-unknown": [("enum", ("acquisition", "basis"))],
    "code-ref-missing": [("required", ())],
    "complete-two-partitions": [
        ("maxItems", ("assertions", "0", "partitions"))],
    "complete-without-partition": [
        ("minItems", ("assertions", "0", "partitions"))],
    "coverage-id-missing": [("required", ())],
    "coverage-id-uppercase": [("pattern", ("coverage_id",))],
    "evidence-detail-blank": [
        ("pattern", ("assertions", "0", "evidence", "0", "detail"))],
    "evidence-empty": [("minItems", ("assertions", "0", "evidence"))],
    "evidence-kind-unknown": [
        ("enum", ("assertions", "0", "evidence", "0", "kind"))],
    "evidence-unknown-field": [
        ("additionalProperties", ("assertions", "0", "evidence", "0"))],
    "features-without-feature-set": [
        ("required", ()),
        ("required", ()),
    ],
    "instrument-blank": [("pattern", ("instrument",))],
    "intent-feb-30": [("format", ("acquisition", "intent_end"))],
    "intent-no-timezone": [
        ("pattern", ("acquisition", "intent_start")),
        ("format", ("acquisition", "intent_start")),
    ],
    "intent-offset": [("pattern", ("acquisition", "intent_end"))],
    "intent-start-seven-fractional-digits": [
        ("pattern", ("acquisition", "intent_start"))],
    "kind-not-in-layer": [("enum", ("dataset_kind",))],
    "mapping-unversioned": [("pattern", ("acquisition", "mapping"))],
    "mapping-version-zero": [("pattern", ("acquisition", "mapping"))],
    "non-features-with-feature-set": [
        (None, ()),
        (None, ()),
    ],
    "partition-key-bare-date": [
        ("pattern", ("assertions", "0", "partitions", "0", "partition_key"))],
    "partition-ref-unknown-field": [
        ("additionalProperties", ("assertions", "0", "partitions", "0"))],
    "partition-revision-zero": [
        ("minimum", ("assertions", "0", "partitions", "0", "revision"))],
    "producer-blank": [("pattern", ("producer",))],
    "schema-version-wrong": [("const", ("schema_version",))],
    "source-semantics-unversioned": [
        ("pattern", ("acquisition", "source_semantics"))],
    "strategic-field-pnl": [("additionalProperties", ())],
    "supersedes-missing": [("required", ())],
    "supersedes-uppercase": [("anyOf", ("supersedes",))],
    "unknown-field": [("additionalProperties", ())],
    "venue-null": [("type", ("venue",))],
    "venue-uppercase": [("pattern", ("venue",))],
}


def load(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def why(error):
    where = ".".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{error.validator} @ {where}"


def rejection_signature(errors):
    return sorted(
        (e.validator, tuple(str(p) for p in e.absolute_path)) for e in errors
    )


def main():
    if not SCHEMA_PATH.exists():
        sys.exit(f"schema non trovato: {SCHEMA_PATH}")

    schema = load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    # Istanza esplicita di FormatChecker: senza, "format": "date-time" sarebbe
    # una semplice annotazione e un 2024-02-30 passerebbe il regex del pattern
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

    print(f"\n{len(invalid_files)} fixture che DEVONO essere respinte "
          f"PER LA RAGIONE PREVISTA:")
    registry_names = set(EXPECTED_REJECTIONS)
    fixture_names = {path.stem for path in invalid_files}
    for stem in sorted(registry_names - fixture_names):
        print(f"  {RED}FAIL{OFF} registro orfano: {stem!r} non ha piu' una "
              f"fixture corrispondente")
        failures.append(f"registry:{stem}")

    for path in invalid_files:
        expected = EXPECTED_REJECTIONS.get(path.stem)
        if expected is None:
            print(f"  {RED}FAIL{OFF} {path.name} — nessuna voce in "
                  f"EXPECTED_REJECTIONS: la fixture esiste ma non dichiara "
                  f"quale regola deve romperla")
            failures.append(f"invalid/{path.name} (unregistered)")
            continue

        errors = list(validator.iter_errors(load(path)))
        actual = rejection_signature(errors)
        expected_sorted = sorted(expected)

        if not errors:
            print(f"  {RED}FAIL{OFF} {path.name} — ACCETTATA, doveva essere "
                  f"respinta")
            failures.append(f"invalid/{path.name} (accepted)")
        elif actual != expected_sorted:
            print(f"  {RED}FAIL{OFF} {path.name} — respinta, ma NON per la "
                  f"ragione attesa")
            print(f"       atteso:  {expected_sorted}")
            print(f"       ottenuto: {actual}")
            failures.append(f"invalid/{path.name} (wrong reason)")
        else:
            reasons = ", ".join(sorted({why(e) for e in errors}))
            print(f"  {GREEN}PASS{OFF} {path.name}")
            print(f"       {DIM}respinta da {reasons}{OFF}")

    total = len(valid_files) + len(invalid_files)
    print()
    if failures:
        print(f"{RED}FAIL{OFF}: {len(failures)}/{total} fixture non conformi")
        for name in failures:
            print(f"  - {name}")
        return 1

    print(f"{GREEN}PASS{OFF}: {total}/{total} fixture conformi "
          f"({len(valid_files)} accettate, {len(invalid_files)} respinte "
          f"per la ragione prevista)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
