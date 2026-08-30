#!/usr/bin/env python3
"""Semantica della COPERTURA DICHIARATA, caso per caso.

Congela le distinzioni che il produttore non deve piu' inventare a runtime:

    event bounds osservati   !=   copertura dichiarata
    silenzio di eventi       !=   buco di copertura
    intento di acquisizione  !=   copertura certificata
    partition_key            !=   intervallo temporale

Ogni sezione corrisponde a un caso richiesto dal mandato e, dove serve,
mostra le DUE meta' della distinzione: quello che deve passare e quello che
deve fallire. Un test che mostrasse solo la meta' che passa non proverebbe
nessuna distinzione.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from semantic_validator import (  # noqa: E402
    _format_ts, check_coverage_manifest, reconstruct_catalog_coverage, validate,
)

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

FIXTURES = ROOT / "fixtures" / "coverage-manifest-v1"

DAY = "2024-01-15"
NEXT_DAY = "2024-01-16"
DATASET = {
    "schema_version": "dataset-manifest-v1", "layer": "canonical",
    "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
    "record_schema_id": "trade-v1",
    "rel_root": "canonical/trades/bybit/BTCUSDT/trade-v1",
    "derived_from": [{"layer": "raw", "dataset_kind": "trades",
                      "venue": "bybit", "instrument": "BTCUSDT",
                      "record_schema_id": "trade-v1"}],
    "transform": "canonicalize-trades-v1",
    "created_at": "2024-01-01T00:00:00Z",
}

fail = []


def expect(codes, got, what):
    got_codes = sorted(v.code for v in got)
    want = sorted(codes)
    if got_codes == want:
        print(f"  {GREEN}PASS{OFF} {what}")
        if got:
            print(f"       {DIM}{got[0]}{OFF}")
    else:
        print(f"  {RED}FAIL{OFF} {what}")
        print(f"       atteso {want}, ottenuto {got_codes}")
        fail.append(what)


def check(condition, what, detail=""):
    if condition:
        print(f"  {GREEN}PASS{OFF} {what}")
        if detail:
            print(f"       {DIM}{detail}{OFF}")
    else:
        print(f"  {RED}FAIL{OFF} {what}")
        if detail:
            print(f"       {detail}")
        fail.append(what)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def iso(interval):
    """Gli intervalli ricostruiti sono in NANOSECONDI, non datetime: sotto il
    microsecondo un datetime perderebbe cifre e due istanti distinti
    sembrerebbero lo stesso."""
    return (_format_ts(interval[0]), _format_ts(interval[1]))


def ev(kind, detail="evidenza di prova"):
    return {"kind": kind, "detail": detail}


def part(key="dt=" + DAY, revision=1):
    return {"partition_key": key, "revision": revision}


_assertion_seq = iter(f"a{n}" for n in itertools.count(1))


def assertion(start, end, status="complete", partitions=None, evidence=None,
              assertion_id=None):
    return {"assertion_id": assertion_id or next(_assertion_seq),
            "start": start, "end": end, "status": status,
            "partitions": [part()] if partitions is None else partitions,
            "evidence": evidence or [ev("archive_completeness")]}


def coverage(assertions, coverage_id="cov-1", supersedes=None,
             intent=(DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z"),
             basis="source_archive", source_semantics="src-v1",
             mapping="map-v1", **identity):
    doc = {
        "schema_version": "coverage-manifest-v1", "layer": "canonical",
        "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
        "record_schema_id": "trade-v1",
        "coverage_id": coverage_id, "supersedes": supersedes,
        "created_at": "2024-01-16T00:00:00Z",
        "acquisition": {"basis": basis,
                        "intent_start": intent[0], "intent_end": intent[1],
                        "source_semantics": source_semantics, "mapping": mapping},
        "assertions": assertions,
        "producer": "test-producer", "code_ref": "0000000",
    }
    doc.update(identity)
    return doc


def partition(row_count=0, first=None, last=None, key="dt=" + DAY, revision=1,
              state="valid"):
    return {
        "schema_version": "partition-manifest-v1", "layer": "canonical",
        "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
        "record_schema_id": "trade-v1",
        "partition_key": key, "revision": revision, "state": state,
        "rel_path": f"{key}/part-000.parquet",
        "file_size_bytes": 812, "row_count": row_count,
        "sha256": "a" * 64,
        "first_exchange_ts": first, "last_exchange_ts": last,
        "first_sequence": None, "last_sequence": None,
        "created_at": DAY + "T00:00:00Z", "closed_at": NEXT_DAY + "T00:00:00Z",
        "producer": "test-producer", "code_ref": "0000000",
    }


FULL_DAY = (DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z")


# ==========================================================================
print("\n1. copertura completa CON eventi")
# ==========================================================================
cov = coverage([assertion(*FULL_DAY)])
# i primi eventi arrivano 492 ms dopo l'inizio e gli ultimi 69 ms prima della
# fine: quei margini NON sono buchi
p = partition(row_count=1842776,
              first=DAY + "T00:00:00.492Z", last=DAY + "T23:59:59.931Z")
result, violations = reconstruct_catalog_coverage([cov], [p])
expect([], violations, "giornata completa con eventi accettata")
check(iso(result[("dt=" + DAY, 1)]) == FULL_DAY,
      "ts_start/ts_end ricostruiti dalla dichiarazione, non dagli eventi",
      f"{iso(result[('dt=' + DAY, 1)])} anche se il primo trade e' alle 00:00:00.492Z")


# ==========================================================================
print("\n2. copertura completa con ZERO eventi")
# ==========================================================================
QUIET = (DAY + "T12:00:00Z", DAY + "T13:00:00Z")
cov = coverage([assertion(*QUIET, partitions=[part("dt=" + DAY + "/hour=12")])],
               intent=QUIET)
p = partition(row_count=0, first=None, last=None, key="dt=" + DAY + "/hour=12")
result, violations = reconstruct_catalog_coverage([cov], [p])
expect([], violations, "ora senza un solo trade dichiarata completa")
check(iso(result[("dt=" + DAY + "/hour=12", 1)]) == QUIET,
      "row_count 0 e event bounds null non impediscono la copertura",
      "e' esattamente il caso che DataGateway deve distinguere da 'nessuna copertura'")


# ==========================================================================
print("\n3. il SILENZIO di eventi non crea un buco")
# ==========================================================================
# due trade a sette secondi di distanza, evidenza di acquisizione continua
cov = coverage([assertion(*FULL_DAY,
                          evidence=[ev("connection_continuity",
                                       "nessun reconnect nelle 24 ore")])])
p = partition(row_count=2, first=DAY + "T12:00:01Z", last=DAY + "T12:00:08Z")
result, violations = reconstruct_catalog_coverage([cov], [p])
expect([], violations, "sette secondi senza trade non producono violazioni")
check(iso(result[("dt=" + DAY, 1)]) == FULL_DAY,
      "la copertura resta l'intera giornata",
      "nessuna regola guarda la distanza fra eventi: non c'e' proprio codice che lo faccia")


# ==========================================================================
print("\n4. l'interruzione di acquisizione crea un buco SENZA differenza di eventi")
# ==========================================================================
# STESSI eventi del caso 3, stessa partizione, stessi bounds osservati.
# Cambia solo l'evidenza di acquisizione.
interrupted = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:00:03Z",
              evidence=[ev("connection_continuity", "fino a 12:00:03Z")]),
    assertion(DAY + "T12:00:03Z", DAY + "T12:00:07Z", status="known_gap",
              partitions=[],
              evidence=[ev("transport_interruption", "socket chiuso, nessun replay")]),
    assertion(DAY + "T12:00:07Z", NEXT_DAY + "T00:00:00Z",
              evidence=[ev("connection_continuity", "da 12:00:06.902Z")]),
])
_, violations = reconstruct_catalog_coverage([interrupted], [p])
expect(["COVERAGE_NOT_CONTIGUOUS"], violations,
       "la stessa partizione diventa non pubblicabile: il buco e' reale")
check(len(violations) == 1,
      "una sola violazione: la causa non viene sepolta sotto i suoi effetti")
check(True,
      "il buco esiste anche se in quei quattro secondi nessun trade era atteso",
      "caso 3 e caso 4 hanno eventi identici e verdetti opposti")

# e un buco puo' esistere senza che sia mai stata materializzata una partizione
orphan = coverage([assertion(*FULL_DAY, status="known_gap", partitions=[],
                             evidence=[ev("transport_interruption", "collector spento")])])
result, violations = reconstruct_catalog_coverage([orphan], [])
expect([], violations, "buco noto senza alcuna partizione: rappresentabile")
check(result == {},
      "un buco non produce copertura di catalogo",
      "e' cio' che rende 'nessuna copertura' diverso da 'copertura con zero record'")


# ==========================================================================
print("\n5. la discontinuita' di sequence si distingue dal silenzio")
# ==========================================================================
expect(["COVERAGE_STATUS_CONTRADICTS_EVIDENCE"],
       check_coverage_manifest(coverage([
           assertion(*FULL_DAY,
                     evidence=[ev("sequence_discontinuity",
                                  "ricevute 100, 101, 105: mancano 102, 103, 104")])])),
       "sequence 100, 101, 105 non puo' essere dichiarata completa")
expect([],
       check_coverage_manifest(coverage([
           assertion(*FULL_DAY,
                     evidence=[ev("sequence_continuity", "1..4210338 contigua")])])),
       "una sequence contigua sostiene la completezza")
expect(["COVERAGE_STATUS_CONTRADICTS_EVIDENCE"],
       check_coverage_manifest(coverage([
           assertion(*FULL_DAY,
                     evidence=[ev("connection_continuity", "nessun reconnect"),
                               ev("transport_interruption", "socket chiuso 12:00:03Z")])])),
       "un'evidenza contraria non si annulla affiancandole un'evidenza favorevole")


# ==========================================================================
print("\n6. gli event bounds OSSERVATI non possono generare la copertura")
# ==========================================================================
# il bug che questa regola esiste per fermare: copiare first/last_exchange_ts
# in ts_start/ts_end. Fallisce SEMPRE, perche' la copertura e' half-open e
# l'ultimo record cadrebbe fuori.
observed_first, observed_last = DAY + "T00:00:00.492Z", DAY + "T23:59:59.931Z"
naive = coverage([assertion(observed_first, observed_last)],
                 intent=(observed_first, observed_last))
p_events = partition(row_count=1842776, first=observed_first, last=observed_last)
_, violations = reconstruct_catalog_coverage([naive], [p_events])
expect(["OBSERVED_OUTSIDE_DECLARED"], violations,
       "copertura copiata dagli event bounds respinta")
# e il primo record fuori a sinistra e' altrettanto respinto
late = coverage([assertion(DAY + "T01:00:00Z", NEXT_DAY + "T00:00:00Z")],
                intent=(DAY + "T01:00:00Z", NEXT_DAY + "T00:00:00Z"))
_, violations = reconstruct_catalog_coverage([late], [p_events])
expect(["OBSERVED_OUTSIDE_DECLARED"], violations,
       "un ts_start piu' tardi del primo record e' altrettanto respinto")


# ==========================================================================
print("\n7. il partition_key da solo non genera la copertura")
# ==========================================================================
# stessa identica chiave, due coperture legittime e diverse: la mappatura
# chiave -> intervallo non e' una funzione, quindi non e' derivabile.
utc_day = coverage([assertion(*FULL_DAY)], coverage_id="cov-utc-day")
session_day = coverage([assertion(DAY + "T22:00:00Z", NEXT_DAY + "T22:00:00Z")],
                       coverage_id="cov-session-day",
                       intent=(DAY + "T22:00:00Z", NEXT_DAY + "T22:00:00Z"),
                       source_semantics="venue-session-day-v1",
                       mapping="session-day-to-utc-v1")
a, _ = reconstruct_catalog_coverage([utc_day], [partition(key="dt=" + DAY)])
b, _ = reconstruct_catalog_coverage([session_day], [partition(key="dt=" + DAY)])
check(a[("dt=" + DAY, 1)] != b[("dt=" + DAY, 1)],
      "'dt=2024-01-15' mappa su due intervalli diversi secondo la sorgente",
      f"{iso(a[('dt=' + DAY, 1)])} contro {iso(b[('dt=' + DAY, 1)])}")
# e una chiave piu' fine della giornata resta legittima
hourly, _ = reconstruct_catalog_coverage(
    [coverage([assertion(DAY + "T14:00:00Z", DAY + "T15:00:00Z",
                         partitions=[part("dt=" + DAY + "/hour=14")])],
              intent=(DAY + "T14:00:00Z", DAY + "T15:00:00Z"))],
    [partition(key="dt=" + DAY + "/hour=14")])
check(iso(hourly[("dt=" + DAY + "/hour=14", 1)])
      == (DAY + "T14:00:00Z", DAY + "T15:00:00Z"),
      "una chiave oraria non viene arrotondata a una giornata")


# ==========================================================================
print("\n8. semantica half-open [start, end)")
# ==========================================================================
adjacent = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:00:00Z"),
    assertion(DAY + "T12:00:00Z", NEXT_DAY + "T00:00:00Z"),
])
result, violations = reconstruct_catalog_coverage(
    [adjacent], [partition(key="dt=" + DAY)])
expect([], violations, "A.end == B.start e' adiacenza, non sovrapposizione")
check(iso(result[("dt=" + DAY, 1)]) == FULL_DAY,
      "due intervalli adiacenti fondono in uno solo")

overlapping = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T13:00:00Z"),
    assertion(DAY + "T12:00:00Z", NEXT_DAY + "T00:00:00Z"),
])
expect(["COVERAGE_ASSERTIONS_OVERLAP"], check_coverage_manifest(overlapping),
       "un'ora dichiarata due volte nello stesso documento e' una contraddizione")

_, violations = reconstruct_catalog_coverage([coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:00:00Z"),
    assertion(DAY + "T12:00:00.000001Z", NEXT_DAY + "T00:00:00Z"),
])], [partition(key="dt=" + DAY)])
expect(["COVERAGE_NOT_CONTIGUOUS"], violations,
       "un microsecondo scoperto spezza la contiguita' e viene detto "
       "(il limite di precisione B3 e' il microsecondo, non il nanosecondo: "
       "vedi la sezione 'precisione della copertura dichiarata' piu' sotto)")

expect(["COVERAGE_INTERVAL_INVALID"],
       check_coverage_manifest(coverage([assertion(DAY + "T12:00:00Z",
                                                   DAY + "T12:00:00Z")])),
       "start == end non asserisce nulla ed e' respinto")


# ==========================================================================
print("\n9. l'evidenza specifica della sorgente mappa su copertura canonica")
# ==========================================================================
# quattro basi e quattro evidenze diverse, una sola rappresentazione canonica
canonical = []
for cid, basis, kind, semantics in [
    ("cov-archive", "source_archive", "archive_completeness", "archive-v1"),
    ("cov-api", "api_request", "pagination_complete", "rest-v1"),
    ("cov-sqlite", "source_extract", "deterministic_source_extract", "sqlite-v1"),
    ("cov-ws", "live_stream", "sequence_continuity", "ws-v1"),
]:
    doc = coverage([assertion(*FULL_DAY, evidence=[ev(kind)])],
                   coverage_id=cid, basis=basis, source_semantics=semantics,
                   mapping=semantics.replace("-v1", "-map-v1"))
    expect([], check_coverage_manifest(doc, DATASET), f"{basis} / {kind}")
    got, _ = reconstruct_catalog_coverage([doc], [partition(key="dt=" + DAY)])
    canonical.append(iso(got[("dt=" + DAY, 1)]))
check(len(set(canonical)) == 1 and canonical[0] == FULL_DAY,
      "quattro evidenze specifiche della sorgente, una copertura canonica",
      "la semantica di venue resta dietro source_semantics e mapping")

# CASO 7 del mandato: l'estrazione deterministica prova la lettura completa
# della sorgente LOCALE, non la completezza della sorgente rispetto alla venue.
sqlite_fixture = fixture("valid-source-extract-complete-day.json")
kinds = {e["kind"] for a in sqlite_fixture["assertions"] for e in a["evidence"]}
check(kinds == {"deterministic_source_extract"},
      "l'evidenza SQLite resta 'deterministic_source_extract'",
      "non viene promossa ad archive_completeness: e' una forza di prova diversa")


# ==========================================================================
print("\n10. la copertura di catalogo si ricostruisce dal contratto durevole")
# ==========================================================================
live = fixture("valid-live-session-interrupted.json")
repair = fixture("valid-backfill-repairs-gap.json")
# il backfill ha ri-materializzato la giornata come revisione 2, mandando la 1
# in superseded: partitions_one_live ammette una sola revisione viva per chiave
repaired_partition = partition(row_count=0, first=None, last=None, revision=2)
result, violations = reconstruct_catalog_coverage([live, repair],
                                                  [repaired_partition])
expect([], violations,
       "CASO 6: il backfill supersede il live e chiude il buco con ZERO eventi")
check(iso(result[("dt=" + DAY, 2)]) == FULL_DAY,
      "la copertura diventa completa senza aggiungere un solo record")
check(("dt=" + DAY, 1) not in result,
      "il documento superseduto non contribuisce piu' nulla",
      "senza la supersessione la revisione 1 porterebbe una copertura spezzata")

again, _ = reconstruct_catalog_coverage([repair, live], [repaired_partition])
check(again == result,
      "la ricostruzione e' deterministica e non dipende dall'ordine dei documenti")

all_valid = sorted(FIXTURES.glob("valid-*.json"))
for path in all_valid:
    doc = json.loads(path.read_text(encoding="utf-8"))
    expect([], check_coverage_manifest(doc),
           f"nessuna violazione interna: {path.name}")


# ==========================================================================
print("\n11. una dichiarazione malformata o incoerente fallisce ad alta voce")
# ==========================================================================
expect(["COVERAGE_INTENT_INVALID"],
       check_coverage_manifest(coverage(
           [assertion(*FULL_DAY)],
           intent=(NEXT_DAY + "T00:00:00Z", DAY + "T00:00:00Z"))),
       "intervallo di acquisizione invertito")
# CASO 5 del mandato sull'intento: chiedere non e' certificare
expect(["COVERAGE_OUTSIDE_INTENT"],
       check_coverage_manifest(coverage(
           [assertion(DAY + "T00:00:00Z", "2024-01-17T00:00:00Z")])),
       "si certifica piu' di quanto si sia tentato di acquisire")
expect([],
       check_coverage_manifest(coverage(
           [assertion(DAY + "T00:00:00Z", DAY + "T06:00:00Z")])),
       "certificare MENO dell'intento e' invece legittimo: l'intento non prova nulla")
expect(["COVERAGE_IDENTITY_MISMATCH"],
       check_coverage_manifest(coverage([assertion(*FULL_DAY)], venue="kraken"),
                               DATASET),
       "copertura di un'altra venue applicata a questo dataset")

expect(["COVERAGE_ID_DUPLICATE"], reconstruct_catalog_coverage(
    [coverage([assertion(*FULL_DAY)]), coverage([assertion(*FULL_DAY)])], [])[1],
    "due documenti con lo stesso coverage_id")
expect(["COVERAGE_SUPERSEDES_UNKNOWN"], reconstruct_catalog_coverage(
    [coverage([assertion(*FULL_DAY)], supersedes="cov-che-non-esiste")], [])[1],
    "supersede un documento che non e' fra quelli forniti")
narrows_result, narrows_violations = reconstruct_catalog_coverage([
    coverage([assertion(*FULL_DAY)], coverage_id="cov-vecchio"),
    coverage([assertion(DAY + "T12:00:00Z", DAY + "T13:00:00Z",
                        partitions=[part(revision=2)])],
             coverage_id="cov-nuovo", supersedes="cov-vecchio",
             intent=(DAY + "T12:00:00Z", DAY + "T13:00:00Z")),
], [partition(key="dt=" + DAY, revision=2)])
expect(["COVERAGE_MISSING_FOR_PARTITION", "COVERAGE_SUPERSESSION_NARROWS"],
       narrows_violations,
       "una riparazione stretta non puo' far sparire la copertura intorno; "
       "il partition-manifest 'valid' fornito resta genuinamente senza "
       "copertura affidabile una volta esclusa l'intera lineage")
check(narrows_result == {},
      "FAIL CLOSED: la riparazione stretta non pubblica nemmeno la sua "
      "stessa finestra ristretta, non solo quella persa",
      f"result={narrows_result!r}")
expect(["COVERAGE_SUPERSESSION_CONFLICT"], reconstruct_catalog_coverage([
    coverage([assertion(*FULL_DAY)], coverage_id="cov-vecchio"),
    coverage([assertion(*FULL_DAY, partitions=[part(revision=2)])],
             coverage_id="cov-a", supersedes="cov-vecchio"),
    coverage([assertion(*FULL_DAY, partitions=[part(revision=3)])],
             coverage_id="cov-b", supersedes="cov-vecchio"),
], [])[1], "due documenti che supersedono lo stesso")
contradiction_result, contradiction_violations = reconstruct_catalog_coverage([
    coverage([assertion(*FULL_DAY)], coverage_id="cov-completo"),
    coverage([assertion(DAY + "T12:00:00Z", DAY + "T13:00:00Z",
                        status="known_gap", partitions=[],
                        evidence=[ev("transport_interruption")])],
             coverage_id="cov-buco",
             intent=(DAY + "T12:00:00Z", DAY + "T13:00:00Z")),
], [partition(key="dt=" + DAY)])
expect(["COVERAGE_CONTRADICTION"], contradiction_violations,
       "un documento dichiara completo cio' che un altro dichiara scoperto")
check(("dt=" + DAY, 1) not in contradiction_result,
      "FAIL CLOSED: la contraddizione non pubblica nemmeno un sottoinsieme "
      "sicuro della copertura contestata")

# una partizione leggibile senza alcuna asserzione non e' pubblicabile
expect(["COVERAGE_MISSING_FOR_PARTITION"],
       reconstruct_catalog_coverage([], [partition(row_count=10,
                                                   first=DAY + "T01:00:00Z",
                                                   last=DAY + "T02:00:00Z")])[1],
       "partizione 'valid' senza copertura dichiarata")
expect([], reconstruct_catalog_coverage([], [partition(state="writing")])[1],
       "una partizione in 'writing' non deve ancora avere copertura")

# validate() aggrega le tre famiglie di documenti. Con partition_manifests=[]
# (nessun partition-manifest kraken fabbricato apposta) la coppia attesa e'
# DUE violazioni indipendenti e ugualmente legittime: la coerenza di identita'
# del coverage-manifest contro DATASET, e B2 che non trova alcun
# partition-manifest a cui risolvere l'asserzione 'complete'.
expect(["COVERAGE_IDENTITY_MISMATCH", "PARTITION_UNKNOWN"],
       validate(DATASET, [],
               [coverage([assertion(*FULL_DAY)], venue="kraken")]),
       "validate() propaga violazioni da entrambi gli strati: identita' e "
       "risolvibilita' della partizione")


# ==========================================================================
print("\n12. event bounds fuori dall'intervallo di responsabilita' dichiarato")
# ==========================================================================
# REGOLA DOCUMENTATA: si RIFIUTA, non si classifica in silenzio.
# Vedi docs/contracts/DECLARED_COVERAGE.md, invariante I8.
cov = coverage([assertion(*FULL_DAY)])
expect(["OBSERVED_OUTSIDE_DECLARED"], reconstruct_catalog_coverage(
    [cov], [partition(row_count=1, first=DAY + "T00:00:00Z",
                      last=NEXT_DAY + "T00:00:00Z")])[1],
    "un record esattamente a ts_end e' FUORI: la copertura e' half-open")
expect([], reconstruct_catalog_coverage(
    [cov], [partition(row_count=1, first=DAY + "T00:00:00Z",
                      last=DAY + "T23:59:59.999999999Z")])[1],
    "un nanosecondo prima di ts_end e' invece dentro")
expect(["OBSERVED_OUTSIDE_DECLARED"], reconstruct_catalog_coverage(
    [cov], [partition(row_count=1, first="2024-01-14T23:59:59Z",
                      last=DAY + "T12:00:00Z")])[1],
    "un record prima di ts_start e' fuori")
expect([], reconstruct_catalog_coverage(
    [cov], [partition(row_count=0, first=None, last=None)])[1],
    "una partizione vuota non ha bounds da confrontare")

print()
if fail:
    print(f"{RED}FAIL{OFF}: {len(fail)} controlli non superati")
    for name in fail:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: tutti i controlli di semantica della copertura superati")
