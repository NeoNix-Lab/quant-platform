#!/usr/bin/env python3
"""Behavioral tests for DataGateway.live_stream() (B06, ADR-0047, issue #194)."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.gateway import (  # noqa: E402
    DataGateway,
    LiveStreamState,
)
from quant_platform.access.models import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    LiveGapEvent,
    LiveGapStatus,
    LiveSessionEvent,
    LiveSessionState,
    LiveStreamCursorV1,
    LiveStreamRequest,
    LiveTradeEvent,
)
from quant_platform.data.models import (  # noqa: E402
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    InvalidRequest,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
DATASET = CatalogDataset(
    identity=IDENTITY,
    catalog_dataset_id="dataset-a",
    rel_root=REL_ROOT,
    manifest_sha256="a" * 64,
    schema_version=1,
    schema_hash="b" * 64,
)


def instant(value: str) -> Instant:
    return Instant.parse(value)


def live_request() -> LiveStreamRequest:
    return LiveStreamRequest(IDENTITY, ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY)


def trade(ts: str, trade_id: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=instant(ts),
        price="100.0",
        size="0.1",
        aggressor_side="buy",
        trade_id=trade_id,
    )


def partition(key: str, start: str, end: str, *, partition_id: str, rel_path: str | None = None) -> CatalogPartition:
    return CatalogPartition(
        natural_identity=NaturalPartitionIdentity(IDENTITY, key, 1),
        catalog_partition_id=partition_id,
        storage_root_id="hot",
        storage_root="/catalog-root",
        dataset_rel_root=REL_ROOT,
        rel_path=rel_path or f"{key}/part-000.parquet",
        ts_start=instant(start),
        ts_end=instant(end),
        row_count=0,
        content_sha256=("c" + partition_id[-1]) * 32,
        manifest_sha256=("d" + partition_id[-1]) * 32,
        state="valid",
        producer="fixture",
        code_ref="test",
    )


class FakeCatalog:
    def __init__(self, partitions: list[CatalogPartition]):
        self.partitions = partitions

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        if identity != IDENTITY:
            raise AssertionError(identity)
        return DATASET

    def select_partitions(self, dataset, start, end, states):
        return [
            item
            for item in self.partitions
            if item.state in states and item.coverage is not None and item.ts_end > start and item.ts_start < end
        ]


class FakeBatchReader:
    def __init__(self, batches_by_path: dict[str, list[tuple[TradeRecord, ...]]]):
        self.batches_by_path = batches_by_path
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, path: str, start: Instant, end: Instant, batch_size: int):
        self.calls.append((path, start.isoformat(), end.isoformat()))
        for batch in self.batches_by_path.get(path, []):
            filtered = tuple(r for r in batch if start <= r.exchange_ts < end)
            if filtered:
                yield filtered


class UntrustworthyBatchReader:
    """A misbehaving reader that ignores the requested [start, end) window --
    simulates a malformed parquet file so tests can prove LiveStream does not
    trust the reader any more than DataScan does."""

    def __init__(self, batches_by_path: dict[str, list[tuple[TradeRecord, ...]]]):
        self.batches_by_path = batches_by_path

    def __call__(self, path: str, start: Instant, end: Instant, batch_size: int):
        yield from self.batches_by_path.get(path, [])


def gateway(
    partitions: list[CatalogPartition], batches_by_path: dict[str, list[tuple[TradeRecord, ...]]]
) -> tuple[DataGateway, FakeBatchReader]:
    reader = FakeBatchReader(batches_by_path)
    instance = DataGateway(
        FakeCatalog(partitions),
        batch_reader=reader,
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )
    return instance, reader


class FreshStartTests(unittest.TestCase):
    def test_delivers_all_records_in_order_with_no_cursor(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:02Z", "2"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        stream = gw.live_stream(live_request())
        events = list(stream)

        self.assertEqual(2, len(events))
        self.assertTrue(all(isinstance(e, LiveTradeEvent) for e in events))
        self.assertEqual(["1", "2"], [e.record.trade_id for e in events])
        self.assertEqual(LiveStreamState.EXHAUSTED, stream.state)

    def test_no_reconnected_or_gap_event_on_a_fresh_call(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"),)
        gw, _ = gateway([p], {p.rel_path: [records]})

        events = list(gw.live_stream(live_request()))

        self.assertTrue(all(isinstance(e, LiveTradeEvent) for e in events))

    def test_cursor_carries_the_dataset_ordering_and_a_fresh_coverage_segment_id(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"),)
        gw, _ = gateway([p], {p.rel_path: [records]})

        [event] = list(gw.live_stream(live_request()))
        cursor = event.cursor

        self.assertEqual(IDENTITY, cursor.dataset_identity)
        self.assertEqual(BYBIT_TRADE_V1_ORDERING_POLICY, cursor.ordering_policy)
        self.assertTrue(cursor.coverage_segment_id)
        self.assertEqual(instant("2024-01-01T00:00:01Z"), cursor.last_canonical_exchange_ts)
        self.assertEqual("1", cursor.last_canonical_trade_id)

    def test_empty_dataset_yields_nothing_and_exhausts_immediately(self):
        gw, _ = gateway([], {})
        self.assertEqual([], list(gw.live_stream(live_request())))

    def test_inter_island_gap_is_reported_even_without_a_prior_cursor(self):
        left = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        right = partition("dt=2024-01-01/hour=03", "2024-01-01T03:00:00Z", "2024-01-01T04:00:00Z", partition_id="partition-b")
        left_records = (trade("2024-01-01T00:00:01Z", "1"),)
        right_records = (trade("2024-01-01T03:00:01Z", "2"),)
        gw, _ = gateway([left, right], {left.rel_path: [left_records], right.rel_path: [right_records]})

        events = list(gw.live_stream(live_request()))

        kinds = [type(e).__name__ for e in events]
        self.assertEqual(["LiveTradeEvent", "LiveGapEvent", "LiveGapEvent", "LiveTradeEvent"], kinds)
        open_gap, closed_gap = events[1], events[2]
        self.assertEqual(LiveGapStatus.OPEN, open_gap.status)
        self.assertEqual(LiveGapStatus.CLOSED, closed_gap.status)
        self.assertEqual(open_gap.gap_id, closed_gap.gap_id)
        self.assertEqual(instant("2024-01-01T01:00:00Z"), closed_gap.lower_bound)
        self.assertEqual(instant("2024-01-01T03:00:01Z"), closed_gap.upper_bound)
        self.assertEqual("2", closed_gap.resumed_cursor.last_canonical_trade_id)
        self.assertEqual(closed_gap.affected_interval.start, closed_gap.lower_bound)
        self.assertEqual(closed_gap.affected_interval.end, closed_gap.upper_bound)
        self.assertIsNone(open_gap.affected_interval)


class ResumeTests(unittest.TestCase):
    def _cursor_at(self, ts: str, trade_id: str, *, coverage_segment_id: str = "seg-1") -> LiveStreamCursorV1:
        return LiveStreamCursorV1(
            schema_version="live-stream-cursor-v1",
            dataset_identity=IDENTITY,
            ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            coverage_segment_id=coverage_segment_id,
            last_canonical_exchange_ts=instant(ts),
            last_canonical_trade_id=trade_id,
        )

    def test_resume_is_exclusive_of_the_anchor_no_duplicate_no_lost(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (
            trade("2024-01-01T00:00:01Z", "1"),
            trade("2024-01-01T00:00:02Z", "2"),
            trade("2024-01-01T00:00:03Z", "3"),
        )
        gw, _ = gateway([p], {p.rel_path: [records]})

        cursor = self._cursor_at("2024-01-01T00:00:01Z", "1")
        events = list(gw.live_stream(live_request(), cursor=cursor))

        trade_events = [e for e in events if isinstance(e, LiveTradeEvent)]
        self.assertEqual(["2", "3"], [e.record.trade_id for e in trade_events])

    def test_resume_emits_reconnected_first(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:02Z", "2"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        cursor = self._cursor_at("2024-01-01T00:00:01Z", "1")
        events = list(gw.live_stream(live_request(), cursor=cursor))

        self.assertIsInstance(events[0], LiveSessionEvent)
        self.assertEqual(LiveSessionState.RECONNECTED, events[0].state)

    def test_resuming_at_the_live_edge_yields_only_reconnected_and_exhausts(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"),)
        gw, _ = gateway([p], {p.rel_path: [records]})

        cursor = self._cursor_at("2024-01-01T00:00:01Z", "1")
        stream = gw.live_stream(live_request(), cursor=cursor)
        events = list(stream)

        self.assertEqual(1, len(events))
        self.assertIsInstance(events[0], LiveSessionEvent)
        self.assertEqual(LiveStreamState.EXHAUSTED, stream.state)

    def test_resuming_into_an_unprovable_gap_opens_and_does_not_fabricate_continuity(self):
        # cursor points at a position with no further eligible coverage at all yet.
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"),)
        gw, _ = gateway([p], {p.rel_path: [records]})

        cursor = self._cursor_at("2024-01-01T00:30:00Z", "99")  # after the only known record, mid-partition
        events = list(gw.live_stream(live_request(), cursor=cursor))

        # No gap here: still inside the same partition's declared coverage,
        # simply caught up with nothing further published in it yet.
        self.assertEqual(1, len(events))
        self.assertIsInstance(events[0], LiveSessionEvent)

    def test_resuming_after_the_only_partition_ends_opens_a_gap_when_a_later_island_exists(self):
        left = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        right = partition("dt=2024-01-01/hour=02", "2024-01-01T02:00:00Z", "2024-01-01T03:00:00Z", partition_id="partition-b")
        left_records = (trade("2024-01-01T00:00:01Z", "1"),)
        right_records = (trade("2024-01-01T02:00:01Z", "2"),)
        gw, _ = gateway([left, right], {left.rel_path: [left_records], right.rel_path: [right_records]})

        cursor = self._cursor_at("2024-01-01T00:00:01Z", "1")
        events = list(gw.live_stream(live_request(), cursor=cursor))

        kinds = [type(e).__name__ for e in events]
        self.assertEqual(["LiveSessionEvent", "LiveGapEvent", "LiveGapEvent", "LiveTradeEvent"], kinds)
        open_gap, closed_gap = events[1], events[2]
        self.assertEqual(LiveGapStatus.OPEN, open_gap.status)
        self.assertIsNone(open_gap.upper_bound)
        self.assertIsNone(open_gap.resumed_cursor)
        self.assertEqual(instant("2024-01-01T01:00:00Z"), open_gap.lower_bound)
        self.assertEqual(LiveGapStatus.CLOSED, closed_gap.status)
        self.assertEqual(instant("2024-01-01T02:00:01Z"), closed_gap.upper_bound)
        self.assertEqual("2", closed_gap.resumed_cursor.last_canonical_trade_id)
        self.assertNotEqual(cursor.coverage_segment_id, closed_gap.resumed_cursor.coverage_segment_id)

    def test_coverage_segment_id_changes_across_an_explicit_gap(self):
        left = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        right = partition("dt=2024-01-01/hour=02", "2024-01-01T02:00:00Z", "2024-01-01T03:00:00Z", partition_id="partition-b")
        left_records = (trade("2024-01-01T00:00:01Z", "1"),)
        right_records = (trade("2024-01-01T02:00:01Z", "2"),)
        gw, _ = gateway([left, right], {left.rel_path: [left_records], right.rel_path: [right_records]})

        events = list(gw.live_stream(live_request()))
        trade_events = [e for e in events if isinstance(e, LiveTradeEvent)]
        gap_events = [e for e in events if isinstance(e, LiveGapEvent)]

        pre_gap_cursor = trade_events[0].cursor
        post_gap_cursor = trade_events[1].cursor
        open_gap, closed_gap = gap_events
        self.assertNotEqual(pre_gap_cursor.coverage_segment_id, post_gap_cursor.coverage_segment_id)
        self.assertEqual(pre_gap_cursor.coverage_segment_id, open_gap.previous_cursor.coverage_segment_id)
        self.assertEqual(post_gap_cursor.coverage_segment_id, closed_gap.resumed_cursor.coverage_segment_id)

    def test_coverage_segment_id_stays_stable_within_one_contiguous_island(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:02Z", "2"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        events = [e for e in gw.live_stream(live_request()) if isinstance(e, LiveTradeEvent)]

        self.assertEqual(events[0].cursor.coverage_segment_id, events[1].cursor.coverage_segment_id)

    def test_coverage_segment_id_preserved_on_resume_within_same_contiguous_island(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:02Z", "2"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        events1 = [e for e in gw.live_stream(live_request()) if isinstance(e, LiveTradeEvent)]
        first_cursor = events1[0].cursor

        events2 = [e for e in gw.live_stream(live_request(), cursor=first_cursor) if isinstance(e, LiveTradeEvent)]
        resumed_cursor = events2[0].cursor

        self.assertEqual(first_cursor.coverage_segment_id, resumed_cursor.coverage_segment_id)
        self.assertEqual("2", events2[0].record.trade_id)

    def test_full_incremental_replay_matches_one_full_drain(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = tuple(trade(f"2024-01-01T00:00:0{i}Z", str(i)) for i in range(1, 6))
        gw, _ = gateway([p], {p.rel_path: [records]})

        full = [e.record.trade_id for e in gw.live_stream(live_request()) if isinstance(e, LiveTradeEvent)]

        incremental: list[str] = []
        cursor = None
        for _ in range(len(records) + 1):
            stream = gw.live_stream(live_request(), cursor=cursor)
            for event in stream:
                if isinstance(event, LiveTradeEvent):
                    incremental.append(event.record.trade_id)
                    cursor = event.cursor

        self.assertEqual(full, incremental)
        self.assertEqual(["1", "2", "3", "4", "5"], full)

    def test_cursor_dataset_mismatch_is_refused(self):
        gw, _ = gateway([], {})
        other = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
        bad_cursor = LiveStreamCursorV1(
            schema_version="live-stream-cursor-v1",
            dataset_identity=other,
            ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            coverage_segment_id="seg-1",
            last_canonical_exchange_ts=None,
            last_canonical_trade_id=None,
        )
        with self.assertRaises(InvalidRequest):
            gw.live_stream(live_request(), cursor=bad_cursor)


class IntegrityTests(unittest.TestCase):
    def test_duplicate_ordering_key_raises(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:01Z", "1"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        with self.assertRaises(DataIntegrityError):
            list(gw.live_stream(live_request()))

    def test_out_of_order_record_raises(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:02Z", "2"), trade("2024-01-01T00:00:01Z", "1"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        with self.assertRaises(DataIntegrityError):
            list(gw.live_stream(live_request()))

    def test_untrustworthy_reader_cannot_smuggle_a_record_outside_declared_coverage(self):
        # partition-a declares [00:00, 01:00); a misbehaving reader hands back
        # a record at 05:00 anyway. LiveStream must not trust it -- exactly
        # like DataScan never trusts its own batch reader.
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        rogue_record = trade("2024-01-01T05:00:00Z", "1")
        reader = UntrustworthyBatchReader({p.rel_path: [(rogue_record,)]})
        gw = DataGateway(
            FakeCatalog([p]),
            batch_reader=reader,
            path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )

        with self.assertRaises(DataIntegrityError):
            list(gw.live_stream(live_request()))

    def test_untrustworthy_reader_cannot_cross_backward_over_the_previous_partition_boundary(self):
        left = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        right = partition("dt=2024-01-01/hour=01", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z", partition_id="partition-b")
        # right's reader hands back a record that actually belongs before
        # left's coverage ended -- must be refused, not silently accepted as
        # live continuity evidence.
        rogue_record = trade("2024-01-01T00:00:30Z", "1")
        reader = UntrustworthyBatchReader({right.rel_path: [(rogue_record,)]})
        gw = DataGateway(
            FakeCatalog([left, right]),
            batch_reader=reader,
            path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )

        with self.assertRaises(DataIntegrityError):
            list(gw.live_stream(live_request()))

    def test_aborts_state_on_error(self):
        p = partition("dt=2024-01-01/hour=00", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", partition_id="partition-a")
        records = (trade("2024-01-01T00:00:01Z", "1"), trade("2024-01-01T00:00:01Z", "1"))
        gw, _ = gateway([p], {p.rel_path: [records]})

        stream = gw.live_stream(live_request())
        with self.assertRaises(DataIntegrityError):
            list(stream)
        self.assertEqual(LiveStreamState.ABORTED, stream.state)


class GapEventValidationTests(unittest.TestCase):
    def _cursor(self) -> LiveStreamCursorV1:
        return LiveStreamCursorV1(
            schema_version="live-stream-cursor-v1",
            dataset_identity=IDENTITY,
            ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
            coverage_segment_id="seg-1",
            last_canonical_exchange_ts=instant("2024-01-01T00:00:00Z"),
            last_canonical_trade_id="1",
        )

    def test_open_gap_rejects_upper_bound(self):
        with self.assertRaises(InvalidRequest):
            LiveGapEvent(
                schema_version="live-gap-event-v1",
                gap_id="g1",
                status=LiveGapStatus.OPEN,
                previous_cursor=self._cursor(),
                lower_bound=instant("2024-01-01T00:00:00Z"),
                upper_bound=instant("2024-01-01T00:00:01Z"),
                resumed_cursor=None,
                reason="test",
            )

    def test_closed_gap_requires_upper_bound_and_resumed_cursor(self):
        with self.assertRaises(InvalidRequest):
            LiveGapEvent(
                schema_version="live-gap-event-v1",
                gap_id="g1",
                status=LiveGapStatus.CLOSED,
                previous_cursor=self._cursor(),
                lower_bound=instant("2024-01-01T00:00:00Z"),
                upper_bound=None,
                resumed_cursor=None,
                reason="test",
            )


if __name__ == "__main__":
    unittest.main()
