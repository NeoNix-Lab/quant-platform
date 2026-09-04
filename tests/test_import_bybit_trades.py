#!/usr/bin/env python3
"""Test del reference importer Bybit trades -> artifact trade-v1 JSONL.

Questo file NON verifica piu' la semantica sorgente: quella appartiene al
production seam ed e' provata da tests/test_bybit_historical_source_v1.py.
Qui si verifica soltanto cio' che il tool possiede davvero:

    TradeRecord prodotto dal seam di produzione
        -> serializzazione JSONL di riferimento
        -> byte esatti, ordine delle chiavi, SHA-256

piu' il percorso reale completo:

    SQLite temporaneo -> seam di produzione -> importer -> artifact

I byte attesi sono CONGELATI in questo file. Non aggiornarli per accomodare
un refactor: un byte diverso significa che l'artifact di riferimento e'
cambiato, non che il test e' obsoleto.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from import_bybit_trades import (  # noqa: E402
    CANONICAL_FIELD_ORDER, ImportError_, encode_record, format_exchange_ts,
    run_import, write_jsonl_atomic,
)
from quant_platform.source_adapters.bybit_historical import (  # noqa: E402
    BybitHistoricalTradeRow, canonicalize_bybit_historical_trade_v1,
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
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def source_row(trade_time_ms, side, price, size, trade_id):
    """Una riga sorgente coerente: i due timestamp sorgente concordano."""
    seconds, millis = divmod(trade_time_ms, 1000)
    moment = EPOCH + timedelta(seconds=seconds)
    return BybitHistoricalTradeRow(
        category="linear", symbol="BTCUSDT", trade_id=trade_id,
        trade_time_ms=trade_time_ms,
        trade_time_utc=f"{moment:%Y-%m-%dT%H:%M:%S}.{millis:03d}000Z",
        side=side, size=size, price=price)


def production_record(*args):
    """Il TradeRecord vero, prodotto dal canonicalizer di produzione."""
    return canonicalize_bybit_historical_trade_v1(source_row(*args))


# ==========================================================================
# 1. Byte congelati: TradeRecord di produzione -> riga JSONL di riferimento
#
# Include obbligatoriamente i due casi che una resa ingenua via
# Instant.isoformat() romperebbe in silenzio: il millisecondo con zero finale
# (.490 -> .49) e il millisecondo nullo (.000 -> nessuna frazione).
# ==========================================================================
print("\n1. byte congelati dell'artifact di riferimento")
FROZEN = [
    ((1705276800490, "Buy", "41731.10", "0.00400", "tid-1"),
     b'{"venue":"bybit","instrument":"BTCUSDT",'
     b'"exchange_ts":"2024-01-15T00:00:00.490Z","receive_ts":null,'
     b'"price":"41731.10","size":"0.00400","aggressor_side":"buy",'
     b'"trade_id":"tid-1","sequence":null}\n'),
    ((1705276800000, "Sell", "41731.1", "0.004",
      "0a14459b-c7a6-5e90-bc0a-30b46a3fbb90"),
     b'{"venue":"bybit","instrument":"BTCUSDT",'
     b'"exchange_ts":"2024-01-15T00:00:00.000Z","receive_ts":null,'
     b'"price":"41731.1","size":"0.004","aggressor_side":"sell",'
     b'"trade_id":"0a14459b-c7a6-5e90-bc0a-30b46a3fbb90","sequence":null}\n'),
    ((1705276800400, "Sell", "9007199254740993.1",
      "0.1000000000000000055511151231257827", "zz"),
     b'{"venue":"bybit","instrument":"BTCUSDT",'
     b'"exchange_ts":"2024-01-15T00:00:00.400Z","receive_ts":null,'
     b'"price":"9007199254740993.1",'
     b'"size":"0.1000000000000000055511151231257827",'
     b'"aggressor_side":"sell","trade_id":"zz","sequence":null}\n'),
    ((1705363199931, "Buy", "1", "123456.789012345678901234567890", "1"),
     b'{"venue":"bybit","instrument":"BTCUSDT",'
     b'"exchange_ts":"2024-01-15T23:59:59.931Z","receive_ts":null,'
     b'"price":"1","size":"123456.789012345678901234567890",'
     b'"aggressor_side":"buy","trade_id":"1","sequence":null}\n'),
    ((0, "Buy", "0.5", "0.0000001", "epoch"),
     b'{"venue":"bybit","instrument":"BTCUSDT",'
     b'"exchange_ts":"1970-01-01T00:00:00.000Z","receive_ts":null,'
     b'"price":"0.5","size":"0.0000001","aggressor_side":"buy",'
     b'"trade_id":"epoch","sequence":null}\n'),
]
for arguments, expected in FROZEN:
    produced = encode_record(production_record(*arguments))
    check(produced == expected,
          f"trade_time_ms={arguments[0]} -> byte congelati",
          produced.decode().strip())

print("\n2. receive_ts e sequence restano null nell'artifact")
for arguments, _ in FROZEN:
    line = json.loads(encode_record(production_record(*arguments)))
    check(line["receive_ts"] is None and line["sequence"] is None,
          f"trade_time_ms={arguments[0]}: receive_ts e sequence null")

print("\n3. price e size restano stringhe nel JSON, mai numeri")
for arguments, _ in FROZEN:
    line = json.loads(encode_record(production_record(*arguments)))
    check(isinstance(line["price"], str) and isinstance(line["size"], str),
          f"trade_time_ms={arguments[0]}: price e size sono stringhe",
          f"price={line['price']!r} size={line['size']!r}")

print("\n4. ordine delle chiavi e assenza di campi sorgente")
forbidden = {"category", "trade_time_ms", "trade_time_utc", "side", "symbol",
             "tick_direction", "gross_value", "home_notional", "foreign_notional"}
for arguments, _ in FROZEN:
    line = json.loads(encode_record(production_record(*arguments)))
    check(tuple(line) == CANONICAL_FIELD_ORDER,
          f"trade_time_ms={arguments[0]}: chiavi nell'ordine del contratto",
          ", ".join(line))
    check(not (set(line) & forbidden),
          f"trade_time_ms={arguments[0]}: nessun campo sorgente filtrato")

print("\n5. conformita' al contratto congelato trade-v1")
for arguments, _ in FROZEN:
    line = json.loads(encode_record(production_record(*arguments)))
    errors = sorted(VALIDATOR.iter_errors(line), key=str)
    check(not errors, f"trade_time_ms={arguments[0]}: valido per trade-v1",
          "; ".join(e.message for e in errors[:2]))

print("\n6. LF, mai CRLF, e determinismo del serializzatore")
for arguments, _ in FROZEN:
    payload = encode_record(production_record(*arguments))
    check(payload.endswith(b"\n") and not payload.endswith(b"\r\n"),
          f"trade_time_ms={arguments[0]}: newline LF")
check(encode_record(production_record(*FROZEN[0][0])) ==
      encode_record(production_record(*FROZEN[0][0])),
      "due serializzazioni dello stesso record danno gli stessi byte")


# ==========================================================================
# 7. Fail-fast del serializzatore su Mapping di riferimento
# ==========================================================================
print("\n7. campi estranei respinti, campi assenti omessi (comportamento baseline)")
canonical_mapping = json.loads(encode_record(production_record(*FROZEN[0][0])))

check(encode_record(canonical_mapping) == FROZEN[0][1],
      "un Mapping canonico produce gli stessi byte del TradeRecord")

for extraneous in ("gross_value", "tick_direction", "unknown_field"):
    polluted = dict(canonical_mapping, **{extraneous: "x"})
    must_fail(lambda p=polluted: encode_record(p),
              f"campo estraneo {extraneous!r} respinto, non scartato in silenzio")

polluted_many = dict(canonical_mapping, gross_value="1", tick_direction="PlusTick")
must_fail(lambda: encode_record(polluted_many),
          "piu' campi estranei insieme respinti")

for absent in ("sequence", "receive_ts", "trade_id"):
    partial = {k: v for k, v in canonical_mapping.items() if k != absent}
    payload = encode_record(partial)
    decoded = json.loads(payload)
    check(absent not in decoded and tuple(decoded) ==
          tuple(n for n in CANONICAL_FIELD_ORDER if n != absent),
          f"campo noto assente {absent!r}: omesso, come nel baseline",
          payload.decode().strip())

must_fail(lambda: encode_record(["not", "a", "record"]),
          "un input che non e' TradeRecord ne' Mapping viene respinto")


# ==========================================================================
# 8. Resa temporale di riferimento: responsabilita' di serializzazione
# ==========================================================================
print("\n8. formattazione di exchange_ts a tre decimali esatti")
for ms, expected in [(1705276800492, "2024-01-15T00:00:00.492Z"),
                     (1705363199931, "2024-01-15T23:59:59.931Z"),
                     (1705276800000, "2024-01-15T00:00:00.000Z"),
                     (1705276800490, "2024-01-15T00:00:00.490Z"),
                     (1705276800400, "2024-01-15T00:00:00.400Z"),
                     (0, "1970-01-01T00:00:00.000Z")]:
    got = format_exchange_ts(ms)
    check(got == expected, f"{ms} -> {expected}", got)
for ms in (1705276800490, 1705276800000, 1705276800400, 1705276800492):
    rendered = format_exchange_ts(ms)
    record = production_record(ms, "Buy", "1", "1", "t")
    check(record.exchange_ts.epoch_ns == ms * 1_000_000,
          f"{ms}: la resa a tre decimali {rendered} descrive l'istante canonico",
          f"{record.exchange_ts.epoch_ns} ns")


# ==========================================================================
# 9. Percorso reale: SQLite -> seam di produzione -> importer -> artifact
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

    print("\n9. boundary della finestra temporale, sul percorso reale")
    out = tmp / "day.jsonl"
    report = run_import(db, "2024-01-15", out)
    ids = [json.loads(l)["trade_id"] for l in out.read_text().splitlines()]
    check("at-start" in ids, "boundary start INCLUSO (>= start_ms)")
    check("at-end" not in ids, "boundary end ESCLUSO (< end_ms)")
    check("before" not in ids, "riga precedente alla finestra esclusa")
    check("last-in" in ids, "ultimo millisecondo della giornata incluso")
    check("other-cat" not in ids and "other-sym" not in ids,
          "category/symbol fuori scope non estratti")

    print("\n10. ordinamento deterministico per (trade_time_ms, trade_id)")
    check(ids == ["at-start", "a-mid", "b-mid", "last-in"],
          "ordine atteso a parita' di trade_time_ms", " -> ".join(ids))
    check(report.canonical_rows == 4 and report.source_rows == 4,
          "4 righe sorgente, 4 canoniche")
    check(report.buy == 2 and report.sell == 2, "conteggi buy/sell coerenti")
    check(report.receive_ts_non_null == 0 and report.sequence_non_null == 0,
          "nessun receive_ts o sequence valorizzato")

    print("\n11. byte dell'artifact prodotto dal percorso reale")
    produced_lines = out.read_bytes().split(b"\n")[:-1]
    expected_first = (
        b'{"venue":"bybit","instrument":"BTCUSDT",'
        b'"exchange_ts":"2024-01-15T00:00:00.000Z","receive_ts":null,'
        b'"price":"100","size":"1","aggressor_side":"buy",'
        b'"trade_id":"at-start","sequence":null}')
    check(produced_lines[0] == expected_first,
          "prima riga dell'artifact identica ai byte attesi",
          produced_lines[0].decode())
    check(all(not line.endswith(b"\r") for line in produced_lines),
          "nessun CRLF introdotto dalla piattaforma")
    check(report.extract_evidence.source_row_count == 4,
          "la source evidence e' disponibile dopo l'esaurimento completo",
          report.extract_evidence.detail)

    print("\n12. riproducibilita': stesso input -> stesso SHA-256")
    out2 = tmp / "day2.jsonl"
    report2 = run_import(db, "2024-01-15", out2)
    check(report.sha256 == report2.sha256,
          "due run indipendenti danno lo stesso digest", report.sha256[:32] + "...")
    check(report.extract_evidence.source_fingerprint_sha256 ==
          report2.extract_evidence.source_fingerprint_sha256,
          "e la stessa source fingerprint")

    print("\n13. output atomico: un fallimento non lascia un artifact valido")
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

    print("\n14. protezione overwrite: un artifact esistente non si sovrascrive")
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

    print("\n15. fuori scope: fallire chiaramente invece di generalizzare")
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

    print("\n16. il writer accetta il TradeRecord di produzione senza ponti")
    direct = tmp / "direct.jsonl"
    result = write_jsonl_atomic(
        (production_record(*arguments) for arguments, _ in FROZEN), direct)
    check(result.rows == len(FROZEN),
          "write_jsonl_atomic consuma direttamente i TradeRecord di produzione")
    check(direct.read_bytes() == b"".join(expected for _, expected in FROZEN),
          "e produce esattamente i byte congelati, nell'ordine di emissione")


# ==========================================================================
print()
if FAILURES:
    print(f"{RED}FAIL{OFF}: {len(FAILURES)} controlli non superati")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: reference importer conforme")
