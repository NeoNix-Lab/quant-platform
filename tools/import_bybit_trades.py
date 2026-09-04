#!/usr/bin/env python3
"""Reference importer: Bybit historical source -> deterministic trade-v1 JSONL.

The production source adapter owns SQLite access, validation, and mapping to
``TradeRecord``. This tool retains only CLI orchestration, statistics, and the
legacy byte-exact JSONL serializer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from quant_platform.data.models import TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit_historical import (  # noqa: E402
    BybitHistoricalExtractAccumulator,
    BybitHistoricalExtractEvidence,
    BybitHistoricalSourceError,
    canonicalize_bybit_historical_trade_v1,
    iter_bybit_historical_trade_rows,
    open_bybit_historical_source,
    utc_day_bounds_ms,
)

# --------------------------------------------------------------------------
# Scope supportato. Non generalizzare finche' non serve davvero.
# --------------------------------------------------------------------------
SUPPORTED_VENUE = "bybit"
SUPPORTED_CATEGORY = "linear"
SUPPORTED_INSTRUMENT = "BTCUSDT"
RECORD_SCHEMA_ID = "trade-v1"

# Ordine dei campi nel record canonico. Determina anche l'ordine delle chiavi
# nel JSON, e quindi lo SHA-256 dell'artifact.
CANONICAL_FIELD_ORDER = (
    "venue", "instrument", "exchange_ts", "receive_ts",
    "price", "size", "aggressor_side", "trade_id", "sequence",
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# The CLI keeps one failure vocabulary. Source semantics, and therefore the
# error that carries them, belong to the production adapter.
ImportError_ = BybitHistoricalSourceError


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


def _record_exchange_ts(record: TradeRecord) -> str:
    if not isinstance(record, TradeRecord):
        raise ImportError_("the JSONL writer accepts TradeRecord instances only")
    nanos = record.exchange_ts.epoch_ns
    if nanos % 1_000_000:
        raise ImportError_(
            "exchange_ts is not representable with the reference 3-digit format",
            trade_id=record.trade_id, field="exchange_ts")
    return format_exchange_ts(nanos // 1_000_000)


def _record_as_json_mapping(record: TradeRecord | Mapping) -> Mapping:
    if isinstance(record, TradeRecord):
        return {
            "venue": record.venue,
            "instrument": record.instrument,
            "exchange_ts": _record_exchange_ts(record),
            "receive_ts": None if record.receive_ts is None else record.receive_ts.isoformat(),
            "price": record.price,
            "size": record.size,
            "aggressor_side": record.aggressor_side,
            "trade_id": record.trade_id,
            "sequence": record.sequence,
        }
    if isinstance(record, Mapping):
        # A Mapping reaches the reference serializer only from callers that
        # already hold a canonical record. It is serialized, never
        # canonicalized -- but an unknown key means the caller built
        # something that is not trade-v1, and dropping it silently would
        # discard a source field without a trace.
        unexpected = set(record) - set(CANONICAL_FIELD_ORDER)
        if unexpected:
            raise ImportError_(
                f"il record canonico contiene campi estranei a trade-v1: "
                f"{sorted(unexpected)}")
        return record
    raise ImportError_("the JSONL writer accepts TradeRecord instances only")


def encode_record(record: TradeRecord | Mapping) -> bytes:
    """Record -> una riga JSON, deterministica byte per byte.

    Ordine delle chiavi fissato dal contratto, separatori espliciti, ASCII,
    newline '\\n' esplicito e output in BINARIO: su Windows il modo testo
    tradurrebbe '\\n' in '\\r\\n' e lo SHA-256 differirebbe per piattaforma.
    """
    mapping = _record_as_json_mapping(record)
    ordered = {name: mapping[name] for name in CANONICAL_FIELD_ORDER
               if name in mapping}
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
        self.by_side[record.aggressor_side] += 1
        if self.first_exchange_ts is None:
            self.first_exchange_ts = _record_exchange_ts(record)
        self.last_exchange_ts = _record_exchange_ts(record)
        if record.receive_ts is not None:
            self.receive_ts_non_null += 1
        if record.sequence is not None:
            self.sequence_non_null += 1


def _canonical_stream(rows, stats):
    for row in rows:
        record = canonicalize_bybit_historical_trade_v1(row)
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
    extract_evidence: BybitHistoricalExtractEvidence


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
    connection = open_bybit_historical_source(sqlite_path)
    stats = TradeStats()
    accumulator = BybitHistoricalExtractAccumulator()
    try:
        counted = _CountingRows(iter_bybit_historical_trade_rows(
            connection, start_ms, end_ms, category=category, symbol=instrument,
            accumulator=accumulator))
        result = write_jsonl_atomic(
            _canonical_stream(counted, stats),
            output_path, overwrite=overwrite)
    finally:
        connection.close()

    if counted.count != result.rows:
        raise ImportError_(
            f"righe sorgente {counted.count} != righe canoniche {result.rows}: "
            f"un record e' stato perso o duplicato")

    return ImportReport(
        source_rows=accumulator.row_count,
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
        extract_evidence=accumulator.evidence(date_text, start_ms, end_ms),
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
