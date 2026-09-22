#!/usr/bin/env python3
"""Real-PostgreSQL proof for K08 backup/restore v1 isolated restore.

Required invocation:

    DATA_GATEWAY_TEST_DSN=postgresql://...         (primary catalog)
    DATA_GATEWAY_TEST_RESTORE_DSN=postgresql://...  (a genuinely SEPARATE database)
    python tests/integration_k08_backup_restore_postgres.py

It does not run as part of the local script runner because it requires
external database infrastructure, exactly like the other
``integration_*_postgres.py`` files in this directory.

``DATA_GATEWAY_TEST_RESTORE_DSN`` is not optional here: K08 v1's restore
contract (``restore_recovery_set``) requires the target catalog to hold no
existing admission for the recovered dataset/partition_key family before
restore begins.  Since the primary database already holds that family's own
admission once ``PublicationCertification`` runs, restoring into that *same*
database can never satisfy that invariant -- it is refused by construction,
exactly as it should be.  A real isolated restore therefore requires a real
second database; this script reflects that rather than offering a
same-catalog fallback that could never actually prove isolation.

Real deployment-independence proof additionally requires the operator to
supply actual distinct mount points and real, independently attested
hardware identity for all three roles, via these optional environment
variables:

    K08_PRIMARY_ROOT                 absolute path on the primary storage device
    K08_BACKUP_ROOT                  absolute path on the backup storage device
    K08_RESTORE_ROOT                 absolute path on the restore storage device
    K08_PRIMARY_DEVICE_ID            operator-attested identifier for the real
    K08_BACKUP_DEVICE_ID              physical device backing each of the three
    K08_RESTORE_DEVICE_ID             roots above (arbitrary strings the operator
                                       chooses; the script only requires the
                                       three -- when all are supplied -- to be
                                       pairwise distinct); backup has no
                                       catalog registration of its own; these
                                       three are the only mechanism that can
                                       attest its hardware independence
    K08_RESTORE_STORAGE_ROOT_ID       an ALREADY-REGISTERED catalog.storage_roots
                                       row in the restore database (real
                                       abs_path == K08_RESTORE_ROOT, real
                                       device_uuid distinct from the primary
                                       database's 'hot' row); this script
                                       never inserts a row when this is
                                       supplied and always compares its
                                       device_uuid against primary 'hot'

Any root left unset falls back to a subdirectory of one process-local
``TemporaryDirectory``.  Roots that end up on the SAME filesystem device are
detected via ``os.stat().st_dev`` (genuine, directly observed evidence, not
a trusted claim) and reported as such.  ``K08_RESTORE_STORAGE_ROOT_ID`` left
unset falls back to inserting a placeholder ``storage_roots`` row with a
random ``device_uuid`` -- explicitly not evidence, only a mechanically
distinct locator.  ``K08_*_DEVICE_ID`` left unset (any of the three) means
real hardware independence is not claimed for any pair involving it.

What this proves in every invocation, against two real PostgreSQL catalog
databases and a real filesystem target (never a fake writer/catalog):

- one finalized publication seals through the unmodified
  ``CatalogPublicationWriter``/``PublicationCertification`` path;
- K08 captures a deterministic ``RecoverySetV1`` (with an attributed K06
  not-applicable assertion for this self-contained fixture) and exports it
  to an explicit, durable, independently reloadable backup without touching
  primary state;
- restoring into a brand-new isolated filesystem target and a genuinely
  separate, empty catalog database, through one ``restore_catalog`` object
  bound to that single database connection (so the storage-locator lookup
  and the admission it authorizes can never be split across two different
  catalogs), whose registered ``catalog.storage_roots`` locator is looked up
  *authoritatively* (never a caller-asserted string) and verified to resolve
  to exactly that target, reproduces the sealed identities/coverage;
- the real ``access.catalog.Catalog``/``access.gateway.DataGateway``,
  pointed at the restore database, resolve and read the restored partition
  from the restored target -- and because that database never held the
  primary's row at all, this is unambiguously a restored-only read, not a
  read that could fall back to or compose with a surviving primary row.

Independence axes and how this script reports them:

- catalog-instance independence: ALWAYS proven (two distinct DSNs are
  mandatory);
- storage-locator isolation within the restore side: ALWAYS proven
  (``storage_root_id`` is authoritatively resolved, from the SAME connection
  used to admit, and checked against ``K08_RESTORE_ROOT``);
- OS-level distinct-device evidence for primary/backup/restore roots:
  proven whenever ``os.stat().st_dev`` actually differs -- directly
  observed, filesystem/mount granularity only (not necessarily distinct
  physical hardware);
- real distinct physical hardware for primary vs. restore (``device_uuid``
  in ``catalog.storage_roots``): proven only when ``K08_RESTORE_STORAGE_ROOT_ID``
  names an operator-registered row whose ``device_uuid`` differs from
  primary ``hot``;
- real distinct physical hardware across ALL THREE roles, backup included
  (``K08_PRIMARY_DEVICE_ID``/``K08_BACKUP_DEVICE_ID``/``K08_RESTORE_DEVICE_ID``):
  proven only when all three are supplied and pairwise distinct -- backup
  has no catalog registration, so this operator attestation is the only
  mechanism that can ever cover it.

``DEPLOYMENT_INDEPENDENCE_PROOF_PENDING`` is printed listing exactly which
axes above were not established this run.  ``DEPLOYMENT_INDEPENDENCE: fully
evidenced this run`` is printed only when every axis -- including backup
hardware -- is satisfied; storage-locator/catalog-instance independence
alone is never enough to claim it.  See ADR-0039 Sec. 2.
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
from quant_platform.operations.recovery import K06NotApplicableAssertion  # noqa: E402
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

NOT_APPLICABLE = K06NotApplicableAssertion(
    asserting_authority_id="adr:k08-integration-authority-v1",
    asserted_at=Instant.parse("2026-09-01T10:00:03Z"),
    rationale="integration fixture publication is self-contained; no RAW source reconstruction is required",
)
NOT_APPLICABLE_DOCUMENT = NOT_APPLICABLE.canonical_payload()


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


def _resolve_root(env_var: str, holder: Path, subdir: str, pending: list[str]) -> Path:
    override = os.environ.get(env_var)
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    pending.append(
        f"{env_var} was not supplied; {subdir} falls back to a subdirectory of one "
        "process-local TemporaryDirectory."
    )
    path = holder / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_device_evidence(roots: dict[str, Path], proven: list[str], pending: list[str]) -> None:
    """Directly observed (not trusted) evidence: do these roots share a filesystem device?"""

    devices = {name: os.stat(path).st_dev for name, path in roots.items()}
    names = list(devices)
    shared = [
        (a, b) for i, a in enumerate(names) for b in names[i + 1:] if devices[a] == devices[b]
    ]
    if shared:
        for a, b in shared:
            pending.append(
                f"{a} and {b} are on the SAME filesystem device (st_dev={devices[a]}); "
                "no OS-level independence evidence between them."
            )
    else:
        proven.append(
            "OS-level st_dev evidence: primary/backup/restore roots are on distinct "
            f"filesystem devices this run ({devices}) -- directly observed, filesystem/mount "
            "granularity only (not necessarily distinct physical hardware)."
        )


def _report_operator_hardware_evidence(proven: list[str], pending: list[str]) -> None:
    """The ONLY mechanism that can attest backup hardware independence: backup
    has no catalog.storage_roots registration of its own to compare via SQL."""

    ids = {
        "primary": os.environ.get("K08_PRIMARY_DEVICE_ID"),
        "backup": os.environ.get("K08_BACKUP_DEVICE_ID"),
        "restore": os.environ.get("K08_RESTORE_DEVICE_ID"),
    }
    missing = [name for name, value in ids.items() if not value]
    if missing:
        pending.append(
            "real distinct physical hardware across all three roles (including backup) is "
            f"unproven: {', '.join(missing)} K08_*_DEVICE_ID not supplied. Set "
            "K08_PRIMARY_DEVICE_ID/K08_BACKUP_DEVICE_ID/K08_RESTORE_DEVICE_ID to real, "
            "operator-verified distinct hardware identifiers to close this axis."
        )
        return
    values = list(ids.values())
    if len(set(values)) != len(values):
        raise RuntimeError(
            "K08_PRIMARY_DEVICE_ID/K08_BACKUP_DEVICE_ID/K08_RESTORE_DEVICE_ID must be pairwise "
            f"distinct; got {ids!r}"
        )
    proven.append(
        f"real distinct physical hardware (operator-attested): primary={ids['primary']!r}, "
        f"backup={ids['backup']!r}, restore={ids['restore']!r} are pairwise distinct -- "
        "operator-verified, trusted but not independently re-verified by this script."
    )


class RestoreCatalogImpl:
    """The complete ``RestoreCatalog`` capability, bound to exactly one connection.

    Wraps the unmodified S13 ``CatalogPublicationWriter`` for admission and
    adds the authoritative read lookups restore needs, both against the same
    ``connection`` -- there is only one object and one connection, so the
    storage-locator lookup that authorizes an admission and the admission
    itself can never be silently split across two different catalogs.
    """

    def __init__(self, connection):
        self.connection = connection
        self._writer = CatalogPublicationWriter(connection)

    def seal_partition(self, **kwargs):
        return self._writer.seal_partition(**kwargs)

    def commit(self):
        return self._writer.commit()

    def rollback(self):
        return self._writer.rollback()

    def resolve_storage_root_abs_path(self, storage_root_id: str) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT abs_path FROM storage_roots WHERE storage_root_id = %s", (storage_root_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"storage_root_id {storage_root_id!r} is not registered in the restore catalog")
        return row[0]

    def family_admission_count(self, dataset_identity: DatasetIdentity, partition_key: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FROM catalog.partitions p
                  JOIN catalog.datasets d ON d.dataset_id = p.dataset_id
                 WHERE d.layer = %s AND d.kind = %s AND d.venue = %s
                   AND d.instrument = %s AND d.schema_id = %s
                   AND p.partition_key = %s
                """,
                (
                    dataset_identity.layer, dataset_identity.dataset_kind, dataset_identity.venue,
                    dataset_identity.instrument, dataset_identity.record_schema_id, partition_key,
                ),
            )
            return cursor.fetchone()[0]


