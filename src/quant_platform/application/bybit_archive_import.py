"""Historical import of one UTC day of Bybit linear trades from the public archive (DG-N ``T04``, ADR-0074 Amendment 1).

One invocation, one instrument of the allow-list, one UTC day, one local archive file. The day goes through the producer path
only: materialize, emit the partition and coverage manifests, S13 certification, and S14 publication when S13 passes. Nothing
here writes a catalog row directly, overwrites a file or repairs a live gap: a day that already has any partition in the catalog
(an SQLite ``BTCUSDT`` day, a live day) is refused, and so is a target file that already exists.

Two imports of the same day never write at once: the day is guarded by an exclusive lock file created before anything is checked or
written, and the catalog and the target files are checked again under it. The loser is refused with phase ``LOCK`` before it touches a
file, so the files that stay on disk are the ones the catalog certified. A lock left by a crashed import is **not** taken over: the
day stays refused until the operator confirms that no import is running and removes the named lock file.

Layout under the dataset root ``<storage root>/canonical/trades/bybit/<INSTRUMENT>/trade-v1``: ``dataset-manifest.json`` shared by
the dataset (emitted only when absent), and per day ``dt=<day>/part-001.parquet``, ``dt=<day>/partition-manifest.json`` and
``dt=<day>/coverage-manifest.json``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import socket
import time
from typing import Any
import uuid

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
    _refuse_if_in_catalog(connection, identity, partition_key, instrument)
    dataset_root = Path(abs_path).joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id
    )
    try:
        dataset_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArchiveImportRefused("TARGET", f"cannot create the dataset root: {exc}") from exc
    with _day_lock(dataset_root / f".import-{partition_key}.lock"):
        # Checked again under the lock: the winner of a race may have published between the first check and the lock.
        _refuse_if_in_catalog(connection, identity, partition_key, instrument)
        return _import_locked(
            connection, identity=identity, instrument=instrument, day=day, start=start, partition_key=partition_key,
            archive_path=archive_path, storage_root_id=storage_root_id, code_ref=code_ref, dataset_root=dataset_root,
        )


REBUILD_SEGMENT = "seg=000000.000"
REBUILD_COVERAGE_TAG_SUFFIX = "-rebuild"


def rebuild_partition_key(day: str) -> str:
    """The fixed key a rebuilt day is imported under: the two-level grammar of ADR-0075 for a window that starts at the day's start."""

    return f"dt={day}/{REBUILD_SEGMENT}"


def import_bybit_archive_day_after_retirement(
    connection: Any,
    *,
    instrument: str,
    day: str,
    archive_path: str | Path,
    storage_root_id: str,
    code_ref: str,
    expected_source_sha256: str,
) -> ArchiveImportReport:
    """The rebuild variant of the import (ADR-0074 Amendment 2, A2.2): the day's legacy revision, once ``invalid``, does not occupy it.

    The ordinary :func:`import_bybit_archive_day` refuses any day that has a partition, in any state, and keeps doing so. This
    variant lets through one thing only: the live revision of the **exact** key ``dt=<day>`` in the state ``invalid`` (its
    superseded predecessors are history). A ``valid``, ``degraded``, ``closed`` or ``writing`` revision of that key, and every
    partition ``dt=<day>/...`` (including the key it would create), still refuses. The key it creates is fixed
    (:func:`rebuild_partition_key`); it is not a ``--force`` and takes no key argument. The bytes it parses and materializes must
    hash to ``expected_source_sha256`` (the file the plan approved): another file is refused as ``SOURCE`` before anything is written."""

    try:
        identity = bybit_archive_dataset_identity(instrument)
        bybit_archive_file_name(instrument, day)  # validates the day
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    except Exception as exc:
        raise ArchiveImportRefused("REQUEST", str(exc)) from exc
    if not isinstance(code_ref, str) or not code_ref.strip():
        raise ArchiveImportRefused("REQUEST", "code_ref must name the producing code")
    abs_path = _storage_root_path(connection, storage_root_id)
    _refuse_unless_retired(connection, identity, day, instrument)
    dataset_root = Path(abs_path).joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id
    )
    try:
        dataset_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArchiveImportRefused("TARGET", f"cannot create the dataset root: {exc}") from exc
    # the lock is the day's, the same file the ordinary import takes: the two exclude each other (its name is derived from the day,
    # not from the two-level key, which would name a directory that does not exist)
    with _day_lock(dataset_root / f".import-dt={day}.lock"):
        _refuse_unless_retired(connection, identity, day, instrument)
        return _import_locked(
            connection, identity=identity, instrument=instrument, day=day, start=start, partition_key=rebuild_partition_key(day),
            archive_path=archive_path, storage_root_id=storage_root_id, code_ref=code_ref, dataset_root=dataset_root,
            coverage_tag_suffix=REBUILD_COVERAGE_TAG_SUFFIX, expected_source_sha256=expected_source_sha256,
        )


