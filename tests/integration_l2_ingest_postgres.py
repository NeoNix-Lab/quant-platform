#!/usr/bin/env python3
"""Real PostgreSQL proof for the Bybit L2 day ingest (A14d).

A synthetic ``ob200`` archive day with one ``u`` discontinuity is ingested (cut into
two runs, ADR-0070 Amendment 1), and both runs are sealed, certified and published
``valid`` through the unchanged S13/S14 path.  The catalog must carry exactly the
folded intervals the ingest planned.  Run it on a fresh disposable database::

    DATA_GATEWAY_TEST_DSN="host=127.0.0.1 port=55441 dbname=<fresh> user=postgres" \
        python tests/integration_l2_ingest_postgres.py
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
from quant_platform.application.bybit_l2_day_ingest import ingest_bybit_l2_day  # noqa: E402
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.l2_publication import L2PublicationCertification  # noqa: E402
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
)
from quant_platform.data.publication_eligibility_catalog import (  # noqa: E402
    PublicationEligibilityCatalog,
)
from quant_platform.source_adapters.bybit_l2 import BybitL2BookEventV1CertificationProfile  # noqa: E402


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL L2 ingest integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    profile = BybitL2BookEventV1CertificationProfile("integration-l2-ingest-certifier")
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        archive = fixtures.make_archive(root, fixtures.DAY, fixtures.ONE_GAP)
        dataset_root = root / "canonical" / "l2" / "bybit" / "BTCUSDT" / "l2-book-event-v1"
        params = dict(
            day=fixtures.DAY, dataset_root=dataset_root, created_at="2026-10-17T10:00:00Z",
            closed_at="2026-10-17T10:00:01Z", producer="integration-l2-ingest",
            code_ref="integration-l2-ingest-commit", storage_root_id="l2-hot",
        )
        result = ingest_bybit_l2_day(archive, **params)
        assert [p.partition_key for p in result.partitions] == [
            f"dt={fixtures.DAY}/seg=001", f"dt={fixtures.DAY}/seg=002"
        ]
        assert len(result.gaps) == 1 and result.excluded_events == 2

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

            writer = CatalogPublicationWriter(connection)
            bridge = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection))
            published = []
            for partition in result.partitions:
                run = L2PublicationCertification(writer, profile).run(partition.evidence)
                assert run.certification.status == "pass", run.certification.violations
                outcome = bridge.publish(
                    PublicationEligibilityEvidence(
                        partition.dataset_manifest_path, partition.partition_manifest_path,
                        (partition.coverage_manifest_path,), "l2-hot",
                        profile.profile_id, profile.check_suite,
                    )
                )
                assert outcome.state == "valid", outcome
                published.append(outcome)

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT partition_key, state, row_count, ts_start, ts_end "
                    "FROM catalog.partitions WHERE dataset_id=%s ORDER BY partition_key",
                    (published[0].dataset_id,),
                )
                rows = cursor.fetchall()
            assert [row[0] for row in rows] == [p.partition_key for p in result.partitions]
            assert [row[1] for row in rows] == ["valid", "valid"]
            assert [row[2] for row in rows] == [3, 3]
            for row, partition in zip(rows, result.partitions):
                assert Instant.parse(row[3]) == Instant.parse(partition.complete_start), (row, partition)
                assert Instant.parse(row[4]) == Instant.parse(partition.complete_end), (row, partition)
            # the gap between the two published intervals is exactly the gap document's
            gap = result.gaps[0]
            assert Instant.parse(rows[0][4]) == Instant.parse(gap.start)
            assert Instant.parse(rows[1][3]) == Instant.parse(gap.end)

        # re-acquisition of the identical archive is a no-op and the catalog is unchanged
        again = ingest_bybit_l2_day(archive, **params)
        assert again.already_published and len(again.partitions) == 2

    print("POSTGRESQL L2 INGEST GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
