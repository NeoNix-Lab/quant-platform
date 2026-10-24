#!/usr/bin/env python3
"""Real PostgreSQL proof of the archive historical import (DG-N T04, ADR-0074 Amendment 1).

One day of ``ETHUSDT`` and one of ``SOLUSDT`` are imported from local archive files through S13 and S14 and read back through the
DataGateway; a day already in the catalog, a refused source file, an existing target and a conflicting dataset manifest are each
refused without publishing anything. Requires ``DATA_GATEWAY_TEST_DSN`` and a POSIX path for the storage root.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application import bybit_archive_import  # noqa: E402
from quant_platform.application.bybit_archive_import import ArchiveImportRefused, import_bybit_archive_day  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY  # noqa: E402

HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"
STORAGE_ROOT_ID = "archive-hot"


def archive(directory: Path, instrument: str, day: str, start: int, rows: list[tuple[int, str, str, str, str, str]]) -> Path:
    path = directory / f"{instrument}{day}.csv.gz"
    lines = [HEADER] + [
        f"{start + offset}{fraction},{instrument},{side},{size},{price},PlusTick,{trade_id},1,{size},1"
        for offset, fraction, side, size, price, trade_id in rows
    ]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
    return path


def day_start(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def refused(phase: str, connection, **kwargs) -> None:
    try:
        import_bybit_archive_day(connection, storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit", **kwargs)
    except ArchiveImportRefused as exc:
        assert exc.phase == phase, (phase, exc.phase, str(exc))
        return
    raise AssertionError(f"expected a {phase} refusal for {kwargs}")


def partition_states(connection, instrument: str) -> dict[str, list[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.partition_key, p.state FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.instrument = %s AND d.layer = 'canonical' ORDER BY p.partition_key, p.revision", (instrument,),
        )
        rows = cursor.fetchall()
    connection.rollback()
    states: dict[str, list[str]] = {}
    for key, state in rows:
        states.setdefault(key, []).append(state)
    return states


def published_rows(connection, instrument: str, partition_key: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.state, p.content_sha256, p.manifest_sha256 FROM catalog.partitions p "
            "JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
            "WHERE d.instrument = %s AND d.layer = 'canonical' AND p.partition_key = %s ORDER BY p.revision",
            (instrument, partition_key),
        )
        rows = cursor.fetchall()
    connection.rollback()
    return [(state, str(content).strip(), str(manifest).strip()) for state, content, manifest in rows]


def assert_files_match_catalog(connection, dataset_root: Path, instrument: str, day: str) -> None:
    """What the catalog certified is what is on disk: the Parquet and the partition manifest hash to the catalog's values."""

    partition_key = f"dt={day}"
    rows = published_rows(connection, instrument, partition_key)
    assert len(rows) == 1 and rows[0][0] == "valid", rows
    parquet = (dataset_root / partition_key / "part-001.parquet").read_bytes()
    manifest = (dataset_root / partition_key / "partition-manifest.json").read_bytes()
    assert hashlib.sha256(parquet).hexdigest() == rows[0][1], "the Parquet on disk is not the one the catalog certified"
    assert hashlib.sha256(manifest).hexdigest() == rows[0][2], "the partition manifest on disk is not the one the catalog holds"


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL ARCHIVE IMPORT GATE NOT EXECUTED LOCALLY")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        base = Path(holder)
        storage = base / "storage"
        staging = base / "staging"
        other = base / "other"
        for directory in (storage, staging, other):
            directory.mkdir()
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
                cursor.execute("INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))
            connection.commit()

            # 1. ETHUSDT 2024-01-15: rows in descending file order, a millisecond tie, a sub-millisecond timestamp.
            start = 1705276800
            eth = archive(staging, "ETHUSDT", "2024-01-15", start, [
                (86399, ".9999", "Sell", "0.5", "2501.5", "t-4"),
                (60, ".1234", "Buy", "1.25", "2500.10", "t-3"),
                (60, ".1239", "Sell", "0.01", "2500.2", "t-2"),
                (0, ".5", "Buy", "2", "2499", "t-1"),
            ])
            report = import_bybit_archive_day(connection, instrument="ETHUSDT", day="2024-01-15", archive_path=eth,
                                              storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit")
            assert report.state == "valid" and report.row_count == 4, report
            identity = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
            request = DataRequest(identity, Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"),
                                  schema_requirement="trade-v1", lifecycle_policy=LifecyclePolicy.VALID_ONLY,
                                  coverage_policy="strict", ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY)
            result = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,)).read(request)
            observed = [(Instant.parse(r.exchange_ts), r.trade_id, r.price, r.size, r.aggressor_side) for r in result.records]
            expected = [
                ("2024-01-15T00:00:00.500000000Z", "t-1", "2499", "2", "buy"),
                ("2024-01-15T00:01:00.123000000Z", "t-2", "2500.2", "0.01", "sell"),
                ("2024-01-15T00:01:00.123000000Z", "t-3", "2500.10", "1.25", "buy"),
                ("2024-01-15T23:59:59.999000000Z", "t-4", "2501.5", "0.5", "sell"),
            ]
            assert observed == [(Instant.parse(a), *rest) for a, *rest in expected], observed
            assert result.metadata.coverage_complete and result.metadata.coverage_gaps == (), result.metadata
            dataset_root = storage / "canonical" / "trades" / "bybit" / "ETHUSDT" / "trade-v1"
            for name in ("dataset-manifest.json", "dt=2024-01-15/part-001.parquet", "dt=2024-01-15/partition-manifest.json",
                         "dt=2024-01-15/coverage-manifest.json"):
                assert (dataset_root / name).is_file(), name
            coverage = (dataset_root / "dt=2024-01-15" / "coverage-manifest.json").read_text(encoding="utf-8")
            assert "bybit-public-trade-archive-v1" in coverage and report.source_file_sha256 in coverage

            # 2. The same day again: already in the catalog, refused before any file is touched.
            refused("CATALOG", connection, instrument="ETHUSDT", day="2024-01-15", archive_path=eth)

            # 3. A refused source file publishes nothing and writes nothing.
            bad = archive(staging, "ETHUSDT", "2024-01-16", start + 86400, [(1, ".1", "buy", "1", "1", "x")])
            refused("SOURCE", connection, instrument="ETHUSDT", day="2024-01-16", archive_path=bad)
            assert not (dataset_root / "dt=2024-01-16").exists()
            assert "dt=2024-01-16" not in partition_states(connection, "ETHUSDT")

            # 4. An existing target file is never overwritten.
            good = archive(other, "ETHUSDT", "2024-01-16", start + 86400, [(1, ".1", "Buy", "1", "1", "y")])
            (dataset_root / "dt=2024-01-16").mkdir()
            (dataset_root / "dt=2024-01-16" / "part-001.parquet").write_bytes(b"old")
            refused("TARGET", connection, instrument="ETHUSDT", day="2024-01-16", archive_path=good)
            assert (dataset_root / "dt=2024-01-16" / "part-001.parquet").read_bytes() == b"old"

            # 5. A second instrument, then a dataset manifest that no longer matches the catalog: S13 refuses, nothing valid.
            sol = archive(staging, "SOLUSDT", "2024-01-15", start, [(5, ".25", "Buy", "3", "95.1", "s-1")])
            assert import_bybit_archive_day(connection, instrument="SOLUSDT", day="2024-01-15", archive_path=sol,
                                            storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit").state == "valid"
            sol_manifest = storage / "canonical" / "trades" / "bybit" / "SOLUSDT" / "trade-v1" / "dataset-manifest.json"
            document = json.loads(sol_manifest.read_text(encoding="utf-8"))
            document["created_at"] = "2020-01-01T00:00:00Z"  # a different dataset manifest than the one in the catalog
            sol_manifest.write_text(json.dumps(document), encoding="utf-8")
            sol_next = archive(staging, "SOLUSDT", "2024-01-16", start + 86400, [(5, ".25", "Sell", "3", "95.2", "s-2")])
            refused("S13", connection, instrument="SOLUSDT", day="2024-01-16", archive_path=sol_next)
            assert "valid" not in partition_states(connection, "SOLUSDT").get("dt=2024-01-16", [])
            assert partition_states(connection, "SOLUSDT")["dt=2024-01-15"] == ["valid"]

            # 6. Two imports of the same day at once: B starts while A is between its checks and its first write. Whoever is
            #    second is refused; the files that stay on disk are exactly the ones the catalog certified (they are never
            #    replaced by the loser before its refusal).
            race_day = "2024-01-18"
            race = archive(staging, "ETHUSDT", race_day, start + 3 * 86400, [(7, ".5", "Buy", "1", "2400", "r-1"), (9, ".5", "Sell", "2", "2401", "r-2")])
            real_read = bybit_archive_import.read_bybit_archive_day
            nested: dict[str, object] = {}

            def read_then_let_b_in(*args, **kwargs):
                if "outcome" not in nested:
                    nested["outcome"] = "started"
                    with psycopg.connect(dsn) as other:
                        try:
                            import_bybit_archive_day(other, instrument="ETHUSDT", day=race_day, archive_path=race,
                                                     storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit-b")
                            nested["outcome"] = "published"
                        except ArchiveImportRefused as exc:
                            nested["outcome"] = f"refused:{exc.phase}"
                return real_read(*args, **kwargs)

            with mock.patch.object(bybit_archive_import, "read_bybit_archive_day", read_then_let_b_in):
                try:
                    import_bybit_archive_day(connection, instrument="ETHUSDT", day=race_day, archive_path=race,
                                             storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit-a")
                    outer = "published"
                except ArchiveImportRefused as exc:
                    outer = f"refused:{exc.phase}"
            outcomes = sorted([str(nested["outcome"]), outer])
            assert outcomes == ["published", "refused:LOCK"], outcomes
            assert_files_match_catalog(connection, dataset_root, "ETHUSDT", race_day)
            assert not list(dataset_root.glob("**/.import-*.lock")), "a lock file was left behind"

            # 6b. The winner publishes and releases between the loser's first check and its lock: the loser looks again under the
            #     lock and is refused by the catalog, before any target file is considered.
            late_day = "2024-01-19"
            late = archive(staging, "ETHUSDT", late_day, day_start(late_day), [(3, ".5", "Buy", "1", "2300", "l-1")])
            real_lock = bybit_archive_import._day_lock
            ran: list[str] = []

            @bybit_archive_import.contextmanager
            def winner_first(path):
                if not ran:
                    ran.append("winner")
                    with psycopg.connect(dsn) as other:
                        import_bybit_archive_day(other, instrument="ETHUSDT", day=late_day, archive_path=late,
                                                 storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit-w")
                with real_lock(path):
                    yield

            with mock.patch.object(bybit_archive_import, "_day_lock", winner_first):
                refused("CATALOG", connection, instrument="ETHUSDT", day=late_day, archive_path=late)
            assert_files_match_catalog(connection, dataset_root, "ETHUSDT", late_day)

            # 7. Real threads: many days of a new instrument at once (first dataset manifest) and the same day from several
            #    threads. Every day ends valid exactly once, with files that match the catalog.
            xrp_days = [f"2024-02-{n:02d}" for n in range(1, 7)]
            xrp_files = {d: archive(staging, "XRPUSDT", d, day_start(d), [(1, ".1", "Buy", "10", "0.5", f"x-{d}-1"), (2, ".2", "Sell", "20", "0.51", f"x-{d}-2")])
                         for d in xrp_days}
            results: list[tuple[str, str]] = []
            details: list[str] = []
            gate = threading.Barrier(len(xrp_days) * 2)

            def work(day: str) -> None:
                with psycopg.connect(dsn) as own:
                    gate.wait()
                    try:
                        import_bybit_archive_day(own, instrument="XRPUSDT", day=day, archive_path=xrp_files[day],
                                                 storage_root_id=STORAGE_ROOT_ID, code_ref="integration-commit")
                        results.append((day, "published"))
                    except ArchiveImportRefused as exc:
                        results.append((day, f"refused:{exc.phase}"))
                        details.append(f"{day} {exc}")

            threads = [threading.Thread(target=work, args=(d,)) for d in xrp_days for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            xrp_root = storage / "canonical" / "trades" / "bybit" / "XRPUSDT" / "trade-v1"
            for day in xrp_days:
                outcomes = sorted(outcome for d, outcome in results if d == day)
                assert len(outcomes) == 2 and outcomes[0] == "published" and outcomes[1].startswith("refused:"), (day, outcomes, details)
                assert outcomes[1] in ("refused:LOCK", "refused:CATALOG"), (day, outcomes)
                assert_files_match_catalog(connection, xrp_root, "XRPUSDT", day)
            assert not list(xrp_root.glob("**/.import-*.lock")), "a lock file was left behind"

            # 8. A lock left by a crashed import is not taken over: the day is refused and nothing is written.
            stale = archive(staging, "XRPUSDT", "2024-02-20", day_start("2024-02-20"), [(1, ".1", "Buy", "1", "1", "s-1")])
            (xrp_root / ".import-dt=2024-02-20.lock").write_text("pid=1 host=crashed", encoding="utf-8")
            refused("LOCK", connection, instrument="XRPUSDT", day="2024-02-20", archive_path=stale)
            assert not (xrp_root / "dt=2024-02-20").exists()

            # 9. Outside the allow-list and unknown storage roots are refused as requests.
            refused("REQUEST", connection, instrument="ADAUSDT", day="2024-01-15", archive_path=eth)
            try:
                import_bybit_archive_day(connection, instrument="ETHUSDT", day="2024-01-17", archive_path=eth,
                                         storage_root_id="missing-root", code_ref="integration-commit")
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG", exc
            else:
                raise AssertionError("an unknown storage root was accepted")
    print("POSTGRESQL ARCHIVE IMPORT GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
