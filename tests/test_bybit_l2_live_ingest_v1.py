"""Hermetic tests for Bybit live L2 ingest, checkpointing, and day roll (A14f v2).

Tests:
- LiveL2CheckpointV1 and advance_l2_checkpoint rules.
- L2CheckpointStore atomic save/load.
- Day roll continuation anchor validation (ADR-0070 Amendment 1 §9).
- BybitL2LiveIngestService batching, materialization, and day roll without synthetic snapshots.
- Restart recovery from durable checkpoint.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_l2_live_ingest import (  # noqa: E402
    BybitL2LiveIngestService,
    L2CheckpointCorruptError,
    L2CheckpointError,
    L2CheckpointRegressionError,
    L2CheckpointStore,
    L2ContinuationAnchorError,
    LiveL2CheckpointV1,
    PredecessorAnchorInfo,
    advance_l2_checkpoint,
    validate_continuation_anchor,
)
from quant_platform.data.models import DatasetIdentity, Instant, L2BookEvent, L2LevelChange  # noqa: E402
from quant_platform.data.publication import NaturalPartitionIdentity, SealedCatalogPartition  # noqa: E402
from quant_platform.source_adapters.bybit_l2_live import (  # noqa: E402
    BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
    canonicalize_bybit_l2_live_message,
)


def sample_event(
    *,
    event_type: str = "delta",
    u: int = 1001,
    seq: int = 5001,
    exchange_ts_iso: str = "2026-10-18T12:00:00.100Z",
) -> L2BookEvent:
    msg = {
        "topic": "orderbook.200.BTCUSDT",
        "type": event_type,
        "ts": 1705276800105,
        "cts": 1705276800100,
        "data": {
            "s": "BTCUSDT",
            "b": [["50000.00", "1.5"]],
            "a": [["50001.00", "2.0"]],
            "u": u,
            "seq": seq,
        },
    }
    event = canonicalize_bybit_l2_live_message(msg)
    # Override exchange_ts with requested ISO timestamp
    return L2BookEvent(
        venue=event.venue,
        market_type=event.market_type,
        instrument=event.instrument,
        native_symbol=event.native_symbol,
        source_channel=event.source_channel,
        acquisition_mode=event.acquisition_mode,
        event_type=event.event_type,
        exchange_ts=Instant.parse(exchange_ts_iso),
        provider_ts=event.provider_ts,
        source_depth_limit=event.source_depth_limit,
        native_sequence=event.native_sequence,
        native_update_id=event.native_update_id,
        continuity_token=event.continuity_token,
        bids=event.bids,
        asks=event.asks,
    )


class BybitL2LiveIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="quant_l2_live_test_")
        self.storage_root = Path(self.temp_dir) / "storage"
        self.checkpoint_path = Path(self.temp_dir) / "checkpoint" / "l2_checkpoint.json"
        self.checkpoint_store = L2CheckpointStore(self.checkpoint_path)
        self.identity = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checkpoint_store_save_and_load(self):
        self.assertIsNone(self.checkpoint_store.load())

        ckpt = LiveL2CheckpointV1(
            dataset_identity=self.identity,
            source_semantics_id=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            last_canonical_exchange_ts=Instant.parse("2026-10-18T12:00:00Z"),
            last_canonical_sequence=1000,
            last_canonical_update_id=500,
            catalog_dataset_id="canonical/l2/bybit/BTCUSDT/l2-book-event-v1",
            partition_key="dt=2026-10-18/seg=001",
            revision=1,
            partition_manifest_sha256="a" * 64,
            coverage_segment_id="cov-001",
            generation=1,
            created_at=Instant.parse("2026-10-18T12:00:01Z"),
        )
        self.checkpoint_store.save(ckpt)
        loaded = self.checkpoint_store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.last_canonical_sequence, 1000)
        self.assertEqual(loaded.last_canonical_update_id, 500)
        self.assertEqual(loaded.generation, 1)

    def test_checkpoint_advance_rules(self):
        c1 = LiveL2CheckpointV1(
            dataset_identity=self.identity,
            source_semantics_id=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            last_canonical_exchange_ts=Instant.parse("2026-10-18T12:00:00Z"),
            last_canonical_sequence=1000,
            last_canonical_update_id=500,
            catalog_dataset_id="canonical/l2/bybit/BTCUSDT/l2-book-event-v1",
            partition_key="dt=2026-10-18/seg=001",
            revision=1,
            partition_manifest_sha256="a" * 64,
            coverage_segment_id="cov-001",
            generation=1,
            created_at=Instant.parse("2026-10-18T12:00:01Z"),
        )
        advanced = advance_l2_checkpoint(None, c1)
        self.assertEqual(advanced, c1)

        # Idempotent re-advance
        same = advance_l2_checkpoint(c1, c1)
        self.assertEqual(same, c1)

        # Valid next generation
        c2 = LiveL2CheckpointV1(
            dataset_identity=self.identity,
            source_semantics_id=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            last_canonical_exchange_ts=Instant.parse("2026-10-18T12:00:05Z"),
            last_canonical_sequence=1005,
            last_canonical_update_id=505,
            catalog_dataset_id="canonical/l2/bybit/BTCUSDT/l2-book-event-v1",
            partition_key="dt=2026-10-18/seg=001",
            revision=1,
            partition_manifest_sha256="b" * 64,
            coverage_segment_id="cov-001",
            generation=2,
            created_at=Instant.parse("2026-10-18T12:00:06Z"),
        )
        self.assertEqual(advance_l2_checkpoint(c1, c2), c2)

        # Regressed sequence fails
        c_bad = LiveL2CheckpointV1(
            dataset_identity=self.identity,
            source_semantics_id=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            last_canonical_exchange_ts=Instant.parse("2026-10-18T12:00:05Z"),
            last_canonical_sequence=999,
            last_canonical_update_id=505,
            catalog_dataset_id="canonical/l2/bybit/BTCUSDT/l2-book-event-v1",
            partition_key="dt=2026-10-18/seg=001",
            revision=1,
            partition_manifest_sha256="b" * 64,
            coverage_segment_id="cov-001",
            generation=2,
            created_at=Instant.parse("2026-10-18T12:00:06Z"),
        )
        with self.assertRaises(L2CheckpointRegressionError):
            advance_l2_checkpoint(c1, c_bad)

    def test_validate_continuation_anchor(self):
        pred = PredecessorAnchorInfo(
            partition_key="dt=2026-10-18/seg=001",
            revision=1,
            final_sequence=1000,
            final_update_id=500,
            final_exchange_ts=Instant.parse("2026-10-18T23:59:59Z"),
            partition_manifest_sha256="c" * 64,
        )

        # Valid continuation delta
        cont_delta = sample_event(
            event_type="delta",
            u=501,
            seq=1001,
            exchange_ts_iso="2026-10-19T00:00:01Z",
        )
        validate_continuation_anchor(pred, cont_delta)  # Passes cleanly

        # Discontinuity in update_id fails
        cont_gap_u = sample_event(
            event_type="delta",
            u=505,
            seq=1002,
            exchange_ts_iso="2026-10-19T00:00:01Z",
        )
        with self.assertRaises(L2ContinuationAnchorError):
            validate_continuation_anchor(pred, cont_gap_u)

        # Sequence regression fails
        cont_reg_seq = sample_event(
            event_type="delta",
            u=501,
            seq=999,
            exchange_ts_iso="2026-10-19T00:00:01Z",
        )
        with self.assertRaises(L2ContinuationAnchorError):
            validate_continuation_anchor(pred, cont_reg_seq)

        # Snapshot at start of continuation partition is forbidden (must start with delta)
        cont_snap = sample_event(
            event_type="snapshot",
            u=501,
            seq=1001,
            exchange_ts_iso="2026-10-19T00:00:01Z",
        )
        with self.assertRaises(L2ContinuationAnchorError):
            validate_continuation_anchor(pred, cont_snap)

    def test_live_ingest_service_batch_and_checkpoint(self):
        service = BybitL2LiveIngestService(
            storage_root=self.storage_root,
            checkpoint_store=self.checkpoint_store,
            dataset_identity=self.identity,
            max_batch_size=3,
        )

        e1 = sample_event(event_type="snapshot", u=100, seq=1000, exchange_ts_iso="2026-10-18T10:00:00Z")
        e2 = sample_event(event_type="delta", u=101, seq=1001, exchange_ts_iso="2026-10-18T10:00:01Z")
        e3 = sample_event(event_type="delta", u=102, seq=1002, exchange_ts_iso="2026-10-18T10:00:02Z")

        p1 = service.feed_event(e1)
        self.assertEqual(len(p1), 0)  # Buffered
        p2 = service.feed_event(e2)
        self.assertEqual(len(p2), 0)  # Buffered
        p3 = service.feed_event(e3)
        self.assertEqual(len(p3), 1)  # Flushed at max_batch_size=3

        sealed = p3[0]
        self.assertEqual(sealed.partition_key, "dt=2026-10-18/seg=001")
        self.assertTrue(sealed.artifact_path.exists())
        self.assertTrue(sealed.partition_manifest_path.exists())
        self.assertTrue(sealed.coverage_manifest_path.exists())

        # Checkpoint is updated and persisted
        ckpt = self.checkpoint_store.load()
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt.last_canonical_sequence, 1002)
        self.assertEqual(ckpt.last_canonical_update_id, 102)
        self.assertEqual(ckpt.generation, 1)

    def test_live_ingest_service_day_roll_without_synthetic_snapshot(self):
        service = BybitL2LiveIngestService(
            storage_root=self.storage_root,
            checkpoint_store=self.checkpoint_store,
            dataset_identity=self.identity,
            max_batch_size=10,
        )

        # Day 1: snapshot + delta
        e1 = sample_event(event_type="snapshot", u=100, seq=1000, exchange_ts_iso="2026-10-18T23:59:58Z")
        e2 = sample_event(event_type="delta", u=101, seq=1001, exchange_ts_iso="2026-10-18T23:59:59Z")
        service.feed_event(e1)
        service.feed_event(e2)

        # Day 2: first event crosses midnight to 2026-10-19T00:00:01Z
        # Delta continues seamlessly (u=102, seq=1002)
        e3 = sample_event(event_type="delta", u=102, seq=1002, exchange_ts_iso="2026-10-19T00:00:01Z")
        partitions = service.feed_event(e3)

        # Day roll flushes Day 1
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0].partition_key, "dt=2026-10-18/seg=001")

        # Now flush Day 2 to verify continuation partition
        sealed_day2 = service.flush()
        self.assertIsNotNone(sealed_day2)
        self.assertEqual(sealed_day2.partition_key, "dt=2026-10-19/seg=001")

        # Checkpoint advanced to Day 2
        ckpt = self.checkpoint_store.load()
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt.partition_key, "dt=2026-10-19/seg=001")
        self.assertEqual(ckpt.last_canonical_sequence, 1002)
        self.assertEqual(ckpt.last_canonical_update_id, 102)

    def test_restart_recovery_from_durable_checkpoint(self):
        # 1. First service run: ingests up to seq=1002, u=102
        service1 = BybitL2LiveIngestService(
            storage_root=self.storage_root,
            checkpoint_store=self.checkpoint_store,
            dataset_identity=self.identity,
            max_batch_size=10,
        )
        service1.feed_event(sample_event(event_type="snapshot", u=100, seq=1000))
        service1.feed_event(sample_event(event_type="delta", u=101, seq=1001))
        service1.feed_event(sample_event(event_type="delta", u=102, seq=1002))
        service1.flush()

        # Checkpoint is at u=102, seq=1002
        self.assertEqual(self.checkpoint_store.load().last_canonical_sequence, 1002)

        # 2. Simulate process restart: create fresh service instance
        service2 = BybitL2LiveIngestService(
            storage_root=self.storage_root,
            checkpoint_store=self.checkpoint_store,
            dataset_identity=self.identity,
            max_batch_size=10,
        )
        # Verify it resumed into STREAMING anchored at checkpoint
        self.assertIsNotNone(service2.current_checkpoint)
        self.assertEqual(service2.state_machine.last_sequence, 1002)
        self.assertEqual(service2.state_machine.last_update_id, 102)

        # 3. Next message in live stream is delta u=103, seq=1003
        e_next = sample_event(event_type="delta", u=103, seq=1003, exchange_ts_iso="2026-10-18T10:05:00Z")
        service2.feed_event(e_next)
        sealed = service2.flush()
        self.assertIsNotNone(sealed)
        self.assertEqual(self.checkpoint_store.load().last_canonical_sequence, 1003)
        self.assertEqual(self.checkpoint_store.load().last_canonical_update_id, 103)

    def test_live_ingest_service_with_catalog_certification(self):
        writer = FakeCatalogWriter()
        service = BybitL2LiveIngestService(
            storage_root=self.storage_root,
            checkpoint_store=self.checkpoint_store,
            catalog_writer=writer,  # type: ignore[arg-type]
            dataset_identity=self.identity,
            max_batch_size=5,
        )
        e1 = sample_event(event_type="snapshot", u=100, seq=1000)
        e2 = sample_event(event_type="delta", u=101, seq=1001)
        service.feed_event(e1)
        service.feed_event(e2)
        res = service.flush()

        self.assertIsNotNone(res)
        self.assertEqual(len(writer.partitions), 1)
        self.assertEqual(len(writer.reports), 1)
        self.assertEqual(writer.reports[0]["status"], "pass")
        self.assertEqual(writer.reports[0]["violations"], [])
        self.assertEqual(writer.commits, 2)


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


if __name__ == "__main__":
    unittest.main()
