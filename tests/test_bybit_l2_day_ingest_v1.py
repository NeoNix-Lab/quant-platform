#!/usr/bin/env python3
"""Hermetic tests for the Bybit historical L2 day ingest (A14d, ADR-0070
Amendment 1)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import bybit_l2_day_ingest as ingest_module  # noqa: E402
from quant_platform.application.bybit_l2_day_ingest import (  # noqa: E402
    L2DayRefusal,
    ingest_bybit_l2_day,
)
from quant_platform.data import DatasetIdentity, Instant, NaturalPartitionIdentity  # noqa: E402
from quant_platform.data.coverage import reconstruct_catalog_coverage  # noqa: E402
from quant_platform.data.l2_publication import L2PublicationCertification  # noqa: E402
from quant_platform.data.publication import SealedCatalogPartition  # noqa: E402
from quant_platform.source_adapters.bybit_l2 import BybitL2BookEventV1CertificationProfile  # noqa: E402
from quant_platform.source_adapters.bybit_l2_archive import (  # noqa: E402
    BybitL2ArchiveError,
    archive_file_name,
    archive_member_name,
    archive_sha256,
    iter_bybit_l2_archive_events,
)

DAY = "2024-01-15"
NEXT = "2024-01-16"
DAY_START = Instant.parse(f"{DAY}T00:00:00Z").epoch_ns
BASE = Instant.parse(f"{DAY}T00:00:01Z").epoch_ns
IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
TOPIC = "orderbook.200.BTCUSDT"
EXCERPT = (
    ROOT / "fixtures" / "l2-book-event-v1" / "bybit-ob200-btcusdt-archive-excerpt-2025-12-01.jsonl"
)


def msg(seq, u, kind="delta", ms=None, *, topic=TOPIC, cts=True, day_base=BASE):
    ms = seq if ms is None else ms
    ts_ms = day_base // 1_000_000 + ms
    message = {
        "topic": topic, "type": kind, "ts": ts_ms + 2,
        "data": {
            "s": "BTCUSDT", "u": u, "seq": seq,
            "b": [["100.0", str(seq)]],
            "a": [["101.0", "4"]] if kind == "snapshot" else [],
        },
    }
    if cts:
        message["cts"] = ts_ms
    return message


class _Unseekable:
    """A write-only stream: zipfile then writes a data descriptor after the data."""

    def __init__(self, handle):
        self._handle = handle

    def write(self, data):
        return self._handle.write(data)

    def flush(self):
        self._handle.flush()


def make_archive(directory: Path, day: str, messages, *, name=None, raw_lines=None, members=None,
                 trailing_newline=True):
    path = directory / (name or archive_file_name(day))
    lines = raw_lines if raw_lines is not None else [json.dumps(m, separators=(",", ":")) for m in messages]
    payload = ("\n".join(lines) + ("\n" if lines and trailing_newline else "")).encode("utf-8")
    with path.open("wb") as handle:
        with zipfile.ZipFile(_Unseekable(handle), "w", zipfile.ZIP_DEFLATED) as archive:
            for member in members or [archive_member_name(day)]:
                archive.writestr(member, payload)
    return path


# scenarios (millisecond offsets default to the sequence number)
CLEAN = [msg(1, 10, "snapshot"), msg(2, 11), msg(3, 12), msg(4, 13), msg(5, 14)]
ONE_GAP = [
    msg(1, 10, "snapshot"), msg(2, 11), msg(3, 12),   # run 1
    msg(4, 50), msg(5, 51),                            # excluded (u 12 -> 50)
    msg(6, 1, "snapshot"), msg(7, 2), msg(8, 3),       # run 2
]
HEAD = [msg(1, 5), msg(2, 6), msg(3, 1, "snapshot"), msg(4, 2)]
OPEN_END = [msg(1, 10, "snapshot"), msg(2, 11), msg(3, 40), msg(4, 41)]
MANY = [
    msg(1, 10, "snapshot"), msg(2, 11),
    msg(3, 99),
    msg(4, 1, "snapshot"), msg(5, 2),
    msg(6, 50), msg(7, 51),
    msg(8, 1, "snapshot"), msg(9, 2),
]


class FakeCatalogWriter:
    def __init__(self):
        self.partitions = []
        self.reports = []

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id):
        identity = DatasetIdentity(
            dataset["layer"], dataset["dataset_kind"], dataset["venue"],
            dataset["instrument"], dataset["record_schema_id"],
        )
        admitted = SealedCatalogPartition(
            f"partition-uuid-{len(self.partitions) + 1}", "dataset-uuid-1",
            NaturalPartitionIdentity(identity, partition["partition_key"], partition["revision"]),
            "closed", coverage_start, coverage_end, partition["row_count"],
            partition["file_size_bytes"], partition["sha256"], partition["_manifest_sha256"],
            partition["producer"], partition["code_ref"],
        )
        self.partitions.append(admitted)
        return admitted

    def record_quality_report(self, *, partition_id, check_suite, status, metrics, violations, code_ref):
        report = {"partition_id": partition_id, "status": status, "metrics": dict(metrics),
                  "violations": list(violations)}
        self.reports.append(report)
        return type("Report", (), {**report, "check_suite": check_suite, "code_ref": code_ref,
                                   "report_id": f"r{len(self.reports)}"})()

    def commit(self):
        pass

    def rollback(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l2-ingest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "canonical" / "l2" / "bybit" / "BTCUSDT" / "l2-book-event-v1"

    def archive(self, messages, **kwargs):
        return make_archive(self.tmp, kwargs.pop("day", DAY), messages, **kwargs)

    def ingest(self, messages=None, *, archive=None, root=None, day=DAY, **kwargs):
        path = archive or self.archive(messages, day=day)
        params = dict(
            day=day, dataset_root=root or self.root, created_at="2026-10-17T10:00:00Z",
            closed_at="2026-10-17T10:00:01Z", producer="test-l2-ingest", code_ref="test-ref",
        )
        params.update(kwargs)
        return ingest_bybit_l2_day(path, **params)

    def refusal(self, messages=None, *, archive=None, **kwargs):
        with self.assertRaises((L2DayRefusal, BybitL2ArchiveError)) as caught:
            self.ingest(messages, archive=archive, **kwargs)
        return caught.exception

    @staticmethod
    def iso(ns):
        return Instant(ns).isoformat()

    def leftovers(self):
        return sorted(p.name for p in self.root.parent.glob("**/.ingest-*")) if self.root.parent.exists() else []

    def certify(self, result):
        writer = FakeCatalogWriter()
        profile = BybitL2BookEventV1CertificationProfile("test-certifier")
        runtime = L2PublicationCertification(writer, profile)
        return [runtime.run(p.evidence).certification for p in result.partitions]


class ArchiveReaderTests(Base):
    def test_a_streamed_zip_with_a_data_descriptor_is_read_lazily(self):
        path = self.archive(CLEAN)
        with zipfile.ZipFile(path) as archive:
            self.assertTrue(archive.infolist()[0].flag_bits & 0x08)  # data descriptor
        events = iter_bybit_l2_archive_events(path, DAY)
        first = next(events)
        self.assertEqual(first.event_type, "snapshot")
        self.assertEqual(first.exchange_ts.epoch_ns, BASE + 1_000_000)  # cts, not ts
        self.assertEqual(first.provider_ts.epoch_ns, BASE + 3_000_000)
        self.assertEqual(first.acquisition_mode, "historical_archive")
        self.assertEqual([e.native_sequence for e in events], ["2", "3", "4", "5"])

    def test_the_real_ob200_excerpt_reads(self):
        lines = EXCERPT.read_text(encoding="utf-8").splitlines()
        path = make_archive(self.tmp, "2025-12-01", None, raw_lines=lines)
        events = list(iter_bybit_l2_archive_events(path, "2025-12-01"))
        self.assertEqual(len(events), 12)
        self.assertEqual(events[0].event_type, "snapshot")
        self.assertEqual(events[0].source_depth_limit, 200)
        self.assertTrue(all(e.source_channel == TOPIC for e in events))

    def test_sha256_is_the_hash_of_the_exact_bytes(self):
        path = self.archive(CLEAN)
        self.assertEqual(archive_sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def assertArchiveError(self, code, **kwargs):
        with self.assertRaises(BybitL2ArchiveError) as caught:
            path = kwargs.pop("archive", None) or self.archive(kwargs.pop("messages", CLEAN), **kwargs)
            list(iter_bybit_l2_archive_events(path, kwargs.get("check_day", DAY)))
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_name_depth_and_day_are_checked(self):
        self.assertArchiveError("source_depth_unsupported", name=f"{DAY}_BTCUSDT_ob500.data.zip")
        self.assertArchiveError("archive_name_mismatch", name=f"{NEXT}_BTCUSDT_ob200.data.zip")
        self.assertArchiveError("archive_name_mismatch", name=f"{DAY}_ETHUSDT_ob200.data.zip")
        self.assertArchiveError("archive_name_mismatch", name="notes.zip")
        with self.assertRaises(BybitL2ArchiveError) as caught:
            list(iter_bybit_l2_archive_events(self.archive(CLEAN), "15-01-2024"))
        self.assertEqual(caught.exception.code, "archive_name_mismatch")

    def test_a_foreign_depth_topic_and_invalid_lines_are_typed(self):
        error = self.assertArchiveError(
            "source_depth_unsupported", messages=[msg(1, 1, "snapshot", topic="orderbook.500.BTCUSDT")]
        )
        self.assertEqual(error.line, 1)
        error = self.assertArchiveError(
            "archive_record_invalid", messages=[msg(1, 1, "snapshot"), msg(2, 2, topic="trades.BTCUSDT")]
        )
        self.assertEqual(error.line, 2)
        error = self.assertArchiveError(
            "archive_record_invalid", messages=[msg(1, 1, "snapshot"), msg(2, 2, cts=False)]
        )
        self.assertEqual(error.line, 2)
        error = self.assertArchiveError(
            "archive_record_invalid", raw_lines=[json.dumps(msg(1, 1, "snapshot")), "{not json"]
        )
        self.assertEqual(error.line, 2)
        self.assertArchiveError("archive_record_invalid", raw_lines=["[1, 2]"])

    def test_unreadable_archives_are_typed(self):
        bad = self.tmp / archive_file_name(DAY)
        bad.write_bytes(b"not a zip")
        self.assertArchiveError("archive_unreadable", archive=bad)
        self.assertArchiveError("archive_unreadable", members=[archive_member_name(DAY), "extra.data"])
        self.assertArchiveError("archive_unreadable", members=["wrong-member.data"])
        with self.assertRaises(BybitL2ArchiveError) as caught:
            archive_sha256(self.tmp / "absent.zip")
        self.assertEqual(caught.exception.code, "archive_unreadable")

    def test_blank_lines_and_a_missing_final_newline_are_tolerated(self):
        lines = [json.dumps(m, separators=(",", ":")) for m in CLEAN]
        path = make_archive(self.tmp, DAY, None, raw_lines=lines[:2] + [""] + lines[2:])
        self.assertEqual(len(list(iter_bybit_l2_archive_events(path, DAY))), 5)
        no_newline = make_archive(
            self.tmp, DAY, None, raw_lines=lines, trailing_newline=False, name=archive_file_name(DAY)
        )
        events = list(iter_bybit_l2_archive_events(no_newline, DAY))
        self.assertEqual([e.native_sequence for e in events], ["1", "2", "3", "4", "5"])
        # an invalid unterminated final line is reported with its own line number
        broken = make_archive(
            self.tmp, DAY, None, raw_lines=lines[:1] + ["{broken"], trailing_newline=False,
            name=archive_file_name(DAY),
        )
        with self.assertRaises(BybitL2ArchiveError) as caught:
            list(iter_bybit_l2_archive_events(broken, DAY))
        self.assertEqual((caught.exception.code, caught.exception.line), ("archive_record_invalid", 2))


class IngestShapeTests(Base):
    def test_a_clean_day_is_one_partition_with_one_complete_assertion(self):
        result = self.ingest(CLEAN)
        self.assertFalse(result.already_published)
        self.assertEqual([p.partition_key for p in result.partitions], [f"dt={DAY}/seg=001"])
        self.assertEqual(result.gaps, ())
        self.assertEqual(result.excluded_events, 0)
        self.assertEqual(result.source_events, 5)
        partition = result.partitions[0]
        self.assertEqual(partition.complete_start, f"{DAY}T00:00:00Z")
        self.assertEqual(partition.complete_end, f"{NEXT}T00:00:00Z")
        self.assertEqual(partition.materialization.row_count, 5)
        self.assertTrue(partition.artifact_path.is_file())
        self.assertEqual(
            partition.artifact_path, self.root / f"dt={DAY}" / "seg=001" / "part-001.parquet"
        )
        document = json.loads(partition.coverage_manifest_path.read_text())
        assertion = document["assertions"][0]
        self.assertEqual(assertion["status"], "complete")
        self.assertEqual(
            [e["kind"] for e in assertion["evidence"]], ["archive_completeness", "sequence_continuity"]
        )
        self.assertIn(archive_sha256(self.tmp / archive_file_name(DAY)), assertion["evidence"][0]["detail"])
        self.assertEqual(self.certify(result)[0].status, "pass")

    def test_one_discontinuity_cuts_two_runs_and_one_gap(self):
        result = self.ingest(ONE_GAP)
        self.assertEqual(
            [p.partition_key for p in result.partitions], [f"dt={DAY}/seg=001", f"dt={DAY}/seg=002"]
        )
        self.assertEqual(result.excluded_events, 2)
        self.assertEqual(result.source_events, 8)
        first, second = result.partitions
        last1 = BASE + 3_000_000
        first2 = BASE + 6_000_000
        self.assertEqual(first.complete_start, f"{DAY}T00:00:00Z")
        self.assertEqual(first.complete_end, self.iso(last1 + 1000))  # ceil_us(last) = +1 us
        self.assertEqual(second.complete_start, self.iso(first2))
        self.assertEqual(second.complete_end, f"{NEXT}T00:00:00Z")
        (gap,) = result.gaps
        self.assertEqual((gap.start, gap.end), (self.iso(last1 + 1000), self.iso(first2)))
        self.assertEqual(gap.excluded_events, 2)
        sha = archive_sha256(self.tmp / archive_file_name(DAY))
        self.assertEqual(
            gap.detail, f"expected_u:13|observed_u:50|excluded_events:2|source_sha256:{sha}"
        )
        self.assertEqual(gap.gap_key, "gap=001")
        self.assertTrue(gap.coverage_manifest_path.is_file())
        self.assertEqual([p.materialization.row_count for p in result.partitions], [3, 3])

    def test_the_cut_day_folds_cleanly_and_every_run_certifies(self):
        result = self.ingest(ONE_GAP)
        documents = [json.loads(p.coverage_manifest_path.read_text()) for p in result.partitions]
        documents += [json.loads(g.coverage_manifest_path.read_text()) for g in result.gaps]
        manifests = [json.loads(p.partition_manifest_path.read_text()) for p in result.partitions]
        folded, violations = reconstruct_catalog_coverage(documents, manifests)
        self.assertEqual(violations, ())
        self.assertEqual(
            {key: (a.isoformat(), b.isoformat()) for key, (a, b) in folded.items()},
            {(p.partition_key, 1): (p.complete_start, p.complete_end) for p in result.partitions},
        )
        self.assertEqual([r.status for r in self.certify(result)], ["pass", "pass"])

    def test_a_day_that_begins_with_deltas_has_a_head_gap(self):
        result = self.ingest(HEAD)
        self.assertEqual([p.partition_key for p in result.partitions], [f"dt={DAY}/seg=001"])
        (gap,) = result.gaps
        self.assertTrue(gap.detail.startswith("unanchored_head|excluded_events:2|source_sha256:"))
        first_ts = BASE + 3_000_000
        self.assertEqual((gap.start, gap.end), (f"{DAY}T00:00:00Z", self.iso(first_ts)))
        self.assertEqual(result.partitions[0].complete_start, self.iso(first_ts))
        self.assertEqual(result.partitions[0].complete_end, f"{NEXT}T00:00:00Z")
        self.assertEqual(self.certify(result)[0].status, "pass")

    def test_a_day_that_ends_inside_a_discontinuity_has_an_open_gap(self):
        result = self.ingest(OPEN_END)
        self.assertEqual(len(result.partitions), 1)
        (gap,) = result.gaps
        last = BASE + 2_000_000
        self.assertEqual((gap.start, gap.end), (self.iso(last + 1000), f"{NEXT}T00:00:00Z"))
        self.assertEqual(gap.excluded_events, 2)
        self.assertEqual(result.partitions[0].complete_end, self.iso(last + 1000))
        self.assertEqual(self.certify(result)[0].status, "pass")

    def test_several_discontinuities_give_ordered_runs_and_gaps(self):
        result = self.ingest(MANY)
        self.assertEqual(
            [p.partition_key for p in result.partitions],
            [f"dt={DAY}/seg=001", f"dt={DAY}/seg=002", f"dt={DAY}/seg=003"],
        )
        self.assertEqual([g.gap_key for g in result.gaps], ["gap=001", "gap=002"])
        self.assertEqual([g.excluded_events for g in result.gaps], [1, 2])
        self.assertEqual([p.materialization.row_count for p in result.partitions], [2, 2, 2])
        # every interval is strictly ordered and non-overlapping
        intervals = []
        for p in result.partitions:
            intervals.append((p.complete_start, p.complete_end))
        for g in result.gaps:
            intervals.append((g.start, g.end))
        intervals.sort(key=lambda item: Instant.parse(item[0]).epoch_ns)
        for (_, end), (start, _) in zip(intervals, intervals[1:]):
            self.assertEqual(end, start)
        self.assertEqual(intervals[0][0], f"{DAY}T00:00:00Z")
        self.assertEqual(intervals[-1][1], f"{NEXT}T00:00:00Z")
        self.assertEqual([r.status for r in self.certify(result)], ["pass"] * 3)

    def test_the_real_ob200_excerpt_ingests_as_a_clean_day_and_certifies(self):
        lines = EXCERPT.read_text(encoding="utf-8").splitlines()
        archive = make_archive(self.tmp, "2025-12-01", None, raw_lines=lines)
        result = self.ingest(archive=archive, day="2025-12-01")
        self.assertEqual(len(result.partitions), 1)
        self.assertEqual(result.partitions[0].materialization.row_count, 12)
        self.assertEqual(self.certify(result)[0].status, "pass")
        self.assertEqual(result.partitions[0].report.outcome, "eligible")

    def test_the_dataset_manifest_is_emitted_once_with_raw_lineage(self):
        result = self.ingest(CLEAN)
        document = json.loads(result.dataset_manifest_path.read_text())
        self.assertEqual(document["rel_root"], "canonical/l2/bybit/BTCUSDT/l2-book-event-v1")
        self.assertEqual(document["derived_from"][0]["layer"], "raw")
        self.assertEqual(document["transform"], "canonicalize-l2-book-events-v1")
        before = result.dataset_manifest_path.read_bytes()
        self.ingest(CLEAN, created_at="2030-01-01T00:00:00Z", day=DAY)  # idempotent re-ingest
        self.assertEqual(result.dataset_manifest_path.read_bytes(), before)


class RefusalTests(Base):
    def assertRefusal(self, code, messages=None, **kwargs):
        error = self.refusal(messages, **kwargs)
        self.assertEqual(error.code, code, str(error))
        self.assertFalse(self.root.exists() and any(self.root.rglob("*.parquet")), "output left behind")
        self.assertEqual(self.leftovers(), [])
        return error

    def test_an_event_outside_the_utc_day_is_refused(self):
        day_before = DAY_START - 2_000_000_000
        self.assertRefusal(
            "event_outside_day",
            [msg(1, 10, "snapshot", day_base=day_before), msg(2, 11, day_base=day_before)],
        )

    def test_the_day_is_closed_open_at_its_boundaries(self):
        # an event exactly at 00:00:00 belongs to the day, one exactly at the next 00:00:00 does not
        at_start = [msg(1, 10, "snapshot", ms=0, day_base=DAY_START), msg(2, 11, ms=1, day_base=DAY_START)]
        result = self.ingest(at_start)
        self.assertEqual(result.partitions[0].materialization.first_exchange_ts.epoch_ns, DAY_START)
        shutil.rmtree(self.root)
        end = DAY_START + 86_400 * 1_000_000_000
        self.assertRefusal(
            "event_outside_day",
            [msg(1, 10, "snapshot", ms=0, day_base=BASE), msg(2, 11, ms=0, day_base=end)],
        )

    def test_no_snapshot_or_no_event_is_refused(self):
        self.assertRefusal("no_anchored_run", [msg(1, 10), msg(2, 11)])
        self.assertRefusal("no_anchored_run", [])

    def test_an_ineligible_source_day_is_refused_with_its_codes(self):
        error = self.assertRefusal(
            "source_day_ineligible", [msg(1, 10, "snapshot"), msg(3, 11), msg(2, 12)]
        )
        self.assertIn("sequence_regression", error.context["violation_counts"])

    def test_a_gap_that_cannot_be_represented_is_refused(self):
        # the discontinuity and the next snapshot share the last anchored event's millisecond
        messages = [
            msg(1, 10, "snapshot", ms=1), msg(2, 11, ms=3), msg(3, 50, ms=3), msg(4, 1, "snapshot", ms=3),
        ]
        self.assertRefusal("discontinuity_not_representable", messages)

    def test_depth_and_name_refusals_are_typed_and_leave_no_output(self):
        self.assertRefusal(
            "source_depth_unsupported", archive=self.archive(CLEAN, name=f"{DAY}_BTCUSDT_ob500.data.zip")
        )
        self.assertRefusal(
            "archive_name_mismatch", archive=self.archive(CLEAN, name=f"{NEXT}_BTCUSDT_ob200.data.zip")
        )
        self.assertRefusal("archive_record_invalid", [msg(1, 10, "snapshot", cts=False)])

    def test_a_failure_in_the_second_pass_leaves_nothing_behind(self):
        real = ingest_module.materialize_l2_book_event_v1
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(*args, **kwargs)

        with mock.patch.object(ingest_module, "materialize_l2_book_event_v1", failing):
            with self.assertRaises(RuntimeError):
                self.ingest(ONE_GAP)
        self.assertFalse((self.root / f"dt={DAY}").exists())
        self.assertEqual(self.leftovers(), [])
        # and a later ingest works
        self.assertEqual(len(self.ingest(ONE_GAP).partitions), 2)


class _Tampered:
    """A profile whose first certification (the whole-day pass) is altered."""

    def __init__(self, real, alter, stored=None):
        self._real = real
        self._alter = alter
        self._stored = stored
        self._calls = 0

    def certify(self, identity, events):
        report = self._real.certify(identity, events)
        self._calls += 1
        if self._calls == 1:
            return self._alter(report)
        if self._stored is not None:
            return self._stored(report)
        return report

    def __getattr__(self, name):
        return getattr(self._real, name)


class InternalConsistencyTests(Base):
    """The second-pass guards against a certifier that disagrees with the stored runs."""

    def real(self):
        return BybitL2BookEventV1CertificationProfile("test-ref")

    def refuse(self, profile):
        with self.assertRaises(L2DayRefusal) as caught:
            self.ingest(ONE_GAP, profile=profile)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.root / f"dt={DAY}").exists())
        return caught.exception

    def test_a_certifier_count_that_disagrees_with_pass_two_is_a_plan_mismatch(self):
        def alter(report):
            wrong = dataclasses.replace(report.discontinuities[0], unanchored_event_count=99)
            return dataclasses.replace(report, discontinuities=(wrong,))

        error = self.refuse(_Tampered(self.real(), alter))
        self.assertEqual(error.code, "plan_mismatch")
        self.assertEqual(error.context, {"certified": 99, "counted": 2})

    def test_a_planned_run_that_does_not_exist_is_a_plan_mismatch(self):
        def alter(report):
            first = report.discontinuities[0]
            fake_resumed = dataclasses.replace(
                first, observed_sequence=2000, resumes_at_sequence=2001, unanchored_event_count=0
            )
            fake_open = dataclasses.replace(
                first, observed_sequence=3000, resumes_at_sequence=None, unanchored_event_count=0
            )
            return dataclasses.replace(report, discontinuities=(first, fake_resumed, fake_open))

        error = self.refuse(_Tampered(self.real(), alter))
        self.assertEqual(error.code, "plan_mismatch")
        self.assertEqual(error.context, {"runs": 2, "expected_runs": 3})

    def test_a_stored_run_that_is_not_clean_is_a_plan_mismatch(self):
        def degraded(report):
            return dataclasses.replace(report, outcome="degraded")

        error = self.refuse(_Tampered(self.real(), lambda r: r, stored=degraded))
        self.assertEqual(error.code, "plan_mismatch")
        self.assertEqual(error.context, {"outcome": "degraded"})


class IntervalPlanTests(unittest.TestCase):
    """The representability rule at the exact microsecond boundary."""

    @staticmethod
    def runs(*pairs):
        return [
            types.SimpleNamespace(
                materialization=types.SimpleNamespace(
                    first_exchange_ts=Instant(first), last_exchange_ts=Instant(last)
                )
            )
            for first, last in pairs
        ]

    @staticmethod
    def cut(high):
        return ingest_module._Cut(5, high, False, 1, 2, 1)

    def test_a_gap_of_exactly_zero_length_is_refused_and_one_microsecond_is_not(self):
        start = DAY_START
        end = start + 86_400 * 1_000_000_000
        # last event at t; ceil_us(t) = t + 1 us; the next run starts at t + 1 us -> empty gap
        t = start + 5_000_000_000
        empty = self.runs((start + 1_000, t), (t + 1_000, t + 9_000))
        with self.assertRaises(L2DayRefusal) as caught:
            ingest_module._plan_intervals(empty, [self.cut(10)], start, end)
        self.assertEqual(caught.exception.code, "discontinuity_not_representable")
        usable = self.runs((start + 1_000, t), (t + 2_000, t + 9_000))
        complete, gaps = ingest_module._plan_intervals(usable, [self.cut(10)], start, end)
        self.assertEqual(gaps, [(t + 1_000, t + 2_000, 0)])
        self.assertEqual(complete, [(start, t + 1_000), (t + 2_000, end)])


class ReacquisitionAndDeterminismTests(Base):
    def test_an_identical_reacquisition_is_a_no_op(self):
        first = self.ingest(ONE_GAP)
        before = {p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}
        second = self.ingest(ONE_GAP)
        self.assertTrue(second.already_published)
        self.assertEqual(
            [p.partition_key for p in second.partitions], [p.partition_key for p in first.partitions]
        )
        after = {p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.leftovers(), [])

    def test_a_changed_archive_for_a_published_day_is_refused(self):
        self.ingest(ONE_GAP)
        before = {p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}
        changed = [dict(m) for m in ONE_GAP]
        changed[1] = msg(2, 11)
        changed[1]["data"]["b"] = [["100.0", "999"]]
        error = self.refusal(archive=self.archive(changed, name=archive_file_name(DAY)))
        self.assertEqual(error.code, "day_already_published_differently")
        after = {p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.leftovers(), [])

    def test_two_roots_give_byte_identical_artifacts_and_manifests(self):
        one = self.ingest(ONE_GAP, root=self.tmp / "a")
        two = self.ingest(ONE_GAP, root=self.tmp / "b")

        def blobs(result):
            files = []
            for p in result.partitions:
                files += [p.artifact_path, p.partition_manifest_path, p.coverage_manifest_path]
            files += [g.coverage_manifest_path for g in result.gaps] + [result.dataset_manifest_path]
            return [f.read_bytes() for f in files]

        self.assertEqual(blobs(one), blobs(two))

    def test_the_archive_is_read_exactly_twice_and_never_materialized(self):
        opened = []
        real = ingest_module.iter_bybit_l2_archive_events

        def counting(path, day):
            opened.append(path)
            return real(path, day)

        with mock.patch.object(ingest_module, "iter_bybit_l2_archive_events", counting):
            self.ingest(ONE_GAP)
        self.assertEqual(len(opened), 2)


if __name__ == "__main__":
    unittest.main()
