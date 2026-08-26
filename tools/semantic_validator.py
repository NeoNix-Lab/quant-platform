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
  2. first_exchange_ts <= last_exchange_ts        (valori TEMPORALI parsati)
  3. first_sequence   <= last_sequence            (valori NUMERICI)
  4. partition.rel_path inizia con partition_key + '/'
  5. identita' della partizione == identita' del dataset
  6. nessuna self-lineage: un dataset non deriva da se stesso
"""

from datetime import datetime, timezone
import string

__all__ = [
    "Violation", "encode_instrument", "decode_instrument", "derive_rel_root",
    "natural_identity", "check_dataset_manifest", "check_partition_manifest",
    "validate",
]

# Caratteri che restano letterali nel percent-encoding. Il '%' NON e' fra
# questi: e' cio' che rende la mappa iniettiva.
SAFE = frozenset(string.ascii_letters + string.digits + "._-")


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
def _parse_ts(value):
    """RFC 3339 con 'Z' -> datetime aware. Il confronto usa SEMPRE il valore
    parsato: '...:11Z' e '...:11.000Z' sono lo stesso istante ma stringhe
    diverse, e ordinarle lessicograficamente darebbe il risultato sbagliato."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_seq(value):
    """Stringa decimale canonica -> int. Il confronto e' NUMERICO: come stringhe
    '9' risulterebbe maggiore di '10'."""
    return None if value is None else int(value)


# --------------------------------------------------------------------------
# controlli
# --------------------------------------------------------------------------
def check_dataset_manifest(manifest):
    """Invarianti interne a un dataset-manifest."""
    v = []

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
    for i, parent in enumerate(manifest.get("derived_from") or []):
        if natural_identity(parent) == me:
            v.append(Violation("SELF_LINEAGE",
                               "il dataset dichiara di derivare da se stesso",
                               f"derived_from[{i}]"))
    return v


def check_partition_manifest(partition, dataset=None):
    """Invarianti interne a un partition-manifest, e coerenza col suo dataset."""
    v = []

    # 2. copertura temporale ordinata, sui valori parsati
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
    return v


def validate(dataset_manifest, partition_manifests=()):
    """Tutte le invarianti su un dataset e le sue partizioni."""
    v = list(check_dataset_manifest(dataset_manifest))
    for partition in partition_manifests:
        v.extend(check_partition_manifest(partition, dataset_manifest))
    return v
