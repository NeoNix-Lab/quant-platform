#!/usr/bin/env python3
"""Test del reference importer Bybit trades -> trade-v1.

La canonicalizzazione e' una funzione pura: la maggior parte dei casi non ha
bisogno di SQLite. Solo i test su finestra temporale, ordinamento e scrittura
atomica costruiscono un database temporaneo.

Ogni record canonico prodotto viene inoltre validato contro il contratto
congelato schemas/trade-v1.json: non basta che l'importer sia coerente con se
stesso, deve produrre record che il contratto accetta.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from import_bybit_trades import (  # noqa: E402
    CANONICAL_FIELD_ORDER, ImportError_, SourceRow, canonicalize_trade,
    encode_record, format_exchange_ts, iter_source_rows, open_source,
    parse_utc_to_nanos, run_import, utc_day_bounds_ms, write_jsonl_atomic,
)

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    sys.exit("manca la dipendenza 'jsonschema'. Vedi tests/requirements.txt")

GREEN, RED, DIM, OFF = "\x1b[32m", "\x1b[31m", "\x1b[90m", "\x1b[0m"

SCHEMA = json.loads((ROOT / "schemas" / "trade-v1.json").read_text(encoding="utf-8"))
_fc = FormatChecker()
if "date-time" not in _fc.checkers:
    sys.exit("FATAL: il format checker non gestisce date-time "
             "(manca rfc3339-validator): i timestamp impossibili non "
             "verrebbero rilevati e il test darebbe un falso PASS")
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=_fc)

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


def must_fail(callable_, what, expect_field=None):
    try:
        callable_()
    except ImportError_ as exc:
        if expect_field is not None and exc.field != expect_field:
            check(False, what, f"respinto ma sul campo {exc.field!r}, "
                               f"atteso {expect_field!r}")
            return
        check(True, what, str(exc)[:110])
    else:
        check(False, what, "ACCETTATO, doveva fallire")


def row(**overrides):
    base = dict(category="linear", symbol="BTCUSDT",
                trade_id="0a14459b-c7a6-5e90-bc0a-30b46a3fbb90",
                trade_time_ms=1705276800492,
                trade_time_utc="2024-01-15T00:00:00.492000Z",
                side="Buy", size="0.004", price="41731.10")
    base.update(overrides)
    return SourceRow(**base)


def canonical_is_valid(record):
    errors = sorted(VALIDATOR.iter_errors(record), key=str)
    return (not errors), "; ".join(e.message for e in errors[:2])


# ==========================================================================
print("\n1-2. mappatura del lato aggressore")
rec_buy = canonicalize_trade(row(side="Buy"))
check(rec_buy["aggressor_side"] == "buy", "Buy -> 'buy'")
rec_sell = canonicalize_trade(row(side="Sell"))
check(rec_sell["aggressor_side"] == "sell", "Sell -> 'sell'")

print("\n3. side sconosciuta -> failure")
for bad in ["buy", "BUY", "Bid", "Ask", "", None, "Buy "]:
    must_fail(lambda b=bad: canonicalize_trade(row(side=b)),
              f"side={bad!r} respinto", expect_field="side")

print("\n4-5. price e size preservati byte per byte")
for field, value in [("price", "41731.10"), ("price", "41731.1"),
                     ("size", "0.004"), ("size", "0.00400"),
                     ("price", "1"), ("size", "123456.789012345678901234567890")]:
    rec = canonicalize_trade(row(**{field: value}))
    check(rec[field] == value and isinstance(rec[field], str),
          f"{field}={value!r} preservato identico", repr(rec[field]))

print("\n6. nessun passaggio tramite float")
# La rappresentazione decimale esatta del float 0.1: se un solo passaggio per
# IEEE-754 avvenisse, questa stringa tornerebbe '0.1'.
exact = "0.1000000000000000055511151231257827"
rec = canonicalize_trade(row(size=exact))
check(rec["size"] == exact, "decimale oltre la precisione di float64 intatto",
      rec["size"])
# 2^53+1 con parte frazionaria: float lo collasserebbe
big = "9007199254740993.1"
rec = canonicalize_trade(row(price=big))
check(rec["price"] == big, "valore oltre 2^53 intatto", rec["price"])
line = json.loads(encode_record(rec).decode())
check(isinstance(line["price"], str),
      "nel JSON price resta una stringa, non un numero", repr(line["price"]))
check("e" not in rec["price"].lower() and "E" not in rec["price"],
      "nessuna notazione esponenziale introdotta")

print("\n7-9. receive_ts, sequence, trade_id")
rec = canonicalize_trade(row())
check(rec["receive_ts"] is None, "receive_ts = null")
check(rec["sequence"] is None, "sequence = null")
check(rec["trade_id"] == "0a14459b-c7a6-5e90-bc0a-30b46a3fbb90",
      "trade_id preservato", rec["trade_id"])
must_fail(lambda: canonicalize_trade(row(trade_id="")),
          "trade_id vuoto respinto", expect_field="trade_id")

print("\n10-11. identita' della sorgente")
must_fail(lambda: canonicalize_trade(row(category="spot")),
          "category errata respinta", expect_field="category")
must_fail(lambda: canonicalize_trade(row(category="inverse")),
          "category 'inverse' respinta", expect_field="category")
must_fail(lambda: canonicalize_trade(row(symbol="ETHUSDT")),
          "symbol errato respinto", expect_field="symbol")

print("\n12-13. coerenza fra trade_time_ms e trade_time_utc")
rec = canonicalize_trade(row(trade_time_ms=1705276800492,
                             trade_time_utc="2024-01-15T00:00:00.492000Z"))
check(rec["exchange_ts"] == "2024-01-15T00:00:00.492Z",
      "ms e UTC coerenti -> pass", rec["exchange_ts"])
rec = canonicalize_trade(row(trade_time_ms=1705276800492,
                             trade_time_utc="2024-01-15T00:00:00.492Z"))
check(rec["exchange_ts"] == "2024-01-15T00:00:00.492Z",
      "stesso istante scritto con 3 decimali invece di 6 -> pass")
for bad_utc in ["2024-01-15T00:00:00.493000Z",   # 1 ms di scarto
                "2024-01-15T00:00:00.492001Z",   # 1 us: sub-millisecondo
                "2024-01-15T01:00:00.492000Z",   # un'ora
                "2024-01-15T00:00:00.492000",    # senza timezone
                "2024-13-45T00:00:00.492000Z"]:  # data inesistente
    must_fail(lambda u=bad_utc: canonicalize_trade(row(trade_time_utc=u)),
              f"UTC divergente {bad_utc!r} respinto", expect_field="trade_time_utc")
must_fail(lambda: canonicalize_trade(row(trade_time_ms=None)),
          "trade_time_ms mancante respinto", expect_field="trade_time_ms")
must_fail(lambda: canonicalize_trade(row(trade_time_utc="")),
          "trade_time_utc mancante respinto", expect_field="trade_time_utc")

print("\n17. determinismo: stessa riga -> stesso record")
a = canonicalize_trade(row())
b = canonicalize_trade(row())
check(a == b, "due canonicalizzazioni della stessa riga coincidono")
check(encode_record(a) == encode_record(b), "e producono gli stessi byte")

print("\n18. nessun campo sorgente extra nell'output")
rec = canonicalize_trade(row())
check(tuple(rec) == CANONICAL_FIELD_ORDER,
      "chiavi esattamente quelle di trade-v1, nell'ordine del contratto",
      ", ".join(rec))
forbidden = {"category", "trade_time_ms", "trade_time_utc", "side",
             "tick_direction", "gross_value", "home_notional", "foreign_notional",
             "symbol"}
check(not (set(rec) & forbidden), "nessun campo sorgente e' filtrato nel record",
      f"assenti: {', '.join(sorted(forbidden))}")

print("\nconformita' al contratto congelato trade-v1")
for name, sample in [("buy", rec_buy), ("sell", rec_sell), ("base", rec)]:
    ok, why = canonical_is_valid(sample)
    check(ok, f"record {name} valido secondo schemas/trade-v1.json", why)

print("\nvalidazione decimale contro il contratto")
for bad in ["0", "0.0", "-1", "-0.5", "01.5", "1.", ".5", "1e5", "abc", "", " 1"]:
    must_fail(lambda b=bad: canonicalize_trade(row(price=b)),
              f"price={bad!r} respinto", expect_field="price")
for bad in ["0", "0.000", "-0.004"]:
    must_fail(lambda b=bad: canonicalize_trade(row(size=b)),
              f"size={bad!r} respinto", expect_field="size")

print("\nformattazione di exchange_ts")
for ms, expected in [(1705276800492, "2024-01-15T00:00:00.492Z"),
                     (1705363199931, "2024-01-15T23:59:59.931Z"),
                     (1705276800000, "2024-01-15T00:00:00.000Z"),
                     (0, "1970-01-01T00:00:00.000Z")]:
    got = format_exchange_ts(ms)
    check(got == expected, f"{ms} -> {expected}", got)
check(parse_utc_to_nanos("2024-01-15T00:00:00.492Z") ==
      parse_utc_to_nanos("2024-01-15T00:00:00.492000Z"),
      "3 e 6 decimali dello stesso istante danno gli stessi nanosecondi")

print("\nfinestra UTC")
start_ms, end_ms = utc_day_bounds_ms("2024-01-15")
check((start_ms, end_ms) == (1705276800000, 1705363200000),
      "2024-01-15 -> [1705276800000, 1705363200000)", f"{start_ms}, {end_ms}")


# ==========================================================================
# Test che hanno davvero bisogno di SQLite
# ==========================================================================
DDL = """
CREATE TABLE trades (
    category TEXT NOT NULL, symbol TEXT NOT NULL, trade_id TEXT NOT NULL,
    trade_time_ms INTEGER NOT NULL, trade_time_utc TEXT NOT NULL,
    side TEXT NOT NULL, size TEXT NOT NULL, price TEXT NOT NULL,
    tick_direction TEXT, gross_value TEXT, home_notional TEXT,
    foreign_notional TEXT,
    PRIMARY KEY (category, symbol, trade_id));
