#!/usr/bin/env python3
"""Integration test: reference importer contro la giornata reale 2024-01-15.

Richiede il database SQLite storico Bybit, che non e' versionato nel repo.

    python tests/integration_bybit_trades_2024_01_15.py --sqlite /percorso/2326.sqlite

Esegue l'import DUE volte verso output distinti e verifica che row_count e
SHA-256 coincidano: senza il secondo run la riproducibilita' resterebbe
un'affermazione invece di una misura.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from import_bybit_trades import run_import  # noqa: E402

GREEN, RED, DIM, OFF = "\x1b[32m", "\x1b[31m", "\x1b[90m", "\x1b[0m"

DATE = "2024-01-15"
EXPECTED = {
    "rows": 1_105_145,
    "buy": 553_875,
    "sell": 551_270,
    "first_exchange_ts": "2024-01-15T00:00:00.492Z",
    "last_exchange_ts": "2024-01-15T23:59:59.931Z",
}

FAILURES = []


def check(condition, what, detail=None):
    if condition:
        print(f"  {GREEN}PASS{OFF} {what}")
        if detail:
            print(f"       {DIM}{detail}{OFF}")
    else:
        print(f"  {RED}FAIL{OFF} {what}")
        if detail:
            print(f"       {detail}")
        FAILURES.append(what)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--keep", metavar="DIR",
                        help="conserva gli artifact in DIR invece di un temporaneo")
    args = parser.parse_args()

    source = Path(args.sqlite)
    if not source.is_file():
        sys.exit(f"database sorgente non trovato: {source}")

    holder = None
    if args.keep:
        workdir = Path(args.keep)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        holder = tempfile.TemporaryDirectory()
        workdir = Path(holder.name)

    try:
        print(f"\nsorgente: {source}  ({source.stat().st_size / 2**30:.1f} GiB)")
        print(f"giornata: {DATE} UTC  [1705276800000, 1705363200000)")

        print("\n--- run 1 ---")
        first = run_import(source, DATE, workdir / "run1.jsonl")
        print("\n--- run 2, output distinto ---")
        second = run_import(source, DATE, workdir / "run2.jsonl")

        print("\nrow count")
        check(first.source_rows == EXPECTED["rows"],
              f"righe sorgente = {EXPECTED['rows']:,}", f"{first.source_rows:,}")
        check(first.canonical_rows == EXPECTED["rows"],
              f"righe canoniche = {EXPECTED['rows']:,}", f"{first.canonical_rows:,}")
        check(first.source_rows == first.canonical_rows,
              "nessuna riga persa o duplicata")

        print("\naggressor side")
        check(first.buy == EXPECTED["buy"], f"buy = {EXPECTED['buy']:,}",
              f"{first.buy:,}")
        check(first.sell == EXPECTED["sell"], f"sell = {EXPECTED['sell']:,}",
              f"{first.sell:,}")
        check(first.buy + first.sell == first.canonical_rows,
              "buy + sell copre tutte le righe: nessun 'unknown'")

        print("\ncampi che devono restare null")
        check(first.receive_ts_non_null == 0, "receive_ts non-null = 0",
              str(first.receive_ts_non_null))
        check(first.sequence_non_null == 0, "sequence non-null = 0",
              str(first.sequence_non_null))

        print("\nboundary temporali canonici")
        check(first.first_exchange_ts == EXPECTED["first_exchange_ts"],
              f"first = {EXPECTED['first_exchange_ts']}", first.first_exchange_ts)
        check(first.last_exchange_ts == EXPECTED["last_exchange_ts"],
              f"last  = {EXPECTED['last_exchange_ts']}", first.last_exchange_ts)

        print("\nriproducibilita'")
        check(first.canonical_rows == second.canonical_rows,
              "stesso row_count nei due run",
              f"{first.canonical_rows:,} == {second.canonical_rows:,}")
        check(first.sha256 == second.sha256, "stesso SHA-256 nei due run",
              first.sha256)
        on_disk = sha256_of(workdir / "run1.jsonl")
        check(on_disk == first.sha256,
              "il digest calcolato in streaming coincide con quello del file")

        print("\nforma dell'artifact")
        with open(workdir / "run1.jsonl", "rb") as handle:
            head = [handle.readline() for _ in range(2)]
        check(all(line.endswith(b"\n") and not line.endswith(b"\r\n")
                  for line in head),
              "newline LF, nessun CRLF introdotto dalla piattaforma")
        record = json.loads(head[0])
        check(list(record) == ["venue", "instrument", "exchange_ts", "receive_ts",
                               "price", "size", "aggressor_side", "trade_id",
                               "sequence"],
              "chiavi nell'ordine del contratto", ", ".join(record))
        check(isinstance(record["price"], str) and isinstance(record["size"], str),
              "price e size sono stringhe nel JSON",
              f"price={record['price']!r} size={record['size']!r}")
        print(f"       {DIM}prima riga: {head[0].decode().strip()}{OFF}")

        print(f"\nSHA-256  {first.sha256}")
        print(f"byte     {first.bytes_written:,}")
        if args.keep:
            print(f"artifact conservati in {workdir}")
    finally:
        if holder is not None:
            holder.cleanup()

    print()
    if FAILURES:
        print(f"{RED}FAIL{OFF}: {len(FAILURES)} controlli non superati")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print(f"{GREEN}PASS{OFF}: giornata {DATE} importata e riproducibile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
