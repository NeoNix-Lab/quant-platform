"""DG-N T02 part 3: one live-ingest server process runs one named instrument (ADR-0074 sections 2 and 3).

Hermetic, with the same doubles as ``test_live_ingest_server_v1.py``: the subject of every signal, the instrument reaching the
phases, a checkpoint of another instrument ending the loop, the explicit gap evidence kept per instrument, and the command line.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import live_ingest_server as cli  # noqa: E402
import quant_platform.application.bybit_live as app_bybit_live  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    DurablePublicationState,
    LiveProviderProofReport,
    RealServerPublishProofReport,
)
from quant_platform.application.live_gap_orchestration import (  # noqa: E402
    LongGapInterval,
    TradeKeyBoundary,
    load_open_long_gap_records,
    record_explicit_long_gap,
)
from quant_platform.application.live_ingest_server import (  # noqa: E402
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    SERVER_FAILED,
    SERVER_STOPPED,
    LiveIngestServerConfigV1,
    run_live_ingest_server,
)
from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore, LiveCheckpointV1, advance_checkpoint  # noqa: E402
from quant_platform.operations.observability import SignalKind  # noqa: E402
from quant_platform.operations.pressure import PressurePolicyDefinition  # noqa: E402
from quant_platform.source_adapters.bybit_live import BybitLiveSourceError, bybit_live_dataset_identity  # noqa: E402

ALL = BYBIT_LINEAR_TRADE_INSTRUMENTS_V1
NOT_BTC = tuple(item for item in ALL if item != "BTCUSDT")
SEMANTICS = "bybit-public-trades-websocket-v1"
POLICY = PressurePolicyDefinition(
    max_capacity_observation_age=timedelta(seconds=300), pressure_available_bytes=1, critical_available_bytes=1,
    exhausted_available_bytes=0,
)


def trade(instrument: str, ts: int, trade_id: str) -> TradeRecord:
    return TradeRecord("bybit", instrument, Instant(ts), "100.00", "1", "buy", None, trade_id, "1")


def checkpoint_for(instrument: str) -> LiveCheckpointV1:
    return advance_checkpoint(None, LiveCheckpointV1(
        dataset_identity=bybit_live_dataset_identity(instrument), source_semantics_id=SEMANTICS,
        last_canonical_exchange_ts=Instant(1000), last_canonical_trade_id="anchor", last_observed_sequence=None,
        catalog_dataset_id=f"dataset-{instrument}", partition_key="dt=2026-09-25", revision=1,
        partition_manifest_sha256="a" * 64, coverage_segment_id="coverage-1", generation=1, created_at=Instant(0),
    ))


def publish_report(instrument: str, records, revision: int = 1) -> RealServerPublishProofReport:
    return RealServerPublishProofReport(
        status="PASS",
        acquisition=LiveProviderProofReport(
            status="PASS", topic=f"publicTrade.{instrument}", messages=1, records=len(records), duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=tuple(records), session_evidence=None,
        ),
        partition_key="dt=2026-09-25", coverage_status="complete", certification_status="pass",
        durable_publication=DurablePublicationState(
            catalog_dataset_id=f"dataset-{instrument}", partition_key="dt=2026-09-25", revision=revision,
            partition_manifest_sha256="a" * 64,
        ),
        coverage_id=f"coverage-{revision}",
    )


def stop_after(n: int):
    state = {"calls": 0}

    def predicate() -> bool:
        state["calls"] += 1
        return state["calls"] > n

    return predicate


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def config(self, *, checkpoint: str = "checkpoint.json", **extra) -> LiveIngestServerConfigV1:
        return LiveIngestServerConfigV1(
            storage_root=self.root, storage_root_id="hot", checkpoint_path=self.root / checkpoint, pressure_policy=POLICY,
            producer="test", code_ref="test", max_messages_per_cycle=1, max_seconds_per_cycle=1, cycle_interval_seconds=0.0,
            **extra,
        )

    def run_server(self, config: LiveIngestServerConfigV1, cycles: int = 1):
        return run_live_ingest_server(config, stop_requested=stop_after(cycles), sleep=lambda _seconds: None)


class ConfigAndSubjectTests(ServerCase):
    def test_the_default_instrument_is_btcusdt_and_its_subject_is_unchanged(self) -> None:
        config = self.config()
        self.assertEqual("BTCUSDT", config.instrument)
        with mock.patch.object(app_bybit_live, "run_real_server_publish_proof", lambda **kw: publish_report(kw["instrument"], (trade("BTCUSDT", 1001, "t1"),))):
            report = self.run_server(config)
        self.assertEqual({"bybit:BTCUSDT:hot"}, {signal.subject.subject_identity for signal in report.signals})

    def test_every_signal_of_an_instrument_names_that_instrument(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                config = self.config(instrument=instrument, checkpoint=f"{instrument}.json")
                publish = lambda **kw: publish_report(kw["instrument"], (trade(kw["instrument"], 1001, "t1"),))  # noqa: E731
                with mock.patch.object(app_bybit_live, "run_real_server_publish_proof", publish):
                    report = self.run_server(config)
                kinds = {signal.kind for signal in report.signals}
                self.assertIn(SignalKind.HEALTH_SNAPSHOT, kinds)
                self.assertEqual({f"bybit:{instrument}:hot"}, {signal.subject.subject_identity for signal in report.signals})
                self.assertEqual(SERVER_STOPPED, report.status)

    def test_an_instrument_outside_the_allow_list_is_refused_when_the_config_is_built(self) -> None:
        for instrument in ("BTCUSD", "BTCUSDC", "ADAUSDT", "ethusdt", "", None):
            with self.subTest(instrument=instrument):
                with self.assertRaises(BybitLiveSourceError):
                    self.config(instrument=instrument)


class PhaseTests(ServerCase):
    def test_the_steady_state_phase_receives_the_instrument(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                seen: list[str] = []

                def publish(**kwargs):
                    seen.append(kwargs["instrument"])
                    return publish_report(kwargs["instrument"], (trade(kwargs["instrument"], 1001, "t1"),))

                with mock.patch.object(app_bybit_live, "run_real_server_publish_proof", publish):
                    self.run_server(self.config(instrument=instrument, checkpoint=f"{instrument}.json"))
                self.assertEqual([instrument], seen)

    def test_the_reconcile_phase_receives_the_instrument(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                path = self.root / f"{instrument}-restart.json"
                CheckpointStore(path).save(checkpoint_for(instrument))
                seen: dict = {}

                def restart(**kwargs):
                    seen.update(kwargs)
                    return app_bybit_live.RealServerRestartProofReport(status="REAL_RESTART_PROOF_PENDING", publication=None)

                with mock.patch.object(app_bybit_live, "run_real_server_restart_phase", restart):
                    report = self.run_server(self.config(instrument=instrument, checkpoint=path.name))
                self.assertEqual(instrument, seen["instrument"])
                self.assertEqual(SERVER_STOPPED, report.status)


class CheckpointDomainTests(ServerCase):
    def test_a_checkpoint_of_another_instrument_ends_the_loop_before_anything_is_acquired(self) -> None:
        for owner, intruder in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT"), ("SOLUSDT", "DOGEUSDT")):
            with self.subTest(owner=owner, intruder=intruder):
                path = self.root / f"{owner}-{intruder}.json"
                CheckpointStore(path).save(checkpoint_for(owner))
                before = path.read_bytes()
                with mock.patch.object(app_bybit_live, "run_real_server_publish_proof", side_effect=AssertionError("acquired")) as publish, \
                        mock.patch.object(app_bybit_live, "fetch_recent_public_trades", side_effect=AssertionError("provider")) as provider, \
                        mock.patch.object(app_bybit_live, "load_current_durable_publication_state", side_effect=AssertionError("catalog")) as catalog:
                    report = self.run_server(self.config(instrument=intruder, checkpoint=path.name), cycles=3)
                    publish.assert_not_called()
                    provider.assert_not_called()
                    catalog.assert_not_called()
                self.assertEqual(SERVER_FAILED, report.status)
                failures = [s for s in report.signals if s.kind is SignalKind.FAILURE]
                self.assertEqual(["CheckpointDomainMismatch"], [s.payload["failure_code"] for s in failures])
                self.assertEqual(f"bybit:{intruder}:hot", failures[0].subject.subject_identity)
                self.assertEqual(before, path.read_bytes())


class GapEvidenceTests(ServerCase):
    @staticmethod
    def interval(instrument: str) -> LongGapInterval:
        return LongGapInterval(
            start_key=TradeKeyBoundary(venue="bybit", instrument=instrument, exchange_ts=Instant(1_000_000_000),
                                       trade_id="anchor"),
            detected_at=Instant(9_000_000_000), reason="anchor is outside the recent window", recent_window_records=3,
            buffered_ws_records=0,
        )

    def dataset_dir(self, instrument: str) -> Path:
        return self.root / "canonical" / "trades" / "bybit" / instrument / "trade-v1"

    def record(self, anchor: str, **kwargs):
        return record_explicit_long_gap(
            storage_root=self.root, interval=self.interval(anchor), producer="p", code_ref="c",
            detected_at=Instant(9_000_000_000), **kwargs,
        )

    def test_the_default_is_the_btcusdt_directory(self) -> None:
        record = self.record("BTCUSDT")
        self.assertEqual(self.dataset_dir("BTCUSDT"), Path(record.coverage_manifest_path).parent)

    def test_each_instrument_writes_and_reads_only_its_own_directory(self) -> None:
        records = {instrument: self.record(instrument, instrument=instrument) for instrument in ALL}
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                self.assertEqual(self.dataset_dir(instrument), Path(records[instrument].coverage_manifest_path).parent)
                loaded = load_open_long_gap_records(storage_root=self.root, instrument=instrument)
                self.assertEqual([records[instrument].coverage_id], [r.coverage_id for r in loaded.records])
                document = json.loads(Path(records[instrument].coverage_manifest_path).read_text(encoding="utf-8"))
                self.assertEqual(instrument, document["instrument"])
        # the default loader sees BTCUSDT's evidence only
        self.assertEqual([records["BTCUSDT"].coverage_id],
                         [r.coverage_id for r in load_open_long_gap_records(storage_root=self.root).records])

    def test_an_instrument_without_evidence_loads_nothing_while_another_has_some(self) -> None:
        self.record("ETHUSDT", instrument="ETHUSDT")
        self.assertEqual((), load_open_long_gap_records(storage_root=self.root, instrument="SOLUSDT").records)
        self.assertEqual((), load_open_long_gap_records(storage_root=self.root).records)

    def test_a_gap_anchor_of_another_instrument_is_never_recorded(self) -> None:
        for anchor, wanted in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT")):
            with self.subTest(anchor=anchor, wanted=wanted):
                with self.assertRaisesRegex(ValueError, "gap evidence never goes into another"):
                    record_explicit_long_gap(
                        storage_root=self.root, interval=self.interval(anchor), producer="p", code_ref="c",
                        detected_at=Instant(9_000_000_000), instrument=wanted)
                self.assertFalse(self.dataset_dir(wanted).exists())

    def test_a_server_starts_with_the_open_gaps_of_its_own_instrument_only(self) -> None:
        btc_gap = self.record("BTCUSDT")
        eth_gap = self.record("ETHUSDT", instrument="ETHUSDT")

        def first_health(instrument: str | None, name: str):
            extra = {} if instrument is None else {"instrument": instrument}
            config = self.config(checkpoint=name, **extra)
            publish = lambda **kw: publish_report(kw["instrument"], (trade(kw["instrument"], 1001, "t1"),))  # noqa: E731
            with mock.patch.object(app_bybit_live, "run_real_server_publish_proof", publish):
                report = self.run_server(config)
            return [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT][0]

        for instrument, name, gap in ((None, "btc.json", btc_gap), ("ETHUSDT", "eth.json", eth_gap)):
            with self.subTest(instrument=instrument or "BTCUSDT"):
                health = first_health(instrument, name)
                self.assertEqual(1, health.payload["open_gap_count"])
                self.assertEqual("RESUMED_WITH_EXPLICIT_GAP", health.payload["session_state"])
                self.assertEqual([gap.coverage_id], [item.evidence_identity for item in health.evidence
                                                      if item.evidence_kind == "coverage_manifest"])
        with self.subTest(instrument="SOLUSDT"):
            health = first_health("SOLUSDT", "sol.json")
            self.assertNotIn("open_gap_count", health.payload)
            self.assertEqual("CONTINUOUS", health.payload["session_state"])
            self.assertEqual([], [item for item in health.evidence if item.evidence_kind == "coverage_manifest"])

    def test_the_server_records_a_startup_gap_in_its_own_instruments_directory(self) -> None:
        for instrument in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
            with self.subTest(instrument=instrument):
                root = self.root / instrument
                root.mkdir()
                path = root / "checkpoint.json"
                CheckpointStore(path).save(checkpoint_for(instrument))
                publication = DurablePublicationState(
                    catalog_dataset_id=f"dataset-{instrument}", partition_key="dt=2026-09-25", revision=1,
                    partition_manifest_sha256="a" * 64)
                new_segment = trade(instrument, 6000, "new-segment")
                with mock.patch.object(app_bybit_live, "load_current_durable_publication_state", lambda **kw: publication), \
                        mock.patch.object(app_bybit_live, "fetch_recent_public_trades",
                                          lambda *, limit, instrument: (trade(instrument, 5000, "unrelated"),)), \
                        mock.patch.object(app_bybit_live, "_publish_restart_reconciliation_records", side_effect=AssertionError("gap")), \
                        mock.patch.object(app_bybit_live, "run_real_server_publish_proof",
                                          lambda **kw: publish_report(kw["instrument"], (new_segment,), revision=2)):
                    config = LiveIngestServerConfigV1(
                        storage_root=root, storage_root_id="hot", checkpoint_path=path, pressure_policy=POLICY,
                        producer="test", code_ref="test", max_messages_per_cycle=1, max_seconds_per_cycle=1,
                        cycle_interval_seconds=0.0, **({} if instrument == "BTCUSDT" else {"instrument": instrument}))
                    report = run_live_ingest_server(config, stop_requested=stop_after(2), sleep=lambda _s: None)
                self.assertEqual(SERVER_STOPPED, report.status)
                health = [s for s in report.signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
                self.assertEqual("DEGRADED", health[0].payload["health"])
                manifest = Path(health[0].payload["gap_manifest_path"])
                expected_dir = root / "canonical" / "trades" / "bybit" / instrument / "trade-v1"
                self.assertEqual(expected_dir, manifest.parent)
                others = [item for item in ALL if item != instrument]
                for other in others:
                    self.assertFalse((root / "canonical" / "trades" / "bybit" / other).exists())


class CommandLineTests(unittest.TestCase):
    ARGS = ["--storage-root", "/srv/marketdata", "--checkpoint-path", "/srv/marketdata/canonical/_k10_checkpoints/x.json"]

    def run_cli(self, *extra: str):
        captured: dict = {}

        def fake_run(config, *, stop_requested, on_signal):
            captured["config"] = config
            return type("Report", (), {"status": cli.SERVER_STOPPED, "cycles": 0, "signals": ()})()

        out = io.StringIO()
        with mock.patch.object(cli, "run_live_ingest_server", fake_run), redirect_stdout(out):
            rc = cli.main([*self.ARGS, *extra])
        return rc, captured["config"], out.getvalue()

    def test_every_instrument_reaches_the_config_and_the_banner(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                rc, config, output = self.run_cli("--instrument", instrument)
                self.assertEqual((0, instrument), (rc, config.instrument))
                self.assertIn(f"instrument={instrument}\n", output)

    def test_without_the_option_it_is_btcusdt(self) -> None:
        rc, config, output = self.run_cli()
        self.assertEqual((0, "BTCUSDT"), (rc, config.instrument))
        self.assertIn("instrument=BTCUSDT\n", output)

    def test_anything_outside_the_allow_list_is_a_usage_error(self) -> None:
        for instrument in ("BTCUSD", "BTCUSDC", "ADAUSDT", "ethusdt", ""):
            with self.subTest(instrument=instrument):
                with redirect_stderr(io.StringIO()), mock.patch.object(cli, "run_live_ingest_server") as run:
                    with self.assertRaises(SystemExit) as raised:
                        cli.main([*self.ARGS, "--instrument", instrument])
                    self.assertEqual(2, raised.exception.code)
                    run.assert_not_called()

    def test_the_choices_are_the_allow_list(self) -> None:
        parser_choices = None
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            cli.main([*self.ARGS, "--instrument", "NOPE"])
        for instrument in ALL:
            self.assertIn(instrument, err.getvalue())
        self.assertIsNone(parser_choices)


if __name__ == "__main__":
    unittest.main()
