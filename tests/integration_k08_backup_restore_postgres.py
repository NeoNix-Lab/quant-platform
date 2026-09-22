#!/usr/bin/env python3
"""Real-PostgreSQL proof for K08 backup/restore v1 isolated restore.

Minimum invocation (no real topology evidence -- everything lives under one
``TemporaryDirectory`` and one catalog, exactly like the hermetic tests):

    DATA_GATEWAY_TEST_DSN=postgresql://... python tests/integration_k08_backup_restore_postgres.py

It does not run as part of the local script runner because it requires
external database infrastructure, exactly like the other
``integration_*_postgres.py`` files in this directory.

Real deployment-independence proof requires the operator to supply actual
distinct mount points and, ideally, an actually separate/empty catalog
database, via these optional environment variables:

    K08_PRIMARY_ROOT              absolute path on the primary storage device
    K08_BACKUP_ROOT                absolute path on the backup storage device
    K08_RESTORE_ROOT                absolute path on the restore storage device
    K08_RESTORE_STORAGE_ROOT_ID     an ALREADY-REGISTERED catalog.storage_roots
                                     row (real abs_path == K08_RESTORE_ROOT,
                                     real device_uuid distinct from 'hot');
                                     this script never inserts a row when this
                                     is supplied, it only verifies one
    DATA_GATEWAY_TEST_RESTORE_DSN   a second PostgreSQL database -- genuinely
                                     separate from DATA_GATEWAY_TEST_DSN's --
                                     used as an empty isolated catalog for the
                                     restore side; without this, restore
                                     re-uses the primary's own catalog
                                     instance (only the storage *locator* is
                                     isolated, not the catalog database)

Any root left unset falls back to a subdirectory of one process-local
``TemporaryDirectory``, which is explicitly NOT topology evidence and is
reported as such.  ``K08_RESTORE_STORAGE_ROOT_ID`` left unset falls back to
inserting a placeholder ``storage_roots`` row with a random ``device_uuid``
-- also explicitly not evidence, only a mechanically distinct locator.

What this proves in every invocation, against a real PostgreSQL catalog and
a real filesystem target (never a fake writer/catalog):

- one finalized publication seals through the unmodified
  ``CatalogPublicationWriter``/``PublicationCertification`` path;
- K08 captures a deterministic ``RecoverySetV1`` and exports it to an
  explicit, durable, independently reloadable backup without touching
  primary state;
- restoring into a brand-new isolated filesystem target, whose registered
  ``catalog.storage_roots`` locator is verified to resolve to exactly that
  target, reproduces the sealed identities/coverage;
- the real ``access.catalog.Catalog``/``access.gateway.DataGateway`` resolve
  and read the restored partition from the restored target -- and, because
  only the restored row is ever promoted to ``valid``, a ``VALID_ONLY``
  historical read can only be satisfied by the restored partition, not by a
  surviving primary row (never a read that merely proves fallback/union
  across both).

What is proven only when the corresponding environment variable is actually
supplied to real distinct hardware/database: physical storage independence
(``K08_RESTORE_STORAGE_ROOT_ID`` with real, operator-verified topology) and
catalog-instance independence (``DATA_GATEWAY_TEST_RESTORE_DSN``).  Absent
those, this script prints ``DEPLOYMENT_INDEPENDENCE_PROOF_PENDING`` and says
exactly which axis remains unproven -- see ADR-0039 Sec. 2.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import uuid
from contextlib import ExitStack

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
DATASET_WHERE = (
    "layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' "
    "AND instrument = 'BTCUSDT' AND schema_id = 'trade-v1'"
)


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


def _resolve_root(env_var: str, holder: Path, subdir: str, evidence: list[str]) -> Path:
    override = os.environ.get(env_var)
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    evidence.append(
        f"{env_var} was not supplied; {subdir} falls back to a subdirectory of one "
        "process-local TemporaryDirectory, which is NOT topology evidence."
    )
    path = holder / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prepare_restore_storage_root(connection, restore_root: Path, evidence: list[str]) -> tuple[str, str, bool]:
    """Return (storage_root_id, abs_path, inserted_by_this_script)."""

    declared_id = os.environ.get("K08_RESTORE_STORAGE_ROOT_ID")
    if declared_id:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT abs_path, device_uuid FROM storage_roots WHERE storage_root_id = %s",
                (declared_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError(
                f"K08_RESTORE_STORAGE_ROOT_ID={declared_id!r} does not name an already-"
                "registered storage_roots row; this script never inserts a row when this "
                "variable is supplied, since the whole point is operator-verified real "
                "topology, not a script-generated placeholder"
            )
        abs_path, device_uuid = row
        if Path(abs_path).resolve() != restore_root.resolve():
            raise RuntimeError(
                f"storage_roots {declared_id!r}.abs_path ({abs_path}) does not match "
                f"K08_RESTORE_ROOT ({restore_root}); fix the registered row or the "
                "environment variable"
            )
        evidence.append(
            f"K08_RESTORE_STORAGE_ROOT_ID={declared_id!r} is an operator-registered row "
            f"(device_uuid={device_uuid!r}); its truth as real distinct hardware is an "
            "operational fact this script trusts but cannot itself verify."
        )
        return declared_id, abs_path, False

    storage_root_id = "k08-integration-restore"
    device_uuid = str(uuid.uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO storage_roots (storage_root_id, tier, abs_path, device_uuid, description) "
            "VALUES (%s, 'cold', %s, %s, 'K08 integration proof isolated restore target')",
            (storage_root_id, str(restore_root.resolve()), device_uuid),
        )
        cursor.execute("SELECT device_uuid FROM storage_roots WHERE storage_root_id = 'hot'")
        primary_device_uuid = cursor.fetchone()[0]
    connection.commit()
    assert primary_device_uuid != device_uuid
    evidence.append(
        "K08_RESTORE_STORAGE_ROOT_ID was not supplied; this script inserted a placeholder "
        f"storage_roots row ({storage_root_id!r}) with a random device_uuid -- a "
        "mechanically distinct locator only, NOT evidence of real distinct hardware."
    )
    return storage_root_id, str(restore_root.resolve()), True


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL K08 backup/restore integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    restore_dsn = os.environ.get("DATA_GATEWAY_TEST_RESTORE_DSN")
    pending: list[str] = []

    with tempfile.TemporaryDirectory() as holder_name, ExitStack() as connections:
        holder = Path(holder_name)
        root = _resolve_root("K08_PRIMARY_ROOT", holder, "primary", pending)
        backup_root = _resolve_root("K08_BACKUP_ROOT", holder, "backup", pending)
        restore_root = _resolve_root("K08_RESTORE_ROOT", holder, "restored", pending)

        rel_root = "canonical/trades/bybit/BTCUSDT/trade-v1"
        dataset_root = root / rel_root
        dataset_path = root / "dataset.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=IDENTITY, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        evidence = write_evidence(root, dataset_root, dataset_path)

        connection = connections.enter_context(psycopg.connect(dsn))
        if restore_dsn:
            restore_connection = connections.enter_context(psycopg.connect(restore_dsn))
            pending.append(
                "DATA_GATEWAY_TEST_RESTORE_DSN was supplied: restore used a genuinely "
                "separate catalog database/connection, not the primary's own catalog "
                "instance."
            )
        else:
            restore_connection = connection
            pending.append(
                "DATA_GATEWAY_TEST_RESTORE_DSN was not supplied: restore re-used the "
                "primary's own catalog database instance (isolated storage locator only, "
                "not an isolated catalog instance)."
            )

        restore_storage_root_id = None
        inserted_storage_root = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT 1 FROM catalog.datasets WHERE {DATASET_WHERE}")
                if cursor.fetchone() is not None:
                    raise RuntimeError("integration database already contains the fixed first-vertical dataset")
            bootstrap_schema_registry(connection, read_schema_registration(ROOT / "schemas" / "trade-v1.json"))
            connection.commit()
            if restore_connection is not connection:
                with restore_connection.cursor() as cursor:
                    cursor.execute(f"SELECT 1 FROM catalog.datasets WHERE {DATASET_WHERE}")
                    if cursor.fetchone() is not None:
                        raise RuntimeError("restore database already contains the fixed first-vertical dataset")
                bootstrap_schema_registry(restore_connection, read_schema_registration(ROOT / "schemas" / "trade-v1.json"))
                restore_connection.commit()

            restore_storage_root_id, storage_root_abs_path, inserted_storage_root = _prepare_restore_storage_root(
                restore_connection, restore_root, pending,
            )

            certification = PublicationCertification(
                CatalogPublicationWriter(connection), BybitTradeV1CertificationProfile("k08-integration-certifier"),
            )
            run = certification.run(evidence)
            assert run.sealed_partition.state == "closed"
            assert run.certification.status == "pass"

            recovery_set = capture_recovery_set(evidence, run.sealed_partition)
            export = export_recovery_set(recovery_set, evidence, backup_root)

            restored = restore_recovery_set(
                export, restore_root,
                forbidden_roots=[root, backup_root],
                catalog_writer=CatalogPublicationWriter(restore_connection),
                storage_root_id=restore_storage_root_id,
                storage_root_abs_path=storage_root_abs_path,
            )
            assert restored.sealed.natural_identity == run.sealed_partition.natural_identity
            assert restored.sealed.content_sha256 == run.sealed_partition.content_sha256
            assert restored.sealed.manifest_sha256 == run.sealed_partition.manifest_sha256
            assert restored.sealed.ts_start == run.sealed_partition.ts_start
            assert restored.sealed.ts_end == run.sealed_partition.ts_end
            if restore_connection is connection:
                assert restored.sealed.partition_id != run.sealed_partition.partition_id, (
                    "restore must not silently reuse the primary catalog partition row"
                )

            # Only the restored row is ever promoted to 'valid'; the primary
            # row is left 'closed' (VALID_ONLY-invisible).  A VALID_ONLY
            # historical read can therefore only be satisfied by the
            # restored partition -- proving a restored-only read, not
            # fallback/union across a surviving primary row.
            with restore_connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE catalog.partitions SET state = 'valid' WHERE partition_id = %s",
                    (restored.sealed.partition_id,),
                )
            restore_connection.commit()

            catalog = Catalog(connection=restore_connection)
            gateway = DataGateway(catalog, ordering_providers=(BYBIT_ORDERING_PROVIDER,))
            request = DataRequest(
                IDENTITY, START, END,
                lifecycle_policy=LifecyclePolicy.VALID_ONLY,
                ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            )
            result = gateway.read(request)
            assert [record.trade_id for record in result.records] == ["1", "2"]
            assert result.metadata.catalog_partition_ids == (restored.sealed.partition_id,), (
                "a VALID_ONLY historical read from the restore-side catalog must resolve "
                "the restored partition alone, never the primary's"
            )

            print("PASS K08 backup/restore v1 PostgreSQL isolated restore proof")
            print("DEPLOYMENT_INDEPENDENCE_PROOF_PENDING:")
            for line in pending:
                print(f"  - {line}")
            return 0
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM catalog.quality_reports WHERE partition_id IN "
                    f"(SELECT partition_id FROM catalog.partitions WHERE dataset_id IN "
                    f"(SELECT dataset_id FROM catalog.datasets WHERE {DATASET_WHERE}))"
                )
                cursor.execute(
                    f"DELETE FROM catalog.partitions WHERE dataset_id IN "
                    f"(SELECT dataset_id FROM catalog.datasets WHERE {DATASET_WHERE})"
                )
                cursor.execute(f"DELETE FROM catalog.datasets WHERE {DATASET_WHERE}")
            connection.commit()
            if restore_connection is not connection:
                with restore_connection.cursor() as cursor:
                    cursor.execute(
                        f"DELETE FROM catalog.quality_reports WHERE partition_id IN "
                        f"(SELECT partition_id FROM catalog.partitions WHERE dataset_id IN "
                        f"(SELECT dataset_id FROM catalog.datasets WHERE {DATASET_WHERE}))"
                    )
                    cursor.execute(
                        f"DELETE FROM catalog.partitions WHERE dataset_id IN "
                        f"(SELECT dataset_id FROM catalog.datasets WHERE {DATASET_WHERE})"
                    )
                    cursor.execute(f"DELETE FROM catalog.datasets WHERE {DATASET_WHERE}")
                restore_connection.commit()
            if restore_storage_root_id is not None and inserted_storage_root:
                with restore_connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM storage_roots WHERE storage_root_id = %s", (restore_storage_root_id,)
                    )
                restore_connection.commit()


if __name__ == "__main__":
    raise SystemExit(main())
