#!/usr/bin/env python3
"""Real PostgreSQL proof for L2 publication certification and S14 eligibility (A14c).

Proves, against the unmodified catalog DDL, that a canonical ``l2`` partition of
``l2-book-event-v1`` is sealed by ``CatalogPublicationWriter``, certified by
``L2PublicationCertification`` and published ``valid`` by the unchanged
``PublicationEligibilityBridge``; that the S14 retry is idempotent; and that a
partition whose events are not one clean continuity segment is certified ``fail``
and refused by the bridge.

Run it on a fresh disposable database (it refuses a database that already holds
the fixed Bybit BTCUSDT L2 dataset)::

    DATA_GATEWAY_TEST_DSN="host=127.0.0.1 port=55441 dbname=<fresh> user=postgres" \\
        python tests/integration_l2_publication_postgres.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import psycopg  # noqa: E402

from bootstrap_schema_registry import (  # noqa: E402
    bootstrap_schema_registry,
    read_schema_registration,
)
from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.l2_materializer import materialize_l2_book_event_v1  # noqa: E402
from quant_platform.data.l2_publication import L2PublicationCertification  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.models import L2BookEvent, L2LevelChange  # noqa: E402
from quant_platform.data.publication import SealedPartitionEvidence  # noqa: E402
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
    PublicationEligibilityRefusal,
)
from quant_platform.data.publication_eligibility_catalog import (  # noqa: E402
    PublicationEligibilityCatalog,
)
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
    BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
    BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
    bybit_l2_continuity_evidence_detail,
)

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
RAW = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
CHANNEL = "orderbook.200.BTCUSDT"


def up(side, price, size="1"):
    return L2LevelChange(side, price, size, "upsert")


def rm(side, price):
    return L2LevelChange(side, price, "0", "delete")


def event(day, seq, u, kind="delta", *, bids=(), asks=()):
    base = Instant.parse(f"{day}T00:00:01Z").epoch_ns
    return L2BookEvent(
        venue="bybit", market_type="linear_perp", instrument="BTCUSDT",
        native_symbol="BTCUSDT", source_channel=CHANNEL, acquisition_mode="historical_archive",
        event_type=kind, exchange_ts=Instant(base + seq * 1_000),
        provider_ts=Instant(base + seq * 1_000 + 2_000_000), source_depth_limit=200,
        native_sequence=str(seq), native_update_id=str(u),
        bids=bids or (up("bid", "100.0", "5"),), asks=asks,
    )


def clean_day(day):
    return [
        event(day, 100, 50, "snapshot", bids=(up("bid", "100.0", "5"), up("bid", "99.0", "7")),
              asks=(up("ask", "101.0", "4"), up("ask", "102.0", "9"))),
        event(day, 101, 51, bids=(up("bid", "100.5", "2"),)),
        event(day, 150, 52, asks=(rm("ask", "102.0"),)),
    ]


def write_partition(root, dataset_path, day, next_day, events, profile, tag):
    key = f"dt={day}"
    artifact = root / key / "part-001.parquet"
    report = profile.certify(IDENTITY, events)
    materialization = materialize_l2_book_event_v1(
        artifact, events, dataset_identity=IDENTITY, ordering_provider=BYBIT_L2_ORDERING_PROVIDER
    )
    partition_path = root / f"partition-{tag}.json"
    partition = emit_partition_manifest(
        partition_path, materialization, dataset_identity=IDENTITY, dataset_root=root,
        partition_key=key, revision=1, rel_path=f"{key}/part-001.parquet",
        created_at="2026-10-16T10:00:00Z", closed_at="2026-10-16T10:00:01Z",
        producer="integration-l2-producer", code_ref="integration-l2-producer-commit",
    ).document
    coverage_path = root / f"coverage-{tag}.json"
    emit_coverage_manifest(
        coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
        coverage_id=f"integration-l2-coverage-{tag}", supersedes=None,
        created_at="2026-10-16T10:00:02Z",
        acquisition={
            "basis": BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
            "intent_start": f"{day}T00:00:00Z", "intent_end": f"{next_day}T00:00:00Z",
            "source_semantics": BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
            "mapping": BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
        },
        assertions=[{
            "assertion_id": f"integration-l2-assertion-{tag}",
            "start": f"{day}T00:00:00Z", "end": f"{next_day}T00:00:00Z", "status": "complete",
            "partitions": [{"partition_key": key, "revision": 1}],
            "evidence": [
                {"kind": "archive_completeness", "detail": "integration archive day"},
                {"kind": "sequence_continuity", "detail": bybit_l2_continuity_evidence_detail(report)},
            ],
        }],
        producer="integration-l2-source", code_ref="integration-l2-source-commit",
        partition_manifests=[partition],
    )
    return (
        SealedPartitionEvidence(dataset_path, partition_path, (coverage_path,), artifact, "l2-hot"),
        partition_path,
        coverage_path,
        materialization,
    )


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL L2 publication integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    profile = BybitL2BookEventV1CertificationProfile("integration-l2-certifier")
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        dataset_path = root / "dataset.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=IDENTITY, created_at="2026-10-16T10:00:00Z",
            derived_from=[RAW], transform="canonicalize-l2-book-events-v1",
        )
        good, good_partition, good_coverage, good_mat = write_partition(
            root, dataset_path, "2024-01-15", "2024-01-16", clean_day("2024-01-15"), profile, "good"
        )
        bad_events = clean_day("2024-01-16") + [
            event("2024-01-16", 151, 90), event("2024-01-16", 152, 91)  # u jumps 52 -> 90
        ]
        bad, bad_partition, bad_coverage, _ = write_partition(
            root, dataset_path, "2024-01-16", "2024-01-17", bad_events, profile, "bad"
        )

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
                    "VALUES ('l2-hot',%s,'hot') ON CONFLICT DO NOTHING",
                    # The catalog requires a POSIX-absolute path of safe characters;
                    # here it is only metadata (S13 reads the artifact through the
                    # evidence path), so a fixed placeholder keeps the test portable.
                    ("/srv/l2-integration-hot",),
                )
                cursor.execute(
                    "INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) "
                    "VALUES ('raw','l2','bybit','BTCUSDT','raw/l2/bybit/BTCUSDT/l2-book-event-v1',"
                    "'l2-book-event-v1',%s) ON CONFLICT DO NOTHING",
                    ("1" * 64,),
                )
            connection.commit()

            writer = CatalogPublicationWriter(connection)
            runtime = L2PublicationCertification(writer, profile)

            # 1. clean partition: seal -> certify -> record, all categories pass
            run = runtime.run(good)
            assert run.certification.status == "pass", run.certification.violations
            assert {c.category: c.status for c in run.certification.categories} == {
                "source": "pass", "canonical": "pass", "physical": "pass",
                "manifests": "pass", "coverage": "pass", "publication": "deferred",
            }
            assert run.quality_report is not None and run.quality_report.status == "pass"
            sealed = run.sealed_partition
            assert sealed.state == "closed"
            assert run.certification.metrics["physical_artifact_hash"] == good_mat.sha256
            assert (
                run.certification.metrics["canonical_content_hash_v1"]
                == good_mat.canonical_content_hash_v1
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT d.layer, d.kind, d.schema_id, d.rel_root FROM catalog.datasets d "
                    "WHERE d.dataset_id = %s", (sealed.dataset_id,)
                )
                assert cursor.fetchone() == (
                    "canonical", "l2", "l2-book-event-v1", "canonical/l2/bybit/BTCUSDT/l2-book-event-v1"
                )

            # 2. S14 publishes it valid through the unchanged bridge; retry is idempotent
            evidence = PublicationEligibilityEvidence(
                dataset_path, good_partition, (good_coverage,), "l2-hot",
                profile.profile_id, profile.check_suite,
            )
            bridge = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection))
            result = bridge.publish(evidence)
            assert result.state == "valid" and result.certification_status == "pass", result
            assert result.partition_id == sealed.partition_id
            assert bridge.publish(evidence) == result
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state, row_count FROM catalog.partitions WHERE partition_id=%s",
                    (result.partition_id,),
                )
                assert cursor.fetchone() == ("valid", 3)
                cursor.execute(
                    "SELECT count(*) FROM catalog.dataset_lineage WHERE child_id=%s", (result.dataset_id,)
                )
                assert cursor.fetchone()[0] == 1
                cursor.execute(
                    "SELECT count(*) FROM catalog.quality_reports WHERE partition_id=%s AND check_suite=%s",
                    (result.partition_id, profile.check_suite),
                )
                assert cursor.fetchone()[0] == 1

            # 3. a partition with a u discontinuity certifies fail and is refused
            bad_run = runtime.run(bad)
            assert bad_run.certification.status == "fail"
            assert bad_run.quality_report is not None and bad_run.quality_report.status == "fail"
            assert any(
                v["category"] == "canonical" and "not publishable" in v["message"]
                for v in bad_run.certification.violations
            )
            try:
                bridge.publish(
                    PublicationEligibilityEvidence(
                        dataset_path, bad_partition, (bad_coverage,), "l2-hot",
                        profile.profile_id, profile.check_suite,
                    )
                )
            except PublicationEligibilityRefusal:
                pass
            else:
                raise AssertionError("a degraded L2 partition was published")
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute(
                        "SELECT state FROM catalog.partitions WHERE partition_id=%s",
                        (bad_run.sealed_partition.partition_id,),
                    )
                    assert cursor.fetchone()[0] == "closed"
                    cursor.execute(
                        "SELECT state FROM catalog.partitions WHERE partition_id=%s",
                        (result.partition_id,),
                    )
                    assert cursor.fetchone()[0] == "valid"

    print("POSTGRESQL L2 PUBLICATION GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
