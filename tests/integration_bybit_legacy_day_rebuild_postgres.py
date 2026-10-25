#!/usr/bin/env python3
"""Real PostgreSQL proof of the legacy-day rebuild (DG-R R02, ADR-0074 Amendment 2).

Legacy-shaped days are made with the ordinary importer: a day published from a *partial* archive file is one ``valid`` revision of
``dt=<day>`` that holds only part of the day (the shape of the damaged live days). The rebuild plans, retires (``R01``), imports the
whole archive day as revision 1 of ``dt=<day>/seg=000000.000`` through the ordinary S13/S14 path and verifies it. Every refusal leaves
the catalog, the files and the live checkpoint byte-identical; a crash between the steps is recovered by running the same plan again.
Requires ``DATA_GATEWAY_TEST_DSN`` and a POSIX storage root.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
import quant_platform.application.bybit_legacy_day_rebuild as rebuild  # noqa: E402
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application import bybit_archive_import as importer  # noqa: E402
from quant_platform.application.bybit_archive_import import (  # noqa: E402
    ArchiveImportRefused,
    import_bybit_archive_day,
    import_bybit_archive_day_after_retirement,
)
from quant_platform.application.bybit_legacy_day_rebuild import (  # noqa: E402
    RebuildRefused,
    execute_rebuild,
    plan_rebuild,
    restore_retired_day,
    verify_rebuilt_day,
)
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.models import NoCoverage  # noqa: E402
from quant_platform.data.parquet import scan_trade_v1_all  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore, LiveCheckpointV1, advance_checkpoint  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY  # noqa: E402
from quant_platform.source_adapters.bybit_archive import bybit_archive_dataset_identity, read_bybit_archive_day  # noqa: E402
from quant_platform.source_adapters.bybit_live import bybit_live_dataset_identity  # noqa: E402

HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"
STORAGE_ROOT_ID = "rebuild-hot"
INSTRUMENT = "BTCUSDT"
PILOT = "2026-10-20"
CODE_REF = "r02-commit"


def day_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()) * 1000


def world(day: str, count: int = 400) -> list[tuple[int, str, str, str, str]]:
    """The whole day: ``(ms, trade_id, side, price, size)``; some trades share a millisecond (ties are ordered by trade id)."""

    rows = []
    for i in range(count):
        ms = day_ms(day) + 1000 + i * 200_000
        if i % 7 == 3 and rows:
            ms = rows[-1][0]  # a tie: the same millisecond as the previous trade (ordered by trade id)
        rows.append((ms, f"{day}-{i:06d}", "Buy" if i % 2 == 0 else "Sell", f"{60000 + (i % 500) / 10:.1f}", f"{(i % 9 + 1) / 100:.2f}"))
    return rows


def write_archive(directory: Path, day: str, rows, name: str | None = None) -> Path:
    path = directory / (name or f"{INSTRUMENT}{day}.csv.gz")
    lines = [HEADER] + [f"{ms // 1000}.{ms % 1000:03d},{INSTRUMENT},{side},{size},{price},PlusTick,{trade_id},1,{size},1"
                        for ms, trade_id, side, price, size in rows]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
    return path


def sql(connection, query, params=()):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall() if cursor.description else None
    connection.commit()
    return rows


def snapshot(connection, storage: Path, checkpoint: Path) -> tuple:
    """Everything a refused operation must leave alone: the catalog rows and reports, every file, the checkpoint."""

    rows = sql(connection, "SELECT p.partition_key, p.revision, p.state, p.content_sha256, p.manifest_sha256 FROM catalog.partitions p "
                           "JOIN catalog.datasets d ON d.dataset_id = p.dataset_id WHERE d.instrument = %s ORDER BY 1, 2", (INSTRUMENT,))
    reports = sql(connection, "SELECT p.partition_key, r.status, r.code_ref FROM catalog.quality_reports r JOIN catalog.partitions p "
                              "ON p.partition_id = r.partition_id ORDER BY 1, 2, 3")
    files = sorted((str(p.relative_to(storage)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in storage.rglob("*") if p.is_file())
    return rows, reports, files, checkpoint.read_bytes() if checkpoint.exists() else None


def states(connection) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, state in sql(connection, "SELECT p.partition_key, p.state FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = "
                                       "p.dataset_id WHERE d.instrument = %s ORDER BY p.partition_key, p.revision", (INSTRUMENT,)):
        result.setdefault(key, []).append(state)
    return result


def gateway_ids(connection, day: str, coverage: str = "strict") -> list[str] | None:
    start = day_ms(day) * 1_000_000
    request = DataRequest(bybit_archive_dataset_identity(INSTRUMENT), Instant(start), Instant(start + 86_400 * 10**9),
                          schema_requirement="trade-v1", lifecycle_policy=LifecyclePolicy.VALID_ONLY, coverage_policy=coverage,
                          ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY)
    try:
        return [r.trade_id for r in DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,)).read(request).records]
    except NoCoverage:
        connection.rollback()
        return None


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL LEGACY DAY REBUILD GATE NOT EXECUTED LOCALLY")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        base = Path(holder)
        storage, staging, archives = base / "storage", base / "staging", base / "archives"
        for directory in (storage, staging, archives):
            directory.mkdir()
        checkpoint = storage / "_k10_checkpoints" / "live-ingest-server-v1.json"
        checkpoint.parent.mkdir()
        days = ["2026-10-08", "2026-10-09", "2026-10-18", "2026-10-19", PILOT, "2026-10-21"]
        full = {day: world(day) for day in days}
        with psycopg.connect(dsn) as connection:
            sql(connection, "INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
            sql(connection, "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))
            dataset_root = storage / "canonical" / "trades" / "bybit" / INSTRUMENT / "trade-v1"
            for day in days:
                write_archive(archives, day, full[day])

            def legacy(day: str, subset=None) -> None:
                """A damaged legacy day: one valid ``dt=<day>`` revision that holds only part of the day."""
                rows = subset if subset is not None else full[day][::7]
                path = write_archive(staging, day, rows)
                report = import_bybit_archive_day(connection, instrument=INSTRUMENT, day=day, archive_path=path,
                                                  storage_root_id=STORAGE_ROOT_ID, code_ref="legacy-commit")
                assert report.state == "valid", report
                path.unlink()

            for day in days[:5]:
                legacy(day)
            # the live checkpoint: bound to a partition of a day that is not rebuilt here
            CheckpointStore(checkpoint).save(advance_checkpoint(None, LiveCheckpointV1(
                dataset_identity=bybit_live_dataset_identity(INSTRUMENT), source_semantics_id="bybit-public-trades-websocket-v1",
                last_canonical_exchange_ts=Instant(1000), last_canonical_trade_id="anchor", last_observed_sequence=None,
                catalog_dataset_id="dataset", partition_key="dt=2026-10-22/seg=000000.000", revision=1,
                partition_manifest_sha256="a" * 64, coverage_segment_id="coverage-1", generation=1, created_at=Instant(0))))

            def make_plan(selected, **kwargs):
                return plan_rebuild(connection, days=selected, archive_dir=archives, storage_root_id=STORAGE_ROOT_ID,
                                    checkpoint_path=checkpoint, code_ref=CODE_REF, **kwargs)

            def run(plan, **kwargs):
                return execute_rebuild(connection, plan=plan, digest=plan["digest"], archive_dir=archives, checkpoint_path=checkpoint, **kwargs)

            def refused_plan(selected, reason, **kwargs):
                plan = make_plan(selected, **kwargs)
                entry = plan["days"][0]
                assert entry["action"] == "refused" and any(reason in item for item in entry["refusals"]), (reason, entry["refusals"])
                before = snapshot(connection, storage, checkpoint)
                result = run(plan, **kwargs)
                assert result["days"][0]["outcome"] == "refused", result
                assert snapshot(connection, storage, checkpoint) == before, f"a refused day ({reason}) must change nothing"
                return plan

            # 1. the plan is read-only, bound by a digest, and says what it will do
            before = snapshot(connection, storage, checkpoint)
            plan = make_plan([PILOT])
            assert snapshot(connection, storage, checkpoint) == before, "planning must write nothing"
            entry = plan["days"][0]
            assert entry["action"] == "retire+import" and not entry["refusals"], entry
            assert entry["key_to_create"] == f"dt={PILOT}/seg=000000.000" and entry["incumbent"]["state"] == "valid"
            assert entry["archive"]["rows"] == len(full[PILOT]) and entry["conservation"]["rows"] == len(full[PILOT][::7])
            assert plan == make_plan([PILOT]) and plan["digest"] == make_plan([PILOT])["digest"]
            incumbent_id = entry["incumbent"]["partition_id"]
            old_file = dataset_root / entry["incumbent"]["rel_path"]
            old_bytes = old_file.read_bytes()
            other_days_before = {key: v for key, v in states(connection).items() if key != f"dt={PILOT}"}
            frozen_files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen_files_paths(dataset_root, PILOT)}

            # 2. refusals: a presented digest that is not the plan's, and a plan that is no longer current
            for wrong in ("0" * 64, plan["digest"][:-1] + ("0" if plan["digest"][-1] != "0" else "1")):
                try:
                    execute_rebuild(connection, plan=plan, digest=wrong, archive_dir=archives, checkpoint_path=checkpoint)
                except RebuildRefused as exc:
                    assert exc.phase == "PLAN", exc
                else:
                    raise AssertionError("a wrong digest must be refused")
            tampered = {**plan, "code_ref": "someone-else"}
            try:
                execute_rebuild(connection, plan=tampered, digest=plan["digest"], archive_dir=archives, checkpoint_path=checkpoint)
            except RebuildRefused as exc:
                assert exc.phase == "PLAN"
            else:
                raise AssertionError("an edited plan must be refused")
            assert snapshot(connection, storage, checkpoint) == before

            # 3. the other refusals, each on its own and each leaving everything as it was
            refused_plan([PILOT], "not closed", now=datetime.fromisoformat(PILOT).replace(tzinfo=timezone.utc) + timedelta(hours=30))
            sql(connection, "UPDATE catalog.partitions SET state='closed' WHERE partition_id=%s", (incumbent_id,))
            plan_closed = make_plan([PILOT])
            assert plan_closed["days"][0]["action"] == "refused" and any("not valid" in r for r in plan_closed["days"][0]["refusals"])
            sql(connection, "UPDATE catalog.partitions SET state='valid' WHERE partition_id=%s", (incumbent_id,))
            # a live slice of the day
            sql(connection, "INSERT INTO catalog.partitions (dataset_id, partition_key, revision, storage_root_id, rel_path, ts_start, ts_end, "
                            "row_count, byte_size, content_sha256, state, manifest_sha256, created_at, closed_at, producer, code_ref) "
                            "SELECT dataset_id, %s, 1, storage_root_id, replace(rel_path, %s, %s), ts_start, ts_start, 0, byte_size, "
                            "content_sha256, 'valid', manifest_sha256, created_at, closed_at, producer, code_ref FROM catalog.partitions "
                            "WHERE partition_id=%s", (f"dt={PILOT}/seg=120000.000", f"dt={PILOT}/", f"dt={PILOT}/seg=120000.000/", incumbent_id))
            refused_plan([PILOT], "live slices")
            try:
                import_bybit_archive_day_after_retirement(connection, instrument=INSTRUMENT, day=PILOT, archive_path=archives / f"{INSTRUMENT}{PILOT}.csv.gz",
                                                          storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG"
            else:
                raise AssertionError("the variant must refuse a day with a live slice")
            sql(connection, "DELETE FROM catalog.partitions WHERE partition_key=%s", (f"dt={PILOT}/seg=120000.000",))
            assert states(connection)[f"dt={PILOT}"] == ["valid"]

            # a checkpoint bound to a partition of the day
            saved = checkpoint.read_bytes()
            checkpoint.unlink()
            CheckpointStore(checkpoint).save(advance_checkpoint(None, LiveCheckpointV1(
                dataset_identity=bybit_live_dataset_identity(INSTRUMENT), source_semantics_id="bybit-public-trades-websocket-v1",
                last_canonical_exchange_ts=Instant(2000), last_canonical_trade_id="anchor2", last_observed_sequence=None,
                catalog_dataset_id="dataset", partition_key=f"dt={PILOT}/seg=000100.000", revision=1,
                partition_manifest_sha256="b" * 64, coverage_segment_id="coverage-1", generation=1, created_at=Instant(0))))
            refused_plan([PILOT], "checkpoint")
            checkpoint.write_bytes(saved)

            # an open gap record that names the day
            from quant_platform.application.live_gap_orchestration import LongGapInterval, TradeKeyBoundary, record_explicit_long_gap

            gap_at = Instant((day_ms(PILOT) + 3_600_000) * 1_000_000)
            record_explicit_long_gap(
                storage_root=storage, producer="gap-probe", code_ref="gap", detected_at=Instant(gap_at.epoch_ns + 5 * 10**9),
                interval=LongGapInterval(start_key=TradeKeyBoundary(venue="bybit", instrument=INSTRUMENT, exchange_ts=gap_at, trade_id="x"),
                                         detected_at=Instant(gap_at.epoch_ns + 5 * 10**9), reason="probe", recent_window_records=None,
                                         buffered_ws_records=None), instrument=INSTRUMENT)
            refused_plan([PILOT], "open gap record")
            for gap_file in dataset_root.glob("coverage-manifest-long-gap-*.json"):
                gap_file.unlink()

            # the archive file: missing, malformed, a row of the incumbent that is not in it, a differing common field
            real_archive = archives / f"{INSTRUMENT}{PILOT}.csv.gz"
            kept = real_archive.read_bytes()
            real_archive.unlink()
            refused_plan([PILOT], "is not in the archive directory")
            real_archive.write_bytes(gzip.compress(b"not,a,valid,archive\n"))
            refused_plan([PILOT], "strict parsing")
            write_archive(archives, PILOT, [row for row in full[PILOT] if row not in full[PILOT][::7][3:4]])
            refused_plan([PILOT], "are not in the archive file")
            changed = [(ms, i, s, "99999.9" if n == 14 else p, z) for n, (ms, i, s, p, z) in enumerate(full[PILOT])]
            write_archive(archives, PILOT, changed)
            refused_plan([PILOT], "differ from the archive")
            real_archive.write_bytes(kept)

            # 4. a plan is stale when the source changed after it was made
            stale = make_plan([PILOT])
            shifted = real_archive.stat().st_mtime + 3600
            os.utime(real_archive, (shifted, shifted))
            before = snapshot(connection, storage, checkpoint)
            try:
                run(stale)
            except RebuildRefused as exc:
                assert exc.phase == "STALE", exc
            else:
                raise AssertionError("a plan whose source changed must be stale")
            assert snapshot(connection, storage, checkpoint) == before
            os.utime(real_archive, (shifted - 3600, shifted - 3600))

            # 5. the rebuild of the pilot day, in order, with the crash windows
            plan = make_plan([PILOT])
            assert plan["days"][0]["action"] == "retire+import", plan["days"][0]["refusals"]
            with mock.patch.object(rebuild, "import_bybit_archive_day_after_retirement", side_effect=RuntimeError("crash after retire")):
                try:
                    run(plan)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("the crash was not raised")
            assert states(connection)[f"dt={PILOT}"] == ["invalid"] and f"dt={PILOT}/seg=000000.000" not in states(connection)
            assert gateway_ids(connection, PILOT) is None, "between the retire and the import the day is uncovered (a strict read refuses)"
            retired_plan = make_plan([PILOT])
            assert retired_plan["digest"] == plan["digest"], "the retire is progress, not a change of the plan"
            assert retired_plan["days"][0]["action"] == "import"
            with mock.patch.object(rebuild, "verify_rebuilt_day", side_effect=RuntimeError("crash after import")):
                try:
                    run(plan)  # the same plan, run again: the retired day is imported, then the crash
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("the second crash was not raised")
            assert states(connection)[f"dt={PILOT}/seg=000000.000"] == ["valid"] and states(connection)[f"dt={PILOT}"] == ["invalid"]
            assert make_plan([PILOT])["days"][0]["action"] == "verify" and make_plan([PILOT])["digest"] == plan["digest"]
            result = run(plan)  # the same plan again: the valid key is found and verified
            (day_result,) = result["days"]
            assert day_result["outcome"] == "verified" and day_result["verification"]["passed"], day_result

            # 6. the pilot day reads strictly as exactly the archive, in order, without duplicates, equal field by field
            archive_records = read_bybit_archive_day(archives / f"{INSTRUMENT}{PILOT}.csv.gz", instrument=INSTRUMENT, day=PILOT).records
            ids = gateway_ids(connection, PILOT)
            assert ids == [r.trade_id for r in archive_records] and len(ids) == len(set(ids)) == len(full[PILOT]), "the pilot day"
            new_file = dataset_root / f"dt={PILOT}" / "seg=000000.000" / "part-001.parquet"
            rebuilt = [r for batch in scan_trade_v1_all(new_file, batch_size=1000) for r in batch]
            assert len(rebuilt) == len(archive_records)
            for got, want in zip(rebuilt, archive_records):
                assert (got.venue, got.instrument, got.exchange_ts, got.aggressor_side, got.price, got.size, got.trade_id) == \
                       (want.venue, want.instrument, want.exchange_ts, want.aggressor_side, want.price, want.size, want.trade_id)
                assert got.receive_ts is None and got.sequence is None, "the rebuilt rows carry the archive's null receive_ts and sequence"
            assert verify_rebuilt_day(connection, day=PILOT, archive_dir=archives, storage_root_id=STORAGE_ROOT_ID)["passed"]
            # nothing else moved: the retired incumbent is intact, the other days and the checkpoint are byte-identical
            assert old_file.read_bytes() == old_bytes
            assert {key: v for key, v in states(connection).items() if not key.startswith(f"dt={PILOT}")} == other_days_before
            assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen_files_paths(dataset_root, PILOT)} == frozen_files
            assert checkpoint.read_bytes() == saved
            for day in ("2026-10-18", "2026-10-19"):
                assert gateway_ids(connection, day) is not None and len(gateway_ids(connection, day)) == len(full[day][::7])

            # 7. the ordinary importer still refuses the occupied day, a retired one included, and the variant refuses what is not retired
            try:
                import_bybit_archive_day(connection, instrument=INSTRUMENT, day=PILOT, archive_path=archives / f"{INSTRUMENT}{PILOT}.csv.gz",
                                         storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG"
            else:
                raise AssertionError("the ordinary importer must refuse an occupied day")
            for day in ("2026-10-18",):  # valid legacy day: the variant refuses
                try:
                    import_bybit_archive_day_after_retirement(connection, instrument=INSTRUMENT, day=day, archive_path=archives / f"{INSTRUMENT}{day}.csv.gz",
                                                              storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
                except ArchiveImportRefused as exc:
                    assert exc.phase == "CATALOG"
                else:
                    raise AssertionError("the variant must refuse a day whose legacy revision is valid")
            try:  # and a rebuilt day, again
                import_bybit_archive_day_after_retirement(connection, instrument=INSTRUMENT, day=PILOT, archive_path=archives / f"{INSTRUMENT}{PILOT}.csv.gz",
                                                          storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG"
            else:
                raise AssertionError("the variant must refuse a day that is already rebuilt")

            # 8. restore: refused over a rebuilt day; for a day retired and not rebuilt, a superseding pass returns it to valid
            try:
                restore_retired_day(connection, day=PILOT, storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except RebuildRefused as exc:
                assert exc.phase == "RESTORE"
            else:
                raise AssertionError("restoring over a rebuilt day must be refused")
            plan_18 = make_plan(["2026-10-18"])
            with mock.patch.object(rebuild, "import_bybit_archive_day_after_retirement", side_effect=ArchiveImportRefused("S13", "probe")):
                result = run(plan_18)
            assert result["days"][0]["outcome"] == "import_refused" and states(connection)["dt=2026-10-18"] == ["invalid"]
            restored = restore_retired_day(connection, day="2026-10-18", storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            assert restored.lifecycle.resulting_state == "valid" and states(connection)["dt=2026-10-18"] == ["valid"]
            assert len(gateway_ids(connection, "2026-10-18")) == len(full["2026-10-18"][::7])
            retry = run(make_plan(["2026-10-18"]))  # and the day can still be rebuilt afterwards
            assert retry["days"][0]["outcome"] == "rebuilt", retry
            assert gateway_ids(connection, "2026-10-18") == [r[1] for r in full["2026-10-18"]]
            # 9. conservation across days: the incumbent holds records of a neighbouring day (the 529 rows of the evidence)
            from quant_platform.data.models import TradeRecord
            from quant_platform.source_adapters.bybit import materialize_bybit_trade_v1

            def crafted(name: str, rows_of: list) -> Path:
                path = base / name
                records = sorted((TradeRecord("bybit", INSTRUMENT, Instant(ms * 1_000_000), price, size, side.lower(), None, tid, None)
                                  for ms, tid, side, price, size in rows_of), key=lambda r: (r.exchange_ts.epoch_ns, r.trade_id))
                materialize_bybit_trade_v1(path, tuple(records), dataset_identity=bybit_archive_dataset_identity(INSTRUMENT))
                return path

            far = "2026-10-30"
            write_archive(archives, far, world(far))
            mixed = full[PILOT][::7][:3] + full["2026-10-19"][:2]
            refusals, summary = rebuild._conservation(connection, crafted("mixed.parquet", mixed), PILOT, archives, [PILOT])
            assert refusals == [] and summary["days"] == {PILOT: 3, "2026-10-19": 2}, (refusals, summary)  # the neighbour is valid already
            refusals, _ = rebuild._conservation(connection, crafted("far.parquet", full[PILOT][:2] + world(far)[:1]), PILOT, archives, [PILOT])
            assert len(refusals) == 1 and "neither rebuilt in this plan nor already valid" in refusals[0], refusals
            refusals, _ = rebuild._conservation(connection, crafted("far.parquet", full[PILOT][:2] + world(far)[:1]), PILOT, archives, [PILOT, far])
            assert refusals == [], refusals  # rebuilt in the same plan
            (archives / f"{INSTRUMENT}{far}.csv.gz").rename(archives / "gone")
            refusals, _ = rebuild._conservation(connection, crafted("far.parquet", full[PILOT][:2] + world(far)[:1]), PILOT, archives, [PILOT, far])
            assert len(refusals) == 1 and "is missing" in refusals[0], refusals
            (archives / "gone").rename(archives / f"{INSTRUMENT}{far}.csv.gz")
            bad = world(far)[:1]
            bad = [(bad[0][0], bad[0][1], bad[0][2], "1.0", bad[0][4])]
            refusals, _ = rebuild._conservation(connection, crafted("bad.parquet", full[PILOT][:2] + bad), PILOT, archives, [PILOT, far])
            assert len(refusals) == 1 and "differ from the archive" in refusals[0], refusals
            nameless = crafted("ok.parquet", full[PILOT][:2])
            assert rebuild._conservation(connection, nameless, PILOT, archives, [PILOT])[0] == []
            # 10. a neighbouring day whose legacy coverage crossed midnight: until its own retirement it overlaps the rebuilt day, which
            #     the gateway refuses to read strictly; after its own rebuild both days read correctly
            neighbour = "2026-10-21"
            crossing_path = write_archive(staging, neighbour, full[neighbour][::7])
            crossing_start = datetime.fromisoformat(neighbour).replace(tzinfo=timezone.utc) - timedelta(minutes=1)
            dataset_identity = bybit_archive_dataset_identity(INSTRUMENT)
            importer._import_locked(
                connection, identity=dataset_identity, instrument=INSTRUMENT, day=neighbour, start=crossing_start,
                partition_key=f"dt={neighbour}", archive_path=crossing_path, storage_root_id=STORAGE_ROOT_ID, code_ref="legacy-commit",
                dataset_root=dataset_root)
            crossing_path.unlink()
            assert states(connection)[f"dt={neighbour}"] == ["valid"]
            verdict = verify_rebuilt_day(connection, day=PILOT, archive_dir=archives, storage_root_id=STORAGE_ROOT_ID)
            assert not verdict["passed"] and any("strict read" in problem for problem in verdict["problems"]), verdict
            result = run(make_plan([neighbour]))
            assert result["days"][0]["outcome"] == "rebuilt", result
            for day in (PILOT, neighbour):
                assert gateway_ids(connection, day) == [r[1] for r in full[day]], day
            # a gap on another day does not refuse this one; an invalid revision this operation did not retire is refused; the
            # incumbent's durable manifest must be on disk
            other_gap = Instant((day_ms("2026-10-19") + 3_600_000) * 1_000_000)
            record_explicit_long_gap(
                storage_root=storage, producer="gap-probe", code_ref="gap", detected_at=Instant(other_gap.epoch_ns + 5 * 10**9),
                interval=LongGapInterval(start_key=TradeKeyBoundary(venue="bybit", instrument=INSTRUMENT, exchange_ts=other_gap, trade_id="y"),
                                         detected_at=Instant(other_gap.epoch_ns + 5 * 10**9), reason="probe", recent_window_records=None,
                                         buffered_ws_records=None), instrument=INSTRUMENT)
            assert make_plan([PILOT])["days"][0]["refusals"] == [], "a gap on another day must not refuse this day"
            refused_plan(["2026-10-19"], "open gap record")
            for gap_file in dataset_root.glob("coverage-manifest-long-gap-*.json"):
                gap_file.unlink()
            sql(connection, "UPDATE catalog.partitions SET state='invalid' WHERE partition_key='dt=2026-10-19'")
            refused_plan(["2026-10-19"], "was not retired by this operation")
            sql(connection, "UPDATE catalog.partitions SET state='valid' WHERE partition_key='dt=2026-10-19'")
            manifest = dataset_root / "dt=2026-10-19" / "partition-manifest.json"
            manifest.rename(manifest.with_name("moved.json"))
            refused_plan(["2026-10-19"], "partition manifest")
            manifest.with_name("moved.json").rename(manifest)
            assert make_plan(["2026-10-19"])["days"][0]["action"] == "retire+import"
            # the coverage manifest is checked by the plan, not first by the retire: unreadable, or not the one the reports certified
            coverage_file = dataset_root / "dt=2026-10-19" / "coverage-manifest.json"
            coverage_raw = coverage_file.read_bytes()
            coverage_file.write_bytes(coverage_raw + b"\n")
            refused_plan(["2026-10-19"], "not the one its quality reports certified")
            coverage_file.write_bytes(b"{not json")
            refused_plan(["2026-10-19"], "do not load")
            coverage_file.write_bytes(coverage_raw)
            # two reports that certify different coverage sets: the plan cannot tell which one is the incumbent's
            sql(connection, "INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics) SELECT q.partition_id, q.check_suite, 'pass', "
                            "jsonb_build_object('certification_profile', q.metrics->>'certification_profile', "
                            "'coverage_manifest_sha256', jsonb_build_array(repeat('0', 64)), 'coverage_manifest_ids', jsonb_build_array('other')) "
                            "FROM catalog.quality_reports q JOIN catalog.partitions p ON p.partition_id = q.partition_id "
                            "WHERE p.partition_key='dt=2026-10-19' LIMIT 1")
            try:
                refused_plan(["2026-10-19"], "certify 2 coverage manifest sets")
            finally:
                sql(connection, "DELETE FROM catalog.quality_reports WHERE metrics->'coverage_manifest_ids' = '[\"other\"]'::jsonb")
            incumbent_19 = make_plan(["2026-10-19"])["days"][0]["incumbent"]
            assert incumbent_19["coverage_ids"] == ["bybit-archive-btcusdt-2026-10-19-v1"] and incumbent_19["assertion_ids"], incumbent_19

            # the verification checks the gateway's answer for count, order and duplicates, not only the Parquet
            class Shuffled:
                def __init__(self, records):
                    self.records = records

                def read(self, request):
                    return SimpleNamespace(records=self.records(request))

            def verify_with(transform):
                real = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
                with mock.patch.object(rebuild, "DataGateway", lambda *a, **k: Shuffled(lambda request: transform(list(real.read(request).records)))):
                    return verify_rebuilt_day(connection, day=PILOT, archive_dir=archives, storage_root_id=STORAGE_ROOT_ID)

            assert verify_with(lambda records: records)["passed"]
            for transform, message in ((lambda r: r[:-1], "returned"), (lambda r: r + r[:1], "more than once"),
                                       (lambda r: list(reversed(r)), "order"), (lambda r: r[1:] + r[:1], "order")):
                verdict = verify_with(transform)
                assert not verdict["passed"] and any(message in problem for problem in verdict["problems"]), (message, verdict)

            # more refusals on a day that is still legacy: a key to create that exists, a gap manifest that cannot be read, the variant's own
            # guard, the verification's own check of the Parquet, and a restore of a day that was never retired
            (legacy_id,) = sql(connection, "SELECT partition_id::text FROM catalog.partitions WHERE partition_key='dt=2026-10-19'")[0]
            copy_row = ("INSERT INTO catalog.partitions (dataset_id, partition_key, revision, storage_root_id, rel_path, ts_start, ts_end, "
                        "row_count, byte_size, content_sha256, state, manifest_sha256, created_at, closed_at, producer, code_ref) "
                        "SELECT dataset_id, %s, 1, storage_root_id, replace(rel_path, %s, %s), ts_start, ts_end, row_count, byte_size, "
                        "content_sha256, %s, manifest_sha256, created_at, closed_at, producer, code_ref FROM catalog.partitions "
                        "WHERE partition_id=%s")
            new_key = "dt=2026-10-19/seg=000000.000"
            for forced, reason in (("closed", "exists in the state"), ("valid", "two valid partitions")):
                sql(connection, copy_row, (new_key, "dt=2026-10-19/", f"{new_key}/", forced, legacy_id))
                try:
                    refused_plan(["2026-10-19"], reason)
                finally:
                    sql(connection, "DELETE FROM catalog.partitions WHERE partition_key=%s", (new_key,))
            sql(connection, "UPDATE catalog.partitions SET state='invalid' WHERE partition_id=%s", (legacy_id,))
            sql(connection, copy_row, ("dt=2026-10-19/seg=120000.000", "dt=2026-10-19/", "dt=2026-10-19/seg=120000.000/", "valid", legacy_id))
            try:
                import_bybit_archive_day_after_retirement(connection, instrument=INSTRUMENT, day="2026-10-19",
                                                          archive_path=archives / f"{INSTRUMENT}2026-10-19.csv.gz",
                                                          storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG" and "seg=120000.000" in str(exc), exc
            else:
                raise AssertionError("the variant must refuse a retired day that has another partition under it")
            finally:
                sql(connection, "DELETE FROM catalog.partitions WHERE partition_key='dt=2026-10-19/seg=120000.000'")
                sql(connection, "UPDATE catalog.partitions SET state='valid' WHERE partition_id=%s", (legacy_id,))
            try:
                restore_retired_day(connection, day="2026-10-19", storage_root_id=STORAGE_ROOT_ID, code_ref=CODE_REF)
            except RebuildRefused as exc:
                assert exc.phase == "RESTORE" and "must be invalid" in str(exc), exc
            else:
                raise AssertionError("a valid day was never retired: there is nothing to restore")
            bad_gap = dataset_root / "coverage-manifest-long-gap-unreadable.json"
            bad_gap.write_text(json.dumps({"coverage_id": "long-gap-unreadable", "assertions": [{
                "assertion_id": "a", "status": "known_gap", "start": "2026-10-19T00:00:00Z", "end": "2026-10-19T00:00:01Z",
                "evidence": [{"kind": "transport_interruption", "detail": "not json"}]}]}), encoding="utf-8")
            try:
                refused_plan(["2026-10-19"], "cannot be read")
            finally:
                bad_gap.unlink()
            real = rebuild.compare_archive_day_with_parquet

            def not_identical(*args, **kwargs):
                return {**real(*args, **kwargs), "identical": False}

            with mock.patch.object(rebuild, "compare_archive_day_with_parquet", not_identical):
                verdict = verify_rebuilt_day(connection, day=PILOT, archive_dir=archives, storage_root_id=STORAGE_ROOT_ID)
            assert not verdict["passed"] and any("not identical" in problem for problem in verdict["problems"]), verdict
            coverage_ids = {}
            for key_dir, label in ((f"dt={PILOT}/seg=000000.000", "rebuilt"), ("dt=2026-10-19", "legacy")):
                coverage_ids[label] = json.loads((dataset_root / key_dir / "coverage-manifest.json").read_text(encoding="utf-8"))["coverage_id"]
            assert coverage_ids == {"rebuilt": f"bybit-archive-btcusdt-{PILOT}-rebuild-v1", "legacy": "bybit-archive-btcusdt-2026-10-19-v1"}, coverage_ids

            # 11. the executor does one day at a time and stops at the first that fails: the next day is not attempted
            # a day published by the live publisher before the slices: ``part-<HHMMSS>`` and its manifests in the dataset root
            old_dir = dataset_root / "dt=2026-10-08"
            (old_dir / "part-001.parquet").rename(old_dir / "part-235942.parquet")
            (old_dir / "partition-manifest.json").rename(dataset_root / "partition-manifest-235942.json")
            (old_dir / "coverage-manifest.json").rename(dataset_root / "coverage-manifest-235942.json")
            sql(connection, "UPDATE catalog.partitions SET rel_path=%s WHERE partition_key='dt=2026-10-08'", ("dt=2026-10-08/part-235942.parquet",))
            two = make_plan(["2026-10-08", "2026-10-09"])
            assert two["days"][0]["incumbent"]["partition_manifest_file"] == "partition-manifest-235942.json", two["days"][0]["incumbent"]
            assert two["days"][0]["incumbent"]["coverage_manifest_file"] == "coverage-manifest-235942.json"
            assert two["days"][0]["action"] == "retire+import" and not two["days"][0]["refusals"], two["days"][0]
            # the manifest inside still names part-001, so the layout is only good for the plan: put the day back before the retire
            (old_dir / "part-235942.parquet").rename(old_dir / "part-001.parquet")
            (dataset_root / "partition-manifest-235942.json").rename(old_dir / "partition-manifest.json")
            (dataset_root / "coverage-manifest-235942.json").rename(old_dir / "coverage-manifest.json")
            sql(connection, "UPDATE catalog.partitions SET rel_path=%s WHERE partition_key='dt=2026-10-08'", ("dt=2026-10-08/part-001.parquet",))
            two = make_plan(["2026-10-08", "2026-10-09"])
            assert [d["action"] for d in two["days"]] == ["retire+import", "retire+import"], [d["refusals"] for d in two["days"]]
            with mock.patch.object(rebuild, "import_bybit_archive_day_after_retirement", side_effect=ArchiveImportRefused("S13", "probe")):
                stopped = run(two)
            assert [d["outcome"] for d in stopped["days"]] == ["import_refused"], stopped
            assert states(connection)["dt=2026-10-09"] == ["valid"], "the second day must not have been touched"
            assert states(connection)["dt=2026-10-08"] == ["invalid"]
            done = run(make_plan(["2026-10-08", "2026-10-09"]))  # and a new run, with the first day retired, completes both
            assert [d["outcome"] for d in done["days"]] == ["rebuilt", "rebuilt"], done
    print("POSTGRESQL LEGACY DAY REBUILD GATE PASSED")
    return 0


def frozen_files_paths(dataset_root: Path, pilot: str):
    return [p for p in dataset_root.rglob("*") if p.is_file() and f"dt={pilot}" not in p.parts[-3:] and not p.name.startswith(".")]


if __name__ == "__main__":
    raise SystemExit(main())
