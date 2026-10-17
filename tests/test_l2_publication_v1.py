#!/usr/bin/env python3
"""Hermetic tests for L2 publication certification v1 (A14c, ADR-0070 §4-§5)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

import quant_platform.data.l2_publication as l2_publication  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant, NaturalPartitionIdentity  # noqa: E402
from quant_platform.data.l2_materializer import (  # noqa: E402
    _table,
    materialize_l2_book_event_v1,
)
from quant_platform.data.l2_parquet import (  # noqa: E402
    l2_book_event_arrow_schema,
    l2_canonical_content_hash_v1,
    scan_l2_book_event_v1_all,
)
from quant_platform.data.l2_publication import L2PublicationCertification  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.models import L2BookEvent, L2LevelChange  # noqa: E402
from quant_platform.data.publication import (  # noqa: E402
    PublicationCertification,
    SealedCatalogPartition,
    SealedPartitionEvidence,
)
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
    BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
    BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
    bybit_l2_continuity_evidence_detail,
)

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
RAW = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"
BASE_NS = Instant.parse("2024-01-15T00:00:01Z").epoch_ns
CHANNEL = "orderbook.200.BTCUSDT"


def up(side, price, size="1"):
    return L2LevelChange(side, price, size, "upsert")


def rm(side, price):
    return L2LevelChange(side, price, "0", "delete")


def event(seq, u, kind="delta", *, bids=(), asks=(), ns=None):
    return L2BookEvent(
        venue="bybit",
        market_type="linear_perp",
        instrument="BTCUSDT",
        native_symbol="BTCUSDT",
        source_channel=CHANNEL,
        acquisition_mode="historical_archive",
        event_type=kind,
        exchange_ts=Instant(BASE_NS + (ns if ns is not None else seq * 1_000)),
        provider_ts=Instant(BASE_NS + (ns if ns is not None else seq * 1_000) + 2_000_000),
        source_depth_limit=200,
        native_sequence=str(seq),
        native_update_id=str(u),
        bids=bids or (up("bid", "100.0", "5"),),
        asks=asks,
    )


def clean_events():
    return [
        event(100, 50, "snapshot", bids=(up("bid", "100.0", "5"), up("bid", "99.0", "7")),
              asks=(up("ask", "101.0", "4"), up("ask", "102.0", "9"))),
        event(101, 51, bids=(up("bid", "100.5", "2"),)),
        event(150, 52, asks=(rm("ask", "102.0"),), bids=(up("bid", "99.5", "1"),)),
    ]


class FakeCatalogWriter:
    def __init__(self):
        self.partitions = []
        self.reports = []
        self.commits = 0
        self.rollbacks = 0

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id):
        identity = DatasetIdentity(
            dataset["layer"], dataset["dataset_kind"], dataset["venue"],
            dataset["instrument"], dataset["record_schema_id"],
        )
        existing = next(
            (p for p in self.partitions
             if p.natural_identity.partition_key == partition["partition_key"]
             and p.natural_identity.revision == partition["revision"]), None)
        if existing is not None:
            return existing
        admitted = SealedCatalogPartition(
            f"partition-uuid-{len(self.partitions) + 1}", "dataset-uuid-1",
            NaturalPartitionIdentity(identity, partition["partition_key"], partition["revision"]),
            "closed", coverage_start, coverage_end,
            partition["row_count"], partition["file_size_bytes"],
            partition["sha256"], partition["_manifest_sha256"],
            partition["producer"], partition["code_ref"],
        )
        self.partitions.append(admitted)
        return admitted

    def record_quality_report(self, *, partition_id, check_suite, status, metrics, violations, code_ref):
        report = {
            "partition_id": partition_id, "check_suite": check_suite, "status": status,
            "metrics": dict(metrics), "violations": list(violations), "code_ref": code_ref,
        }
        self.reports.append(report)
        return type("Report", (), {**report, "report_id": f"report-{len(self.reports)}"})()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l2-pub-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data_path = self.tmp / "dt=2024-01-15" / "part-001.parquet"
        self.dataset_path = self.tmp / "dataset.json"
        self.partition_path = self.tmp / "partition.json"
        self.coverage_path = self.tmp / "coverage.json"
        self.profile = BybitL2BookEventV1CertificationProfile("l2-test-certifier")
        self.writer = FakeCatalogWriter()

    def build(self, events=None, *, evidence_kinds=None, detail=None, basis=None,
              semantics=None, mapping=None, coverage_end=END, sequence_detail_override=None):
        events = events if events is not None else clean_events()
        report = self.profile.certify(IDENTITY, events)
        materialization = materialize_l2_book_event_v1(
            self.data_path, events, dataset_identity=IDENTITY,
            ordering_provider=BYBIT_L2_ORDERING_PROVIDER,
        )
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=IDENTITY, created_at="2026-10-16T10:00:00Z",
            derived_from=[RAW], transform="canonicalize-l2-book-events-v1",
        )
        emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=IDENTITY,
            dataset_root=self.tmp, partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-001.parquet",
            created_at="2026-10-16T10:00:00Z", closed_at="2026-10-16T10:00:01Z",
            producer="test-l2-materializer", code_ref="producer-ref",
        )
        continuity = sequence_detail_override or bybit_l2_continuity_evidence_detail(report)
        kinds = evidence_kinds or ("archive_completeness", "sequence_continuity")
        evidence = []
        for kind in kinds:
            evidence.append({
                "kind": kind,
                "detail": continuity if kind == "sequence_continuity" else (detail or "archive day complete"),
            })
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
            coverage_id="coverage-1", supersedes=None, created_at="2026-10-16T10:00:02Z",
            acquisition={
                "basis": basis or BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
                "intent_start": START, "intent_end": END,
                "source_semantics": semantics or BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
                "mapping": mapping or BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
            },
            assertions=[{
                "assertion_id": "assertion-1", "start": START, "end": coverage_end,
                "status": "complete",
                "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}],
                "evidence": evidence,
            }],
            producer="test-source", code_ref="source-ref",
            partition_manifests=[json.loads(self.partition_path.read_text())],
        )
        self.materialization = materialization
        return SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,), self.data_path, "hot"
        )

    def runtime(self, **kwargs):
        return L2PublicationCertification(self.writer, self.profile, **kwargs)

    def run_certification(self, evidence):
        return self.runtime().run(evidence)

    @staticmethod
    def statuses(result):
        return {c.category: c.status for c in result.categories}

    def rewrite_partition_physical_evidence(self):
        document = json.loads(self.partition_path.read_text())
        raw = self.data_path.read_bytes()
        document["sha256"] = hashlib.sha256(raw).hexdigest()
        document["file_size_bytes"] = len(raw)
        self.partition_path.write_text(json.dumps(document))

    def rewrite_artifact(self, events, *, schema=None):
        schema = schema or l2_book_event_arrow_schema(pa)
        table = _table(list(events), schema, pa)
        pq.write_table(table, str(self.data_path))
        self.rewrite_partition_physical_evidence()


class PassPathTests(Base):
    def test_a_clean_partition_certifies_pass_and_records_the_report(self):
        evidence = self.build()
        run = self.run_certification(evidence)
        result = run.certification
        self.assertEqual(result.status, "pass", result.violations)
        self.assertEqual(
            self.statuses(result),
            {"source": "pass", "canonical": "pass", "physical": "pass",
             "manifests": "pass", "coverage": "pass", "publication": "deferred"},
        )
        self.assertEqual(run.sealed_partition.state, "closed")
        self.assertEqual(self.writer.commits, 2)  # seal + evidence
        self.assertEqual(len(self.writer.reports), 1)
        report = self.writer.reports[0]
        self.assertEqual(report["check_suite"], self.profile.check_suite)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["violations"], [])
        metrics = report["metrics"]
        self.assertEqual(metrics["certification_profile"], self.profile.profile_id)
        self.assertEqual(metrics["physical_artifact_hash"], self.materialization.sha256)
        self.assertEqual(
            metrics["canonical_content_hash_v1"], self.materialization.canonical_content_hash_v1
        )
        self.assertEqual(
            metrics["canonical_content_hash_v1"],
            l2_canonical_content_hash_v1(
                [e for batch in scan_l2_book_event_v1_all(self.data_path) for e in batch]
            ),
        )
        canonical = metrics["evidence"]["canonical"]["l2_certification"]
        self.assertEqual(canonical["outcome"], "eligible")
        self.assertEqual(canonical["event_count"], 3)
        self.assertEqual(
            metrics["evidence"]["source"]["sequence_continuity_detail"],
            bybit_l2_continuity_evidence_detail(self.profile.certify(IDENTITY, clean_events())),
        )

    def test_the_metrics_have_the_shape_the_quality_lifecycle_reads(self):
        from quant_platform.data.quality_lifecycle import current_partition_report

        evidence = self.build()
        run = self.run_certification(evidence)
        report = self.writer.reports[0]
        partition = json.loads(self.partition_path.read_text())
        dataset = json.loads(self.dataset_path.read_text())
        coverage = json.loads(self.coverage_path.read_text())
        dataset_sha = hashlib.sha256(self.dataset_path.read_bytes()).hexdigest()
        partition_sha = hashlib.sha256(self.partition_path.read_bytes()).hexdigest()
        coverage_sha = hashlib.sha256(self.coverage_path.read_bytes()).hexdigest()
        target = [None] * 12
        target[11] = partition["sha256"]
        self.assertTrue(
            current_partition_report(
                report["metrics"], report["status"], report["violations"], report["code_ref"],
                self.profile.profile_id, self.profile.check_suite, IDENTITY, partition,
                dataset_sha, partition_sha, [coverage["coverage_id"]],
                [coverage["assertions"][0]["assertion_id"]], [coverage_sha], target,
            )
        )
        self.assertEqual(run.certification.publication_eligibility, "deferred-to-s14-bridge")
        self.assertEqual(dataset["dataset_kind"], "l2")

    def test_the_sibling_inherits_seal_record_and_run_unchanged(self):
        for name in ("seal", "record_evidence", "run"):
            self.assertIs(
                getattr(L2PublicationCertification, name), getattr(PublicationCertification, name)
            )
        self.assertIsNot(L2PublicationCertification.certify, PublicationCertification.certify)

    def test_a_replay_is_idempotent(self):
        evidence = self.build()
        first = self.run_certification(evidence)
        second = self.run_certification(evidence)
        self.assertEqual(first.sealed_partition.partition_id, second.sealed_partition.partition_id)
        self.assertEqual(first.certification.status, second.certification.status)
        self.assertEqual(
            first.certification.metrics["canonical_content_hash_v1"],
            second.certification.metrics["canonical_content_hash_v1"],
        )


class EventOutcomeTests(Base):
    def assertCanonicalFails(self, events, fragment):
        evidence = self.build(events)
        run = self.run_certification(evidence)
        result = run.certification
        self.assertEqual(result.status, "fail")
        self.assertEqual(self.statuses(result)["canonical"], "fail")
        messages = [v["message"] for v in result.violations if v["category"] == "canonical"]
        self.assertTrue(any(fragment in m for m in messages), messages)
        # the artifact itself is fine, the evidence is bound, and the hash is recorded
        self.assertEqual(self.statuses(result)["physical"], "pass")
        self.assertIsNotNone(result.metrics["canonical_content_hash_v1"])
        self.assertEqual(run.sealed_partition.state, "closed")
        return result

    def test_a_u_discontinuity_fails_canonical_because_it_is_not_publishable(self):
        events = clean_events() + [event(151, 90), event(152, 91)]
        result = self.assertCanonicalFails(events, "not publishable")
        summary = result.metrics["evidence"]["canonical"]["l2_certification"]
        self.assertEqual(summary["outcome"], "degraded")
        self.assertEqual(summary["discontinuities"], 1)

    def test_an_unanchored_head_fails_canonical(self):
        events = [event(100, 50), event(101, 51)] + [
            event(102, 1, "snapshot"), event(103, 2)
        ]
        result = self.assertCanonicalFails(events, "not publishable")
        self.assertTrue(
            result.metrics["evidence"]["canonical"]["l2_certification"]["unanchored_head"]
        )

    def test_an_ineligible_event_stream_fails_canonical_with_its_codes(self):
        events = clean_events() + [
            event(151, 53, bids=(up("bid", "103.0", "1"),))  # crosses the best ask 101.0
        ]
        self.assertCanonicalFails(events, "crossed_book")

    def test_exchange_time_regression_is_ineligible(self):
        events = [
            event(100, 50, "snapshot", ns=5_000),
            event(101, 51, ns=3_000),  # time goes backwards along the sequence
            event(102, 52, ns=9_000),
        ]
        self.assertCanonicalFails(events, "event_time_regression")


class PhysicalTests(Base):
    def test_tampered_bytes_fail_the_physical_category(self):
        evidence = self.build()
        sealed = self.runtime().seal(evidence)
        raw = bytearray(self.data_path.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        self.data_path.write_bytes(bytes(raw))
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(result.status, "fail")
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertEqual(self.statuses(result)["canonical"], "fail")
        self.assertTrue(
            any("hash mismatches" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_a_missing_artifact_fails_physical(self):
        evidence = self.build()
        sealed = self.runtime().seal(evidence)
        self.data_path.unlink()
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertTrue(
            any("artifact is missing" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_a_row_count_mismatch_fails_physical(self):
        evidence = self.build()
        # a different artifact with the manifest's hash and size refreshed but the old row_count
        self.rewrite_artifact(clean_events()[:2])
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertTrue(
            any("row count" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_a_file_size_that_disagrees_with_the_evidence_fails_physical(self):
        evidence = self.build()
        document = json.loads(self.partition_path.read_text())
        document["file_size_bytes"] += 1  # the hash still matches; only the size claim is wrong
        self.partition_path.write_text(json.dumps(document))
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertTrue(
            any("file size" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_duplicate_rows_fail_physical_not_only_canonical(self):
        evidence = self.build()
        events = clean_events()
        self.rewrite_artifact([events[0], events[1], events[1], events[2]])
        document = json.loads(self.partition_path.read_text())
        document["row_count"] = 4
        self.partition_path.write_text(json.dumps(document))
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertTrue(
            any("strictly ordered" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_a_zero_event_partition_certifies(self):
        # The manifest validator already refuses observed bounds on a zero-row
        # partition, so the certifier's own zero-row bounds check is defence in
        # depth with no reachable failing input.
        evidence = self.build([])
        result = self.run_certification(evidence).certification
        self.assertEqual(result.status, "pass", result.violations)
        self.assertEqual(
            result.metrics["evidence"]["canonical"]["l2_certification"]["event_count"], 0
        )
        self.assertEqual(result.metrics["evidence"]["physical"]["row_count"], 0)

    def test_observed_bounds_mismatch_fails_physical(self):
        evidence = self.build()
        document = json.loads(self.partition_path.read_text())
        document["last_exchange_ts"] = "2024-01-15T00:00:02Z"
        self.partition_path.write_text(json.dumps(document))
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")

    def test_out_of_order_rows_fail_physical(self):
        evidence = self.build()
        events = clean_events()
        events[1], events[2] = events[2], events[1]
        self.rewrite_artifact(events)
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")
        self.assertTrue(
            any("strictly ordered" in v["message"] for v in result.violations if v["category"] == "physical")
        )

    def test_a_foreign_layout_fails_physical(self):
        evidence = self.build()
        table = pa.table({"venue": ["bybit"], "surprise": [1]})
        pq.write_table(table, str(self.data_path))
        self.rewrite_partition_physical_evidence()
        sealed = self.runtime().seal(evidence)
        result = self.runtime().certify(evidence, sealed)
        self.assertEqual(self.statuses(result)["physical"], "fail")


class SourceEvidenceTests(Base):
    def certify_source(self, **kwargs):
        evidence = self.build(**kwargs)
        sealed = self.runtime().seal(evidence)
        return self.runtime().certify(evidence, sealed)

    def assertSourceFails(self, result, fragment):
        self.assertEqual(result.status, "fail")
        self.assertEqual(self.statuses(result)["source"], "fail")
        messages = [v["message"] for v in result.violations if v["category"] == "source"]
        self.assertTrue(any(fragment in m for m in messages), messages)

    def test_complete_coverage_needs_archive_completeness(self):
        result = self.certify_source(evidence_kinds=("sequence_continuity",))
        self.assertSourceFails(result, "archive_completeness")

    def test_complete_coverage_needs_sequence_continuity(self):
        result = self.certify_source(evidence_kinds=("archive_completeness",))
        self.assertSourceFails(result, "sequence_continuity")

    def test_the_continuity_detail_must_describe_the_stored_events(self):
        result = self.certify_source(sequence_detail_override="first=seq:1;u:1|events=99")
        self.assertSourceFails(result, "does not describe the stored events")

    def test_a_detail_for_a_different_artifact_is_refused(self):
        other = clean_events()[:2]
        report = self.profile.certify(IDENTITY, other)
        result = self.certify_source(
            sequence_detail_override=bybit_l2_continuity_evidence_detail(report)
        )
        self.assertSourceFails(result, "does not describe the stored events")

    def test_source_validation_refuses_each_missing_piece_directly(self):
        from quant_platform.source_adapters.bybit_l2 import (
            BybitL2EligibilityError,
            validate_bybit_l2_coverage_source,
        )

        def document(kinds):
            return {
                "acquisition": {
                    "basis": BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
                    "source_semantics": BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
                    "mapping": BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
                },
                "assertions": [{
                    "assertion_id": "a", "status": "complete",
                    "evidence": [{"kind": k, "detail": "x"} for k in kinds],
                }],
            }

        ok = validate_bybit_l2_coverage_source(
            IDENTITY, [document(("archive_completeness", "sequence_continuity"))]
        )
        self.assertEqual(ok["complete_assertions"], 1)
        for kinds in (("archive_completeness",), ("sequence_continuity",), ()):
            with self.subTest(kinds=kinds):
                with self.assertRaises(BybitL2EligibilityError):
                    validate_bybit_l2_coverage_source(IDENTITY, [document(kinds)])
        with self.assertRaises(BybitL2EligibilityError):
            validate_bybit_l2_coverage_source(IDENTITY, [])
        with self.assertRaises(BybitL2EligibilityError):
            validate_bybit_l2_coverage_source(
                DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"),
                [document(("archive_completeness", "sequence_continuity"))],
            )

    def test_the_continuity_detail_format_is_frozen(self):
        report = self.profile.certify(IDENTITY, clean_events())
        self.assertEqual(
            bybit_l2_continuity_evidence_detail(report),
            "first=seq:100;u:50;type:snapshot;ts:2024-01-15T00:00:01.0001Z"
            "|last=seq:150;u:52;type:delta;ts:2024-01-15T00:00:01.00015Z"
            "|events=3|segments=1",
        )
        self.assertEqual(
            bybit_l2_continuity_evidence_detail(self.profile.certify(IDENTITY, [])),
            "first=none|last=none|events=0|segments=0",
        )

    def test_every_continuity_evidence_item_must_match(self):
        from quant_platform.source_adapters.bybit_l2 import (
            BybitL2EligibilityError,
            validate_bybit_l2_coverage_against_events,
        )

        report = self.profile.certify(IDENTITY, clean_events())
        good = bybit_l2_continuity_evidence_detail(report)

        def documents(*details):
            return [{
                "assertions": [{
                    "assertion_id": "a", "status": "complete",
                    "evidence": [{"kind": "sequence_continuity", "detail": d} for d in details],
                }]
            }]

        self.assertEqual(
            validate_bybit_l2_coverage_against_events(documents(good), report)["bound_assertions"], 1
        )
        for details in ((), ("wrong",), (good, "wrong"), ("wrong", good)):
            with self.subTest(details=details):
                with self.assertRaises(BybitL2EligibilityError):
                    validate_bybit_l2_coverage_against_events(documents(*details), report)
        # a document with no complete assertion binds nothing and is not an error here
        self.assertEqual(
            validate_bybit_l2_coverage_against_events(
                [{"assertions": [{"assertion_id": "g", "status": "known_gap", "evidence": []}]}], report
            )["bound_assertions"],
            0,
        )

    def test_basis_semantics_and_mapping_are_checked(self):
        self.assertSourceFails(self.certify_source(basis="source_extract"), "coverage basis")
        self.assertSourceFails(
            self.certify_source(semantics="bybit-orderbook-l2-v1"), "source_semantics"
        )
        self.assertSourceFails(
            self.certify_source(mapping="bybit-sqlite-day-extract-v1"), "mapping"
        )

    def test_a_non_l2_identity_is_not_certifiable(self):
        profile = self.profile
        evidence = self.build()
        dataset = json.loads(self.dataset_path.read_text())
        self.assertTrue(profile.applies_to(IDENTITY))
        self.assertEqual(dataset["record_schema_id"], "l2-book-event-v1")
        trade_identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
        self.assertFalse(profile.applies_to(trade_identity))
        del evidence

    def test_coverage_not_covering_the_events_is_refused_at_the_seal(self):
        from quant_platform.data.publication import PublicationCertificationError

        evidence = self.build(coverage_end="2024-01-15T00:00:01.0001Z")
        with self.assertRaises(PublicationCertificationError):
            self.runtime().seal(evidence)
        self.assertEqual(self.writer.partitions, [])


class StreamingTests(Base):
    def test_the_artifact_is_read_lazily_in_one_pass(self):
        evidence = self.build(
            [event(100, 50, "snapshot")] + [event(101 + i, 51 + i) for i in range(40)]
        )
        sealed = self.runtime().seal(evidence)
        produced = {"batches": 0, "events": 0}
        at_first_event: dict[str, int] = {}
        original_scan = l2_publication.scan_l2_book_event_v1_all
        original_validate = self.profile.validate_events

        def counting_scan(path, *, batch_size):
            for batch in original_scan(path, batch_size=batch_size):
                produced["batches"] += 1
                produced["events"] += len(batch)
                yield batch

        def validate(identity, events):
            self.assertNotIsInstance(events, (list, tuple))

            def tapped():
                first = True
                for item in events:
                    if first:
                        at_first_event.update(produced)
                        first = False
                    yield item

            return original_validate(identity, tapped())

        profile = _ValidateWrapper(self.profile, validate)
        with mock.patch.object(l2_publication, "scan_l2_book_event_v1_all", counting_scan):
            result = L2PublicationCertification(self.writer, profile, batch_size=8).certify(
                evidence, sealed
            )
        self.assertEqual(result.status, "pass", result.violations)
        self.assertEqual(produced["events"], 41)
        self.assertEqual(produced["batches"], 6)  # ceil(41 / 8), consumed batch by batch
        # when the first event reached the certifier only the first batch had been read
        self.assertEqual(at_first_event["batches"], 1)
        self.assertLessEqual(at_first_event["events"], 8)


class _ValidateWrapper:
    def __init__(self, profile, validate):
        self._profile = profile
        self.validate_events = validate

    def __getattr__(self, name):
        return getattr(self._profile, name)


if __name__ == "__main__":
    unittest.main()
