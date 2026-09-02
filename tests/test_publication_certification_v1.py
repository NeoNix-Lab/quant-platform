#!/usr/bin/env python3
"""Behavioral and architecture tests for S13 publication certification v1."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data import (  # noqa: E402
    CatalogSealer,
    Certifier,
    CertificationEvidenceRecorder,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    PublicationCertification,
    SealedCatalogPartition,
    SealedPartitionEvidence,
    TradeRecord,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
    materialize_trade_v1,
    CatalogPublicationWriter,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_TRADE_V1_CHECK_SUITE,
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)
from quant_platform.ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"


def trade(timestamp: str, trade_id: str = "1", *, receive_ts=None, sequence=None) -> TradeRecord:
    return TradeRecord(
        IDENTITY.venue, IDENTITY.instrument, Instant.parse(timestamp),
        "100.00", "0.5000", "buy", receive_ts, trade_id, sequence,
    )


class FakeCatalogWriter:
    def __init__(self):
        self.partition: SealedCatalogPartition | None = None
        self.reports = []
        self.commits = 0
        self.rollbacks = 0
        self.lifecycle_updates = []

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id):
        identity = DatasetIdentity(
            dataset["layer"], dataset["dataset_kind"], dataset["venue"],
            dataset["instrument"], dataset["record_schema_id"],
        )
        if self.partition is None:
            self.partition = SealedCatalogPartition(
                "partition-uuid-1", "dataset-uuid-1",
                NaturalPartitionIdentity(identity, partition["partition_key"], partition["revision"]),
                "closed", coverage_start, coverage_end,
                partition["row_count"], partition["file_size_bytes"],
                partition["sha256"], partition["_manifest_sha256"],
                partition["producer"], partition["code_ref"],
            )
        return self.partition

    def record_quality_report(self, *, partition_id, check_suite, status, metrics, violations, code_ref):
        report = {
            "partition_id": partition_id, "check_suite": check_suite,
            "status": status, "metrics": dict(metrics),
            "violations": list(violations), "code_ref": code_ref,
        }
        self.reports.append(report)
        return type("Report", (), {**report, "report_id": f"report-{len(self.reports)}"})()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class PublicationCertificationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.data_path = self.root / "dt=2024-01-15" / "part-000.parquet"
        self.dataset_path = self.root / "dataset.json"
        self.partition_path = self.root / "partition.json"
        self.coverage_path = self.root / "coverage.json"
        self.last_materialization = None

    def tearDown(self):
        self.tempdir.cleanup()

    def evidence(self, records=None, *, coverage_start=START, coverage_end=END, source_detail="sqlite extract complete"):
        records = records if records is not None else [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")]
        if any(item.trade_id is None for item in records):
            provider = OrderingProvider(
                "test-ordering", frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
                lambda item: (item.exchange_ts, item.trade_id or ""),
                lambda candidate: candidate == IDENTITY,
            )
            materialization = materialize_trade_v1(
                self.data_path, records, dataset_identity=IDENTITY,
                ordering_provider=provider,
            )
        else:
            materialization = materialize_bybit_trade_v1(self.data_path, records, dataset_identity=IDENTITY)
        self.last_materialization = materialization
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=IDENTITY,
            created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=IDENTITY,
            dataset_root=self.root, partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="test-materializer", code_ref="producer-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
            coverage_id="coverage-1", supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={
                "basis": "source_extract", "intent_start": START, "intent_end": END,
                "source_semantics": "bybit-public-trades-sqlite-v1",
                "mapping": "bybit-sqlite-day-extract-v1",
            },
            assertions=[{
                "assertion_id": "assertion-1", "start": coverage_start,
                "end": coverage_end, "status": "complete",
                "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": source_detail}],
            }], producer="test-source", code_ref="source-ref",
            partition_manifests=[json.loads(self.partition_path.read_text())],
        )
        return SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,),
            self.data_path, "hot",
        )

    def rewrite_json(self, path, mutate):
        document = json.loads(path.read_text())
        mutate(document)
        path.write_text(json.dumps(document))

    def rewrite_partition_physical_evidence(self):
        document = json.loads(self.partition_path.read_text())
        raw = self.data_path.read_bytes()
        document["sha256"] = hashlib.sha256(raw).hexdigest()
        document["file_size_bytes"] = len(raw)
        self.partition_path.write_text(json.dumps(document))

    def rewrite_artifact_rows(self, indices):
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(str(self.data_path))
        pq.write_table(
            table.take(pa.array(indices)), str(self.data_path),
            compression="zstd", version="2.6", coerce_timestamps=None,
            allow_truncated_timestamps=False, use_deprecated_int96_timestamps=False,
            use_dictionary=True, write_statistics=True,
        )
        self.rewrite_partition_physical_evidence()

    def runtime(self, writer=None):
        return PublicationCertification(
            writer or FakeCatalogWriter(),
            BybitTradeV1CertificationProfile("certifier-ref"),
            batch_size=1,
        )

    def test_non_empty_pass_records_evidence_and_stays_closed(self):
        writer = FakeCatalogWriter()
        run = self.runtime(writer).run(self.evidence())
        self.assertEqual(run.certification.status, "pass")
        self.assertEqual(run.sealed_partition.state, "closed")
        self.assertEqual(run.quality_report.status, "pass")
        self.assertEqual(writer.reports[0]["check_suite"], BYBIT_TRADE_V1_CHECK_SUITE)
        self.assertEqual(writer.reports[0]["code_ref"], "certifier-ref")
        metrics = writer.reports[0]["metrics"]
        for required in (
            "certification_profile", "natural_partition_identity", "dataset_manifest_sha256",
            "partition_manifest_sha256", "coverage_manifest_id", "coverage_assertion_id",
            "physical_artifact_hash", "canonical_content_hash_v1", "evidence",
        ):
            self.assertIn(required, metrics)
        self.assertEqual(run.sealed_partition.code_ref, "producer-ref")
        self.assertNotEqual(run.sealed_partition.code_ref, run.certification.code_ref)
        self.assertNotIn("valid", writer.lifecycle_updates)
        self.assertNotIn("degraded", writer.lifecycle_updates)
        self.assertEqual([item.category for item in run.certification.categories], ["source", "canonical", "physical", "manifests", "coverage", "publication"])
        self.assertEqual(run.certification.categories[-1].status, "deferred")

    def test_zero_row_complete_coverage_passes(self):
        writer = FakeCatalogWriter()
        run = self.runtime(writer).run(self.evidence([]))
        self.assertEqual(run.certification.status, "pass")
        self.assertEqual(run.quality_report.status, "pass")
        self.assertEqual(run.sealed_partition.state, "closed")

    def test_adjacent_coverage_documents_fold_before_certification(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        first = json.loads(self.coverage_path.read_text())
        first["assertions"][0]["end"] = "2024-01-15T12:00:00Z"
        self.coverage_path.write_text(json.dumps(first))
        second_path = self.root / "coverage-2.json"
        second = json.loads(json.dumps(first))
        second["coverage_id"] = "coverage-2"
        second["assertions"][0]["assertion_id"] = "assertion-2"
        second["assertions"][0]["start"] = "2024-01-15T12:00:00Z"
        second["assertions"][0]["end"] = END
        second_path.write_text(json.dumps(second))
        evidence = SealedPartitionEvidence(
            evidence.dataset_manifest_path, evidence.partition_manifest_path,
            (evidence.coverage_manifest_paths[0], second_path),
            evidence.artifact_path, evidence.storage_root_id,
        )
        run = self.runtime(writer).run(evidence)
        self.assertEqual(run.certification.status, "pass")
        self.assertEqual(run.sealed_partition.ts_start, Instant.parse(START))
        self.assertEqual(run.sealed_partition.ts_end, Instant.parse(END))

    def test_coverage_gap_is_refused_before_seal(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        document = json.loads(self.coverage_path.read_text())
        document["assertions"][0]["end"] = "2024-01-15T12:00:00Z"
        self.coverage_path.write_text(json.dumps(document))
        second_path = self.root / "coverage-gap.json"
        second = json.loads(json.dumps(document))
        second["coverage_id"] = "coverage-gap-2"
        second["assertions"][0]["assertion_id"] = "assertion-gap-2"
        second["assertions"][0]["start"] = "2024-01-15T13:00:00Z"
        second["assertions"][0]["end"] = END
        second_path.write_text(json.dumps(second))
        evidence = SealedPartitionEvidence(
            evidence.dataset_manifest_path, evidence.partition_manifest_path,
            (evidence.coverage_manifest_paths[0], second_path),
            evidence.artifact_path, evidence.storage_root_id,
        )
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(evidence)
        self.assertIsNone(writer.partition)

    def test_null_trade_id_is_fail_closed_and_records_fail_not_pass(self):
        writer = FakeCatalogWriter()
        run = self.runtime(writer).run(self.evidence([trade("2024-01-15T00:00:01Z", None)]))
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(run.quality_report.status, "fail")
        self.assertEqual(writer.reports[0]["status"], "fail")
        self.assertEqual(run.sealed_partition.state, "closed")

    def test_missing_source_evidence_is_fail_closed(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        document = json.loads(self.coverage_path.read_text())
        document["assertions"][0]["evidence"] = []
        self.coverage_path.write_text(json.dumps(document))
        # Durable malformed evidence cannot pass the Phase 1 seal.
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(evidence)
        self.assertEqual(writer.reports, [])

    def test_physical_hash_mismatch_does_not_sort_or_publish(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        self.data_path.write_bytes(self.data_path.read_bytes() + b"tamper")
        run = self.runtime(writer).run(evidence)
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(run.quality_report.status, "fail")
        self.assertEqual(run.sealed_partition.state, "closed")

    def test_missing_artifact_is_fail_closed_after_seal(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        sealed = self.runtime(writer).seal(evidence)
        self.data_path.unlink()
        result = self.runtime(writer).certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertEqual(next(item for item in result.categories if item.category == "physical").status, "fail")
        self.assertEqual(writer.reports, [])

    def test_corrupt_parquet_is_rejected_after_authoritative_hash_update(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        self.data_path.write_bytes(b"not a parquet file")
        self.rewrite_partition_physical_evidence()
        run = self.runtime(writer).run(evidence)
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(run.quality_report.status, "fail")
        self.assertEqual(run.sealed_partition.state, "closed")

    def test_wrong_parquet_schema_is_rejected(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        writer = FakeCatalogWriter()
        evidence = self.evidence()
        pq.write_table(pa.table({"wrong": [1, 2]}), str(self.data_path), compression="zstd")
        self.rewrite_partition_physical_evidence()
        run = self.runtime(writer).run(evidence)
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(next(item for item in run.certification.categories if item.category == "physical").status, "fail")

    def test_file_size_mismatch_is_rejected(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        sealed = self.runtime(writer).seal(evidence)
        self.rewrite_json(self.partition_path, lambda item: item.__setitem__("file_size_bytes", item["file_size_bytes"] + 1))
        result = self.runtime(writer).certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertEqual(next(item for item in result.categories if item.category == "physical").status, "fail")

    def test_manifest_hash_mismatch_is_rejected(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        sealed = self.runtime(writer).seal(evidence)
        self.rewrite_json(self.partition_path, lambda item: item.__setitem__("producer", "different-producer"))
        result = self.runtime(writer).certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertEqual(next(item for item in result.categories if item.category == "manifests").status, "fail")

    def test_dataset_identity_mismatch_is_rejected_before_seal(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        self.rewrite_json(self.dataset_path, lambda item: item.__setitem__("instrument", "ETHUSDT"))
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(evidence)
        self.assertIsNone(writer.partition)

    def test_invalid_revision_is_rejected_before_seal(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        self.rewrite_json(self.partition_path, lambda item: item.__setitem__("revision", 0))
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(evidence)
        self.assertIsNone(writer.partition)

    def test_natural_partition_identity_change_is_rejected_after_seal(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        sealed = self.runtime(writer).seal(evidence)
        def change_partition(item):
            item["partition_key"] = "dt=2024-01-16"
            item["rel_path"] = "dt=2024-01-16/part-000.parquet"
        self.rewrite_json(self.partition_path, change_partition)
        result = self.runtime(writer).certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertTrue(result.violations)

    def test_observed_bounds_mismatch_is_rejected(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        sealed = self.runtime(writer).seal(evidence)
        self.rewrite_json(self.partition_path, lambda item: item.__setitem__("first_exchange_ts", "2024-01-15T00:00:02Z"))
        result = self.runtime(writer).certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertEqual(next(item for item in result.categories if item.category == "physical").status, "fail")

    def test_zero_row_non_null_observed_bounds_are_rejected(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence([])
        self.rewrite_json(self.partition_path, lambda item: item.__setitem__("first_exchange_ts", START))
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(evidence)
        self.assertIsNone(writer.partition)

    def test_physical_ordering_and_duplicate_key_are_not_repaired(self):
        for indices in ((1, 0), (0, 0)):
            with self.subTest(indices=indices):
                writer = FakeCatalogWriter()
                evidence = self.evidence()
                self.rewrite_artifact_rows(indices)
                run = self.runtime(writer).run(evidence)
                self.assertEqual(run.certification.status, "fail")
                self.assertEqual(next(item for item in run.certification.categories if item.category == "physical").status, "fail")

    def test_forbidden_optional_source_fields_fail_canonical_certification(self):
        for kwargs in ({"receive_ts": "2024-01-15T00:00:03Z"}, {"sequence": "1"}):
            with self.subTest(kwargs=kwargs):
                writer = FakeCatalogWriter()
                run = self.runtime(writer).run(self.evidence([trade("2024-01-15T00:00:01Z", "1", **kwargs)]))
                self.assertEqual(run.certification.status, "fail")
                self.assertEqual(next(item for item in run.certification.categories if item.category == "canonical").status, "fail")

    def test_source_identity_and_missing_source_evidence_fail_certification(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        self.rewrite_json(self.coverage_path, lambda item: item["acquisition"].__setitem__("source_semantics", "other-source-v1"))
        run = self.runtime(writer).run(evidence)
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(next(item for item in run.certification.categories if item.category == "source").status, "fail")

    def test_coverage_contradiction_fails_closed(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        contradiction_path = self.root / "coverage-contradiction.json"
        contradiction = json.loads(self.coverage_path.read_text())
        contradiction["coverage_id"] = "coverage-contradiction"
        contradiction["assertions"][0].update({
            "assertion_id": "assertion-known-gap",
            "status": "known_gap",
            "partitions": [],
            "evidence": [{"kind": "transport_interruption", "detail": "known gap"}],
        })
        contradiction_path.write_text(json.dumps(contradiction))
        evidence = SealedPartitionEvidence(
            evidence.dataset_manifest_path, evidence.partition_manifest_path,
            (evidence.coverage_manifest_paths[0], contradiction_path),
            evidence.artifact_path, evidence.storage_root_id,
        )
        with self.assertRaises(RuntimeError) as context:
            self.runtime(writer).run(evidence)
        self.assertIn("COVERAGE_CONTRADICTION", str(context.exception))
        self.assertIsNone(writer.partition)

    def test_observed_record_outside_final_declared_coverage_fails(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence(coverage_end="2024-01-15T00:00:01Z")
        with self.assertRaises(RuntimeError) as context:
            self.runtime(writer).run(evidence)
        self.assertIn("OBSERVED_OUTSIDE_DECLARED", str(context.exception))
        self.assertIsNone(writer.partition)

    def test_missing_certifier_code_ref_cannot_record_evidence(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        runtime = PublicationCertification(writer, BybitTradeV1CertificationProfile(""), batch_size=1)
        sealed = runtime.seal(evidence)
        result = runtime.certify(evidence, sealed)
        with self.assertRaises(RuntimeError):
            runtime.record_evidence(sealed, result)
        self.assertEqual(writer.reports, [])

    def test_phase_three_failure_leaves_closed_row_without_report(self):
        class FailingWriter(FakeCatalogWriter):
            def record_quality_report(self, **kwargs):
                raise RuntimeError("simulated phase three failure")

        writer = FailingWriter()
        with self.assertRaises(RuntimeError):
            self.runtime(writer).run(self.evidence())
        self.assertEqual(writer.partition.state, "closed")
        self.assertEqual(writer.reports, [])
        self.assertEqual(writer.rollbacks, 1)

    def test_canonical_hash_is_layout_independent_and_physical_hash_is_not_required_equal(self):
        records = [trade("2024-01-15T00:00:01Z", "1"), trade("2024-01-15T00:00:02Z", "2")]
        first = materialize_bybit_trade_v1(self.root / "first.parquet", records, dataset_identity=IDENTITY, compression="zstd", row_group_size=1)
        second = materialize_bybit_trade_v1(self.root / "second.parquet", records, dataset_identity=IDENTITY, compression="snappy", row_group_size=2)
        self.assertEqual(first.canonical_content_hash_v1, second.canonical_content_hash_v1)
        self.assertNotEqual(first.sha256, second.sha256)

    def test_certifier_does_not_expose_lifecycle_promotion(self):
        source = (ROOT / "src" / "quant_platform" / "data" / "publication.py").read_text()
        writer = (ROOT / "src" / "quant_platform" / "data" / "publication_catalog.py").read_text()
        self.assertNotIn("state = 'valid'", source + writer)
        self.assertNotIn("state = 'degraded'", source + writer)
        self.assertNotIn("UPDATE catalog.partitions SET state", source + writer)

    def test_phase_two_has_no_catalog_lifecycle_side_effect(self):
        writer = FakeCatalogWriter()
        evidence = self.evidence()
        runtime = self.runtime(writer)
        sealed = runtime.seal(evidence)
        commits = writer.commits
        result = runtime.certify(evidence, sealed)
        self.assertEqual(result.status, "pass")
        self.assertEqual(writer.commits, commits)
        self.assertEqual(writer.partition.state, "closed")

    def test_s13_responsibilities_are_explicit_protocol_seams(self):
        self.assertIn(CatalogSealer, PublicationCertification.__bases__)
        self.assertIn(Certifier, PublicationCertification.__bases__)
        self.assertIn(CertificationEvidenceRecorder, PublicationCertification.__bases__)

    def test_generic_certifier_has_no_source_adapter_dependency(self):
        tree = ast.parse((ROOT / "src" / "quant_platform" / "data" / "publication.py").read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        imported = " ".join(ast.unparse(node) for node in imports)
        self.assertNotIn("source_adapters", imported)
        self.assertNotIn("bybit", imported.lower())

    def test_fake_second_profile_is_injected_without_generic_branch(self):
        class FakeProfile:
            profile_id = "fake-venue-profile-v1"
            check_suite = "fake-suite"
            code_ref = "fake-certifier"
            def applies_to(self, identity): return identity.venue == "fake"
            def ordering_key(self, record): return (record.exchange_ts.epoch_ns, record.trade_id)
            def validate_source(self, identity, coverage_documents): return {"source": "fake"}
            def validate_records(self, identity, records): return {"records": len(records)}
        profile = FakeProfile()
        identity = DatasetIdentity("canonical", "trades", "fake", "X", "trade-v1")
        self.assertTrue(profile.applies_to(identity))
        self.assertEqual(profile.validate_source(identity, ()), {"source": "fake"})
        self.assertEqual(profile.validate_records(identity, ()), {"records": 0})
        artifact = self.root / "dt=2024-01-15" / "part-000.parquet"
        fake_provider = OrderingProvider(
            "fake-ordering", frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
            lambda item: (item.exchange_ts, item.trade_id or ""),
            lambda candidate: candidate == identity,
        )
        fake_records = (TradeRecord("fake", "X", Instant.parse("2024-01-15T00:00:01Z"), "100.00", "0.5000", "buy", None, "1", None),)
        materialization = materialize_trade_v1(
            artifact, fake_records, dataset_identity=identity, ordering_provider=fake_provider,
        )
        dataset_path = self.root / "fake-dataset.json"
        partition_path = self.root / "fake-partition.json"
        coverage_path = self.root / "fake-coverage.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "fake", "X", "trade-v1")],
            transform="fake-canonicalize-v1",
        )
        partition = emit_partition_manifest(
            partition_path, materialization, dataset_identity=identity, dataset_root=self.root,
            partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-01T10:00:00Z", closed_at="2026-09-01T10:00:01Z",
            producer="fake-producer", code_ref="fake-producer-ref",
        )
        emit_coverage_manifest(
            coverage_path, dataset_identity=identity, source_dataset_identity=identity,
            coverage_id="fake-coverage", supersedes=None, created_at="2026-09-01T10:00:02Z",
            acquisition={"basis": "source_extract", "intent_start": START, "intent_end": END,
                         "source_semantics": "fake-source-v1", "mapping": "fake-map-v1"},
            assertions=[{"assertion_id": "fake-assertion", "start": START, "end": END,
                         "status": "complete", "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
                         "evidence": [{"kind": "deterministic_source_extract", "detail": "fake source"}]}],
            producer="fake-source", code_ref="fake-source-ref", partition_manifests=[partition.document],
        )
        writer = FakeCatalogWriter()
        run = PublicationCertification(writer, profile).run(SealedPartitionEvidence(
            dataset_path, partition_path, (coverage_path,), artifact, "hot",
        ))
        self.assertEqual(run.certification.status, "pass")
        self.assertEqual(run.sealed_partition.state, "closed")
        self.assertEqual(run.quality_report.check_suite, "fake-suite")

    def test_profile_mismatch_prevents_phase_one_seal(self):
        class NonApplicableProfile:
            profile_id = "not-applicable"
            check_suite = "not-applicable"
            code_ref = "certifier"
            def applies_to(self, identity): return False
            def ordering_key(self, record): return ()
            def validate_source(self, identity, coverage_documents): return {}
            def validate_records(self, identity, records): return {}

        writer = FakeCatalogWriter()
        with self.assertRaises(RuntimeError):
            PublicationCertification(writer, NonApplicableProfile()).run(self.evidence())
        self.assertIsNone(writer.partition)

    def test_postgres_writer_boundary_has_only_closed_seal_and_quality_report_writes(self):
        class Cursor:
            def __init__(self):
                self.responses = [None, ("dataset-1",), None, ("partition-1",), ("closed",), ("report-1",)]
                self.sql = []
                self.executions = []
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, statement, params=None):
                self.sql.append(statement)
                self.executions.append((statement, params))
            def fetchone(self): return self.responses.pop(0)
            def fetchall(self): return []
        class Connection:
            def __init__(self): self.cursor_instance = Cursor(); self.commits = 0; self.rollbacks = 0
            def cursor(self): return self.cursor_instance
            def commit(self): self.commits += 1
            def rollback(self): self.rollbacks += 1
        connection = Connection()
        writer = CatalogPublicationWriter(connection)
        partition = json.loads(self.evidence().partition_manifest_path.read_text())
        partition["_manifest_sha256"] = "a" * 64
        dataset = json.loads(self.evidence().dataset_manifest_path.read_text())
        dataset["_manifest_sha256"] = "b" * 64
        sealed = writer.seal_partition(
            dataset=dataset, partition=partition,
            coverage_start=Instant.parse(START), coverage_end=Instant.parse(END), storage_root_id="hot",
        )
        report = writer.record_quality_report(
            partition_id=sealed.partition_id, check_suite="suite", status="pass",
            metrics={"evidence": {}}, violations=[], code_ref="certifier",
        )
        self.assertEqual(sealed.state, "closed")
        self.assertEqual(report.status, "pass")
        statements = " ".join(connection.cursor_instance.sql)
        self.assertIn("'closed'", statements)
        self.assertNotIn("state = 'valid'", statements)
        self.assertNotIn("state = 'degraded'", statements)

        partition_sql, partition_params = next(
            (statement, params)
            for statement, params in connection.cursor_instance.executions
            if "INSERT INTO catalog.partitions" in statement
        )
        target_columns = partition_sql.split("INSERT INTO catalog.partitions (", 1)[1].split(") VALUES", 1)[0]
        self.assertEqual(len([item for item in target_columns.split(",") if item.strip()]), 17)
        self.assertEqual(partition_sql.count("%s"), 16)
        self.assertEqual(partition_sql.count("'closed'"), 1)
        self.assertEqual(len(partition_params), 16)
        self.assertEqual(tuple(partition_params[:3]), ("dataset-1", "dt=2024-01-15", 1))
        self.assertEqual(tuple(partition_params[3:10]), (
            "hot", partition["rel_path"], Instant.parse(START).to_datetime(),
            Instant.parse(END).to_datetime(), partition["row_count"],
            partition["file_size_bytes"], partition["sha256"],
        ))
        self.assertEqual(partition_params[10], "a" * 64)
        self.assertEqual(partition_params[11], partition["closed_at"])
        self.assertEqual(tuple(partition_params[12:]), (None, None, partition["producer"], partition["code_ref"]))


if __name__ == "__main__":
    unittest.main()
