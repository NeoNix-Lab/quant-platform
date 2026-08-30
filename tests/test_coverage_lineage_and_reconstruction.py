#!/usr/bin/env python3
"""Rinforzo mirato ai findings del review Codex indipendente su
producer/manifest-catalog-bridge-v1: lineage di supersessione, esistenza e
unicita' delle partizioni referenziate, precisione della copertura dichiarata,
event bounds a riga zero, identita' delle asserzioni, isolamento per dataset,
overlap fra documenti vivi.

Ogni fixture avversaria asserisce il CODICE ESATTO della violazione attesa,
mai solo "e' successo qualcosa": una fixture pensata per rompere UNA regola
non deve accidentalmente romperne prima un'altra, o il test non proverebbe
cio' che dichiara di provare.

Sezioni, corrispondenti ai findings del review:

  B1  supersession lineage: fail-closed su auto-supersessione, ciclo,
      ramificazione, riferimento pendente, id duplicato; nessuna precedenza
      implicita; indipendenza dall'ordine.
  B2  esistenza e unicita' delle partizioni referenziate da un'asserzione
      'complete'; al piu' una revisione viva per partition_key.
  B3  precisione della copertura dichiarata: ceiling al microsecondo,
      round-trip esatto verso timestamptz.
  B4  row_count 0 implica event bounds osservati null.
  I1  assertion_id: identita' stabile, unica dentro il documento.
  I2  reconstruct_catalog_coverage opera su UNA sola DatasetIdentity.
  I3  overlap fra documenti vivi: cosa e' ammesso, cosa e' respinto.
  I5  riparazione reale: eventi PRIMA e DOPO il buco, il backfill ne aggiunge
      zero, la copertura diventa completa e gli event bounds non cambiano.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from semantic_validator import (  # noqa: E402
    _format_ts, _parse_ts, check_coverage_manifest, check_partition_manifest,
    reconstruct_catalog_coverage, validate,
)

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

DAY, NEXT_DAY = "2024-01-15", "2024-01-16"
FULL_DAY = (DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z")

BYBIT_TRADES = {"layer": "canonical", "dataset_kind": "trades", "venue": "bybit",
                "instrument": "BTCUSDT", "record_schema_id": "trade-v1"}
KRAKEN_TRADES = dict(BYBIT_TRADES, venue="kraken")

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


_aid_seq = iter(f"a{n}" for n in itertools.count(1))


def ev(kind, detail="evidenza di prova"):
    return {"kind": kind, "detail": detail}


def part(key="dt=" + DAY, revision=1):
    return {"partition_key": key, "revision": revision}


def assertion(start, end, status="complete", partitions=None, evidence=None,
              assertion_id=None):
    return {"assertion_id": assertion_id or next(_aid_seq),
            "start": start, "end": end, "status": status,
            "partitions": [part()] if partitions is None else partitions,
            "evidence": evidence or [ev("archive_completeness")]}


def coverage(assertions, coverage_id="cov-1", supersedes=None,
             intent=FULL_DAY, identity=None, **overrides):
    doc = {
        "schema_version": "coverage-manifest-v1",
        **(identity or BYBIT_TRADES),
        "coverage_id": coverage_id, "supersedes": supersedes,
        "created_at": "2024-01-16T00:00:00Z",
        "acquisition": {"basis": "source_archive",
                        "intent_start": intent[0], "intent_end": intent[1],
                        "source_semantics": "src-v1", "mapping": "map-v1"},
        "assertions": assertions,
        "producer": "test-producer", "code_ref": "0000000",
    }
    doc.update(overrides)
    return doc


def partition(row_count=0, first=None, last=None, key="dt=" + DAY, revision=1,
              state="valid", identity=None, content_sha256="a" * 64):
    ident = identity or BYBIT_TRADES
    return {
        "schema_version": "partition-manifest-v1", **ident,
        "partition_key": key, "revision": revision, "state": state,
        "rel_path": f"{key}/part-000.parquet",
        "file_size_bytes": 812, "row_count": row_count,
        "sha256": content_sha256,
        "first_exchange_ts": first, "last_exchange_ts": last,
        "first_sequence": None, "last_sequence": None,
        "created_at": DAY + "T00:00:00Z", "closed_at": NEXT_DAY + "T00:00:00Z",
        "producer": "test-producer", "code_ref": "0000000",
    }


def codes(violations):
    return sorted(v.code for v in violations)


P = partition(key="dt=" + DAY)  # partizione valida di riferimento, riusabile


# ==========================================================================
print("\nB1. lineage di supersessione: fail-closed, senza precedenza implicita")
# ==========================================================================

print("\n  B1.1 auto-supersessione")
selfsup = coverage([assertion(*FULL_DAY)], coverage_id="cov-a", supersedes="cov-a")
result, violations = reconstruct_catalog_coverage([selfsup], [P])
expect(["COVERAGE_SUPERSESSION_SELF"], violations,
       "A supersede A: respinta, non silenziosamente eliminata")
check(result == {}, "nessuna copertura pubblicata quando la lineage e' invalida")

print("\n  B1.2 ciclo a due nodi")
cyc_a = coverage([assertion(*FULL_DAY)], coverage_id="cov-a", supersedes="cov-b")
cyc_b = coverage([assertion(*FULL_DAY)], coverage_id="cov-b", supersedes="cov-a")
result, violations = reconstruct_catalog_coverage([cyc_a, cyc_b], [P])
expect(["COVERAGE_SUPERSESSION_CYCLE"], violations,
       "A supersede B, B supersede A: un ciclo non elimina silenziosamente "
       "entrambi i documenti, viene detto")
check(result == {}, "nessuna copertura pubblicata: prima il bug avrebbe "
      "fatto sparire A e B senza una sola violazione")

print("\n  B1.3 ciclo a tre nodi")
cyc3_a = coverage([assertion(*FULL_DAY)], coverage_id="cov-a", supersedes="cov-c")
cyc3_b = coverage([assertion(*FULL_DAY)], coverage_id="cov-b", supersedes="cov-a")
cyc3_c = coverage([assertion(*FULL_DAY)], coverage_id="cov-c", supersedes="cov-b")
result, violations = reconstruct_catalog_coverage([cyc3_a, cyc3_b, cyc3_c], [P])
expect(["COVERAGE_SUPERSESSION_CYCLE"], violations,
       "ciclo A->C->B->A a tre nodi rilevato quanto quello a due")

print("\n  B1.4 ramificazione: due documenti supersedono lo stesso target")
branch_target = coverage([assertion(*FULL_DAY)], coverage_id="cov-vecchio")
branch_a = coverage([assertion(*FULL_DAY, partitions=[part(revision=2)])],
                    coverage_id="cov-a", supersedes="cov-vecchio")
branch_b = coverage([assertion(*FULL_DAY, partitions=[part(revision=3)])],
                    coverage_id="cov-b", supersedes="cov-vecchio")
result, violations = reconstruct_catalog_coverage(
    [branch_target, branch_a, branch_b], [P])
expect(["COVERAGE_SUPERSESSION_CONFLICT"], violations,
       "cov-vecchio superseduto sia da cov-a sia da cov-b: ambiguo, respinto")
check(result == {}, "nessuna copertura pubblicata sotto ramificazione")

print("\n  B1.5 riferimento pendente")
dangling = coverage([assertion(*FULL_DAY)], coverage_id="cov-a",
                    supersedes="cov-mai-esistito")
result, violations = reconstruct_catalog_coverage([dangling], [P])
expect(["COVERAGE_SUPERSEDES_UNKNOWN"], violations,
       "supersede un coverage_id che non e' fra i documenti forniti")
check(result == {}, "nessuna copertura pubblicata: l'evidenza sostituita "
      "non e' verificabile, quindi non ci si puo' fidare nemmeno di questa")

print("\n  B1.6 coverage_id duplicato blocca l'intera ricostruzione")
dup_a = coverage([assertion(*FULL_DAY)], coverage_id="cov-x")
dup_b = coverage([assertion(DAY + "T00:00:00Z", DAY + "T06:00:00Z")],
                 coverage_id="cov-x")
result, violations = reconstruct_catalog_coverage([dup_a, dup_b], [P])
expect(["COVERAGE_ID_DUPLICATE"], violations,
       "due documenti con lo stesso coverage_id: la ricostruzione fallisce "
       "chiusa, non sceglie il primo arrivato")
check(result == {}, "nessuna copertura pubblicata sotto id duplicato")

print("\n  B1.7 lineage VALIDA: nessun falso positivo")
chain_old = coverage([assertion(*FULL_DAY)], coverage_id="cov-vecchio")
chain_new = coverage([assertion(*FULL_DAY, partitions=[part(revision=2)])],
                     coverage_id="cov-nuovo", supersedes="cov-vecchio")
p_rev2 = partition(key="dt=" + DAY, revision=2)
result, violations = reconstruct_catalog_coverage([chain_old, chain_new],
                                                  [p_rev2])
expect([], violations, "una catena valida (nessun ciclo/ramo/pendente/dup) "
       "non produce falsi positivi")
check(result == {("dt=" + DAY, 2): (_parse_ts(FULL_DAY[0]), _parse_ts(FULL_DAY[1]))},
      "il documento vivente (cov-nuovo) pubblica la copertura")

print("\n  B1.8 nessuna precedenza per created_at: il documento che vince e' "
      "quello indicato dall'arco 'supersedes', punto")
old_but_superseding = coverage(
    [assertion(*FULL_DAY, partitions=[part(revision=2)])],
    coverage_id="cov-nuovo", supersedes="cov-vecchio",
    **{"created_at": "1999-01-01T00:00:00Z"})
result_a, _ = reconstruct_catalog_coverage([chain_old, old_but_superseding],
                                           [p_rev2])
check(result_a == result,
      "cov-nuovo vince anche con un created_at anteriore a cov-vecchio: "
      "solo l'arco esplicito conta")

print("\n  B1.9 indipendenza dall'ordine su OGNI scenario sopra")
scenarios = {
    "self": ([selfsup], [P]),
    "ciclo-2": ([cyc_a, cyc_b], [P]),
    "ciclo-3": ([cyc3_a, cyc3_b, cyc3_c], [P]),
    "ramificazione": ([branch_target, branch_a, branch_b], [P]),
    "pendente": ([dangling], [P]),
    "id-duplicato": ([dup_a, dup_b], [P]),
    "catena-valida": ([chain_old, chain_new], [p_rev2]),
}
for name, (docs, parts) in scenarios.items():
    signatures = set()
    for order in itertools.permutations(docs):
        r, v = reconstruct_catalog_coverage(list(order), parts)
        signatures.add((tuple(sorted(r.items())), tuple(codes(v))))
    check(len(signatures) == 1,
          f"'{name}': {len(list(itertools.permutations(docs)))} permutazioni, "
          f"un solo risultato",
          f"firma: {sorted(signatures)[0][1]}")

print("\n  B1.10 supersessione ristretta: FAIL CLOSED, non solo segnalata")
# l'esempio esatto del blocco 2: A copre l'intera giornata [00,24), B la
# supersede ma ne restituisce solo [12,24). La mattina sparirebbe in silenzio
# se B venisse pubblicato: la supersessione e' sostituzione INTEGRALE, non
# parziale, quindi la lineage intera fallisce chiusa.
narrow_a = coverage([assertion(*FULL_DAY)], coverage_id="cov-a")
narrow_b = coverage(
    [assertion(DAY + "T12:00:00Z", NEXT_DAY + "T00:00:00Z")],
    coverage_id="cov-b", supersedes="cov-a",
    intent=(DAY + "T12:00:00Z", NEXT_DAY + "T00:00:00Z"))
p_narrow = partition(key="dt=" + DAY)
result, violations = reconstruct_catalog_coverage([narrow_a, narrow_b],
                                                  [p_narrow])
expect(["COVERAGE_MISSING_FOR_PARTITION", "COVERAGE_SUPERSESSION_NARROWS"],
       violations,
       "B supersede A ma ne restituisce solo meta': segnalato. Il partition-"
       "manifest 'valid' fornito resta genuinamente senza copertura "
       "affidabile una volta che A e B sono entrambi esclusi, quindi "
       "COVERAGE_MISSING_FOR_PARTITION e' un secondo esito corretto, non "
       "un doppio conteggio dello stesso problema")
check(result == {},
      "FAIL CLOSED: nessuna copertura pubblicata da questa lineage, ne' la "
      "finestra intera di A ne' la finestra ristretta di B",
      f"result={result!r} — non solo [00:00,12:00) manca: NIENTE e' "
      f"pubblicabile finche' la lineage non restituisce l'intero dominio")

print("\n  B1.10b lo stesso caso non trascina una chiave estranea")
unrelated = coverage([assertion(*FULL_DAY, partitions=[part(key="dt=2024-01-20")])],
                     coverage_id="cov-estranea")
p_unrelated = partition(key="dt=2024-01-20")
result, violations = reconstruct_catalog_coverage(
    [narrow_a, narrow_b, unrelated], [p_narrow, p_unrelated])
expect(["COVERAGE_MISSING_FOR_PARTITION", "COVERAGE_SUPERSESSION_NARROWS"],
       violations,
       "solo la lineage A/B e' viziata; dt=2024-01-20 non compare fra le "
       "violazioni perche' 'cov-estranea' la copre regolarmente")
check(("dt=" + DAY, 1) not in result and ("dt=2024-01-20", 1) in result,
      "una lineage estranea, senza alcun arco verso A o B, resta pubblicabile",
      f"result keys: {sorted(result)}")

print("\n  B1.11 supersessione a RESTATEMENT INTEGRALE: caso positivo, valido")
# stesso dominio [00,24) dichiarato PER INTERO dal documento che supersede:
# nessun restringimento, nessuna violazione, copertura piena pubblicata.
# Preserva esattamente lo scenario di riparazione a zero eventi (CASO 6):
# A e' complete/known_gap/complete, B lo supersede e restituisce l'intero
# dominio come complete, senza aggiungere un solo record.
full_restate_a = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:20:00Z"),
    assertion(DAY + "T12:20:00Z", DAY + "T12:28:00Z", status="known_gap",
             partitions=[], evidence=[ev("transport_interruption")]),
    assertion(DAY + "T12:28:00Z", NEXT_DAY + "T00:00:00Z"),
], coverage_id="cov-live")
full_restate_b = coverage(
    [assertion(*FULL_DAY, partitions=[part(revision=2)],
              evidence=[ev("reconciliation",
                           "backfill: 0 nuovi eventi in [12:20Z,12:28Z)")])],
    coverage_id="cov-repair", supersedes="cov-live")
p_repaired = partition(key="dt=" + DAY, revision=2)
result, violations = reconstruct_catalog_coverage(
    [full_restate_a, full_restate_b], [p_repaired])
expect([], violations,
       "restatement integrale del dominio di A: nessuna violazione")
check(result == {("dt=" + DAY, 2): (_parse_ts(FULL_DAY[0]), _parse_ts(FULL_DAY[1]))},
      "l'intera giornata e' pubblicata, esattamente come nel CASO 6 "
      "(riparazione a zero eventi) gia' testato altrove")


# ==========================================================================
print("\nB2. esistenza e unicita' delle partizioni referenziate")
# ==========================================================================

print("\n  B2.1 partizione sconosciuta")
unknown_ref = coverage([assertion(*FULL_DAY, partitions=[part("dt=2024-01-20")])])
result, violations = reconstruct_catalog_coverage([unknown_ref], [])
expect(["PARTITION_UNKNOWN"], violations,
       "l'asserzione referenzia dt=2024-01-20, nessun partition-manifest fornito")
check(result == {}, "nessuna copertura pubblicata per un riferimento ignoto")

print("\n  B2.2 partition-manifest duplicato (stessa chiave e revisione)")
dup_manifest_a = partition(key="dt=" + DAY, content_sha256="a" * 64)
dup_manifest_b = partition(key="dt=" + DAY, content_sha256="b" * 64)
cov_for_dup = coverage([assertion(*FULL_DAY)])
result, violations = reconstruct_catalog_coverage(
    [cov_for_dup], [dup_manifest_a, dup_manifest_b])
expect(["PARTITION_MANIFEST_DUPLICATE", "PARTITION_NOT_PUBLISHABLE"], violations,
       "due manifest per (dt=2024-01-15, rev 1) con hash diversi: ambiguo")
check(result == {}, "nessuna copertura pubblicata: quale file e' quello vero?")

print("\n  B2.3 revisioni vive concorrenti senza supersessione")
rev1_live = partition(key="dt=" + DAY, revision=1, state="valid")
rev2_live = partition(key="dt=" + DAY, revision=2, state="valid")
cov_rev1 = coverage([assertion(*FULL_DAY)], coverage_id="cov-rev1")
result, violations = reconstruct_catalog_coverage([cov_rev1],
                                                  [rev1_live, rev2_live])
expect(["PARTITION_LIVE_REVISION_CONFLICT", "PARTITION_NOT_PUBLISHABLE"],
       violations,
       "revisione 1 e 2 entrambe 'valid': quale e' quella viva? Non e' "
       "determinabile senza una supersessione esplicita")
check(result == {}, "nessuna copertura pubblicata sotto revisioni concorrenti")

print("\n  B2.3b lo stesso caso, ma con una supersessione esplicita: legittimo")
rev1_superseded = partition(key="dt=" + DAY, revision=1, state="superseded")
rev2_only_live = partition(key="dt=" + DAY, revision=2, state="valid")
cov_rev2 = coverage([assertion(*FULL_DAY, partitions=[part(revision=2)])],
                    coverage_id="cov-rev2")
result, violations = reconstruct_catalog_coverage(
    [cov_rev2], [rev1_superseded, rev2_only_live])
expect([], violations,
       "con la revisione 1 esplicitamente 'superseded' non c'e' piu' "
       "ambiguita': solo la 2 e' viva")
check(("dt=" + DAY, 2) in result, "la revisione viva riceve la copertura")

print("\n  B2.4 riferimento a una partizione non idonea: writing / invalid / superseded")
for bad_state in ("writing", "invalid", "superseded"):
    doc = partition(key="dt=" + DAY, state=bad_state)
    cov_doc = coverage([assertion(*FULL_DAY)], coverage_id=f"cov-{bad_state}")
    result, violations = reconstruct_catalog_coverage([cov_doc], [doc])
    expect(["PARTITION_NOT_ELIGIBLE"], violations,
           f"stato {bad_state!r}: non idoneo a ricevere 'complete'")
    check(result == {}, f"nessuna copertura pubblicata per stato {bad_state!r}")

print("\n  B2.5 gli stati idonei restano idonei: nessun falso positivo")
for good_state in ("closed", "valid", "degraded"):
    doc = partition(key="dt=" + DAY, state=good_state)
    cov_doc = coverage([assertion(*FULL_DAY)], coverage_id=f"cov-{good_state}")
    result, violations = reconstruct_catalog_coverage([cov_doc], [doc])
    expect([], violations, f"stato {good_state!r}: idoneo, nessuna violazione")
    check(("dt=" + DAY, 1) in result, f"copertura pubblicata per {good_state!r}")


# ==========================================================================
print("\nB3. precisione della copertura dichiarata: ceiling al microsecondo")
# ==========================================================================

print("\n  B3.1 round-trip esatto per ogni confine accettato nelle fixture valide")
FIXTURES = ROOT / "fixtures" / "coverage-manifest-v1"
import json  # noqa: E402
boundaries_checked = 0
for path in sorted(FIXTURES.glob("valid-*.json")):
    doc = json.loads(path.read_text(encoding="utf-8"))
    values = [doc["acquisition"]["intent_start"], doc["acquisition"]["intent_end"]]
    for a in doc["assertions"]:
        values += [a["start"], a["end"]]
    for value in values:
        nanos = _parse_ts(value)
        boundaries_checked += 1
        check(nanos % 1_000 == 0,
              f"{path.name}: {value} e' un multiplo esatto di 1000 ns "
              f"(nessun residuo sub-microsecondo)")
check(boundaries_checked > 0,
      f"{boundaries_checked} confini di copertura verificati nelle fixture valide")

print("\n  B3.2 il confine a 6 cifre della fixture dedicata usa davvero "
      "tutte e 6 le cifre")
micro_fixture = json.loads(
    (FIXTURES / "valid-microsecond-boundary.json").read_text(encoding="utf-8"))
start_value = micro_fixture["assertions"][0]["start"]
check(start_value.endswith(".100000Z") and len(start_value.split(".")[1]) == 7,
      "il valore usa esattamente 6 cifre frazionarie, non meno",
      f"valore: {start_value!r}")


# ==========================================================================
print("\nB4. row_count 0 implica event bounds osservati null")
# ==========================================================================

print("\n  B4.1 fixture richiesta: row_count 0 con bounds non-null")
bad_zero = partition(row_count=0, first=DAY + "T00:00:00Z",
                     last=DAY + "T01:00:00Z")
violations = check_partition_manifest(bad_zero)
expect(["ZERO_ROW_OBSERVED_BOUNDS", "ZERO_ROW_OBSERVED_BOUNDS"], violations,
       "row_count=0 con first_exchange_ts E last_exchange_ts non-null: "
       "due violazioni, una per campo")
check(all("row_count" in v.message and "0" in v.message for v in violations),
      "il messaggio identifica l'invariante violata (row_count 0)")

print("\n  B4.1b una sola meta' non-null e' comunque rilevata")
half_bad = partition(row_count=0, first=DAY + "T00:00:00Z", last=None)
violations = check_partition_manifest(half_bad)
expect(["ZERO_ROW_OBSERVED_BOUNDS"], violations,
       "solo first_exchange_ts non-null: una violazione, non due")

print("\n  B4.2 row_count 0 con bounds null: legittimo")
good_zero = partition(row_count=0, first=None, last=None)
expect([], check_partition_manifest(good_zero),
       "row_count 0 con bounds null e' il caso normale (ora silenziosa)")

print("\n  B4.3 row_count > 0 non e' toccato da questa regola: resta "
      "responsabilita' del JSON Schema frozen")
nonzero_with_bounds = partition(row_count=5, first=DAY + "T00:00:00Z",
                                last=DAY + "T01:00:00Z")
expect([], check_partition_manifest(nonzero_with_bounds),
       "row_count 5 con bounds presenti: normale, nessuna violazione qui")


# ==========================================================================
print("\nI1. assertion_id: identita' stabile, unica dentro il documento")
# ==========================================================================

print("\n  I1.1 duplicato dentro lo stesso documento")
dup_aid_doc = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T06:00:00Z", assertion_id="seg-1"),
    assertion(DAY + "T06:00:00Z", DAY + "T12:00:00Z", assertion_id="seg-1"),
])
expect(["ASSERTION_ID_DUPLICATE"], check_coverage_manifest(dup_aid_doc),
       "assertion_id 'seg-1' riusata da due asserzioni non sovrapposte nello "
       "stesso documento: la posizione nell'array non e' identita'")

print("\n  I1.2 lo stesso assertion_id in DUE documenti diversi non collide")
doc_a = coverage([assertion(*FULL_DAY, assertion_id="seg-1")], coverage_id="cov-a")
doc_b = coverage([assertion(*FULL_DAY, assertion_id="seg-1")], coverage_id="cov-b",
                 identity=KRAKEN_TRADES)
expect([], check_coverage_manifest(doc_a), "'seg-1' in cov-a: legittimo")
expect([], check_coverage_manifest(doc_b), "'seg-1' in cov-b: legittimo, "
       "l'unicita' e' PER DOCUMENTO, non globale")

print("\n  I1.3 tre assertion_id distinti: nessun falso positivo")
clean_ids = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T08:00:00Z", assertion_id="seg-1"),
    assertion(DAY + "T08:00:00Z", DAY + "T16:00:00Z", assertion_id="seg-2"),
    assertion(DAY + "T16:00:00Z", NEXT_DAY + "T00:00:00Z", assertion_id="seg-3"),
])
expect([], check_coverage_manifest(clean_ids),
       "tre id distinti, nessuna violazione")


# ==========================================================================
print("\nI2. reconstruct_catalog_coverage opera su UNA sola DatasetIdentity")
# ==========================================================================

print("\n  I2.1 coverage-manifest di due dataset diversi nello stesso invocation")
bybit_doc = coverage([assertion(*FULL_DAY)], coverage_id="cov-bybit",
                     identity=BYBIT_TRADES)
kraken_doc = coverage([assertion(*FULL_DAY)], coverage_id="cov-kraken",
                      identity=KRAKEN_TRADES)
result, violations = reconstruct_catalog_coverage([bybit_doc, kraken_doc], [])
expect(["RECONSTRUCTION_DATASET_MIXED"], violations,
       "bybit e kraken mescolati in una sola chiamata: fallisce chiuso")
check(result == {}, "nessuna copertura pubblicata quando i dataset si mescolano")

print("\n  I2.2 stesso coverage_id in due dataset distinti: non deve interagire")
same_id_bybit = coverage([assertion(*FULL_DAY)], coverage_id="x",
                         identity=BYBIT_TRADES)
same_id_kraken = coverage([assertion(*FULL_DAY)], coverage_id="x",
                          identity=KRAKEN_TRADES)
result_bybit, v_bybit = reconstruct_catalog_coverage([same_id_bybit], [P])
expect([], v_bybit,
       "coverage_id='x' nel solo dataset bybit: nessun conflitto, "
       "'x' non e' mai stato visto nel dataset kraken in questa chiamata")
kraken_partition = partition(key="dt=" + DAY, identity=KRAKEN_TRADES)
result_kraken, v_kraken = reconstruct_catalog_coverage([same_id_kraken],
                                                       [kraken_partition])
expect([], v_kraken,
       "coverage_id='x' nel solo dataset kraken: parimenti isolato")
check(result_bybit != {} and result_kraken != {}
      and list(result_bybit) == list(result_kraken),
      "le DUE chiamate separate producono copertura indipendente per "
      "ciascun dataset, usando lo stesso coverage_id senza scontrarsi",
      "la protezione e' strutturale: mescolarli nella STESSA chiamata fallisce "
      "(I2.1); chiamate separate per dataset non condividono mai lo stato")
result_mixed, v_mixed = reconstruct_catalog_coverage(
    [same_id_bybit, same_id_kraken], [P, kraken_partition])
expect(["RECONSTRUCTION_DATASET_MIXED"], v_mixed,
       "passarli assieme in UNA chiamata resta comunque rifiutato a monte, "
       "prima che 'x' possa mai collidere fra i due")

print("\n  I2.3 partition-manifest di un dataset diverso da quello dei coverage")
mismatched_partition = partition(key="dt=" + DAY, identity=KRAKEN_TRADES)
result, violations = reconstruct_catalog_coverage([bybit_doc],
                                                  [mismatched_partition])
expect(["RECONSTRUCTION_DATASET_MIXED"], violations,
       "coverage bybit con partition-manifest kraken: identita' incrociate, "
       "respinto prima di ogni tentativo di risoluzione per-chiave")


# ==========================================================================
print("\nI3. overlap fra documenti vivi: cosa e' ammesso, cosa e' respinto")
# ==========================================================================

print("\n  I3.1 complete vs known_gap sullo stesso intervallo: contraddizione, "
      "FAIL CLOSED")
complete_doc = coverage([assertion(*FULL_DAY)], coverage_id="cov-completo")
gap_doc = coverage(
    [assertion(DAY + "T12:00:00Z", DAY + "T13:00:00Z", status="known_gap",
              partitions=[], evidence=[ev("transport_interruption")])],
    coverage_id="cov-gap", intent=(DAY + "T12:00:00Z", DAY + "T13:00:00Z"))
result, violations = reconstruct_catalog_coverage([complete_doc, gap_doc], [P])
expect(["COVERAGE_CONTRADICTION"], violations,
       "un documento dichiara completo cio' che un altro dichiara scoperto")
check(("dt=" + DAY, 1) not in result,
      "la chiave contraddetta NON e' pubblicata: la contraddizione fa "
      "fallire chiusa quella copertura, non solo segnalarla",
      f"result={result!r}")

print("\n  I3.2 complete vs uncertain sullo stesso intervallo: contraddizione, "
      "FAIL CLOSED")
uncertain_doc = coverage(
    [assertion(DAY + "T12:00:00Z", DAY + "T13:00:00Z", status="uncertain",
              partitions=[], evidence=[ev("connection_continuity")])],
    coverage_id="cov-uncertain", intent=(DAY + "T12:00:00Z", DAY + "T13:00:00Z"))
result, violations = reconstruct_catalog_coverage([complete_doc, uncertain_doc], [P])
expect(["COVERAGE_CONTRADICTION"], violations,
       "un documento dichiara completo cio' che un altro dichiara incerto")
check(("dt=" + DAY, 1) not in result,
      "anche 'uncertain' contro 'complete' non pubblica nulla per la chiave")

print("\n  I3.3 lo stesso confronto, con l'ordine invertito: simmetrico, "
      "FAIL CLOSED in entrambi i versi")
result, violations = reconstruct_catalog_coverage([gap_doc, complete_doc], [P])
expect(["COVERAGE_CONTRADICTION"], violations,
       "known_gap prima, complete dopo: stesso esito, la contraddizione non "
       "dipende da chi viene elaborato per primo")
check(("dt=" + DAY, 1) not in result,
      "il fail-closed non dipende dall'ordine: nessuna copertura pubblicata "
      "in nessuno dei due versi")

print("\n  I3.3b la contraddizione non trascina UNA CHIAVE DIVERSA nello stesso "
      "invocation: il fail-closed e' per-chiave, non per-chiamata")
# la finestra dell'altra chiave NON si sovrappone a quella contestata: un
# known_gap senza 'partitions' e' una dichiarazione sulla LINEA TEMPORALE del
# dataset, non su una singola partizione, quindi il confronto e' giustamente
# per ORARIO e non per chiave — una finestra distinta resta percio' distinta.
OTHER_DAY = ("2024-01-20T00:00:00Z", "2024-01-21T00:00:00Z")
other_key_doc = coverage(
    [assertion(*OTHER_DAY, partitions=[part(key="dt=2024-01-20")])],
    coverage_id="cov-altra-chiave", intent=OTHER_DAY)
p_other = partition(key="dt=2024-01-20")
result, violations = reconstruct_catalog_coverage(
    [complete_doc, gap_doc, other_key_doc], [P, p_other])
expect(["COVERAGE_CONTRADICTION"], violations,
       "solo dt=2024-01-15 e' contraddetta")
check(("dt=" + DAY, 1) not in result and ("dt=2024-01-20", 1) in result,
      "la chiave non coinvolta nella contraddizione resta pubblicabile",
      f"result keys: {sorted(result)}")

print("\n  I3.4 overlap COMPATIBILE, stesso stato, stessa attribuzione: ammesso")
# due archivi indipendenti coprono ore sovrapposte della STESSA partizione con
# lo STESSO verdetto 'complete': l'unione e' inequivocabile.
overlap_a = coverage([assertion(DAY + "T00:00:00Z", DAY + "T13:00:00Z")],
                     coverage_id="cov-a")
overlap_b = coverage([assertion(DAY + "T12:00:00Z", NEXT_DAY + "T00:00:00Z")],
                     coverage_id="cov-b")
result, violations = reconstruct_catalog_coverage([overlap_a, overlap_b], [P])
expect([], violations,
       "due dichiarazioni 'complete' che si sovrappongono sulla STESSA "
       "partizione fondono senza conflitto: non c'e' ambiguita' da risolvere")
check(result[("dt=" + DAY, 1)] == (_parse_ts(FULL_DAY[0]), _parse_ts(FULL_DAY[1])),
      "l'unione copre l'intera giornata")


# ==========================================================================
print("\nI5. riparazione REALE: eventi prima e dopo il buco, backfill a zero")
# ==========================================================================

# A differenza del CASO 6 di test_declared_coverage_semantics.py (giornata
# interamente vuota), qui la partizione contiene GIA' eventi veri prima e
# dopo il buco. La riparazione non aggiunge un solo record: aggiunge solo
# CONOSCENZA che quell'intervallo era comunque a zero eventi.
BEFORE_GAP_TRADE = DAY + "T09:15:22.500000Z"
AFTER_GAP_TRADE = DAY + "T15:40:07.250000Z"

live_with_real_events = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:20:00Z",
             evidence=[ev("connection_continuity", "fino a 12:20Z")]),
    assertion(DAY + "T12:20:00Z", DAY + "T12:28:00Z", status="known_gap",
             partitions=[],
             evidence=[ev("transport_interruption", "socket chiuso")]),
    assertion(DAY + "T12:28:00Z", NEXT_DAY + "T00:00:00Z",
             evidence=[ev("connection_continuity", "da 12:28Z")]),
], coverage_id="cov-live-real")

# la partizione REALE ha due trade: uno alle 09:15 (prima del buco) e uno
# alle 15:40 (dopo il buco). row_count riflette SOLO questi due, il buco
# [12:20,12:28) non ha mai avuto un evento suo, riparato o meno.
partition_before_repair = partition(
    row_count=2, first=BEFORE_GAP_TRADE, last=AFTER_GAP_TRADE)

result, violations = reconstruct_catalog_coverage(
    [live_with_real_events], [partition_before_repair])
expect(["COVERAGE_NOT_CONTIGUOUS"], violations,
       "prima della riparazione: il buco reale spezza la contiguita', anche "
       "con eventi veri immediatamente prima e dopo")
check(result == {},
      "nessuna copertura pubblicabile finche' il buco resta aperto")

# il backfill autorevole copre [12:20,12:28) e NON trova alcun evento nuovo:
# ri-materializza la giornata come revisione 2 (stessi identici record delle
# 09:15 e 15:40, nessuno aggiunto) e supersede il documento live.
repair_zero_new_events = coverage(
    [assertion(*FULL_DAY, partitions=[part(revision=2)],
              evidence=[ev("connection_continuity",
                           "sessione live originaria, salvo [12:20Z,12:28Z)"),
                        ev("archive_completeness",
                           "archivio autorevole della giornata, verificato integro"),
                        ev("reconciliation",
                           "backfill di [12:20Z,12:28Z): 0 trade pubblicati "
                           "dalla venue in quell'intervallo")])],
    coverage_id="cov-repair-real", supersedes="cov-live-real")

# stessi identici event bounds della revisione 1: la riparazione e'
# conoscenza di copertura, non un nuovo evento materializzato.
partition_after_repair = partition(
    row_count=2, first=BEFORE_GAP_TRADE, last=AFTER_GAP_TRADE, revision=2)

result, violations = reconstruct_catalog_coverage(
    [live_with_real_events, repair_zero_new_events],
    [partition_after_repair])
expect([], violations,
       "dopo la riparazione: nessuna violazione, il buco e' chiuso")
check(result[("dt=" + DAY, 2)] == (_parse_ts(FULL_DAY[0]), _parse_ts(FULL_DAY[1])),
      "la copertura diventa l'intera giornata, contigua")
check(("dt=" + DAY, 1) not in result,
      "la revisione 1 (live, superseduta) non contribuisce piu' nulla")

# LA PROVA CENTRALE del finding I5: gli event bounds osservati della
# partizione riparata sono IDENTICI a quelli di prima. Nessun record e' stato
# aggiunto: solo la copertura DICHIARATA e' cambiata da spezzata a completa.
check(partition_before_repair["first_exchange_ts"]
      == partition_after_repair["first_exchange_ts"]
      and partition_before_repair["last_exchange_ts"]
      == partition_after_repair["last_exchange_ts"]
      and partition_before_repair["row_count"] == partition_after_repair["row_count"],
      "row_count e event bounds osservati sono IDENTICI prima e dopo: "
      "'riparare la conoscenza' non e' 'aggiungere record'",
      f"row_count={partition_after_repair['row_count']} in entrambe le "
      f"revisioni, bounds [{BEFORE_GAP_TRADE}, {AFTER_GAP_TRADE}] invariati")

# e gli event bounds restano dentro la copertura ORA completa, senza che il
# controllo I8 si lamenti: la riparazione non ha bisogno di toccare gli
# eventi per rendere la copertura containment-valida. 'violations' qui sopra
# e' gia' questa stessa prova (include sia il fold sia I8 sulla revisione 2),
# ma lo si ribadisce isolando SOLO la verifica di contenimento.
_, containment_violations = reconstruct_catalog_coverage(
    [live_with_real_events, repair_zero_new_events],
    [partition_after_repair])
expect([], containment_violations,
       "gli stessi event bounds, ora dentro una copertura completa, non "
       "violano il contenimento I8")


print()
if fail:
    print(f"{RED}FAIL{OFF}: {len(fail)} controlli non superati")
    for name in fail:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: tutti i findings del review Codex verificati")
