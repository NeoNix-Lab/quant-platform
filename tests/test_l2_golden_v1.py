#!/usr/bin/env python3
"""Hermetic tests for the L2 v1 golden harness and the E2-E4 measurement (A14
closeout evidence)."""

from __future__ import annotations

import copy
import dataclasses
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import l2_golden_e2e  # noqa: E402
import test_bybit_l2_day_ingest_v1 as fixtures  # noqa: E402
from quant_platform.application import l2_golden as golden_module  # noqa: E402
from quant_platform.application.bybit_l2_day_evidence import measure_bybit_l2_day  # noqa: E402
from quant_platform.application.bybit_l2_day_ingest import L2DayRefusal  # noqa: E402
from quant_platform.application import bybit_l2_day_evidence as evidence_module  # noqa: E402
from quant_platform.application.l2_golden import run_l2_golden  # noqa: E402
from quant_platform.source_adapters.bybit_l2_archive import (  # noqa: E402
    BybitL2ArchiveError,
    archive_file_name,
)

DAY = fixtures.DAY
msg = fixtures.msg


def seam_messages():
    """A real-shaped day: it starts late, and its tail runs past midnight and ends with a
    closing snapshot that repeats the previous delta's ``seq`` and ``u``."""

    end = fixtures.DAY_START + 86_400 * 1_000_000_000
    return [
        msg(1, 10, "snapshot", ms=1112, day_base=fixtures.DAY_START),
        msg(2, 11, ms=-50, day_base=end),
        msg(3, 12, ms=14, day_base=end),
        msg(4, 13, ms=1113, day_base=end),
        msg(4, 13, "snapshot", ms=1113, day_base=end),
    ]


def strip_timing(document):
    """The evidence minus its named wall-clock fields."""

    document = copy.deepcopy(document)
    document.pop("timing", None)
    document["measurement"].pop("timing", None)
    return document


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l2-golden-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Tripwire: nothing in this file may publish into a catalog (tests/integration_l2_golden_postgres.py owns that leg).
        leg = mock.patch.object(
            golden_module, "_catalog_leg",
            side_effect=AssertionError("a unit test reached the catalog leg: a DSN leaked in from the environment"),
        )
        leg.start()
        self.addCleanup(leg.stop)

    def archive(self, messages, day=DAY, **kwargs):
        return fixtures.make_archive(self.tmp, day, messages, **kwargs)

    def root(self, name="canonical/l2/bybit/BTCUSDT/l2-book-event-v1"):
        return self.tmp / name

    def golden(self, messages=None, *, archive=None, day=DAY, root=None, **kwargs):
        params = dict(
            day=day, dataset_root=root or self.root(), created_at="2026-10-17T10:00:00Z",
            closed_at="2026-10-17T10:00:01Z", producer="test-golden", code_ref="test-ref",
        )
        params.update(kwargs)
        return run_l2_golden(archive or self.archive(messages, day=day), **params)

    @staticmethod
    def claims(evidence):
        return {c["claim"]: c for c in evidence["claims"]}


