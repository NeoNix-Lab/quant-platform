#!/usr/bin/env python3
"""Genera le fixture dei due manifest.

Le valide derivano rel_root dalla convenzione invece di scriverlo a mano, cosi'
una fixture non puo' divergere dalla regola che dovrebbe dimostrare.
Le negative partono da una base valida e rompono UNA sola cosa.
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/market-platform")


# UNICA source of truth della convenzione: il semantic validator.
sys.path.insert(0, str(ROOT / "tools"))
from semantic_validator import derive_rel_root  # noqa: E402


def dataset(**identity):
    doc = {"schema_version": "dataset-manifest-v1"}
    doc.update(identity)
    doc["rel_root"] = derive_rel_root(identity)
    return doc


DROP = object()


def emit(directory, name, base, **changes):
    doc = copy.deepcopy(base)
    for key, value in changes.items():
        if value is DROP:
            doc.pop(key, None)
        else:
            doc[key] = value
    (directory / name).write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fresh(path):
    path.mkdir(parents=True, exist_ok=True)
    for f in path.glob("*.json"):
        f.unlink()
    return path


RAW_TRADES = {"layer": "raw", "dataset_kind": "trades", "venue": "bybit",
              "instrument": "BTCUSDT", "record_schema_id": "trade-v1"}
CANON_TRADES = dict(RAW_TRADES, layer="canonical")

# ======================================================= dataset-manifest
dv = fresh(ROOT / "fixtures" / "dataset-manifest-v1")

emit(dv, "valid-raw.json",
     dataset(**RAW_TRADES, created_at="2026-08-01T00:00:00Z"))

emit(dv, "valid-raw-schema-v2-distinct.json",
     dataset(**dict(RAW_TRADES, record_schema_id="trade-v2"),
             created_at="2027-01-01T00:00:00Z"))

emit(dv, "valid-canonical-derived.json",
     dataset(**CANON_TRADES, created_at="2026-08-01T00:05:00Z",
             derived_from=[RAW_TRADES], transform="canonicalize-trades-v1"))

emit(dv, "valid-canonical-multi-parent.json",
     dataset(layer="canonical", dataset_kind="footprint", venue="bybit",
             instrument="BTCUSDT", record_schema_id="footprint-v1",
             created_at="2026-08-01T00:10:00Z",
             derived_from=[
                 CANON_TRADES,
                 dict(CANON_TRADES, dataset_kind="l2", record_schema_id="l2-v1"),
             ],
             transform="build-footprint-v1"))

FEAT_TM = {"layer": "features", "dataset_kind": "trade_microstructure",
           "venue": "bybit", "instrument": "BTCUSDT",
           "feature_set_slug": "trade_microstructure", "feature_set_version": 1,
           "record_schema_id": "feature-set-v1"}

emit(dv, "valid-features.json",
     dataset(**FEAT_TM, created_at="2026-08-01T00:20:00Z",
             derived_from=[CANON_TRADES],
             transform="build-trade-microstructure-v1"))

emit(dv, "valid-features-parent-is-features.json",
     dataset(layer="features", dataset_kind="footprint_microstructure",
             venue="bybit", instrument="BTCUSDT",
             feature_set_slug="footprint_microstructure", feature_set_version=2,
             record_schema_id="feature-set-v1",
             created_at="2026-08-02T00:00:00Z",
             derived_from=[FEAT_TM],
             transform="build-footprint-microstructure-v2"))

emit(dv, "valid-instrument-with-separator.json",
     dataset(layer="raw", dataset_kind="trades", venue="kraken",
             instrument="XBT/USD", record_schema_id="trade-v1",
             created_at="2026-08-01T00:00:00Z"))

# stesso simbolo con '-' invece di '/': deve restare un dataset DISTINTO
emit(dv, "valid-instrument-hyphen-not-slash.json",
     dataset(layer="raw", dataset_kind="trades", venue="kraken",
             instrument="XBT-USD", record_schema_id="trade-v1",
             created_at="2026-08-01T00:00:00Z"))

# il '%' del valore nativo viene encodato a sua volta
emit(dv, "valid-instrument-with-percent.json",
     dataset(layer="raw", dataset_kind="trades", venue="bybit",
             instrument="BTC%USD", record_schema_id="trade-v1",
             created_at="2026-08-01T00:00:00Z"))

DATASET_BASE = json.loads((dv / "valid-canonical-derived.json").read_text())
di = fresh(dv / "invalid")
D = lambda name, **kw: emit(di, name, DATASET_BASE, **kw)

# venue / instrument obbligatori in TUTTI i layer
D("venue-null.json", venue=None)
D("venue-missing.json", venue=DROP)
D("venue-uppercase.json", venue="Bybit")
D("raw-venue-null.json", layer="raw", venue=None,
  derived_from=DROP, transform=DROP)
D("instrument-missing.json", instrument=DROP)
D("instrument-blank.json", instrument="   ")
# lineage
D("raw-with-derived-from.json", layer="raw")
D("raw-with-transform.json", layer="raw", derived_from=[])
D("derived-without-parents.json", derived_from=[])
D("derived-without-transform.json", transform=DROP)
D("transform-code-ref-removed.json", transform_code_ref="b30fa77")
D("parent-missing-schema-id.json",
  derived_from=[{k: v for k, v in RAW_TRADES.items() if k != "record_schema_id"}])
D("parent-venue-null.json", derived_from=[dict(RAW_TRADES, venue=None)])
D("parent-unknown-field.json", derived_from=[dict(RAW_TRADES, dataset_id="abc")])
D("parent-duplicated.json", derived_from=[RAW_TRADES, dict(RAW_TRADES)])
D("parent-features-without-feature-set.json",
  derived_from=[{"layer": "features", "dataset_kind": "trade_microstructure",
                 "venue": "bybit", "instrument": "BTCUSDT",
                 "record_schema_id": "feature-set-v1"}])
# feature set
D("features-without-feature-set.json", layer="features",
  dataset_kind="trade_microstructure")
D("non-features-with-feature-set.json", feature_set_slug="tm", feature_set_version=1)
D("feature-set-version-zero.json", layer="features",
  dataset_kind="trade_microstructure", feature_set_slug="tm", feature_set_version=0)
# kind vs layer
D("kind-not-in-layer.json", dataset_kind="trade_microstructure")
D("kind-canonical-only-in-raw.json", layer="raw", dataset_kind="footprint",
  derived_from=DROP, transform=DROP)
# identita'
D("record-schema-id-missing.json", record_schema_id=DROP)
D("record-schema-id-empty.json", record_schema_id="")
# path
D("rel-root-absolute.json", rel_root="/srv/marketdata/canonical")
D("rel-root-traversal.json", rel_root="canonical/../../etc")
D("rel-root-empty-component.json", rel_root="canonical//trades")
D("rel-root-encoded-dot.json", rel_root="canonical/trades/bybit/%2E%2E/trade-v1")
D("rel-root-lowercase-escape.json", rel_root="canonical/trades/bybit/BTC%2fUSD/trade-v1")
D("rel-root-dangling-percent.json", rel_root="canonical/trades/bybit/BTC%/trade-v1")
D("rel-root-short-escape.json", rel_root="canonical/trades/bybit/BTC%2/trade-v1")
# identificatori canonici: niente maiuscole, separatori o spazi
D("record-schema-id-uppercase.json", record_schema_id="Trade-V1")
D("record-schema-id-with-slash.json", record_schema_id="trade/v1")
D("record-schema-id-double-separator.json", record_schema_id="trade..v1")
D("record-schema-id-trailing-separator.json", record_schema_id="trade-v1-")
D("feature-set-slug-uppercase.json", layer="features",
  dataset_kind="trade_microstructure", feature_set_slug="TM",
  feature_set_version=1)
# forma
D("schema-version-wrong.json", schema_version="dataset-manifest-v2")
D("created-at-no-timezone.json", created_at="2026-08-01T00:05:00")
# formalmente conformi al pattern, ma date inesistenti: le coglie solo il
# FormatChecker, non il regex
D("created-at-month-13.json", created_at="2026-13-01T00:05:00Z")
D("created-at-day-32.json", created_at="2026-08-32T00:05:00Z")
D("created-at-hour-25.json", created_at="2026-08-01T25:05:00Z")
D("created-at-feb-30.json", created_at="2027-02-30T00:05:00Z")
D("unknown-field.json", dataset_id="7f3a")
D("strategic-field-pnl.json", pnl="1240.55")

# ===================================================== partition-manifest
pv = fresh(ROOT / "fixtures" / "partition-manifest-v1")

SEALED = {
    "schema_version": "partition-manifest-v1", **RAW_TRADES,
    "partition_key": "dt=2026-08-25", "revision": 1, "state": "valid",
    "rel_path": "dt=2026-08-25/part-000.parquet",
    "file_size_bytes": 48211934, "row_count": 1842776,
    "sha256": "9f2c1b7a4e0d63558a1cf90b2e77d4a6cc38b915de204671f8ab3c5d9e0172b4",
    "first_exchange_ts": "2026-08-25T00:00:00.014287Z",
    "last_exchange_ts": "2026-08-25T23:59:59.982104Z",
    "first_sequence": "9007199254740993",
    "last_sequence": "9007199256583769",
    "created_at": "2026-08-25T00:00:00.001Z",
    "closed_at": "2026-08-26T00:00:04.220Z",
    "producer": "bybit-collector", "code_ref": "4f1a9c3",
}
emit(pv, "valid-sealed-raw.json", SEALED)
emit(pv, "valid-writing.json", SEALED, state="writing", sha256=None,
     closed_at=None, first_exchange_ts=None, last_exchange_ts=None,
     first_sequence=None, last_sequence=None, row_count=0, file_size_bytes=0,
     partition_key="dt=2026-08-26", rel_path="dt=2026-08-26/part-000.parquet")
emit(pv, "valid-writing-optionals-absent.json", SEALED, state="writing",
     sha256=DROP, closed_at=DROP, first_sequence=DROP, last_sequence=DROP,
     first_exchange_ts=None, last_exchange_ts=None, row_count=0,
     file_size_bytes=0, dataset_kind="l2", record_schema_id="l2-v1",
     venue="kraken", instrument="XBT/USD",
     partition_key="dt=2026-08-26/hour=14",
     rel_path="dt=2026-08-26/hour=14/part-000.parquet")
emit(pv, "valid-empty-sealed.json", SEALED, row_count=0, file_size_bytes=812,
     first_exchange_ts=None, last_exchange_ts=None,
     first_sequence=None, last_sequence=None,
     sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     partition_key="dt=2026-01-01", rel_path="dt=2026-01-01/part-000.parquet")
emit(pv, "valid-canonical-no-sequences.json", SEALED, layer="canonical",
     dataset_kind="footprint", record_schema_id="footprint-v1",
     state="degraded", first_sequence=None, last_sequence=None,
     producer="footprint-builder", code_ref="c41ee02")
emit(pv, "valid-features-superseded-rev2.json", SEALED,
     layer="features", dataset_kind="trade_microstructure",
     feature_set_slug="trade_microstructure", feature_set_version=1,
     record_schema_id="feature-set-v1", revision=2, state="superseded",
     first_sequence="0", last_sequence="86399",
     producer="trade-microstructure-builder", code_ref="d92aa10")

pi = fresh(pv / "invalid")
P = lambda name, **kw: emit(pi, name, SEALED, **kw)

P("venue-null.json", venue=None)
P("venue-missing.json", venue=DROP)
P("sealed-without-sha256.json", sha256=DROP)
P("sealed-sha256-null.json", sha256=None)
P("sealed-without-closed-at.json", closed_at=DROP)
P("sealed-closed-at-null.json", closed_at=None)
P("sha256-uppercase.json",
  sha256="9F2C1B7A4E0D63558A1CF90B2E77D4A6CC38B915DE204671F8AB3C5D9E0172B4")
P("sha256-too-short.json", sha256="9f2c1b7a")
P("rows-without-first-ts.json", first_exchange_ts=None)
P("rows-without-last-ts.json", last_exchange_ts=None)
P("timestamp-no-timezone.json", first_exchange_ts="2026-08-25T00:00:00.014287")
P("timestamp-offset.json", last_exchange_ts="2026-08-25T23:59:59+02:00")
# date impossibili: pattern superato, FormatChecker no
P("timestamp-month-13.json", first_exchange_ts="2026-13-25T00:00:00Z")
P("timestamp-day-32.json", last_exchange_ts="2026-08-32T00:00:00Z")
P("timestamp-minute-61.json", first_exchange_ts="2026-08-25T00:61:00Z")
P("closed-at-feb-30.json", closed_at="2027-02-30T00:00:00Z")
P("created-at-impossible.json", created_at="2026-00-00T00:00:00Z")
P("sequence-numeric.json", first_sequence=9007199254740993)
P("sequence-leading-zeros.json", first_sequence="007")
P("sequence-only-first.json", last_sequence=None)
P("sequence-only-last.json", first_sequence=None)
P("sequence-negative.json", first_sequence="-5")
P("row-count-negative.json", row_count=-1)
P("row-count-not-integer.json", row_count="1842776")
P("file-size-negative.json", file_size_bytes=-1)
P("file-size-float.json", file_size_bytes=1.5)
P("revision-zero.json", revision=0)
P("revision-string.json", revision="1")
P("state-unknown.json", state="active")
P("rel-path-absolute.json", rel_path="/srv/marketdata/raw/x.parquet")
P("rel-path-traversal.json", rel_path="../../etc/shadow")
P("rel-path-empty-component.json", rel_path="dt=2026-08-25//part.parquet")
P("rel-path-encoded-dot.json", rel_path="%2E%2E/part-000.parquet")
P("rel-path-lowercase-escape.json", rel_path="dt=2026-08-25/part%2fx.parquet")
P("rel-path-dangling-percent.json", rel_path="dt=2026-08-25/part%.parquet")
P("record-schema-id-uppercase.json", record_schema_id="Trade-V1")
P("record-schema-id-with-slash.json", record_schema_id="trade/v1")
P("partition-key-bare-date.json", partition_key="2026-08-25")
P("partition-key-absolute.json", partition_key="/dt=2026-08-25")
P("features-without-feature-set.json", layer="features",
  dataset_kind="trade_microstructure")
P("non-features-with-feature-set.json", feature_set_slug="tm", feature_set_version=1)
P("kind-not-in-layer.json", dataset_kind="footprint")
P("record-schema-id-missing.json", record_schema_id=DROP)
P("producer-blank.json", producer="  ")
P("code-ref-missing.json", code_ref=DROP)
P("schema-version-wrong.json", schema_version="partition-manifest-v2")
P("unknown-field.json", file_name="part-000.parquet")
P("strategic-field-delta.json", delta="12.5")

# ====================================================== coverage-manifest
# La copertura DICHIARATA non deriva ne' dagli event bounds osservati ne' dal
# partition_key: e' un'asserzione con una base di acquisizione, una semantica
# sorgente versionata, una mappatura versionata e delle evidenze.
cv = fresh(ROOT / "fixtures" / "coverage-manifest-v1")

DAY = "2024-01-15"
NEXT_DAY = "2024-01-16"


def part(key, revision=1):
    return {"partition_key": key, "revision": revision}


def ev(kind, detail):
    return {"kind": kind, "detail": detail}


def assertion(assertion_id, start, end, status, partitions, evidence):
    return {"assertion_id": assertion_id, "start": start, "end": end,
            "status": status, "partitions": partitions, "evidence": evidence}


# CASO 1 e CASO 7: estrazione SQL deterministica dallo SQLite Bybit su una
# giornata UTC intera. Prova la lettura completa della sorgente LOCALE, non che
# la sorgente locale contenga tutto quello che la venue ha pubblicato.
SQLITE_EXTRACT = {
    "schema_version": "coverage-manifest-v1", **CANON_TRADES,
    "coverage_id": "bybit-btcusdt-sqlite-extract-2024-01-15",
    "supersedes": None,
    "created_at": "2024-03-01T09:12:00Z",
    "acquisition": {
        "basis": "source_extract",
        "intent_start": DAY + "T00:00:00Z",
        "intent_end": NEXT_DAY + "T00:00:00Z",
        "source_semantics": "bybit-public-trades-sqlite-v1",
        "mapping": "bybit-sqlite-day-extract-v1",
    },
    "assertions": [
        assertion("extract-full-day", DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z", "complete",
                  [part("dt=" + DAY)],
                  [ev("deterministic_source_extract",
                      "SELECT ... WHERE ts >= 1705276800000 AND ts < 1705363200000 "
                      "ORDER BY ts, trade_id; 1842776 righe sorgente = 1842776 righe canoniche")]),
    ],
    "producer": "bybit-sqlite-importer",
    "code_ref": "4f1a9c3",
}
emit(cv, "valid-source-extract-complete-day.json", SQLITE_EXTRACT)

# CASO 2: copertura COMPLETA con ZERO eventi. La partizione esiste, ha
# row_count 0 e nessun event bound: la copertura resta completa.
emit(cv, "valid-zero-events-complete.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-quiet-hour-2024-01-15t12",
     acquisition={"basis": "source_archive",
                  "intent_start": DAY + "T12:00:00Z",
                  "intent_end": DAY + "T13:00:00Z",
                  "source_semantics": "bybit-public-trades-archive-v1",
                  "mapping": "bybit-archive-hour-v1"},
     assertions=[
         assertion("quiet-hour", DAY + "T12:00:00Z", DAY + "T13:00:00Z", "complete",
                   [part("dt=" + DAY + "/hour=12")],
                   [ev("archive_completeness",
                       "archivio orario scaricato e verificato integro; 0 trade nell'ora")]),
     ],
     producer="bybit-archive-importer")

# CASO 4: sessione live interrotta. Il buco esiste indipendentemente dal fatto
# che dei trade fossero attesi in quei quattro secondi.
emit(cv, "valid-live-session-interrupted.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-live-2024-01-15-seg-1",
     acquisition={"basis": "live_stream",
                  "intent_start": DAY + "T00:00:00Z",
                  "intent_end": NEXT_DAY + "T00:00:00Z",
                  "source_semantics": "bybit-public-trade-ws-v1",
                  "mapping": "bybit-ws-session-continuity-v1"},
     assertions=[
         assertion("live-seg-before-drop", DAY + "T00:00:00Z", DAY + "T12:00:03Z", "complete",
                   [part("dt=" + DAY)],
                   [ev("connection_continuity",
                       "sottoscrizione stabilita 00:00:00.000Z, nessun reconnect fino a 12:00:03Z")]),
         assertion("live-seg-drop", DAY + "T12:00:03Z", DAY + "T12:00:07Z", "known_gap", [],
                   [ev("transport_interruption",
                       "socket chiuso 12:00:03.114Z, riconnesso 12:00:06.902Z; nessun replay disponibile")]),
         assertion("live-seg-after-drop", DAY + "T12:00:07Z", NEXT_DAY + "T00:00:00Z", "complete",
                   [part("dt=" + DAY)],
                   [ev("connection_continuity",
                       "sottoscrizione ristabilita 12:00:06.902Z, nessun reconnect fino a fine giornata")]),
     ],
     producer="bybit-collector")

# CASO 5: discontinuita' di sequence. Evidenza FORTE di eventi sorgente persi,
# distinta dal semplice silenzio di eventi.
emit(cv, "valid-sequence-discontinuity.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-live-2024-01-15-seq-break",
     acquisition={"basis": "live_stream",
                  "intent_start": DAY + "T00:00:00Z",
                  "intent_end": NEXT_DAY + "T00:00:00Z",
                  "source_semantics": "bybit-public-trade-ws-v1",
                  "mapping": "bybit-ws-sequence-continuity-v1"},
     assertions=[
         assertion("seq-before-break", DAY + "T00:00:00Z", DAY + "T08:00:00Z", "complete",
                   [part("dt=" + DAY)],
                   [ev("sequence_continuity", "sequence 1..4210338 contigua, nessun salto")]),
         assertion("seq-break", DAY + "T08:00:00Z", DAY + "T08:00:01Z", "known_gap", [],
                   [ev("sequence_discontinuity",
                       "ricevute 100, 101, 105: mancano 102, 103, 104")]),
         assertion("seq-after-break-uncertain", DAY + "T08:00:01Z", NEXT_DAY + "T00:00:00Z", "uncertain", [],
                   [ev("connection_continuity",
                       "connessione mantenuta, ma dopo il salto la continuita' della sequence non e' piu' dimostrata")]),
     ],
     producer="bybit-collector")

# CASO 6: buco riparato da un backfill che restituisce ZERO eventi. Il
# documento SUPERSEDE quello live e RISTABILISCE per intero cio' che resta
# vero: la copertura diventa completa senza aggiungere un solo record.
emit(cv, "valid-backfill-repairs-gap.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-backfill-2024-01-15-repair",
     supersedes="bybit-btcusdt-live-2024-01-15-seg-1",
     created_at="2024-01-16T04:30:00Z",
     acquisition={"basis": "backfill",
                  "intent_start": DAY + "T00:00:00Z",
                  "intent_end": NEXT_DAY + "T00:00:00Z",
                  "source_semantics": "bybit-public-trades-archive-v1",
                  "mapping": "bybit-archive-interval-v1"},
     assertions=[
         assertion("repaired-full-day", DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z", "complete",
                   [part("dt=" + DAY, revision=2)],
                   [ev("connection_continuity",
                       "sessione live originaria, salvo [12:00:03Z, 12:00:07Z)"),
                    ev("archive_completeness",
                       "archivio autorevole della giornata scaricato e verificato integro"),
                    ev("reconciliation",
                       "backfill di [12:00:03Z, 12:00:07Z) completato: 0 trade pubblicati in quell'intervallo")]),
     ],
     producer="bybit-backfill")

# Buco NOTO senza alcuna partizione: il collector era fermo, non e' stato
# materializzato nulla. Rappresentabile proprio perche' la copertura non e'
# appesa a un file.
emit(cv, "valid-known-gap-without-partition.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-outage-2024-01-14",
     acquisition={"basis": "source_session",
                  "intent_start": "2024-01-14T00:00:00Z",
                  "intent_end": DAY + "T00:00:00Z",
                  "source_semantics": "bybit-public-trade-ws-v1",
                  "mapping": "bybit-ws-session-continuity-v1"},
     assertions=[
         assertion("outage", "2024-01-14T00:00:00Z", DAY + "T00:00:00Z", "known_gap", [],
                   [ev("transport_interruption",
                       "host del collector spento per manutenzione; nessuna partizione materializzata")]),
     ],
     producer="bybit-collector")

# Paginazione API finita, chiave di partizione ORARIA: la grammatica di
# partition_key non implica una giornata UTC.
emit(cv, "valid-api-pagination-hourly-key.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-api-2024-01-15t14",
     acquisition={"basis": "api_request",
                  "intent_start": DAY + "T14:00:00Z",
                  "intent_end": DAY + "T15:00:00Z",
                  "source_semantics": "bybit-v5-market-recent-trade-v1",
                  "mapping": "bybit-rest-pagination-v1"},
     assertions=[
         assertion("hour-14", DAY + "T14:00:00Z", DAY + "T15:00:00Z", "complete",
                   [part("dt=" + DAY + "/hour=14")],
                   [ev("pagination_complete",
                       "cursore esaurito: l'ultima pagina ha restituito nextPageCursor vuoto")]),
     ],
     producer="bybit-rest-importer")

# Layer features: l'identita' del feature set fa parte dell'identita' naturale
# anche qui.
emit(cv, "valid-features-layer.json", SQLITE_EXTRACT,
     layer="features", dataset_kind="trade_microstructure",
     feature_set_slug="trade_microstructure", feature_set_version=1,
     record_schema_id="feature-set-v1",
     coverage_id="bybit-btcusdt-tm-2024-01-15",
     acquisition={"basis": "reconciliation",
                  "intent_start": DAY + "T00:00:00Z",
                  "intent_end": NEXT_DAY + "T00:00:00Z",
                  "source_semantics": "canonical-trades-upstream-v1",
                  "mapping": "derive-from-upstream-coverage-v1"},
     assertions=[
         assertion("tm-full-day", DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z", "complete",
                   [part("dt=" + DAY)],
                   [ev("reconciliation",
                       "copertura ereditata dal dataset canonical padre, gia' dichiarata completa")]),
     ],
     producer="trade-microstructure-builder", code_ref="d92aa10")

# copertura dichiarata al limite di precisione ammesso: 6 cifre frazionarie
# (microsecondo), esattamente cio' che catalog.partitions.ts_start/ts_end
# (PostgreSQL timestamptz) puo' rappresentare senza troncare. Non basta un
# confine a zero decimali per dimostrare che il limite e' 6 e non 9: serve un
# valore che userebbe davvero la settima cifra se fosse ammessa.
emit(cv, "valid-microsecond-boundary.json", SQLITE_EXTRACT,
     coverage_id="bybit-btcusdt-microsecond-boundary",
     acquisition={"basis": "source_archive",
                  "intent_start": DAY + "T12:00:00.100000Z",
                  "intent_end": DAY + "T12:00:00.900000Z",
                  "source_semantics": "bybit-public-trades-archive-v1",
                  "mapping": "bybit-archive-hour-v1"},
     assertions=[
         assertion("microsecond-window",
                   DAY + "T12:00:00.100000Z", DAY + "T12:00:00.900000Z",
                   "complete", [part("dt=" + DAY + "/hour=12")],
                   [ev("archive_completeness",
                       "finestra di prova al limite di precisione ammesso")]),
     ],
     producer="bybit-archive-importer")

ci = fresh(cv / "invalid")
C = lambda name, **kw: emit(ci, name, SQLITE_EXTRACT, **kw)

ONE = SQLITE_EXTRACT["assertions"][0]
ACQ = SQLITE_EXTRACT["acquisition"]

C("schema-version-wrong.json", schema_version="coverage-manifest-v2")
C("unknown-field.json", ts_start=DAY + "T00:00:00Z")
C("strategic-field-pnl.json", pnl="1240.55")
C("coverage-id-uppercase.json", coverage_id="Bybit-Extract")
C("coverage-id-missing.json", coverage_id=DROP)
# 'supersedes' e' OBBLIGATORIO anche quando e' null: assente significherebbe
# "non ci ho pensato", null significa "non supersede nulla".
C("supersedes-missing.json", supersedes=DROP)
C("supersedes-uppercase.json", supersedes="Bybit-Live-1")
C("assertions-empty.json", assertions=[])
C("assertions-missing.json", assertions=DROP)
C("acquisition-missing.json", acquisition=DROP)
C("acquisition-unknown-field.json", acquisition=dict(ACQ, requested_by="ops"))
C("basis-unknown.json", acquisition=dict(ACQ, basis="download"))
# la semantica sorgente e la mappatura DEVONO essere versionate
C("source-semantics-unversioned.json",
  acquisition=dict(ACQ, source_semantics="bybit-public-trades-sqlite"))
C("mapping-unversioned.json", acquisition=dict(ACQ, mapping="bybit-sqlite-day-extract"))
C("mapping-version-zero.json", acquisition=dict(ACQ, mapping="bybit-sqlite-day-extract-v0"))
C("intent-no-timezone.json", acquisition=dict(ACQ, intent_start=DAY + "T00:00:00"))
C("intent-offset.json", acquisition=dict(ACQ, intent_end="2024-01-16T00:00:00+01:00"))
C("intent-feb-30.json", acquisition=dict(ACQ, intent_end="2024-02-30T00:00:00Z"))
C("assertion-status-unknown.json", assertions=[dict(ONE, status="partial")])
C("assertion-unknown-field.json", assertions=[dict(ONE, row_count=0)])
C("assertion-partitions-missing.json",
  assertions=[{k: v for k, v in ONE.items() if k != "partitions"}])
C("assertion-ts-month-13.json", assertions=[dict(ONE, start="2024-13-15T00:00:00Z")])
C("assertion-ts-no-timezone.json", assertions=[dict(ONE, end="2024-01-16T00:00:00")])
# B3: la copertura dichiarata e' capped a MICROSECONDI (6 cifre); il timestamp
# di evento resta nanosecond-capable altrove, ma non qui.
C("assertion-start-seven-fractional-digits.json",
  assertions=[dict(ONE, start="2024-01-15T00:00:00.1234567Z")])
C("assertion-end-nine-fractional-digits.json",
  assertions=[dict(ONE, end="2024-01-16T00:00:00.123456789Z")])
C("intent-start-seven-fractional-digits.json",
  acquisition=dict(ACQ, intent_start=DAY + "T00:00:00.1234567Z"))
# I1: assertion_id e' obbligatoria, unica identita' stabile dell'asserzione;
# la posizione nell'array non lo e'.
C("assertion-id-missing.json",
  assertions=[{k: v for k, v in ONE.items() if k != "assertion_id"}])
C("assertion-id-uppercase.json", assertions=[dict(ONE, assertion_id="Extract-Full-Day")])
# una copertura completa deve essere materializzata in ESATTAMENTE una partizione
C("complete-without-partition.json", assertions=[dict(ONE, partitions=[])])
C("complete-two-partitions.json",
  assertions=[dict(ONE, partitions=[part("dt=" + DAY), part("dt=" + NEXT_DAY)])])
C("evidence-empty.json", assertions=[dict(ONE, evidence=[])])
C("evidence-kind-unknown.json",
  assertions=[dict(ONE, evidence=[ev("looked_fine", "sembrava a posto")])])
C("evidence-detail-blank.json",
  assertions=[dict(ONE, evidence=[ev("archive_completeness", "   ")])])
C("evidence-unknown-field.json",
  assertions=[dict(ONE, evidence=[dict(ev("archive_completeness", "ok"), rows=10)])])
C("partition-key-bare-date.json", assertions=[dict(ONE, partitions=[part(DAY)])])
C("partition-revision-zero.json",
  assertions=[dict(ONE, partitions=[part("dt=" + DAY, revision=0)])])
C("partition-ref-unknown-field.json",
  assertions=[dict(ONE, partitions=[dict(part("dt=" + DAY), rel_path="x.parquet")])])
C("venue-uppercase.json", venue="Bybit")
C("venue-null.json", venue=None)
C("instrument-blank.json", instrument="   ")
C("producer-blank.json", producer="  ")
C("code-ref-missing.json", code_ref=DROP)
C("features-without-feature-set.json", layer="features",
  dataset_kind="trade_microstructure")
C("non-features-with-feature-set.json", feature_set_slug="tm", feature_set_version=1)
C("kind-not-in-layer.json", dataset_kind="trade_microstructure")

print(f"dataset-manifest : {len(list(dv.glob('valid-*.json')))} valide, "
      f"{len(list(di.glob('*.json')))} negative")
print(f"partition-manifest: {len(list(pv.glob('valid-*.json')))} valide, "
      f"{len(list(pi.glob('*.json')))} negative")
print(f"coverage-manifest : {len(list(cv.glob('valid-*.json')))} valide, "
      f"{len(list(ci.glob('*.json')))} negative")
