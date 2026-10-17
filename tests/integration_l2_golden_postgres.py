#!/usr/bin/env python3
"""Real PostgreSQL proof for the L2 v1 golden harness (A14 closeout).

Runs ``run_l2_golden`` with a catalog DSN on a synthetic ``ob200`` day with one ``u``
discontinuity: both runs are sealed, certified and published ``valid`` and the whole
day is read back through ``L2DataGateway`` and the ``l2_book_events@1`` consumer read.
Run it on a fresh disposable database::

    DATA_GATEWAY_TEST_DSN="host=127.0.0.1 port=55441 dbname=<fresh> user=postgres" \
        python tests/integration_l2_golden_postgres.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

import test_bybit_l2_day_ingest_v1 as fixtures  # noqa: E402
from bootstrap_schema_registry import (  # noqa: E402
    bootstrap_schema_registry,
    read_schema_registration,
)
from quant_platform.application.l2_golden import run_l2_golden  # noqa: E402


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL L2 golden integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        archive = fixtures.make_archive(root, fixtures.DAY, fixtures.ONE_GAP)
        dataset_root = root / "canonical" / "l2" / "bybit" / "BTCUSDT" / "l2-book-event-v1"

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM catalog.datasets WHERE layer='canonical' AND kind='l2' "
                    "AND venue='bybit' AND instrument='BTCUSDT' AND schema_id='l2-book-event-v1'"
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError("integration database already contains the fixed L2 dataset")
            bootstrap_schema_registry(
                connection, read_schema_registration(ROOT / "schemas" / "l2-book-event-v1.json")
            )
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) "
                    "VALUES ('l2-hot','/srv/l2-integration-hot','hot') ON CONFLICT DO NOTHING"
                )
                cursor.execute(
                    "INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) "
                    "VALUES ('raw','l2','bybit','BTCUSDT','raw/l2/bybit/BTCUSDT/l2-book-event-v1',"
                    "'l2-book-event-v1',%s) ON CONFLICT DO NOTHING", ("1" * 64,),
                )
            connection.commit()

        # the catalog stores a POSIX placeholder storage root; the artifacts live in a
        # temporary directory, so the physical path is resolved by an injected resolver
        def resolver(storage_root, dataset_rel_root, rel_path):
            return dataset_root / Path(*rel_path.split("/"))

        params = dict(
            day=fixtures.DAY, dataset_root=dataset_root, created_at="2026-10-17T10:00:00Z",
            closed_at="2026-10-17T10:00:01Z", producer="integration-l2-golden",
            code_ref="integration-l2-golden-commit", dsn=dsn, storage_root_id="l2-hot",
            path_resolver=resolver,
        )
        evidence = run_l2_golden(archive, **params)
        claims = {c["claim"]: c for c in evidence["claims"]}
        failed = [c for c in evidence["claims"] if not c["ok"]]
        assert evidence["ok"] and not failed, failed
        for key in (f"dt={fixtures.DAY}/seg=001", f"dt={fixtures.DAY}/seg=002"):
            assert claims[f"certified_pass:{key}"]["ok"], claims
            assert claims[f"published_valid:{key}"]["detail"] == "valid", claims
        for name in (
            "read_events_in_partition_ranges", "read_hash_equals_partition_hash",
            "read_event_count", "read_gaps_equal_gap_documents", "read_starts_anchored",
            "consumer_read_window_equals_scan",
        ):
            assert claims[name]["ok"], (name, claims[name])
        assert claims["read_event_count"]["detail"] == {"read": 6, "stored": 6}
        assert [r["state"] for r in evidence["catalog"]["partitions"]] == ["valid", "valid"]
        assert [r["row_count"] for r in evidence["catalog"]["partitions"]] == [3, 3]
        assert len(evidence["catalog"]["read"]["gaps"]) == 3

        # a tampered artifact must not read back as the published day
        artifact = next(dataset_root.rglob("*.parquet"))
        data = bytearray(artifact.read_bytes())
        data[len(data) // 2] ^= 0xFF
        artifact.write_bytes(bytes(data))
        try:
            tampered = run_l2_golden(archive, reproduce=False, **params)
        except Exception as exc:  # the reader / ingest refuses the damaged artifact
            print(f"tampered artifact refused: {type(exc).__name__}")
        else:
            assert not tampered["ok"], "a tampered artifact still passed every claim"
            print("tampered artifact failed a claim:", [c["claim"] for c in tampered["claims"] if not c["ok"]])

    print("POSTGRESQL L2 GOLDEN GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