class MeasurementTests(Base):
    def test_a_clean_day_is_eligible_with_one_snapshot_and_no_discontinuity(self):
        path = self.archive(fixtures.CLEAN)
        measured = measure_bybit_l2_day(path, DAY)
        self.assertEqual(measured["outcome"], "eligible")
        self.assertEqual(measured["events"], 5)
        self.assertEqual(measured["snapshots"], 1)
        self.assertEqual(measured["discontinuities"]["count"], 0)
        self.assertEqual(measured["deletes_of_absent"], 0)
        self.assertEqual(measured["violation_counts"], {})
        self.assertEqual(measured["archive"]["name"], archive_file_name(DAY))
        self.assertEqual(measured["archive"]["bytes"], path.stat().st_size)
        self.assertEqual(len(measured["archive"]["sha256"]), 64)
        self.assertEqual(measured["timing"].keys(), {"elapsed_seconds"})

    def test_source_events_counts_every_event_read_not_the_certifier_count(self):
        clean = measure_bybit_l2_day(self.archive(fixtures.CLEAN), DAY)
        self.assertEqual((clean["source_events"], clean["events"]), (5, 5))
        seam = measure_bybit_l2_day(self.archive(seam_messages()), DAY)
        self.assertEqual(seam["source_events"], 5)
        self.assertEqual(seam["events"], 4, "the certifier leaves out the rejected closing duplicate")
        self.assertEqual(seam["outcome"], "ineligible")
        self.assertEqual(seam["violation_counts"], {"duplicate_sequence": 1})
        self.assertEqual(seam["events_outside_day"], 3)

    def test_a_cut_day_reports_the_discontinuity_and_the_restart_signals(self):
        # run 1, a gap (u 12 -> 50), then a snapshot whose u goes back to 1 (a provider restart)
        measured = measure_bybit_l2_day(self.archive(fixtures.ONE_GAP), DAY)
        self.assertEqual(measured["outcome"], "degraded")
        self.assertEqual(measured["snapshots"], 2)
        self.assertEqual(measured["snapshots_with_u_1"], 1)
        self.assertEqual(measured["u_backwards_snapshots"], 1)
        self.assertEqual(measured["discontinuities"]["count"], 1)
        sample = measured["discontinuities"]["samples"][0]
        self.assertEqual((sample["expected_update_id"], sample["observed_update_id"]), ("13", "50"))
        self.assertEqual(sample["unanchored_event_count"], 2)
        self.assertEqual(measured["unanchored_events"], 2)
        self.assertEqual(measured["segments"], 3)

    def test_deletes_of_absent_levels_regressions_and_outside_events_are_counted(self):
        absent = msg(2, 11)
        absent["data"]["b"] = [["55.0", "0"]]  # delete of a level that was never there
        measured = measure_bybit_l2_day(self.archive([msg(1, 10, "snapshot"), absent]), DAY)
        self.assertEqual(measured["deletes_of_absent"], 1)
        self.assertEqual(measured["outcome"], "ineligible")
        regressed = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot"), msg(3, 11), msg(2, 12)]), DAY
        )
        self.assertIn("sequence_regression", regressed["violation_counts"])
        outside = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot", day_base=fixtures.DAY_START - 2_000_000_000)]), DAY
        )
        self.assertEqual(outside["events_outside_day"], 1)

    def test_a_snapshot_that_does_not_advance_u_is_counted_as_backwards(self):
        equal = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot"), msg(2, 11), msg(3, 11, "snapshot")]), DAY
        )
        self.assertEqual(equal["u_backwards_snapshots"], 1)
        advancing = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot"), msg(2, 11), msg(3, 12, "snapshot")]), DAY
        )
        self.assertEqual(advancing["u_backwards_snapshots"], 0)

    def test_the_day_is_half_open_for_events_outside_it(self):
        first = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot", ms=0, day_base=fixtures.DAY_START)]), DAY
        )
        self.assertEqual(first["events_outside_day"], 0)
        last_ms = 86_400_000 - 1
        inside = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot"), msg(2, 11, ms=last_ms, day_base=fixtures.DAY_START)]), DAY
        )
        self.assertEqual(inside["events_outside_day"], 0)
        at_end = measure_bybit_l2_day(
            self.archive([msg(1, 10, "snapshot"), msg(2, 11, ms=86_400_000, day_base=fixtures.DAY_START)]), DAY
        )
        self.assertEqual(at_end["events_outside_day"], 1)

    def test_it_reads_the_archive_once_and_writes_nothing(self):
        path = self.archive(fixtures.ONE_GAP)
        before = path.read_bytes()
        names_before = sorted(p.name for p in self.tmp.iterdir())
        opened = []
        real = evidence_module.iter_bybit_l2_archive_events

        def counting(archive, day):
            opened.append(archive)
            return real(archive, day)

        with mock.patch.object(evidence_module, "iter_bybit_l2_archive_events", counting):
            measure_bybit_l2_day(path, DAY)
        self.assertEqual(len(opened), 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), names_before)

    def test_archive_errors_propagate_typed(self):
        with self.assertRaises(BybitL2ArchiveError) as caught:
            measure_bybit_l2_day(self.archive(fixtures.CLEAN, name=f"{DAY}_BTCUSDT_ob500.data.zip"), DAY)
        self.assertEqual(caught.exception.code, "source_depth_unsupported")


