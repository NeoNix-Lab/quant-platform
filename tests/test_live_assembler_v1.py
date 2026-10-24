"""DG-O O02 (ADR-0075 sections 2 and 5): the slice assembler of a continuous session. Pure: an injected clock, no network, no catalog."""

from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.source_adapters.bybit_live import BybitLiveIntegrityError  # noqa: E402
from quant_platform.source_adapters.bybit_live_assembler import SliceAssembler  # noqa: E402
from quant_platform.source_adapters.bybit_live_slices import LiveSliceError  # noqa: E402

MS = 1_000_000
DAY_MS = 86_400_000
D0 = int(Instant.parse("2026-10-21T14:30:15Z").epoch_ns // MS)
MIDNIGHT = int(Instant.parse("2026-10-22T00:00:00Z").epoch_ns // MS)


def trade(ms: int, trade_id: str, price: str = "100.10", sequence: str = "1") -> TradeRecord:
    return TradeRecord("bybit", "ETHUSDT", Instant(ms * MS), price, "0.5", "buy", None, trade_id, sequence)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ids(slice_) -> list[str]:
    return [r.trade_id for r in slice_.records]


class AssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()

    def assembler(self, **kwargs) -> SliceAssembler:
        return SliceAssembler(clock=self.clock, **kwargs)

    def test_the_first_window_starts_at_the_earliest_trade_of_the_first_message(self) -> None:
        a = self.assembler()
        self.assertIsNone(a.window_start)
        self.assertEqual(0, a.add([trade(D0 + 5, "b"), trade(D0 + 2, "a")]))
        self.assertEqual(Instant((D0 + 2) * MS), a.window_start)
        self.assertFalse(a.due())
        self.assertEqual([], a.take_due())

    def test_the_wall_clock_bound_closes_at_the_greatest_exchange_ts_and_ties_stay(self) -> None:
        a = self.assembler(slice_seconds=300)
        a.add([trade(D0, "a"), trade(D0 + 10, "b")])
        a.add([trade(D0 + 20, "c"), trade(D0 + 20, "d")])
        self.clock.now += 299.9
        self.assertFalse(a.due())
        self.clock.now += 0.2
        self.assertTrue(a.due())
        slices = a.take_due()
        self.assertEqual(1, len(slices))
        self.assertEqual((Instant(D0 * MS), Instant((D0 + 20) * MS)), (slices[0].window_start, slices[0].window_end))
        self.assertEqual(["a", "b"], ids(slices[0]))
        self.assertEqual(2, a.pending_count)  # the two trades of the boundary millisecond stay
        self.assertEqual(Instant((D0 + 20) * MS), a.window_start)
        self.assertFalse(a.due())  # the carried trades opened a new window: its clock restarted

    def test_the_wall_clock_bound_is_inclusive(self) -> None:
        a = self.assembler(slice_seconds=300)
        a.add([trade(D0, "a"), trade(D0 + 10, "b")])
        self.clock.now += 300.0
        self.assertTrue(a.due())
        (only,) = a.take_due()
        self.assertEqual(["a"], ids(only))

    def test_a_trade_exactly_at_midnight_is_cut_at_once(self) -> None:
        a = self.assembler()
        a.add([trade(MIDNIGHT - 1, "a"), trade(MIDNIGHT, "b")])
        self.assertTrue(a.due())
        (before,) = a.take_due()
        self.assertEqual((["a"], Instant(MIDNIGHT * MS)), (ids(before), before.window_end))
        self.assertEqual(Instant(MIDNIGHT * MS), a.window_start)
        self.assertEqual(1, a.pending_count)

    def test_the_size_bound_closes_a_slice(self) -> None:
        a = self.assembler(max_trades=3)
        a.add([trade(D0, "a"), trade(D0 + 1, "b")])
        self.assertFalse(a.due())
        a.add([trade(D0 + 2, "c")])
        self.assertTrue(a.due())
        (only,) = a.take_due()
        self.assertEqual(["a", "b"], ids(only))
        self.assertEqual(1, a.pending_count)

    def test_a_window_of_one_millisecond_cannot_be_cut_and_waits(self) -> None:
        a = self.assembler(max_trades=2)
        a.add([trade(D0, "a"), trade(D0, "b")])
        self.assertTrue(a.due())
        self.assertEqual([], a.take_due())
        self.assertEqual(2, a.pending_count)
        a.add([trade(D0 + 1, "c")])
        (only,) = a.take_due()
        self.assertEqual(["a", "b"], ids(only))

    def test_a_utc_midnight_is_cut_at_once_and_the_later_trades_open_the_next_window(self) -> None:
        a = self.assembler()
        a.add([trade(MIDNIGHT - 5, "a"), trade(MIDNIGHT - 1, "b")])
        self.assertEqual([], a.take_due())
        a.add([trade(MIDNIGHT, "c"), trade(MIDNIGHT + 3, "d")])
        self.assertTrue(a.due())
        (before,) = a.take_due()
        self.assertEqual(["a", "b"], ids(before))
        self.assertEqual(Instant(MIDNIGHT * MS), before.window_end)
        self.assertEqual(Instant(MIDNIGHT * MS), a.window_start)
        self.assertEqual(2, a.pending_count)
        (after,) = a.flush()
        self.assertEqual((Instant(MIDNIGHT * MS), Instant((MIDNIGHT + 4) * MS)), (after.window_start, after.window_end))
        self.assertEqual(["c", "d"], ids(after))

    def test_a_trade_below_the_open_windows_start_is_an_ordering_violation(self) -> None:
        a = self.assembler()
        a.add([trade(D0, "a"), trade(D0 + 10, "b")])
        self.clock.now += 301
        a.take_due()  # the window now starts at D0 + 10
        self.assertEqual(1, a.add([trade(D0 + 9, "late"), trade(D0 + 11, "c")]))
        self.assertEqual(1, a.ordering_violations)
        self.assertEqual(0, a.add([trade(D0 + 10, "tie")]))  # a trade at the boundary is the next window's own
        self.assertEqual(["b", "tie", "c"], [k[1] for k in sorted(a._pending)])

    def test_an_equivalent_duplicate_is_idempotent_and_a_conflicting_one_fails_closed(self) -> None:
        a = self.assembler()
        a.add([trade(D0, "a")])
        a.add([trade(D0, "a")])
        self.assertEqual(1, a.pending_count)
        with self.assertRaises(BybitLiveIntegrityError):
            a.add([trade(D0, "a", price="999.9")])

    def test_trades_at_or_before_the_skip_key_are_duplicates_of_what_is_already_published(self) -> None:
        skip = (Instant((D0 + 5) * MS), "m")
        a = self.assembler(window_start=Instant((D0 + 6) * MS), skip_through=skip)
        a.add([trade(D0 + 5, "m"), trade(D0 + 4, "z"), trade(D0 + 6, "n")])
        self.assertEqual((2, 0, 1), (a.duplicates_skipped, a.ordering_violations, a.pending_count))

    def test_only_the_keys_a_reconciliation_accepted_are_duplicates_not_every_key_below_the_last_of_them(self) -> None:
        a = self.assembler()
        a.start_window(Instant((D0 + 6) * MS), skip_through=(Instant((D0 + 2) * MS), "a"), skip_keys=[(Instant((D0 + 5) * MS), "m")])
        # "m" was accepted by the reconciliation; "k" (below its last key, never seen) is a trade like any other: it is late
        violations = a.add([trade(D0 + 5, "m"), trade(D0 + 4, "k"), trade(D0 + 2, "a"), trade(D0 + 7, "n")])
        self.assertEqual(2, a.duplicates_skipped)  # "m" by key, "a" by the anchor
        self.assertEqual(1, violations)  # "k"
        self.assertEqual(1, a.pending_count)  # "n"

    def test_flush_closes_one_millisecond_after_the_greatest_trade_and_ends_the_chain(self) -> None:
        a = self.assembler()
        a.add([trade(D0, "a"), trade(D0 + 7, "b"), trade(D0 + 7, "c")])
        (only,) = a.flush()
        self.assertEqual((Instant(D0 * MS), Instant((D0 + 8) * MS)), (only.window_start, only.window_end))
        self.assertEqual(["a", "b", "c"], ids(only))
        self.assertEqual((0, None), (a.pending_count, a.window_start))
        self.assertEqual([], a.flush())

    def test_a_window_cannot_be_restarted_while_records_are_pending(self) -> None:
        a = self.assembler()
        a.add([trade(D0, "a")])
        with self.assertRaises(LiveSliceError):
            a.start_window(None)
        a.flush()
        a.start_window(Instant((D0 + 100) * MS), skip_through=(Instant((D0 + 99) * MS), "z"))
        self.assertEqual(Instant((D0 + 100) * MS), a.window_start)

    def test_a_sub_millisecond_trade_and_bad_bounds_are_refused(self) -> None:
        with self.assertRaises(LiveSliceError):
            self.assembler().add([TradeRecord("bybit", "ETHUSDT", Instant(D0 * MS + 1), "1", "1", "buy", None, "a", "1")])
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(LiveSliceError):
                self.assembler(max_trades=bad)
            with self.assertRaises(LiveSliceError):
                self.assembler(slice_seconds=bad)

    def test_random_streams_lose_nothing_and_chain_exactly(self) -> None:
        for seed in range(40):
            rng = random.Random(seed)
            a = self.assembler(max_trades=rng.choice((2, 5, 50)), slice_seconds=rng.choice((1, 10, 300)))
            published: list = []
            sent: list[TradeRecord] = []
            ts = MIDNIGHT - rng.randrange(0, 2000)
            for index in range(rng.randrange(1, 120)):
                ts += rng.choice((0, 0, 1, 3, 40, 900))
                batch = [trade(ts, f"s{seed}-{index}-{k}") for k in range(rng.randrange(1, 4))]
                sent.extend(batch)
                self.assertEqual(0, a.add(batch))
                self.clock.now += rng.choice((0.0, 0.5, 4.0, 12.0))
                published.extend(a.take_due())
            published.extend(a.flush())
            with self.subTest(seed=seed):
                got = [r.trade_id for s in published for r in s.records]
                self.assertEqual(sorted(r.trade_id for r in sent), sorted(got))
                self.assertEqual(len(got), len(set(got)))
                for left, right in zip(published, published[1:]):
                    self.assertTrue(
                        left.window_end == right.window_start or left.window_end.epoch_ns % (DAY_MS * MS) == 0,
                        (left.window_end, right.window_start),
                    )
                    self.assertLess(left.records[-1].exchange_ts.epoch_ns, right.records[0].exchange_ts.epoch_ns)
                for s in published:
                    self.assertTrue(s.records)
                    self.assertLessEqual(s.window_start.epoch_ns, s.records[0].exchange_ts.epoch_ns)
                    self.assertLess(s.records[-1].exchange_ts.epoch_ns, s.window_end.epoch_ns)
                    self.assertLessEqual(s.window_end.epoch_ns, (s.window_start.epoch_ns // (DAY_MS * MS) + 1) * DAY_MS * MS)


if __name__ == "__main__":
    unittest.main()
