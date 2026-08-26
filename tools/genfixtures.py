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

print(f"dataset-manifest : {len(list(dv.glob('valid-*.json')))} valide, "
      f"{len(list(di.glob('*.json')))} negative")
print(f"partition-manifest: {len(list(pv.glob('valid-*.json')))} valide, "
      f"{len(list(pi.glob('*.json')))} negative")
