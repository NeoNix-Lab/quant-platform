#!/usr/bin/env python3
"""Reference importer: trades storici Bybit (SQLite) -> trade-v1 JSON Lines.

Scope deliberatamente stretto: venue=bybit, category=linear, instrument=BTCUSDT.
Un input fuori scope fallisce invece di essere generalizzato.

Architettura, in quattro pezzi separati:

    1. source reader        iter_source_rows()      streaming da SQLite
    2. canonicalization     canonicalize_trade()    FUNZIONE PURA, senza SQLite
    3. validation           dentro canonicalize_trade(), fail-fast
    4. deterministic writer write_jsonl_atomic()    temporaneo + rename atomico

La semantica non vive nell'SQL ne' nel writer: l'SQL seleziona e ordina, il
writer serializza. Tutto cio' che decide COSA sia un trade canonico sta in
canonicalize_trade(), che prende una riga sorgente e restituisce un dict.

I pattern di validazione NON sono ricopiati qui: vengono letti da
schemas/trade-v1.json, il contratto congelato. Una copia locale potrebbe
divergere dal contratto proprio mentre dichiara di rispettarlo.

FAIL-FAST: qualunque anomalia interrompe l'import e non produce l'artifact
finale. Nessuna coercizione silenziosa, nessuno scarto silenzioso di record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

# --------------------------------------------------------------------------
# Scope supportato. Non generalizzare finche' non serve davvero.
# --------------------------------------------------------------------------
SUPPORTED_VENUE = "bybit"
SUPPORTED_CATEGORY = "linear"
SUPPORTED_INSTRUMENT = "BTCUSDT"
RECORD_SCHEMA_ID = "trade-v1"

# Bybit public trades: 'side' e' il lato del TAKER, cioe' dell'aggressore.
# Non si inferisce da tick_direction, dal movimento del prezzo o dal book.
AGGRESSOR_SIDE_BY_SOURCE = {"Buy": "buy", "Sell": "sell"}

# Ordine dei campi nel record canonico. Determina anche l'ordine delle chiavi
# nel JSON, e quindi lo SHA-256 dell'artifact.
CANONICAL_FIELD_ORDER = (
    "venue", "instrument", "exchange_ts", "receive_ts",
    "price", "size", "aggressor_side", "trade_id", "sequence",
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_UTC_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{RECORD_SCHEMA_ID}.json"


class ImportError_(Exception):
    """Errore di import. Porta il contesto della riga che l'ha causato."""

    def __init__(self, message, *, trade_id=None, field=None):
        self.trade_id = trade_id
        self.field = field
        where = []
        if trade_id is not None:
            where.append(f"trade_id={trade_id!r}")
        if field is not None:
            where.append(f"campo={field!r}")
        super().__init__(message + (f"  [{', '.join(where)}]" if where else ""))


# --------------------------------------------------------------------------
# Contratto congelato: i pattern arrivano da li', non da una copia locale
# --------------------------------------------------------------------------
def load_contract_patterns(schema_path=SCHEMA_PATH):
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    defs = schema["$defs"]
    return {
        "positive_decimal": re.compile(defs["positive_decimal"]["pattern"]),
        "utc_timestamp": re.compile(defs["utc_timestamp"]["pattern"]),
        "venue": re.compile(schema["properties"]["venue"]["pattern"]),
        "required": tuple(schema["required"]),
        "properties": tuple(schema["properties"]),
    }


_CONTRACT = None


def contract():
    global _CONTRACT
    if _CONTRACT is None:
        _CONTRACT = load_contract_patterns()
    return _CONTRACT


# --------------------------------------------------------------------------
# Tempo. Nessun float: i millisecondi sono interi e restano interi.
# --------------------------------------------------------------------------
def format_exchange_ts(trade_time_ms: int) -> str:
    """epoch-ms -> RFC 3339 UTC con esattamente 3 decimali.

    Tre decimali perche' e' la precisione REALE della sorgente: aggiungerne
    altri inventerebbe zeri che il dato non contiene, toglierne perderebbe
    informazione. Il contratto trade-v1 ammette da 1 a 9 decimali, quindi
    questa forma vi rientra senza creare una convenzione nuova.
    """
    if not isinstance(trade_time_ms, int) or isinstance(trade_time_ms, bool):
        raise ImportError_(f"trade_time_ms non e' un intero: {trade_time_ms!r}",
                           field="trade_time_ms")
    seconds, millis = divmod(trade_time_ms, 1000)
    moment = EPOCH + timedelta(seconds=seconds)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{millis:03d}Z"


