#!/usr/bin/env python3
"""Real PostgreSQL proof of the continuous live session (DG-O O02, ADR-0075 slice 9.2).

The real ``run_live_ingest_server`` runs ONE session for ``ETHUSDT`` over a real storage root and catalog, through the real
publication path (assembler, S13, S14, DataGateway read-back), the real reconciliation and real checkpoint files. Only the provider is
scripted: a WebSocket with the ``websockets.connect`` shape and a recent-trades window drawn from one list of "true" trades, so what
the session misses during an outage is exactly what the reconciliation can (or cannot) recover. Requires ``DATA_GATEWAY_TEST_DSN``.

Proves: several slices over ONE connection whose windows chain exactly (a strict read over all of them returns every delivered trade);
a disconnect followed by a reconciliation that restores continuity (the trades the session missed are recovered, none twice, and a
strict read across the interruption succeeds); an anchor outside the recent window is an explicit gap (recorded, never a ``complete``
claim: a strict read across it refuses, ``allow-partial`` returns everything, the checkpoint does not move across it); a crash between
a publication and the checkpoint advance is replayed without a duplicate; a trade below the open window is counted and ends the
session.
"""

from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import psycopg  # noqa: E402
import quant_platform.application.bybit_live as app  # noqa: E402
from integration_live_slices_postgres import gateway_read, key_of, partitions, rows  # noqa: E402
from quant_platform.application.live_gap_orchestration import load_open_long_gap_records  # noqa: E402
from quant_platform.application.live_ingest_server import (  # noqa: E402
    SERVER_FAILED,
    SERVER_STOPPED,
    LiveIngestServerConfigV1,
    run_live_ingest_server,
)
from quant_platform.application.live_session import LiveSessionConfig  # noqa: E402
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.models import NoCoverage  # noqa: E402
from quant_platform.operations.checkpoint import CheckpointStore  # noqa: E402
from quant_platform.operations.observability import SignalKind  # noqa: E402
from quant_platform.operations.pressure import PressurePolicyDefinition  # noqa: E402
from quant_platform.source_adapters.bybit_live import canonicalize_bybit_live_message  # noqa: E402
from test_live_session_v1 import Closed, FakeProvider  # noqa: E402

