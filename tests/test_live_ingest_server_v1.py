#!/usr/bin/env python3
"""Hermetic proof for ADR-0043's bounded live-ingest server v1 loop.

Composes the exact same restart/reconcile/publish functions PR #122 already
proved on the real server, faking only the network/catalog boundaries
exactly the way ``tests/test_bybit_live_checkpoint_v1.py`` does, so the
loop's own control flow (bootstrap -> steady-state live-acquisition cycles
-> drop-triggered reconcile -> explicit gap record -> new segment -> clean stop -> failure) is
exercised against real checkpoint persistence rather than a second, parallel
fake of the restart/publish composition itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
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
    SERVER_FAILED,
    SERVER_STOPPED,
    SESSION_CONTINUOUS,
    SESSION_RESUMED_WITH_EXPLICIT_GAP,
    run_live_ingest_server,
)
from quant_platform.data.models import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore, LiveCheckpointV1, advance_checkpoint  # noqa: E402
from quant_platform.operations.observability import SignalKind  # noqa: E402
from quant_platform.operations.pressure import PressurePolicyDefinition  # noqa: E402


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
            storage_root=self.tempdir.name, storage_root_id="hot",
            checkpoint_path=self.checkpoint_path,
            pressure_policy=PressurePolicyDefinition(
                max_capacity_observation_age=timedelta(seconds=300),
                pressure_available_bytes=1, critical_available_bytes=1, exhausted_available_bytes=0,
            ),
            producer="test", code_ref="test",
            max_messages_per_cycle=1, max_seconds_per_cycle=1,
            # 0 here so _stop_after's call-counting predicate stays 1:1 with
            # completed cycles; non-zero pacing calls stop_requested() again
            # per tick inside _wait_between_cycles, which the dedicated
            # pacing test below exercises with its own stop mechanism.
            cycle_interval_seconds=0.0,
        )
        self._sleeps: list[float] = []
        self._originals = {name: getattr(app_bybit_live, name) for name in self.PATCHED}

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(app_bybit_live, name, value)
        self.tempdir.cleanup()

    def _run(self, *, stop_after):
        return run_live_ingest_server(
            self.config, stop_requested=self._stop_after(stop_after), sleep=self._sleeps.append,
        )

    def _publish_report(self, records, *, revision=None):
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
            datagateway_read_matches_published=True,
            durable_publication=DurablePublicationState(
                catalog_dataset_id=self.publication.catalog_dataset_id,
                partition_key=self.publication.partition_key,
                revision=revision or self.publication.revision,
                partition_manifest_sha256=self.publication.partition_manifest_sha256,
            ),
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

    def test_bootstrap_then_steady_state_uses_live_acquisition_every_cycle(self):
        anchor, second, third = trade(1000, "anchor", "1"), trade(1001, "second", "2"), trade(1002, "third", "3")
        batches = [(anchor,), (second,), (third,)]

        def fake_publish(**_kwargs):
            return self._publish_report(batches.pop(0))

        def fail_recent(*, limit):
            raise AssertionError("steady-state cycles must not fall back to bounded reconciliation")

        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live.fetch_recent_public_trades = fail_recent

        report = self._run(stop_after=3)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 3)
        self.assertEqual(batches, [])  # every cycle called the live acquisition/publish path

        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 3)
        self.assertEqual(checkpoint.last_canonical_trade_id, "third")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(len(health), 3)
        self.assertTrue(all(s.payload["health"] == "HEALTHY" for s in health))
        self.assertTrue(all(s.payload["session_state"] == SESSION_CONTINUOUS for s in health))
        self.assertTrue(all(s.payload["phase"] == "acquire" for s in health))
        self.assertTrue(all("pressure_state" in s.payload for s in health))

    def test_pending_acquisition_triggers_reconcile_before_resuming_acquisition(self):
        anchor, after = trade(1000, "anchor", "1"), trade(1001, "after", "2")
        pending = RealServerPublishProofReport(
            status="LIVE_PROVIDER_PROOF_PENDING",
            acquisition=LiveProviderProofReport(
                status="LIVE_PROVIDER_PROOF_PENDING", topic="publicTrade.BTCUSDT",
                messages=0, records=0, duplicates_removed=0, final_state="DISCONNECTED", errors=("timeout",),
            ),
        )
        publish_calls = {"n": 0}

        def fake_publish(**_kwargs):
            publish_calls["n"] += 1
            if publish_calls["n"] == 1:
                return self._publish_report((anchor,))
            return pending  # cycle 2: simulated drop

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

        report = self._run(stop_after=3)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 3)
        self.assertEqual(publish_calls["n"], 2)  # cycle 3 reconciled instead of calling publish again

        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 2)
        self.assertEqual(checkpoint.last_canonical_trade_id, "after")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual([s.payload["health"] for s in health], ["HEALTHY", "DEGRADED", "HEALTHY"])
        self.assertEqual([s.payload["phase"] for s in health], ["acquire", "acquire", "reconcile"])

    def test_startup_with_existing_checkpoint_reconciles_before_steady_state(self):
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))
        after, third = trade(1001, "after", "2"), trade(1002, "third", "3")

        def fake_current_publication(*, checkpoint, dsn):
            return self.publication

        def fake_recent(*, limit):
            return (trade(1000, "anchor", "1"), after)

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            return self._restart_publish_report(accepted_records, revision=2)

        def fake_publish(**_kwargs):
            return self._publish_report((third,), revision=3)

        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish
        app_bybit_live.run_real_server_publish_proof = fake_publish

        report = self._run(stop_after=2)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 2)
        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 3)  # 1 (seeded) -> 2 (reconcile) -> 3 (steady-state acquire)
        self.assertEqual(checkpoint.last_canonical_trade_id, "third")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual([s.payload["phase"] for s in health], ["reconcile", "acquire"])

    def test_gap_detected_on_startup_records_non_complete_gap_and_starts_new_segment(self):
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))
        unrelated = trade(5000, "unrelated", "99")
        new_segment = trade(6000, "new-segment", "100")

        def fake_current_publication(*, checkpoint, dsn):
            return self.publication

        def fake_recent(*, limit):
            return (unrelated,)

        def fail_restart_publish(**_kwargs):
            raise AssertionError("must not publish reconciliation records for an unresolved gap")

        def fake_publish(**_kwargs):
            return self._publish_report((new_segment,), revision=2)

        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live._publish_restart_reconciliation_records = fail_restart_publish
        app_bybit_live.run_real_server_publish_proof = fake_publish

        report = self._run(stop_after=2)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 2)

        checkpoint = CheckpointStore(self.checkpoint_path).load()
        self.assertEqual(checkpoint.generation, 2)
        self.assertEqual(checkpoint.last_canonical_trade_id, "new-segment")

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual([s.payload["health"] for s in health], ["DEGRADED", "HEALTHY"])
        self.assertEqual([s.payload["phase"] for s in health], ["reconcile", "acquire"])
        self.assertTrue(all(s.payload["session_state"] == SESSION_RESUMED_WITH_EXPLICIT_GAP for s in health))
        self.assertEqual([s.payload["open_gap_count"] for s in health], [1, 1])
        self.assertEqual(health[0].payload["gap_state"], "REPAIR_SOURCE_UNPROVEN")
        manifest_path = Path(health[0].payload["gap_manifest_path"])
        self.assertTrue(manifest_path.exists())
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(document["assertions"][0]["status"], "known_gap")
        self.assertEqual(document["assertions"][0]["partitions"], [])
        self.assertIn("repair_state=REPAIR_SOURCE_UNPROVEN", document["assertions"][0]["evidence"][0]["detail"])

    def test_open_gap_observability_survives_restart_after_checkpoint_advances(self):
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))
        unrelated = trade(5000, "unrelated", "99")
        new_segment = trade(6000, "new-segment", "100")
        after_restart = trade(7000, "after-restart", "101")

        def fake_current_publication_v1(*, checkpoint, dsn):
            return self.publication

        def fake_recent_gap(*, limit):
            return (unrelated,)

        def fail_restart_publish(**_kwargs):
            raise AssertionError("must not publish reconciliation records for an unresolved gap")

        def fake_publish_v2(**_kwargs):
            return self._publish_report((new_segment,), revision=2)

        app_bybit_live.load_current_durable_publication_state = fake_current_publication_v1
        app_bybit_live.fetch_recent_public_trades = fake_recent_gap
        app_bybit_live._publish_restart_reconciliation_records = fail_restart_publish
        app_bybit_live.run_real_server_publish_proof = fake_publish_v2

        process_a = self._run(stop_after=2)
        process_a_health = [s for s in process_a.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(process_a_health[-1].payload["open_gap_count"], 1)

        publication_v2 = DurablePublicationState(
            catalog_dataset_id=self.publication.catalog_dataset_id,
            partition_key=self.publication.partition_key,
            revision=2,
            partition_manifest_sha256=self.publication.partition_manifest_sha256,
        )

        def fake_current_publication_v2(*, checkpoint, dsn):
            return publication_v2

        def fake_recent_resumed(*, limit):
            return (new_segment, after_restart)

        def fake_restart_publish(*, accepted_records, reconcile_result, **_kwargs):
            self.assertEqual([record.trade_id for record in accepted_records], ["after-restart"])
            return self._restart_publish_report(accepted_records, revision=3)

        app_bybit_live.load_current_durable_publication_state = fake_current_publication_v2
        app_bybit_live.fetch_recent_public_trades = fake_recent_resumed
        app_bybit_live._publish_restart_reconciliation_records = fake_restart_publish

        process_b = self._run(stop_after=1)

        self.assertEqual(process_b.status, SERVER_STOPPED)
        self.assertEqual(process_b.cycles, 1)
        process_b_health = [s for s in process_b.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(len(process_b_health), 1)
        self.assertEqual(process_b_health[0].payload["health"], "HEALTHY")
        self.assertEqual(process_b_health[0].payload["phase"], "reconcile")
        self.assertEqual(process_b_health[0].payload["session_state"], SESSION_RESUMED_WITH_EXPLICIT_GAP)
        self.assertEqual(process_b_health[0].payload["open_gap_count"], 1)
        self.assertEqual(
            process_b_health[0].payload["open_gap_coverage_ids"],
            process_a_health[-1].payload["open_gap_coverage_ids"],
        )

    def test_sustained_gap_updates_one_open_gap_chain_instead_of_appending_events(self):
        store = CheckpointStore(self.checkpoint_path)
        store.save(first_checkpoint(exchange_ts=1000, trade_id="anchor", publication=self.publication))
        unrelated = trade(5000, "unrelated", "99")
        pending = RealServerPublishProofReport(
            status="LIVE_PROVIDER_PROOF_PENDING",
            acquisition=LiveProviderProofReport(
                status="LIVE_PROVIDER_PROOF_PENDING", topic="publicTrade.BTCUSDT",
                messages=0, records=0, duplicates_removed=0, final_state="DISCONNECTED", errors=("timeout",),
            ),
        )

        def fake_current_publication(*, checkpoint, dsn):
            return self.publication

        def fake_recent(*, limit):
            return (unrelated,)

        def fake_publish(**_kwargs):
            return pending

        def fail_restart_publish(**_kwargs):
            raise AssertionError("must not publish reconciliation records for an unresolved gap")

        app_bybit_live.load_current_durable_publication_state = fake_current_publication
        app_bybit_live.fetch_recent_public_trades = fake_recent
        app_bybit_live.run_real_server_publish_proof = fake_publish
        app_bybit_live._publish_restart_reconciliation_records = fail_restart_publish

        report = self._run(stop_after=6)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 6)
        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual([s.payload["phase"] for s in health], [
            "reconcile", "acquire", "reconcile", "acquire", "reconcile", "acquire",
        ])
        self.assertEqual([s.payload["open_gap_count"] for s in health], [1, 1, 1, 1, 1, 1])
        gap_signals = [s for s in health if s.payload["phase"] == "reconcile"]
        self.assertIsNone(gap_signals[0].payload["gap_supersedes"])
        self.assertEqual(gap_signals[1].payload["gap_supersedes"], gap_signals[0].payload["gap_coverage_id"])
        self.assertEqual(gap_signals[2].payload["gap_supersedes"], gap_signals[1].payload["gap_coverage_id"])
        self.assertEqual(health[-1].payload["open_gap_coverage_ids"], (gap_signals[-1].payload["gap_coverage_id"],))

    def test_unhandled_cycle_exception_emits_failure_signal_and_stops(self):
        def raise_publish(**_kwargs):
            raise RuntimeError("simulated catalog outage")

        app_bybit_live.run_real_server_publish_proof = raise_publish

        report = self._run(stop_after=5)

        self.assertEqual(report.status, SERVER_FAILED)
        self.assertEqual(report.cycles, 1)
        failures = [s for s in report.signals if s.kind is SignalKind.FAILURE]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["failure_code"], "RuntimeError")
        self.assertEqual(failures[0].payload["context"]["message"], "simulated catalog outage")

    def test_corrupt_startup_checkpoint_emits_failure_signal_and_stops(self):
        self.checkpoint_path.write_text("not valid json", encoding="utf-8")

        def fail_publish(**_kwargs):
            raise AssertionError("must not attempt acquisition once the checkpoint load fails")

        app_bybit_live.run_real_server_publish_proof = fail_publish

        report = self._run(stop_after=5)

        self.assertEqual(report.status, SERVER_FAILED)
        self.assertEqual(report.cycles, 1)
        failures = [s for s in report.signals if s.kind is SignalKind.FAILURE]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["failure_code"], "CheckpointCorruptError")

        lifecycle = [s for s in report.signals if s.kind is SignalKind.LIFECYCLE_TRANSITION]
        self.assertEqual(
            [(s.payload["previous_state"], s.payload["resulting_state"]) for s in lifecycle],
            [("STARTING", "RUNNING"), ("RUNNING", "FAILED")],
        )

    def test_capacity_and_pressure_evidence_is_folded_into_health_signals(self):
        anchor = trade(1000, "anchor", "1")

        def fake_publish(**_kwargs):
            return self._publish_report((anchor,))

        app_bybit_live.run_real_server_publish_proof = fake_publish

        report = self._run(stop_after=1)

        health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
        self.assertEqual(len(health), 1)
        self.assertIn("capacity_available_bytes", health[0].payload)
        self.assertIn("pressure_state", health[0].payload)
        evidence_kinds = {e.evidence_kind for e in health[0].evidence}
        self.assertIn("pressure_decision", evidence_kinds)

    def test_stop_before_any_cycle_runs_produces_zero_cycles(self):
        def fail_publish(**_kwargs):
            raise AssertionError("must not attempt acquisition when stop is requested immediately")

        app_bybit_live.run_real_server_publish_proof = fail_publish

        report = run_live_ingest_server(self.config, stop_requested=lambda: True, sleep=self._sleeps.append)

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 0)
        self.assertFalse(self.checkpoint_path.exists())
        self.assertEqual(self._sleeps, [])

        lifecycle = [s for s in report.signals if s.kind is SignalKind.LIFECYCLE_TRANSITION]
        self.assertEqual(
            [(s.payload["previous_state"], s.payload["resulting_state"]) for s in lifecycle],
            [("STARTING", "RUNNING"), ("RUNNING", "STOPPED")],
        )

    def test_cycle_interval_paces_completed_cycles_via_injected_sleep(self):
        # cycle_interval_seconds=0 in the shared config keeps _stop_after's
        # call-counting predicate 1:1 with completed cycles (see setUp); this
        # test needs real pacing, so it uses its own signal-driven stop
        # instead, which stays correct regardless of how many times
        # stop_requested() is polled inside a pacing wait.
        anchor, second = trade(1000, "anchor", "1"), trade(1001, "second", "2")
        batches = [(anchor,), (second,)]

        def fake_publish(**_kwargs):
            return self._publish_report(batches.pop(0))

        app_bybit_live.run_real_server_publish_proof = fake_publish

        config = replace(self.config, cycle_interval_seconds=2.0)
        stop = {"flag": False}
        seen = {"n": 0}

        def on_signal(signal):
            if signal.kind is SignalKind.HEALTH_SNAPSHOT:
                seen["n"] += 1
                if seen["n"] >= 2:
                    stop["flag"] = True

        report = run_live_ingest_server(
            config, stop_requested=lambda: stop["flag"], on_signal=on_signal, sleep=self._sleeps.append,
        )

        self.assertEqual(report.status, SERVER_STOPPED)
        self.assertEqual(report.cycles, 2)
        # Cycle 1 completes and paces the full interval; cycle 2's health
        # signal flips the stop flag before its own pacing wait begins, so
        # it sleeps nothing -- one full interval's worth of ticks in total.
        self.assertEqual(sum(self._sleeps), config.cycle_interval_seconds)


if __name__ == "__main__":
    unittest.main()
