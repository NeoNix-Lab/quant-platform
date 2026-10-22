"""DG-O O01 (ADR-0075 sections 2 and 3): the slice planner, the partition key and the window checks of the live profiles.

Pure tests: no network, no catalog. The PostgreSQL proof that a multi-cycle run leaves every trade readable is
``tests/integration_live_slices_postgres.py``.
"""

from __future__ import annotations

from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.bybit_live as app  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    BybitRestartReconciliationCertificationProfile,
    LiveProviderProofReport,
    RealServerPublishProofReport,
)
from quant_platform.data.manifests import _PARTITION_KEY  # noqa: E402
from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    BybitLiveSourceError,
    BybitLiveTradeV1CertificationProfile,
    LiveSessionTracker,
    build_bybit_live_coverage_document,
    bybit_live_dataset_identity,
    canonicalize_bybit_live_message,
    check_live_slice_coverage,
)
from quant_platform.source_adapters.bybit_live_slices import (  # noqa: E402
    LiveSliceError,
    SLICE_MAX_TRADES_DEFAULT,
    SLICE_SECONDS_DEFAULT,
    parse_slice_partition_key,
    plan_slices,
    slice_partition_key,
    slice_token,
)

MS = 1_000_000
SECOND = 1_000_000_000
DAY = 86_400 * SECOND


def at(text: str) -> Instant:
    return Instant.parse(text)


def ts_ms(text: str) -> int:
    return at(text).epoch_ns // MS


def trade(ms: int, trade_id: str, instrument: str = "ETHUSDT") -> TradeRecord:
    return TradeRecord("bybit", instrument, Instant(ms * MS), "100.10", "0.5", "buy", None, trade_id, "1")


D0 = ts_ms("2026-10-21T14:30:15.123Z")


