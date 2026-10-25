"""R02 (DG-R): the operator-run rebuild of explicitly selected, closed legacy live days (ADR-0074 Amendment 2, A2.2 and A2.4 to A2.5).

A legacy live day (``dt=<day>``, many superseded revisions, one ``valid`` revision that holds only the day's last batch) is rebuilt
from the Bybit public archive, one day at a time, in this order: **retire** the damaged live revision with the ``R01`` reassessment
(``valid -> invalid``, nothing deleted), **import** the whole archive day as revision 1 of ``dt=<day>/seg=000000.000`` through the
ordinary S13/S14 path, **verify** it field by field against the archive and by a strict ``DataGateway`` read.

* ``plan_rebuild`` is read-only. It records, per day, the incumbent live revision, the archive file, the producing commit, the key to
  create and the result of every refusal below; its digest is what the executor must present. ``execute_rebuild`` recomputes the plan
  from the current catalog, files and clock: a changed incumbent, source, catalog or a day that closed in between makes the plan stale
  and nothing is done. The executor never acquires another target and never widens the set of days.
* A day is refused, and nothing is changed for it, if it is not closed in UTC (its end is less than 24 hours before execution); it has
  a live slice, a checkpoint binding to one of its partitions or an open gap record that names it; its live revision is not ``valid``
  (or ``invalid`` retired by this operation for the same archive); conservation fails; or the archive fails the strict parsing.
  **Conservation:** every record of the incumbent, by ``(exchange_ts, trade_id)``, exists in the archive file of its own UTC day with
  the same venue, instrument, exchange time (millisecond), side, price and size, and that day is readable afterwards (rebuilt in the
  same plan or already valid); any difference refuses the day, there is no partial acceptance.
* One day at a time; no lock is held across days or while the archive is read or verified. A crash between the retire and the import is
  recovered by running again (the retired day is imported); a crash between the import and the verification, by running again (the
  valid key is found and verified). The restore (a superseding ``pass``) is :func:`restore_retired_day`, never automatic.

The rows rebuilt carry the archive's null ``receive_ts`` and ``sequence``; the provider ``sequence`` of the rows the incumbent served
is lost in the served data (declared and accepted in A2.4; the old bytes are kept). Nothing is deleted and no retention is touched.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from quant_platform.access.catalog import Catalog
from quant_platform.access.gateway import DataGateway
from quant_platform.access.models import DataRequest, LifecyclePolicy
from quant_platform.application.bybit_archive_import import (
    ArchiveImportRefused,
    compare_archive_day_with_parquet,
    import_bybit_archive_day_after_retirement,
    rebuild_partition_key,
)
from quant_platform.application.live_gap_orchestration import load_open_long_gap_records
from quant_platform.canonical import canonical_bytes
from quant_platform.data import Instant
from quant_platform.data.parquet import scan_trade_v1_all
from quant_platform.data.quality_reassessment import (
    REASON_LEGACY_INCOMPLETE_DAY,
    LifecycleInputs,
    ReassessmentResult,
    load_lifecycle_inputs,
    reassess_live_partition,
)
from quant_platform.operations.checkpoint import CheckpointStore
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY
from quant_platform.source_adapters.bybit_archive import (
    bybit_archive_dataset_identity,
    bybit_archive_file_name,
    bybit_archive_url,
    read_bybit_archive_day,
)

INSTRUMENT = "BTCUSDT"  # the damage is the legacy BTCUSDT live path's; the amendment names no other instrument
PLAN_SCHEMA = "bybit-legacy-day-rebuild-plan-v1"
MAX_DAYS_PER_PLAN = 31
_DAY_NS = 86_400 * 10**9
_MS_NS = 10**6
_EXAMPLES = 5


class RebuildRefused(RuntimeError):
    """The plan, the execution or the restore is refused; ``phase`` names the step. Nothing was changed by the refused step."""

    def __init__(self, phase: str, message: str):
        self.phase = phase
        super().__init__(f"{phase}: {message}")


# ------------------------------------------------------------------------------------------------------------ catalog views


def _identity():
    return bybit_archive_dataset_identity(INSTRUMENT)


def _storage_root(connection: Any, storage_root_id: str) -> Path:
    with connection.cursor() as cursor:
        cursor.execute("SELECT abs_path FROM catalog.storage_roots WHERE storage_root_id = %s", (storage_root_id,))
        row = cursor.fetchone()
    connection.rollback()
    if row is None:
        raise RebuildRefused("REQUEST", f"storage root {storage_root_id!r} is not registered")
    return Path(str(row[0]))


def _dataset_root(abs_path: Path) -> Path:
    identity = _identity()
    return abs_path.joinpath(identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id)


def _day_partitions(connection: Any, day: str) -> list[dict[str, Any]]:
    """Every partition of the dataset whose key is ``dt=<day>`` or under it, superseded ones included, by key and revision."""

    identity = _identity()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.partition_id::text, p.partition_key, p.revision, p.state, p.rel_path, p.ts_start, p.ts_end, p.row_count, "
            "p.content_sha256, p.manifest_sha256 FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.layer = %s AND d.kind = %s AND d.venue = %s AND d.instrument = %s AND d.schema_id = %s "
            "AND d.feature_set_def_id IS NULL AND (p.partition_key = %s OR p.partition_key LIKE %s) "
            "ORDER BY p.partition_key, p.revision",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
             f"dt={day}", f"dt={day}/%"),
        )
        rows = cursor.fetchall()
    connection.rollback()
    return [
        {"partition_id": str(r[0]), "key": r[1], "revision": int(r[2]), "state": r[3], "rel_path": r[4],
         "ts_start": Instant.parse(r[5]).isoformat(), "ts_end": Instant.parse(r[6]).isoformat(), "row_count": int(r[7]),
         "content_sha256": str(r[8]).strip(), "manifest_sha256": str(r[9]).strip()}
        for r in rows
    ]


def _live_rows(rows: Sequence[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r["key"] == key and r["state"] != "superseded"]


def _readable_without_us(connection: Any, day: str) -> bool:
    """The neighbouring day is already served: a ``valid`` partition of its legacy key or of the key a rebuild creates."""

    identity = _identity()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.layer = %s AND d.kind = %s AND d.venue = %s AND d.instrument = %s AND d.schema_id = %s "
            "AND d.feature_set_def_id IS NULL AND p.state = 'valid' AND p.partition_key IN (%s, %s) LIMIT 1",
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
             f"dt={day}", rebuild_partition_key(day)),
        )
        row = cursor.fetchone()
    connection.rollback()
    return row is not None


def _assessment_scopes(connection: Any, partition_id: str) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT check_suite, metrics->>'certification_profile' FROM catalog.quality_reports WHERE partition_id = %s",
            (partition_id,),
        )
        rows = cursor.fetchall()
    connection.rollback()
    return sorted((str(a), str(b)) for a, b in rows)


def _retired_for(connection: Any, partition_id: str, archive_sha256: str) -> bool:
    """The partition carries a ``fail`` of this operation (the legacy reason) measured against this very archive file."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT violations FROM catalog.quality_reports WHERE partition_id = %s AND status = 'fail'", (partition_id,))
        rows = cursor.fetchall()
    connection.rollback()
    for (violations,) in rows:
        for item in violations or ():
            if (isinstance(item, Mapping) and item.get("message") == REASON_LEGACY_INCOMPLETE_DAY
                    and isinstance(item.get("archive"), Mapping) and item["archive"].get("sha256") == archive_sha256):
                return True
    return False


