#!/usr/bin/env python3
"""Real PostgreSQL proof of the superseding reassessment (DG-R R01, ADR-0074 Amendment 2 A2.3).

A partial ``ETHUSDT`` day is imported through the ordinary S13/S14 path (a ``valid`` revision with a ``pass`` assessment, the shape of a
damaged legacy live day). The reassessment writer retires it (``valid -> invalid``) with a ``fail`` that supersedes the exact
signature of the previous leaf and copies its bound evidence unchanged, then restores it with a superseding ``pass``. Reads exclude
the retired revision; the bytes, the manifests and every earlier report are untouched; the call is idempotent; each refusal writes
nothing; a concurrent S13 publication on the same dataset waits for the writer's short transaction; a crash between the report and
the lifecycle application converges when the call is repeated. Requires ``DATA_GATEWAY_TEST_DSN`` and a POSIX storage root.
"""

from __future__ import annotations

from dataclasses import replace
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
import quant_platform.data.quality_reassessment as reassessment  # noqa: E402
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application.bybit_archive_import import import_bybit_archive_day  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.models import NoCoverage  # noqa: E402
from quant_platform.data.quality_lifecycle import (  # noqa: E402
    QualityLifecycleCatalog,
    QualityLifecycleRefusal,
    semantic_assessment_signature,
)
from quant_platform.data.quality_reassessment import (  # noqa: E402
    REASON_LEGACY_INCOMPLETE_DAY,
    load_lifecycle_inputs,
    reassess_live_partition,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_CERTIFICATION_PROFILE,
    BYBIT_TRADE_V1_CHECK_SUITE,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)

HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"
STORAGE_ROOT_ID = "reassess-hot"
INSTRUMENT = "ETHUSDT"
DAY = "2024-01-15"
START = Instant.parse("2024-01-15T00:00:00Z")
END = Instant.parse("2024-01-16T00:00:00Z")
ARCHIVE = {"url": "https://public.bybit.com/trading/ETHUSDT/ETHUSDT2024-01-15.csv.gz", "sha256": "a" * 64, "rows": 1568861}


def archive(directory: Path, day: str, rows: list[tuple[int, str, str]]) -> Path:
    base = int(Instant.parse(f"{day}T00:00:00Z").epoch_ns // 1_000_000_000)
    path = directory / f"{INSTRUMENT}{day}.csv.gz"
    lines = [HEADER] + [f"{base + offset}.5,{INSTRUMENT},Buy,1,2500,PlusTick,{trade_id},1,1,1" for offset, trade_id, _ in rows]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
    return path


def sql(connection, query, params=()):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall() if cursor.description else None
    connection.commit()
    return rows


def reports(connection, key=f"dt={DAY}"):
    return sql(connection, "SELECT r.status, r.metrics, r.violations, r.code_ref, r.report_id::text FROM catalog.quality_reports r "
                           "JOIN catalog.partitions p ON p.partition_id = r.partition_id JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
                           "WHERE d.instrument = %s AND p.partition_key = %s ORDER BY r.ran_at, r.report_id", (INSTRUMENT, key))


def state(connection, key=f"dt={DAY}"):
    return sql(connection, "SELECT p.state, p.revision FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
                           "WHERE d.instrument = %s AND p.partition_key = %s ORDER BY p.revision", (INSTRUMENT, key))


def signature(row) -> str:
    return semantic_assessment_signature(BYBIT_TRADE_V1_CHECK_SUITE, row[0], row[1], row[2], row[3])


def read(connection, policy, coverage="allow-partial"):
    identity = DatasetIdentity("canonical", "trades", "bybit", INSTRUMENT, "trade-v1")
    request = DataRequest(identity, START, END, schema_requirement="trade-v1", lifecycle_policy=policy, coverage_policy=coverage,
                          ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY)
    try:
        return [r.trade_id for r in DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,)).read(request).records]
    except NoCoverage:
        return None


