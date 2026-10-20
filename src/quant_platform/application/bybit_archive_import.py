"""Historical import of one UTC day of Bybit linear trades from the public archive (DG-N ``T04``, ADR-0074 Amendment 1).

One invocation, one instrument of the allow-list, one UTC day, one local archive file. The day goes through the producer path
only: materialize, emit the partition and coverage manifests, S13 certification, and S14 publication when S13 passes. Nothing
here writes a catalog row directly, overwrites a file or repairs a live gap: a day that already has any partition in the catalog
(an SQLite ``BTCUSDT`` day, a live day) is refused, and so is a target file that already exists.

Layout under the dataset root ``<storage root>/canonical/trades/bybit/<INSTRUMENT>/trade-v1``: ``dataset-manifest.json`` shared by
the dataset (emitted only when absent), and per day ``dt=<day>/part-001.parquet``, ``dt=<day>/partition-manifest.json`` and
``dt=<day>/coverage-manifest.json``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_platform.data import Instant
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest, emit_partition_manifest
from quant_platform.data.parquet import scan_trade_v1_all
from quant_platform.data.publication import PublicationCertification, SealedPartitionEvidence
from quant_platform.data.publication_catalog import CatalogPublicationWriter
from quant_platform.data.publication_eligibility import PublicationEligibilityBridge, PublicationEligibilityEvidence
from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog
from quant_platform.source_adapters.bybit import (
    BYBIT_ARCHIVE_SOURCE_PROFILE_V1,
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    BYBIT_TRADE_V1_CERTIFICATION_PROFILE,
    BYBIT_TRADE_V1_CHECK_SUITE,
    BybitTradeV1CertificationProfile,
    build_bybit_trade_v1_source_extract_coverage,
    materialize_bybit_trade_v1,
)
from quant_platform.source_adapters.bybit_archive import (
    bybit_archive_dataset_identity,
    bybit_archive_file_name,
    bybit_archive_url,
    read_bybit_archive_day,
)

ARCHIVE_IMPORT_PRODUCER = "bybit-archive-day-import-v1"
DATASET_TRANSFORM = "canonicalize-trades-v1"
DATASET_ORIGIN = "source_acquired"


class ArchiveImportRefused(RuntimeError):
    """The day is not imported. ``phase`` names the step that refused; files written before it stay unpublished."""

    def __init__(self, phase: str, message: str):
        self.phase = phase
        super().__init__(f"{phase}: {message}")


@dataclass(frozen=True, slots=True)
class ArchiveImportReport:
    instrument: str
    day: str
    partition_key: str
    artifact_path: Path
    row_count: int
    source_file_sha256: str
    partition_id: str
    state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "day": self.day,
            "partition_key": self.partition_key,
            "artifact_path": str(self.artifact_path),
            "row_count": self.row_count,
            "source_file_sha256": self.source_file_sha256,
            "partition_id": self.partition_id,
            "state": self.state,
        }


def import_bybit_archive_day(
    connection: Any,
    *,
    instrument: str,
    day: str,
    archive_path: str | Path,
    storage_root_id: str,
    code_ref: str,
) -> ArchiveImportReport:
    """Import one day; returns the published partition or raises :class:`ArchiveImportRefused`."""

    try:
        identity = bybit_archive_dataset_identity(instrument)
        bybit_archive_file_name(instrument, day)  # validates the day
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    except Exception as exc:
        raise ArchiveImportRefused("REQUEST", str(exc)) from exc
    if not isinstance(code_ref, str) or not code_ref.strip():
        raise ArchiveImportRefused("REQUEST", "code_ref must name the producing code")
    partition_key = f"dt={day}"

    abs_path = _storage_root_path(connection, storage_root_id)
    if _partition_exists(connection, identity, partition_key):
        raise ArchiveImportRefused(
            "CATALOG", f"{instrument} {partition_key} already has a partition in the catalog; the archive does not replace it"
        )
    dataset_root = Path(abs_path).joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id
    )
    rel_path = f"{partition_key}/part-001.parquet"
    artifact_path = dataset_root / rel_path
    dataset_manifest_path = dataset_root / "dataset-manifest.json"
    partition_manifest_path = dataset_root / partition_key / "partition-manifest.json"
    coverage_manifest_path = dataset_root / partition_key / "coverage-manifest.json"
    existing = [str(path) for path in (artifact_path, partition_manifest_path, coverage_manifest_path) if path.exists()]
    if existing:
        raise ArchiveImportRefused("TARGET", "target files already exist; refusing overwrite: " + ", ".join(existing))

    try:
        records_day = read_bybit_archive_day(archive_path, instrument=instrument, day=day)
    except Exception as exc:
        raise ArchiveImportRefused("SOURCE", str(exc)) from exc

    interval_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    interval_end = (start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = _utc_text(datetime.now(timezone.utc))
    try:
        materialization = materialize_bybit_trade_v1(artifact_path, records_day.records, dataset_identity=identity)
        if materialization.row_count != records_day.evidence.row_count:
            raise ArchiveImportRefused("MATERIALIZE", "materialized row count differs from the source row count")
        if not dataset_manifest_path.exists():
            emit_dataset_manifest(
                dataset_manifest_path, dataset_identity=identity, created_at=now,
                schema_version="dataset-manifest-v2", origin=DATASET_ORIGIN, transform=DATASET_TRANSFORM,
            )
        partition = emit_partition_manifest(
            partition_manifest_path, materialization, dataset_identity=identity, dataset_root=dataset_root,
            partition_key=partition_key, revision=1, rel_path=rel_path, created_at=now, closed_at=now,
            producer=ARCHIVE_IMPORT_PRODUCER, code_ref=code_ref,
        )
        tag = f"bybit-archive-{instrument.lower()}-{day}"
        coverage = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=identity, coverage_id=f"{tag}-v1", intent_start=interval_start, intent_end=interval_end,
            assertion_id=f"{tag}-complete-v1", assertion_start=interval_start, assertion_end=interval_end,
            partition_key=partition_key, revision=1, source_extract_detail=records_day.evidence.detail, created_at=now,
            producer=ARCHIVE_IMPORT_PRODUCER, code_ref=code_ref, source_profile=BYBIT_ARCHIVE_SOURCE_PROFILE_V1,
        )
        emit_coverage_manifest(
            coverage_manifest_path, dataset_identity=identity, partition_manifests=[partition.document], **coverage,
        )
    except ArchiveImportRefused:
        raise
    except Exception as exc:
        raise ArchiveImportRefused("MATERIALIZE", str(exc)) from exc

    try:
        run = PublicationCertification(CatalogPublicationWriter(connection), BybitTradeV1CertificationProfile(code_ref)).run(
            SealedPartitionEvidence(
                dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,), artifact_path, storage_root_id,
            )
        )
    except Exception as exc:
        raise ArchiveImportRefused("S13", str(exc)) from exc
    if run.certification.status != "pass":
        failed = ", ".join(item.category for item in run.certification.categories if item.status != "pass")
        raise ArchiveImportRefused("S13", f"certification status {run.certification.status!r} ({failed}); not published")

    try:
        published = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
            PublicationEligibilityEvidence(
                dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,), storage_root_id,
                BYBIT_TRADE_V1_CERTIFICATION_PROFILE, BYBIT_TRADE_V1_CHECK_SUITE,
            )
        )
    except Exception as exc:
        raise ArchiveImportRefused("S14", str(exc)) from exc
    return ArchiveImportReport(
        instrument=instrument,
        day=day,
        partition_key=partition_key,
        artifact_path=artifact_path,
        row_count=materialization.row_count,
        source_file_sha256=records_day.evidence.file_sha256,
        partition_id=published.partition_id,
        state=published.state,
    )


def compare_archive_day_with_parquet(
    archive_path: str | Path, parquet_path: str | Path, *, instrument: str, day: str
) -> dict[str, Any]:
    """Compare one archive day with an existing ``trade-v1`` Parquet file of the same day; reads both, writes nothing.

    The evidence of ADR-0074 Amendment 1 A1.2 item 8 (old file against the archive before it is replaced) and of the ``BTCUSDT``
    equivalence proof. Timestamps are compared at millisecond precision, the precision of the import; prices and sizes both as
    text and as values, so a trailing-zero difference is told apart from a different number.
    """

    archive = read_bybit_archive_day(archive_path, instrument=instrument, day=day).records
    existing: dict[str, Any] = {}
    parquet_rows = 0
    for batch in scan_trade_v1_all(Path(parquet_path), batch_size=65_536):
        for record in batch:
            parquet_rows += 1
            existing[record.trade_id] = record
    fresh = {record.trade_id: record for record in archive}
    only_archive = sorted(set(fresh) - set(existing))
    only_parquet = sorted(set(existing) - set(fresh))
    differences: Counter[str] = Counter()
    for trade_id in set(fresh) & set(existing):
        new, old = fresh[trade_id], existing[trade_id]
        if new.instrument != old.instrument:
            differences["instrument"] += 1
        if Instant.parse(new.exchange_ts).epoch_ns != Instant.parse(old.exchange_ts).epoch_ns // 1_000_000 * 1_000_000:
            differences["exchange_ts_ms"] += 1
        if new.aggressor_side != old.aggressor_side:
            differences["aggressor_side"] += 1
        for name in ("price", "size"):
            if getattr(new, name) != getattr(old, name):
                differences[f"{name}_text"] += 1
                if Decimal(getattr(new, name)) != Decimal(getattr(old, name)):
                    differences[f"{name}_value"] += 1
    value_fields = ("instrument", "exchange_ts_ms", "aggressor_side", "price_value", "size_value")
    same_trades = (
        not only_archive and not only_parquet and parquet_rows == len(existing) and not any(differences[f] for f in value_fields)
    )
    return {
        "instrument": instrument,
        "day": day,
        "archive_rows": len(archive),
        "parquet_rows": parquet_rows,
        "parquet_duplicate_trade_ids": parquet_rows - len(existing),
        "only_in_archive": len(only_archive),
        "only_in_parquet": len(only_parquet),
        "examples_only_in_archive": only_archive[:5],
        "examples_only_in_parquet": only_parquet[:5],
        "field_differences": dict(sorted(differences.items())),
        "same_trades": same_trades,
        "identical": same_trades and not differences,
    }


def _storage_root_path(connection: Any, storage_root_id: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT abs_path FROM catalog.storage_roots WHERE storage_root_id = %s", (storage_root_id,))
        row = cursor.fetchone()
    connection.rollback()
    if row is None:
        raise ArchiveImportRefused("CATALOG", f"storage root {storage_root_id!r} is not registered")
    return str(row[0])


def _partition_exists(connection: Any, identity: Any, partition_key: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.layer = %s AND d.kind = %s AND d.venue = %s AND d.instrument = %s AND d.schema_id = %s "
            "AND d.feature_set_def_id IS NULL AND p.partition_key = %s LIMIT 1",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
             partition_key),
        )
        row = cursor.fetchone()
    connection.rollback()
    return row is not None


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "ARCHIVE_IMPORT_PRODUCER",
    "BYBIT_LINEAR_TRADE_INSTRUMENTS_V1",
    "ArchiveImportRefused",
    "ArchiveImportReport",
    "bybit_archive_file_name",
    "bybit_archive_url",
    "compare_archive_day_with_parquet",
    "import_bybit_archive_day",
]
