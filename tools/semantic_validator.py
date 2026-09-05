#!/usr/bin/env python3
"""Semantic validator — reference implementation.

Verifica le invarianti che JSON Schema NON puo' esprimere, perche' richiedono
di confrontare il valore di due proprieta' fra loro o di due documenti fra loro.
JSON Schema valuta ogni campo in isolamento; qui si guarda la coerenza.

    JSON Schema        -> forma e dominio di ogni singolo campo
    semantic validator -> coerenza fra campi e fra documenti

Non e' un servizio e non fa I/O: nessun accesso a PostgreSQL, nessun filesystem,
nessun collector. Prende documenti gia' parsati e restituisce violazioni.
Le verifiche che richiedono di aprire il data file (sha256 reale, dimensione
reale, conteggio righe del Parquet) NON stanno qui: appartengono a un
verificatore fisico, che ha bisogno di I/O.

Invarianti coperte:
  1. dataset.rel_root == rel_root derivato dall'identita' naturale
  2. first_exchange_ts <= last_exchange_ts        (event bounds OSSERVATI)
  3. first_sequence   <= last_sequence            (valori NUMERICI)
  4. partition.rel_path inizia con partition_key + '/'
  5. identita' della partizione == identita' del dataset
  6. nessuna self-lineage: un dataset non deriva da se stesso
  7. intervallo di acquisizione half-open e non degenere
  8. intervallo dichiarato half-open e non degenere
  9. asserzione dentro l'intent di acquisizione dichiarato
 10. stato che non contraddice la propria evidenza
 11. asserzioni non sovrapposte dentro un coverage-manifest
 12. identita' del coverage-manifest == identita' del dataset
 13. row_count 0 -> event bounds osservati null
 14. assertion_id unica dentro un coverage-manifest (posizione non e' identita')

Ricostruzione (reconstruct_catalog_coverage):
 tutti i documenti passati devono condividere UNA sola DatasetIdentity, sia
 fra i coverage-manifest sia fra i partition-manifest (altrimenti fallisce
 chiuso su tutta la chiamata: RECONSTRUCTION_DATASET_MIXED). Il grafo di
 supersessione (auto-supersessione, ciclo, ramificazione, riferimento
 pendente, coverage_id duplicato) deve essere valido, o nessuna copertura
 viene pubblicata (fail-closed, nessuna precedenza per created_at/ordine).
 Ogni asserzione 'complete' deve risolvere a ESATTAMENTE un partition-manifest
 esistente, idoneo per stato, e ogni partition_key deve avere al piu' una
 revisione viva fra quelle fornite. La copertura ricavata deve infine
 rispettare i confini osservati (event bounds dentro la copertura dichiarata)
 e restare un unico intervallo contiguo per partizione.
"""

from datetime import datetime, timedelta, timezone
import re
import string

__all__ = [
    "Violation", "encode_instrument", "decode_instrument", "derive_rel_root",
    "natural_identity", "check_dataset_manifest", "check_partition_manifest",
    "check_coverage_manifest", "reconstruct_catalog_coverage",
    "COVERAGE_STRUCTURAL_CODES", "validate",
]

# Caratteri che restano letterali nel percent-encoding. Il '%' NON e' fra
# questi: e' cio' che rende la mappa iniettiva.
SAFE = frozenset(string.ascii_letters + string.digits + "._-")
DATASET_MANIFEST_V1 = "dataset-manifest-v1"
DATASET_MANIFEST_V2 = "dataset-manifest-v2"
DATASET_ORIGINS = frozenset(("dataset_derived", "source_acquired"))
CANONICAL_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class Violation:
    """Una invariante violata. `code` e' stabile, `message` e' per l'umano."""

    __slots__ = ("code", "message", "where")

    def __init__(self, code, message, where=""):
        self.code = code
        self.message = message
        self.where = where

    def __repr__(self):
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.code}{loc}: {self.message}"

    def __eq__(self, other):
        return (isinstance(other, Violation) and self.code == other.code
                and self.where == other.where)


# --------------------------------------------------------------------------
# percent-encoding canonico di instrument
# --------------------------------------------------------------------------
def encode_instrument(value):
    """Valore nativo -> componente di path. Iniettivo e reversibile.

    Ogni carattere fuori da SAFE diventa %XX sui byte del suo UTF-8, con
    esadecimale MAIUSCOLO. Case e punteggiatura non sono normalizzati: sono
    differenze semantiche, non rumore.
    """
    out = []
    for ch in value:
        if ch in SAFE:
            out.append(ch)
        else:
            out.extend("%%%02X" % b for b in ch.encode("utf-8"))
    return "".join(out)


def decode_instrument(component):
    """Inversa esatta di encode_instrument."""
    raw = bytearray()
    i = 0
    while i < len(component):
        if component[i] == "%":
            raw.append(int(component[i + 1:i + 3], 16))
            i += 3
        else:
            raw.extend(component[i].encode("utf-8"))
            i += 1
    return raw.decode("utf-8")