STORAGE_ROOT_ID = "live-hot"
INSTRUMENT = "ETHUSDT"
MS = 1_000_000
BASE_MS = int(Instant.parse("2026-10-21T10:00:00Z").epoch_ns // MS)
POLICY = PressurePolicyDefinition(
    max_capacity_observation_age=timedelta(seconds=300), pressure_available_bytes=1, critical_available_bytes=1,
    exhausted_available_bytes=0,
)
SESSION = LiveSessionConfig(
    slice_seconds=3600, slice_max_trades=7, buffer_messages=500, ping_interval=30.0, pong_timeout=5.0, connect_timeout=5.0,
    reconnect_delay=0.05, reconnect_delay_max=0.1, tick=0.01,
)


class World:
    """The true trades of the instrument. The session sees the ones the provider delivers; the REST window sees the tail of all."""

    def __init__(self) -> None:
        self.trades: list = []
        self.next_ms = BASE_MS
        self.index = 0

    def make(self, count: int, step: int = 3) -> list:
        made = []
        for _ in range(count):
            row = {"i": f"t-{self.index}", "s": INSTRUMENT, "T": self.next_ms, "S": "Buy" if self.index % 2 == 0 else "Sell",
                   "p": f"100.{self.index % 90 + 10}", "v": "0.5", "seq": self.index + 1}
            made.append(row)
            self.index += 1
            self.next_ms += step
        records = canonicalize_bybit_live_message(
            {"topic": f"publicTrade.{INSTRUMENT}", "type": "snapshot", "ts": self.next_ms, "data": made}, instrument=INSTRUMENT).records
        self.trades.extend(records)
        return made

    @staticmethod
    def message(rows_: list) -> dict:
        return {"topic": f"publicTrade.{INSTRUMENT}", "type": "snapshot", "ts": rows_[-1]["T"], "data": rows_}


class Run:
    """One run of the real server in a worker thread."""

    def __init__(self, dsn, storage, checkpoint, provider: FakeProvider) -> None:
        self.provider = provider
        self.signals: list = []
        self.stop = threading.Event()
        self.report = None
        self.error = None
        self.config = LiveIngestServerConfigV1(
            storage_root=storage, storage_root_id=STORAGE_ROOT_ID, checkpoint_path=checkpoint, pressure_policy=POLICY, dsn=dsn,
            producer="live-session-proof", code_ref="integration", recent_limit=1000, instrument=INSTRUMENT,
            continuous_session=SESSION,
        )
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.report = run_live_ingest_server(self.config, stop_requested=self.stop.is_set, on_signal=self.signals.append)
        except BaseException as exc:  # noqa: BLE001
            self.error = exc

    def start(self) -> "Run":
        self.thread.start()
        return self

    def finish(self) -> None:
        self.stop.set()
        self.thread.join(30)
        assert not self.thread.is_alive(), "the server did not stop"
        assert self.error is None, repr(self.error)

    def until(self, condition, what: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error is not None or self.report is not None:
                raise AssertionError(f"the server ended while waiting for {what}: {self.error!r} {self.report}")
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {what}; signals={[dict(s.payload).get('phase') for s in self.signals][-8:]}")

    def health(self, phase: str) -> list[dict]:
        return [dict(s.payload) for s in self.signals if s.kind is SignalKind.HEALTH_SNAPSHOT and dict(s.payload).get("phase") == phase]


def websockets_stub(provider: FakeProvider):
    module = types.ModuleType("websockets")
    module.connect = provider.connect
    module.ConnectionClosed = Closed
    return mock.patch.dict(sys.modules, {"websockets": module})


def slices(connection):
    return partitions(connection, INSTRUMENT)


def check_catalog(connection, expected_trades: set[str]) -> list:
    """Nothing superseded, nothing but valid revision 1; every trade the session published readable exactly once, in order."""

    found = slices(connection)
    assert found and {r[1] for r in found} == {1} and {r[2] for r in found} == {"valid"}, [(r[0], r[1], r[2]) for r in found]
    everything = gateway_read(connection, Instant.parse(found[0][3]), Instant.parse(found[-1][4]), "allow-partial", INSTRUMENT)
    ids = [r.trade_id for r in everything]
    assert len(ids) == len(set(ids)), "a trade is published twice"
    assert set(ids) == expected_trades, (sorted(expected_trades - set(ids)), sorted(set(ids) - expected_trades))
    assert [key_of(r) for r in everything] == sorted(key_of(r) for r in everything)
    return found


def chain_is_exact(found: list) -> bool:
    return all(Instant.parse(a[4]) == Instant.parse(b[3]) for a, b in zip(found, found[1:]))


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL LIVE SESSION GATE NOT EXECUTED LOCALLY")
        return 0
    with tempfile.TemporaryDirectory() as holder, psycopg.connect(dsn) as connection:
        storage = Path(holder) / "storage"
        checkpoint = storage / "_k10_checkpoints" / f"live-ingest-server-v1-{INSTRUMENT}.json"
        checkpoint.parent.mkdir(parents=True)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
            cursor.execute("INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))
        connection.commit()

        world = World()
        provider = FakeProvider()
        delivered: set[str] = set()  # the trades the platform has published or must publish (the session saw them or the REST proved them)

        def recent(*, limit, instrument="BTCUSDT"):
            return tuple(world.trades[-limit:])

        with mock.patch.object(app, "fetch_recent_public_trades", recent), websockets_stub(provider):
            # 1. One session, many slices: the connection is opened once, never closed between publications, the windows chain
            #    exactly, and a STRICT read over all of them returns every delivered trade.
            run = Run(dsn, storage, checkpoint, provider).start()
            run.until(lambda: provider.sockets, "the connection")
            for k in range(8):
                rows_ = world.make(5)
                provider.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                time.sleep(0.12)
            run.until(lambda: len(slices(connection)) >= 4, "four slices")
            time.sleep(0.5)
            assert provider.connects == 1 and provider.closed_sockets == 0, (provider.connects, provider.closed_sockets)
            published = slices(connection)
            assert chain_is_exact(published), "the windows of one session must chain exactly"
            strict = gateway_read(connection, Instant.parse(published[0][3]), Instant.parse(published[-1][4]), "strict", INSTRUMENT)
            assert strict and {r.trade_id for r in strict} <= delivered
            saved = CheckpointStore(checkpoint).load()
            assert saved.partition_key == published[-1][0] and saved.generation == len(published), saved
            assert all(0 <= dict(s.payload)["buffer_high_water"] <= 500 for s in run.signals
                       if s.kind is SignalKind.HEALTH_SNAPSHOT and dict(s.payload).get("phase") == "slice")

            # 2. A disconnect: what was listened to is published, the session reconnects, the recent trades prove continuity from
            #    the last durable key, the trades the session MISSED are recovered, none twice, and a strict read across the
            #    interruption succeeds.
            before = len(slices(connection))
            missed = world.make(6)  # delivered nowhere over the WebSocket: the provider closes the connection first
            delivered.update(r["i"] for r in missed)
            provider.close()
            run.until(lambda: provider.connects == 2 and len(provider.sockets) == 2, "the reconnect")
            # the first trades of the new subscription: they are in the REST window too (taken after them), the overlap that proves
            # continuity; until a trade arrives the session waits and reconciles nothing
            arrival = world.make(4)
            provider.push(world.message(arrival))
            delivered.update(r["i"] for r in arrival)
            run.until(lambda: run.health("reconcile"), "the reconciliation")
            more = world.make(5)
            provider.push(world.message(more))
            delivered.update(r["i"] for r in more)
            time.sleep(0.3)
            run.until(lambda: len(slices(connection)) > before + 1, "the slices after the reconnect")
            interruptions = run.health("interruption")
            assert [h["interruption"] for h in interruptions] == ["disconnect"], interruptions
            assert provider.connects == 2
            published = slices(connection)
            assert chain_is_exact(published), "the interruption was proven continuous: the chain must stay exact"
            gateway_read(connection, Instant.parse(published[0][3]), Instant.parse(published[-1][4]), "strict", INSTRUMENT)
            run.finish()
            assert run.report.status == SERVER_STOPPED and not [s for s in run.signals if s.kind is SignalKind.FAILURE]
            assert run.health("slice")[-1]["reconnects"] == 1  # the slice published at the stop is the second session's
            found = check_catalog(connection, delivered)
            assert chain_is_exact(found)
            assert CheckpointStore(checkpoint).load().partition_key == found[-1][0]

            # 3. An anchor outside the recent window is an explicit gap: recorded, never a complete claim, the checkpoint does not
            #    move across it, and a strict read across it refuses.
            provider2 = FakeProvider()
            run = Run(dsn, storage, checkpoint, provider2)
            world.make(60)  # a long outage: the REST window below holds only the last few
            tail = world.trades[-3:]
            generation = CheckpointStore(checkpoint).load().generation

            def recent_after_outage(*, limit, instrument="BTCUSDT"):
                return tuple(tail)

            with mock.patch.object(app, "fetch_recent_public_trades", recent_after_outage), websockets_stub(provider2):
                run.start()
                run.until(lambda: provider2.sockets, "the connection after the outage")
                first_after = world.make(2)  # the first trades of the subscription: below every bound, nothing is published yet
                provider2.push(world.message(first_after))
                delivered.update(r["i"] for r in first_after)
                run.until(lambda: run.health("reconcile") and any("anchor" in str(h.get("reason", "")) or "last durable" in str(h.get("reason", "")) for h in run.health("reconcile")), "the gap")
                assert CheckpointStore(checkpoint).load().generation == generation, "the checkpoint moved across a gap"
                assert load_open_long_gap_records(storage_root=storage, instrument=INSTRUMENT).records, "the gap was not recorded"
                after = world.make(8)
                provider2.push(world.message(after))
                delivered.update(r["i"] for r in after)
                time.sleep(0.3)
                run.finish()
            found = check_catalog(connection, delivered)
            assert not chain_is_exact(found), "a gap must not be chained over"
            try:
                gateway_read(connection, Instant.parse(found[0][3]), Instant.parse(found[-1][4]), "strict", INSTRUMENT)
            except NoCoverage:
                pass
            else:
                raise AssertionError("a strict read across an explicit gap must refuse")
            assert CheckpointStore(checkpoint).load().generation > generation  # the next publication advanced it, after the gap

            # 4. A crash between a publication and the checkpoint advance: the checkpoint is behind a durable slice; the restart
            #    replays it and publishes no second trade.
            provider3 = FakeProvider()
            crash = Run(dsn, storage, checkpoint, provider3)
            real_persist = app.persist_publication_checkpoint
            calls = {"n": 0}

            def failing_persist(**kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("crash after the publication, before the checkpoint advance")
                return real_persist(**kwargs)

            def recent_all(*, limit, instrument="BTCUSDT"):
                return tuple(world.trades[-limit:])

            with mock.patch.object(app, "fetch_recent_public_trades", recent_all), \
                    mock.patch.object(app, "persist_publication_checkpoint", failing_persist), websockets_stub(provider3):
                crash.start()
                crash.until(lambda: provider3.sockets, "the connection")
                rows_ = world.make(3)
                provider3.push(world.message(rows_))  # the first trade of the subscription: the reconciliation follows it
                delivered.update(r["i"] for r in rows_)
                crash.until(lambda: crash.health("reconcile"), "the reconciliation")
                for _ in range(5):
                    rows_ = world.make(5)
                    provider3.push(world.message(rows_))
                    delivered.update(r["i"] for r in rows_)
                    time.sleep(0.15)
                crash.thread.join(15)
            assert crash.report is not None and crash.report.status == SERVER_FAILED, (
                crash.report, calls, [(s.kind.name, dict(s.payload).get("phase"), dict(s.payload).get("reason")) for s in crash.signals][-8:])
            behind = CheckpointStore(checkpoint).load()
            durable = slices(connection)
            assert Instant.parse(durable[-1][4]) > behind.last_canonical_exchange_ts, "the slice was not durable ahead of the checkpoint"
            provider4 = FakeProvider()
            restart = Run(dsn, storage, checkpoint, provider4)
            with mock.patch.object(app, "fetch_recent_public_trades", recent_all), websockets_stub(provider4):
                restart.start()
                restart.until(lambda: provider4.sockets, "the connection")
                rows_ = world.make(2)
                provider4.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                restart.until(lambda: restart.health("reconcile") or restart.health("slice"), "the replay")
                more = world.make(4)
                provider4.push(world.message(more))
                delivered.update(r["i"] for r in more)
                time.sleep(0.3)
                restart.finish()
            found = check_catalog(connection, delivered)  # no trade twice, none lost

            # 5. A trade below the open window is counted, never accepted, and ends the session.
            provider5 = FakeProvider()
            late = Run(dsn, storage, checkpoint, provider5)
            with mock.patch.object(app, "fetch_recent_public_trades", recent_all), websockets_stub(provider5):
                late.start()
                late.until(lambda: provider5.sockets, "the connection")
                rows_ = world.make(2)
                provider5.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                late.until(lambda: late.health("reconcile"), "the reconciliation")
                rows_ = world.make(5)
                provider5.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                rows_ = world.make(5)
                provider5.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                late.until(lambda: len(slices(connection)) > len(found), "a slice")
                window_start = Instant.parse(slices(connection)[-1][4])
                straggler = {"i": "t-late", "s": INSTRUMENT, "T": window_start.epoch_ns // MS - 4, "S": "Buy", "p": "100.1", "v": "0.5", "seq": 99999}
                provider5.push(world.message([straggler]))
                late.until(lambda: late.health("interruption"), "the interruption")
                late.finish()
            assert late.health("interruption")[0]["interruption"] == "ordering_violation"
            assert late.health("interruption")[0]["ordering_violations"] == 1
            check_catalog(connection, delivered)  # "t-late" was never published

            # 6. A recent-trades window that still holds the anchor but is behind the subscription proves nothing: the trades of the
            #    interval nobody listened to are missing from both. The first trades of the subscription do not meet the window, so
            #    the interval is an explicit gap, never "continuity restored"; the trades in between are not claimed.
            stale = tuple(world.trades)  # holds the anchor (the last trade ever published), nothing newer
            world.make(3)  # delivered nowhere: the unlistened interval
            provider6 = FakeProvider()
            behind = Run(dsn, storage, checkpoint, provider6)
            before_gaps = len(load_open_long_gap_records(storage_root=storage, instrument=INSTRUMENT).records)
            generation = CheckpointStore(checkpoint).load().generation

            def recent_stale(*, limit, instrument="BTCUSDT"):
                return stale

            with mock.patch.object(app, "fetch_recent_public_trades", recent_stale), websockets_stub(provider6):
                behind.start()
                behind.until(lambda: provider6.sockets, "the connection")
                rows_ = world.make(2)
                provider6.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                behind.until(lambda: behind.health("reconcile"), "the gap")
                assert any("last durable" in str(h.get("reason", "")) or "overlap" in str(h.get("reason", ""))
                           for h in behind.health("reconcile")), behind.health("reconcile")
                assert CheckpointStore(checkpoint).load().generation == generation, "the checkpoint moved across an unproven interval"
                assert len(load_open_long_gap_records(storage_root=storage, instrument=INSTRUMENT).records) == before_gaps + 1
                rows_ = world.make(8)
                provider6.push(world.message(rows_))
                delivered.update(r["i"] for r in rows_)
                time.sleep(0.4)
                behind.finish()
            found = check_catalog(connection, delivered)  # the three unlistened trades are not in it: nothing was claimed for them

        assert [r[0] for r in rows(connection, "SELECT instrument FROM catalog.datasets ORDER BY 1")] == [INSTRUMENT]
    print("POSTGRESQL LIVE SESSION GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