def _manifest_files(dataset_root: Path, rel_path: str, manifest_sha256: str | None = None) -> tuple[Path, Path]:
    """The partition and coverage manifests that belong to an artifact ``<dir>/part-<suffix>.parquet`` (``001`` is unsuffixed).

    Two layouts exist. The importer and the live slices write them beside the artifact; the live publisher before the slices (#510)
    wrote ``partition-manifest-<HHMMSS>.json`` and ``coverage-manifest-<HHMMSS>.json`` in the root of the dataset, the time without
    the date. The pair beside the artifact is looked at first; with the catalog's manifest hash, the first location whose partition
    manifest has that hash wins, so a root file that a later day overwrote (the same second) is never taken for this revision's."""

    artifact = dataset_root / rel_path
    stem = artifact.stem  # part-<suffix>
    suffix = stem.split("-", 1)[1] if "-" in stem else "001"
    if suffix == "001":
        return artifact.parent / "partition-manifest.json", artifact.parent / "coverage-manifest.json"
    names = (f"partition-manifest-{suffix}.json", f"coverage-manifest-{suffix}.json")
    candidates = [(artifact.parent / names[0], artifact.parent / names[1])]
    if artifact.parent != dataset_root:
        candidates.append((dataset_root / names[0], dataset_root / names[1]))
    if manifest_sha256 is not None:
        for partition_manifest, coverage_manifest in candidates:
            if partition_manifest.is_file() and _sha256_file(partition_manifest) == manifest_sha256:
                return partition_manifest, coverage_manifest
    for partition_manifest, coverage_manifest in candidates:
        if partition_manifest.is_file():
            return partition_manifest, coverage_manifest
    return candidates[0]


