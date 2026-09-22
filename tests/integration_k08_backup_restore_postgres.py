#!/usr/bin/env python3
"""Real-PostgreSQL proof for K08 backup/restore v1 isolated restore.

Run explicitly against a PostgreSQL database initialized by ``db/init``:

    DATA_GATEWAY_TEST_DSN=postgresql://... python tests/integration_k08_backup_restore_postgres.py

It does not run as part of the local script runner because it requires
external database infrastructure, exactly like the other
``integration_*_postgres.py`` files in this directory.

What this proves, against a real PostgreSQL catalog and a real filesystem
target (not a fake writer/catalog):

- one finalized publication seals through the unmodified
  ``CatalogPublicationWriter``/``PublicationCertification`` path;
- K08 captures a deterministic ``RecoverySetV1`` and exports it to an
  explicit backup destination without touching primary state;
- restoring into a brand-new isolated filesystem target, re-admitted through
  a distinct ``catalog.storage_roots`` row (inserted the same way any real
  third disk/NAS would be -- adding a row never touches another table, see
  ``db/init/001_catalog.sql``), reproduces the sealed identities/coverage;
- the real ``access.catalog.Catalog``/``access.gateway.DataGateway`` resolve
  and read the restored partition from the restored target.

What this does NOT prove: that ``hot`` and the inserted restore storage root
are genuinely on independent physical storage on the server this DSN points
at.  ``storage_roots.device_uuid`` differing is recorded as evidence below,
but whether that value reflects real distinct hardware on this deployment is
an operational fact this script cannot observe -- see ADR-0039 Sec. 2 and the
``DEPLOYMENT_INDEPENDENCE_PROOF_PENDING`` status this run reports.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import psycopg  # noqa: E402

from bootstrap_schema_registry import bootstrap_schema_registry, read_schema_registration  # noqa: E402

from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application.backup_restore import (  # noqa: E402
    capture_recovery_set,
    export_recovery_set,
    restore_recovery_set,
)
from quant_platform.data import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest, emit_partition_manifest  # noqa: E402
from quant_platform.data.publication import CatalogPublicationWriter, PublicationCertification, SealedPartitionEvidence  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"


def trade(timestamp: str, trade_id: str) -> TradeRecord:
    return TradeRecord("bybit", "BTCUSDT", Instant.parse(timestamp), "100.00", "0.5000", "buy", None, trade_id, None)


def write_evidence(root: Path, dataset_root: Path, dataset_path: Path) -> SealedPartitionEvidence:
    artifact = dataset_root / "dt=2024-01-15" / "part-000.parquet"
    partition_path = root / "partition.json"
    coverage_path = root / "coverage.json"
    materialization = materialize_bybit_trade_v1(
        artifact, [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")],
        dataset_identity=IDENTITY,
    )
    partition = emit_partition_manifest(
        partition_path, materialization, dataset_identity=IDENTITY, dataset_root=dataset_root,
        partition_key="dt=2024-01-15", revision=1, rel_path="dt=2024-01-15/part-000.parquet",
        created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
        producer="k08-integration-materializer", code_ref="k08-integration-producer",
    )
    emit_coverage_manifest(
        coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
        coverage_id="k08-integration-coverage-1", supersedes=None, created_at="2026-09-01T10:00:02Z",
        acquisition={
            "basis": "source_extract", "intent_start": START, "intent_end": END,
            "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
        },
        assertions=[{
            "assertion_id": "k08-integration-assertion-1", "start": START, "end": END, "status": "complete",
            "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
            "evidence": [{"kind": "deterministic_source_extract", "detail": "integration source"}],
        }], producer="k08-integration-source", code_ref="k08-integration-source",
        partition_manifests=[partition.document],
    )
    return SealedPartitionEvidence(dataset_path, partition_path, (coverage_path,), artifact, "hot")


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL K08 backup/restore integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0

    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder) / "primary"
        rel_root = "canonical/trades/bybit/BTCUSDT/trade-v1"
        dataset_root = root / rel_root
        dataset_path = root / "dataset.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=IDENTITY, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        evidence = write_evidence(root, dataset_root, dataset_path)

        connection = psycopg.connect(dsn)
        restore_storage_root_id = None
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' "
                    "AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError("integration database already contains the fixed first-vertical dataset")
            bootstrap_schema_registry(connection, read_schema_registration(ROOT / "schemas" / "trade-v1.json"))
            connection.commit()

            # Register the restore target's storage root exactly the way a
            # real third disk/NAS would be added: one new storage_roots row,
            # touching no other table (db/init/001_catalog.sql's own
            # documented design).  A fresh random device_uuid is explicit
            # evidence this row is *declared* distinct from 'hot' -- whether
            # that declaration matches real deployed hardware on this DSN's
            # server is exactly the fact this script cannot observe.
            restore_root = Path(holder) / "restored"
            restore_storage_root_id = "k08-integration-restore"
            restore_device_uuid = str(uuid.uuid4())
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO storage_roots (storage_root_id, tier, abs_path, device_uuid, description) "
                    "VALUES (%s, 'cold', %s, %s, 'K08 integration proof isolated restore target')",
                    (restore_storage_root_id, str(restore_root.resolve()), restore_device_uuid),
                )
                cursor.execute("SELECT device_uuid FROM storage_roots WHERE storage_root_id = 'hot'")
                primary_device_uuid = cursor.fetchone()[0]
            connection.commit()
            assert primary_device_uuid != restore_device_uuid, (
                "restore storage root must be declared distinct from primary 'hot'"
            )

            certification = PublicationCertification(
                CatalogPublicationWriter(connection), BybitTradeV1CertificationProfile("k08-integration-certifier"),
            )
            run = certification.run(evidence)
            assert run.sealed_partition.state == "closed"
            assert run.certification.status == "pass"

            recovery_set = capture_recovery_set(evidence, run.sealed_partition)
            backup_root = Path(holder) / "backup"
            export = export_recovery_set(recovery_set, evidence, backup_root)

            restored = restore_recovery_set(
                export, restore_root,
                forbidden_roots=[root, backup_root],
                catalog_writer=CatalogPublicationWriter(connection),
                storage_root_id=restore_storage_root_id,
            )
            assert restored.sealed.natural_identity == run.sealed_partition.natural_identity
            assert restored.sealed.content_sha256 == run.sealed_partition.content_sha256
            assert restored.sealed.manifest_sha256 == run.sealed_partition.manifest_sha256
            assert restored.sealed.ts_start == run.sealed_partition.ts_start
            assert restored.sealed.ts_end == run.sealed_partition.ts_end
            assert restored.sealed.partition_id != run.sealed_partition.partition_id, (
                "restore must not silently reuse the primary catalog partition row"
            )

            # Promote to 'valid' so LifecyclePolicy.VALID_ONLY can read it,
            # mirroring how a real historical read would be configured --
            # K08 never invents its own lifecycle-promotion path.
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE catalog.partitions SET state = 'valid' WHERE partition_id = %s",
                    (restored.sealed.partition_id,),
                )
                cursor.execute(
                    "UPDATE catalog.partitions SET state = 'valid' WHERE partition_id = %s",
                    (run.sealed_partition.partition_id,),
                )
            connection.commit()

            catalog = Catalog(connection=connection)
            gateway = DataGateway(catalog, ordering_providers=(BYBIT_ORDERING_PROVIDER,))
            request = DataRequest(
                IDENTITY, START, END,
                lifecycle_policy=LifecyclePolicy.VALID_ONLY,
                ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            )
            result = gateway.read(request)
            assert [record.trade_id for record in result.records] == ["1", "2"]
            assert restored.sealed.partition_id in result.metadata.catalog_partition_ids
            assert run.sealed_partition.partition_id in result.metadata.catalog_partition_ids

            print("PASS K08 backup/restore v1 PostgreSQL isolated restore proof")
            print(
                "DEPLOYMENT_INDEPENDENCE_PROOF_PENDING: storage_roots device_uuid values differ "
                "as declared evidence; independent physical storage on this server is unverified "
                "by this script."
            )
            return 0
        finally:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM catalog.quality_reports WHERE partition_id IN "
                    "(SELECT partition_id FROM catalog.partitions WHERE dataset_id IN "
                    "(SELECT dataset_id FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' "
                    "AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'))"
                )
                cursor.execute(
                    "DELETE FROM catalog.partitions WHERE dataset_id IN "
                    "(SELECT dataset_id FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' "
                    "AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1')"
                )
                cursor.execute(
                    "DELETE FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' "
                    "AND venue = 'bybit' AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
                )
                if restore_storage_root_id is not None:
                    cursor.execute(
                        "DELETE FROM storage_roots WHERE storage_root_id = %s", (restore_storage_root_id,)
                    )
            connection.commit()
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