def _refuse_unless_retired(connection: Any, identity: Any, day: str, instrument: str) -> None:
    legacy_key = f"dt={day}"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.partition_key, p.state FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.layer = %s AND d.kind = %s AND d.venue = %s AND d.instrument = %s AND d.schema_id = %s "
            "AND d.feature_set_def_id IS NULL AND (p.partition_key = %s OR p.partition_key LIKE %s)",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
             legacy_key, f"{legacy_key}/%"),
        )
        rows = cursor.fetchall()
    connection.rollback()
    foreign = [key for key, _state in rows if key != legacy_key]
    if foreign:
        raise ArchiveImportRefused("CATALOG", f"{instrument} {day} has partitions under {legacy_key}/ ({', '.join(sorted(foreign))}); "
                                              "the rebuild creates its key only on a day that has none")
    live = [state for _key, state in rows if state != "superseded"]
    if live != ["invalid"]:
        raise ArchiveImportRefused(
            "CATALOG", f"{instrument} {legacy_key} must have exactly one live revision and it must be invalid (retired); found {live}")


def _import_locked(
    connection: Any,
    *,
    identity: Any,
    instrument: str,
    day: str,
    start: datetime,
    partition_key: str,
    archive_path: str | Path,
    storage_root_id: str,
    code_ref: str,
    dataset_root: Path,
    coverage_tag_suffix: str = "",
    expected_source_sha256: str | None = None,
) -> ArchiveImportReport:
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
    if expected_source_sha256 is not None and records_day.evidence.file_sha256 != expected_source_sha256:
        raise ArchiveImportRefused(
            "SOURCE", f"the archive file is not the approved one: it hashes to {records_day.evidence.file_sha256}, "
                      f"the plan approved {expected_source_sha256}")

    interval_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    interval_end = (start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = _utc_text(datetime.now(timezone.utc))
    try:
        materialization = materialize_bybit_trade_v1(artifact_path, records_day.records, dataset_identity=identity)
        if materialization.row_count != records_day.evidence.row_count:
            raise ArchiveImportRefused("MATERIALIZE", "materialized row count differs from the source row count")
        _ensure_dataset_manifest(dataset_manifest_path, identity, now)
        partition = emit_partition_manifest(
            partition_manifest_path, materialization, dataset_identity=identity, dataset_root=dataset_root,
            partition_key=partition_key, revision=1, rel_path=rel_path, created_at=now, closed_at=now,
            producer=ARCHIVE_IMPORT_PRODUCER, code_ref=code_ref,
        )
        tag = f"bybit-archive-{instrument.lower()}-{day}{coverage_tag_suffix}"
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
        with _first_registration_guard(connection, identity, dataset_root):
            run = PublicationCertification(
                CatalogPublicationWriter(connection), BybitTradeV1CertificationProfile(code_ref)
            ).run(
                SealedPartitionEvidence(
                    dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,), artifact_path, storage_root_id,
                )
            )
    except ArchiveImportRefused:
        raise
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
    equivalence proof. Every field of the canonical record is compared: ``venue``, ``instrument``, ``exchange_ts`` (at
    millisecond precision, the precision of the import), ``aggressor_side``, ``price`` and ``size`` (as text and as values, so a
    trailing-zero difference is told apart from a different number), ``receive_ts`` and ``sequence`` (the archive has neither, so
    the old file must not carry them either). A row of the old file without a ``trade_id`` cannot be matched and is a difference.
    """

    archive_day = read_bybit_archive_day(archive_path, instrument=instrument, day=day)
    archive = archive_day.records
    existing: dict[str, Any] = {}
    parquet_rows = 0
    null_trade_ids = 0
    for batch in scan_trade_v1_all(Path(parquet_path), batch_size=65_536):
        for record in batch:
            parquet_rows += 1
            if record.trade_id is None:
                null_trade_ids += 1
            else:
                existing[record.trade_id] = record
    fresh = {record.trade_id: record for record in archive}
    only_archive = sorted(set(fresh) - set(existing))
    only_parquet = sorted(set(existing) - set(fresh))
    differences: Counter[str] = Counter()
    for trade_id in set(fresh) & set(existing):
        new, old = fresh[trade_id], existing[trade_id]
        for name in ("venue", "instrument", "aggressor_side", "receive_ts", "sequence"):
            if getattr(new, name) != getattr(old, name):
                differences[name] += 1
        if Instant.parse(new.exchange_ts).epoch_ns != Instant.parse(old.exchange_ts).epoch_ns // 1_000_000 * 1_000_000:
            differences["exchange_ts_ms"] += 1
        for name in ("price", "size"):
            if getattr(new, name) != getattr(old, name):
                differences[f"{name}_text"] += 1
                if Decimal(getattr(new, name)) != Decimal(getattr(old, name)):
                    differences[f"{name}_value"] += 1
    value_differences = sum(count for name, count in differences.items() if not name.endswith("_text"))
    duplicates = parquet_rows - null_trade_ids - len(existing)
    same_trades = not (only_archive or only_parquet or null_trade_ids or duplicates or value_differences)
    return {
        "instrument": instrument,
        "day": day,
        "archive_sha256": archive_day.evidence.file_sha256,
        "archive_rows": len(archive),
        "parquet_rows": parquet_rows,
        "parquet_null_trade_ids": null_trade_ids,
        "parquet_duplicate_trade_ids": duplicates,
        "only_in_archive": len(only_archive),
        "only_in_parquet": len(only_parquet),
        "examples_only_in_archive": only_archive[:5],
        "examples_only_in_parquet": only_parquet[:5],
        "field_differences": dict(sorted(differences.items())),
        "same_trades": same_trades,
        "identical": same_trades and not differences,
    }


class _LockHeld(ArchiveImportRefused):
    """The lock file exists: another import holds it, or a crashed one left it."""


def _acquire_lock(path: Path) -> None:
    """Create the lock file exclusively; never waits and never takes over an existing one."""

    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = path.read_text(encoding="utf-8").strip() or "being created"
        except OSError:
            holder = "unreadable"
        raise _LockHeld(
            "LOCK", f"another import holds {path.name} ({holder}); if no import is running, confirm that and remove {path}"
        ) from None
    except OSError as exc:
        raise ArchiveImportRefused("LOCK", f"cannot create the lock {path}: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} host={socket.gethostname()} at={_utc_text(datetime.now(timezone.utc))}")


def day_lock(dataset_root: Path, day: str):
    """The lock of one legacy day (``dt=<day>``), the one the ordinary import and the rebuild import take; it never waits."""

    return _day_lock(dataset_root / f".import-dt={day}.lock")


@contextmanager
def _day_lock(path: Path) -> Iterator[None]:
    """Exclusive lock on one day: a held or left-over lock refuses the import."""

    _acquire_lock(path)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def _first_registration_guard(connection: Any, identity: Any, dataset_root: Path, *, timeout: float = 120.0) -> Iterator[None]:
    """Serialize the S13 seal while the instrument's dataset is not in the catalog yet.

    S13 registers the dataset row on the first seal, and two seals racing to do it end in a unique-key violation for the second
    (fail-closed, but the day is lost). While the dataset is unregistered the imports of the instrument take this lock, one at a
    time, waiting at most ``timeout`` seconds; once it is registered nothing is held and days run in parallel.
    """

    if _dataset_registered(connection, identity):
        yield
        return
    path = dataset_root / ".import-dataset-registration.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            _acquire_lock(path)
            break
        except _LockHeld:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _ensure_dataset_manifest(path: Path, identity: Any, created_at: str) -> None:
    """Create the shared dataset manifest at most once: the first writer wins, every other import reads the same bytes.

    The manifest is written to a private temporary name and linked into place; ``link`` fails when the target exists, so the
    manifest the catalog registers is never replaced by a concurrent import of another day of the same instrument.
    """

    if path.exists():
        return
    temporary = path.with_name(f".dataset-manifest.{uuid.uuid4().hex}.json")
    try:
        emit_dataset_manifest(
            temporary, dataset_identity=identity, created_at=created_at,
            schema_version="dataset-manifest-v2", origin=DATASET_ORIGIN, transform=DATASET_TRANSFORM,
        )
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _dataset_registered(connection: Any, identity: Any) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM catalog.datasets WHERE layer = %s AND kind = %s AND venue = %s AND instrument = %s "
            "AND schema_id = %s AND feature_set_def_id IS NULL LIMIT 1",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id),
        )
        row = cursor.fetchone()
    connection.rollback()
    return row is not None


def _refuse_if_in_catalog(connection: Any, identity: Any, partition_key: str, instrument: str) -> None:
    if _partition_exists(connection, identity, partition_key):
        raise ArchiveImportRefused(
            "CATALOG", f"{instrument} {partition_key} already has a partition in the catalog; the archive does not replace it"
        )


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
            "AND d.feature_set_def_id IS NULL AND (p.partition_key = %s OR p.partition_key LIKE %s) LIMIT 1",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
             partition_key, f"{partition_key}/%"),
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
    "day_lock",
    "REBUILD_SEGMENT",
    "import_bybit_archive_day",
    "import_bybit_archive_day_after_retirement",
    "rebuild_partition_key",
]