class GoldenChainTests(Base):
    def test_a_real_shaped_day_with_a_post_midnight_tail_and_a_closing_duplicate_holds_every_claim(self):
        evidence = self.golden(seam_messages())
        self.assertTrue(evidence["ok"], evidence["claims"])
        claims = self.claims(evidence)
        self.assertEqual(claims["event_accounting"]["detail"], {"measured": 5, "stored": 2, "excluded": 3})
        self.assertTrue(claims["partitions_eligible"]["ok"])
        self.assertTrue(claims["reproducible_byte_identical"]["ok"])
        # the file-level measurement is still reported honestly: it is ineligible as a file
        self.assertEqual(evidence["measurement"]["outcome"], "ineligible")
        self.assertEqual(evidence["measurement"]["source_events"], 5)

    def test_the_chain_on_a_cut_day_holds_every_claim(self):
        evidence = self.golden(fixtures.ONE_GAP)
        self.assertTrue(evidence["ok"], evidence["claims"])
        claims = self.claims(evidence)
        self.assertEqual(
            set(claims),
            {"event_accounting", "partitions_eligible", "reproducible_byte_identical"},
        )
        self.assertEqual(claims["event_accounting"]["detail"], {"measured": 8, "stored": 6, "excluded": 2})
        # two runs x (artifact, partition manifest, coverage) + three gap documents + the dataset manifest
        self.assertEqual(claims["reproducible_byte_identical"]["detail"], 10)
        self.assertIsNone(evidence["catalog"])
        ingest = evidence["ingest"]
        self.assertEqual([p["partition_key"] for p in ingest["partitions"]],
                         [f"dt={DAY}/seg=001", f"dt={DAY}/seg=002"])
        self.assertEqual(len(ingest["gaps"]), 3)
        self.assertEqual(ingest["excluded_events"], 2)
        self.assertFalse(ingest["already_published"])
        self.assertEqual(evidence["measurement"]["outcome"], "degraded")
        # the reproduction root is removed once it has matched
        self.assertFalse(self.root().with_name(self.root().name + ".reproduce").exists())

    def test_the_evidence_is_deterministic_apart_from_its_timing_fields(self):
        one = self.golden(fixtures.ONE_GAP, root=self.tmp / "a" / "l2")
        two = self.golden(
            archive=self.tmp / archive_file_name(DAY), root=self.tmp / "b" / "l2",
        )
        self.assertEqual(strip_timing(one), strip_timing(two))
        self.assertEqual(one["timing"].keys(), {"elapsed_seconds"})
        # the document is plain JSON
        self.assertEqual(json.loads(json.dumps(one, sort_keys=True))["day"], DAY)

    def test_a_real_excerpt_runs_the_chain(self):
        lines = fixtures.EXCERPT.read_text(encoding="utf-8").splitlines()
        archive = fixtures.make_archive(self.tmp, "2025-12-01", None, raw_lines=lines)
        evidence = self.golden(archive=archive, day="2025-12-01")
        self.assertTrue(evidence["ok"], evidence["claims"])
        self.assertEqual(evidence["measurement"]["outcome"], "eligible")
        self.assertEqual(evidence["ingest"]["partitions"][0]["rows"], 12)
        self.assertEqual(evidence["measurement"]["snapshots_with_u_1"], 0)

    def test_a_non_reproducible_ingest_fails_its_claim_and_keeps_the_second_root(self):
        real = golden_module.ingest_bybit_l2_day
        calls = []

        def tampering(*args, **kwargs):
            result = real(*args, **kwargs)
            calls.append(1)
            if len(calls) == 2:  # the reproduction ingest
                path = result.partitions[0].partition_manifest_path
                path.write_bytes(path.read_bytes() + b" ")
            return result

        with mock.patch.object(golden_module, "ingest_bybit_l2_day", tampering):
            evidence = self.golden(fixtures.ONE_GAP)
        self.assertFalse(evidence["ok"])
        claim = self.claims(evidence)["reproducible_byte_identical"]
        self.assertFalse(claim["ok"])
        self.assertTrue(self.root().with_name(self.root().name + ".reproduce").exists())

    def test_reproduction_can_be_skipped(self):
        evidence = self.golden(fixtures.CLEAN, reproduce=False)
        self.assertTrue(evidence["ok"])
        self.assertNotIn("reproducible_byte_identical", self.claims(evidence))

    def test_a_replay_of_a_published_day_is_still_ok(self):
        self.golden(fixtures.ONE_GAP)
        again = self.golden(archive=self.tmp / archive_file_name(DAY))
        self.assertTrue(again["ok"])
        self.assertTrue(again["ingest"]["already_published"])


