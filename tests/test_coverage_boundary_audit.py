#!/usr/bin/env python3
"""Audit trasversale: copertura dichiarata -> catalogo -> DataGateway -> API.

test_declared_coverage_semantics.py copre il lato produttore in isolamento.
Qui si attraversa il confine: si prende il DataGateway VERO e si verifica cosa
il catalogo attuale sa e non sa rappresentare.

Quattro domande:

 13. un buco riparato da un backfill autorevole a zero record diventa completo;
 14. l'ordine dei documenti non cambia il risultato del fold;
 15. una partizione 'degraded' il cui span contiene un buco interno noto viene
     dichiarata COMPLETAMENTE COPERTA dal DataGateway attuale — ed e' per
     questo che il contratto la rende non pubblicabile;
 16. copertura completa con zero record resta un successo, distinto da
     no_coverage, lungo tutta la catena.

Il caso 15 e' un test NEGATIVO a livello di contratto: non simula un supporto
che il catalogo non ha, lo dimostra assente.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from semantic_validator import (  # noqa: E402
    _format_ts, reconstruct_catalog_coverage,
)
from quant_platform.access.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    DataRequest,
    LifecyclePolicy,
)
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.data import (  # noqa: E402
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    NoCoverage,
    TradeRecord,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

DAY, NEXT_DAY = "2024-01-15", "2024-01-16"
FULL_DAY = (DAY + "T00:00:00Z", NEXT_DAY + "T00:00:00Z")

IDENTITY = DatasetIdentity(
    layer="canonical", dataset_kind="trades", venue="bybit",
    instrument="BTCUSDT", record_schema_id="trade-v1",
)
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
DATASET = CatalogDataset(
    identity=IDENTITY, catalog_dataset_id="dataset-uuid", rel_root=REL_ROOT,
    manifest_sha256="d" * 64, schema_version=1, schema_hash="e" * 64,
)

fail = []


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


# --------------------------------------------------------------------------
# lato produttore
# --------------------------------------------------------------------------
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


def coverage(assertions, coverage_id, supersedes=None, intent=FULL_DAY,
             basis="source_archive"):
    return {
        "schema_version": "coverage-manifest-v1", "layer": "canonical",
        "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
        "record_schema_id": "trade-v1",
        "coverage_id": coverage_id, "supersedes": supersedes,
        "created_at": "2024-01-16T00:00:00Z",
        "acquisition": {"basis": basis, "intent_start": intent[0],
                        "intent_end": intent[1],
                        "source_semantics": "src-v1", "mapping": "map-v1"},
        "assertions": assertions,
        "producer": "test-producer", "code_ref": "0000000",
    }


def partition_manifest(row_count=0, first=None, last=None, revision=1,
                       state="valid"):
    key = "dt=" + DAY
    return {
        "schema_version": "partition-manifest-v1", "layer": "canonical",
        "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
        "record_schema_id": "trade-v1",
        "partition_key": key, "revision": revision, "state": state,
        "rel_path": key + "/part-000.parquet",
        "file_size_bytes": 812, "row_count": row_count,
        "sha256": "a" * 64, "first_exchange_ts": first, "last_exchange_ts": last,
        "first_sequence": None, "last_sequence": None,
        "created_at": DAY + "T00:00:00Z", "closed_at": NEXT_DAY + "T00:00:00Z",
        "producer": "test-producer", "code_ref": "0000000",
    }


def iso(interval):
    return (_format_ts(interval[0]), _format_ts(interval[1]))


def codes(violations):
    return sorted(v.code for v in violations)


# --------------------------------------------------------------------------
# lato consumatore: DataGateway VERO, catalogo e reader iniettati
# --------------------------------------------------------------------------
class FakeCatalog:
    """Sostituisce solo l'accesso a PostgreSQL. La selezione riproduce
    esattamente i predicati di catalog.py, half-open inclusi."""

    def __init__(self, partitions):
        self.partitions = partitions

    def resolve_dataset(self, identity):
        return DATASET

    def select_partitions(self, dataset, start, end, states):
        return [p for p in self.partitions
                if p.state in states and p.coverage is not None
                and p.ts_end > start and p.ts_start < end]


def catalog_partition(ts_start, ts_end, *, state="valid", row_count=0,
                      key="dt=" + DAY, revision=1, partition_id="p-a"):
    return CatalogPartition(
        natural_identity=NaturalPartitionIdentity(IDENTITY, key, revision),
        catalog_partition_id=partition_id, storage_root_id="hot",
        storage_root="/srv/marketdata", dataset_rel_root=REL_ROOT,
        rel_path=key + "/part-000.parquet",
        ts_start=Instant.parse(ts_start), ts_end=Instant.parse(ts_end),
        row_count=row_count, content_sha256="c" * 64, manifest_sha256="d" * 64,
        state=state, producer="fixture", code_ref="test",
    )


def gateway(partitions, records=()):
    """Reader e path resolver iniettati: qui si misura la SEMANTICA DI
    COPERTURA, non la lettura di un Parquet."""
    return DataGateway(
        FakeCatalog(partitions),
        batch_reader=lambda path, start, end, _batch_size: [tuple(
            r for r in records if start <= r.exchange_ts < end)],
        path_resolver=lambda root, rel_root, rel_path: Path(root) / rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )


def trade(ts, trade_id):
    return TradeRecord(venue="bybit", instrument="BTCUSDT",
                       exchange_ts=Instant.parse(ts), price="1", size="1",
                       aggressor_side="buy", trade_id=trade_id)


def read(gw, start, end, policy=LifecyclePolicy.VALID_ONLY):
    return gw.read(DataRequest(
        dataset_selector=IDENTITY, schema_requirement="trade-v1",
        start=start, end=end, lifecycle_policy=policy,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY))


# ==========================================================================
print("\n13. buco riparato da un backfill autorevole a ZERO record")
# ==========================================================================
# T0: la sessione live perde [12:20, 12:28). La partizione revisione 1 ha una
# copertura completa spezzata in due tronconi.
live = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:20:00Z",
              evidence=[ev("connection_continuity", "fino a 12:20Z")]),
    assertion(DAY + "T12:20:00Z", DAY + "T12:28:00Z", status="known_gap",
              partitions=[],
              evidence=[ev("transport_interruption", "socket chiuso")]),
    assertion(DAY + "T12:28:00Z", NEXT_DAY + "T00:00:00Z",
              evidence=[ev("connection_continuity", "da 12:28Z")]),
], "cov-live-seg-1")

result, violations = reconstruct_catalog_coverage([live], [partition_manifest()])
check(codes(violations) == ["COVERAGE_NOT_CONTIGUOUS"],
      "T0: la partizione con buco interno non e' pubblicabile",
      f"violazioni: {codes(violations)}")

# T1: il backfill autorevole copre [12:20, 12:28) e restituisce ZERO record.
# Ri-materializza la giornata come revisione 2 e supersede il documento live.
repair = coverage([
    assertion(*FULL_DAY, partitions=[part(revision=2)],
              evidence=[ev("connection_continuity", "sessione live originaria"),
                        ev("archive_completeness", "archivio della giornata integro"),
                        ev("reconciliation",
                           "backfill di [12:20Z, 12:28Z): 0 trade pubblicati")]),
], "cov-backfill-repair", supersedes="cov-live-seg-1")

repaired = partition_manifest(row_count=0, revision=2)
result, violations = reconstruct_catalog_coverage([live, repair], [repaired])
check(codes(violations) == [],
      "T1: dopo la riparazione nessuna violazione")
check(iso(result[("dt=" + DAY, 2)]) == FULL_DAY,
      "la copertura e' completa senza aggiungere un solo record",
      f"{iso(result[('dt=' + DAY, 2)])} con row_count=0")
check(("dt=" + DAY, 1) not in result,
      "la revisione 1 superseduta non contribuisce piu' nulla")

# la precedenza e' l'arco `supersedes`, non un timestamp
older_written_later = dict(repair, created_at="2020-01-01T00:00:00Z")
r2, v2 = reconstruct_catalog_coverage([live, older_written_later], [repaired])
check(r2 == result and codes(v2) == [],
      "la precedenza NON dipende da created_at",
      "il documento riparatore vince anche con un created_at anteriore")


# ==========================================================================
print("\n14. l'ordine dei documenti non cambia il risultato")
# ==========================================================================
docs = [live, repair]
parts = [repaired, partition_manifest(revision=1, state="superseded")]
baseline = None
stable = True
for doc_order in itertools.permutations(docs):
    for part_order in itertools.permutations(parts):
        got, viol = reconstruct_catalog_coverage(list(doc_order), list(part_order))
        signature = ({k: iso(v) for k, v in got.items()}, codes(viol))
        if baseline is None:
            baseline = signature
        elif signature != baseline:
            stable = False
check(stable,
      "4 permutazioni di documenti x partizioni: risultato identico",
      "coverage E multiset dei codici di violazione invariati")

# stessa prova su un insieme che PRODUCE violazioni: deve restare stabile
broken = [
    coverage([assertion(*FULL_DAY)], "cov-a"),
    coverage([assertion(DAY + "T06:00:00Z", DAY + "T07:00:00Z",
                        status="known_gap", partitions=[],
                        evidence=[ev("transport_interruption")])],
             "cov-b", intent=(DAY + "T06:00:00Z", DAY + "T07:00:00Z")),
]
sigs = {tuple(codes(reconstruct_catalog_coverage(list(o), [partition_manifest()])[1]))
        for o in itertools.permutations(broken)}
check(len(sigs) == 1,
      "anche un insieme contraddittorio produce lo stesso verdetto in ogni ordine",
      f"codici: {sorted(sigs)[0]}")

# il limite dichiarato: con coverage_id duplicato il fold segnala SEMPRE, ma la
# copertura restituita accanto alla violazione non e' garantita stabile
dup = [coverage([assertion(*FULL_DAY)], "cov-x"),
       coverage([assertion(DAY + "T00:00:00Z", DAY + "T06:00:00Z")], "cov-x")]
dup_codes = {tuple(codes(reconstruct_catalog_coverage(list(o), [])[1]))
             for o in itertools.permutations(dup)}
check(dup_codes == {("COVERAGE_ID_DUPLICATE",)},
      "coverage_id duplicato: la violazione e' sempre sollevata, in ogni ordine",
      "la copertura affiancata non e' utilizzabile e il contratto lo dice")


# ==========================================================================
print("\n15. 'degraded' NON restringe la copertura: prova sul DataGateway vero")
# ==========================================================================
# Opzione A del mandato: ts_start/ts_end = span esterno, state = degraded.
# La partizione conterrebbe un buco noto [12:10, 12:17).
spanning = catalog_partition(*FULL_DAY, state="degraded", row_count=2)
records = [trade(DAY + "T11:00:00Z", "t1"), trade(DAY + "T13:00:00Z", "t2")]

# VALID_ONLY: la partizione e' esclusa del tutto
try:
    read(gateway([spanning], records), *FULL_DAY)
    check(False, "VALID_ONLY dovrebbe escludere la partizione degraded")
except NoCoverage:
    check(True, "VALID_ONLY esclude l'intera partizione degraded",
          "sicuro ma inservibile: si perde anche la parte davvero coperta")

# VALID_CLOSED_AND_DEGRADED: la partizione entra, e lo span vale INTERO
slice_ = read(gateway([spanning], records), *FULL_DAY,
              policy=LifecyclePolicy.VALID_CLOSED_AND_DEGRADED)
check(slice_.metadata.coverage_complete is True and not slice_.metadata.coverage_gaps,
      "con opt-in degraded il gateway dichiara la giornata COMPLETAMENTE coperta",
      "il buco noto [12:10Z, 12:17Z) sparisce: e' una bugia in ts_start/ts_end")

# la richiesta ristretta al solo buco ha successo e restituisce zero record:
# indistinguibile da un'ora legittimamente silenziosa
gap_slice = read(gateway([spanning], records), DAY + "T12:10:00Z", DAY + "T12:17:00Z",
                 policy=LifecyclePolicy.VALID_CLOSED_AND_DEGRADED)
check(gap_slice.metadata.coverage_complete is True and gap_slice.metadata.row_count == 0,
      "una richiesta sul solo buco ritorna successo con zero record",
      "il consumatore non ha modo di distinguerla da 'nessun trade in quell'ora'")
# lo stato filtra QUALI partizioni si leggono, non QUANTO coprono: la stessa
# riga letta come 'valid' o come 'degraded' produce la stessa identica copertura
as_valid = read(gateway([catalog_partition(*FULL_DAY, row_count=2)], records),
                *FULL_DAY)
check(as_valid.metadata.eligible_coverage[0].stable_dict()
      == slice_.metadata.eligible_coverage[0].stable_dict()
      and as_valid.metadata.coverage_complete == slice_.metadata.coverage_complete,
      "lo stato del ciclo di vita non restringe di un nanosecondo la copertura",
      "'valid' e 'degraded' danno lo stesso eligible_coverage: nessun campo del "
      "catalogo puo' esprimere una copertura parziale")

# percio' il contratto rifiuta di produrre quella riga di catalogo
internal_gap = coverage([
    assertion(DAY + "T00:00:00Z", DAY + "T12:10:00Z"),
    assertion(DAY + "T12:10:00Z", DAY + "T12:17:00Z", status="known_gap",
              partitions=[], evidence=[ev("transport_interruption")]),
    assertion(DAY + "T12:17:00Z", NEXT_DAY + "T00:00:00Z"),
], "cov-internal-gap")
_, violations = reconstruct_catalog_coverage(
    [internal_gap], [partition_manifest(row_count=2,
                                        first=DAY + "T11:00:00Z",
                                        last=DAY + "T13:00:00Z")])
check(codes(violations) == ["COVERAGE_NOT_CONTIGUOUS"],
      "il fold rifiuta di pubblicare lo span esterno come riga unica",
      "test negativo di contratto: il catalogo attuale non sa rappresentare il caso")


# ==========================================================================
print("\n16. copertura completa a zero record != nessuna copertura")
# ==========================================================================
QUIET = (DAY + "T12:00:00Z", DAY + "T13:00:00Z")

# copertura dichiarata completa, nessun evento
quiet = catalog_partition(*FULL_DAY, row_count=0)
empty_success = read(gateway([quiet], []), *QUIET)
check(empty_success.metadata.row_count == 0
      and empty_success.metadata.coverage_complete is True
      and empty_success.metadata.returned_record_bounds is None,
      "successo vuoto: row_count 0, complete true, bounds null",
      "e' la forma che CONSUMER_API sezione 5 mappa su un risultato riuscito")

# nessuna copertura dichiarata sull'intervallo richiesto
try:
    read(gateway([catalog_partition(DAY + "T00:00:00Z", DAY + "T06:00:00Z")], []),
         *QUIET)
    check(False, "un intervallo scoperto dovrebbe fallire")
except NoCoverage:
    check(True, "nessuna copertura: NoCoverage, non un successo vuoto",
          "la distinzione che CONSUMER_API deve preservare come no_coverage")

# copertura parziale: sotto la policy STRICT v1 e' no_coverage, non un parziale
try:
    read(gateway([catalog_partition(DAY + "T00:00:00Z", DAY + "T12:30:00Z")], []),
         *QUIET)
    check(False, "una copertura parziale dovrebbe fallire sotto STRICT")
except NoCoverage:
    check(True, "copertura parziale: NoCoverage sotto la policy STRICT v1",
          "il sottoinsieme disponibile non viene restituito in silenzio")

check(empty_success.metadata.dataset_identity == IDENTITY
      and empty_success.metadata.rel_paths and empty_success.metadata.catalog_partition_ids,
      "i locator fisici restano nei diagnostici, non nell'identita' stabile",
      "il partizionamento fisico non trapela nella semantica consumer")

print()
if fail:
    print(f"{RED}FAIL{OFF}: {len(fail)} controlli non superati")
    for name in fail:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: audit trasversale della copertura superato")