def _prepare_restore_storage_root(
    restore_connection, primary_connection, restore_root: Path, proven: list[str], pending: list[str],
) -> str:
    """Return storage_root_id; append to ``proven`` or ``pending`` as appropriate."""

    declared_id = os.environ.get("K08_RESTORE_STORAGE_ROOT_ID")
    with primary_connection.cursor() as cursor:
        cursor.execute("SELECT device_uuid FROM storage_roots WHERE storage_root_id = 'hot'")
        primary_device_uuid = cursor.fetchone()[0]

    if declared_id:
        with restore_connection.cursor() as cursor:
            cursor.execute(
                "SELECT abs_path, device_uuid FROM storage_roots WHERE storage_root_id = %s",
                (declared_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError(
                f"K08_RESTORE_STORAGE_ROOT_ID={declared_id!r} does not name an already-"
                "registered storage_roots row in the restore database; this script never "
                "inserts a row when this variable is supplied"
            )
        abs_path, device_uuid = row
        if Path(abs_path).resolve() != restore_root.resolve():
            raise RuntimeError(
                f"storage_roots {declared_id!r}.abs_path ({abs_path}) does not match "
                f"K08_RESTORE_ROOT ({restore_root})"
            )
        if device_uuid == primary_device_uuid:
            raise RuntimeError(
                f"K08_RESTORE_STORAGE_ROOT_ID={declared_id!r} declares the SAME device_uuid "
                "as the primary database's 'hot' storage root; it cannot be evidence of "
                "independent storage"
            )
        proven.append(
            f"real distinct physical hardware (primary vs. restore): K08_RESTORE_STORAGE_ROOT_ID="
            f"{declared_id!r} is operator-registered (device_uuid={device_uuid!r}) and differs "
            f"from primary 'hot' (device_uuid={primary_device_uuid!r}) -- operator-verified, "
            "trusted but not independently re-verified by this script."
        )
        return declared_id

    storage_root_id = "k08-integration-restore"
    device_uuid = str(uuid.uuid4())
    with restore_connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO storage_roots (storage_root_id, tier, abs_path, device_uuid, description) "
            "VALUES (%s, 'cold', %s, %s, 'K08 integration proof isolated restore target')",
            (storage_root_id, str(restore_root.resolve()), device_uuid),
        )
    restore_connection.commit()
    pending.append(
        "real distinct physical hardware (primary vs. restore): K08_RESTORE_STORAGE_ROOT_ID was "
        f"not supplied; this script inserted a placeholder storage_roots row ({storage_root_id!r}) "
        "with a random device_uuid in the restore database -- a mechanically distinct locator "
        "only, NOT evidence of real distinct hardware."
    )
    return storage_root_id


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL K08 backup/restore integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    restore_dsn = os.environ.get("DATA_GATEWAY_TEST_RESTORE_DSN")
    if not restore_dsn:
        print(
            "SKIP PostgreSQL K08 backup/restore integration: DATA_GATEWAY_TEST_RESTORE_DSN is "
            "unset. K08 v1 restore requires the restore catalog to hold no existing admission "
            "for the recovered family; the primary database always holds that admission once "
            "PublicationCertification runs, so a genuinely separate second database is "
            "mandatory for this proof, not optional."
        )
        return 0

    proven: list[str] = []
    pending: list[str] = []

    with tempfile.TemporaryDirectory() as holder_name, ExitStack() as connections:
        holder = Path(holder_name)
        root = _resolve_root("K08_PRIMARY_ROOT", holder, "primary", pending)
        backup_root = _resolve_root("K08_BACKUP_ROOT", holder, "backup", pending)
        restore_root = _resolve_root("K08_RESTORE_ROOT", holder, "restored", pending)
        _report_device_evidence({"primary": root, "backup": backup_root, "restore": restore_root}, proven, pending)
        _report_operator_hardware_evidence(proven, pending)

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
        restore_connection = connections.enter_context(psycopg.connect(restore_dsn))
        proven.append(
            "catalog-instance independence: DATA_GATEWAY_TEST_RESTORE_DSN is a genuinely "
            "separate database from DATA_GATEWAY_TEST_DSN."
        )

        restore_storage_root_id = None
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT 1 FROM catalog.datasets WHERE {DATASET_WHERE}")
                if cursor.fetchone() is not None:
                    raise RuntimeError("primary database already contains the fixed first-vertical dataset")
            bootstrap_schema_registry(connection, read_schema_registration(ROOT / "schemas" / "trade-v1.json"))
            connection.commit()

            with restore_connection.cursor() as cursor:
                cursor.execute(f"SELECT 1 FROM catalog.datasets WHERE {DATASET_WHERE}")
                if cursor.fetchone() is not None:
                    raise RuntimeError("restore database already contains the fixed first-vertical dataset")
            bootstrap_schema_registry(restore_connection, read_schema_registration(ROOT / "schemas" / "trade-v1.json"))
            restore_connection.commit()

            restore_catalog = RestoreCatalogImpl(restore_connection)
            restore_storage_root_id = _prepare_restore_storage_root(
                restore_connection, connection, restore_root, proven, pending,
            )

            certification = PublicationCertification(
                CatalogPublicationWriter(connection), BybitTradeV1CertificationProfile("k08-integration-certifier"),
            )
            run = certification.run(evidence)
            assert run.sealed_partition.state == "closed"
            assert run.certification.status == "pass"

            recovery_set = capture_recovery_set(evidence, run.sealed_partition, k06_not_applicable=NOT_APPLICABLE)
            export = export_recovery_set(
                recovery_set, evidence, backup_root, k06_not_applicable_document=NOT_APPLICABLE_DOCUMENT,
            )

            restored = restore_recovery_set(
                export, restore_root,
                forbidden_roots=[root, backup_root],
                restore_catalog=restore_catalog,
                storage_root_id=restore_storage_root_id,
            )
            assert restored.sealed.natural_identity == run.sealed_partition.natural_identity
            assert restored.sealed.content_sha256 == run.sealed_partition.content_sha256
            assert restored.sealed.manifest_sha256 == run.sealed_partition.manifest_sha256
            assert restored.sealed.ts_start == run.sealed_partition.ts_start
            assert restored.sealed.ts_end == run.sealed_partition.ts_end

            # The restore database never held the primary's row at all (it is
            # a different database), so any read from it is unambiguously
            # restored-only -- promote to 'valid' the same way a real
            # historical read would be configured.
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
            assert result.metadata.catalog_partition_ids == (restored.sealed.partition_id,)

            print("PASS K08 backup/restore v1 PostgreSQL isolated restore proof")
            if not pending:
                print("DEPLOYMENT_INDEPENDENCE: fully evidenced this run")
                for line in proven:
                    print(f"  - {line}")
            else:
                print("DEPLOYMENT_INDEPENDENCE_PROOF_PENDING:")
                for line in pending:
                    print(f"  - {line}")
                if proven:
                    print("Already established this run:")
                    for line in proven:
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
            if restore_storage_root_id is not None and not os.environ.get("K08_RESTORE_STORAGE_ROOT_ID"):
                with restore_connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM storage_roots WHERE storage_root_id = %s", (restore_storage_root_id,)
                    )
                restore_connection.commit()


if __name__ == "__main__":
    raise SystemExit(main())