def call(connection, inputs, status, **kwargs):
    arguments = dict(inputs=inputs, coverage_start=START, coverage_end=END, storage_root_id=STORAGE_ROOT_ID,
                     expected_profile=BYBIT_TRADE_V1_CERTIFICATION_PROFILE, expected_check_suite=BYBIT_TRADE_V1_CHECK_SUITE,
                     status=status, code_ref="r01-commit", archive_identity=ARCHIVE if status == "fail" else None)
    arguments.update(kwargs)
    return reassess_live_partition(connection, **arguments)


def refused(connection, inputs, status, message, **kwargs):
    before = (reports(connection), state(connection))
    try:
        call(connection, inputs, status, **kwargs)
    except QualityLifecycleRefusal as exc:
        assert message in str(exc), (message, str(exc))
    else:
        raise AssertionError(f"expected a refusal containing {message!r}")
    assert (reports(connection), state(connection)) == before, "a refused reassessment must write nothing"


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL QUALITY REASSESSMENT GATE NOT EXECUTED LOCALLY")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        base = Path(holder)
        storage, staging = base / "storage", base / "staging"
        storage.mkdir()
        staging.mkdir()
        dataset_root = storage / "canonical" / "trades" / "bybit" / INSTRUMENT / "trade-v1"
        with psycopg.connect(dsn) as connection:
            sql(connection, "INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
            sql(connection, "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))

            # the damaged legacy day: a partial day, valid, with one pass assessment
            partial = archive(staging, DAY, [(10, "a", ""), (20, "b", ""), (30, "c", ""), (40, "d", "")])
            imported = import_bybit_archive_day(connection, instrument=INSTRUMENT, day=DAY, archive_path=partial,
                                                storage_root_id=STORAGE_ROOT_ID, code_ref="import-commit")
            assert imported.state == "valid", imported
            key = f"dt={DAY}"
            inputs = load_lifecycle_inputs(dataset_root / "dataset-manifest.json", dataset_root / key / "partition-manifest.json",
                                           [dataset_root / key / "coverage-manifest.json"])
            parquet = dataset_root / key / "part-001.parquet"
            manifest = dataset_root / key / "partition-manifest.json"
            frozen = (hashlib.sha256(parquet.read_bytes()).hexdigest(), hashlib.sha256(manifest.read_bytes()).hexdigest(),
                      sql(connection, "SELECT p.content_sha256, p.manifest_sha256, p.created_at, p.closed_at FROM catalog.partitions p "
                                      "JOIN catalog.datasets d ON d.dataset_id=p.dataset_id WHERE d.instrument=%s", (INSTRUMENT,)))
            first = reports(connection)
            assert len(first) == 1 and first[0][0] == "pass" and state(connection) == [("valid", 1)]
            original = signature(first[0])
            assert read(connection, LifecyclePolicy.VALID_ONLY) == ["a", "b", "c", "d"]

            # 1. retire: valid -> invalid, a fail that supersedes the exact signature of the previous leaf
            retired = call(connection, inputs, "fail")
            assert retired.inserted and retired.superseded_signature == original, retired
            assert (retired.lifecycle.prior_state, retired.lifecycle.resulting_state, retired.lifecycle.mutated) == ("valid", "invalid", True)
            assert state(connection) == [("invalid", 1)]
            after = reports(connection)
            assert len(after) == 2 and after[0] == first[0], "the earlier report is untouched"
            fail = after[1]
            assert fail[0] == "fail" and fail[3] == "r01-commit" and fail[4] == retired.report_id
            assert fail[1]["supersedes_assessment_signature"] == original
            # the bound evidence is the previous report's, unchanged
            for name in ("certification_profile", "natural_partition_identity", "dataset_manifest_sha256", "partition_manifest_sha256",
                         "physical_artifact_hash", "canonical_content_hash_v1", "coverage_manifest_id", "coverage_assertion_id",
                         "coverage_manifest_ids", "coverage_assertion_ids", "coverage_manifest_sha256"):
                assert fail[1][name] == first[0][1][name], name
            for category in ("source", "canonical", "physical", "manifests", "publication"):
                assert fail[1]["evidence"][category] == first[0][1]["evidence"][category], category
            assert fail[1]["evidence"]["coverage"]["status"] == "fail"
            assert first[0][1]["evidence"]["coverage"]["status"] == "pass"
            (violation,) = fail[2]
            assert violation["message"] == REASON_LEGACY_INCOMPLETE_DAY and violation["archive"] == ARCHIVE, violation

            # 2. the retired revision is excluded by every lifecycle policy; the bytes and manifests are untouched
            for policy in LifecyclePolicy:
                assert not read(connection, policy), policy
            assert read(connection, LifecyclePolicy.VALID_ONLY, "strict") is None
            now = (hashlib.sha256(parquet.read_bytes()).hexdigest(), hashlib.sha256(manifest.read_bytes()).hexdigest(),
                   sql(connection, "SELECT p.content_sha256, p.manifest_sha256, p.created_at, p.closed_at FROM catalog.partitions p "
                                   "JOIN catalog.datasets d ON d.dataset_id=p.dataset_id WHERE d.instrument=%s", (INSTRUMENT,)))
            assert now == frozen, "retiring a revision must not change its bytes, its manifests or its catalog row"

            # 3. idempotent: the same call adds no leaf and changes no state
            again = call(connection, inputs, "fail")
            assert not again.inserted and again.report_id is None and not again.lifecycle.mutated, again
            assert reports(connection) == after and state(connection) == [("invalid", 1)]

            # 4. restore: a superseding pass returns the revision to valid, through the same seam
            restored = call(connection, inputs, "pass", code_ref="r01-restore")
            assert restored.inserted and restored.superseded_signature == signature(fail), restored
            assert (restored.lifecycle.prior_state, restored.lifecycle.resulting_state) == ("invalid", "valid")
            third = reports(connection)
            assert len(third) == 3 and third[:2] == after and third[2][1]["supersedes_assessment_signature"] == signature(fail)
            assert third[2][1]["evidence"]["coverage"]["status"] == "pass" and not third[2][2]
            assert read(connection, LifecyclePolicy.VALID_ONLY) == ["a", "b", "c", "d"]
            assert not call(connection, inputs, "pass", code_ref="r01-restore").inserted
            assert len(reports(connection)) == 3
            retired_again = call(connection, inputs, "fail")  # a later retire supersedes the restore, not the first fail
            assert retired_again.inserted and retired_again.superseded_signature == signature(third[2])
            assert state(connection) == [("invalid", 1)]
            call(connection, inputs, "pass", code_ref="r01-restore-2")
            assert state(connection) == [("valid", 1)]

            # 5. refusals write nothing
            refused(connection, inputs, "warn", "a pass or a fail")
            refused(connection, inputs, "fail", "producing commit", code_ref="  ")
            refused(connection, inputs, "fail", "archive identity", archive_identity=None)
            for field in ("partition_sha256", "dataset_sha256"):
                refused(connection, replace(inputs, **{field: "0" * 64}), "fail", "")
            refused(connection, inputs, "fail", "does not equal", coverage_end=Instant.parse("2024-01-15T12:00:00Z"))
            refused(connection, inputs, "fail", "storage identity", storage_root_id="elsewhere")
            wrong_revision = replace(inputs, partition={**inputs.partition, "revision": 3})
            refused(connection, wrong_revision, "fail", "target revision is missing")
            for forced, message in (("writing", "not quality-eligible"), ("closed", "ordinary S13")):
                sql(connection, "UPDATE catalog.partitions SET state=%s WHERE partition_key=%s", (forced, key))
                try:
                    refused(connection, inputs, "fail", message)
                finally:
                    sql(connection, "UPDATE catalog.partitions SET state='valid' WHERE partition_key=%s", (key,))
            # a superseded revision, and a key whose live revision is another
            sql(connection, "UPDATE catalog.partitions SET state='superseded' WHERE partition_key=%s", (key,))
            sql(connection, "INSERT INTO catalog.partitions (dataset_id, partition_key, revision, storage_root_id, rel_path, ts_start, ts_end, "
                            "row_count, byte_size, content_sha256, state, manifest_sha256, created_at, closed_at, producer, code_ref) "
                            "SELECT dataset_id, partition_key, 2, storage_root_id, rel_path, ts_start, ts_end, row_count, byte_size, "
                            "content_sha256, 'valid', manifest_sha256, created_at, closed_at, producer, code_ref FROM catalog.partitions "
                            "WHERE partition_key=%s AND revision=1", (key,))
            try:
                refused(connection, inputs, "fail", "superseded")
            finally:
                sql(connection, "DELETE FROM catalog.partitions WHERE partition_key=%s AND revision=2", (key,))
                sql(connection, "UPDATE catalog.partitions SET state='valid' WHERE partition_key=%s", (key,))
            assert state(connection) == [("valid", 1)]
            # a foreign dataset (the dataset manifest of another instrument names no catalog dataset)
            foreign = replace(inputs, dataset={**inputs.dataset, "instrument": "SOLUSDT"})
            refused(connection, foreign, "fail", "dataset natural identity")

            # 6. the writer holds no lock beyond its transaction, and a concurrent S13 publication on the dataset waits for it
            gate, entered, released = threading.Event(), threading.Event(), threading.Event()
            real_select = reassessment.select_current_quality_assessment

            def gated(*args, **kwargs):
                entered.set()
                assert gate.wait(30), "the test did not release the writer"
                return real_select(*args, **kwargs)

            outcome: dict = {}

            def writer():
                with psycopg.connect(dsn) as other:
                    outcome["writer"] = call(other, inputs, "fail")

            def publisher():
                with psycopg.connect(dsn) as other:
                    other_day = archive(staging, "2024-01-16", [(5, "e", "")])
                    outcome["published"] = import_bybit_archive_day(
                        other, instrument=INSTRUMENT, day="2024-01-16", archive_path=other_day, storage_root_id=STORAGE_ROOT_ID,
                        code_ref="import-commit-2")
                released.set()

            with mock.patch.object(reassessment, "select_current_quality_assessment", gated):
                first_thread = threading.Thread(target=writer, daemon=True)
                first_thread.start()
                assert entered.wait(30), "the writer did not reach its report"
                second_thread = threading.Thread(target=publisher, daemon=True)
                second_thread.start()
                time.sleep(2.0)
                assert not released.is_set(), "a publication on the same dataset must wait for the writer's transaction"
                gate.set()
                first_thread.join(60)
                second_thread.join(60)
            assert outcome["writer"].inserted and outcome["published"].state == "valid", outcome
            assert state(connection) == [("invalid", 1)] and state(connection, "dt=2024-01-16") == [("valid", 1)]
            with psycopg.connect(dsn) as probe, probe.cursor() as cursor:  # no lock is held by the writer any more
                cursor.execute("SELECT 1 FROM catalog.datasets WHERE instrument = %s FOR UPDATE NOWAIT", (INSTRUMENT,))
                cursor.execute("SELECT 1 FROM catalog.partitions WHERE partition_key = %s FOR UPDATE NOWAIT", (key,))

            # 7. a crash between the report and the lifecycle application leaves a current fail with the state still valid; the
            #    same call again converges without a second report
            call(connection, inputs, "pass", code_ref="r01-restore-3")
            assert state(connection) == [("valid", 1)]
            count = len(reports(connection))
            with mock.patch.object(QualityLifecycleCatalog, "apply_partition_lifecycle", side_effect=RuntimeError("crash")):
                try:
                    call(connection, inputs, "fail", code_ref="r01-commit-2")
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("the crash was not raised")
            with psycopg.connect(dsn) as other:  # the report was committed on its own, before the seam
                assert len(reports(other)) == count + 1 and state(other) == [("valid", 1)]
            healed = call(connection, inputs, "fail", code_ref="r01-commit-2")
            assert not healed.inserted and healed.lifecycle.mutated and state(connection) == [("invalid", 1)]
            assert len(reports(connection)) == count + 1

            # a fail that names another archive identity, or comes from another commit, is a different assessment: it supersedes the
            # current fail instead of being taken for a repeat (the state stays invalid)
            count = len(reports(connection))
            other_archive = call(connection, inputs, "fail", code_ref="r01-commit-2", archive_identity={**ARCHIVE, "sha256": "b" * 64})
            assert other_archive.inserted and len(reports(connection)) == count + 1 and state(connection) == [("invalid", 1)]
            other_commit = call(connection, inputs, "fail", code_ref="r01-commit-3", archive_identity={**ARCHIVE, "sha256": "b" * 64})
            assert other_commit.inserted and len(reports(connection)) == count + 2 and state(connection) == [("invalid", 1)]
            assert not call(connection, inputs, "fail", code_ref="r01-commit-3", archive_identity={**ARCHIVE, "sha256": "b" * 64}).inserted

            # a leaf that is a warn (a degraded source) cannot be turned into a pass by this writer: the copied evidence would not allow
            # it; a fail over it is fine
            warn_day = archive(staging, "2024-01-17", [(7, "w", "")])
            import_bybit_archive_day(connection, instrument=INSTRUMENT, day="2024-01-17", archive_path=warn_day,
                                     storage_root_id=STORAGE_ROOT_ID, code_ref="import-commit-3")
            warn_key = "dt=2024-01-17"
            warn_inputs = load_lifecycle_inputs(dataset_root / "dataset-manifest.json", dataset_root / warn_key / "partition-manifest.json",
                                                [dataset_root / warn_key / "coverage-manifest.json"])
            (row,) = sql(connection, "SELECT r.report_id::text, r.metrics FROM catalog.quality_reports r JOIN catalog.partitions p ON "
                                     "p.partition_id = r.partition_id WHERE p.partition_key = %s", (warn_key,))
            warn_metrics = row[1]
            warn_metrics["evidence"]["source"]["status"] = "warn"
            sql(connection, "UPDATE catalog.quality_reports SET status='warn', metrics=%s::jsonb, violations=%s::jsonb WHERE report_id=%s",
                (json.dumps(warn_metrics), '[{"category": "source", "message": "warn"}]', row[0]))
            warn_args = dict(coverage_start=Instant.parse("2024-01-17T00:00:00Z"), coverage_end=Instant.parse("2024-01-18T00:00:00Z"))
            before = reports(connection, warn_key)
            try:
                call(connection, warn_inputs, "pass", **warn_args)
            except QualityLifecycleRefusal as exc:
                assert "evidence does not allow it" in str(exc), str(exc)
            else:
                raise AssertionError("a pass over a warn leaf must be refused")
            assert reports(connection, warn_key) == before
            over_warn = call(connection, warn_inputs, "fail", **warn_args)
            assert over_warn.inserted and state(connection, warn_key) == [("invalid", 1)], over_warn

            # 8. the ordinary path still refuses to record a report for a partition that is not closed
            from quant_platform.data.publication_catalog import CatalogPublicationConflict, CatalogPublicationWriter

            partition_id = sql(connection, "SELECT p.partition_id::text FROM catalog.partitions p WHERE p.partition_key = %s", (key,))[0][0]
            try:
                CatalogPublicationWriter(connection).record_quality_report(
                    partition_id=partition_id, check_suite=BYBIT_TRADE_V1_CHECK_SUITE, status="fail", metrics={}, violations=[],
                    code_ref="x")
            except CatalogPublicationConflict as exc:
                assert "closed partition" in str(exc)
            else:
                raise AssertionError("record_quality_report must keep refusing a partition that is not closed")
            connection.rollback()
    print("POSTGRESQL QUALITY REASSESSMENT GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