class ClaimSensitivityTests(Base):
    """Each claim must be able to fail: the ingest result is doctored under a live harness."""

    def doctored(self, mutate):
        real = golden_module.ingest_bybit_l2_day

        def wrapper(*args, **kwargs):
            return mutate(real(*args, **kwargs))

        with mock.patch.object(golden_module, "ingest_bybit_l2_day", wrapper):
            return self.golden(fixtures.ONE_GAP, reproduce=False)

    def test_event_accounting_fails_when_an_event_is_unaccounted_for(self):
        evidence = self.doctored(lambda result: dataclasses.replace(result, excluded_events=1))
        self.assertFalse(self.claims(evidence)["event_accounting"]["ok"])
        self.assertFalse(evidence["ok"])

    def test_partitions_eligible_fails_for_a_non_eligible_partition(self):
        def degrade(result):
            partition = result.partitions[0]
            report = dataclasses.replace(partition.report, outcome="degraded")
            return dataclasses.replace(
                result, partitions=(dataclasses.replace(partition, report=report),) + tuple(result.partitions[1:])
            )

        evidence = self.doctored(degrade)
        claim = self.claims(evidence)["partitions_eligible"]
        self.assertFalse(claim["ok"])
        self.assertEqual(claim["detail"], ["degraded", "eligible"])


class RefusalTests(Base):
    def test_an_ob500_archive_is_refused_with_its_typed_code(self):
        with self.assertRaises(BybitL2ArchiveError) as caught:
            self.golden(archive=self.archive(fixtures.CLEAN, name=f"{DAY}_BTCUSDT_ob500.data.zip"))
        self.assertEqual(caught.exception.code, "source_depth_unsupported")

    def test_a_conflicting_duplicate_is_refused_with_its_typed_code(self):
        conflicting = msg(3, 12)
        conflicting["data"]["b"] = [["100.0", "999"]]  # same seq 3, different payload
        messages = [msg(1, 10, "snapshot"), msg(2, 11), msg(3, 12), conflicting]
        with self.assertRaises(L2DayRefusal) as caught:
            self.golden(messages)
        self.assertEqual(caught.exception.code, "source_day_ineligible")
        self.assertIn("duplicate_sequence", caught.exception.context["violation_counts"])
        self.assertFalse(any(self.root().rglob("*.parquet")) if self.root().exists() else False)


