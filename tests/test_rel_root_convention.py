#!/usr/bin/env python3
"""Convenzione rel_root: derivazione deterministica dall'identita' naturale.

    <layer>/<kind>/<venue>/<instrument_path>
            [/<feature_set_slug>/v<feature_set_version>]
            /<record_schema_id>

Il segmento fra parentesi compare se e solo se layer e' 'features'.

record_schema_id e' l'ULTIMO segmento perche' fa parte dell'identita' naturale:
due dataset identici salvo la versione di schema dei record devono finire in
directory diverse, altrimenti trade-v1 e trade-v2 si sovrascriverebbero.

instrument_path e' un PERCENT-ENCODING CANONICO del valore nativo, iniettivo e
reversibile. Non normalizza il case ne' la punteggiatura: sono differenze
semantiche, non rumore. Sostituisce la precedente slugification lossy, che
mandava 'BTC/USD' e 'BTC-USD' sulla stessa stringa.

Uscita: 0 se tutto conforme, 1 altrimenti.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# UNICA source of truth della convenzione: il semantic validator. Qui non se ne
# tiene una copia, altrimenti test e produzione potrebbero divergere proprio
# sulla funzione che definisce l'identita' dei dataset.
from semantic_validator import (  # noqa: E402
    SAFE, encode_instrument, decode_instrument, derive_rel_root,
)

FIXTURES = ROOT / "fixtures" / "dataset-manifest-v1"

GREEN, RED, DIM, OFF = "\x1b[32m", "\x1b[31m", "\x1b[90m", "\x1b[0m"

PATH_SHAPE = re.compile(r"^[A-Za-z0-9._=%-]+(/[A-Za-z0-9._=%-]+)*$")
BAD_ESCAPE = re.compile(r"%(?![0-9A-F]{2})")
ENCODED_DOT = re.compile(r"%2[Ee]")
CANONICAL_ID = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")


def main():
    fail = []
    ok = lambda msg: print(f"  {GREEN}PASS{OFF} {msg}")

    def check(cond, msg):
        if cond:
            ok(msg)
        else:
            print(f"  {RED}FAIL{OFF} {msg}")
            fail.append(msg)

    print("\nconvenzione: <layer>/<kind>/<venue>/<instrument_path>"
          "[/<feature_set_slug>/v<version>]/<record_schema_id>")

    # --- 1. le fixture valide rispettano la convenzione --------------------
    fixtures = sorted(FIXTURES.glob("valid-*.json"))
    if not fixtures:
        sys.exit(f"nessuna fixture valid-*.json in {FIXTURES}")
    print(f"\n{len(fixtures)} fixture: rel_root dichiarato == rel_root derivato")
    for path in fixtures:
        doc = json.loads(path.read_text(encoding="utf-8"))
        want = derive_rel_root(doc)
        if doc["rel_root"] == want:
            print(f"  {GREEN}PASS{OFF} {path.name}")
            print(f"       {DIM}{want}{OFF}")
        else:
            print(f"  {RED}FAIL{OFF} {path.name}")
            print(f"       dichiarato: {doc['rel_root']}")
            print(f"       derivato:   {want}")
            fail.append(path.name)

    # --- 2. i casi obbligatori: nessuna collisione -------------------------
    print("\ncasi obbligatori — nessuna collisione:")
    for a, b in [("BTC/USD", "BTC-USD"), ("BTC-USD", "btc-usd"),
                 ("BTC/USD", "btc-usd"), ("BTC%USD", "BTC-USD"),
                 ("BTC%2FUSD", "BTC/USD"), ("BTC%25USD", "BTC%USD")]:
        ea, eb = encode_instrument(a), encode_instrument(b)
        check(ea != eb, f"{a!r} -> {ea}   !=   {b!r} -> {eb}")

    # --- 3. iniettivita su un corpus ---------------------------------------
    corpus = ["BTCUSDT", "btcusdt", "BTC-USD", "btc-usd", "BTC/USD", "btc/usd",
              "BTC%USD", "BTC%2FUSD", "BTC%25USD", "BTC.PERP", "ETH_USDT",
              "BTC USD", "BTC:USD", "BTC/USD:USDT", "XBT/USD", "BTC=USD",
              "PERP/BTC-USD", "PERP-BTC/USD", "BTC€USD", "市場/BTC"]
    encoded = [encode_instrument(v) for v in corpus]
    print(f"\niniettivita su {len(corpus)} valori distinti:")
    check(len(set(encoded)) == len(corpus),
          f"{len(set(encoded))} encoding distinti da {len(corpus)} input distinti")

    # --- 4. reversibilita ---------------------------------------------------
    print("\nreversibilita:")
    broken = [v for v in corpus if decode_instrument(encode_instrument(v)) != v]
    check(not broken, "decode(encode(x)) == x per ogni valore del corpus")
    if broken:
        for v in broken:
            print(f"       {v!r} -> {encode_instrument(v)!r} -> "
                  f"{decode_instrument(encode_instrument(v))!r}")

    # --- 5. l output e path-safe e canonico ---------------------------------
    print("\nforma dell'output:")
    check(all(PATH_SHAPE.fullmatch(e) for e in encoded),
          "ogni encoding rispetta il dominio dei componenti di path")
    check(not any(BAD_ESCAPE.search(e) for e in encoded),
          "ogni '%' e seguito da due cifre esadecimali MAIUSCOLE")
    check(not any(ENCODED_DOT.search(e) for e in encoded),
          "nessun encoding produce '%2E' (il '.' resta letterale)")
    check(not any("/" in e for e in encoded),
          "nessun encoding introduce '/': non puo nascere un componente di piu")
    check(encode_instrument("../../etc") == "..%2F..%2Fetc",
          "un instrument con traversal viene neutralizzato dall'encoding")

    # --- 6. gli altri componenti sono gia path-safe -------------------------
    print("\ncomponenti non-instrument: identificatori canonici:")
    for value in ["trade-v1", "trade-v2", "footprint-v1", "l2-v1",
                  "feature-set-v1", "trade_microstructure",
                  "footprint_microstructure", "bybit", "coinbase", "kraken"]:
        check(bool(CANONICAL_ID.fullmatch(value)), f"{value}")
    for bad in ["Trade-V1", "trade/v1", "trade v1", "trade..v1", "-trade", "trade-"]:
        check(not CANONICAL_ID.fullmatch(bad), f"respinto: {bad!r}")

    # --- 7. ogni componente dell identita discrimina ------------------------
    base = {"layer": "canonical", "dataset_kind": "trades", "venue": "bybit",
            "instrument": "BTCUSDT", "record_schema_id": "trade-v1"}
    base_root = derive_rel_root(base)
    print(f"\ncambiando UN componente, rel_root cambia:\n  {DIM}base: {base_root}{OFF}")
    for field, variant in [
        ("record_schema_id", dict(base, record_schema_id="trade-v2")),
        ("layer",            dict(base, layer="raw")),
        ("dataset_kind",     dict(base, dataset_kind="l2")),
        ("venue",            dict(base, venue="coinbase")),
        ("instrument",       dict(base, instrument="ETHUSDT")),
        ("instrument case",  dict(base, instrument="btcusdt")),
    ]:
        got = derive_rel_root(variant)
        check(got != base_root, f"{field:<18} -> {got}")

    feat = {"layer": "features", "dataset_kind": "trade_microstructure",
            "venue": "bybit", "instrument": "BTCUSDT",
            "feature_set_slug": "trade_microstructure", "feature_set_version": 1,
            "record_schema_id": "feature-set-v1"}
    print("\nfeature set: slug e versione discriminano:")
    for field, variant in [
        ("feature_set_version", dict(feat, feature_set_version=2)),
        ("feature_set_slug",    dict(feat, feature_set_slug="other-measure")),
        ("record_schema_id",    dict(feat, record_schema_id="feature-set-v2")),
    ]:
        check(derive_rel_root(variant) != derive_rel_root(feat),
              f"{field:<20} -> {derive_rel_root(variant)}")

    # --- 8. purezza ---------------------------------------------------------
    print("\ndeterminismo:")
    check(derive_rel_root(base) == derive_rel_root(dict(base)) == base_root,
          "stessa identita', stesso rel_root")

    print()
    if fail:
        print(f"{RED}FAIL{OFF}: {len(fail)} controlli non superati")
        for name in fail:
            print(f"  - {name}")
        return 1
    print(f"{GREEN}PASS{OFF}: rel_root deterministico, iniettivo, reversibile e path-safe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
