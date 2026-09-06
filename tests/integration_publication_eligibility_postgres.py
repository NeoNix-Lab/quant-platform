#!/usr/bin/env python3
"""Real PostgreSQL proof for S14 eligibility, lineage, retry, and rollback."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data import DatasetIdentity  # noqa: E402
from quant_platform.data.publication import (  # noqa: E402
    PublicationCertification,
    SealedPartitionEvidence,
)
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
    PublicationEligibilityRefusal,
)
from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data import Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1CertificationProfile, materialize_bybit_trade_v1,
)


class FailAfterEligibilityUpdate:
    def __init__(self, connection): self.connection = connection
    def cursor(self): return FailCursor(self.connection.cursor())
    def commit(self): return self.connection.commit()
    def rollback(self): return self.connection.rollback()


class FailCursor:
    def __init__(self, cursor): self.cursor = cursor
    def __enter__(self): self.cursor.__enter__(); return self
    def __exit__(self, *args): return self.cursor.__exit__(*args)
    def execute(self, statement, params=None):
        self.cursor.execute(statement, params)
        if "UPDATE catalog.partitions" in statement and "SET state" in statement:
            self.cursor.execute("SELECT 1 / 0")
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()


def quality_metrics(identity, partition, dataset_sha, partition_sha, coverage, coverage_sha, profile):
    assertion = coverage["assertions"][0]
    return {
        "certification_profile": profile,
        "natural_partition_identity": {
            "dataset_identity": identity.stable_dict(),
            "partition_key": partition["partition_key"],
            "revision": partition["revision"],
        },
        "dataset_manifest_sha256": dataset_sha,
        "partition_manifest_sha256": partition_sha,
        "coverage_manifest_id": coverage["coverage_id"],
        "coverage_assertion_id": assertion["assertion_id"],
        "coverage_manifest_ids": [coverage["coverage_id"]],
        "coverage_assertion_ids": [assertion["assertion_id"]],
        "coverage_manifest_sha256": [coverage_sha],
        "physical_artifact_hash": partition["sha256"],
        "canonical_content_hash_v1": "c" * 64,
        "evidence": {name: {"status": "pass"} for name in ("source", "canonical", "physical", "manifests", "coverage")},
    }


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL S14 PERSISTENCE GATE NOT EXECUTED LOCALLY")
        return 0
    identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
    parent = DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        dataset_path = root / "dataset.json"; partition_path = root / "partition.json"; coverage_path = root / "coverage.json"
        dataset = emit_dataset_manifest(dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z", derived_from=[parent], transform="canonicalize-trades-v1")
        artifact = root / "dt=2024-01-15" / "part-001.parquet"
        materialization = materialize_bybit_trade_v1(artifact, [
            TradeRecord("bybit", "BTCUSDT", Instant.parse("2024-01-15T00:00:01Z"), "100.00", "0.5000", "buy", None, "1", None),
        ], dataset_identity=identity)
        partition = emit_partition_manifest(partition_path, materialization, dataset_identity=identity, dataset_root=root, partition_key="dt=2024-01-15", revision=1, rel_path="dt=2024-01-15/part-001.parquet", created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z", producer="integration-producer", code_ref="integration-producer-commit").document
        emit_coverage_manifest(coverage_path, dataset_identity=identity, source_dataset_identity=identity, coverage_id="integration-coverage", supersedes=None, created_at="2026-09-01T10:00:02Z", acquisition={"basis": "source_extract", "intent_start": "2024-01-15T00:00:00Z", "intent_end": "2024-01-16T00:00:00Z", "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1"}, assertions=[{"assertion_id": "integration-assertion", "start": "2024-01-15T00:00:00Z", "end": "2024-01-16T00:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "integration"}]}], producer="integration-source", code_ref="integration-source-commit", partition_manifests=[partition])
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
                cursor.execute("INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES ('s14-hot',%s,'hot') ON CONFLICT DO NOTHING", (str(root),))
                cursor.execute("INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) VALUES ('raw','trades','bybit','BTCUSDT','raw/trades/bybit/BTCUSDT/trade-v1','trade-v1',%s) ON CONFLICT DO NOTHING", ("1" * 64,))
            connection.commit()
            writer = CatalogPublicationWriter(connection)
            from quant_platform.data.publication import _load_manifest
            ds, ds_sha = _load_manifest(dataset_path, "dataset"); part, part_sha = _load_manifest(partition_path, "partition")
            cov, cov_sha = _load_manifest(coverage_path, "coverage")
            # The real S13 runtime establishes the closed row and quality report.
            profile = BybitTradeV1CertificationProfile("integration-certifier")
            run = PublicationCertification(writer, profile).run(SealedPartitionEvidence(dataset_path, partition_path, (coverage_path,), artifact, "s14-hot"))
            sealed = run.sealed_partition
            evidence_a = PublicationEligibilityEvidence(dataset_path, partition_path, (coverage_path,), "s14-hot", profile.profile_id, profile.check_suite)
            original_uuid = sealed.partition_id
            original_interval = (sealed.ts_start, sealed.ts_end)
            original_hashes = (sealed.content_sha256, sealed.manifest_sha256)
            # Inject failure after UPDATE and prove a fresh connection sees the
            # original closed state: Phase 4 and lineage are one transaction.
            failing = PublicationEligibilityBridge(PublicationEligibilityCatalog(FailAfterEligibilityUpdate(connection)))
            try:
                failing.publish(evidence_a)
            except Exception:
                pass
            else:
                raise AssertionError("injected post-update failure was not raised")
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (sealed.partition_id,)); assert cursor.fetchone()[0] == "closed"
            # Real S13 same-revision restatement A -> B, followed by fresh S13
            # certification/evidence recording.  A remains in quality_reports.
            coverage_b_path = root / "coverage-b.json"
            emit_coverage_manifest(coverage_b_path, dataset_identity=identity, source_dataset_identity=identity, coverage_id="integration-coverage-b", supersedes="integration-coverage", created_at="2026-09-01T10:00:03Z", acquisition={"basis": "source_extract", "intent_start": "2024-01-15T00:00:00Z", "intent_end": "2024-01-16T00:00:00Z", "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1"}, assertions=[{"assertion_id": "integration-assertion-b", "start": "2024-01-15T00:00:00Z", "end": "2024-01-15T23:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "integration restatement"}]}], producer="integration-source", code_ref="integration-source-commit-b", partition_manifests=[partition])
            # The fold must see the superseded document A as well as B.  Put B
            # first so the frozen positional singular evidence IDs continue to
            # identify the current restatement, while plural evidence proves the
            # complete durable A -> B lineage.
            evidence_b_s13 = SealedPartitionEvidence(dataset_path, partition_path, (coverage_b_path, coverage_path), artifact, "s14-hot")
            run_b = PublicationCertification(writer, profile).run(evidence_b_s13)
            assert run_b.sealed_partition.partition_id == original_uuid
            assert run_b.sealed_partition.ts_start == Instant.parse("2024-01-15T00:00:00Z")
            assert run_b.sealed_partition.ts_end == Instant.parse("2024-01-15T23:00:00Z")
            evidence_b = PublicationEligibilityEvidence(dataset_path, partition_path, (coverage_b_path, coverage_path), "s14-hot", profile.profile_id, profile.check_suite)
            duplicate = run_b.quality_report
            assert duplicate is not None
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics, violations, code_ref, ran_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (sealed.partition_id, duplicate.check_suite, duplicate.status, Jsonb(duplicate.metrics), Jsonb(duplicate.violations), duplicate.code_ref, "2030-01-01T00:00:00Z"))
            connection.commit()
            result = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(evidence_b)
            assert result.state == "valid" and result.certification_status == "pass"
            assert result.partition_id == original_uuid == sealed.partition_id
            retry = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(evidence_b)
            assert retry == result
            with connection.cursor() as cursor:
                cursor.execute("SELECT partition_id::text, state, ts_start, ts_end, content_sha256, manifest_sha256 FROM catalog.partitions WHERE partition_id=%s", (result.partition_id,))
                row = cursor.fetchone()
                assert row[0] == original_uuid and row[1] == "valid"
                assert (Instant.parse(row[2]), Instant.parse(row[3])) == (run_b.sealed_partition.ts_start, run_b.sealed_partition.ts_end)
                assert (row[4].strip(), row[5].strip()) == original_hashes
                cursor.execute("SELECT count(*) FROM catalog.dataset_lineage WHERE child_id=%s", (result.dataset_id,)); assert cursor.fetchone()[0] == 1
                cursor.execute("SELECT count(*) FROM catalog.partitions WHERE dataset_id=%s AND partition_key=%s AND state <> 'superseded'", (result.dataset_id, "dt=2024-01-15")); assert cursor.fetchone()[0] == 1
                cursor.execute("SELECT count(*) FROM catalog.quality_reports WHERE partition_id=%s AND check_suite=%s", (result.partition_id, profile.check_suite)); assert cursor.fetchone()[0] == 3
                cursor.execute("SELECT report_id::text, metrics #>> '{coverage_manifest_id}' FROM catalog.quality_reports WHERE partition_id=%s ORDER BY ran_at, report_id::text", (result.partition_id,)); reports = cursor.fetchall(); assert len(reports) == 3 and {row[1] for row in reports} == {"integration-coverage", "integration-coverage-b"}
                cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='catalog' AND indexname='partitions_one_live'"); assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (result.partition_id,)); assert cursor.fetchone()[0] == "valid"

            # A separate current target with pass + fail evidence must refuse
            # without choosing by ran_at and must remain closed.
            conflict_artifact = root / "dt=2024-01-16" / "part-001.parquet"
            conflict_partition_path = root / "partition-conflict.json"
            conflict_coverage_path = root / "coverage-conflict.json"
            conflict_materialization = materialize_bybit_trade_v1(conflict_artifact, [TradeRecord("bybit", "BTCUSDT", Instant.parse("2024-01-16T00:00:01Z"), "100.00", "0.5000", "buy", None, "2", None)], dataset_identity=identity)
            conflict_partition = emit_partition_manifest(conflict_partition_path, conflict_materialization, dataset_identity=identity, dataset_root=root, partition_key="dt=2024-01-16", revision=1, rel_path="dt=2024-01-16/part-001.parquet", created_at="2026-09-01T10:01:00Z", closed_at="2026-09-01T10:01:01Z", producer="integration-producer", code_ref="integration-producer-commit").document
            emit_coverage_manifest(conflict_coverage_path, dataset_identity=identity, source_dataset_identity=identity, coverage_id="integration-coverage-conflict", supersedes=None, created_at="2026-09-01T10:01:02Z", acquisition={"basis": "source_extract", "intent_start": "2024-01-16T00:00:00Z", "intent_end": "2024-01-17T00:00:00Z", "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1"}, assertions=[{"assertion_id": "integration-assertion-conflict", "start": "2024-01-16T00:00:00Z", "end": "2024-01-17T00:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-16", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "integration conflict"}]}], producer="integration-source", code_ref="integration-source-commit", partition_manifests=[conflict_partition])
            conflict_evidence = SealedPartitionEvidence(dataset_path, conflict_partition_path, (conflict_coverage_path,), conflict_artifact, "s14-hot")
            conflict_run = PublicationCertification(writer, profile).run(conflict_evidence)
            conflict_quality = conflict_run.quality_report
            assert conflict_quality is not None
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics, violations, code_ref, ran_at) VALUES (%s,%s,'fail',%s,%s,%s,%s)", (conflict_run.sealed_partition.partition_id, profile.check_suite, Jsonb(conflict_quality.metrics), Jsonb([{"category": "source", "message": "controlled semantic conflict"}]), conflict_quality.code_ref, "2031-01-01T00:00:00Z"))
            connection.commit()
            try:
                PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(PublicationEligibilityEvidence(dataset_path, conflict_partition_path, (conflict_coverage_path,), "s14-hot", profile.profile_id, profile.check_suite))
            except PublicationEligibilityRefusal:
                pass
            else:
                raise AssertionError("conflicting current reports were accepted")
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (conflict_run.sealed_partition.partition_id,)); assert cursor.fetchone()[0] == "closed"

            # Missing parent proof: S13 can admit the child/closed revision,
            # but S14 must refuse before lineage or eligibility becomes durable.
            missing_identity = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "trade-v1")
            missing_parent = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "trade-v1")
            missing_dataset_path = root / "dataset-missing-parent.json"
            missing_partition_path = root / "partition-missing-parent.json"
            missing_coverage_path = root / "coverage-missing-parent.json"
            missing_dataset = emit_dataset_manifest(missing_dataset_path, dataset_identity=missing_identity, created_at="2026-09-01T10:02:00Z", derived_from=[missing_parent], transform="derive-l2-v1")
            missing_partition = {"schema_version": "partition-manifest-v1", **missing_identity.stable_dict(), "partition_key": "dt=2024-01-17", "revision": 1, "state": "closed", "rel_path": "dt=2024-01-17/part-001.parquet", "file_size_bytes": 0, "row_count": 0, "sha256": "0" * 64, "first_exchange_ts": None, "last_exchange_ts": None, "created_at": "2026-09-01T10:02:00Z", "closed_at": "2026-09-01T10:02:01Z", "producer": "integration-l2-producer", "code_ref": "integration-l2-producer-commit"}
            missing_partition_path.write_bytes(json.dumps(missing_partition, sort_keys=True, separators=(",", ":")).encode())
            emit_coverage_manifest(missing_coverage_path, dataset_identity=missing_identity, source_dataset_identity=missing_identity, coverage_id="integration-coverage-missing-parent", supersedes=None, created_at="2026-09-01T10:02:02Z", acquisition={"basis": "source_extract", "intent_start": "2024-01-17T00:00:00Z", "intent_end": "2024-01-18T00:00:00Z", "source_semantics": "generic-source-v1", "mapping": "generic-map-v1"}, assertions=[{"assertion_id": "integration-assertion-missing-parent", "start": "2024-01-17T00:00:00Z", "end": "2024-01-18T00:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-17", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "missing parent"}]}], producer="integration-source", code_ref="integration-source-commit", partition_manifests=[missing_partition])
            missing_ds, missing_ds_sha = _load_manifest(missing_dataset_path, "dataset")
            missing_part, missing_part_sha = _load_manifest(missing_partition_path, "partition")
            missing_cov, missing_cov_sha = _load_manifest(missing_coverage_path, "coverage")
            missing_sealed = writer.seal_partition(dataset={**missing_ds, "_manifest_sha256": missing_ds_sha}, partition={**missing_part, "_manifest_sha256": missing_part_sha}, coverage_start=Instant.parse("2024-01-17T00:00:00Z"), coverage_end=Instant.parse("2024-01-18T00:00:00Z"), storage_root_id="s14-hot")
            writer.commit()
            writer.record_quality_report(partition_id=missing_sealed.partition_id, check_suite="missing-parent-suite", status="pass", metrics=quality_metrics(missing_identity, missing_part, missing_ds_sha, missing_part_sha, missing_cov, missing_cov_sha, "missing-parent-profile"), violations=[], code_ref="missing-parent-certifier")
            writer.commit()
            missing_evidence = PublicationEligibilityEvidence(missing_dataset_path, missing_partition_path, (missing_coverage_path,), "s14-hot", "missing-parent-profile", "missing-parent-suite")
            try:
                PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(missing_evidence)
            except PublicationEligibilityRefusal:
                pass
            else:
                raise AssertionError("missing parent dataset was accepted")
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (missing_sealed.partition_id,)); assert cursor.fetchone()[0] == "closed"
                    cursor.execute("SELECT count(*) FROM catalog.dataset_lineage WHERE child_id=%s", (missing_sealed.dataset_id,)); assert cursor.fetchone()[0] == 0

            # Cross-phase precision proof: partition-manifest-v1 permits
            # nanosecond created_at/closed_at, catalog.partitions holds
            # timestamptz microseconds.  S13 seals and certifies the durable
            # manifest, so S14 must accept the microsecond row as the same
            # Phase-1 evidence instead of refusing what S13 legitimately
            # sealed.  Both values deliberately exceed the half-microsecond
            # rounding boundary: S13 must project before PostgreSQL receives
            # them.
            precision_created = "2026-09-01T10:03:00.123456789Z"
            precision_closed = "2026-09-01T10:03:01.987654789Z"
            precision_artifact = root / "dt=2024-01-18" / "part-001.parquet"
            precision_partition_path = root / "partition-precision.json"
            precision_coverage_path = root / "coverage-precision.json"
            precision_materialization = materialize_bybit_trade_v1(precision_artifact, [TradeRecord("bybit", "BTCUSDT", Instant.parse("2024-01-18T00:00:01Z"), "100.00", "0.5000", "buy", None, "3", None)], dataset_identity=identity)
            precision_partition = emit_partition_manifest(precision_partition_path, precision_materialization, dataset_identity=identity, dataset_root=root, partition_key="dt=2024-01-18", revision=1, rel_path="dt=2024-01-18/part-001.parquet", created_at=precision_created, closed_at=precision_closed, producer="integration-producer", code_ref="integration-producer-commit").document
            assert precision_partition["created_at"] == precision_created
            assert precision_partition["closed_at"] == precision_closed
            emit_coverage_manifest(precision_coverage_path, dataset_identity=identity, source_dataset_identity=identity, coverage_id="integration-coverage-precision", supersedes=None, created_at="2026-09-01T10:03:02Z", acquisition={"basis": "source_extract", "intent_start": "2024-01-18T00:00:00Z", "intent_end": "2024-01-19T00:00:00Z", "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1"}, assertions=[{"assertion_id": "integration-assertion-precision", "start": "2024-01-18T00:00:00Z", "end": "2024-01-19T00:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-18", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "integration precision"}]}], producer="integration-source", code_ref="integration-source-commit", partition_manifests=[precision_partition])
            precision_run = PublicationCertification(writer, profile).run(SealedPartitionEvidence(dataset_path, precision_partition_path, (precision_coverage_path,), precision_artifact, "s14-hot"))
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT created_at, closed_at FROM catalog.partitions WHERE partition_id=%s", (precision_run.sealed_partition.partition_id,))
                    first_created, first_closed = cursor.fetchone()
                    assert first_created == Instant.parse(precision_created).to_datetime()
                    assert first_closed == Instant.parse(precision_closed).to_datetime()
            precision_retry = PublicationCertification(writer, profile).run(SealedPartitionEvidence(dataset_path, precision_partition_path, (precision_coverage_path,), precision_artifact, "s14-hot"))
            assert precision_retry.sealed_partition.partition_id == precision_run.sealed_partition.partition_id
            precision_result = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(PublicationEligibilityEvidence(dataset_path, precision_partition_path, (precision_coverage_path,), "s14-hot", profile.profile_id, profile.check_suite))
            assert precision_result.state == "valid"
            assert precision_result.partition_id == precision_run.sealed_partition.partition_id
            durable_precision = json.loads(precision_partition_path.read_text())
            assert durable_precision["created_at"] == precision_created
            assert durable_precision["closed_at"] == precision_closed
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT state, created_at, closed_at FROM catalog.partitions WHERE partition_id=%s", (precision_run.sealed_partition.partition_id,))
                    precision_state, persisted_created, persisted_closed = cursor.fetchone()
                    assert precision_state == "valid"
                    # PostgreSQL persisted microseconds, the manifest declared nanoseconds.
                    assert (persisted_created.microsecond, persisted_closed.microsecond) == (123456, 987654)
                    assert persisted_created == Instant.parse(precision_created).to_datetime()
                    assert persisted_closed == Instant.parse(precision_closed).to_datetime()
    print("S14 PostgreSQL eligibility integration PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
