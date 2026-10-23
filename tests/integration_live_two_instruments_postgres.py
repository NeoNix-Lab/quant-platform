#!/usr/bin/env python3
"""Real PostgreSQL proof that two live-ingest instances do not collide (DG-N T02 part 5, ADR-0074 section 3).

The real ``run_live_ingest_server`` runs for two instruments in two threads, started at the same instant, over ONE storage root and
ONE catalog, through the real publication path (S13, S14, DataGateway read-back) and real checkpoint files. Only the network is
scripted: the provider gives both instruments the same timestamps and the same ``trade_id`` strings, so any shared key would
collide. Requires ``DATA_GATEWAY_TEST_DSN`` and a POSIX storage path.

Every publication is a slice (ADR-0075, DG-O O01): revision 1 of its own partition ``dt=<day>/seg=<HHMMSS.mmm>``, never superseded.
The proof of that behaviour as such is ``tests/integration_live_slices_postgres.py``; this test pins it per instrument: each
instrument's slices, files, catalog rows and checkpoint are its own, and the DataGateway returns all of its trades and none of the other's.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
import quant_platform.application.bybit_live as app  # noqa: E402
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application.bybit_live import LiveProviderProofReport  # noqa: E402
from quant_platform.application.live_ingest_server import (  # noqa: E402
    SERVER_FAILED,
    SERVER_STOPPED,
    LiveIngestServerConfigV1,
    run_live_ingest_server,
)
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.parquet import scan_trade_v1_all  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore  # noqa: E402
from quant_platform.operations.observability import SignalKind  # noqa: E402
from quant_platform.operations.pressure import PressurePolicyDefinition  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER  # noqa: E402
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    LiveSessionTracker,
    bybit_live_dataset_identity,
    canonicalize_bybit_live_message,
)

STORAGE_ROOT_ID = "live-hot"
A, B = INSTRUMENTS = ("ETHUSDT", "SOLUSDT")
POLICY = PressurePolicyDefinition(
    max_capacity_observation_age=timedelta(seconds=300), pressure_available_bytes=1, critical_available_bytes=1,
    exhausted_available_bytes=0,
)


class ScriptedProvider:
    """The same timestamps and the same trade_id strings for every instrument; only the symbol differs.

    The certifier requires every trade to lie inside the acquisition window the server declares (wall clock), so the scripted
    time is real time. To make both instruments' trades carry *identical* timestamps, the instances meet at a barrier inside
    the scripted acquisition and take one common time from it.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_index: dict[str, int] = defaultdict(int)
        self.history: dict[str, list] = defaultdict(list)
        self.rendezvous: threading.Barrier | None = None
        self.common_ms = 0

    def meet(self, parties: int) -> None:
        self.rendezvous = threading.Barrier(parties, action=self._take_common_time)

    def alone(self) -> None:
        self.rendezvous = None

    def _take_common_time(self) -> None:
        self.common_ms = int(time.time() * 1000) + 2

    def message(self, instrument: str, timestamp_ms: int, count: int = 2) -> dict:
        with self.lock:
            first = self.next_index[instrument]
            self.next_index[instrument] += count
        rows = [
            {"i": f"t-{k}", "s": instrument, "T": timestamp_ms + (k - first), "S": "Buy" if k % 2 == 0 else "Sell",
             "p": f"100.{k % 90 + 10}", "v": "0.5", "seq": k + 1}
            for k in range(first, first + count)
        ]
        return {"topic": f"publicTrade.{instrument}", "type": "snapshot", "ts": timestamp_ms + count, "data": rows}

    def acquisition(self, *, max_messages, max_seconds, instrument="BTCUSDT"):
        timestamp_ms = int(time.time() * 1000) + 2
        if self.rendezvous is not None:
            try:
                self.rendezvous.wait(timeout=30)
                timestamp_ms = self.common_ms
            except threading.BrokenBarrierError:
                pass
        batch = canonicalize_bybit_live_message(self.message(instrument, timestamp_ms), instrument=instrument)
        tracker = LiveSessionTracker(instrument)
        tracker.connected(conn_id="c")
        tracker.subscribed(conn_id="c")
        tracker.observed_message(batch)
        with self.lock:
            self.history[instrument].extend(batch.records)
        time.sleep(0.05)  # successive cycles of one instrument get distinct millisecond windows
        return LiveProviderProofReport(
            status="PASS", topic=f"publicTrade.{instrument}", messages=1, records=len(batch.records), duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=batch.records, session_evidence=tracker.evidence(),
        )

    def recent(self, *, limit, instrument="BTCUSDT"):
        """The last three delivered trades (the anchor among them) and one new trade: a restart that resumes cleanly."""

        with self.lock:
            window = tuple(self.history[instrument][-3:])
            anchor_ms = window[-1].exchange_ts.epoch_ns // 1_000_000
        # one millisecond after the anchor: later than it, earlier than any real-time acquisition that follows
        message = self.message(instrument, anchor_ms + 1, count=1)
        fresh = canonicalize_bybit_live_message(message, instrument=instrument).records
        with self.lock:
            self.history[instrument].extend(fresh)
        return window + tuple(fresh)