def check_plan_invariants(test: unittest.TestCase, records, plan, *, bridges: bool = False) -> None:
    """The properties every plan must have, whatever the stream. ``bridges``: a reconciliation may hold empty bridge slices."""

    slices = plan.slices
    taken = [r for s in slices for r in s.records]
    # every record is in exactly one slice, or below the floor; nothing is invented
    test.assertEqual(sorted((r.exchange_ts.epoch_ns, r.trade_id) for r in records),
                     sorted((r.exchange_ts.epoch_ns, r.trade_id) for r in (*taken, *plan.below_floor)))
    previous_end = None
    for s in slices:
        if not bridges:
            test.assertTrue(s.records, "an empty window is never published")
        test.assertLess(s.window_start.epoch_ns, s.window_end.epoch_ns)
        for r in s.records:  # half-open: start <= ts < end
            test.assertLessEqual(s.window_start.epoch_ns, r.exchange_ts.epoch_ns)
            test.assertLess(r.exchange_ts.epoch_ns, s.window_end.epoch_ns)
        keys = [(r.exchange_ts.epoch_ns, r.trade_id) for r in s.records]
        test.assertEqual(keys, sorted(keys))
        # a window never crosses a UTC midnight
        test.assertLessEqual(s.window_end.epoch_ns, (s.window_start.epoch_ns // DAY + 1) * DAY)
        if previous_end is not None:  # windows never overlap
            test.assertLessEqual(previous_end, s.window_start.epoch_ns)
        previous_end = s.window_end.epoch_ns
    # a millisecond is never split between two slices
    populated = [s for s in slices if s.records]
    for left, right in zip(populated, populated[1:]):
        test.assertLess(left.records[-1].exchange_ts.epoch_ns, right.records[0].exchange_ts.epoch_ns)
    if bridges:  # no hole: the windows of a reconciliation chain exactly, bridges included
        for left, right in zip(slices, slices[1:]):
            test.assertEqual(left.window_end, right.window_start)
        for s in slices:
            if not s.records:
                test.assertEqual(s.window_end.epoch_ns % DAY, 0, "a bridge ends at a UTC midnight")


class PlannerTests(unittest.TestCase):
    def test_one_acquisition_is_one_slice_that_ends_after_its_last_trade(self) -> None:
        records = [trade(D0, "a"), trade(D0 + 5, "b"), trade(D0 + 9, "c")]
        plan = plan_slices(records)
        self.assertEqual(1, len(plan.slices))
        only = plan.slices[0]
        self.assertEqual((D0 * MS, (D0 + 10) * MS), (only.window_start.epoch_ns, only.window_end.epoch_ns))
        self.assertEqual(3, len(only.records))
        self.assertEqual((), plan.below_floor)

    def test_the_input_order_does_not_matter(self) -> None:
        records = [trade(D0 + 9, "c"), trade(D0, "a"), trade(D0 + 5, "b"), trade(D0 + 5, "a")]
        self.assertEqual(plan_slices(records), plan_slices(sorted(records, key=lambda r: r.trade_id)))

    def test_a_tie_group_is_never_split_and_goes_to_the_next_slice(self) -> None:
        records = [trade(D0, "a")] + [trade(D0 + 1000, f"t{i}") for i in range(3)]
        plan = plan_slices(records, max_trades=2)
        self.assertEqual([1, 3], [len(s.records) for s in plan.slices])  # the three-trade millisecond exceeds the bound whole
        first, second = plan.slices
        self.assertEqual((D0 * MS, (D0 + 1000) * MS), (first.window_start.epoch_ns, first.window_end.epoch_ns))
        self.assertEqual(first.window_end, second.window_start)  # the boundary is the greatest ts received; equal trades stay
        self.assertEqual((D0 + 1001) * MS, second.window_end.epoch_ns)
        check_plan_invariants(self, records, plan)

    def test_max_trades_closes_a_slice_at_the_next_boundary(self) -> None:
        records = [trade(D0 + i, f"t{i}") for i in range(5)]
        plan = plan_slices(records, max_trades=2)
        self.assertEqual([2, 2, 1], [len(s.records) for s in plan.slices])
        check_plan_invariants(self, records, plan)

    def test_slice_seconds_closes_a_slice_when_the_next_trade_is_that_far_from_its_first(self) -> None:
        first, edge_in, edge_out = D0, D0 + 299_999, D0 + 300_000
        plan = plan_slices([trade(first, "a"), trade(edge_in, "b"), trade(edge_out, "c")], slice_seconds=300)
        self.assertEqual([2, 1], [len(s.records) for s in plan.slices])
        self.assertEqual(edge_out * MS, plan.slices[0].window_end.epoch_ns)
        self.assertEqual(edge_out * MS, plan.slices[1].window_start.epoch_ns)
        check_plan_invariants(self, [trade(first, "a"), trade(edge_in, "b"), trade(edge_out, "c")], plan)

    def test_defaults_are_the_accepted_bounds(self) -> None:
        self.assertEqual((300, 50_000), (SLICE_SECONDS_DEFAULT, SLICE_MAX_TRADES_DEFAULT))
        records = [trade(D0 + i * 1000, f"t{i}") for i in range(301)]  # 301 trades, one a second: 300 s span
        self.assertEqual([300, 1], [len(s.records) for s in plan_slices(records).slices])

    def test_a_window_never_crosses_a_utc_midnight(self) -> None:
        midnight = ts_ms("2026-10-22T00:00:00Z")
        records = [trade(midnight - 500, "a"), trade(midnight - 1, "b"), trade(midnight + 100, "c")]
        plan = plan_slices(records)
        self.assertEqual([2, 1], [len(s.records) for s in plan.slices])
        before, after = plan.slices
        self.assertEqual(midnight * MS, before.window_end.epoch_ns)  # forced to the midnight instant
        self.assertEqual(midnight * MS, after.window_start.epoch_ns)  # the next window starts there, before its first trade
        self.assertEqual("dt=2026-10-21/seg=235959.500", before.partition_key)
        self.assertEqual("dt=2026-10-22/seg=000000.000", after.partition_key)
        check_plan_invariants(self, records, plan)

    def test_a_trade_exactly_at_midnight_belongs_to_the_new_day(self) -> None:
        midnight = ts_ms("2026-10-22T00:00:00Z")
        plan = plan_slices([trade(midnight - 1, "a"), trade(midnight, "b")])
        self.assertEqual(["a", "b"], [s.records[0].trade_id for s in plan.slices])

    def test_a_day_without_trades_is_not_published_and_the_next_window_starts_on_its_own_day(self) -> None:
        late = ts_ms("2026-10-21T23:59:59.999Z")
        later = ts_ms("2026-10-23T00:00:01Z")  # a whole day without a trade in between
        plan = plan_slices([trade(late, "a"), trade(later, "b")])
        self.assertEqual(2, len(plan.slices))
        self.assertEqual(ts_ms("2026-10-22T00:00:00Z") * MS, plan.slices[0].window_end.epoch_ns)
        self.assertEqual(ts_ms("2026-10-23T00:00:00Z") * MS, plan.slices[1].window_start.epoch_ns)
        check_plan_invariants(self, [trade(late, "a"), trade(later, "b")], plan)

    def test_trades_before_the_floor_are_never_in_a_slice(self) -> None:
        floor = Instant((D0 + 10) * MS)
        records = [trade(D0, "old"), trade(D0 + 9, "older"), trade(D0 + 10, "edge"), trade(D0 + 11, "new")]
        plan = plan_slices(records, floor=floor)
        self.assertEqual({"old", "older"}, {r.trade_id for r in plan.below_floor})
        self.assertEqual(["edge", "new"], [r.trade_id for s in plan.slices for r in s.records])  # the floor itself is allowed
        self.assertEqual(floor, plan.slices[0].window_start)
        check_plan_invariants(self, records, plan)

    def test_a_floor_above_everything_leaves_no_slice(self) -> None:
        plan = plan_slices([trade(D0, "a")], floor=Instant((D0 + 1) * MS))
        self.assertEqual(((), 1), (plan.slices, len(plan.below_floor)))

    def test_start_at_floor_claims_the_proven_interval_before_the_first_trade(self) -> None:
        floor = Instant(D0 * MS)
        plan = plan_slices([trade(D0 + 500, "a")], floor=floor, start_at_floor=True)
        self.assertEqual(floor, plan.slices[0].window_start)
        self.assertEqual("dt=2026-10-21/seg=143015.123", plan.slices[0].partition_key)
        plain = plan_slices([trade(D0 + 500, "a")], floor=floor)
        self.assertEqual(Instant((D0 + 500) * MS), plain.slices[0].window_start)  # a live acquisition starts at its first trade

    def test_slice_seconds_is_measured_from_the_first_trade_not_from_the_window_start(self) -> None:
        floor = Instant(D0 * MS)
        # the window starts at the floor, the first trade is 100 s later and the second 250 s after that: 250 s < 300 s
        records = [trade(D0 + 100_000, "a"), trade(D0 + 350_000, "b")]
        plan = plan_slices(records, floor=floor, start_at_floor=True, slice_seconds=300)
        self.assertEqual([2], [len(s.records) for s in plan.slices])
        self.assertEqual([1, 1], [len(s.records) for s in plan_slices(records, floor=floor, start_at_floor=True, slice_seconds=200).slices])

    def test_a_reconciliation_across_a_midnight_bridges_the_span_up_to_it(self) -> None:
        midnight = ts_ms("2026-10-22T00:00:00Z")
        floor = Instant((midnight - 1) * MS)  # the last published slice ended one millisecond before the midnight
        records = [trade(midnight + 4, "a")]
        plan = plan_slices(records, floor=floor, start_at_floor=True)
        bridge, real = plan.slices
        self.assertEqual((), bridge.records)
        self.assertEqual((floor, Instant(midnight * MS)), (bridge.window_start, bridge.window_end))
        self.assertEqual("dt=2026-10-21/seg=235959.999", bridge.partition_key)
        self.assertEqual((Instant(midnight * MS), Instant((midnight + 5) * MS)), (real.window_start, real.window_end))
        check_plan_invariants(self, records, plan, bridges=True)

    def test_a_live_acquisition_across_a_midnight_does_not_bridge(self) -> None:
        floor = Instant(ts_ms("2026-10-21T23:00:00Z") * MS)
        plan = plan_slices([trade(ts_ms("2026-10-22T00:10:00Z"), "a")], floor=floor)
        self.assertEqual(1, len(plan.slices))  # it starts at its own first trade; nothing is claimed before it

    def test_a_reconciliation_that_stays_in_its_day_has_no_bridge(self) -> None:
        floor = Instant(ts_ms("2026-10-21T23:00:00Z") * MS)
        plan = plan_slices([trade(ts_ms("2026-10-21T23:10:00Z"), "a")], floor=floor, start_at_floor=True)
        self.assertEqual([floor], [s.window_start for s in plan.slices])
        self.assertEqual(1, len(plan.slices))

    def test_a_reconciliation_after_several_idle_days_bridges_each_of_them(self) -> None:
        floor = Instant(ts_ms("2026-10-21T22:00:00Z") * MS)
        records = [trade(ts_ms("2026-10-23T01:00:00Z"), "a")]
        plan = plan_slices(records, floor=floor, start_at_floor=True)
        self.assertEqual([0, 0, 1], [len(s.records) for s in plan.slices])
        check_plan_invariants(self, records, plan, bridges=True)

    def test_nothing_in_nothing_out(self) -> None:
        self.assertEqual(((), ()), (plan_slices([]).slices, plan_slices([]).below_floor))

    def test_refusals(self) -> None:
        with self.assertRaises(LiveSliceError):
            plan_slices([TradeRecord("bybit", "ETHUSDT", Instant(D0 * MS + 1), "1", "1", "buy", None, "a", "1")])
        with self.assertRaises(LiveSliceError):
            plan_slices([trade(D0, "a"), trade(D0, "a")])
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(LiveSliceError):
                plan_slices([trade(D0, "a")], max_trades=bad)
            with self.assertRaises(LiveSliceError):
                plan_slices([trade(D0, "a")], slice_seconds=bad)
        with self.assertRaises(LiveSliceError):
            plan_slices([trade(D0, "a")], start_at_floor=True)

    def test_random_streams_keep_every_invariant(self) -> None:
        rng = random.Random(75)
        for case in range(60):
            base = ts_ms("2026-10-21T23:58:00Z") + rng.randrange(0, 4 * 60_000)
            count = rng.randrange(1, 120)
            records, used = [], set()
            for i in range(count):
                millisecond = base + rng.choice((0, 0, 1, 1, 7, 900, 61_000, 299_999, 300_000, 400_000))
                trade_id = f"t{rng.randrange(0, 10_000)}"
                if (millisecond, trade_id) in used:
                    continue
                used.add((millisecond, trade_id))
                records.append(trade(millisecond, trade_id))
            floor = Instant(base * MS) if case % 3 == 0 else None
            plan = plan_slices(
                records, floor=floor, start_at_floor=floor is not None and case % 2 == 0,
                max_trades=rng.choice((1, 2, 5, 50_000)), slice_seconds=rng.choice((1, 60, 300)),
            )
            with self.subTest(case=case):
                check_plan_invariants(self, records, plan, bridges=floor is not None and case % 2 == 0)
                if floor is not None:
                    self.assertTrue(all(r.exchange_ts.epoch_ns < floor.epoch_ns for r in plan.below_floor))
                    self.assertTrue(all(r.exchange_ts.epoch_ns >= floor.epoch_ns for s in plan.slices for r in s.records))
                for left, right in zip(plan.slices, plan.slices[1:]):
                    # windows chain exactly unless a live acquisition crosses a midnight with no trade (a reconciliation bridges)
                    self.assertTrue(
                        left.window_end == right.window_start
                        or (left.window_end.epoch_ns % DAY == 0 and not (floor is not None and case % 2 == 0)),
                        (left.window_end, right.window_start),
                    )


class PartitionKeyTests(unittest.TestCase):
    def test_key_and_token(self) -> None:
        start = Instant(D0 * MS)
        self.assertEqual("dt=2026-10-21/seg=143015.123", slice_partition_key(start))
        self.assertEqual("20261021-143015.123", slice_token(start))
        self.assertEqual("dt=2026-10-21/seg=143015.000", slice_partition_key(Instant(ts_ms("2026-10-21T14:30:15Z") * MS)))
        self.assertEqual("dt=2026-10-21/seg=000000.000", slice_partition_key(at("2026-10-21T00:00:00Z")))

    def test_the_key_names_the_window_start_and_back(self) -> None:
        rng = random.Random(7)
        for _ in range(200):
            start = Instant(rng.randrange(0, 4_102_444_800_000) * MS)
            key = slice_partition_key(start)
            self.assertEqual(start, parse_slice_partition_key(key))
            self.assertIsNotNone(_PARTITION_KEY.fullmatch(key), "the key must stay inside the accepted partition grammar")

    def test_a_start_that_is_not_a_whole_millisecond_has_no_key(self) -> None:
        with self.assertRaises(LiveSliceError):
            slice_partition_key(Instant(D0 * MS + 1))

    def test_malformed_and_impossible_keys_are_refused(self) -> None:
        for bad in (
            "dt=2026-10-21", "dt=2026-10-21/seg=143015", "dt=2026-10-21/seg=14301.123", "dt=2026-10-21/seg=143015.12",
            "dt=2026-10-21/seg=143015.1234", "dt=2026-13-01/seg=000000.000", "dt=2026-10-21/seg=250000.000",
            "dt=2026-10-21/seg=146000.000", "dt=2026-10-21/seg=000000.000/x", "dt=2026-10-21/seg=abcdef.123", "", None, 5,
        ):
            with self.subTest(key=bad), self.assertRaises(LiveSliceError):
                parse_slice_partition_key(bad)


def window_document(key: str, start: str, end: str, *, revision: int = 1, intent=None, status: str = "complete") -> dict:
    intent_start, intent_end = intent or (start, end)
    return {
        "acquisition": {"intent_start": intent_start, "intent_end": intent_end},
        "assertions": [{"start": start, "end": end, "status": status,
                        "partitions": [{"partition_key": key, "revision": revision}], "evidence": []}],
    }


class WindowCheckTests(unittest.TestCase):
    KEY = "dt=2026-10-21/seg=143015.123"
    START, END = "2026-10-21T14:30:15.123Z", "2026-10-21T14:30:40.5Z"

    def good(self, **overrides) -> dict:
        arguments = dict(key=self.KEY, start=self.START, end=self.END)
        arguments.update(overrides)
        return window_document(**arguments)

    def test_a_window_that_matches_its_key_is_accepted(self) -> None:
        self.assertEqual(1, check_live_slice_coverage((self.good(),)))

    def test_the_legacy_day_key_is_accepted_unchanged(self) -> None:
        self.assertEqual(0, check_live_slice_coverage((window_document("dt=2026-10-21", self.START, self.END),)))

    def test_refusals(self) -> None:
        cases = {
            "superseding revision": self.good(revision=2),
            "start does not match the key": self.good(start="2026-10-21T14:30:15.124Z"),
            "assertion start differs from the key while the intent matches it": self.good(
                start="2026-10-21T14:30:15.124Z", intent=(self.START, self.END)),
            "intent start differs from the window": self.good(intent=("2026-10-21T14:30:15Z", self.END)),
            "intent end differs from the window": self.good(intent=(self.START, "2026-10-21T14:30:41Z")),
            "crosses a midnight": self.good(end="2026-10-22T00:00:00.001Z"),
            "intent is not the window": self.good(intent=("2026-10-21T14:30:15.123Z", "2026-10-21T14:31:00Z")),
            "unsupported key": window_document("dt=2026-10-21/seg=abc", self.START, self.END),
            "unsupported depth": window_document("dt=2026-10-21/seg=143015.123/x=1", self.START, self.END),
        }
        for name, document in cases.items():
            with self.subTest(name), self.assertRaises(BybitLiveSourceError):
                check_live_slice_coverage((document,))

    def test_a_later_revision_is_accepted_only_when_it_is_the_expected_one(self) -> None:
        self.assertEqual(1, check_live_slice_coverage((self.good(revision=2),), revision=2))
        for expected, named in ((1, 2), (2, 1), (2, 3)):
            with self.subTest(expected=expected, named=named), self.assertRaises(BybitLiveSourceError):
                check_live_slice_coverage((self.good(revision=named),), revision=expected)

    def test_the_end_of_the_day_is_allowed(self) -> None:
        document = window_document("dt=2026-10-21/seg=235959.500", "2026-10-21T23:59:59.5Z", "2026-10-22T00:00:00Z")
        self.assertEqual(1, check_live_slice_coverage((document,)))

    def test_a_window_may_not_start_before_what_is_published(self) -> None:
        start = at(self.START)
        self.assertEqual(1, check_live_slice_coverage((self.good(),), published_floor=start))  # equal: allowed
        with self.assertRaises(BybitLiveSourceError):
            check_live_slice_coverage((self.good(),), published_floor=Instant(start.epoch_ns + MS))

    def test_a_chained_window_starts_exactly_where_the_previous_one_ended(self) -> None:
        start = at(self.START)
        self.assertEqual(1, check_live_slice_coverage((self.good(),), chain_start=start))
        for off in (-MS, MS):
            with self.subTest(off=off), self.assertRaises(BybitLiveSourceError):
                check_live_slice_coverage((self.good(),), chain_start=Instant(start.epoch_ns + off))


def live_coverage(instrument: str, key: str, start: str, end: str) -> dict:
    row = {"T": ts_ms(start), "s": instrument, "S": "Buy", "v": "0.5", "p": "100.10", "i": "t-1", "seq": 7}
    batch = canonicalize_bybit_live_message(
        {"topic": f"publicTrade.{instrument}", "type": "snapshot", "ts": ts_ms(start) + 1, "data": [row]}, instrument=instrument,
    )
    tracker = LiveSessionTracker(instrument)
    tracker.connected(conn_id="c")
    tracker.subscribed(conn_id="c")
    tracker.observed_message(batch)
    return build_bybit_live_coverage_document(
        dataset_identity=bybit_live_dataset_identity(instrument), coverage_id="live-slice-x", intent_start=start, intent_end=end,
        assertion_id="live-slice-assertion-x", assertion_start=start, assertion_end=end, partition_key=key, revision=1,
        session_evidence=tracker.evidence(), created_at=end, producer="t", code_ref="t",
    )


class ProfileTests(unittest.TestCase):
    def test_the_live_profile_checks_the_window_of_a_real_coverage_document(self) -> None:
        key, start, end = "dt=2026-10-21/seg=143015.123", "2026-10-21T14:30:15.123Z", "2026-10-21T14:30:20Z"
        document = live_coverage("ETHUSDT", key, start, end)
        identity = bybit_live_dataset_identity("ETHUSDT")
        details = BybitLiveTradeV1CertificationProfile("t").validate_source(identity, (document,))
        self.assertEqual(1, details["slices"])
        for floor in (Instant(at(start).epoch_ns + MS),):
            with self.assertRaises(BybitLiveSourceError):
                BybitLiveTradeV1CertificationProfile("t", published_floor=floor).validate_source(identity, (document,))
        with self.assertRaises(BybitLiveSourceError):
            BybitLiveTradeV1CertificationProfile("t", chain_start=Instant(at(start).epoch_ns - MS)).validate_source(
                identity, (document,))

    def test_the_reconciliation_profile_runs_the_same_window_checks(self) -> None:
        key, start, end = "dt=2026-10-21/seg=143015.123", "2026-10-21T14:30:15.123Z", "2026-10-21T14:30:20Z"
        document = {
            "acquisition": {"basis": "reconciliation", "intent_start": start, "intent_end": end,
                            "source_semantics": "bybit-recent-public-trades-v5-v1",
                            "mapping": "bybit-recent-public-trades-v5-to-trade-v1"},
            "assertions": [{"start": start, "end": end, "status": "complete",
                            "partitions": [{"partition_key": key, "revision": 1}],
                            "evidence": [{"kind": "reconciliation", "detail": "d"}]}],
        }
        identity = bybit_live_dataset_identity("ETHUSDT")
        self.assertEqual(1, BybitRestartReconciliationCertificationProfile("t").validate_source(identity, (document,))["slices"])
        with self.assertRaises(BybitLiveSourceError):
            BybitRestartReconciliationCertificationProfile(
                "t", published_floor=Instant(at(start).epoch_ns + MS)).validate_source(identity, (document,))
        with self.assertRaises(BybitLiveSourceError):
            BybitRestartReconciliationCertificationProfile(
                "t", chain_start=Instant(at(start).epoch_ns + MS)).validate_source(identity, (document,))


class PublishWiringTests(unittest.TestCase):
    """What ``_publish_slices`` hands to the slice publication: the floor, the chain, the mode and the order of the slices."""

    @staticmethod
    def acquisition(records) -> LiveProviderProofReport:
        return LiveProviderProofReport(
            status="PASS", topic="publicTrade.ETHUSDT", messages=1, records=len(records), duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=tuple(records),
        )

    def publish(self, records, *, reconciling=False, floor=None, max_trades=2, outcomes=None, revision=1):
        calls: list[dict] = []

        def fake_one(**kwargs):
            calls.append(kwargs)
            if outcomes is not None and len(calls) in outcomes:
                return outcomes[len(calls)]
            planned = kwargs["planned"]
            sealed = SimpleNamespace(
                dataset_id="dataset", manifest_sha256="a" * 64,
                natural_identity=SimpleNamespace(partition_key=planned.partition_key, revision=kwargs["revision"]))
            run = SimpleNamespace(certification=SimpleNamespace(status="pass"), sealed_partition=sealed)
            return app._PublishedSlice(
                planned, kwargs["revision"], run, f"c-{len(calls)}", f"a-{len(calls)}", "complete", (), len(planned.records), True)

        with tempfile.TemporaryDirectory() as holder,                 mock.patch.object(app, "_published_floor", lambda dsn, identity: floor), mock.patch.object(app, "_next_slice_revision", lambda dsn, identity, key: revision),                 mock.patch.object(app, "_publish_one_slice", fake_one):
            report = app._publish_slices(
                records=tuple(records), acquisition=self.acquisition(records), reconciling=reconciling,
                live_session_evidence=None, reconcile_result=None, storage_root=holder, storage_root_id="hot", dsn=None,
                producer="p", code_ref="c", instrument="ETHUSDT", slice_seconds=300, slice_max_trades=max_trades)
        return report, calls

    def test_each_slice_is_chained_to_the_previous_and_the_report_is_the_last(self) -> None:
        floor = Instant((D0 - 100) * MS)
        report, calls = self.publish([trade(D0 + i, f"t{i}") for i in range(5)], floor=floor)
        self.assertEqual("PASS", report.status)
        self.assertEqual([None, calls[0]["planned"].window_end, calls[1]["planned"].window_end],
                         [c["chain_start"] for c in calls])
        self.assertEqual([floor] * 3, [c["floor"] for c in calls])
        self.assertEqual([False] * 3, [c["reconciling"] for c in calls])
        keys = tuple(c["planned"].partition_key for c in calls)
        self.assertEqual((3, keys), (report.slices, report.slice_keys))
        self.assertEqual(keys[-1], report.partition_key)
        self.assertEqual((keys[-1], 1), (report.durable_publication.partition_key, report.durable_publication.revision))
        self.assertEqual(("c-3", "a-3"), (report.coverage_id, report.coverage_assertion_id))
        self.assertEqual(5, report.datagateway_read_record_count)
        self.assertTrue(report.datagateway_read_matches_published and report.eligibility_published)

    def test_a_live_trade_inside_what_is_published_publishes_nothing(self) -> None:
        floor = Instant((D0 + 3) * MS)
        report, calls = self.publish([trade(D0 + 1, "a"), trade(D0 + 2, "b"), trade(D0 + 5, "c")], floor=floor)
        self.assertEqual(("SLICE_ORDERING_VIOLATION", 2, []), (report.status, report.ordering_violations, calls))

    def test_a_reconciliation_drops_the_replay_and_starts_where_the_last_slice_ended(self) -> None:
        floor = Instant((D0 + 3) * MS)
        report, calls = self.publish(
            [trade(D0 + 1, "a"), trade(D0 + 2, "b"), trade(D0 + 9, "c")], reconciling=True, floor=floor)
        self.assertEqual(("PASS", 2, [True]), (report.status, report.replayed_records, [c["reconciling"] for c in calls]))
        self.assertEqual([floor], [c["planned"].window_start for c in calls])
        self.assertEqual([floor], [c["chain_start"] for c in calls])  # the first slice must start exactly at the floor
        self.assertEqual(["c"], [r.trade_id for c in calls for r in c["planned"].records])

    def test_the_revision_chosen_for_the_key_reaches_the_slice(self) -> None:
        report, calls = self.publish([trade(D0, "a")], revision=3)
        self.assertEqual(([3], 3), ([c["revision"] for c in calls], report.durable_publication.revision))

    def test_a_bridge_is_published_like_any_slice_and_the_report_is_the_last_populated_one(self) -> None:
        midnight = ts_ms("2026-10-22T00:00:00Z")
        floor = Instant((midnight - 1) * MS)
        report, calls = self.publish([trade(midnight + 4, "a")], reconciling=True, floor=floor, max_trades=50_000)
        self.assertEqual(2, len(calls))
        self.assertEqual([floor, calls[0]["planned"].window_end], [c["chain_start"] for c in calls])
        self.assertEqual(("PASS", 2, calls[1]["planned"].partition_key), (report.status, report.slices, report.partition_key))

    def test_without_a_floor_a_reconciliation_starts_at_its_first_trade(self) -> None:
        report, calls = self.publish([trade(D0 + 4, "a")], reconciling=True, floor=None)
        self.assertEqual("PASS", report.status)
        self.assertEqual([Instant((D0 + 4) * MS)], [c["planned"].window_start for c in calls])

    def test_nothing_new_is_a_pending_outcome_of_the_right_kind(self) -> None:
        floor = Instant((D0 + 10) * MS)
        report, calls = self.publish([trade(D0, "a")], reconciling=True, floor=floor)
        self.assertEqual(("REAL_RESTART_PROOF_PENDING", 1, []), (report.status, report.replayed_records, calls))
        report, calls = self.publish([], reconciling=False)
        self.assertEqual(("LIVE_PROVIDER_PROOF_PENDING", []), (report.status, calls))

    def test_the_first_failing_slice_ends_the_publication(self) -> None:
        failure = RealServerPublishProofReport(status="CERTIFICATION_FAILED", acquisition=self.acquisition([]))
        report, calls = self.publish([trade(D0 + i, f"t{i}") for i in range(5)], outcomes={2: failure})
        self.assertEqual(("CERTIFICATION_FAILED", 2), (report.status, len(calls)))  # the third slice is never attempted


class NextRevisionTests(unittest.TestCase):
    """``_next_slice_revision``: 1 for a window never sealed; the next revision over an attempt that never became valid."""

    @staticmethod
    def revision(found):
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                self.params = params

            def fetchall(self):
                return found

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        with mock.patch.object(app, "_connect_catalog", lambda dsn: Connection()):
            return app._next_slice_revision(None, app.bybit_live_dataset_identity("ETHUSDT"), "dt=2026-10-21/seg=143015.123")

    def test_a_window_never_sealed_is_revision_one(self) -> None:
        self.assertEqual(1, self.revision([]))

    def test_an_attempt_that_never_became_valid_is_replaced_by_the_next_revision(self) -> None:
        self.assertEqual(2, self.revision([(1, "closed")]))
        self.assertEqual(3, self.revision([(1, "superseded"), (2, "closed")]))
        self.assertEqual(4, self.revision([(2, "superseded"), (1, "superseded"), (3, "closed")]))

    def test_a_valid_slice_is_never_superseded(self) -> None:
        for found in ([(1, "valid")], [(1, "superseded"), (2, "valid")]):
            with self.subTest(found=found), self.assertRaises(BybitLiveSourceError):
                self.revision(found)


class NextRevisionTests(unittest.TestCase):
    """``_next_slice_revision``: 1 for a window never sealed; the next revision over an attempt that never became valid."""

    @staticmethod
    def revision(found):
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                self.params = params

            def fetchall(self):
                return found

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        with mock.patch.object(app, "_connect_catalog", lambda dsn: Connection()):
            return app._next_slice_revision(None, app.bybit_live_dataset_identity("ETHUSDT"), "dt=2026-10-21/seg=143015.123")

    def test_a_window_never_sealed_is_revision_one(self) -> None:
        self.assertEqual(1, self.revision([]))

    def test_an_attempt_that_never_became_valid_is_replaced_by_the_next_revision(self) -> None:
        self.assertEqual(2, self.revision([(1, "closed")]))
        self.assertEqual(3, self.revision([(1, "superseded"), (2, "closed")]))
        self.assertEqual(4, self.revision([(2, "superseded"), (1, "superseded"), (3, "closed")]))

    def test_a_valid_slice_is_never_superseded(self) -> None:
        for found in ([(1, "valid")], [(1, "superseded"), (2, "valid")]):
            with self.subTest(found=found), self.assertRaises(BybitLiveSourceError):
                self.revision(found)


if __name__ == "__main__":
    unittest.main()