CREATE INDEX idx_trades_symbol_time ON trades (category, symbol, trade_time_ms);
"""


def ms_to_utc(ms):
    return format_exchange_ts(ms).replace("Z", "000Z")


def build_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript(DDL)
    con.executemany(
        "INSERT INTO trades (category,symbol,trade_id,trade_time_ms,"
        "trade_time_utc,side,size,price,tick_direction,gross_value,"
        "home_notional,foreign_notional) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(c, s, tid, ms, ms_to_utc(ms), side, size, price,
          "PlusTick", "1", "1", "1")
         for (c, s, tid, ms, side, size, price) in rows])
    con.commit()
    con.close()


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    db = tmp / "fixture.sqlite"
    START, END = 1705276800000, 1705363200000
    build_db(db, [
        ("linear", "BTCUSDT", "before", START - 1, "Buy", "1", "100"),
        ("linear", "BTCUSDT", "at-start", START, "Buy", "1", "100"),
        ("linear", "BTCUSDT", "b-mid", START + 5, "Sell", "2", "200"),
        ("linear", "BTCUSDT", "a-mid", START + 5, "Buy", "3", "300"),
        ("linear", "BTCUSDT", "last-in", END - 1, "Sell", "1", "100"),
        ("linear", "BTCUSDT", "at-end", END, "Buy", "1", "100"),
        ("spot", "BTCUSDT", "other-cat", START + 9, "Buy", "1", "100"),
        ("linear", "ETHUSDT", "other-sym", START + 9, "Buy", "1", "100"),
    ])

    print("\n14-15. boundary della finestra temporale")
    out = tmp / "day.jsonl"
    report = run_import(db, "2024-01-15", out)
    ids = [json.loads(l)["trade_id"] for l in out.read_text().splitlines()]
    check("at-start" in ids, "boundary start INCLUSO (>= start_ms)")
    check("at-end" not in ids, "boundary end ESCLUSO (< end_ms)")
    check("before" not in ids, "riga precedente alla finestra esclusa")
    check("last-in" in ids, "ultimo millisecondo della giornata incluso")
    check("other-cat" not in ids and "other-sym" not in ids,
          "category/symbol fuori scope non estratti")

    print("\n16. ordinamento deterministico per (trade_time_ms, trade_id)")
    check(ids == ["at-start", "a-mid", "b-mid", "last-in"],
          "ordine atteso a parita' di trade_time_ms", " -> ".join(ids))
    check(report.canonical_rows == 4 and report.source_rows == 4,
          "4 righe sorgente, 4 canoniche")
    check(report.buy == 2 and report.sell == 2, "conteggi buy/sell coerenti")
    check(report.receive_ts_non_null == 0 and report.sequence_non_null == 0,
          "nessun receive_ts o sequence valorizzato")

    print("\nriproducibilita': stesso input -> stesso SHA-256")
    out2 = tmp / "day2.jsonl"
    report2 = run_import(db, "2024-01-15", out2)
    check(report.sha256 == report2.sha256,
          "due run indipendenti danno lo stesso digest", report.sha256[:32] + "...")

    print("\nstreaming: nessuna query per record")
    con = open_source(db)
    try:
        rows = list(iter_source_rows(con, "linear", "BTCUSDT", START, END))
        check(len(rows) == 4, "il reader restituisce solo la finestra richiesta")
        check(all(isinstance(r, SourceRow) for r in rows),
              "il reader restituisce SourceRow, non tuple grezze")
    finally:
        con.close()

    print("\noutput atomico: un fallimento non lascia un artifact valido")
    bad_db = tmp / "bad.sqlite"
    build_db(bad_db, [
        ("linear", "BTCUSDT", "ok-1", START, "Buy", "1", "100"),
        ("linear", "BTCUSDT", "zz-broken", START + 1, "Hold", "1", "100"),
    ])
    bad_out = tmp / "should-not-exist.jsonl"
    must_fail(lambda: run_import(bad_db, "2024-01-15", bad_out),
              "una riga corrotta interrompe l'import", expect_field="side")
    check(not bad_out.exists(),
          "l'artifact finale NON e' stato creato")
    leftovers = [p.name for p in tmp.iterdir() if ".tmp-" in p.name]
    check(not leftovers, "nessun file temporaneo abbandonato", str(leftovers))

    print("\nprotezione overwrite: un artifact esistente non si sovrascrive")
    guarded = tmp / "guarded.jsonl"
    first_run = run_import(db, "2024-01-15", guarded)
    original_bytes = guarded.read_bytes()
    must_fail(lambda: run_import(db, "2024-01-15", guarded),
              "output gia' esistente respinto senza --overwrite")
    check(guarded.read_bytes() == original_bytes,
          "il file originale e' rimasto intatto, byte per byte",
          f"{len(original_bytes)} byte")
    stale = [pp.name for pp in tmp.iterdir() if ".tmp-" in pp.name]
    check(not stale, "nessun temporaneo abbandonato dal tentativo respinto",
          str(stale))
    again = run_import(db, "2024-01-15", guarded, overwrite=True)
    check(again.sha256 == first_run.sha256,
          "con overwrite=True la sostituzione avviene e da' lo stesso digest",
          again.sha256[:32] + "...")
    occupied = tmp / "occupied.jsonl"
    occupied.mkdir()
    must_fail(lambda: run_import(db, "2024-01-15", occupied),
              "un percorso occupato da una directory viene respinto")

    print("\nfuori scope: fallire chiaramente invece di generalizzare")
    for kwargs, label in [({"venue": "coinbase"}, "venue"),
                          ({"category": "spot"}, "category"),
                          ({"instrument": "ETHUSDT"}, "instrument")]:
        must_fail(lambda k=kwargs: run_import(db, "2024-01-15",
                                              tmp / "nope.jsonl", **k),
                  f"{label} fuori scope respinto")
    must_fail(lambda: run_import(tmp / "assente.sqlite", "2024-01-15",
                                 tmp / "nope.jsonl"),
              "database sorgente inesistente respinto")
    must_fail(lambda: run_import(db, "2024-13-45", tmp / "nope.jsonl"),
              "data inesistente respinta")

    print("\nogni record dell'artifact e' valido secondo il contratto")
    bad_records = []
    for line in out.read_text().splitlines():
        record = json.loads(line)
        ok, why = canonical_is_valid(record)
        if not ok:
            bad_records.append((record.get("trade_id"), why))
    check(not bad_records, "tutti i record scritti passano schemas/trade-v1.json",
          str(bad_records[:2]))

print()
if FAILURES:
    print(f"{RED}FAIL{OFF}: {len(FAILURES)} controlli non superati")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: reference importer conforme")