def parse_utc_to_nanos(value: str) -> int:
    """RFC 3339 UTC -> nanosecondi dall'epoch, esatti.

    Nanosecondi e non millisecondi: se la sorgente portasse precisione
    sub-millisecondo, arrotondare qui nasconderebbe proprio la divergenza che
    il controllo di integrita' deve scoprire.
    """
    if not isinstance(value, str):
        raise ImportError_(f"trade_time_utc non e' una stringa: {value!r}",
                           field="trade_time_utc")
    match = _UTC_RE.match(value)
    if not match:
        raise ImportError_(
            f"trade_time_utc non e' un istante UTC RFC 3339 valido: {value!r}",
            field="trade_time_utc")
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    fraction = match.group(7) or ""
    try:
        moment = datetime(year, month, day, hour, minute, second,
                          tzinfo=timezone.utc)
    except ValueError as exc:
        raise ImportError_(f"trade_time_utc non e' una data esistente: "
                           f"{value!r} ({exc})", field="trade_time_utc") from exc
    epoch_seconds = (moment - EPOCH) // timedelta(seconds=1)
    nanos_in_second = int(fraction.ljust(9, "0")) if fraction else 0
    return epoch_seconds * 1_000_000_000 + nanos_in_second


def utc_day_bounds_ms(date_text: str):
    """'YYYY-MM-DD' -> [start_ms, end_ms) della giornata UTC."""
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ImportError_(f"data non valida: {date_text!r} ({exc})") from exc
    start_ms = ((day - EPOCH) // timedelta(milliseconds=1))
    end_ms = ((day + timedelta(days=1) - EPOCH) // timedelta(milliseconds=1))
    return start_ms, end_ms


# --------------------------------------------------------------------------
# 1. Source reader
# --------------------------------------------------------------------------
class SourceRow(NamedTuple):
    """Una riga della tabella `trades`, coi soli campi che ci servono.

    tick_direction, gross_value, home_notional e foreign_notional non compaiono
    nemmeno qui: non appartengono a trade-v1 e non servono a validarlo.
    """
    category: str
    symbol: str
    trade_id: str
    trade_time_ms: int
    trade_time_utc: str
    side: str
    size: str
    price: str


SELECT_SQL = """
SELECT category, symbol, trade_id, trade_time_ms, trade_time_utc, side, size, price
  FROM trades
 WHERE category = ?
   AND symbol = ?
   AND trade_time_ms >= ?
   AND trade_time_ms <  ?
 ORDER BY trade_time_ms, trade_id
"""


def open_source(sqlite_path) -> sqlite3.Connection:
    """Apre il database in SOLA LETTURA: un importer non modifica la sorgente."""
    path = Path(sqlite_path)
    if not path.is_file():
        raise ImportError_(f"database SQLite non trovato: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def iter_source_rows(connection, category, symbol, start_ms, end_ms,
                     batch_size=10_000) -> Iterator[SourceRow]:
    """Streaming, non fetchall(): 1.1 milioni di righe non entrano in RAM per
    scelta. Una sola query, mai una query per record.

    L'ORDER BY (trade_time_ms, trade_id) serve SOLO alla riproducibilita'
    dell'artifact. Non e' una sequence e non asserisce l'ordinamento reale di
    esecuzione sull'exchange, che questi dati non permettono di dimostrare.
    """
    cursor = connection.cursor()
    cursor.arraysize = batch_size
    cursor.execute(SELECT_SQL, (category, symbol, start_ms, end_ms))
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        for row in batch:
            yield SourceRow(*row)


# --------------------------------------------------------------------------
# 2 + 3. Canonicalization e validation — FUNZIONE PURA, senza SQLite
# --------------------------------------------------------------------------
def _validate_decimal(value, field, trade_id):
    """Valida un decimale ESATTO. Mai float: IEEE-754 non rappresenta 0.004."""
    if not isinstance(value, str):
        raise ImportError_(f"{field} non e' una stringa decimale: {value!r}",
                           trade_id=trade_id, field=field)
    if not contract()["positive_decimal"].match(value):
        raise ImportError_(
            f"{field}={value!r} non rispetta il formato decimale canonico di "
            f"trade-v1 (niente zeri iniziali, niente notazione esponenziale, "
            f"niente punto sospeso)", trade_id=trade_id, field=field)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ImportError_(f"{field}={value!r} non e' un decimale: {exc}",
                           trade_id=trade_id, field=field) from exc
    if not amount.is_finite() or amount <= 0:
        raise ImportError_(f"{field}={value!r} deve essere > 0",
                           trade_id=trade_id, field=field)
    return value


def canonicalize_trade(row: SourceRow, *,
                       venue=SUPPORTED_VENUE,
                       expected_category=SUPPORTED_CATEGORY,
                       expected_instrument=SUPPORTED_INSTRUMENT) -> dict:
    """Una riga sorgente -> un record trade-v1. Pura: nessun I/O, nessuno stato.

    Solleva ImportError_ su qualunque anomalia. Non corregge, non arrotonda,
    non sceglie: se il dato non e' inequivocabile, l'import si ferma.
    """
    trade_id = row.trade_id

    # --- identita' della sorgente, verificata riga per riga -----------------
    # Non ci si fida del solo dataset_info ne' del WHERE della query: se una
    # riga estratta viola l'identita' attesa, l'estrazione stessa e' sospetta.
    if row.category != expected_category:
        raise ImportError_(
            f"category={row.category!r}, attesa {expected_category!r}",
            trade_id=trade_id, field="category")
    if row.symbol != expected_instrument:
        raise ImportError_(
            f"symbol={row.symbol!r}, atteso {expected_instrument!r}",
            trade_id=trade_id, field="symbol")

    # --- trade_id -----------------------------------------------------------
    if not isinstance(trade_id, str) or not trade_id:
        raise ImportError_(f"trade_id mancante o non stringa: {trade_id!r}",
                           field="trade_id")

    # --- tempo: trade_time_ms e' autorevole, trade_time_utc lo controlla -----
    if row.trade_time_ms is None:
        raise ImportError_("trade_time_ms mancante", trade_id=trade_id,
                           field="trade_time_ms")
    exchange_ts = format_exchange_ts(row.trade_time_ms)

    if not row.trade_time_utc:
        raise ImportError_("trade_time_utc mancante", trade_id=trade_id,
                           field="trade_time_utc")
    utc_nanos = parse_utc_to_nanos(row.trade_time_utc)
    ms_nanos = row.trade_time_ms * 1_000_000
    if utc_nanos != ms_nanos:
        # Non si sceglie quale dei due sia giusto: non e' deducibile.
        raise ImportError_(
            f"trade_time_ms e trade_time_utc rappresentano istanti DIVERSI: "
            f"{row.trade_time_ms} ms = {ms_nanos} ns, "
            f"{row.trade_time_utc!r} = {utc_nanos} ns "
            f"(differenza {utc_nanos - ms_nanos} ns)",
            trade_id=trade_id, field="trade_time_utc")

    # --- aggressor side -----------------------------------------------------
    try:
        aggressor_side = AGGRESSOR_SIDE_BY_SOURCE[row.side]
    except (KeyError, TypeError):
        raise ImportError_(
            f"side={row.side!r} non riconosciuto: attesi "
            f"{sorted(AGGRESSOR_SIDE_BY_SOURCE)}. Il lato aggressore non si "
            f"inferisce da tick_direction ne' dal movimento del prezzo.",
            trade_id=trade_id, field="side") from None

    # --- price / size: stringhe decimali esatte, byte per byte --------------
    price = _validate_decimal(row.price, "price", trade_id)
    size = _validate_decimal(row.size, "size", trade_id)

    if not contract()["venue"].match(venue):
        raise ImportError_(f"venue={venue!r} non rispetta il contratto",
                           field="venue")

    return {
        "venue": venue,
        "instrument": row.symbol,
        "exchange_ts": exchange_ts,
        # Lo storico non possiede un vero local-arrival timestamp. Derivarlo da
        # exchange_ts, dalla data del file o dall'ora di import falsificherebbe
        # ogni misura futura di latenza: resta null.
        "receive_ts": None,
        "price": price,
        "size": size,
        "aggressor_side": aggressor_side,
        "trade_id": trade_id,
        # Nessuna sequence sintetica da rowid, ordine di iterazione o indice.
        "sequence": None,
    }


def encode_record(record: dict) -> bytes:
    """Record -> una riga JSON, deterministica byte per byte.

    Ordine delle chiavi fissato dal contratto, separatori espliciti, ASCII,
    newline '\\n' esplicito e output in BINARIO: su Windows il modo testo
    tradurrebbe '\\n' in '\\r\\n' e lo SHA-256 differirebbe per piattaforma.
    """
    unexpected = set(record) - set(CANONICAL_FIELD_ORDER)
    if unexpected:
        raise ImportError_(
            f"il record canonico contiene campi estranei a trade-v1: "
            f"{sorted(unexpected)}")
    ordered = {name: record[name] for name in CANONICAL_FIELD_ORDER
               if name in record}
    line = json.dumps(ordered, ensure_ascii=True, separators=(",", ":"))
    return line.encode("utf-8") + b"\n"


# --------------------------------------------------------------------------
# 4. Deterministic writer, atomico
# --------------------------------------------------------------------------
class WriteResult(NamedTuple):
    rows: int
    bytes_written: int
    sha256: str


def write_jsonl_atomic(records: Iterable[dict], output_path,
                       *, overwrite: bool = False) -> WriteResult:
    """Scrive su un temporaneo nello STESSO filesystem e pubblica con rename.

    Il rename e' atomico dentro un filesystem: o l'artifact finale non esiste,
    o e' completo. Non esiste uno stato intermedio in cui sembri valido.
    Se qualcosa fallisce, il temporaneo viene rimosso e il file finale non
    viene creato affatto.

    Un artifact gia' esistente NON viene sovrascritto se non esplicitamente
    richiesto: un import e' una pubblicazione, e sovrascrivere in silenzio un
    dataset gia' pubblicato distrugge dati senza lasciare traccia. Il controllo
    e' fatto due volte, prima di lavorare (per fallire subito invece che dopo
    un milione di righe) e appena prima del rename (per restringere la
    finestra fra controllo e pubblicazione). Resta una race teorica se un
    altro processo crea il file esattamente in quell'istante: e' accettata
    consapevolmente e non giustifica di rinunciare al controllo.
    """
    output_path = Path(output_path)
    if not overwrite and output_path.exists():
        raise ImportError_(
            f"l'artifact {output_path} esiste gia'. Passare --overwrite per "
            f"sostituirlo esplicitamente; il file esistente non e' stato toccato.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")

    digest = hashlib.sha256()
    rows = 0
    written = 0
    try:
        with open(temporary, "wb") as handle:
            for record in records:
                payload = encode_record(record)
                handle.write(payload)
                digest.update(payload)
                rows += 1
                written += len(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not overwrite and output_path.exists():
            raise ImportError_(
                f"l'artifact {output_path} e' comparso durante l'import. "
                f"Non lo sovrascrivo: passare --overwrite se e' voluto.")
        os.replace(temporary, output_path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return WriteResult(rows=rows, bytes_written=written,
                       sha256=digest.hexdigest())


# --------------------------------------------------------------------------
# Statistiche di dominio, tenute FUORI dal writer
# --------------------------------------------------------------------------
class TradeStats:
    def __init__(self):
        self.rows = 0
        self.by_side = {"buy": 0, "sell": 0, "unknown": 0}
        self.first_exchange_ts = None
        self.last_exchange_ts = None
        self.receive_ts_non_null = 0
        self.sequence_non_null = 0

    def observe(self, record):
        self.rows += 1
        self.by_side[record["aggressor_side"]] += 1
        if self.first_exchange_ts is None:
            self.first_exchange_ts = record["exchange_ts"]
        self.last_exchange_ts = record["exchange_ts"]
        if record["receive_ts"] is not None:
            self.receive_ts_non_null += 1
        if record["sequence"] is not None:
            self.sequence_non_null += 1


def _canonical_stream(rows, stats, venue, category, instrument):
    for row in rows:
        record = canonicalize_trade(row, venue=venue,
                                    expected_category=category,
                                    expected_instrument=instrument)
        stats.observe(record)
        yield record


# --------------------------------------------------------------------------
# Orchestrazione
# --------------------------------------------------------------------------
class ImportReport(NamedTuple):
    source_rows: int
    canonical_rows: int
    buy: int
    sell: int
    first_exchange_ts: str
    last_exchange_ts: str
    receive_ts_non_null: int
    sequence_non_null: int
    sha256: str
    bytes_written: int
    output: str


def run_import(sqlite_path, date_text, output_path, *,
               venue=SUPPORTED_VENUE,
               category=SUPPORTED_CATEGORY,
               instrument=SUPPORTED_INSTRUMENT,
               overwrite: bool = False) -> ImportReport:
    if venue != SUPPORTED_VENUE:
        raise ImportError_(
            f"venue {venue!r} fuori scope: questo reference importer supporta "
            f"solo {SUPPORTED_VENUE!r}")
    if category != SUPPORTED_CATEGORY:
        raise ImportError_(
            f"category {category!r} fuori scope: solo {SUPPORTED_CATEGORY!r}")
    if instrument != SUPPORTED_INSTRUMENT:
        raise ImportError_(
            f"instrument {instrument!r} fuori scope: solo "
            f"{SUPPORTED_INSTRUMENT!r}")

    start_ms, end_ms = utc_day_bounds_ms(date_text)
    connection = open_source(sqlite_path)
    stats = TradeStats()
    try:
        counted = _CountingRows(
            iter_source_rows(connection, category, instrument, start_ms, end_ms))
        result = write_jsonl_atomic(
            _canonical_stream(counted, stats, venue, category, instrument),
            output_path, overwrite=overwrite)
    finally:
        connection.close()

    if counted.count != result.rows:
        raise ImportError_(
            f"righe sorgente {counted.count} != righe canoniche {result.rows}: "
            f"un record e' stato perso o duplicato")

    return ImportReport(
        source_rows=counted.count,
        canonical_rows=result.rows,
        buy=stats.by_side["buy"],
        sell=stats.by_side["sell"],
        first_exchange_ts=stats.first_exchange_ts,
        last_exchange_ts=stats.last_exchange_ts,
        receive_ts_non_null=stats.receive_ts_non_null,
        sequence_non_null=stats.sequence_non_null,
        sha256=result.sha256,
        bytes_written=result.bytes_written,
        output=str(output_path),
    )


class _CountingRows:
    """Conta le righe SORGENTE separatamente da quelle canoniche, cosi' che
    'nessuna riga persa o duplicata' sia una verifica e non un'assunzione."""

    def __init__(self, rows):
        self._rows = rows
        self.count = 0

    def __iter__(self):
        for row in self._rows:
            self.count += 1
            yield row


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="import_bybit_trades",
        description="Reference importer: trades Bybit da SQLite a trade-v1 JSONL.")
    parser.add_argument("--sqlite", required=True,
                        help="percorso del database SQLite sorgente")
    parser.add_argument("--date", required=True,
                        help="giornata UTC da importare, YYYY-MM-DD")
    parser.add_argument("--output", required=True,
                        help="percorso dell'artifact JSON Lines da produrre")
    parser.add_argument("--venue", default=SUPPORTED_VENUE)
    parser.add_argument("--category", default=SUPPORTED_CATEGORY)
    parser.add_argument("--instrument", default=SUPPORTED_INSTRUMENT)
    parser.add_argument("--overwrite", action="store_true",
                        help="sostituisce un artifact gia' esistente; senza "
                             "questo flag un output esistente fa fallire l'import")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = run_import(args.sqlite, args.date, args.output,
                            venue=args.venue, category=args.category,
                            instrument=args.instrument,
                            overwrite=args.overwrite)
    except ImportError_ as exc:
        print(f"IMPORT FALLITO: {exc}", file=sys.stderr)
        print("nessun artifact finale e' stato creato.", file=sys.stderr)
        return 1

    print(f"venue/instrument     {args.venue} / {args.instrument} ({args.category})")
    print(f"giornata UTC         {args.date}")
    print(f"righe sorgente       {report.source_rows:,}")
    print(f"righe canoniche      {report.canonical_rows:,}")
    print(f"  aggressor buy      {report.buy:,}")
    print(f"  aggressor sell     {report.sell:,}")
    print(f"first exchange_ts    {report.first_exchange_ts}")
    print(f"last  exchange_ts    {report.last_exchange_ts}")
    print(f"receive_ts non-null  {report.receive_ts_non_null}")
    print(f"sequence non-null    {report.sequence_non_null}")
    print(f"byte scritti         {report.bytes_written:,}")
    print(f"sha256               {report.sha256}")
    print(f"output               {report.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
