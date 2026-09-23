#!/usr/bin/env python3
"""Hermetic proof for K10 (#109) restart composition: application.bybit_live's
resume_live_ingest/next_checkpoint against real A11 primitives
(reconcile_after_disconnect, TradeKeyV1, BybitLiveIntegrityError) -- no
network or catalog connection. Complements test_operations_checkpoint_v1.py's
pure-seam proof with the parts ADR-0042 S5 requires actually composing A11's
existing bounded reconciliation, never a second recovery path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_live import (  # noqa: E402
    DurablePublicationState,
    next_checkpoint,
    resume_live_ingest,
)
from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import (  # noqa: E402
    CheckpointBindingError,
    CheckpointError,
    CheckpointStore,
    LiveCheckpointV1,
    advance_checkpoint,
)
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    BybitLiveIntegrityError,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
SEMANTICS = "bybit-public-trades-websocket-v1"


def trade(timestamp: int, trade_id: str, sequence: str) -> TradeRecord:
    return TradeRecord("bybit", "BTCUSDT", Instant(timestamp), "100.00", "1", "buy", None, trade_id, sequence)


def first_checkpoint(*, exchange_ts: int, trade_id: str, publication: DurablePublicationState) -> LiveCheckpointV1:
    candidate = LiveCheckpointV1(
        dataset_identity=IDENTITY, source_semantics_id=SEMANTICS,
        last_canonical_exchange_ts=Instant(exchange_ts), last_canonical_trade_id=trade_id,
        last_observed_sequence=None, catalog_dataset_id=publication.catalog_dataset_id,
        partition_key=publication.partition_key, revision=publication.revision,
        partition_manifest_sha256=publication.partition_manifest_sha256,
        coverage_segment_id="coverage-1", generation=1, created_at=Instant(0),
    )
    return advance_checkpoint(None, candidate)


class ResumeLiveIngestTests(unittest.TestCase):
    def setUp(self):
        self.publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )

    def test_no_checkpoint_means_first_acquisition_not_a_restart(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            outcome = resume_live_ingest(
                checkpoint_store=store, recent_rest_records=(), buffered_ws_records=(),
            )
            self.assertEqual(outcome.status, "NO_CHECKPOINT")
            self.assertIsNone(outcome.checkpoint)
        finally:
            tempdir.cleanup()

    def test_bounded_reconnect_resumes_when_durable_anchor_is_recoverable(self):
        # Proof matrix items 1-4, 12: crash at any point before/after
        # checkpoint advance, anchor still inside the bounded REST window ->
        # continuity re-established, accepted records ready to republish.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)

            anchor = trade(1000, "anchor", "1")
            after = trade(1001, "after", "2")
            outcome = resume_live_ingest(
                checkpoint_store=store,
                recent_rest_records=(anchor, after),
                buffered_ws_records=(),
                durable_publication=self.publication,
            )
            self.assertEqual(outcome.status, "RESUMED")
            self.assertEqual([r.trade_id for r in outcome.accepted_records], ["after"])
        finally:
            tempdir.cleanup()

    def test_durable_anchor_outside_bounded_window_records_explicit_gap(self):
        # Proof matrix item 13: durable anchor outside bounded window ->
        # explicit non-complete gap, never fabricated completeness. The
        # checkpoint itself must not advance past a gap like this.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)

            unrelated = trade(5000, "far-away", "99")
            outcome = resume_live_ingest(
                checkpoint_store=store, recent_rest_records=(unrelated,), buffered_ws_records=(),
                durable_publication=self.publication,
            )
            self.assertEqual(outcome.status, "GAP_RECORDED")
            self.assertEqual(outcome.accepted_records, ())
            self.assertEqual(outcome.reconcile_result.evidence["coverage_status"], "non_complete")
            # The checkpoint on disk is unchanged -- a gap can never be
            # silently crossed by advancing past it.
            self.assertEqual(store.load().checkpoint_identity, cp.checkpoint_identity)
        finally:
            tempdir.cleanup()

    def test_duplicate_rest_and_ws_observation_collapses_to_one_canonical_event(self):
        # Proof matrix item 10.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)

            anchor = trade(1000, "anchor", "1")
            after = trade(1001, "after", "2")
            outcome = resume_live_ingest(
                checkpoint_store=store,
                recent_rest_records=(anchor, after),
                buffered_ws_records=(replace(after),),  # same key+payload, observed twice
                durable_publication=self.publication,
            )
            self.assertEqual(outcome.status, "RESUMED")
            self.assertEqual([r.trade_id for r in outcome.accepted_records], ["after"])
        finally:
            tempdir.cleanup()

    def test_conflicting_payload_for_same_key_fails_closed_via_a11(self):
        # Proof matrix item 11: same TradeKey with conflicting payload fails
        # closed via A11's existing integrity semantics -- K10 must not
        # swallow or bypass BybitLiveIntegrityError.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)

            anchor = trade(1000, "anchor", "1")
            after = trade(1001, "after", "2")
            conflicting = replace(after, size="999")
            with self.assertRaises(BybitLiveIntegrityError):
                resume_live_ingest(
                    checkpoint_store=store,
                    recent_rest_records=(anchor, after),
                    buffered_ws_records=(conflicting,),
                    durable_publication=self.publication,
                )
        finally:
            tempdir.cleanup()

    def test_stale_publication_binding_is_refused_before_reconciling(self):
        # Proof matrix items 5/8: a checkpoint bound to a superseded
        # publication generation is refused before any reconnect is even
        # attempted.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)

            superseded = DurablePublicationState(
                catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
                revision=2, partition_manifest_sha256="b" * 64,
            )
            with self.assertRaises(CheckpointBindingError):
                resume_live_ingest(
                    checkpoint_store=store, recent_rest_records=(), buffered_ws_records=(),
                    durable_publication=superseded,
                )
        finally:
            tempdir.cleanup()


class NextCheckpointTests(unittest.TestCase):
    def test_advances_generation_from_one_more_durable_record(self):
        publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        first = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=publication)
        new_record = trade(1001, "after", "2")
        second = next_checkpoint(
            first, last_record=new_record, last_observed_sequence="2",
            durable_publication=publication, coverage_segment_id="coverage-2",
            coverage_status="complete", created_at=Instant(2000),
        )
        self.assertEqual(second.generation, 2)
        self.assertEqual(second.last_canonical_trade_id, "after")

    def test_regression_is_still_refused_through_next_checkpoint(self):
        publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        first = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=publication)
        earlier_record = trade(999, "before", "0")
        with self.assertRaises(Exception):
            next_checkpoint(
                first, last_record=earlier_record, last_observed_sequence="0",
                durable_publication=publication, coverage_segment_id="coverage-1",
                coverage_status="complete", created_at=Instant(2000),
            )

    def test_known_gap_coverage_segment_cannot_advance_checkpoint(self):
        # Proof matrix item 14: unresolved coverage interruption cannot be
        # crossed by checkpoint advancement, even though the underlying
        # publication itself is real and durable.
        publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        first = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=publication)
        new_record = trade(1001, "after", "2")
        with self.assertRaises(CheckpointError):
            next_checkpoint(
                first, last_record=new_record, last_observed_sequence="2",
                durable_publication=publication, coverage_segment_id="coverage-2",
                coverage_status="known_gap", created_at=Instant(2000),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
