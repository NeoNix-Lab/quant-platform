#!/usr/bin/env python3
"""Test del semantic validator.

Per ogni invariante: un caso che deve passare e uno che deve essere respinto,
col codice atteso. Verifica anche che le fixture VALIDE dei due manifest, che
per costruzione sono coerenti, non producano violazioni.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from semantic_validator import (  # noqa: E402
    check_dataset_manifest, check_partition_manifest, derive_rel_root,
    encode_instrument, decode_instrument, natural_identity, validate,
)

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

DATASET = {
    "schema_version": "dataset-manifest-v1", "layer": "canonical",
    "dataset_kind": "trades", "venue": "bybit", "instrument": "BTC/USD",
    "record_schema_id": "trade-v1",
    "rel_root": "canonical/trades/bybit/BTC%2FUSD/trade-v1",
    "derived_from": [{"layer": "raw", "dataset_kind": "trades",
                      "venue": "bybit", "instrument": "BTC/USD",
                      "record_schema_id": "trade-v1"}],
    "transform": "canonicalize-trades-v1",
    "created_at": "2026-08-01T00:00:00Z",
}

V2_SOURCE_ACQUIRED = {
    "schema_version": "dataset-manifest-v2", "layer": "canonical",
    "dataset_kind": "trades", "venue": "bybit", "instrument": "BTC/USD",
    "record_schema_id": "trade-v1",
    "rel_root": "canonical/trades/bybit/BTC%2FUSD/trade-v1",
    "origin": "source_acquired", "transform": "canonicalize-trades-v1",
    "created_at": "2026-08-01T00:00:00Z",
}

V2_DATASET_DERIVED = {
    **V2_SOURCE_ACQUIRED,
    "origin": "dataset_derived",
    "derived_from": [DATASET["derived_from"][0]],
}

PARTITION = {
    "schema_version": "partition-manifest-v1", "layer": "canonical",
    "dataset_kind": "trades", "venue": "bybit", "instrument": "BTC/USD",
    "record_schema_id": "trade-v1",
    "partition_key": "dt=2026-08-25", "revision": 1, "state": "valid",
    "rel_path": "dt=2026-08-25/part-000.parquet",
    "file_size_bytes": 1, "row_count": 1,
    "sha256": "a" * 64,
    "first_exchange_ts": "2026-08-25T00:00:00Z",
    "last_exchange_ts": "2026-08-25T23:59:59Z",
    "first_sequence": "9007199254740993",
    "last_sequence": "9007199256583769",
    "created_at": "2026-08-25T00:00:00Z",
    "closed_at": "2026-08-26T00:00:00Z",
    "producer": "trades-canonicalizer", "code_ref": "4f1a9c3",
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


def variant(base, **changes):
    d = dict(base)
    d.update(changes)
    return d


print("\n1. rel_root == rel_root derivato dall'identita'")
expect([], check_dataset_manifest(DATASET), "rel_root coerente")
expect(["REL_ROOT_MISMATCH"],
       check_dataset_manifest(variant(DATASET, rel_root="canonical/trades/bybit/BTC-USD/trade-v1")),
       "rel_root con instrument non encodato respinto")
expect(["REL_ROOT_MISMATCH"],
       check_dataset_manifest(variant(DATASET, record_schema_id="trade-v2")),
       "rel_root non aggiornato al cambio di record_schema_id respinto")
expect(["REL_ROOT_UNDERIVABLE"],
       check_dataset_manifest({k: v for k, v in DATASET.items() if k != "venue"}),
       "identita' incompleta segnalata invece di crashare")

print("\n2. first_exchange_ts <= last_exchange_ts (valori parsati)")
expect([], check_partition_manifest(PARTITION), "copertura ordinata")
expect(["TS_OUT_OF_ORDER"],
       check_partition_manifest(variant(PARTITION,
                                        first_exchange_ts="2026-08-25T23:59:59Z",
                                        last_exchange_ts="2026-08-25T00:00:00Z")),
       "copertura invertita respinta")
expect([], check_partition_manifest(variant(PARTITION,
                                            first_exchange_ts="2026-08-25T00:00:00Z",
                                            last_exchange_ts="2026-08-25T00:00:00.000Z")),
       "stesso istante con precisioni diverse accettato (confronto non lessicografico)")
expect(["TS_OUT_OF_ORDER"],
       check_partition_manifest(variant(PARTITION,
                                        first_exchange_ts="2026-08-25T00:00:00.500Z",
                                        last_exchange_ts="2026-08-25T00:00:00.100Z")),
       "inversione nei soli decimali rilevata")
expect([], check_partition_manifest(variant(PARTITION, first_exchange_ts=None,
                                            last_exchange_ts=None)),
       "partizione vuota senza copertura accettata")

print("\n3. first_sequence <= last_sequence (numericamente)")
expect(["SEQUENCE_OUT_OF_ORDER"],
       check_partition_manifest(variant(PARTITION, first_sequence="100",
                                        last_sequence="99")),
       "sequence invertite respinte")
expect([], check_partition_manifest(variant(PARTITION, first_sequence="9",
                                            last_sequence="10")),
       "9 <= 10 accettato (lessicograficamente sarebbe il contrario)")
expect(["SEQUENCE_OUT_OF_ORDER"],
       check_partition_manifest(variant(PARTITION, first_sequence="10",
                                        last_sequence="9")),
       "10 > 9 respinto (lessicograficamente passerebbe)")
expect([], check_partition_manifest(
    variant(PARTITION, first_sequence="9007199254740993",
            last_sequence="9007199254740994")),
       "confronto esatto oltre 2^53")

print("\n4. rel_path dentro la partizione")
expect(["REL_PATH_OUTSIDE_PARTITION"],
       check_partition_manifest(variant(PARTITION, rel_path="dt=2026-08-24/part-000.parquet")),
       "rel_path di un altro giorno respinto")
expect(["REL_PATH_OUTSIDE_PARTITION"],
       check_partition_manifest(variant(PARTITION, rel_path="part-000.parquet")),
       "rel_path senza prefisso di partizione respinto")
expect(["REL_PATH_OUTSIDE_PARTITION"],
       check_partition_manifest(variant(PARTITION,
                                        partition_key="dt=2026-08-2",
                                        rel_path="dt=2026-08-25/part-000.parquet")),
       "prefisso parziale non basta: serve il separatore")
expect([], check_partition_manifest(
    variant(PARTITION, partition_key="dt=2026-08-26/hour=14",
            rel_path="dt=2026-08-26/hour=14/part-000.parquet")),
       "partition_key composta accettata")

print("\n5. identita' partizione == identita' dataset")
expect([], check_partition_manifest(PARTITION, DATASET), "identita' coerenti")
expect(["IDENTITY_MISMATCH"],
       check_partition_manifest(variant(PARTITION, venue="coinbase"), DATASET),
       "venue diversa respinta")
expect(["IDENTITY_MISMATCH"],
       check_partition_manifest(variant(PARTITION, record_schema_id="trade-v2"), DATASET),
       "record_schema_id diverso respinto")
expect(["IDENTITY_MISMATCH"],
       check_partition_manifest(variant(PARTITION, instrument="BTC-USD"), DATASET),
       "instrument che differisce solo per il separatore respinto")

print("\n6. no self-lineage")
expect(["SELF_LINEAGE"],
       check_dataset_manifest(variant(DATASET, derived_from=[
           {"layer": "canonical", "dataset_kind": "trades", "venue": "bybit",
            "instrument": "BTC/USD", "record_schema_id": "trade-v1"}])),
       "dataset che deriva da se stesso respinto")
expect([], check_dataset_manifest(DATASET), "derivazione da un altro layer accettata")

print("\n7. encoding: iniettivo e reversibile")
for value in ["BTC/USD", "BTC-USD", "btc-usd", "BTC%USD", "XBT/USD", "BTC€USD"]:
    enc = encode_instrument(value)
    if decode_instrument(enc) == value:
        print(f"  {GREEN}PASS{OFF} {value!r} -> {enc} -> {value!r}")
    else:
        print(f"  {RED}FAIL{OFF} {value!r} non reversibile")
        fail.append(f"reversibilita {value}")

print("\n8. le fixture valide non producono violazioni")
dsets = sorted((ROOT / "fixtures" / "dataset-manifest-v1").glob("valid-*.json"))
for path in dsets:
    doc = json.loads(path.read_text(encoding="utf-8"))
    v = check_dataset_manifest(doc)
    if v:
        print(f"  {RED}FAIL{OFF} {path.name}: {v}")
        fail.append(path.name)
    else:
        print(f"  {GREEN}PASS{OFF} {path.name}")

parts = sorted((ROOT / "fixtures" / "partition-manifest-v1").glob("valid-*.json"))
for path in parts:
    doc = json.loads(path.read_text(encoding="utf-8"))
    v = check_partition_manifest(doc)
    if v:
        print(f"  {RED}FAIL{OFF} {path.name}: {v}")
        fail.append(path.name)
    else:
        print(f"  {GREEN}PASS{OFF} {path.name}")

print("\n9. validate() aggrega dataset e partizioni")
v = validate(DATASET, [PARTITION, variant(PARTITION, venue="coinbase")])
expect(["IDENTITY_MISMATCH"], v, "una partizione incoerente su due segnalata")

print("\n10. dataset-manifest-v2 dispatcha la topologia senza reinterpretare v1")
expect([], check_dataset_manifest(V2_SOURCE_ACQUIRED), "source_acquired v2 senza lineage accettato")
expect([], check_dataset_manifest(V2_DATASET_DERIVED), "dataset_derived v2 con genitore accettato")
expect(["MISSING_ORIGIN"], check_dataset_manifest(
    {key: value for key, value in V2_SOURCE_ACQUIRED.items() if key != "origin"}
), "canonical v2 senza origin respinto")
expect(["UNSUPPORTED_DATASET_MANIFEST_VERSION"], check_dataset_manifest(
    variant(V2_SOURCE_ACQUIRED, schema_version="dataset-manifest-v9")
), "schema version non supportato respinto")
expect(["LINEAGE_REQUIRED"], check_dataset_manifest(
    variant(DATASET, derived_from=[])
), "canonical v1 senza genitore continua a essere respinto")

print()
if fail:
    print(f"{RED}FAIL{OFF}: {len(fail)} controlli non superati")
    for name in fail:
        print(f"  - {name}")
    sys.exit(1)
print(f"{GREEN}PASS{OFF}: tutte le invarianti semantiche verificate")