def derive_rel_root(identity):
    """<layer>/<kind>/<venue>/<instrument_path>[/<fs_slug>/v<fs_version>]/<record_schema_id>"""
    parts = [
        identity["layer"],
        identity["dataset_kind"],
        identity["venue"],
        encode_instrument(identity["instrument"]),
    ]
    if identity["layer"] == "features":
        parts.append(identity["feature_set_slug"])
        parts.append("v%d" % identity["feature_set_version"])
    parts.append(identity["record_schema_id"])
    return "/".join(parts)


def natural_identity(doc):
    """Tupla che identifica un dataset. Non un ID, non un path."""
    base = (doc.get("layer"), doc.get("dataset_kind"), doc.get("venue"),
            doc.get("instrument"), doc.get("record_schema_id"))
    if doc.get("layer") == "features":
        return base + (doc.get("feature_set_slug"), doc.get("feature_set_version"))
    return base


# --------------------------------------------------------------------------
# helper di confronto
# --------------------------------------------------------------------------
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_ts(value):
    """RFC 3339 con 'Z' -> NANOSECONDI dall'epoch, esatti.

    Il confronto usa SEMPRE il valore parsato: '...:11Z' e '...:11.000Z' sono
    lo stesso istante ma stringhe diverse, e ordinarle lessicograficamente
    darebbe il risultato sbagliato.

    Nanosecondi interi e non datetime: utc_timestamp ammette 9 decimali, ma
    datetime ne regge 6 e datetime.fromisoformat TRONCA in silenzio i tre piu'
    fini. Con quel troncamento due istanti distinti a 100 ns risulterebbero
    uguali, e un buco di copertura sotto il microsecondo sparirebbe fondendo
    due intervalli che non sono affatto adiacenti. DataGateway lavora in
    epoch_ns: qui si tiene la stessa precisione.
    """
    if value is None:
        return None
    text = value[:-1] if value.endswith("Z") else value
    head, _, fraction = text.partition(".")
    moment = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    seconds = (moment - EPOCH) // timedelta(seconds=1)
    return seconds * 1_000_000_000 + int(fraction.ljust(9, "0")) if fraction \
        else seconds * 1_000_000_000


