"""DG-O O03: the evidence of a continuous run against the archive (read-only; the real catalog is in the PostgreSQL gate)."""

from __future__ import annotations

import gzip
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.application.live_window_evidence import (  # noqa: E402
    DeclaredGap,
    SliceWindow,
    archive_days_missing,
    archive_days_needed,
    build_window_evidence,
    chain_segments,
    load_archive_days,
    summarize_signals,
)
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.models import TradeRecord  # noqa: E402

INSTRUMENT = "ETHUSDT"
MS = 1_000_000
T0 = Instant.parse("2026-10-21T10:00:00Z").epoch_ns // MS


def at(ms: int) -> Instant:
    return Instant(ms * MS)


def trade(ms: int, trade_id: str, price: str = "100.10", size: str = "0.5", side: str = "buy") -> TradeRecord:
    return TradeRecord("bybit", INSTRUMENT, at(ms), price, size, side, None, trade_id, None)


def window(start: int, end: int, rows: int = 1, state: str = "valid", revision: int = 1) -> SliceWindow:
    return SliceWindow(f"dt=2026-10-21/seg={start}", revision, state, at(start), at(end), rows)


class Reader:
    """A scripted strict read: the trades whose time is in the window, from what the gateway would hold."""

    def __init__(self, held: list[TradeRecord]) -> None:
        self.held = held
        self.calls: list[tuple[int, int]] = []

    def __call__(self, start: Instant, end: Instant):
        self.calls.append((start.epoch_ns // MS, end.epoch_ns // MS))
        return [r for r in self.held if start.epoch_ns <= r.exchange_ts.epoch_ns < end.epoch_ns]


def evidence(archive, slices, held, **kwargs):
    reader = Reader(held)
    return build_window_evidence(instrument=INSTRUMENT, archive=archive, slices=slices, read_segment=reader, **kwargs), reader


class ComparisonTests(unittest.TestCase):
    def test_a_run_whose_every_archive_trade_is_read_back_passes_with_a_full_ratio(self) -> None:
        trades = [trade(T0 + i, f"t{i}") for i in range(10)]
        report, _ = evidence(trades, [window(T0, T0 + 5, 5), window(T0 + 5, T0 + 10, 5)], trades)
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual((10, 10, 10, 1.0), (report["archive_trades_in_window"], report["read_through_gateway"],
                                            report["captured_of_archive"], report["captured_ratio"]))
        self.assertEqual((0, 0, 0, 0), (report["missing_inside_claimed_windows"], report["missing_inside_declared_gaps"],
                                       report["missing_inside_undeclared_holes"], report["read_not_in_archive"]))
        self.assertEqual([], report["holes"])
        self.assertEqual(1.0, report["claimed_share"])
        self.assertEqual(2, report["slices"]["valid"])

    HOLE_SLICES = (window(T0, T0 + 5, 5), window(T0 + 7, T0 + 10, 3))

    def hole_case(self, **kwargs):
        archive = [trade(T0 + i, f"t{i}") for i in range(10)]
        held = [r for r in archive if r.trade_id not in {"t5", "t6"}]
        return evidence(archive, list(self.HOLE_SLICES), held, **kwargs)

    def test_an_archive_trade_in_a_hole_with_a_recorded_gap_is_reported_with_the_hole_and_is_not_a_stop(self) -> None:
        report, reader = self.hole_case(declared_gaps=[DeclaredGap(at(T0 + 4), at(T0 + 7), "gap-1")])
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual((2, 0, 0), (report["missing_inside_declared_gaps"], report["missing_inside_undeclared_holes"],
                                    report["missing_inside_claimed_windows"]))
        (hole,) = report["holes"]
        self.assertEqual((2, ["t5", "t6"]), (hole["archive_trades_inside"], hole["examples"]))
        self.assertEqual((2, 0), (hole["declared_trades"], hole["undeclared_trades"]))
        self.assertTrue(hole["declared_by"][0].startswith("gap_record:gap-1:"))
        self.assertEqual(2, report["slices"]["chain_segments"])
        self.assertAlmostEqual(0.8, report["captured_ratio"])  # the shortfall is stated, never rounded up
        self.assertAlmostEqual(8 / 10, report["claimed_share"])
        self.assertEqual(2, len(reader.calls))  # one strict read per exact chain

    def test_the_same_hole_with_no_recorded_gap_and_no_signal_is_an_undeclared_loss(self) -> None:
        for kwargs in ({}, {"signals_text": "signal kind=HEALTH_SNAPSHOT capability=c signal_id=s health=HEALTHY,phase=slice"}):
            report, _ = self.hole_case(**kwargs)
            self.assertFalse(report["passed"], kwargs)
            self.assertEqual((0, 2), (report["missing_inside_declared_gaps"], report["missing_inside_undeclared_holes"]))
            self.assertTrue(any("outside every recorded gap span" in c for c in report["stop_conditions"]))
            self.assertEqual([], report["holes"][0]["declared_by"])

    def test_a_signalled_unclaimed_span_over_the_hole_declares_it(self) -> None:
        span = f"unclaimed_start={at(T0 + 5).isoformat()},unclaimed_end={at(T0 + 7).isoformat()}"
        log = f"signal kind=HEALTH_SNAPSHOT capability=c signal_id=s health=DEGRADED,phase=slice,{span}"
        report, _ = self.hole_case(signals_text=log)
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertTrue(report["holes"][0]["declared_by"][0].startswith("unclaimed_span_signal:"))
        smaller = f"unclaimed_start={at(T0 + 5).isoformat()},unclaimed_end={at(T0 + 6).isoformat()}"  # does not cover the hole
        report, _ = self.hole_case(signals_text=log.replace(span, smaller))
        self.assertFalse(report["passed"])

    def test_a_recorded_gap_excuses_only_the_trades_inside_its_span_not_the_whole_hole(self) -> None:
        # the hole is [T0+5, T0+7) and the archive trades t5 (T0+5) and t6 (T0+6) are missing
        for span, declared, undeclared in (((4, 6), ["t5"], ["t6"]), ((6, 7), ["t6"], ["t5"]), ((4, 5), [], ["t5", "t6"]),
                                           ((4, 7), ["t5", "t6"], [])):
            report, _ = self.hole_case(declared_gaps=[DeclaredGap(at(T0 + span[0]), at(T0 + span[1]), "short")])
            (hole,) = report["holes"]
            self.assertEqual((len(declared), len(undeclared)), (hole["declared_trades"], hole["undeclared_trades"]), span)
            self.assertEqual(undeclared, hole["undeclared_examples"], span)
            self.assertEqual(not undeclared, report["passed"], (span, report["stop_conditions"]))
            if undeclared:
                self.assertEqual(len(undeclared), report["missing_inside_undeclared_holes"])
                self.assertTrue(any("outside every recorded gap span" in c for c in report["stop_conditions"]))

    def test_the_end_of_a_gap_span_is_exclusive_and_two_spans_together_cover_what_each_alone_does_not(self) -> None:
        report, _ = self.hole_case(declared_gaps=[DeclaredGap(at(T0 + 4), at(T0 + 6), "a")])  # ends AT t6
        self.assertEqual(["t6"], report["holes"][0]["undeclared_examples"])
        report, _ = self.hole_case(declared_gaps=[DeclaredGap(at(T0 + 4), at(T0 + 6), "a"), DeclaredGap(at(T0 + 4), at(T0 + 7), "b")])
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual(2, len(report["holes"][0]["declared_by"]))

    def test_a_signalled_span_elsewhere_is_not_attributed_to_the_hole(self) -> None:
        span = f"unclaimed_start={at(T0 + 100).isoformat()},unclaimed_end={at(T0 + 200).isoformat()}"
        report, _ = self.hole_case(signals_text=f"signal kind=HEALTH_SNAPSHOT capability=c signal_id=s health=DEGRADED,{span}")
        self.assertEqual([], report["holes"][0]["declared_by"])
        self.assertFalse(report["passed"])

    def test_a_signalled_span_excuses_only_the_trades_inside_it(self) -> None:
        span = f"unclaimed_start={at(T0 + 5).isoformat()},unclaimed_end={at(T0 + 6).isoformat()}"
        report, _ = self.hole_case(signals_text=f"signal kind=HEALTH_SNAPSHOT capability=c signal_id=s health=DEGRADED,phase=slice,{span}")
        self.assertEqual((1, ["t6"]), (report["holes"][0]["declared_trades"], report["holes"][0]["undeclared_examples"]))
        self.assertFalse(report["passed"])

    def test_a_gap_record_of_another_interval_does_not_declare_the_hole(self) -> None:
        for start in (T0 - 100, T0 + 8):  # before the segment that precedes the hole; after the hole
            report, _ = self.hole_case(declared_gaps=[DeclaredGap(at(start), at(start + 200), "elsewhere")])
            self.assertFalse(report["passed"], start)

    def test_a_hole_without_archive_trades_needs_no_declaration_but_is_listed(self) -> None:
        archive = [trade(T0, "a"), trade(T0 + 8, "b")]
        report, _ = evidence(archive, list(self.HOLE_SLICES), archive)
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual(([], 0), (report["holes"][0]["declared_by"], report["holes"][0]["archive_trades_inside"]))

    def test_gap_manifests_that_cannot_be_read_are_a_stop_condition(self) -> None:
        report, _ = self.hole_case(declared_gaps=[DeclaredGap(at(T0 + 4), at(T0 + 7), "gap-1")], unparseable_gap_manifests=1)
        self.assertFalse(report["passed"])

    def test_the_requested_window_is_kept_the_head_and_the_tail_are_compared(self) -> None:
        archive = [trade(T0 - 3, "head"), trade(T0, "a"), trade(T0 + 7, "tail")]
        report, _ = evidence(archive, [window(T0, T0 + 5, 1)], [trade(T0, "a")], window=(at(T0 - 5), at(T0 + 10)))
        self.assertFalse(report["passed"])
        self.assertEqual(3, report["archive_trades_in_window"])
        self.assertEqual(2, report["missing_outside_claimed_edges"])
        self.assertEqual(["head", "tail"], report["examples_missing_outside_claimed_edges"])
        self.assertAlmostEqual(1 / 3, report["captured_ratio"])
        self.assertEqual((15.0 / 1000, True), (report["window"]["seconds"], report["window"]["requested"]))
        self.assertAlmostEqual(5 / 15, report["claimed_share"])

    def test_a_requested_window_with_nothing_but_claimed_time_passes(self) -> None:
        archive = [trade(T0, "a")]
        report, _ = evidence(archive, [window(T0, T0 + 5, 1)], archive, window=(at(T0), at(T0 + 5)))
        self.assertTrue(report["passed"], report["stop_conditions"])

    def test_a_slice_that_crosses_a_bound_of_the_requested_window_is_refused_not_clipped(self) -> None:
        for bounds in ((T0 + 2, T0 + 10), (T0, T0 + 3)):
            with self.assertRaises(ValueError):
                evidence([trade(T0, "a")], [window(T0, T0 + 5, 1)], [trade(T0, "a")], window=(at(bounds[0]), at(bounds[1])))

    def test_no_valid_slice_in_a_requested_window_still_counts_its_archive_trades(self) -> None:
        report, _ = evidence([trade(T0, "a"), trade(T0 + 1, "b")], [], [], window=(at(T0), at(T0 + 5)))
        self.assertFalse(report["passed"])
        self.assertEqual(2, report["archive_trades_in_window"])

    def test_the_archive_days_are_those_of_the_whole_requested_window_not_of_the_surviving_slices(self) -> None:
        midnight = Instant.parse("2026-10-22T00:00:00Z").epoch_ns // MS
        slices = [window(midnight - 20, midnight - 10)]
        asked = (at(midnight - 1000), at(midnight + 1000))
        self.assertEqual((["2026-10-21", "2026-10-22"], ["2026-10-22"]), archive_days_missing(slices, asked, ["2026-10-21"]))
        self.assertEqual((["2026-10-21"], []), archive_days_missing(slices, None, ["2026-10-21"]))
        self.assertEqual(([], []), archive_days_missing([], None, []))

    def test_an_archive_trade_inside_a_claimed_window_that_is_not_read_back_is_a_stop_condition(self) -> None:
        archive = [trade(T0 + i, f"t{i}") for i in range(10)]
        held = [r for r in archive if r.trade_id != "t3"]
        report, _ = evidence(archive, [window(T0, T0 + 10, 9)], held)
        self.assertFalse(report["passed"])
        self.assertEqual(1, report["missing_inside_claimed_windows"])
        self.assertEqual(["t3"], report["examples_missing_inside_claimed_windows"])
        self.assertTrue(any("undeclared loss" in condition for condition in report["stop_conditions"]))

    def test_a_trade_that_differs_from_the_archive_is_a_stop_but_a_trailing_zero_is_not(self) -> None:
        archive = [trade(T0, "a", price="100.10", size="0.5"), trade(T0 + 1, "b")]
        held = [trade(T0, "a", price="100.1", size="0.50"), trade(T0 + 1, "b")]
        report, _ = evidence(archive, [window(T0, T0 + 2, 2)], held)
        self.assertTrue(report["passed"], report["conflicts"])
        for changed, name in ((trade(T0 + 1, "b", price="100.11"), "price_value"), (trade(T0 + 1, "b", size="0.6"), "size_value"),
                              (trade(T0 + 1, "b", side="sell"), "aggressor_side")):
            report, _ = evidence(archive, [window(T0, T0 + 2, 2)], [trade(T0, "a"), changed])
            self.assertFalse(report["passed"], name)
            self.assertEqual([name], report["conflicts"]["b"])

    def test_an_archive_trade_at_the_start_of_the_next_chain_is_inside_a_claimed_window_not_in_the_hole(self) -> None:
        archive = [trade(T0 + 3, "last-of-first"), trade(T0 + 7, "first-of-second")]
        report, _ = evidence(archive, [window(T0, T0 + 5, 1), window(T0 + 7, T0 + 10, 1)], [trade(T0 + 3, "last-of-first")])
        self.assertFalse(report["passed"])
        self.assertEqual(["first-of-second"], report["examples_missing_inside_claimed_windows"])
        self.assertEqual(0, report["holes"][0]["archive_trades_inside"])

    def test_an_archive_trade_at_the_end_of_the_last_window_is_outside_it(self) -> None:
        archive = [trade(T0, "a"), trade(T0 + 5, "at-the-end")]
        report, _ = evidence(archive, [window(T0, T0 + 5, 1)], [trade(T0, "a")])
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual(1, report["archive_trades_in_window"])

    def test_a_read_that_shifts_the_millisecond_differs_from_the_archive(self) -> None:
        report, _ = evidence([trade(T0, "a")], [window(T0, T0 + 5, 1)], [trade(T0 + 1, "a")])
        self.assertFalse(report["passed"])
        self.assertEqual(["exchange_ts_ms"], report["conflicts"]["a"])

    def test_a_read_out_of_exchange_time_order_is_a_stop_condition(self) -> None:
        archive = [trade(T0, "a"), trade(T0 + 1, "b")]
        report, _ = evidence(archive, [window(T0, T0 + 5, 2)], list(reversed(archive)))
        self.assertFalse(report["passed"])
        self.assertTrue(any("out of (exchange_ts, trade_id) order" in c for c in report["stop_conditions"]))

    def test_trades_of_one_millisecond_must_come_back_in_trade_id_order(self) -> None:
        a, b = trade(T0, "a"), trade(T0, "b")
        report, _ = evidence([a, b], [window(T0, T0 + 5, 2)], [a, b])
        self.assertTrue(report["passed"], report["stop_conditions"])
        report, _ = evidence([a, b], [window(T0, T0 + 5, 2)], [b, a])  # the canonical tie-break is the trade id
        self.assertFalse(report["passed"])
        self.assertTrue(any("out of (exchange_ts, trade_id) order" in c for c in report["stop_conditions"]))

    def test_valid_slices_that_overlap_are_a_stop_condition_and_are_not_chained(self) -> None:
        archive = [trade(T0, "a")]
        report, _ = evidence(archive, [window(T0, T0 + 10, 1), window(T0 + 5, T0 + 15, 0)], archive)
        self.assertFalse(report["passed"])
        self.assertTrue(any("overlap" in c for c in report["stop_conditions"]))
        segments = chain_segments([window(T0, T0 + 10), window(T0 + 5, T0 + 15)])
        self.assertEqual(2, len(segments))

    def test_a_trade_read_twice_is_a_stop_condition(self) -> None:
        archive = [trade(T0, "a")]
        report, _ = evidence(archive, [window(T0, T0 + 1, 1)], [trade(T0, "a"), trade(T0, "a")])
        self.assertFalse(report["passed"])
        self.assertEqual(["a"], report["duplicated_reads"])

    def test_a_trade_read_back_that_the_archive_does_not_hold_is_reported_not_failed(self) -> None:
        report, _ = evidence([trade(T0, "a")], [window(T0, T0 + 2, 2)], [trade(T0, "a"), trade(T0 + 1, "extra")])
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual((1, ["extra"]), (report["read_not_in_archive"], report["examples_read_not_in_archive"]))
        self.assertEqual(1.0, report["captured_ratio"])  # what the archive holds and was read back, not what was read

    def test_a_refused_strict_read_is_a_stop_condition_not_a_crash(self) -> None:
        def refuse(start, end):
            raise RuntimeError("NoCoverage")

        report = build_window_evidence(instrument=INSTRUMENT, archive=[], slices=[window(T0, T0 + 1, 1)], read_segment=refuse)
        self.assertFalse(report["passed"])  # nothing is missing from the archive: only the refusal can fail it
        self.assertEqual(0, report["missing_inside_claimed_windows"])
        self.assertEqual(1, len(report["strict_reads_refused"]))
        self.assertIn("NoCoverage", report["strict_reads_refused"][0])

    def test_the_archive_outside_the_claimed_window_is_not_counted(self) -> None:
        archive = [trade(T0 - 5, "before"), trade(T0, "a"), trade(T0 + 4, "after")]
        report, _ = evidence(archive, [window(T0, T0 + 2, 1)], [trade(T0, "a")])
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual(1, report["archive_trades_in_window"])

    def test_slices_that_are_not_valid_are_not_a_claim_and_are_counted(self) -> None:
        archive = [trade(T0, "a"), trade(T0 + 3, "b")]
        slices = [window(T0, T0 + 2, 1), window(T0 + 2, T0 + 4, 1, state="superseded"), window(T0 + 2, T0 + 4, 1, revision=2)]
        report, _ = evidence(archive, slices, archive)
        self.assertEqual(1 + 1, report["slices"]["valid"])
        self.assertEqual(1, report["slices"]["not_valid_in_catalog"])
        self.assertEqual(1, report["slices"]["revisions_above_one"])  # reported: a live slice is revision 1 unless a replay replaced it

    def test_a_requested_window_selects_the_slices_inside_it(self) -> None:
        archive = [trade(T0 + i, f"t{i}") for i in range(10)]
        slices = [window(T0, T0 + 5, 5), window(T0 + 5, T0 + 10, 5)]
        report, _ = evidence(archive, slices, archive, window=(at(T0 + 5), at(T0 + 10)))
        self.assertEqual(5, report["archive_trades_in_window"])
        self.assertEqual(1, report["slices"]["valid"])

    def test_no_valid_slice_is_a_stop_condition(self) -> None:
        report, _ = evidence([trade(T0, "a")], [window(T0, T0 + 1, 1, state="invalid")], [])
        self.assertFalse(report["passed"])

    def test_a_long_chain_is_read_in_bounded_chunks_that_end_on_slice_boundaries(self) -> None:
        slices = [window(T0 + i * 10, T0 + (i + 1) * 10, 1) for i in range(30)]
        archive = [trade(T0 + i * 10, f"t{i}") for i in range(30)]
        report, reader = evidence(archive, slices, archive)
        self.assertTrue(report["passed"], report["stop_conditions"])
        self.assertEqual([(T0, T0 + 120), (T0 + 120, T0 + 240), (T0 + 240, T0 + 300)], reader.calls)

    def test_chain_segments_join_only_exact_chains(self) -> None:
        segments = chain_segments([window(T0 + 10, T0 + 20), window(T0, T0 + 10), window(T0 + 21, T0 + 30)])
        self.assertEqual([(T0, T0 + 20), (T0 + 21, T0 + 30)],
                         [(a.epoch_ns // MS, b.epoch_ns // MS) for a, b, _ in segments])


class SignalsTests(unittest.TestCase):
    LOG = "\n".join([
        "=== live-ingest server v1 ===",
        "signal kind=LIFECYCLE_TRANSITION capability=live-ingest-server-v1 signal_id=s1 previous_state=STARTING,resulting_state=RUNNING",
        "signal kind=HEALTH_SNAPSHOT capability=live-ingest-server-v1 signal_id=s2 buffer_capacity=20000,buffer_high_water=4,"
        "health=HEALTHY,ordering_violations=0,phase=slice,reconnects=0,slice_lag_ms=1200,trades=500",
        "signal kind=HEALTH_SNAPSHOT capability=live-ingest-server-v1 signal_id=s3 buffer_capacity=20000,buffer_high_water=9,"
        "health=HEALTHY,ordering_violations=0,phase=slice,reconnects=1,slice_lag_ms=3400,trades=420",
        "signal kind=HEALTH_SNAPSHOT capability=live-ingest-server-v1 signal_id=s4 buffer_capacity=20000,buffer_high_water=9,"
        "health=DEGRADED,interruption=ordering_violation,ordering_violations=2,phase=interruption,reconnects=1,"
        "reason=ordering_violation: a trade, below the window",
        "signal kind=HEALTH_SNAPSHOT capability=live-ingest-server-v1 signal_id=s4b buffer_capacity=20000,buffer_high_water=2,"
        "health=HEALTHY,ordering_violations=1,phase=slice,reconnects=0,slice_lag_ms=100,trades=10",
        "signal kind=FAILURE capability=live-ingest-server-v1 signal_id=s5 failure_code=SLICE_PUBLICATION_FAILED,message=x",
        "signal kind=LIFECYCLE_TRANSITION capability=live-ingest-server-v1 signal_id=s6 previous_state=RUNNING,resulting_state=FAILED,cycles=2",
        "LIVE_INGEST_SERVER: FAILED",
    ])

    def test_the_counters_the_session_reports_are_taken_from_the_log(self) -> None:
        summary = summarize_signals(self.LOG)
        self.assertEqual(2, summary["ordering_violations"])
        self.assertEqual(1, summary["reconnects"])
        self.assertEqual((9, 20000), (summary["buffer_high_water"], summary["buffer_capacity"]))
        self.assertEqual((3400, 1200), (summary["slice_lag_ms_max"], summary["slice_lag_ms_median"]))
        self.assertEqual({"ordering_violation": 1}, summary["interruptions"])
        self.assertEqual({"DEGRADED": 1, "HEALTHY": 3}, summary["health_snapshots"])
        self.assertEqual({"interruption": 1, "slice": 3}, summary["phases"])
        self.assertEqual(["SLICE_PUBLICATION_FAILED"], summary["failures"])
        self.assertEqual("FAILED", summary["final_lifecycle_state"])
        self.assertEqual({"FAILURE": 1, "HEALTH_SNAPSHOT": 4, "LIFECYCLE_TRANSITION": 2}, summary["signals"])

    def test_a_log_without_signals_reports_zeros_not_an_error(self) -> None:
        summary = summarize_signals("nothing\n")
        self.assertEqual((0, 0, 0, []), (summary["ordering_violations"], summary["reconnects"], summary["buffer_high_water"],
                                         summary["failures"]))

    def test_the_signals_are_part_of_the_report_only_when_a_log_is_given(self) -> None:
        archive = [trade(T0, "a")]
        report, _ = evidence(archive, [window(T0, T0 + 1, 1)], archive)
        self.assertNotIn("signals", report)
        report, _ = evidence(archive, [window(T0, T0 + 1, 1)], archive, signals_text=self.LOG)
        self.assertEqual(2, report["signals"]["ordering_violations"])


class ArchiveTests(unittest.TestCase):
    HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"

    def test_the_days_a_window_touches_each_need_a_file(self) -> None:
        self.assertEqual(["2026-10-21"], archive_days_needed(Instant.parse("2026-10-21T10:00:00Z"), Instant.parse("2026-10-21T11:00:00Z")))
        self.assertEqual(["2026-10-21"], archive_days_needed(Instant.parse("2026-10-21T00:00:00Z"), Instant.parse("2026-10-22T00:00:00Z")))
        self.assertEqual(["2026-10-21", "2026-10-22"],
                         archive_days_needed(Instant.parse("2026-10-21T23:30:00Z"), Instant.parse("2026-10-22T00:30:00Z")))

    def test_an_archive_file_is_read_by_its_name_and_a_foreign_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            seconds = Instant.parse("2026-10-21T10:00:00Z").epoch_ns // 1_000_000_000
            good = Path(holder) / f"{INSTRUMENT}2026-10-21.csv.gz"
            good.write_bytes(gzip.compress(f"{self.HEADER}\n{seconds}.25,{INSTRUMENT},Buy,2,2499,PlusTick,a-1,1,2,1\n".encode()))
            days = load_archive_days([good], instrument=INSTRUMENT)
            self.assertEqual(["a-1"], [r.trade_id for r in days["2026-10-21"]])
            for name in ("BTCUSDT2026-10-21.csv.gz", "ETHUSDT-2026-10-21.csv.gz", "ETHUSDT2026-10-21.csv"):
                other = Path(holder) / name
                other.write_bytes(good.read_bytes())
                with self.assertRaises(ValueError):
                    load_archive_days([other], instrument=INSTRUMENT)


class ToolTests(unittest.TestCase):
    def test_start_and_end_go_together_and_the_window_is_a_usage_error_otherwise(self) -> None:
        import io
        from contextlib import redirect_stderr

        import live_window_evidence as tool

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            tool.main(["--instrument", INSTRUMENT, "--archive", "x", "--start", "2026-10-21T10:00:00Z"])
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
