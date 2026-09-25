#!/usr/bin/env python3
"""Hermetic proof for ADR-0043's bounded live-ingest server v1 loop.

Composes the exact same restart/reconcile functions PR #122 already proved
on the real server (``bybit_live.run_real_server_restart_publish_phase`` /
``run_real_server_restart_phase``), faking only the network/catalog
boundaries exactly the way ``tests/test_bybit_live_checkpoint_v1.py`` does,
so the loop's own control flow (bootstrap -> steady-state cycle -> gap stop
-> clean stop) is exercised against real checkpoint persistence rather than
a second, parallel fake of the restart composition itself.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.bybit_live as app_bybit_live  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    DurablePublicationState,
    LiveProviderProofReport,
    RealServerPublishProofReport,
)
from quant_platform.application.live_ingest_server import (  # noqa: E402
    LiveIngestServerConfigV1,
    SERVER_GAP_DETECTED_AWAITING_REMEDIATION,
    SERVER_STOPPED,
    SESSION_CONTINUOUS,
    SESSION_RESUMED_WITH_EXPLICIT_GAP,
    run_live_ingest_server,
)
from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore  # noqa: E402
from quant_platform.operations.observability import SignalKind  # noqa: E402


def trade(timestamp: int, trade_id: str, sequence: str) -> TradeRecord:
    return TradeRecord("bybit", "BTCUSDT", Instant(timestamp), "100.00", "1", "buy", None, trade_id, sequence)


class LiveIngestServerLoopTests(unittest.TestCase):
    PATCHED = (
        "run_real_server_publish_proof",
        "fetch_recent_public_trades",
        "_publish_restart_reconciliation_records",
        "load_current_durable_publication_state",
    )

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.checkpoint_path = Path(self.tempdir.name) / "k10-checkpoint.json"
        self.publication = DurablePublicationState(
            catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-25",
            revision=1, partition_manifest_sha256="a" * 64,
        )
        self.config = LiveIngestServerConfigV1(
            storage_root="/srv/quant", storage_root_id="hot",
            checkpoint_path=self.checkpoint_path, producer="test", code_ref="test",
            max_messages_per_cycle=1, max_seconds_per_cycle=1,
        )
        self._originals = {name: getattr(app_bybit_live, name) for name in self.PATCHED}

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(app_bybit_live, name, value)
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
            artifact_path="/srv/quant/canonical/trades/bybit/BTCUSDT/trade-v1/dt=2026-09-25/part.parquet",
            coverage_status="complete", certification_status="pass",
            eligibility_published=True, datagateway_read_record_count=len(records),
            datagateway_read_matches_published=True, durable_publication=self.publication,
            coverage_id="coverage-1", coverage_assertion_id="coverage-assertion-1",
        )

    def _restart_publish_report(self, records, *, revision):
        return RealServerPublishProofReport(
            status="PASS",
            acquisition=LiveProviderProofReport(
                status="PASS", topic="publicTrade.BTCUSDT", messages=0,
                records=len(records), duplicates_removed=0, final_state="RECONCILED",
                errors=(), accepted_records=tuple(records), session_evidence=None,
            ),
            partition_key=self.publication.partition_key,
            artifact_path="/srv/quant/canonical/trades/bybit/BTCUSDT/trade-v1/dt=2026-09-25/restart.parquet",
            coverage_status="complete", certification_status="pass",
            eligibility_published=True, datagateway_read_record_count=len(records),
            datagateway_read_matches_published=True,
            durable_publication=DurablePublicationState(
                catalog_dataset_id=self.publication.catalog_dataset_id,
                partition_key=self.publication.partition_key,
                revision=revision, partition_manifest_sha256="b" * 64,
            ),
            coverage_id=f"coverage-{revision}", coverage_assertion_id=f"coverage-assertion-{revision}",
        )

    @staticmethod
    def _stop_after(n):
        state = {"calls": 0}

        def predicate():
            state["calls"] += 1
            return state["calls"] > n

        return predicate

    def test_bootstrap_cycle_then_steady_state_cycle_are_both_healthy(self):
        anchor = trade(1000, "anchor", "1")
        after = trade(1001, "after", "2")
        calls = {"publish": 0}

        def fake_publish(**_kwargs):
            calls["publish"] += 1
            return self._publish_report((anchor,))

        def fake_current_publication(*, checkpoint, dsn):
            return self.publication

        def fake_recent(*, limit):
            return (anchor, after)

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            self.assertEqual([r.trade_id for r in accepted_records], ["after"])
            self.assertEqual(reconcile_result.status.value, "CONTINUITY_RESTORED")
            return self._restart_publish_report(accepted_records, revision=2)

        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish

        report = run_live_ingest_server(self.config, stop_requested=self._stop_after(2))

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 2)
        self.assertEqual(calls["publish"], 1)  # bootstrap runs once; cycle 2 reuses the checkpoint

        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 2)
        self.assertEqual(checkpoint.last_canonical_trade_id, "after")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(len(health), 2)
        self.assertTrue(all(s.payload["health"] == "HEALTHY" for s in health))
        self.assertTrue(all(s.payload["session_state"] == SESSION_CONTINUOUS for s in health))

        lifecycle = [s for s in report.signals if s.kind is SignalKind.LIFECYCLE_TRANSITION]
        self.assertEqual(
            [(s.payload["previous_state"], s.payload["resulting_state"]) for s in lifecycle],
            [("STARTING", "RUNNING"), ("RUNNING", "STOPPED")],
        )

    def test_gap_detected_stops_loop_and_leaves_checkpoint_unadvanced(self):
        anchor = trade(1000, "anchor", "1")
        unrelated = trade(5000, "unrelated", "99")

        def fake_publish(**_kwargs):
            return self._publish_report((anchor,))

        def fake_current_publication(*, checkpoint, dsn):
            return self.publication

        def fake_recent(*, limit):
            return (unrelated,)  # does not contain the durable anchor -> UNRESOLVED_GAP

        def fail_restart_publish(**_kwargs):
            raise AssertionError("must not publish reconciliation records for an unresolved gap")

        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live._publish_restart_reconciliation_records = fail_restart_publish

        report = run_live_ingest_server(self.config, stop_requested=self._stop_after(2))

        self.assertEqual(report.status, SERVER_GAP_DETECTED_AWAITING_REMEDIATION)
        self.assertEqual(report.cycles, 2)

        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 1)  # unadvanced, per ADR-0042 S5/S6
        self.assertEqual(checkpoint.last_canonical_trade_id, "anchor")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(health[-1].payload["health"], "DEGRADED")
        self.assertEqual(health[-1].payload["session_state"], SESSION_RESUMED_WITH_EXPLICIT_GAP)

    def test_pending_acquisition_retries_without_failing(self):
        anchor = trade(1000, "anchor", "1")
        pending = RealServerPublishProofReport(
            status="LIVE_PROVIDER_PROOF_PENDING",
            acquisition=LiveProviderProofReport(
                status="LIVE_PROVIDER_PROOF_PENDING", topic="publicTrade.BTCUSDT",
                messages=0, records=0, duplicates_removed=0,
                final_state="DISCONNECTED", errors=("timeout",),
            ),
        )
        calls = {"n": 0}

        def fake_publish(**_kwargs):
            calls["n"] += 1
            return pending if calls["n"] == 1 else self._publish_report((anchor,))

        app_bybit_live.run_real_server_publish_proof = fake_publish

        report = run_live_ingest_server(self.config, stop_requested=self._stop_after(2))

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 2)
        self.assertTrue(self.checkpoint_path.exists())

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual([s.payload["health"] for s in health], ["DEGRADED", "HEALTHY"])

    def test_stop_before_any_cycle_runs_produces_zero_cycles(self):
        def fail_publish(**_kwargs):
            raise AssertionError("must not attempt acquisition when stop is requested immediately")

        app_bybit_live.run_real_server_publish_proof = fail_publish

        report = run_live_ingest_server(self.config, stop_requested=lambda: True)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 0)
        self.assertFalse(self.checkpoint_path.exists())

        lifecycle = [s for s in report.signals if s.kind is SignalKind.LIFECYCLE_TRANSITION]
        self.assertEqual(
            [(s.payload["previous_state"], s.payload["resulting_state"]) for s in lifecycle],
            [("STARTING", "RUNNING"), ("RUNNING", "STOPPED")],
        )


if __name__ == "__main__":
    unittest.main()