def _format_ts(nanos):
    """Nanosecondi -> RFC 3339 con 'Z', per i messaggi di violazione."""
    seconds, remainder = divmod(nanos, 1_000_000_000)
    moment = (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{moment}.{remainder:09d}Z" if remainder else moment + "Z"


def _parse_seq(value):
    """Stringa decimale canonica -> int. Il confronto e' NUMERICO: come stringhe
    '9' risulterebbe maggiore di '10'."""
    return None if value is None else int(value)


# --------------------------------------------------------------------------
# controlli
# --------------------------------------------------------------------------
def _check_transform(manifest, violations):
    transform = manifest.get("transform")
    if not isinstance(transform, str) or not CANONICAL_IDENTIFIER.fullmatch(transform):
        violations.append(Violation(
            "TRANSFORM_REQUIRED",
            "transform deve essere un identificatore semantico non vuoto",
            "transform",
        ))


def _check_dataset_topology_v1(manifest):
    violations = []
    layer = manifest.get("layer")
    lineage = manifest.get("derived_from")
    if layer == "raw":
        if lineage not in (None, []):
            violations.append(Violation(
                "RAW_LINEAGE",
                "un dataset raw non puo' dichiarare genitori",
                "derived_from",
            ))
        if "transform" in manifest:
            violations.append(Violation(
                "RAW_TRANSFORM",
                "un dataset raw non puo' dichiarare transform",
                "transform",
            ))
    elif layer in {"canonical", "features"}:
        if not isinstance(lineage, list) or not lineage:
            violations.append(Violation(
                "LINEAGE_REQUIRED",
                "un dataset v1 non raw richiede almeno un genitore",
                "derived_from",
            ))
        if "transform" not in manifest or not isinstance(manifest.get("transform"), str) or not CANONICAL_IDENTIFIER.fullmatch(manifest["transform"]):
            violations.append(Violation(
                "TRANSFORM_REQUIRED",
                "un dataset v1 non raw richiede transform",
                "transform",
            ))
    return violations


def _check_dataset_topology_v2(manifest):
    violations = []
    layer = manifest.get("layer")
    origin = manifest.get("origin")
    lineage = manifest.get("derived_from")
    if layer == "raw":
        for field in ("origin", "derived_from", "transform"):
            if field in manifest:
                violations.append(Violation(
                    "RAW_TOPOLOGY",
                    "un dataset raw v2 non puo' dichiarare metadati di topologia",
                    field,
                ))
    elif layer == "canonical":
        if not isinstance(origin, str) or origin not in DATASET_ORIGINS:
            violations.append(Violation(
                "MISSING_ORIGIN" if "origin" not in manifest else "INVALID_ORIGIN",
                "un dataset canonical v2 richiede un origin supportato",
                "origin",
            ))
        elif origin == "source_acquired":
            if "derived_from" in manifest:
                violations.append(Violation(
                    "SOURCE_ACQUIRED_LINEAGE",
                    "source_acquired richiede derived_from assente",
                    "derived_from",
                ))
            if "transform" not in manifest or not isinstance(manifest.get("transform"), str) or not CANONICAL_IDENTIFIER.fullmatch(manifest["transform"]):
                violations.append(Violation(
                    "TRANSFORM_REQUIRED",
                    "source_acquired richiede transform",
                    "transform",
                ))
        else:
            if not isinstance(lineage, list) or not lineage:
                violations.append(Violation(
                    "LINEAGE_REQUIRED",
                    "dataset_derived richiede almeno un genitore",
                    "derived_from",
                ))
            _check_transform(manifest, violations)
    elif layer == "features":
        if origin != "dataset_derived":
            violations.append(Violation(
                "FEATURE_ORIGIN",
                "features v2 richiede origin=dataset_derived",
                "origin",
            ))
        if not isinstance(lineage, list) or not lineage:
            violations.append(Violation(
                "LINEAGE_REQUIRED",
                "features v2 richiede almeno un genitore",
                "derived_from",
            ))
        _check_transform(manifest, violations)
    return violations


def check_dataset_manifest(manifest):
    """Invarianti interne a un dataset-manifest."""
    v = []

    version = manifest.get("schema_version")
    if version == DATASET_MANIFEST_V1:
        v.extend(_check_dataset_topology_v1(manifest))
    elif version == DATASET_MANIFEST_V2:
        v.extend(_check_dataset_topology_v2(manifest))
    else:
        v.append(Violation(
            "UNSUPPORTED_DATASET_MANIFEST_VERSION",
            f"schema_version dataset non supportato: {version!r}",
            "schema_version",
        ))

    # 1. rel_root derivato
    try:
        expected = derive_rel_root(manifest)
    except (KeyError, TypeError):
        v.append(Violation("REL_ROOT_UNDERIVABLE",
                           "identita' incompleta: rel_root non derivabile",
                           "rel_root"))
    else:
        if manifest.get("rel_root") != expected:
            v.append(Violation(
                "REL_ROOT_MISMATCH",
                f"rel_root dichiarato {manifest.get('rel_root')!r}, "
                f"derivato dall'identita' {expected!r}", "rel_root"))

    # 6. no self-lineage
    me = natural_identity(manifest)
    lineage = manifest.get("derived_from")
    if not isinstance(lineage, list):
        lineage = []
    for i, parent in enumerate(lineage):
        if natural_identity(parent) == me:
            v.append(Violation("SELF_LINEAGE",
                               "il dataset dichiara di derivare da se stesso",
                               f"derived_from[{i}]"))
    return v


def check_partition_manifest(partition, dataset=None):
    """Invarianti interne a un partition-manifest, e coerenza col suo dataset."""
    v = []

    # 2. event bounds OSSERVATI ordinati, sui valori parsati. Sono i
    #    timestamp del primo e dell'ultimo record, NON la copertura dichiarata:
    #    quella sta nel coverage-manifest e non si deriva da qui.
    start = _parse_ts(partition.get("first_exchange_ts"))
    end = _parse_ts(partition.get("last_exchange_ts"))
    if start is not None and end is not None and start > end:
        v.append(Violation(
            "TS_OUT_OF_ORDER",
            f"first_exchange_ts {partition['first_exchange_ts']} successivo a "
            f"last_exchange_ts {partition['last_exchange_ts']}",
            "first_exchange_ts"))

    # 3. confini di sequence ordinati, numericamente
    first = _parse_seq(partition.get("first_sequence"))
    last = _parse_seq(partition.get("last_sequence"))
    if first is not None and last is not None and first > last:
        v.append(Violation(
            "SEQUENCE_OUT_OF_ORDER",
            f"first_sequence {partition['first_sequence']} maggiore di "
            f"last_sequence {partition['last_sequence']} (confronto numerico)",
            "first_sequence"))

    # 4. rel_path dentro la partizione
    key = partition.get("partition_key")
    rel = partition.get("rel_path")
    if key and rel and not rel.startswith(key + "/"):
        v.append(Violation(
            "REL_PATH_OUTSIDE_PARTITION",
            f"rel_path {rel!r} non inizia con partition_key {key + '/'!r}",
            "rel_path"))

    # 5. identita' coerente col dataset in cui vive
    if dataset is not None:
        if natural_identity(partition) != natural_identity(dataset):
            v.append(Violation(
                "IDENTITY_MISMATCH",
                f"identita' della partizione {natural_identity(partition)} "
                f"diversa da quella del dataset {natural_identity(dataset)}",
                "identity"))

    # 13. row_count 0 implica event bounds osservati null. Il JSON Schema
    # congelato impone il verso opposto (row_count>0 -> bounds tipizzati) ma
    # non vieta un valore non-null quando row_count e' 0: un simile documento
    # affermerebbe implicitamente "zero record, ma eccone comunque uno primo e
    # uno ultimo", una contraddizione interna che first_exchange_ts/
    # last_exchange_ts null-quando-vuoto (gia' descritto nel campo) rende
    # rilevabile solo qui, incrociando i due campi.
    if partition.get("row_count") == 0:
        if partition.get("first_exchange_ts") is not None:
            v.append(Violation(
                "ZERO_ROW_OBSERVED_BOUNDS",
                "row_count e' 0 ma first_exchange_ts non e' null: una "
                "partizione senza record non puo' avere un primo evento "
                "osservato",
                "first_exchange_ts"))
        if partition.get("last_exchange_ts") is not None:
            v.append(Violation(
                "ZERO_ROW_OBSERVED_BOUNDS",
                "row_count e' 0 ma last_exchange_ts non e' null: una "
                "partizione senza record non puo' avere un ultimo evento "
                "osservato",
                "last_exchange_ts"))
    return v


def validate(dataset_manifest, partition_manifests=(), coverage_manifests=()):
    """Tutte le invarianti su un dataset, le sue partizioni e la sua copertura.

    Con coverage_manifests non vuoto verifica anche la ricostruibilita' di
    catalog.partitions.ts_start/ts_end: senza quella, il catalogo non e'
    ricostruibile dai manifest e la sua ricostruibilita' dichiarata sarebbe
    una promessa non mantenuta.
    """
    v = list(check_dataset_manifest(dataset_manifest))
    for partition in partition_manifests:
        v.extend(check_partition_manifest(partition, dataset_manifest))
    for coverage in coverage_manifests:
        v.extend(check_coverage_manifest(coverage, dataset_manifest))
    if coverage_manifests:
        _, coverage_violations = reconstruct_catalog_coverage(
            coverage_manifests, partition_manifests)
        v.extend(coverage_violations)
    return v


# --------------------------------------------------------------------------
# copertura DICHIARATA (coverage-manifest-v1)
#
# La copertura dichiarata NON si deriva:
#   * dagli event bounds osservati (first/last_exchange_ts): quelli dicono
#     quando sono arrivati i record, non per quale intervallo il produttore
#     stava osservando la sorgente;
#   * dal partition_key: 'dt=2024-01-15' identifica la partizione, non impone
#     una giornata UTC, finche' un contratto di partizionamento non lo dice.
# Si legge dalle asserzioni di un coverage-manifest, e basta.
# --------------------------------------------------------------------------

# Evidenze che CONTRADDICONO una dichiarazione di completezza: se il trasporto
# si e' interrotto o la sequence sorgente ha saltato dei valori, l'intervallo
# non e' completo, comunque lo si voglia raccontare.
EVIDENCE_AGAINST_COMPLETE = frozenset(
    ("transport_interruption", "sequence_discontinuity"))


def _interval(doc, start_key="start", end_key="end"):
    return _parse_ts(doc.get(start_key)), _parse_ts(doc.get(end_key))


def _overlaps(a_start, a_end, b_start, b_end):
    """Half-open [start, end): l'adiacenza a_end == b_start NON e' overlap."""
    return a_start < b_end and b_start < a_end


def _merge(intervals):
    """Unione half-open di intervalli ordinati. L'adiacenza fonde."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def check_coverage_manifest(coverage, dataset=None):
    """Invarianti interne a un coverage-manifest, e coerenza col suo dataset."""
    v = []

    intent_start, intent_end = _interval(coverage.get("acquisition") or {},
                                         "intent_start", "intent_end")

    # 7. l'intervallo di acquisizione e' half-open e non degenere
    if intent_start is not None and intent_end is not None and intent_start >= intent_end:
        v.append(Violation(
            "COVERAGE_INTENT_INVALID",
            f"intent_start {coverage['acquisition']['intent_start']} non precede "
            f"intent_end {coverage['acquisition']['intent_end']}: "
            f"[start, end) e' half-open e non puo' essere vuoto",
            "acquisition"))
        # un intento gia' invalido non e' un metro con cui misurare le
        # asserzioni: continuare produrrebbe una violazione derivata per
        # ognuna, seppellendo la causa sotto i suoi effetti
        intent_start = intent_end = None

    spans = []
    seen_assertion_ids = {}
    for i, assertion in enumerate(coverage.get("assertions") or []):
        where = f"assertions[{i}]"

        # 14. assertion_id e' l'identita' STABILE dell'asserzione: la
        # posizione nell'array non lo e'. Unica dentro QUESTO documento, non
        # oltre: due coverage-manifest distinti possono riusare lo stesso
        # valore senza collidere, cosi' come coverage_id.
        aid = assertion.get("assertion_id")
        if aid is not None:
            if aid in seen_assertion_ids:
                v.append(Violation(
                    "ASSERTION_ID_DUPLICATE",
                    f"assertion_id {aid!r} gia' usato da assertions"
                    f"[{seen_assertion_ids[aid]}]: l'identita' non e' unica "
                    f"dentro il documento",
                    where))
            else:
                seen_assertion_ids[aid] = i

        start, end = _interval(assertion)
        if start is None or end is None:
            continue

        # 8. ogni intervallo dichiarato e' half-open e non degenere
        if start >= end:
            v.append(Violation(
                "COVERAGE_INTERVAL_INVALID",
                f"start {assertion['start']} non precede end {assertion['end']}: "
                f"una dichiarazione vuota non asserisce nulla",
                where))
            continue

        # 9. non si certifica piu' di quanto si sia tentato di acquisire
        if intent_start is not None and intent_end is not None:
            if start < intent_start or end > intent_end:
                v.append(Violation(
                    "COVERAGE_OUTSIDE_INTENT",
                    f"l'asserzione [{assertion['start']}, {assertion['end']}) esce "
                    f"dall'intervallo di acquisizione dichiarato",
                    where))

        # 10. lo stato non puo' contraddire l'evidenza che lo accompagna
        if assertion.get("status") == "complete":
            against = sorted({e.get("kind") for e in assertion.get("evidence") or []}
                             & EVIDENCE_AGAINST_COMPLETE)
            if against:
                v.append(Violation(
                    "COVERAGE_STATUS_CONTRADICTS_EVIDENCE",
                    f"dichiarata 'complete' portando evidenza {against}: "
                    f"un'interruzione di trasporto o un salto di sequence "
                    f"escludono la completezza",
                    where))

        spans.append((start, end, i))

    # 11. dentro UN documento le asserzioni non si sovrappongono: due verdetti
    # diversi sullo stesso istante sarebbero una contraddizione, non evidenza.
    ordered = sorted(spans)
    for (a_start, a_end, a_i), (b_start, b_end, b_i) in zip(ordered, ordered[1:]):
        if _overlaps(a_start, a_end, b_start, b_end):
            v.append(Violation(
                "COVERAGE_ASSERTIONS_OVERLAP",
                f"le asserzioni {a_i} e {b_i} si sovrappongono nello stesso documento",
                f"assertions[{b_i}]"))

    # 12. identita' coerente col dataset in cui vive
    if dataset is not None:
        if natural_identity(coverage) != natural_identity(dataset):
            v.append(Violation(
                "COVERAGE_IDENTITY_MISMATCH",
                f"identita' del coverage-manifest {natural_identity(coverage)} "
                f"diversa da quella del dataset {natural_identity(dataset)}",
                "identity"))
    return v


# Codici che rendono l'INTERO grafo di supersessione inaffidabile: quando
# anche uno solo compare, nessun documento del set e' pubblicabile. Fail
# closed, mai una precedenza per created_at, ordine di array o di
# elaborazione: solo l'arco esplicito 'supersedes'.
COVERAGE_STRUCTURAL_CODES = frozenset((
    "COVERAGE_ID_DUPLICATE",
    "COVERAGE_SUPERSESSION_SELF",
    "COVERAGE_SUPERSESSION_CYCLE",
    "COVERAGE_SUPERSESSION_CONFLICT",
    "COVERAGE_SUPERSEDES_UNKNOWN",
))

# Stati di partition-manifest idonei a ricevere l'attribuzione di
# un'asserzione 'complete'. 'writing' non e' ancora sigillato, 'invalid' non
# va usato, 'superseded' e' stato rimpiazzato: nessuno dei tre e' un bersaglio
# affidabile per una dichiarazione di completezza.
_ELIGIBLE_ATTRIBUTION_STATES = frozenset(("closed", "valid", "degraded"))


def _validate_supersession_graph(coverage_manifests):
    """Valida il grafo di supersessione e determina i documenti VIVI.

    Ritorna (violazioni, vivi_o_None). ``vivi`` e' None quando il grafo non e'
    valido (id duplicato, auto-supersessione, ciclo, ramificazione o
    riferimento pendente): nessun documento di questo insieme e' allora
    pubblicabile, perche' non esiste un modo affidabile di scegliere chi e'
    vivo. Il calcolo dipende solo dalla FORMA del grafo (l'insieme di coppie
    coverage_id/supersedes), mai dall'ORDINE con cui i documenti sono stati
    passati: due permutazioni dello stesso insieme producono sempre lo stesso
    risultato.
    """
    v = []

    # id duplicati: i documenti coinvolti sono esclusi dal grafo PER INTERO,
    # cosi' che nessuna loro proprieta' (incluso 'supersedes') possa
    # influenzare il risultato a seconda di quale copia l'iterazione incontra
    # prima.
    seen_once = set()
    duplicated_ids = set()
    for doc in coverage_manifests:
        cid = doc.get("coverage_id")
        if cid in seen_once:
            duplicated_ids.add(cid)
        else:
            seen_once.add(cid)
    for cid in sorted(duplicated_ids, key=str):
        v.append(Violation(
            "COVERAGE_ID_DUPLICATE",
            f"coverage_id {cid!r} usato da piu' di un documento: "
            f"la supersessione non saprebbe quale sostituire",
            str(cid)))

    by_id = {doc.get("coverage_id"): doc for doc in coverage_manifests
             if doc.get("coverage_id") not in duplicated_ids}

    # auto-supersessione
    for cid in sorted((c for c, doc in by_id.items()
                       if doc.get("supersedes") == c), key=str):
        v.append(Violation(
            "COVERAGE_SUPERSESSION_SELF",
            f"{cid!r} supersede se stesso",
            str(cid)))

    # archi validi: niente self-loop (gia' segnalati) e niente target ignoti
    # (segnalati qui, come riferimento pendente)
    edges = {}
    for cid, doc in sorted(by_id.items(), key=lambda kv: str(kv[0])):
        target = doc.get("supersedes")
        if target is None or target == cid:
            continue
        if target not in by_id:
            v.append(Violation(
                "COVERAGE_SUPERSEDES_UNKNOWN",
                f"supersede {target!r}, che non e' fra i documenti forniti: "
                f"l'evidenza sostituita non e' verificabile",
                str(cid)))
            continue
        edges[cid] = target

    # ramificazione: piu' documenti dichiarano di sostituire lo stesso target
    reverse = {}
    for cid, target in edges.items():
        reverse.setdefault(target, []).append(cid)
    for target in sorted(reverse, key=str):
        supersessors = sorted(reverse[target], key=str)
        if len(supersessors) > 1:
            v.append(Violation(
                "COVERAGE_SUPERSESSION_CONFLICT",
                f"{target!r} e' superseduto da piu' di un documento: "
                f"{supersessors}",
                str(target)))

    # ciclicita': un percorso lungo 'supersedes' che rivisita un nodo prima di
    # uscire dal grafo (cammino finito perche' by_id e' finito).
    cycle_members = set()
    for start in sorted(edges, key=str):
        path = []
        cur = start
        while cur in edges:
            if cur in path:
                cycle_members.update(path[path.index(cur):])
                break
            path.append(cur)
            cur = edges[cur]
    if cycle_members:
        v.append(Violation(
            "COVERAGE_SUPERSESSION_CYCLE",
            f"ciclo di supersessione fra {sorted(cycle_members, key=str)}: "
            f"seguendolo alla lettera ogni documento coinvolto risulterebbe "
            f"superseduto, e quindi silenziosamente eliminato",
            ""))

    if any(item.code in COVERAGE_STRUCTURAL_CODES for item in v):
        return v, None

    # grafo valido: nessuna supersessione puo' restringere il dominio di
    # responsabilita' di cio' che sostituisce. La supersessione e' SOSTITUZIONE
    # INTEGRALE (I10): chi supersede deve RISTABILIRE per intero cio' che il
    # documento sostituito copriva, non solo una porzione. Restringere non e'
    # un rischio da segnalare e pubblicare comunque: e' un fallimento della
    # lineage. Ne' la finestra vecchia (A, ormai dichiarata sostituita) ne'
    # quella nuova e piu' stretta (B, che non ha mantenuto la promessa di
    # sostituzione integrale) sono affidabili, quindi ENTRAMBI gli estremi
    # dell'arco vengono esclusi dai documenti vivi: nessuna copertura viene
    # pubblicata da questa specifica lineage. Una lineage diversa, senza
    # alcun arco verso questi due documenti, non ne risente.
    narrowing_endpoints = set()
    for cid, target in sorted(edges.items(), key=lambda kv: str(kv[0])):
        old_start, old_end = _interval(by_id[target].get("acquisition") or {},
                                       "intent_start", "intent_end")
        new_start, new_end = _interval(by_id[cid].get("acquisition") or {},
                                       "intent_start", "intent_end")
        if None not in (old_start, old_end, new_start, new_end):
            if new_start > old_start or new_end < old_end:
                v.append(Violation(
                    "COVERAGE_SUPERSESSION_NARROWS",
                    f"supersede {target!r} con una finestra piu' stretta: "
                    f"la sostituzione non e' integrale, quindi ne' {target!r} "
                    f"ne' {cid!r} sono pubblicabili da questa lineage",
                    str(cid)))
                narrowing_endpoints.add(cid)
                narrowing_endpoints.add(target)

    superseded = set(edges.values())
    live_ids = sorted((set(by_id) - superseded) - narrowing_endpoints, key=str)
    live = [by_id[cid] for cid in live_ids]
    return v, live


def _validate_partition_inputs(partition_manifests):
    """Verifica esistenza e unicita' dei partition-manifest forniti.

    Ritorna (by_ref, famiglie_non_pubblicabili, violazioni).

    ``by_ref`` mappa (partition_key, revision) -> lista dei manifest con
    quella identita' naturale: piu' di uno e' gia' un'ambiguita' irrisolvibile
    (quale dei due e' il file vero?), segnalata qui.

    ``famiglie_non_pubblicabili`` e' l'insieme dei soli ``partition_key`` la
    cui intera famiglia di revisioni e' inaffidabile: duplicata, oppure con
    piu' di una revisione non-superseded senza che nessuna supersessione
    esplicita lo risolva. Nessuna revisione di quelle chiavi puo' ricevere
    copertura, indipendentemente da cosa dicono i coverage-manifest: la
    catalog.partitions_one_live ammette una sola revisione viva per chiave,
    quindi 'quale revisione e' viva' deve essere gia' inequivocabile nei soli
    partition-manifest forniti, prima ancora di guardare la copertura.
    """
    v = []
    by_ref = {}
    for doc in partition_manifests:
        ref = (doc.get("partition_key"), doc.get("revision"))
        by_ref.setdefault(ref, []).append(doc)

    unpublishable_keys = set()

    for ref in sorted((r for r, docs in by_ref.items() if len(docs) > 1),
                      key=str):
        v.append(Violation(
            "PARTITION_MANIFEST_DUPLICATE",
            f"{len(by_ref[ref])} partition-manifest per {ref}: riferimento "
            f"ambiguo, nessuna revisione di questa chiave e' pubblicabile",
            f"{ref[0]}@rev{ref[1]}"))
        unpublishable_keys.add(ref[0])

    by_key = {}
    for (key, revision), docs in by_ref.items():
        by_key.setdefault(key, []).append((revision, docs[0]))

    for key in sorted(by_key, key=str):
        live_revisions = sorted(
            revision for revision, doc in by_key[key]
            if doc.get("state") != "superseded")
        if len(live_revisions) > 1:
            v.append(Violation(
                "PARTITION_LIVE_REVISION_CONFLICT",
                f"{len(live_revisions)} revisioni non-superseded per "
                f"partition_key {key!r}: {live_revisions}. Senza una "
                f"supersessione esplicita non e' determinabile quale sia "
                f"quella viva",
                key))
            unpublishable_keys.add(key)

    return by_ref, unpublishable_keys, v


def reconstruct_catalog_coverage(coverage_manifests, partition_manifests=()):
    """coverage-manifest durevoli -> catalog.partitions.ts_start / ts_end.

    Restituisce (coverage_per_partizione, violazioni), dove le chiavi sono
    l'identita' NATURALE (partition_key, revision) e i valori sono l'intervallo
    half-open dichiarato. Deterministica: gli stessi documenti danno sempre lo
    stesso risultato, che e' cio' che rende il catalogo ricostruibile.

    Solo le asserzioni 'complete' contribuiscono. 'uncertain' e 'known_gap' non
    coprono nulla, per costruzione: un buco resta un buco anche quando nessun
    evento sarebbe stato atteso.

    Opera su UNA sola DatasetIdentity per chiamata: se i documenti forniti
    (di entrambe le famiglie) ne attraversano piu' d'una, la chiamata fallisce
    chiusa senza tentare alcuna attribuzione, perche' un coverage_id o un
    partition_key possono coincidere per puro caso fra dataset distinti e
    farli interagire sarebbe un errore silenzioso.
    """
    identities = {natural_identity(doc) for doc in coverage_manifests}
    identities |= {natural_identity(doc) for doc in partition_manifests}
    if len(identities) > 1:
        return {}, [Violation(
            "RECONSTRUCTION_DATASET_MIXED",
            f"l'input attraversa piu' di una DatasetIdentity: "
            f"{sorted(identities, key=str)}. reconstruct_catalog_coverage "
            f"opera su una sola identita' naturale per chiamata",
            "")]

    by_ref, unpublishable_full_keys, partition_violations = \
        _validate_partition_inputs(partition_manifests)
    graph_violations, live = _validate_supersession_graph(coverage_manifests)
    v = list(partition_violations) + list(graph_violations)

    if live is None:
        return {}, v

    attributed = {}
    non_complete = []
    for doc in live:
        for assertion in doc.get("assertions") or []:
            start, end = _interval(assertion)
            if start is None or end is None or start >= end:
                continue
            if assertion.get("status") != "complete":
                non_complete.append((start, end, doc.get("coverage_id")))
                continue
            for ref in assertion.get("partitions") or []:
                key = (ref.get("partition_key"), ref.get("revision"))
                attributed.setdefault(key, []).append((start, end))

    coverage = {}
    unpublishable = set()
    for key, intervals in sorted(attributed.items(), key=lambda kv: str(kv[0])):
        where = f"{key[0]}@rev{key[1]}"

        # B2: la famiglia di revisioni di questa chiave e' gia' inaffidabile
        # (duplicata o con piu' di una revisione viva), indipendentemente da
        # cosa dice la copertura.
        if key[0] in unpublishable_full_keys:
            v.append(Violation(
                "PARTITION_NOT_PUBLISHABLE",
                f"la famiglia di revisioni per {key[0]!r} non e' affidabile "
                f"(duplicata o con piu' di una revisione viva): nessuna "
                f"copertura viene pubblicata",
                where))
            unpublishable.add(key)
            continue

        # B2: l'asserzione 'complete' deve risolvere a ESATTAMENTE un
        # partition-manifest esistente.
        ref_docs = by_ref.get(key)
        if not ref_docs:
            v.append(Violation(
                "PARTITION_UNKNOWN",
                f"nessun partition-manifest per {key}: un'asserzione "
                f"'complete' non puo' attribuire copertura a una "
                f"partizione che non esiste",
                where))
            unpublishable.add(key)
            continue

        # B2: e deve essere in uno stato idoneo a riceverla.
        state = ref_docs[0].get("state")
        if state not in _ELIGIBLE_ATTRIBUTION_STATES:
            v.append(Violation(
                "PARTITION_NOT_ELIGIBLE",
                f"lo stato {state!r} non e' idoneo a ricevere una "
                f"copertura 'complete' dichiarata",
                where))
            unpublishable.add(key)
            continue

        merged = _merge(intervals)
        if len(merged) > 1:
            # Il catalogo ha UNA sola coppia (ts_start, ts_end) per partizione,
            # e partitions_one_live ammette una sola revisione viva per
            # partition_key: pubblicare l'intervallo esterno dichiarerebbe
            # coperto anche il buco interno. Va detto ad alta voce, non risolto
            # qui inventando una regola.
            v.append(Violation(
                "COVERAGE_NOT_CONTIGUOUS",
                f"la copertura completa attribuita alla partizione e' "
                f"{[(_format_ts(a), _format_ts(b)) for a, b in merged]}: "
                f"piu' di un intervallo "
                f"non e' pubblicabile in una sola riga di catalogo",
                where))
            unpublishable.add(key)
            continue
        start, end = merged[0]

        # una dichiarazione non-completa che intersechi la copertura completa
        # e' una contraddizione fra documenti vivi. Non c'e' precedenza da
        # scegliere fra le due affermazioni opposte: la chiave contestata
        # fallisce chiusa, non solo segnalata. Non si pubblica ne' un
        # "sottoinsieme sicuro" ne' l'intervallo intero: la contraddizione
        # rende l'intera attribuzione a questa chiave inaffidabile.
        contradicted = False
        for gap_start, gap_end, cid in non_complete:
            if _overlaps(start, end, gap_start, gap_end):
                v.append(Violation(
                    "COVERAGE_CONTRADICTION",
                    f"l'intervallo dichiarato completo interseca "
                    f"[{_format_ts(gap_start)}, {_format_ts(gap_end)}) "
                    f"dichiarato non coperto da {cid!r}",
                    where))
                contradicted = True
        if contradicted:
            unpublishable.add(key)
            continue
        coverage[key] = (start, end)

    v.extend(_check_observed_within_declared(
        coverage, partition_manifests, unpublishable, unpublishable_full_keys))
    return coverage, v


def _check_observed_within_declared(coverage, partition_manifests,
                                    unpublishable=frozenset(),
                                    unpublishable_full_keys=frozenset()):
    """Gli event bounds OSSERVATI devono stare dentro la copertura DICHIARATA.

    Regola, con confine destro STRETTO perche' la copertura e' [start, end):
        first_exchange_ts >= ts_start   e   last_exchange_ts < ts_end

    Due conseguenze volute:

    1. Il bug 'copio i bounds osservati in ts_start/ts_end' viene respinto
       sempre, perche' produce ts_end == last_exchange_ts e quindi l'ultimo
       record cade FUORI dalla copertura dichiarata.
    2. Un record fuori dalla copertura dichiarata renderebbe il risultato di
       DataGateway dipendente da come e' inquadrata la richiesta: il pruning
       seleziona le partizioni con ts_end > start AND ts_start < end, mentre
       il reader filtra sull'intervallo RICHIESTO. Lo stesso record sarebbe
       visibile o invisibile a seconda della domanda. Non e' un dettaglio di
       qualita': e' non-determinismo.

    Non si classifica in silenzio e non si allarga la copertura da soli: o il
    produttore dichiara (con evidenza) una copertura che contiene il record,
    oppure il record non appartiene a questa partizione.
    """
    v = []
    for partition in partition_manifests:
        key = (partition.get("partition_key"), partition.get("revision"))
        where = f"{key[0]}@rev{key[1]}"
        declared = coverage.get(key)
        if declared is None:
            # la copertura c'era ma non e' pubblicabile: la causa e' gia' stata
            # detta una volta (COVERAGE_NOT_CONTIGUOUS, PARTITION_NOT_ELIGIBLE,
            # PARTITION_NOT_PUBLISHABLE, ...), ripeterla come "manca" sarebbe
            # fuorviante
            if key in unpublishable or key[0] in unpublishable_full_keys:
                continue
            if partition.get("state") in ("writing", "invalid", "superseded"):
                continue
            v.append(Violation(
                "COVERAGE_MISSING_FOR_PARTITION",
                "nessuna asserzione 'complete' attribuisce copertura a questa "
                "partizione: senza di essa ts_start/ts_end non sono ricostruibili "
                "e la partizione non e' pubblicabile",
                where))
            continue
        if not partition.get("row_count"):
            continue
        ts_start, ts_end = declared
        first = _parse_ts(partition.get("first_exchange_ts"))
        last = _parse_ts(partition.get("last_exchange_ts"))
        if first is not None and first < ts_start:
            v.append(Violation(
                "OBSERVED_OUTSIDE_DECLARED",
                f"first_exchange_ts {partition['first_exchange_ts']} precede "
                f"ts_start {_format_ts(ts_start)}: il record e' fuori dalla "
                f"copertura dichiarata",
                where))
        if last is not None and last >= ts_end:
            v.append(Violation(
                "OBSERVED_OUTSIDE_DECLARED",
                f"last_exchange_ts {partition['last_exchange_ts']} non precede "
                f"ts_end {_format_ts(ts_end)}: la copertura e' half-open, quindi l'ultimo "
                f"record cade fuori. Copiare i bounds osservati nella copertura "
                f"dichiarata fallisce sempre qui, ed e' voluto",
                where))
    return v
