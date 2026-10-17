"""Hermetic tests for the Bybit L2 source semantics, ordering provider and
certification profile (A14a, ADR-0070 §1, §2, §5, §7).

``fixtures/l2-book-event-v1/bybit-orderbook-50-btcusdt-live-excerpt-2026-10-14.jsonl``
is the first 40 ``orderbook.50.BTCUSDT`` messages of a real public capture
(linear, one connection, 2026-10-14); it carries no credentials and is far
below any bulk-data threshold.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import (  # noqa: E402
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    L2BookEvent,
    L2LevelChange,
)
from quant_platform.ordering import (  # noqa: E402
    L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1,
    ordering_policy_satisfies,
    provider_for,
)
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE,
    BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE,
    BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
    BybitL2EligibilityError,
    BybitL2SequenceConflict,
    L2CertificationParameters,
    OUTCOME_DEGRADED,
    OUTCOME_ELIGIBLE,
    OUTCOME_INELIGIBLE,
    bybit_l2_book_event_v1_applies_to,
    bybit_l2_book_event_v1_ordering_key,
    certify_bybit_l2_book_events,
    resolve_duplicate_sequences,
)
from quant_platform.source_adapters.l2 import (  # noqa: E402
    BYBIT_L2_SOURCE_SEMANTICS_V2,
    L2SourceError,
    normalize_bybit_l2_message,
)

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
CHANNEL = "orderbook.200.BTCUSDT"
BASE_MS = 1_705_276_800_000
EXCERPT = (
    ROOT
    / "fixtures"
    / "l2-book-event-v1"
    / "bybit-orderbook-50-btcusdt-live-excerpt-2026-10-14.jsonl"
)


def up(side: str, price: str, size: str = "1") -> L2LevelChange:
    return L2LevelChange(side, price, size, "upsert")


def rm(side: str, price: str) -> L2LevelChange:
    return L2LevelChange(side, price, "0", "delete")


def ev(
    seq: int | str | None,
    u: int | str | None,
    kind: str = "delta",
    *,
    ms: int | None = None,
    bids: tuple[L2LevelChange, ...] = (),
    asks: tuple[L2LevelChange, ...] = (),
    channel: str = CHANNEL,
    depth: int | None = 200,
    venue: str = "bybit",
    instrument: str = "BTCUSDT",
) -> L2BookEvent:
    if not bids and not asks:
        bids = (up("bid", "100.0"),)
    return L2BookEvent(
        venue=venue,
        market_type="linear_perp",
        instrument=instrument,
        native_symbol=instrument,
        source_channel=channel,
        acquisition_mode="historical_archive",
        event_type=kind,
        exchange_ts=Instant((BASE_MS + (ms if ms is not None else (int(seq) if seq is not None else 0))) * 1_000_000),
        provider_ts=None,
        source_depth_limit=depth,
        native_sequence=None if seq is None else str(seq),
        native_update_id=None if u is None else str(u),
        bids=bids,
        asks=asks,
    )


def snapshot(seq: int, u: int, **kwargs) -> L2BookEvent:
    kwargs.setdefault("bids", (up("bid", "100.0", "5"), up("bid", "99.0", "7")))
    kwargs.setdefault("asks", (up("ask", "101.0", "4"), up("ask", "102.0", "9")))
    return ev(seq, u, "snapshot", **kwargs)


def delta(seq: int, u: int, **kwargs) -> L2BookEvent:
    return ev(seq, u, "delta", **kwargs)


def certify(events, **kwargs):
    return certify_bybit_l2_book_events(IDENTITY, events, **kwargs)


def codes(report) -> set[str]:
    return set(report.violation_counts)


class AdapterSemanticsV2Tests(unittest.TestCase):
    MESSAGE = {
        "topic": "orderbook.200.BTCUSDT",
        "type": "delta",
        "ts": 1_705_276_800_123,
        "cts": 1_705_276_800_120,
        "data": {"s": "BTCUSDT", "u": 5, "seq": 9, "b": [["100.0", "1"]], "a": []},
    }

    def test_exchange_ts_is_cts_and_provider_ts_is_ts(self):
        event = normalize_bybit_l2_message(self.MESSAGE, acquisition_mode="historical_archive")
        self.assertEqual(BYBIT_L2_SOURCE_SEMANTICS_V2, "bybit-orderbook-l2-v2")
        self.assertEqual(event.exchange_ts, Instant(1_705_276_800_120 * 1_000_000))
        self.assertEqual(event.provider_ts, Instant(1_705_276_800_123 * 1_000_000))
        self.assertEqual(event.native_sequence, "9")
        self.assertEqual(event.native_update_id, "5")

    def test_missing_cts_is_refused_with_a_stable_reason_and_no_ts_fallback(self):
        message = {key: value for key, value in self.MESSAGE.items() if key != "cts"}
        with self.assertRaises(L2SourceError) as caught:
            normalize_bybit_l2_message(message, acquisition_mode="historical_archive")
        self.assertEqual(caught.exception.reason, "missing_engine_time")
        self.assertEqual(caught.exception.field, "cts")

    def test_invalid_cts_is_refused(self):
        for bad in ("1705276800120", 1.5, True, -1):
            with self.subTest(cts=bad):
                with self.assertRaises(L2SourceError) as caught:
                    normalize_bybit_l2_message(
                        {**self.MESSAGE, "cts": bad}, acquisition_mode="historical_archive"
                    )
                self.assertEqual(caught.exception.reason, "invalid_engine_time")


class OrderingProviderTests(unittest.TestCase):
    def test_identity_requirement_and_applicability(self):
        self.assertEqual(BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY, "bybit-l2-book-event-v1-seq-v1")
        self.assertTrue(
            ordering_policy_satisfies(
                BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
                L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1,
                [BYBIT_L2_ORDERING_PROVIDER],
            )
        )
        self.assertIs(
            provider_for(BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY, [BYBIT_L2_ORDERING_PROVIDER]),
            BYBIT_L2_ORDERING_PROVIDER,
        )
        self.assertTrue(BYBIT_L2_ORDERING_PROVIDER.applies(IDENTITY))
        for other in (
            DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"),
            DatasetIdentity("canonical", "l2", "bybit", "ETHUSDT", "l2-book-event-v1"),
            DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("canonical", "l2", "okx", "BTCUSDT", "l2-book-event-v1"),
        ):
            with self.subTest(identity=other.stable_dict()):
                self.assertFalse(bybit_l2_book_event_v1_applies_to(other))

    def test_key_is_the_numeric_sequence_not_the_text(self):
        nine, ten = delta(9, 1), delta(10, 2)
        self.assertEqual(bybit_l2_book_event_v1_ordering_key(nine), (9,))
        self.assertLess(
            bybit_l2_book_event_v1_ordering_key(nine),
            bybit_l2_book_event_v1_ordering_key(ten),
        )
        # Lexicographic order would put "10" before "9".
        self.assertGreater(nine.native_sequence, ten.native_sequence)
        ordered = resolve_duplicate_sequences([ten, nine])
        self.assertEqual([e.native_sequence for e in ordered], ["9", "10"])

    def test_key_refuses_missing_or_non_canonical_sequence(self):
        for bad in (None, "007", "1e3", "-1", ""):
            with self.subTest(sequence=bad):
                event = ev(1, 1)
                object.__setattr__(event, "native_sequence", bad)
                with self.assertRaises(DataIntegrityError):
                    bybit_l2_book_event_v1_ordering_key(event)
        with self.assertRaises(DataIntegrityError):
            bybit_l2_book_event_v1_ordering_key("not an event")  # type: ignore[arg-type]

    def test_idempotent_duplicates_collapse_and_conflicts_fail_closed(self):
        first = delta(5, 4, bids=(up("bid", "100.0", "1"),))
        again = delta(5, 4, bids=(up("bid", "100.0", "1"),))
        self.assertEqual(len(resolve_duplicate_sequences([first, again])), 1)
        conflicting = delta(5, 4, bids=(up("bid", "100.0", "2"),))
        with self.assertRaises(BybitL2SequenceConflict):
            resolve_duplicate_sequences([first, conflicting])
        self.assertTrue(issubclass(BybitL2SequenceConflict, DataIntegrityError))


class CertificationOutcomeTests(unittest.TestCase):
    def test_clean_stream_is_eligible(self):
        report = certify(
            [
                snapshot(100, 50),
                delta(101, 51, bids=(up("bid", "100.5", "2"),)),
                delta(150, 52, asks=(rm("ask", "102.0"),)),
            ]
        )
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(report.event_count, 3)
        self.assertEqual(len(report.segments), 1)
        segment = report.segments[0]
        self.assertTrue(segment.anchored)
        self.assertEqual((segment.first_sequence, segment.last_sequence), (100, 150))
        self.assertEqual((segment.first_update_id, segment.last_update_id), (50, 52))
        self.assertEqual(report.discontinuities, ())
        self.assertFalse(report.unanchored_head)
        self.assertEqual(report.first_event["event_type"], "snapshot")
        self.assertEqual(report.last_event["native_sequence"], "150")
        self.assertEqual(report.violation_counts, {})
        self.assertEqual(report.profile_id, BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE)
        self.assertEqual(report.check_suite, BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE)

    def test_sequence_gaps_are_not_a_continuity_signal(self):
        # seq is an engine-wide counter with large jumps; only u carries continuity.
        report = certify([snapshot(10, 1), delta(1_000_000, 2), delta(9_000_000_000, 3)])
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)

    def test_update_id_gap_is_a_discontinuity_and_degrades(self):
        report = certify(
            [
                snapshot(10, 100),
                delta(11, 101, bids=(up("bid", "100.5", "2"),)),
                # u jumps 101 -> 105: book state is unknown until the next snapshot.
                delta(12, 105, bids=(rm("bid", "55.0"),)),  # absent level, must not count
                delta(13, 106),
                snapshot(20, 1),  # a snapshot resets u and anchors again
                delta(21, 2),
            ]
        )
        self.assertEqual(report.outcome, OUTCOME_DEGRADED)
        self.assertEqual(len(report.discontinuities), 1)
        gap = report.discontinuities[0]
        self.assertEqual((gap.after_sequence, gap.after_update_id), (11, 101))
        self.assertEqual((gap.expected_update_id, gap.observed_update_id), (102, 105))
        self.assertEqual(gap.observed_sequence, 12)
        self.assertEqual(gap.resumes_at_sequence, 20)
        self.assertEqual(gap.unanchored_event_count, 2)
        self.assertEqual(report.unanchored_event_count, 2)
        self.assertFalse(report.unanchored_head)
        # Unanchored events are preserved but excluded from replay.
        self.assertEqual(report.deletes_of_absent, 0)
        self.assertEqual([s.anchored for s in report.segments], [True, False, True])
        self.assertEqual(report.event_count, 6)

    def test_discontinuity_open_at_the_end_has_no_resume(self):
        report = certify([snapshot(10, 100), delta(11, 105)])
        self.assertEqual(report.outcome, OUTCOME_DEGRADED)
        self.assertIsNone(report.discontinuities[0].resumes_at_sequence)
        self.assertIsNone(report.discontinuities[0].resumes_at_exchange_ts)

    def test_snapshot_resets_update_id_without_a_discontinuity(self):
        report = certify(
            [snapshot(10, 900), delta(11, 901), snapshot(12, 1), delta(13, 2), snapshot(14, 7)]
        )
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(len(report.segments), 3)
        self.assertEqual(report.discontinuities, ())

    def test_first_event_delta_is_an_unanchored_head_and_degrades(self):
        report = certify(
            [
                delta(5, 40, bids=(rm("bid", "55.0"),)),  # not replayed
                delta(6, 41),
                snapshot(7, 42),
                delta(8, 43),
            ]
        )
        self.assertEqual(report.outcome, OUTCOME_DEGRADED)
        self.assertTrue(report.unanchored_head)
        self.assertEqual(report.unanchored_event_count, 2)
        self.assertEqual(report.discontinuities, ())
        self.assertEqual([s.anchored for s in report.segments], [False, True])
        self.assertEqual(report.deletes_of_absent, 0)

    def test_empty_stream_is_eligible_with_no_events(self):
        report = certify([])
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(report.event_count, 0)
        self.assertIsNone(report.first_event)
        self.assertEqual(report.segments, ())


class CertificationViolationTests(unittest.TestCase):
    def assertIneligible(self, events, code, **kwargs):
        report = certify(events, **kwargs)
        self.assertEqual(report.outcome, OUTCOME_INELIGIBLE, report.stable_dict())
        self.assertIn(code, codes(report))
        return report

    def test_sequence_regression_is_ineligible_and_the_event_is_not_applied(self):
        report = self.assertIneligible(
            [snapshot(10, 1), delta(12, 2), delta(11, 3, bids=(rm("bid", "100.0"),))],
            "sequence_regression",
        )
        # the regressed event is skipped, so its delete never reaches the book
        self.assertEqual(report.event_count, 2)
        self.assertEqual(report.deletes_of_absent, 0)

    def test_duplicate_sequence_is_ineligible(self):
        self.assertIneligible([snapshot(10, 1), delta(11, 2), delta(11, 2)], "duplicate_sequence")

    def test_exchange_ts_regression_along_sequence_is_ineligible(self):
        self.assertIneligible(
            [snapshot(10, 1, ms=500), delta(11, 2, ms=400)], "event_time_regression"
        )

    def test_equal_exchange_ts_is_allowed(self):
        report = certify([snapshot(10, 1, ms=500), delta(11, 2, ms=500)])
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)

    def test_mixed_stream_and_depth_are_ineligible(self):
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, channel="orderbook.50.BTCUSDT")],
            "source_channel_mismatch",
        )
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, depth=500)], "source_depth_mismatch"
        )
        self.assertIneligible([snapshot(10, 1, depth=None)], "source_depth_mismatch")

    def test_missing_sequence_or_update_id_is_ineligible(self):
        self.assertIneligible([snapshot(10, 1), ev(None, 2)], "missing_sequence")
        self.assertIneligible([snapshot(10, 1), ev(11, None)], "missing_update_id")

    def test_foreign_or_mismatched_records_are_ineligible(self):
        self.assertIneligible([snapshot(10, 1), "not an event"], "record_invalid")  # type: ignore[list-item]
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, instrument="ETHUSDT")], "record_identity_mismatch"
        )
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, venue="okx")], "record_identity_mismatch"
        )

    def test_crossed_book_is_ineligible(self):
        report = self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, bids=(up("bid", "101.5", "1"),))], "crossed_book"
        )
        self.assertEqual(report.crossed_states, 1)

    def test_side_depth_over_the_effective_depth_is_ineligible(self):
        params = L2CertificationParameters(effective_depth=2)
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, bids=(up("bid", "98.0", "1"),))],
            "depth_exceeded",
            parameters=params,
        )
        # an insert paired with a delete in one message stays within the depth
        report = certify(
            [
                snapshot(10, 1),
                delta(11, 2, bids=(up("bid", "100.5", "1"), rm("bid", "99.0"))),
            ],
            parameters=params,
        )
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)

    def test_delete_of_absent_level_is_ineligible_by_default_and_a_parameter(self):
        events = [snapshot(10, 1), delta(11, 2, bids=(rm("bid", "55.0"),))]
        report = self.assertIneligible(events, "delete_of_absent_over_policy")
        self.assertEqual(report.deletes_of_absent, 1)
        tolerant = certify(events, parameters=L2CertificationParameters(max_deletes_of_absent=1))
        self.assertEqual(tolerant.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(tolerant.deletes_of_absent, 1)

    def test_a_delete_after_an_upsert_is_not_a_delete_of_absent(self):
        report = certify(
            [
                snapshot(10, 1),
                delta(11, 2, bids=(up("bid", "98.0", "1"),)),
                delta(12, 3, bids=(rm("bid", "98.0"),)),
            ]
        )
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(report.deletes_of_absent, 0)

    def test_best_price_tracking_survives_deleting_the_best_level(self):
        # delete the best ask, then a bid above the *new* best ask must be crossed
        report = certify(
            [
                snapshot(10, 1),
                delta(11, 2, asks=(rm("ask", "101.0"),)),
                delta(12, 3, bids=(up("bid", "101.5", "1"),)),
            ]
        )
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)  # best ask is now 102.0
        crossed = certify(
            [
                snapshot(10, 1),
                delta(11, 2, asks=(rm("ask", "101.0"),)),
                delta(12, 3, bids=(up("bid", "102.5", "1"),)),
            ]
        )
        self.assertIn("crossed_book", codes(crossed))

    def test_a_locked_book_is_crossed(self):
        # a bid equal to the best ask is not a valid resting book
        self.assertIneligible(
            [snapshot(10, 1), delta(11, 2, bids=(up("bid", "101.0", "1"),))], "crossed_book"
        )

    def test_best_price_is_recomputed_on_both_sides_after_deleting_the_best(self):
        # delete the best bid (100.0): the best bid becomes 99.0, so an ask at
        # 98.5 now crosses
        bid_side = [
            snapshot(10, 1),
            delta(11, 2, bids=(rm("bid", "100.0"),)),
            delta(12, 3, asks=(up("ask", "98.5", "1"),)),
        ]
        self.assertIneligible(bid_side, "crossed_book")
        # symmetric: delete the best ask (101.0): the best ask becomes 102.0,
        # so a bid at 102.5 crosses
        ask_side = [
            snapshot(10, 1),
            delta(11, 2, asks=(rm("ask", "101.0"),)),
            delta(12, 3, bids=(up("bid", "102.5", "1"),)),
        ]
        self.assertIneligible(ask_side, "crossed_book")
        # and an ask above the *new* best bid is fine
        fine = certify(
            [
                snapshot(10, 1),
                delta(11, 2, bids=(rm("bid", "100.0"),)),
                delta(12, 3, asks=(up("ask", "99.5", "1"),)),
            ]
        )
        self.assertEqual(fine.outcome, OUTCOME_ELIGIBLE)

    def test_a_snapshot_replaces_the_whole_book(self):
        # the second snapshot omits the 99.0 bid, so deleting it afterwards is
        # a delete of an absent level
        replaced = [
            snapshot(10, 1),
            snapshot(11, 1, bids=(up("bid", "100.0", "5"),), asks=(up("ask", "101.0", "4"),)),
            delta(12, 2, bids=(rm("bid", "99.0"),)),
        ]
        report = self.assertIneligible(replaced, "delete_of_absent_over_policy")
        self.assertEqual(report.deletes_of_absent, 1)
        # levels do not accumulate across snapshots, so depth is not exceeded
        params = L2CertificationParameters(effective_depth=2)
        disjoint = [
            snapshot(10, 1, bids=(up("bid", "100.0"), up("bid", "99.0"))),
            snapshot(11, 1, bids=(up("bid", "98.0"), up("bid", "97.0"))),
        ]
        self.assertEqual(certify(disjoint, parameters=params).outcome, OUTCOME_ELIGIBLE)

    def test_violation_samples_are_bounded_but_counts_are_exact(self):
        events = [snapshot(1, 1)] + [delta(2 + i, 2 + i, channel="other") for i in range(100)]
        report = certify(events)
        self.assertEqual(report.violation_counts["source_channel_mismatch"], 100)
        self.assertLessEqual(len(report.violations), 32)


class ProfileAndDeterminismTests(unittest.TestCase):
    def test_profile_refuses_a_foreign_identity(self):
        with self.assertRaises(BybitL2EligibilityError):
            certify_bybit_l2_book_events(
                DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"), []
            )

    def test_profile_object_delegates_with_its_ids_and_code_ref(self):
        profile = BybitL2BookEventV1CertificationProfile(code_ref="abc123")
        self.assertTrue(profile.applies_to(IDENTITY))
        self.assertEqual(profile.ordering_key(delta(5, 1)), (5,))
        report = profile.certify(IDENTITY, [snapshot(1, 1)])
        self.assertEqual(report.code_ref, "abc123")
        self.assertEqual(report.profile_id, profile.profile_id)

    def test_parameters_validate(self):
        for kwargs in (
            {"effective_depth": 0},
            {"source_depth_limit": True},
            {"max_deletes_of_absent": -1},
            {"source_channel": " "},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(Exception):
                    L2CertificationParameters(**kwargs)

    def test_report_is_deterministic_and_independent_of_chunking(self):
        events = [
            snapshot(10, 100),
            delta(11, 101, bids=(up("bid", "100.5", "2"),)),
            delta(12, 105),
            snapshot(20, 1),
            delta(21, 2),
        ]

        def chunked(size):
            for start in range(0, len(events), size):
                yield from events[start : start + size]

        reference = json.dumps(certify(events).stable_dict(), sort_keys=True)
        self.assertEqual(reference, json.dumps(certify(iter(events)).stable_dict(), sort_keys=True))
        for size in (1, 2, 3):
            self.assertEqual(
                reference, json.dumps(certify(chunked(size)).stable_dict(), sort_keys=True)
            )

    def test_a_single_pass_iterator_is_enough(self):
        consumed = []

        def source():
            for event in [snapshot(1, 1), delta(2, 2), delta(3, 3)]:
                consumed.append(event)
                yield event

        report = certify(source())
        self.assertEqual(report.event_count, 3)
        self.assertEqual(len(consumed), 3)

    def test_a_long_stream_certifies_with_bounded_state(self):
        events = [snapshot(1, 1)]
        for i in range(2, 20_001):
            price = f"{100 - (i % 40) * 0.5:.1f}"
            events.append(delta(i, i, bids=(up("bid", price, "1"),)))
        report = certify(events)
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(report.event_count, 20_000)
        self.assertEqual(len(report.segments), 1)


class RealExcerptTests(unittest.TestCase):
    def messages(self):
        return [json.loads(line) for line in EXCERPT.read_text(encoding="utf-8").splitlines()]

    def test_live_excerpt_normalizes_and_certifies_eligible(self):
        messages = self.messages()
        self.assertEqual(len(messages), 40)
        events = [
            normalize_bybit_l2_message(m, acquisition_mode="live_websocket") for m in messages
        ]
        first = messages[0]
        self.assertEqual(events[0].event_type, "snapshot")
        self.assertEqual(events[0].exchange_ts, Instant(first["cts"] * 1_000_000))
        self.assertEqual(events[0].provider_ts, Instant(first["ts"] * 1_000_000))
        params = L2CertificationParameters(
            source_channel="orderbook.50.BTCUSDT", source_depth_limit=50, effective_depth=50
        )
        report = certify(events, parameters=params)
        self.assertEqual(report.outcome, OUTCOME_ELIGIBLE, report.stable_dict())
        self.assertEqual(report.event_count, 40)
        self.assertEqual(len(report.segments), 1)
        self.assertEqual(report.deletes_of_absent, 0)
        self.assertEqual(report.crossed_states, 0)
        # order by the numeric sequence reproduces the source order
        self.assertEqual(
            [e.native_sequence for e in resolve_duplicate_sequences(reversed(events))],
            [e.native_sequence for e in events],
        )
        # the default (depth-200) parameters must refuse a depth-50 stream
        wrong = certify(events)
        self.assertEqual(wrong.outcome, OUTCOME_INELIGIBLE)
        self.assertIn("source_channel_mismatch", codes(wrong))


if __name__ == "__main__":
    unittest.main()