# ------------------------------------------------------------------------------------------------------------ the checks


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _differences(archive: Any, live: Any) -> tuple[list[str], bool]:
    """``(value differences, text-only difference)`` of one trade between the archive and the incumbent."""

    differences = [name for name in ("venue", "instrument", "aggressor_side") if getattr(archive, name) != getattr(live, name)]
    if archive.exchange_ts.epoch_ns != live.exchange_ts.epoch_ns // _MS_NS * _MS_NS:
        differences.append("exchange_ts_ms")
    text_only = False
    for name in ("price", "size"):
        if getattr(archive, name) != getattr(live, name):
            if Decimal(getattr(archive, name)) != Decimal(getattr(live, name)):
                differences.append(f"{name}_value")
            else:
                text_only = True
    return differences, text_only


def _row(record: Any) -> tuple[Any, ...]:
    return (record.venue, record.instrument, record.aggressor_side, record.price, record.size, record.exchange_ts.epoch_ns)


def _conservation(
    connection: Any, incumbent_file: Path, day: str, archive_dir: Path, plan_days: Sequence[str],
) -> tuple[list[str], dict[str, Any]]:
    """Refusals and a summary: every record of the incumbent is in the archive file of its own day, equal in the common fields."""

    refusals: list[str] = []
    incumbent: dict[str, Any] = {}
    duplicated = 0
    for batch in scan_trade_v1_all(incumbent_file, batch_size=65_536):
        for record in batch:
            if record.trade_id is None:
                refusals.append("a record of the incumbent has no trade_id and cannot be compared with the archive")
                return refusals, {}
            first = incumbent.setdefault(record.trade_id, record)
            if first is not record:
                duplicated += 1
                if _row(first) != _row(record):
                    refusals.append(f"the incumbent holds trade_id {record.trade_id!r} twice with different content")
                    return refusals, {}
    by_day: dict[str, list[Any]] = {}
    for record in incumbent.values():
        by_day.setdefault(Instant(record.exchange_ts.epoch_ns // _DAY_NS * _DAY_NS).isoformat()[:10], []).append(record)
    summary: dict[str, Any] = {"rows": len(incumbent), "duplicate_trade_ids": duplicated, "days": {}, "text_only_differences": 0}
    for owner in sorted(by_day):
        records = by_day[owner]
        summary["days"][owner] = len(records)
        archive_path = archive_dir / bybit_archive_file_name(INSTRUMENT, owner)
        if not archive_path.is_file():
            refusals.append(f"{len(records)} records of the incumbent belong to {owner}, whose archive file {archive_path.name} is missing")
            continue
        try:
            parsed = read_bybit_archive_day(archive_path, instrument=INSTRUMENT, day=owner)
        except Exception as exc:  # noqa: BLE001 - a malformed neighbour file refuses the day, never crashes the plan
            refusals.append(f"the archive file of {owner} fails the strict parsing: {exc}")
            continue
        wanted = {r.trade_id for r in records}
        found = {r.trade_id: r for r in parsed.records if r.trade_id in wanted}
        del parsed
        absent = sorted(wanted - set(found))
        if absent:
            refusals.append(f"{len(absent)} records of the incumbent are not in the archive file of {owner} (e.g. {absent[:_EXAMPLES]})")
        differing = []
        for record in records:
            if record.trade_id in found:
                names, text_only = _differences(found[record.trade_id], record)
                summary["text_only_differences"] += int(text_only)
                if names:
                    differing.append((record.trade_id, names))
        if differing:
            refusals.append(f"{len(differing)} records of the incumbent differ from the archive of {owner} (e.g. {differing[:_EXAMPLES]})")
        if owner != day and owner not in plan_days and not _readable_without_us(connection, owner):
            refusals.append(f"the incumbent holds {len(records)} records of {owner}, which is neither rebuilt in this plan nor already valid")
    return refusals, summary


def _day_text(day: str) -> tuple[int, int]:
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    start_ns = int(start.timestamp()) * 10**9
    return start_ns, start_ns + _DAY_NS


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------------------------------------------------ the plan


def _validated_days(days: Sequence[str]) -> list[str]:
    if not days:
        raise RebuildRefused("REQUEST", "at least one day must be named: the rebuild never chooses its own days")
    if len(days) > MAX_DAYS_PER_PLAN:
        raise RebuildRefused("REQUEST", f"a plan names at most {MAX_DAYS_PER_PLAN} days")
    if len(set(days)) != len(days) or list(days) != sorted(days):
        raise RebuildRefused("REQUEST", "the days must be unique and in ascending order")
    for day in days:
        try:
            bybit_archive_file_name(INSTRUMENT, day)
        except Exception as exc:
            raise RebuildRefused("REQUEST", f"{day!r}: {exc}") from exc
    return list(days)


_INCUMBENT_IDENTITY = (
    "partition_id", "key", "revision", "rel_path", "ts_start", "ts_end", "row_count", "content_sha256", "manifest_sha256",
    "check_suite", "certification_profile", "partition_manifest_file", "coverage_manifest_file", "coverage_manifest_sha256",
    "coverage_ids", "assertion_ids",
)


def _digest(plan: Mapping[str, Any]) -> str:
    """The digest binds what the operation is about (the incumbent's identity, the archive, the checks, the commit), not how far it has
    got: the state of the incumbent and the action are progress, so a plan stays current after a retire that was not followed by the
    import (the crash of A2.5) and is stale as soon as anything the operation depends on changes."""

    days = []
    for entry in plan["days"]:
        incumbent = entry.get("incumbent")
        days.append({
            "day": entry["day"], "key_to_create": entry["key_to_create"], "archive": entry.get("archive"),
            "conservation": entry.get("conservation"), "refusals": entry.get("refusals"),
            "incumbent": None if incumbent is None else {k: incumbent.get(k) for k in _INCUMBENT_IDENTITY},
        })
    body = {key: plan[key] for key in ("schema", "instrument", "storage_root_id", "code_ref")}
    body["days"] = days
    return hashlib.sha256(canonical_bytes(body, profile="sorted-compact-ascii-v1", allow_nan=False)).hexdigest()


def plan_rebuild(
    connection: Any,
    *,
    days: Sequence[str],
    archive_dir: str | Path,
    storage_root_id: str,
    checkpoint_path: str | Path,
    code_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The read-only plan for the named days, with its digest. Writes nothing and holds no lock."""

    days = _validated_days(days)
    if not isinstance(code_ref, str) or not code_ref.strip():
        raise RebuildRefused("REQUEST", "the plan names the producing commit (code_ref)")
    now = now or datetime.now(timezone.utc)
    archive_dir = Path(archive_dir)
    abs_path = _storage_root(connection, storage_root_id)
    checkpoint = CheckpointStore(Path(checkpoint_path)).load() if Path(checkpoint_path).exists() else None
    gaps = load_open_long_gap_records(storage_root=abs_path, instrument=INSTRUMENT)
    entries = [
        _plan_day(connection, day, days, archive_dir, abs_path, storage_root_id, checkpoint, gaps, now) for day in days
    ]
    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "instrument": INSTRUMENT,
        "storage_root_id": storage_root_id,
        "code_ref": code_ref.strip(),
        "days": entries,
    }
    plan["digest"] = _digest(plan)
    return plan


def _plan_day(
    connection: Any, day: str, plan_days: Sequence[str], archive_dir: Path, abs_path: Path, storage_root_id: str,
    checkpoint: Any, gaps: Any, now: datetime,
) -> dict[str, Any]:
    refusals: list[str] = []
    start_ns, end_ns = _day_text(day)
    key = rebuild_partition_key(day)
    legacy_key = f"dt={day}"
    entry: dict[str, Any] = {"day": day, "key_to_create": key, "incumbent": None, "archive": None, "conservation": None, "action": "refused"}

    if end_ns > int((now - timedelta(hours=24)).timestamp()) * 10**9:
        refusals.append("the day is not closed in UTC: its end is less than 24 hours before the execution")

    rows = _day_partitions(connection, day)
    slices = sorted({r["key"] for r in rows if r["key"] != legacy_key and r["key"] != key})
    if slices:
        refusals.append(f"the day has live slices: {', '.join(slices)}")
    created = _live_rows(rows, key)
    if checkpoint is not None and (checkpoint.partition_key == legacy_key or str(checkpoint.partition_key).startswith(f"{legacy_key}/")):
        refusals.append(f"the live checkpoint is bound to {checkpoint.partition_key} of this day")
    if gaps.unparseable_manifest_paths:
        refusals.append("a long-gap manifest cannot be read, so no open gap can be excluded")
    for record in gaps.records:
        gap_start = Instant.parse(record.interval.coverage_start).epoch_ns
        gap_end = Instant.parse(record.interval.coverage_end).epoch_ns
        if gap_start < end_ns and start_ns < gap_end:
            refusals.append(f"an open gap record ({record.coverage_id}) names this day")

    # the archive file of the day: strict parsing, identity, hash
    archive_path = archive_dir / bybit_archive_file_name(INSTRUMENT, day)
    archive: dict[str, Any] | None = None
    if not archive_path.is_file():
        refusals.append(f"the archive file {archive_path.name} is not in the archive directory")
    else:
        try:
            parsed = read_bybit_archive_day(archive_path, instrument=INSTRUMENT, day=day)
            evidence = parsed.evidence
            archive = {
                "file_name": evidence.file_name, "url": bybit_archive_url(INSTRUMENT, day), "sha256": evidence.file_sha256,
                "size_bytes": evidence.file_size_bytes, "rows": evidence.row_count,
                "observed_at": _utc_text(datetime.fromtimestamp(archive_path.stat().st_mtime, timezone.utc)),
            }
            del parsed
        except Exception as exc:  # noqa: BLE001 - the strict parsing refusing the file refuses the day
            refusals.append(f"the archive file fails the strict parsing: {exc}")
    entry["archive"] = archive

    # the incumbent live revision of the legacy key
    live = _live_rows(rows, legacy_key)
    incumbent: dict[str, Any] | None = None
    if len(live) != 1:
        refusals.append(f"{legacy_key} must have exactly one live revision; it has {len(live)}")
    else:
        incumbent = dict(live[0])
        if incumbent["state"] not in ("valid", "invalid"):
            refusals.append(f"the live revision of {legacy_key} is {incumbent['state']}, not valid")
        elif incumbent["state"] == "invalid" and (archive is None or not _retired_for(connection, incumbent["partition_id"], archive["sha256"])):
            refusals.append(f"the live revision of {legacy_key} is invalid but was not retired by this operation for this archive file")
        scopes = _assessment_scopes(connection, incumbent["partition_id"])
        if len(scopes) != 1:
            refusals.append(f"the incumbent's assessments name {len(scopes)} suite/profile pairs; exactly one is required")
        else:
            incumbent["check_suite"], incumbent["certification_profile"] = scopes[0]
        refusals += _incumbent_evidence(connection, abs_path, incumbent)
        incumbent_file = _dataset_root(abs_path) / incumbent["rel_path"]
        if incumbent_file.is_file() and _sha256_file(incumbent_file) == incumbent["content_sha256"]:
            found, summary = _conservation(connection, incumbent_file, day, archive_dir, plan_days)
            refusals += found
            entry["conservation"] = summary
    entry["incumbent"] = incumbent
    if created:
        if len(created) != 1 or created[0]["state"] != "valid":
            refusals.append(f"the key to create, {key}, exists in the state {[r['state'] for r in created]}")
        elif incumbent is not None and incumbent["state"] == "valid":
            refusals.append(f"{key} is valid while {legacy_key} is valid too: two valid partitions cover the day")
        entry["created"] = {"partition_id": created[0]["partition_id"], "state": created[0]["state"]} if created else None
    entry["refusals"] = refusals
    if not refusals:
        if created:
            entry["action"] = "verify"
        elif incumbent is not None and incumbent["state"] == "invalid":
            entry["action"] = "import"
        else:
            entry["action"] = "retire+import"
    return entry


def _certified_coverage(connection: Any, partition_id: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """The coverage manifest hashes and ids the incumbent's quality reports certified (the distinct pairs; one is expected)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT metrics->'coverage_manifest_sha256', metrics->'coverage_manifest_ids' FROM catalog.quality_reports "
            "WHERE partition_id = %s AND metrics ? 'coverage_manifest_sha256'",
            (partition_id,),
        )
        rows = cursor.fetchall()
    connection.rollback()
    return sorted((tuple(map(str, hashes or ())), tuple(map(str, ids or ()))) for hashes, ids in rows)


def _incumbent_evidence(connection: Any, abs_path: Path, incumbent: dict[str, Any]) -> list[str]:
    """The incumbent's file and durable manifests exist, load, are the ones the catalog holds and the coverage the reports certified."""

    problems: list[str] = []
    dataset_root = _dataset_root(abs_path)
    artifact = dataset_root / incumbent["rel_path"]
    if not artifact.is_file():
        problems.append(f"the incumbent's Parquet {incumbent['rel_path']} is not on disk")
    elif _sha256_file(artifact) != incumbent["content_sha256"]:
        problems.append("the incumbent's Parquet does not hash to the catalog's content hash")
    partition_manifest, coverage_manifest = _manifest_files(dataset_root, incumbent["rel_path"], incumbent["manifest_sha256"])
    for label, path in (("partition", partition_manifest), ("coverage", coverage_manifest), ("dataset", dataset_root / "dataset-manifest.json")):
        if not path.is_file():
            problems.append(f"the incumbent's {label} manifest {path.name} is not on disk")
    if partition_manifest.is_file():
        if _sha256_file(partition_manifest) != incumbent["manifest_sha256"]:
            problems.append("the incumbent's partition manifest does not hash to the catalog's manifest hash")
        incumbent["partition_manifest_file"] = str(partition_manifest.relative_to(dataset_root)).replace("\\", "/")
    if coverage_manifest.is_file():
        incumbent["coverage_manifest_file"] = str(coverage_manifest.relative_to(dataset_root)).replace("\\", "/")
        incumbent["coverage_manifest_sha256"] = _sha256_file(coverage_manifest)
    if not problems:
        certified = _certified_coverage(connection, incumbent["partition_id"])
        if len(certified) != 1:
            problems.append(f"the incumbent's quality reports certify {len(certified)} coverage manifest sets; exactly one is required")
        else:
            try:
                loaded = load_lifecycle_inputs(
                    dataset_root / "dataset-manifest.json", partition_manifest, [coverage_manifest],
                )
            except Exception as exc:  # noqa: BLE001 - a manifest that does not load refuses the day in the plan, not in the retire
                problems.append(f"the incumbent's durable manifests do not load: {exc}")
            else:
                hashes, ids = certified[0]
                if loaded.coverage_sha256 != hashes or tuple(loaded.coverage_ids) != ids:
                    problems.append("the incumbent's coverage manifest is not the one its quality reports certified")
                incumbent["coverage_ids"] = list(loaded.coverage_ids)
                incumbent["assertion_ids"] = list(loaded.assertion_ids)
    return problems


# ------------------------------------------------------------------------------------------------------------ execution


def execute_rebuild(
    connection: Any,
    *,
    plan: Mapping[str, Any],
    digest: str,
    archive_dir: str | Path,
    checkpoint_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute a reviewed plan, one day at a time; refuses a plan that is not the one presented or no longer current."""

    if plan.get("schema") != PLAN_SCHEMA:
        raise RebuildRefused("PLAN", "not a legacy-day rebuild plan")
    if _digest(plan) != plan.get("digest") or plan.get("digest") != digest:
        raise RebuildRefused("PLAN", "the plan does not match the digest presented")
    current = plan_rebuild(
        connection, days=[entry["day"] for entry in plan["days"]], archive_dir=archive_dir,
        storage_root_id=plan["storage_root_id"], checkpoint_path=checkpoint_path, code_ref=plan["code_ref"], now=now,
    )
    if current["digest"] != digest:
        raise RebuildRefused("STALE", "the incumbent, the archive, the catalog or the clock changed since the plan; plan again")
    results: list[dict[str, Any]] = []
    for entry in current["days"]:  # the live state of the same targets: a day already retired or rebuilt resumes where it stopped
        result = _execute_day(connection, entry, current, Path(archive_dir))
        results.append(result)
        if result["outcome"] not in ("rebuilt", "verified", "refused"):
            break  # a failure stops the run: the operator decides, the executor does not go on to the next day
    return {"plan_digest": digest, "days": results}


def _execute_day(connection: Any, entry: Mapping[str, Any], plan: Mapping[str, Any], archive_dir: Path) -> dict[str, Any]:
    day = entry["day"]
    result: dict[str, Any] = {"day": day, "key": entry["key_to_create"], "action": entry["action"]}
    if entry["action"] == "refused":
        return {**result, "outcome": "refused", "refusals": list(entry["refusals"])}
    archive_path = archive_dir / entry["archive"]["file_name"]
    steps: list[str] = []
    if entry["action"] == "retire+import":
        try:
            retired = _retire(connection, entry, plan)
        except Exception as exc:  # noqa: BLE001 - reported with its phase; nothing else is attempted for this day
            return {**result, "outcome": "retire_failed", "detail": f"{type(exc).__name__}: {exc}", "steps": steps}
        steps.append("retired" if retired.inserted else "already retired")
    if entry["action"] in ("retire+import", "import"):
        try:
            report = import_bybit_archive_day_after_retirement(
                connection, instrument=INSTRUMENT, day=day, archive_path=archive_path, storage_root_id=plan["storage_root_id"],
                code_ref=plan["code_ref"],
            )
        except ArchiveImportRefused as exc:
            return {**result, "outcome": "import_refused", "phase": exc.phase, "detail": str(exc), "steps": steps}
        steps.append(f"imported {report.row_count} rows as {report.state}")
        if report.state != "valid":
            return {**result, "outcome": "import_not_valid", "detail": report.state, "steps": steps}
    verified = verify_rebuilt_day(connection, day=day, archive_dir=archive_dir, storage_root_id=plan["storage_root_id"])
    steps.append("verified" if verified["passed"] else "verification failed")
    outcome = ("verified" if entry["action"] == "verify" else "rebuilt") if verified["passed"] else "verify_failed"
    return {**result, "outcome": outcome, "steps": steps, "verification": verified}


def _incumbent_inputs(abs_path: Path, incumbent: Mapping[str, Any]) -> LifecycleInputs:
    dataset_root = _dataset_root(abs_path)
    return load_lifecycle_inputs(
        dataset_root / "dataset-manifest.json", dataset_root / incumbent["partition_manifest_file"],
        [dataset_root / incumbent["coverage_manifest_file"]],
    )


def _retire(connection: Any, entry: Mapping[str, Any], plan: Mapping[str, Any]) -> ReassessmentResult:
    incumbent = entry["incumbent"]
    abs_path = _storage_root(connection, plan["storage_root_id"])
    return reassess_live_partition(
        connection, inputs=_incumbent_inputs(abs_path, incumbent),
        coverage_start=Instant.parse(incumbent["ts_start"]), coverage_end=Instant.parse(incumbent["ts_end"]),
        storage_root_id=plan["storage_root_id"], expected_profile=incumbent["certification_profile"],
        expected_check_suite=incumbent["check_suite"], status="fail", code_ref=plan["code_ref"],
        archive_identity={**{k: entry["archive"][k] for k in ("url", "sha256", "size_bytes", "rows", "file_name")},
                          "plan_digest": plan["digest"]},
    )


# ------------------------------------------------------------------------------------------------------------ verify, restore


def verify_rebuilt_day(connection: Any, *, day: str, archive_dir: str | Path, storage_root_id: str) -> dict[str, Any]:
    """Read-only. The imported records equal the archive field by field, and a strict gateway read returns them exactly."""

    archive_path = Path(archive_dir) / bybit_archive_file_name(INSTRUMENT, day)
    key = rebuild_partition_key(day)
    report: dict[str, Any] = {"day": day, "key": key, "passed": False, "problems": []}
    created = [r for r in _day_partitions(connection, day) if r["key"] == key and r["state"] != "superseded"]
    if len(created) != 1 or created[0]["state"] != "valid":
        report["problems"].append(f"{key} has no single valid revision ({[r['state'] for r in created]})")
        return report
    parquet = _dataset_root(_storage_root(connection, storage_root_id)) / created[0]["rel_path"]
    compared = compare_archive_day_with_parquet(archive_path, parquet, instrument=INSTRUMENT, day=day)
    report["comparison"] = {k: compared[k] for k in ("archive_rows", "parquet_rows", "only_in_archive", "only_in_parquet",
                                                     "field_differences", "same_trades", "identical")}
    if not compared["identical"]:
        report["problems"].append("the imported Parquet is not identical to the archive file, field by field")
    start_ns, end_ns = _day_text(day)
    request = DataRequest(
        _identity(), Instant(start_ns), Instant(end_ns), schema_requirement="trade-v1", lifecycle_policy=LifecyclePolicy.VALID_ONLY,
        coverage_policy="strict", ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )
    try:
        records = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,)).read(request).records
    except Exception as exc:  # noqa: BLE001 - a refused strict read (a gap, an overlap with a neighbour) is a finding
        report["problems"].append(f"the strict read of the day was refused: {type(exc).__name__}: {exc}")
        connection.rollback()
        return report
    connection.rollback()
    ids = [r.trade_id for r in records]
    keys = [(r.exchange_ts.epoch_ns, r.trade_id) for r in records]
    report["read"] = {"records": len(records), "unique_trade_ids": len(set(ids)), "in_order": keys == sorted(keys)}
    if len(records) != compared["archive_rows"]:
        report["problems"].append(f"the strict read returned {len(records)} records, the archive has {compared['archive_rows']}")
    if len(set(ids)) != len(ids):
        report["problems"].append("the strict read returned a trade more than once")
    if keys != sorted(keys):
        report["problems"].append("the strict read is not in (exchange_ts, trade_id) order")
    report["passed"] = not report["problems"]
    return report


def restore_retired_day(connection: Any, *, day: str, storage_root_id: str, code_ref: str) -> ReassessmentResult:
    """The documented way back: a superseding ``pass`` returns the retired legacy revision to ``valid``. Never automatic.

    Refused while the day has any partition under ``dt=<day>/``: restoring would put two valid partitions over one day."""

    rows = _day_partitions(connection, day)
    under = [r["key"] for r in rows if r["key"] != f"dt={day}"]
    if under:
        raise RebuildRefused("RESTORE", f"{day} has partitions under dt={day}/ ({', '.join(sorted(set(under)))}); restoring would overlap them")
    live = _live_rows(rows, f"dt={day}")
    if len(live) != 1 or live[0]["state"] != "invalid":
        raise RebuildRefused("RESTORE", f"dt={day} must have exactly one live revision and it must be invalid")
    incumbent = dict(live[0])
    scopes = _assessment_scopes(connection, incumbent["partition_id"])
    if len(scopes) != 1:
        raise RebuildRefused("RESTORE", "the incumbent's assessments name more than one suite/profile pair")
    incumbent["check_suite"], incumbent["certification_profile"] = scopes[0]
    abs_path = _storage_root(connection, storage_root_id)
    problems = _incumbent_evidence(connection, abs_path, incumbent)
    if problems:
        raise RebuildRefused("RESTORE", "; ".join(problems))
    return reassess_live_partition(
        connection, inputs=_incumbent_inputs(abs_path, incumbent),
        coverage_start=Instant.parse(incumbent["ts_start"]), coverage_end=Instant.parse(incumbent["ts_end"]),
        storage_root_id=storage_root_id, expected_profile=incumbent["certification_profile"],
        expected_check_suite=incumbent["check_suite"], status="pass", code_ref=code_ref,
    )


__all__ = [
    "INSTRUMENT",
    "MAX_DAYS_PER_PLAN",
    "PLAN_SCHEMA",
    "RebuildRefused",
    "execute_rebuild",
    "plan_rebuild",
    "restore_retired_day",
    "verify_rebuilt_day",
]