class ToolTests(Base):
    EXPORTED_DSN = "host=catalog.invalid dbname=must_not_be_used"

    def run_tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        # The tool defaults --dsn to CATALOG_DSN: the shell that runs the suite must never choose the catalog.
        with mock.patch.dict(os.environ):
            os.environ.pop("CATALOG_DSN", None)
            code = l2_golden_e2e.run(list(argv), out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def base_args(self, archive):
        return ["--archive", str(archive), "--day", DAY, "--dataset-root", str(self.root())]

    def test_success_prints_the_evidence_json_and_exits_zero(self):
        code, out, err = self.run_tool(*self.base_args(self.archive(fixtures.ONE_GAP)))
        self.assertEqual(code, 0, err)
        document = json.loads(out)
        self.assertTrue(document["ok"])
        self.assertEqual(len(document["ingest"]["partitions"]), 2)

    def test_a_refused_day_exits_two_with_a_typed_json_refusal(self):
        code, out, err = self.run_tool(
            *self.base_args(self.archive(fixtures.CLEAN, name=f"{DAY}_BTCUSDT_ob500.data.zip"))
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["refusal"]["code"], "source_depth_unsupported")
        self.assertIn("refused", err)
        code, out, _ = self.run_tool(*self.base_args(self.archive([msg(1, 10)])))
        self.assertEqual((code, json.loads(out)["refusal"]["code"]), (2, "no_anchored_run"))

    def test_a_failed_claim_exits_one_and_names_the_claim(self):
        failing = {"ok": False, "claims": [{"claim": "read_event_count", "ok": False, "detail": None}]}
        with mock.patch.object(l2_golden_e2e, "run_l2_golden", lambda *a, **k: failing):
            code, out, err = self.run_tool(*self.base_args(self.archive(fixtures.CLEAN)))
        self.assertEqual(code, 1)
        self.assertIn("read_event_count", err)
        self.assertFalse(json.loads(out)["ok"])

    def test_no_reproduce_and_dsn_flags_are_forwarded(self):
        seen = {}

        def fake(*args, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "claims": []}

        with mock.patch.object(l2_golden_e2e, "run_l2_golden", fake):
            self.run_tool(*self.base_args(self.archive(fixtures.CLEAN)), "--no-reproduce",
                          "--dsn", "host=x", "--storage-root-id", "cold")
        self.assertFalse(seen["reproduce"])
        self.assertEqual((seen["dsn"], seen["storage_root_id"]), ("host=x", "cold"))
        with mock.patch.object(l2_golden_e2e, "run_l2_golden", fake):
            self.run_tool(*self.base_args(self.archive(fixtures.CLEAN)))
        self.assertTrue(seen["reproduce"])

    def test_the_tool_itself_still_defaults_the_dsn_to_catalog_dsn(self):
        with mock.patch.dict(os.environ, {"CATALOG_DSN": self.EXPORTED_DSN}):
            args = l2_golden_e2e.build_parser().parse_args(self.base_args(self.tmp / "x.zip"))
        self.assertEqual(args.dsn, self.EXPORTED_DSN)

    def test_an_exported_catalog_dsn_is_never_forwarded_by_the_tool_tests(self):
        seen = {}

        def fake(*args, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "claims": []}

        with mock.patch.dict(os.environ, {"CATALOG_DSN": self.EXPORTED_DSN}),                 mock.patch.object(l2_golden_e2e, "run_l2_golden", fake):
            self.run_tool(*self.base_args(self.archive(fixtures.CLEAN)))
        self.assertIsNone(seen["dsn"])

    def test_the_real_success_path_with_an_exported_catalog_dsn_never_reaches_the_catalog_leg(self):
        with mock.patch.dict(os.environ, {"CATALOG_DSN": self.EXPORTED_DSN}):
            code, out, err = self.run_tool(*self.base_args(self.archive(fixtures.ONE_GAP)))
            self.assertEqual(os.environ["CATALOG_DSN"], self.EXPORTED_DSN, "the test must restore the caller's environment")
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out)["catalog"])
        golden_module._catalog_leg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
