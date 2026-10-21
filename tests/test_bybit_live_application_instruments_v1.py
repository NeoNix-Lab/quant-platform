"""DG-N T02 part 2: acquisition, publication, checkpoint and restart run for one named instrument (ADR-0074 sections 2 and 3).

Hermetic: a scripted WebSocket, a scripted REST response, a scripted publication; no network and no catalog. The real
publication of two instruments side by side is part 5 of T02.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.bybit_live as app  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    DurablePublicationState,
    LiveProviderProofReport,
    RealServerPublishProofReport,
    fetch_recent_public_trades,
    resume_live_ingest,
    run_bounded_live_provider_proof,
    run_real_server_restart_phase,
    run_real_server_restart_publish_phase,
)
from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointDomainMismatch, CheckpointStore  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1  # noqa: E402
from quant_platform.source_adapters.bybit_live import BybitLiveSourceError, bybit_live_dataset_identity  # noqa: E402

ALL = BYBIT_LINEAR_TRADE_INSTRUMENTS_V1
NOT_BTC = tuple(item for item in ALL if item != "BTCUSDT")
T0 = 1705276800490


def trade_row(instrument: str, trade_id: str = "tid-1", ms: int = T0) -> dict:
    return {"T": ms, "s": instrument, "S": "Buy", "v": "0.5", "p": "100.10", "i": trade_id, "seq": 7}


def trade_message(instrument: str, *, topic_of: str | None = None, rows_of: str | None = None, trade_id: str = "tid-1") -> str:
    return json.dumps({
        "topic": f"publicTrade.{topic_of or instrument}", "type": "snapshot", "ts": T0 + 5,
        "data": [trade_row(rows_of or instrument, trade_id)],
    })


def ack() -> str:
    return json.dumps({"op": "subscribe", "success": True, "conn_id": "c1"})


class ScriptedSocket:
    def __init__(self, script: tuple[str, ...]) -> None:
        self.script = list(script)
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self.script:
            raise TimeoutError("script exhausted")
        return self.script.pop(0)

    async def __aenter__(self) -> "ScriptedSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@contextmanager
def scripted_websockets(*script: str):
    socket = ScriptedSocket(script)
    module = types.ModuleType("websockets")
    module.ConnectionClosed = type("ConnectionClosed", (Exception,), {})  # type: ignore[attr-defined]
    module.connect = lambda *args, **kwargs: socket  # type: ignore[attr-defined]
    saved = sys.modules.get("websockets")
    sys.modules["websockets"] = module
    try:
        yield socket
    finally:
        if saved is None:
            sys.modules.pop("websockets", None)
        else:
            sys.modules["websockets"] = saved


def run_proof(**kwargs):
    return asyncio.run(run_bounded_live_provider_proof(max_messages=1, max_seconds=5.0, **kwargs))


class WebSocketTests(unittest.TestCase):
    def test_every_instrument_subscribes_to_its_own_topic_and_publishes_its_own_records(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument), scripted_websockets(ack(), trade_message(instrument)) as socket:
                report = run_proof(instrument=instrument)
                self.assertEqual([{"op": "subscribe", "args": [f"publicTrade.{instrument}"]}], socket.sent)
                self.assertEqual("PASS", report.status)
                self.assertEqual(f"publicTrade.{instrument}", report.topic)
                self.assertEqual({instrument}, {record.instrument for record in report.accepted_records})

    def test_without_an_argument_it_is_btcusdt(self) -> None:
        with scripted_websockets(ack(), trade_message("BTCUSDT")) as socket:
            report = run_proof()
        self.assertEqual([{"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}], socket.sent)
        self.assertEqual(("publicTrade.BTCUSDT", "PASS"), (report.topic, report.status))

    def test_a_message_of_another_topic_is_never_counted(self) -> None:
        for instrument in NOT_BTC:
            with self.subTest(instrument=instrument), scripted_websockets(ack(), trade_message("BTCUSDT")):
                report = run_proof(instrument=instrument)
                self.assertEqual("LIVE_PROVIDER_PROOF_PENDING", report.status)
                self.assertEqual((), report.accepted_records)

    def test_the_right_topic_carrying_another_symbol_is_refused(self) -> None:
        with scripted_websockets(ack(), trade_message("ETHUSDT", rows_of="BTCUSDT")):
            with self.assertRaisesRegex(BybitLiveSourceError, "unsupported symbol"):
                run_proof(instrument="ETHUSDT")

    def test_an_instrument_outside_the_allow_list_is_refused_before_connecting(self) -> None:
        for instrument in ("BTCUSD", "ADAUSDT", "ethusdt", "", None):
            with self.subTest(instrument=instrument), scripted_websockets() as socket:
                with self.assertRaises(BybitLiveSourceError):
                    run_proof(instrument=instrument)
                self.assertEqual([], socket.sent)


class RestTests(unittest.TestCase):
    @staticmethod
    def response(instrument: str, *, rows_of: str | None = None) -> io.BytesIO:
        row = {"execId": "e-1", "symbol": rows_of or instrument, "time": str(T0), "side": "Buy", "price": "100.10",
               "size": "0.5", "seq": "7"}
        return io.BytesIO(json.dumps({"retCode": 0, "result": {"category": "linear", "list": [row]}}).encode("utf-8"))

    @contextmanager
    def provider(self, instrument: str, **kwargs):
        calls: list[str] = []

        def urlopen(url, timeout=None):
            calls.append(url)
            response = self.response(instrument, **kwargs)
            response.__enter__ = lambda: response  # type: ignore[method-assign]
            response.__exit__ = lambda *exc: False  # type: ignore[method-assign]
            return response

        with mock.patch.object(app, "urlopen", urlopen):
            yield calls

    def test_the_request_and_the_records_follow_the_instrument(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument), self.provider(instrument) as calls:
                records = fetch_recent_public_trades(limit=5, instrument=instrument)
                query = parse_qs(urlparse(calls[0]).query)
                self.assertEqual({"category": ["linear"], "symbol": [instrument], "limit": ["5"]}, query)
                self.assertEqual([instrument], [record.instrument for record in records])

    def test_without_an_argument_it_is_btcusdt(self) -> None:
        with self.provider("BTCUSDT") as calls:
            records = fetch_recent_public_trades(limit=5)
        self.assertEqual(["BTCUSDT"], parse_qs(urlparse(calls[0]).query)["symbol"])
        self.assertEqual("BTCUSDT", records[0].instrument)

    def test_a_row_of_another_symbol_is_refused(self) -> None:
        with self.provider("ETHUSDT", rows_of="BTCUSDT"):
            with self.assertRaisesRegex(BybitLiveSourceError, "unsupported symbol"):
                fetch_recent_public_trades(limit=5, instrument="ETHUSDT")

    def test_an_instrument_outside_the_allow_list_is_refused_before_the_request(self) -> None:
        for instrument in ("BTCUSD", "ADAUSDT", None):
            with self.subTest(instrument=instrument), self.provider("BTCUSDT") as calls:
                with self.assertRaises(BybitLiveSourceError):
                    fetch_recent_public_trades(limit=5, instrument=instrument)
                self.assertEqual([], calls)


def trade(instrument: str, ms: int, trade_id: str, sequence: str = "1") -> TradeRecord:
    return TradeRecord("bybit", instrument, Instant(ms * 1_000_000), "100.10", "0.5", "buy", None, trade_id, sequence)


def publication_report(instrument: str, *, last_ms: int = T0, trade_id: str = "t-1", revision: int = 1) -> RealServerPublishProofReport:
    records = (trade(instrument, last_ms, trade_id),)
    return RealServerPublishProofReport(
        status="PASS",
        acquisition=LiveProviderProofReport(
            status="PASS", topic=f"publicTrade.{instrument}", messages=1, records=1, duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=records,
        ),
        durable_publication=DurablePublicationState(
            catalog_dataset_id=f"dataset-{instrument}", partition_key="dt=2024-01-15", revision=revision,
            partition_manifest_sha256="a" * 64,
        ),
        coverage_id=f"coverage-{instrument}-{revision}",
        coverage_status="complete",
    )


class CheckpointDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.path = Path(self.holder.name) / "checkpoint.json"

    def tearDown(self) -> None:
        self.holder.cleanup()

    def persist(self, instrument: str, **kwargs):
        return app._persist_checkpoint_from_publication(
            publication=publication_report(instrument, **kwargs), checkpoint_path=self.path, instrument=instrument,
        )

    def test_the_first_checkpoint_carries_the_identity_of_its_instrument(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                path = Path(self.holder.name) / f"{instrument}.json"
                app._persist_checkpoint_from_publication(
                    publication=publication_report(instrument), checkpoint_path=path, instrument=instrument,
                )
                saved = CheckpointStore(path).load()
                self.assertEqual(bybit_live_dataset_identity(instrument), saved.dataset_identity)
                self.assertEqual(1, saved.generation)

    def test_without_an_argument_the_first_checkpoint_is_btcusdt(self) -> None:
        app._persist_checkpoint_from_publication(publication=publication_report("BTCUSDT"), checkpoint_path=self.path)
        self.assertEqual(bybit_live_dataset_identity("BTCUSDT"), CheckpointStore(self.path).load().dataset_identity)

    def test_a_checkpoint_advances_within_its_instrument(self) -> None:
        self.persist("ETHUSDT")
        self.persist("ETHUSDT", last_ms=T0 + 10, trade_id="t-2", revision=2)
        saved = CheckpointStore(self.path).load()
        self.assertEqual((2, "t-2"), (saved.generation, saved.last_canonical_trade_id))

    def test_an_instance_of_another_instrument_never_writes_the_checkpoint(self) -> None:
        for owner, intruder in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT"), ("SOLUSDT", "XRPUSDT")):
            with self.subTest(owner=owner, intruder=intruder):
                self.path.unlink(missing_ok=True)
                self.persist(owner)
                before = self.path.read_bytes()
                # the explicit guard names the checkpoint's owner (not the later record-level check)
                with self.assertRaisesRegex(CheckpointDomainMismatch, f"the checkpoint belongs to bybit/{owner}, not to {intruder}"):
                    self.persist(intruder, last_ms=T0 + 10, trade_id="x-2", revision=2)
                self.assertEqual(before, self.path.read_bytes())

    def test_the_publish_phase_stops_before_acquiring_anything(self) -> None:
        self.persist("BTCUSDT")
        before = self.path.read_bytes()
        with mock.patch.object(app, "run_real_server_publish_proof", side_effect=AssertionError("acquired")) as publish:
            with self.assertRaises(CheckpointDomainMismatch):
                run_real_server_restart_publish_phase(
                    max_messages=1, max_seconds=1.0, storage_root=self.holder.name, storage_root_id="hot", dsn=None,
                    checkpoint_path=self.path, producer="p", code_ref="c", instrument="ETHUSDT",
                )
            publish.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_the_restart_phase_stops_before_the_catalog_and_the_provider(self) -> None:
        self.persist("ETHUSDT")
        before = self.path.read_bytes()
        with mock.patch.object(app, "load_current_durable_publication_state", side_effect=AssertionError("catalog")) as catalog, \
                mock.patch.object(app, "fetch_recent_public_trades", side_effect=AssertionError("provider")) as provider:
            with self.assertRaises(CheckpointDomainMismatch):
                run_real_server_restart_phase(
                    checkpoint_path=self.path, dsn=None, storage_root=self.holder.name, storage_root_id="hot", producer="p",
                    code_ref="c", instrument="BTCUSDT",
                )
            catalog.assert_not_called()
            provider.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_the_default_phases_are_still_btcusdt(self) -> None:
        self.persist("ETHUSDT")
        with mock.patch.object(app, "run_real_server_publish_proof", side_effect=AssertionError("acquired")):
            with self.assertRaises(CheckpointDomainMismatch):
                run_real_server_restart_publish_phase(
                    max_messages=1, max_seconds=1.0, storage_root=self.holder.name, storage_root_id="hot", dsn=None,
                    checkpoint_path=self.path, producer="p", code_ref="c",
                )

    def test_the_publish_phase_passes_its_instrument_down(self) -> None:
        seen: dict = {}

        def fake_publish(**kwargs):
            seen.update(kwargs)
            return publication_report(kwargs["instrument"])

        with mock.patch.object(app, "run_real_server_publish_proof", fake_publish):
            report = run_real_server_restart_publish_phase(
                max_messages=1, max_seconds=1.0, storage_root=self.holder.name, storage_root_id="hot", dsn=None,
                checkpoint_path=self.path, producer="p", code_ref="c", instrument="SOLUSDT",
            )
        self.assertEqual("SOLUSDT", seen["instrument"])
        self.assertEqual("CHECKPOINT_PERSISTED", report.status)
        self.assertEqual(bybit_live_dataset_identity("SOLUSDT"), CheckpointStore(self.path).load().dataset_identity)


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.path = Path(self.holder.name) / "checkpoint.json"

    def tearDown(self) -> None:
        self.holder.cleanup()

    def checkpoint_for(self, instrument: str):
        self.path.unlink(missing_ok=True)  # one fresh checkpoint per case: an existing one would be another case's domain
        app._persist_checkpoint_from_publication(
            publication=publication_report(instrument), checkpoint_path=self.path, instrument=instrument,
        )
        store = CheckpointStore(self.path)
        saved = store.load()
        durable = DurablePublicationState(
            catalog_dataset_id=saved.catalog_dataset_id, partition_key=saved.partition_key, revision=saved.revision,
            partition_manifest_sha256=saved.partition_manifest_sha256,
        )
        return store, durable

    def test_each_instrument_resumes_from_its_own_records(self) -> None:
        for instrument in ALL:
            with self.subTest(instrument=instrument):
                store, durable = self.checkpoint_for(instrument)
                outcome = resume_live_ingest(
                    checkpoint_store=store, durable_publication=durable, buffered_ws_records=(),
                    recent_rest_records=(trade(instrument, T0, "t-1"), trade(instrument, T0 + 1, "t-2")),
                )
                self.assertEqual("RESUMED", outcome.status)
                self.assertEqual(["t-2"], [record.trade_id for record in outcome.accepted_records])

    def test_a_record_of_another_instrument_is_refused_in_either_list(self) -> None:
        for owner in ("BTCUSDT", "ETHUSDT"):
            other = "ETHUSDT" if owner == "BTCUSDT" else "BTCUSDT"
            with self.subTest(owner=owner):
                store, durable = self.checkpoint_for(owner)
                good = trade(owner, T0, "t-1")
                with self.assertRaises(CheckpointDomainMismatch):
                    resume_live_ingest(checkpoint_store=store, durable_publication=durable, buffered_ws_records=(),
                                       recent_rest_records=(good, trade(other, T0 + 1, "t-2")))
                with self.assertRaises(CheckpointDomainMismatch):
                    resume_live_ingest(checkpoint_store=store, durable_publication=durable,
                                       recent_rest_records=(good,), buffered_ws_records=(trade(other, T0 + 1, "t-2"),))


class StopHere(Exception):
    """Raised by a test double to stop a function right after the value under test was captured."""


class PlumbingTests(unittest.TestCase):
    """The instrument given to a function reaches the function it calls: the places where a default would silently win."""

    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.path = Path(self.holder.name) / "checkpoint.json"

    def tearDown(self) -> None:
        self.holder.cleanup()

    def test_the_sync_proof_passes_the_instrument_to_the_async_proof(self) -> None:
        seen: dict = {}

        async def fake(**kwargs):
            seen.update(kwargs)
            return LiveProviderProofReport(status="LIVE_PROVIDER_PROOF_PENDING", topic="t", messages=0, records=0,
                                           duplicates_removed=0, final_state="DISCONNECTED", errors=())

        with mock.patch.object(app, "run_bounded_live_provider_proof", fake):
            app.run_bounded_live_provider_proof_sync(max_messages=1, max_seconds=1.0, instrument="SOLUSDT")
            self.assertEqual("SOLUSDT", seen["instrument"])
            app.run_bounded_live_provider_proof_sync(max_messages=1, max_seconds=1.0)
            self.assertEqual("BTCUSDT", seen["instrument"])

    @staticmethod
    def passing_report(instrument: str) -> LiveProviderProofReport:
        records = (trade(instrument, T0, "t-1"),)
        return LiveProviderProofReport(
            status="PASS", topic=f"publicTrade.{instrument}", messages=1, records=1, duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=records, session_evidence=object(),
        )

    def test_the_publish_proof_acquires_and_publishes_under_its_instrument(self) -> None:
        for instrument in ("BTCUSDT", "ETHUSDT", "DOGEUSDT"):
            with self.subTest(instrument=instrument):
                acquired: dict = {}
                identities: list = []

                def fake_sync(**kwargs):
                    acquired.update(kwargs)
                    return self.passing_report(kwargs["instrument"])

                def fake_revision(dsn, identity, partition_key):
                    identities.append(identity)
                    raise StopHere

                extra = {} if instrument == "BTCUSDT" else {"instrument": instrument}
                with mock.patch.object(app, "run_bounded_live_provider_proof_sync", fake_sync), \
                        mock.patch.object(app, "_next_revision", fake_revision):
                    with self.assertRaises(StopHere):
                        app.run_real_server_publish_proof(
                            max_messages=1, max_seconds=1.0, storage_root=self.holder.name, storage_root_id="hot",
                            dsn=None, producer="p", code_ref="c", **extra,
                        )
                self.assertEqual(instrument, acquired["instrument"])
                self.assertEqual([bybit_live_dataset_identity(instrument)], identities)

    def test_the_reconciliation_publication_uses_its_instrument(self) -> None:
        for instrument in ("BTCUSDT", "XRPUSDT"):
            with self.subTest(instrument=instrument):
                identities: list = []

                def fake_revision(dsn, identity, partition_key):
                    identities.append(identity)
                    raise StopHere

                extra = {} if instrument == "BTCUSDT" else {"instrument": instrument}
                empty = app._publish_restart_reconciliation_records(
                    accepted_records=(), reconcile_result=None, storage_root=self.holder.name, storage_root_id="hot",
                    dsn=None, producer="p", code_ref="c", **extra,
                )
                self.assertEqual(f"publicTrade.{instrument}", empty.acquisition.topic)
                with mock.patch.object(app, "_next_revision", fake_revision):
                    with self.assertRaises(StopHere):
                        app._publish_restart_reconciliation_records(
                            accepted_records=(trade(instrument, T0 + 1, "t-2"),), reconcile_result=None,
                            storage_root=self.holder.name, storage_root_id="hot", dsn=None, producer="p",
                            code_ref="c", **extra,
                        )
                self.assertEqual([bybit_live_dataset_identity(instrument)], identities)

    def test_the_restart_phase_reconciles_and_advances_under_its_instrument(self) -> None:
        for instrument in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            with self.subTest(instrument=instrument):
                self.path.unlink(missing_ok=True)
                app._persist_checkpoint_from_publication(
                    publication=publication_report(instrument), checkpoint_path=self.path, instrument=instrument)
                saved = CheckpointStore(self.path).load()
                durable = DurablePublicationState(
                    catalog_dataset_id=saved.catalog_dataset_id, partition_key=saved.partition_key,
                    revision=saved.revision, partition_manifest_sha256=saved.partition_manifest_sha256)
                fetched: dict = {}
                published: dict = {}

                def fake_recent(*, limit, instrument):
                    fetched["instrument"] = instrument
                    return (trade(instrument, T0, "t-1"), trade(instrument, T0 + 1, "t-2"))

                def fake_reconciliation(**kwargs):
                    published.update(kwargs)
                    return publication_report(kwargs["instrument"], last_ms=T0 + 1, trade_id="t-2", revision=2)

                extra = {} if instrument == "BTCUSDT" else {"instrument": instrument}
                with mock.patch.object(app, "fetch_recent_public_trades", fake_recent), \
                        mock.patch.object(app, "_publish_restart_reconciliation_records", fake_reconciliation):
                    report = run_real_server_restart_phase(
                        checkpoint_path=self.path, dsn=None, storage_root=self.holder.name, storage_root_id="hot",
                        producer="p", code_ref="c", durable_publication=durable, **extra,
                    )
                self.assertEqual("PASS", report.status)
                self.assertEqual(instrument, fetched["instrument"])
                self.assertEqual(instrument, published["instrument"])
                advanced = CheckpointStore(self.path).load()
                self.assertEqual((bybit_live_dataset_identity(instrument), 2, "t-2"),
                                 (advanced.dataset_identity, advanced.generation, advanced.last_canonical_trade_id))

    def test_the_proof_entry_point_passes_the_instrument_to_every_phase(self) -> None:
        calls: list[tuple[str, str]] = []

        def publish_phase(**kwargs):
            calls.append(("publish", kwargs["instrument"]))
            return app.RealServerRestartProofReport(
                status="CHECKPOINT_PERSISTED", publication=publication_report(kwargs["instrument"]))

        def restart_phase(**kwargs):
            calls.append(("restart", kwargs["instrument"]))
            return app.RealServerRestartProofReport(status="PASS", publication=None)

        common = dict(max_messages=1, max_seconds=1.0, storage_root=self.holder.name, storage_root_id="hot", dsn=None,
                      checkpoint_path=self.path, producer="p", code_ref="c")
        with mock.patch.object(app, "run_real_server_restart_publish_phase", publish_phase), \
                mock.patch.object(app, "run_real_server_restart_phase", restart_phase):
            for phase, expected in (("publish", ["publish"]), ("restart", ["restart"]), ("both", ["publish", "restart"])):
                for instrument in ("BTCUSDT", "DOGEUSDT"):
                    with self.subTest(phase=phase, instrument=instrument):
                        calls.clear()
                        extra = {} if instrument == "BTCUSDT" else {"instrument": instrument}
                        app.run_real_server_restart_proof(phase=phase, **common, **extra)
                        self.assertEqual([(name, instrument) for name in expected], calls)

    def test_a_publication_of_another_instrument_never_becomes_a_checkpoint(self) -> None:
        # First checkpoint: the instrument says ETHUSDT, the published records are BTCUSDT.
        with self.assertRaises(CheckpointDomainMismatch):
            app._persist_checkpoint_from_publication(
                publication=publication_report("BTCUSDT"), checkpoint_path=self.path, instrument="ETHUSDT")
        self.assertFalse(self.path.exists())
        # Advance: the checkpoint and the records are BTCUSDT, the instance says ETHUSDT.
        app._persist_checkpoint_from_publication(publication=publication_report("BTCUSDT"), checkpoint_path=self.path)
        before = self.path.read_bytes()
        with self.assertRaises(CheckpointDomainMismatch):
            app._persist_checkpoint_from_publication(
                publication=publication_report("BTCUSDT", last_ms=T0 + 10, trade_id="t-2", revision=2),
                checkpoint_path=self.path, instrument="ETHUSDT")
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