def run_instance(dsn, storage, checkpoint, instrument, cycles, gate, out):
    config = LiveIngestServerConfigV1(
        storage_root=storage, storage_root_id=STORAGE_ROOT_ID, checkpoint_path=checkpoint, pressure_policy=POLICY, dsn=dsn,
        producer="live-two-instruments-proof", code_ref="integration", max_messages_per_cycle=10, max_seconds_per_cycle=1,
        cycle_interval_seconds=0.0, instrument=instrument,
    )
    state = {"count": 0}
    signals = []

    def on_signal(signal):
        signals.append(signal)
        if signal.kind is SignalKind.HEALTH_SNAPSHOT:
            state["count"] += 1

    try:
        if gate is not None:
            gate.wait()
        report = run_live_ingest_server(
            config, stop_requested=lambda: state["count"] >= cycles, on_signal=on_signal, sleep=time.sleep,
        )
        out[instrument] = (report, signals)
    except BaseException as exc:  # noqa: BLE001 - surfaced by the caller
        out[instrument] = (exc, signals)


def run_servers(dsn, storage, assignments, cycles):
    """Run ``(instrument, checkpoint_path)`` pairs at the same instant, one thread each; return ``{instrument: (report, signals)}``."""

    out: dict = {}
    gate = threading.Barrier(len(assignments)) if len(assignments) > 1 else None
    threads = [
        threading.Thread(target=run_instance, args=(dsn, storage, checkpoint, instrument, cycles, gate, out))
        for instrument, checkpoint in assignments
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return out


def rows(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        result = cursor.fetchall()
    connection.rollback()
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_of(storage: Path, instrument: str | None = None) -> dict[str, str]:
    """``relative path -> sha256`` of every file under the storage root (or under one instrument's dataset directory)."""

    base = storage if instrument is None else storage / "canonical" / "trades" / "bybit" / instrument
    return {str(p.relative_to(storage)): sha(p) for p in sorted(base.rglob("*")) if p.is_file()} if base.exists() else {}


def catalog_state(connection) -> list:
    return rows(connection, "SELECT d.instrument, p.partition_key, p.revision, p.state, p.rel_path, p.row_count, "
                            "p.content_sha256, p.manifest_sha256 FROM catalog.partitions p JOIN catalog.datasets d "
                            "ON d.dataset_id = p.dataset_id ORDER BY 1, 2, 3")


def check_healthy(out, expected_status=SERVER_STOPPED):
    for instrument, (report, signals) in out.items():
        assert not isinstance(report, BaseException), (instrument, repr(report))
        failures = [dict(s.payload) for s in signals if s.kind is SignalKind.FAILURE]
        assert report.status == expected_status and not failures, (instrument, report.status, failures)
        assert {s.subject.subject_identity for s in signals} == {f"bybit:{instrument}:{STORAGE_ROOT_ID}"}, instrument


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL LIVE TWO-INSTRUMENT GATE NOT EXECUTED LOCALLY")
        return 0
    provider = ScriptedProvider()
    with tempfile.TemporaryDirectory() as holder, psycopg.connect(dsn) as connection:
        storage = Path(holder) / "storage"
        checkpoint = {i: storage / "_k10_checkpoints" / f"live-ingest-server-v1-{i}.json" for i in INSTRUMENTS}
        checkpoint[A].parent.mkdir(parents=True)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
            cursor.execute("INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))
        connection.commit()
        pairs = [(i, checkpoint[i]) for i in INSTRUMENTS]

        with mock.patch.object(app, "run_bounded_live_provider_proof_sync", provider.acquisition), \
                mock.patch.object(app, "fetch_recent_public_trades", provider.recent):

            # 1. Fresh, side by side: three cycles each, started at the same instant, over one root and one catalog.
            provider.meet(len(INSTRUMENTS))
            out = run_servers(dsn, storage, pairs, cycles=3)
            check_healthy(out)
            verify_isolation(connection, storage, checkpoint, provider, slices=3, generation=3)

            # 2. Restart, side by side: each reconciles from its own checkpoint (real catalog binding check), publishes
            #    under its own instrument, then acquires once more.
            provider.meet(len(INSTRUMENTS))
            check_healthy(run_servers(dsn, storage, pairs, cycles=2))
            verify_isolation(connection, storage, checkpoint, provider, slices=5, generation=5)

            # 3. One restarts alone: the other's files, catalog rows and checkpoint are byte-identical afterwards.
            before = {"files": files_of(storage, B), "checkpoint": checkpoint[B].read_bytes(),
                      "catalog": [r for r in catalog_state(connection) if r[0] == B]}
            provider.alone()
            check_healthy(run_servers(dsn, storage, [(A, checkpoint[A])], cycles=1))
            assert before["files"] == files_of(storage, B)
            assert before["checkpoint"] == checkpoint[B].read_bytes()
            assert before["catalog"] == [r for r in catalog_state(connection) if r[0] == B]
            assert CheckpointStore(checkpoint[A]).load().generation == 6
            assert CheckpointStore(checkpoint[B]).load().generation == 5

            # 4. Intruders: each server pointed at the other's checkpoint ends FAILED and changes nothing at all.
            for instrument, other in ((A, B), (B, A)):
                snapshot = (files_of(storage), {i: checkpoint[i].read_bytes() for i in INSTRUMENTS}, catalog_state(connection))
                out = run_servers(dsn, storage, [(instrument, checkpoint[other])], cycles=2)
                report, signals = out[instrument]
                assert report.status == SERVER_FAILED, (instrument, report)
                failures = [s for s in signals if s.kind is SignalKind.FAILURE]
                assert [s.payload["failure_code"] for s in failures] == ["CheckpointDomainMismatch"], failures
                assert not [s for s in signals if s.kind is SignalKind.HEALTH_SNAPSHOT]
                assert snapshot == (files_of(storage), {i: checkpoint[i].read_bytes() for i in INSTRUMENTS}, catalog_state(connection))

        assert not (storage / "canonical" / "trades" / "bybit" / "BTCUSDT").exists()
        assert [r[0] for r in rows(connection, "SELECT instrument FROM catalog.datasets ORDER BY 1")] == sorted(INSTRUMENTS)
    print("POSTGRESQL LIVE TWO-INSTRUMENT GATE PASSED")
    return 0


def verify_isolation(connection, storage, checkpoint, provider, *, slices, generation):
    """Each instrument has its own dataset, partitions, files and checkpoint, and nothing of the other's."""

    datasets = dict(rows(connection, "SELECT instrument, dataset_id::text FROM catalog.datasets"))
    assert set(datasets) == set(INSTRUMENTS) and len(set(datasets.values())) == len(INSTRUMENTS), datasets
    for instrument, rel_root in rows(connection, "SELECT instrument, rel_root FROM catalog.datasets"):
        assert rel_root == f"canonical/trades/bybit/{instrument}/trade-v1", (instrument, rel_root)

    # files: every file is under exactly one instrument's dataset directory, or is one of the two checkpoints
    everything = files_of(storage)
    allowed_prefixes = {f"canonical/trades/bybit/{i}/trade-v1/": i for i in INSTRUMENTS}
    owner: dict[str, str] = {}
    for relative in everything:
        if relative.startswith("_k10_checkpoints/"):
            continue
        matches = [i for prefix, i in allowed_prefixes.items() if relative.replace("\\", "/").startswith(prefix)]
        assert len(matches) == 1, f"{relative} is under no instrument's directory"
        owner[relative] = matches[0]
    assert sorted(p for p in everything if p.startswith("_k10_checkpoints/")) == sorted(
        f"_k10_checkpoints/live-ingest-server-v1-{i}.json" for i in INSTRUMENTS)

    keys_by_instrument: dict[str, set] = {}
    for instrument in INSTRUMENTS:
        directory = storage / "canonical" / "trades" / "bybit" / instrument / "trade-v1"
        own = [p for p, who in owner.items() if who == instrument]
        parquet = sorted(directory.glob("dt=*/seg=*/part-001.parquet"))
        assert len(parquet) == slices, (instrument, len(parquet))
        assert len([p for p in own if p.endswith("dataset-manifest.json")]) == 1
        assert len(list(directory.glob("dt=*/seg=*/partition-manifest.json"))) == slices
        assert len(list(directory.glob("dt=*/seg=*/coverage-manifest.json"))) == slices

        # catalog partitions of this instrument: every slice revision 1 of its own key, none superseded
        partitions = rows(connection, "SELECT p.revision, p.state, p.rel_path, p.row_count, p.content_sha256, p.manifest_sha256, "
                                      "p.partition_key FROM catalog.partitions p WHERE p.dataset_id = %s ORDER BY p.ts_start",
                          (datasets[instrument],))
        assert [r[0] for r in partitions] == [1] * slices, partitions
        assert [r[1] for r in partitions] == ["valid"] * slices, partitions
        assert len({r[6] for r in partitions}) == slices
        for _revision, _state, rel_path, row_count, content_sha, manifest_sha, key in partitions:
            artifact = directory / rel_path
            assert rel_path == f"{key}/part-001.parquet", (rel_path, key)
            assert str(content_sha).strip() == sha(artifact), "the Parquet on disk is not the one the catalog certified"
            manifest = artifact.parent / "partition-manifest.json"
            assert str(manifest_sha).strip() == sha(manifest), "the partition manifest on disk is not the catalog's"
            records = [r for batch in scan_trade_v1_all(artifact) for r in batch]
            assert len(records) == row_count
            assert {r.instrument for r in records} == {instrument} and {r.venue for r in records} == {"bybit"}
            keys_by_instrument.setdefault(instrument, set()).update((r.exchange_ts.epoch_ns, r.trade_id) for r in records)

        # checkpoint: its own instrument and its own catalog dataset, at the expected generation
        saved = CheckpointStore(checkpoint[instrument]).load()
        assert saved.dataset_identity == bybit_live_dataset_identity(instrument), saved.dataset_identity
        assert saved.catalog_dataset_id == datasets[instrument]
        assert (saved.generation, saved.revision) == (generation, 1), (instrument, saved.generation, saved.revision)
        last = provider.history[instrument][-1]
        assert saved.last_canonical_trade_id == last.trade_id and saved.last_canonical_exchange_ts == last.exchange_ts

        # DataGateway: only this instrument's records, and all of them (nothing is superseded)
        published = sorted(
            (r for p in parquet for batch in scan_trade_v1_all(p) for r in batch), key=lambda r: (r.exchange_ts, r.trade_id))
        start = Instant(published[0].exchange_ts.epoch_ns - 1_000_000)
        end = Instant(published[-1].exchange_ts.epoch_ns + 1_000_000)
        gateway = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        result = gateway.read(DataRequest(
            bybit_live_dataset_identity(instrument), start, end, schema_requirement="trade-v1",
            lifecycle_policy=LifecyclePolicy.VALID_ONLY, coverage_policy="allow-partial",
            ordering_policy=BYBIT_ORDERING_PROVIDER.identity))
        assert {r.instrument for r in result.records} == {instrument}
        assert [(r.exchange_ts, r.trade_id) for r in result.records] == [(r.exchange_ts, r.trade_id) for r in published]
        assert len(result.records) == len(provider.history[instrument]), "a published trade is not readable"

    # the adversarial premise held: both instruments carry the very same (timestamp, trade_id) keys
    shared = keys_by_instrument[A] & keys_by_instrument[B]
    assert shared and len(shared) >= slices, f"the scripted keys were not shared across instruments: {len(shared)}"


if __name__ == "__main__":
    raise SystemExit(main())
