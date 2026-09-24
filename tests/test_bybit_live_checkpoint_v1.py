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

import quant_platform.application.bybit_live as app_bybit_live  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    BybitRestartReconciliationCertificationProfile,
    DurablePublicationState,
    LiveProviderProofReport,
    RealServerPublishProofReport,
    next_checkpoint,
    resume_live_ingest,
    run_real_server_restart_proof,
)
from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import (  # noqa: E402
    CheckpointBindingError,
    CheckpointDomainMismatch,
    CheckpointError,
    CheckpointStore,
    LiveCheckpointV1,
    advance_checkpoint,
)
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    BybitLiveIntegrityError,
    ReconnectResult,
    ReconnectStatus,
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

    def test_omitting_durable_publication_with_an_existing_checkpoint_fails_closed(self):
        # ADR-0042 S5 step 1: validating the checkpoint against durable
        # publication state is a mandatory restart step. A caller must not
        # be able to skip it merely by omitting the argument.
        tempdir = tempfile.TemporaryDirectory()
        try:
            store = CheckpointStore(Path(tempdir.name) / "checkpoint.json")
            cp = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
            store.save(cp)
            with self.assertRaises(CheckpointError):
                resume_live_ingest(
                    checkpoint_store=store, recent_rest_records=(), buffered_ws_records=(),
                )
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

    def test_durable_anchor_outside_bounded_window_detects_explicit_gap(self):
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
            self.assertEqual(outcome.status, "GAP_DETECTED")
            self.assertEqual(outcome.accepted_records, ())
            self.assertEqual(outcome.reconcile_result.evidence["coverage_status"], "non_complete")
            # The checkpoint on disk is unchanged -- a gap can never be
            # silently crossed by advancing past it. GAP_DETECTED is only
            # an in-memory signal; durably recording the gap (e.g. via
            # build_bybit_live_coverage_document) is the caller's separate
            # responsibility, not something this call performs.
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

    def test_cross_domain_record_cannot_advance_a_checkpoint(self):
        # Finding 2 of Codex's adversarial review on PR #114: the candidate
        # keeps dataset_identity=previous.dataset_identity regardless of
        # what last_record actually is, so without an explicit check a
        # record from a different venue/instrument would silently advance
        # a checkpoint that still claims the original domain.
        publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        first = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=publication)
        wrong_instrument = replace(trade(1001, "after", "2"), instrument="ETHUSDT")
        with self.assertRaises(CheckpointDomainMismatch):
            next_checkpoint(
                first, last_record=wrong_instrument, last_observed_sequence="2",
                durable_publication=publication, coverage_segment_id="coverage-2",
                coverage_status="complete", created_at=Instant(2000),
            )
        wrong_venue = replace(trade(1001, "after", "2"), venue="coinbase")
        with self.assertRaises(CheckpointDomainMismatch):
            next_checkpoint(
                first, last_record=wrong_venue, last_observed_sequence="2",
                durable_publication=publication, coverage_segment_id="coverage-2",
                coverage_status="complete", created_at=Instant(2000),
            )

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


class RestartReconciliationCertificationProfileTests(unittest.TestCase):
    def test_restart_reconciliation_interval_is_rounded_to_coverage_microseconds(self):
        start, end = app_bybit_live._record_intent_interval((trade(1001, "after", "2"),))

        self.assertEqual(start, "1970-01-01T00:00:00.000001Z")
        self.assertEqual(end, "1970-01-01T00:00:00.000002Z")

    def test_restart_reconciliation_coverage_is_accepted_without_fake_session_evidence(self):
        record = trade(1001, "after", "2")
        result = ReconnectResult(
            status=ReconnectStatus.CONTINUITY_RESTORED,
            accepted_records=(record,),
            evidence={"reason": "last durable TradeKeyV1 found", "recent_window_records": 2},
        )
        coverage = app_bybit_live._build_restart_reconciliation_coverage_document(
            dataset_identity=IDENTITY,
            coverage_id="k10-restart-reconciliation-1",
            intent_start="1970-01-01T00:00:01Z",
            intent_end="1970-01-01T00:00:01.000000001Z",
            assertion_id="k10-restart-reconciliation-assertion-1",
            assertion_start="1970-01-01T00:00:01Z",
            assertion_end="1970-01-01T00:00:01.000000001Z",
            partition_key="dt=1970-01-01",
            revision=2,
            reconcile_result=result,
            created_at="1970-01-01T00:00:01.000000001Z",
            producer="test",
            code_ref="test",
        )
        profile = BybitRestartReconciliationCertificationProfile("test")

        source = profile.validate_source(IDENTITY, (coverage,))
        canonical = profile.validate_records(IDENTITY, (record,))

        self.assertEqual(source["source_semantics"], "bybit-recent-public-trades-v5-v1")
        self.assertEqual(source["mapping"], "bybit-recent-public-trades-v5-to-trade-v1")
        self.assertEqual(canonical["records"], 1)
        self.assertEqual(canonical["trade_id_policy"], "non-null-unique-(exchange_ts,trade_id)")


class RealServerRestartProofEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.checkpoint_path = Path(self.tempdir.name) / "k10-checkpoint.json"
        self.publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        self._original_publish = app_bybit_live.run_real_server_publish_proof
        self._original_recent = app_bybit_live.fetch_recent_public_trades
        self._original_resume = app_bybit_live.resume_live_ingest
        self._original_restart_publish = app_bybit_live._publish_restart_reconciliation_records
        self._original_load_publication = app_bybit_live.load_current_durable_publication_state
        self._original_connect_catalog = app_bybit_live._connect_catalog

    def tearDown(self):
        app_bybit_live.run_real_server_publish_proof = self._original_publish
        app_bybit_live.fetch_recent_public_trades = self._original_recent
        app_bybit_live.resume_live_ingest = self._original_resume
        app_bybit_live._publish_restart_reconciliation_records = self._original_restart_publish
        app_bybit_live.load_current_durable_publication_state = self._original_load_publication
        app_bybit_live._connect_catalog = self._original_connect_catalog
        self.tempdir.cleanup()

    def _publish_report(self, records):
        return RealServerPublishProofReport(
            status="PASS",
            acquisition=LiveProviderProofReport(
                status="PASS", topic="publicTrade.BTCUSDT", messages=1,
                records=len(records), duplicates_removed=0, final_state="ACQUIRING",
                errors=(), accepted_records=tuple(records), session_evidence=None,
            ),
            partition_key=self.publication.partition_key,
            artifact_path="/srv/quant/canonical/trades/bybit/BTCUSDT/trade-v1/dt=2026-09-24/part.parquet",
            coverage_status="complete",
            certification_status="pass",
            eligibility_published=True,
            datagateway_read_record_count=len(records),
            datagateway_read_matches_published=True,
            durable_publication=self.publication,
            coverage_id="coverage-1",
            coverage_assertion_id="coverage-assertion-1",
        )

    def _restart_publish_report(self, records):
        return RealServerPublishProofReport(
            status="PASS",
            acquisition=LiveProviderProofReport(
                status="PASS", topic="publicTrade.BTCUSDT", messages=0,
                records=len(records), duplicates_removed=0, final_state="RECONCILED",
                errors=(), accepted_records=tuple(records), session_evidence=None,
            ),
            partition_key=self.publication.partition_key,
            artifact_path="/srv/quant/canonical/trades/bybit/BTCUSDT/trade-v1/dt=2026-09-24/restart.parquet",
            coverage_status="complete",
            certification_status="pass",
            eligibility_published=True,
            datagateway_read_record_count=len(records),
            datagateway_read_matches_published=True,
            durable_publication=DurablePublicationState(
                catalog_dataset_id=self.publication.catalog_dataset_id,
                partition_key=self.publication.partition_key,
                revision=self.publication.revision + 1,
                partition_manifest_sha256="b" * 64,
            ),
            coverage_id="coverage-2",
            coverage_assertion_id="coverage-assertion-2",
        )

    def test_real_restart_proof_persists_checkpoint_and_resumes_from_recent_window(self):
        anchor = trade(1000, "anchor", "1")
        after = trade(1001, "after", "2")

        def fake_publish(**_kwargs):
            return self._publish_report((anchor,))

        def fake_recent(*, limit):
            self.assertEqual(limit, 1000)
            return (anchor, after)

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            self.assertEqual([record.trade_id for record in accepted_records], ["after"])
            self.assertEqual(reconcile_result.status.value, "CONTINUITY_RESTORED")
            return self._restart_publish_report(accepted_records)

        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish

        result = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(self.checkpoint_path.exists())
        self.assertEqual(result.restart_outcome.status, "RESUMED")
        self.assertEqual([record.trade_id for record in result.restart_outcome.accepted_records], ["after"])
        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 2)
        self.assertEqual(checkpoint.last_canonical_trade_id, "after")
        self.assertEqual(result.checkpoint_identity, checkpoint.checkpoint_identity)

    def test_real_restart_proof_does_not_checkpoint_when_publish_is_pending(self):
        def fake_publish(**_kwargs):
            return RealServerPublishProofReport(
                status="LIVE_PROVIDER_PROOF_PENDING",
                acquisition=LiveProviderProofReport(
                    status="LIVE_PROVIDER_PROOF_PENDING", topic="publicTrade.BTCUSDT",
                    messages=0, records=0, duplicates_removed=0,
                    final_state="DISCONNECTED", errors=("timeout",),
                ),
            )

        app_bybit_live.run_real_server_publish_proof = fake_publish

        result = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
        )

        self.assertEqual(result.status, "LIVE_PROVIDER_PROOF_PENDING")
        self.assertFalse(self.checkpoint_path.exists())

    def test_publish_phase_persists_checkpoint_without_restart_work(self):
        anchor = trade(1000, "anchor", "1")

        def fake_publish(**_kwargs):
            return self._publish_report((anchor,))

        def fail_recent(*, limit):
            raise AssertionError(f"publish phase must not fetch recent trades, got limit={limit}")

        def fail_resume(**_kwargs):
            raise AssertionError("publish phase must not run restart reconciliation")

        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live.fetch_recent_public_trades = fail_recent
        app_bybit_live.resume_live_ingest = fail_resume

        result = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
            phase="publish",
        )

        self.assertEqual(result.status, "CHECKPOINT_PERSISTED")
        self.assertTrue(self.checkpoint_path.exists())
        self.assertIsNone(result.restart_outcome)
        self.assertIsNotNone(result.checkpoint_identity)

    def test_restart_phase_publishes_reconciled_records_and_advances_checkpoint(self):
        anchor = trade(1000, "anchor", "1")
        after = trade(1001, "after", "2")
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))

        def fail_publish(**_kwargs):
            raise AssertionError("restart phase must not publish a fresh batch")

        def fake_recent(*, limit):
            self.assertEqual(limit, 1000)
            return (anchor, after)

        def fake_current_publication(*, checkpoint, dsn):
            self.assertEqual(checkpoint.catalog_dataset_id, self.publication.catalog_dataset_id)
            self.assertIsNone(dsn)
            return self.publication

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            self.assertEqual([record.trade_id for record in accepted_records], ["after"])
            self.assertEqual(reconcile_result.status.value, "CONTINUITY_RESTORED")
            return self._restart_publish_report(accepted_records)

        app_bybit_live.run_real_server_publish_proof = fail_publish
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish

        result = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
            phase="restart",
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.publication.status, "PASS")
        self.assertEqual(result.restart_outcome.status, "RESUMED")
        self.assertEqual([record.trade_id for record in result.restart_outcome.accepted_records], ["after"])
        advanced = store.load()
        self.assertEqual(advanced.generation, 2)
        self.assertEqual(advanced.last_canonical_trade_id, "after")
        self.assertEqual(result.checkpoint_identity, advanced.checkpoint_identity)

    def test_restart_phase_refuses_stale_catalog_binding_before_fetch(self):
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))
        stale = DurablePublicationState(
            catalog_dataset_id=self.publication.catalog_dataset_id,
            partition_key=self.publication.partition_key,
            revision=2,
            partition_manifest_sha256="b" * 64,
        )

        def fail_publish(**_kwargs):
            raise AssertionError("restart phase must not publish a fresh batch")

        def fake_current_publication(*, checkpoint, dsn):
            self.assertEqual(checkpoint.revision, 1)
            self.assertIsNone(dsn)
            return stale

        def fail_recent(*, limit):
            raise AssertionError(f"stale catalog binding must fail before recent fetch, got limit={limit}")

        app_bybit_live.run_real_server_publish_proof = fail_publish
        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fail_recent

        with self.assertRaises(CheckpointBindingError):
            run_real_server_restart_proof(
                max_messages=1,
                max_seconds=1,
                storage_root="/srv/quant",
                storage_root_id="hot",
                dsn=None,
                checkpoint_path=self.checkpoint_path,
                producer="test",
                code_ref="test",
                phase="restart",
            )

    def test_catalog_lookup_uses_eligible_states_and_newest_revision(self):
        checkpoint = first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication)
        executions = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, statement, params):
                executions.append((statement, params))

            def fetchall(self):
                return [
                    (self.publication.partition_key, 2, "b" * 64),
                    (self.publication.partition_key, 1, "a" * 64),
                ]

        class Connection:
            def __init__(self, outer):
                self.outer = outer
                self.closed = False

            def cursor(self):
                cursor = Cursor()
                cursor.publication = self.outer.publication
                return cursor

            def close(self):
                self.closed = True

        connection = Connection(self)

        def fake_connect(dsn):
            self.assertEqual(dsn, "postgresql://example")
            return connection

        app_bybit_live._connect_catalog = fake_connect

        durable = app_bybit_live.load_current_durable_publication_state(
            checkpoint=checkpoint,
            dsn="postgresql://example",
        )

        self.assertTrue(connection.closed)
        self.assertEqual(durable.revision, 2)
        self.assertEqual(durable.partition_manifest_sha256, "b" * 64)
        statement, params = executions[0]
        self.assertIn("p.state IN (%s, %s, %s)", statement)
        self.assertIn("ORDER BY p.revision DESC", statement)
        self.assertIn("LIMIT 1", statement)
        self.assertNotIn("p.state <> 'superseded'", statement)
        self.assertEqual(
            params,
            (
                self.publication.catalog_dataset_id,
                self.publication.partition_key,
                "valid",
                "closed",
                "degraded",
            ),
        )

    def test_restart_phase_without_checkpoint_is_explicit_and_does_not_fetch(self):
        def fail_recent(*, limit):
            raise AssertionError(f"missing-checkpoint restart must not fetch recent trades, got limit={limit}")

        app_bybit_live.fetch_recent_public_trades = fail_recent

        result = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
            phase="restart",
        )

        self.assertEqual(result.status, "NO_CHECKPOINT_TO_RESTART_FROM")
        self.assertEqual(result.checkpoint_path, str(self.checkpoint_path))
        self.assertFalse(self.checkpoint_path.exists())

    def test_publish_then_restart_phases_match_combined_resume_outcome(self):
        anchor = trade(1000, "anchor", "1")
        after = trade(1001, "after", "2")

        def fake_publish(**_kwargs):
            return self._publish_report((anchor,))

        app_bybit_live.run_real_server_publish_proof = fake_publish

        published = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
            phase="publish",
        )

        def fail_publish(**_kwargs):
            raise AssertionError("restart phase must not publish a fresh batch")

        def fake_recent(*, limit):
            self.assertEqual(limit, 1000)
            return (anchor, after)

        def fake_current_publication(*, checkpoint, dsn):
            self.assertEqual(checkpoint.catalog_dataset_id, self.publication.catalog_dataset_id)
            self.assertIsNone(dsn)
            return self.publication

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            return self._restart_publish_report(accepted_records)

        app_bybit_live.run_real_server_publish_proof = fail_publish
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish

        restarted = run_real_server_restart_proof(
            max_messages=1,
            max_seconds=1,
            storage_root="/srv/quant",
            storage_root_id="hot",
            dsn=None,
            checkpoint_path=self.checkpoint_path,
            producer="test",
            code_ref="test",
            phase="restart",
        )

        self.assertEqual(published.status, "CHECKPOINT_PERSISTED")
        self.assertEqual(restarted.status, "PASS")
        self.assertEqual(restarted.restart_outcome.status, "RESUMED")
        self.assertEqual([record.trade_id for record in restarted.restart_outcome.accepted_records], ["after"])
        self.assertEqual(CheckpointStore(self.checkpoint_path).load().generation, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
