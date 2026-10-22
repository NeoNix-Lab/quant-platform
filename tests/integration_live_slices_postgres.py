#!/usr/bin/env python3
"""Real PostgreSQL proof that live slices are append-only (DG-O O01, ADR-0075 sections 2 to 4).

The real ``run_live_ingest_server`` runs for ``ETHUSDT`` over a real storage root and a real catalog, through the real publication
path (planner, S13, S14, DataGateway read-back) and real checkpoint files. Only the network is scripted. Requires
``DATA_GATEWAY_TEST_DSN`` and a POSIX storage path.

What it proves, each of which fails on the behaviour before ADR-0075 (every cycle a new revision of ``dt=<day>``, the previous
one ``superseded``): after many cycles every published trade is readable through the DataGateway and no partition is
superseded; every live partition is revision 1 of its own ``dt=<day>/seg=<HHMMSS.mmm>`` key; publications of one wall-clock second
do not collide; a restart's reconciliation chains to the last slice; a replay of a published slice publishes nothing; a live
trade inside what is already published is refused, fail closed; an acquisition across a UTC midnight is two chained slices; a
slice that cannot be read back exactly as published ends the loop; the archive import and the live path do not cover the same
interval.

What it does NOT claim: that the pauses between cycles are covered. The loop still closes its WebSocket between cycles (F2,
ended by O02), so the windows of two cycles do not chain and a strict read across them refuses.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import gzip
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
import quant_platform.application.bybit_live as app  # noqa: E402
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application.bybit_archive_import import ArchiveImportRefused, import_bybit_archive_day  # noqa: E402
from quant_platform.application.bybit_live import LiveProviderProofReport  # noqa: E402
from quant_platform.application.live_ingest_server import (  # noqa: E402
    SERVER_FAILED,
    SERVER_STOPPED,
    LiveIngestServerConfigV1,
    run_live_ingest_server,
)
from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.models import NoCoverage  # noqa: E402
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
from quant_platform.source_adapters.bybit_live_slices import slice_partition_key  # noqa: E402

STORAGE_ROOT_ID = "live-hot"
INSTRUMENT = "ETHUSDT"
OTHER = "SOLUSDT"
MS = 1_000_000
POLICY = PressurePolicyDefinition(
    max_capacity_observation_age=timedelta(seconds=300), pressure_available_bytes=1, critical_available_bytes=1,
    exhausted_available_bytes=0,
)
# a fixed instant: the windows are exchange time, so the script needs no wall clock (the old path did)
BASE_MS = int(Instant.parse("2026-10-21T10:00:00Z").epoch_ns // MS)
MIDNIGHT_MS = int(Instant.parse("2026-10-22T00:00:00Z").epoch_ns // MS)
ARCHIVE_HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional"


class ScriptedProvider:
    """Three trades a cycle, one millisecond apart; the next cycle starts 200 ms (of exchange time) later: the unlistened pause."""

    def __init__(self, instrument: str) -> None:
        self.instrument = instrument
        self.cursor_ms = BASE_MS
        self.index = 0
        self.history: list = []
        self.recent_mode = "fresh"  # fresh: the anchor and one new trade; replay: exactly the published tail
        self.recent_gap_ms = 0  # fresh: how long after the anchor the new trade is
        self.late_next = False
        self.acquired_at: list[float] = []

    def message(self, timestamp_ms: int, count: int) -> dict:
        first, self.index = self.index, self.index + count
        rows = [
            {"i": f"t-{k}", "s": self.instrument, "T": timestamp_ms + (k - first), "S": "Buy" if k % 2 == 0 else "Sell",
             "p": f"100.{k % 90 + 10}", "v": "0.5", "seq": k + 1}
            for k in range(first, first + count)
        ]
        return {"topic": f"publicTrade.{self.instrument}", "type": "snapshot", "ts": timestamp_ms + count, "data": rows}

    def acquisition(self, *, max_messages, max_seconds, instrument="BTCUSDT"):
        assert instrument == self.instrument
        self.acquired_at.append(time.time())
        late = self.late_next
        self.late_next = False
        if late:  # a trade 5 ms before the last published millisecond: an ordering violation
            timestamp_ms = self.history[-1].exchange_ts.epoch_ns // MS - 5
        else:
            timestamp_ms, self.cursor_ms = self.cursor_ms, self.cursor_ms + 200
        batch = canonicalize_bybit_live_message(self.message(timestamp_ms, 3), instrument=instrument)
        tracker = LiveSessionTracker(instrument)
        tracker.connected(conn_id="c")
        tracker.subscribed(conn_id="c")
        tracker.observed_message(batch)
        if not late:
            self.history.extend(batch.records)
        return LiveProviderProofReport(
            status="PASS", topic=f"publicTrade.{instrument}", messages=1, records=len(batch.records), duplicates_removed=0,
            final_state="ACQUIRING", errors=(), accepted_records=batch.records, session_evidence=tracker.evidence(),
        )

    def recent(self, *, limit, instrument="BTCUSDT"):
        assert instrument == self.instrument
        if self.recent_mode == "replay":
            return tuple(self.history[-limit:])
        window = tuple(self.history[-3:])
        anchor_ms = window[-1].exchange_ts.epoch_ns // MS
        fresh = canonicalize_bybit_live_message(self.message(anchor_ms + 1 + self.recent_gap_ms, 1), instrument=instrument).records
        self.history.extend(fresh)
        return window + tuple(fresh)


def run_server(dsn, storage, checkpoint, cycles, instrument=INSTRUMENT):
    config = LiveIngestServerConfigV1(
        storage_root=storage, storage_root_id=STORAGE_ROOT_ID, checkpoint_path=checkpoint, pressure_policy=POLICY, dsn=dsn,
        producer="live-slices-proof", code_ref="integration", max_messages_per_cycle=10, max_seconds_per_cycle=1,
        cycle_interval_seconds=0.0, instrument=instrument,
    )
    signals = []
    count = {"n": 0}

    def on_signal(signal):
        signals.append(signal)
        if signal.kind in (SignalKind.HEALTH_SNAPSHOT, SignalKind.FAILURE):
            count["n"] += 1

    report = run_live_ingest_server(config, stop_requested=lambda: count["n"] >= cycles, on_signal=on_signal, sleep=time.sleep)
    return report, signals


def rows(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        result = cursor.fetchall()
    connection.rollback()
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def partitions(connection, instrument=INSTRUMENT, slices_only=True):
    """``(key, revision, state, ts_start, ts_end, row_count, content_sha, manifest_sha)`` of the instrument, by window start."""

    like = "dt=%/seg=%" if slices_only else "%"
    return rows(connection, "SELECT p.partition_key, p.revision, p.state, p.ts_start, p.ts_end, p.row_count, p.content_sha256, "
                            "p.manifest_sha256 FROM catalog.partitions p JOIN catalog.datasets d ON d.dataset_id = p.dataset_id "
                            "WHERE d.instrument = %s AND p.partition_key LIKE %s ORDER BY p.ts_start", (instrument, like))


def gateway_read(connection, start: Instant, end: Instant, policy: str, instrument=INSTRUMENT):
    gateway = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
    return gateway.read(DataRequest(
        bybit_live_dataset_identity(instrument), start, end, schema_requirement="trade-v1",
        lifecycle_policy=LifecyclePolicy.VALID_ONLY, coverage_policy=policy,
        ordering_policy=BYBIT_ORDERING_PROVIDER.identity)).records


def key_of(record):
    return (record.exchange_ts.epoch_ns, record.trade_id)


def check_healthy(out, expected_status=SERVER_STOPPED):
    report, signals = out
    failures = [dict(s.payload) for s in signals if s.kind is SignalKind.FAILURE]
    assert report.status == expected_status and not failures, (report.status, failures)


def failure_codes(signals):
    return [dict(s.payload)["failure_code"] for s in signals if s.kind is SignalKind.FAILURE]


def archive_file(directory: Path, instrument: str, day: str) -> Path:
    """A one-trade archive day (``timestamp`` is the day's first second plus a fraction, as Bybit writes it)."""

    start = int(Instant.parse(f"{day}T00:00:00Z").epoch_ns // 1_000_000_000)
    path = directory / f"{instrument}{day}.csv.gz"
    line = f"{start + 5}.25,{instrument},Buy,2,2499,PlusTick,a-1,1,2,1"
    path.write_bytes(gzip.compress((ARCHIVE_HEADER + "\n" + line + "\n").encode("utf-8")))
    return path


def verify_catalog(connection, storage: Path, provider: ScriptedProvider, expected_partitions: int) -> list:
    """The invariants after any number of cycles: append-only, every published trade readable, files as certified."""

    found = partitions(connection, provider.instrument)
    assert len(found) == expected_partitions, [r[0] for r in found]
    assert {r[1] for r in found} == {1}, "a live slice is revision 1 of its own partition"
    assert {r[2] for r in found} == {"valid"}, [(r[0], r[2]) for r in found]  # nothing superseded, nothing else
    assert len({r[0] for r in found}) == len(found), "two slices share a key"
    directory = storage / "canonical" / "trades" / "bybit" / provider.instrument / "trade-v1"
    for key, _revision, _state, ts_start, ts_end, row_count, content_sha, manifest_sha in found:
        window = directory / key
        assert sorted(p.name for p in window.iterdir()) == ["coverage-manifest.json", "part-001.parquet", "partition-manifest.json"], key
        assert str(content_sha).strip() == sha(window / "part-001.parquet"), "the Parquet is not the one the catalog certified"
        assert str(manifest_sha).strip() == sha(window / "partition-manifest.json")
        records = [r for batch in scan_trade_v1_all(window / "part-001.parquet") for r in batch]
        assert len(records) == row_count and {r.instrument for r in records} == {provider.instrument}
        start = Instant.parse(ts_start)
        assert key == slice_partition_key(start), (key, start)  # the key names the window the catalog holds
        assert all(start <= r.exchange_ts < Instant.parse(ts_end) for r in records)
    # nothing but slices at the top of the dataset: no leftover of the old day-level naming
    assert not list(directory.glob("part-*")) and not list(directory.glob("*-manifest-*.json"))

    # F1 closed: the DataGateway returns EVERY published trade, in order, not the last cycle's few
    everything = gateway_read(connection, Instant.parse(found[0][3]), Instant.parse(found[-1][4]), "allow-partial", provider.instrument)
    assert [key_of(r) for r in everything] == [key_of(r) for r in sorted(provider.history, key=key_of)], \
        (len(everything), len(provider.history))
    return found


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL LIVE SLICES GATE NOT EXECUTED LOCALLY")
        return 0
    provider = ScriptedProvider(INSTRUMENT)
    other = ScriptedProvider(OTHER)
    providers = {INSTRUMENT: provider, OTHER: other}

    def acquisition(**kwargs):
        return providers[kwargs["instrument"]].acquisition(**kwargs)

    def recent(**kwargs):
        return providers[kwargs["instrument"]].recent(**kwargs)

    with tempfile.TemporaryDirectory() as holder, psycopg.connect(dsn) as connection:
        storage = Path(holder) / "storage"
        staging = Path(holder) / "staging"
        staging.mkdir()
        checkpoint = storage / "_k10_checkpoints" / f"live-ingest-server-v1-{INSTRUMENT}.json"
        checkpoint.parent.mkdir(parents=True)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING", ("0" * 64, "{}"))
            cursor.execute("INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES (%s,%s,'hot')", (STORAGE_ROOT_ID, str(storage)))
        connection.commit()

        # 0. A day imported from the archive is already in the dataset when the live path starts: the two live in one dataset.
        imported = import_bybit_archive_day(
            connection, instrument=INSTRUMENT, day="2026-10-20", archive_path=archive_file(staging, INSTRUMENT, "2026-10-20"),
            storage_root_id=STORAGE_ROOT_ID, code_ref="integration")
        assert imported.state == "valid"

        with mock.patch.object(app, "run_bounded_live_provider_proof_sync", acquisition), \
                mock.patch.object(app, "fetch_recent_public_trades", recent):

            # 1. Many cycles: every one a slice of its own. Before ADR-0075 the last cycle replaced all the earlier ones.
            check_healthy(run_server(dsn, storage, checkpoint, cycles=6))
            found = verify_catalog(connection, storage, provider, expected_partitions=6)
            assert len(provider.history) == 18
            saved = CheckpointStore(checkpoint).load()
            assert (saved.generation, saved.revision, saved.partition_key) == (6, 1, found[-1][0]), saved
            assert str(saved.partition_manifest_sha256).strip() == str(found[-1][7]).strip()
            # six publications inside 1.2 s of exchange time, back to back in wall time: the names come from the window, so
            # no pair collides (the old names came from the wall-clock second) -- and some did land in one wall-clock second
            starts = [Instant.parse(r[3]).epoch_ns // MS for r in found]
            assert max(starts) - min(starts) < 1200
            seconds = [int(t) for t in provider.acquired_at]
            assert len(seconds) > len(set(seconds)), f"no two publications fell in one wall-clock second: {seconds}"
            # the imported day and the live slices read together, in order, without overlap
            both = gateway_read(connection, Instant.parse("2026-10-20T00:00:00Z"), Instant.parse(found[-1][4]), "allow-partial")
            assert [r.trade_id for r in both] == ["a-1"] + [r.trade_id for r in provider.history]
            # and an archive day that already has live slices is not imported over them
            try:
                import_bybit_archive_day(
                    connection, instrument=INSTRUMENT, day="2026-10-21", archive_path=archive_file(staging, INSTRUMENT, "2026-10-21"),
                    storage_root_id=STORAGE_ROOT_ID, code_ref="integration")
            except ArchiveImportRefused as exc:
                assert exc.phase == "CATALOG", exc.phase
            else:
                raise AssertionError("the archive imported a day that has live slices")

            # 2. The pause between two cycles is not claimed: a strict read across it refuses, one slice reads strictly.
            first, second = found[0], found[1]
            try:
                gateway_read(connection, Instant.parse(first[3]), Instant.parse(second[4]), "strict")
            except NoCoverage:
                pass
            else:
                raise AssertionError("a strict read across the unlistened pause must refuse")
            only = gateway_read(connection, Instant.parse(second[3]), Instant.parse(second[4]), "strict")
            assert [key_of(r) for r in only] == [key_of(r) for r in provider.history[3:6]]

            # 3. Restart: the reconciliation publishes as a slice that starts where the last one ended (continuity proven by the
            #    provider's recent window) even though the first reconciled trade is later; then the loop acquires again.
            last_end = Instant.parse(found[-1][4])
            provider.recent_gap_ms = 50
            check_healthy(run_server(dsn, storage, checkpoint, cycles=2))
            provider.recent_gap_ms = 0
            found = verify_catalog(connection, storage, provider, expected_partitions=8)
            assert Instant.parse(found[6][3]) == last_end, "the reconciliation slice does not start where the last slice ended"
            assert found[6][0] == slice_partition_key(last_end)
            joined = gateway_read(connection, Instant.parse(found[5][3]), Instant.parse(found[6][4]), "strict")
            assert len(joined) == 4, "two chained slices must read strictly as one interval"

            # 4. A replay of published slices (a crash between publication and checkpoint advance) publishes nothing.
            stale = checkpoint.read_bytes()
            check_healthy(run_server(dsn, storage, checkpoint, cycles=3))  # reconcile + 2 acquisitions
            found = verify_catalog(connection, storage, provider, expected_partitions=11)
            current = checkpoint.read_bytes()
            checkpoint.write_bytes(stale)  # the publications are durable, the checkpoint never advanced
            provider.recent_mode = "replay"
            snapshot = [r[:2] + r[5:] for r in found]
            out = run_server(dsn, storage, checkpoint, cycles=1)
            check_healthy(out)
            assert [r[:2] + r[5:] for r in partitions(connection)] == snapshot, "a replay published something"
            assert checkpoint.read_bytes() == stale
            assert any("no new records" in str(dict(s.payload).get("reason", "")) for s in out[1]), "the replay was not recognised"
            checkpoint.write_bytes(current)

            # 5. A live trade before what is already published is an ordering violation: fail closed, nothing is published.
            provider.recent_mode = "fresh"
            provider.late_next = True  # the second cycle (after the reconciliation) brings a trade 5 ms before the published end
            generation = CheckpointStore(checkpoint).load().generation
            report, signals = run_server(dsn, storage, checkpoint, cycles=2)
            assert report.status == SERVER_FAILED, report.status
            assert failure_codes(signals) == ["SLICE_ORDERING_VIOLATION"], failure_codes(signals)
            found = verify_catalog(connection, storage, provider, expected_partitions=12)  # only the reconciliation slice was added
            assert CheckpointStore(checkpoint).load().generation == generation + 1

            # 6. One acquisition across a UTC midnight is two slices of one cycle: the first ends at the midnight, the second
            #    starts there, they chain exactly and read strictly as one interval; the checkpoint binds the last one.
            provider.cursor_ms = MIDNIGHT_MS - 1  # three trades: 23:59:59.999, 00:00:00.000, 00:00:00.001
            generation = CheckpointStore(checkpoint).load().generation
            check_healthy(run_server(dsn, storage, checkpoint, cycles=2))  # reconciliation, then the midnight acquisition
            found = verify_catalog(connection, storage, provider, expected_partitions=15)
            before_midnight, after_midnight = found[-2], found[-1]
            assert before_midnight[0] == "dt=2026-10-21/seg=235959.999" and after_midnight[0] == "dt=2026-10-22/seg=000000.000", \
                (before_midnight[0], after_midnight[0])
            assert Instant.parse(before_midnight[4]) == Instant.parse(after_midnight[3]) == Instant.parse("2026-10-22T00:00:00Z")
            joined = gateway_read(connection, Instant.parse(before_midnight[3]), Instant.parse(after_midnight[4]), "strict")
            assert [r.trade_id for r in joined] == [r.trade_id for r in provider.history[-3:]]
            saved = CheckpointStore(checkpoint).load()
            assert (saved.generation, saved.partition_key) == (generation + 2, after_midnight[0]), saved  # one advance a cycle

            # 7. A slice that cannot be read back exactly as published ends the loop (fail closed) and the checkpoint stays:
            #    first with other trades back (same count), then with nothing back.
            kept = checkpoint.read_bytes()
            provider.recent_mode = "replay"  # the checkpoint stays behind the slice that was just published: a replay recognises it
            wrong = [lambda c, i, planned: [replace(r, trade_id=r.trade_id + "x") for r in planned.records], lambda *_: []]
            for expected, double in enumerate(wrong):
                with mock.patch.object(app, "_read_back_slice", double):
                    report, signals = run_server(dsn, storage, checkpoint, cycles=2)  # replay (nothing new), then a slice
                assert report.status == SERVER_FAILED, report.status
                assert failure_codes(signals) == ["SLICE_READ_BACK_MISMATCH"], failure_codes(signals)
                assert checkpoint.read_bytes() == kept
                verify_catalog(connection, storage, provider, expected_partitions=16 + expected)  # the slice itself is durable

            # 8. The live path never publishes into an interval that is already covered: an archive day of the day the live
            #    stream is on makes the first live acquisition a violation, and nothing is published.
            sol_checkpoint = storage / "_k10_checkpoints" / f"live-ingest-server-v1-{OTHER}.json"
            covered = import_bybit_archive_day(
                connection, instrument=OTHER, day="2026-10-21", archive_path=archive_file(staging, OTHER, "2026-10-21"),
                storage_root_id=STORAGE_ROOT_ID, code_ref="integration")
            assert covered.state == "valid"
            report, signals = run_server(dsn, storage, sol_checkpoint, cycles=1, instrument=OTHER)
            assert report.status == SERVER_FAILED and failure_codes(signals) == ["SLICE_ORDERING_VIOLATION"], \
                (report.status, failure_codes(signals))
            assert partitions(connection, OTHER) == [], "the live path published into a covered interval"
            assert [r[0] for r in partitions(connection, OTHER, slices_only=False)] == ["dt=2026-10-21"]
            assert not sol_checkpoint.exists()

        assert [r[0] for r in rows(connection, "SELECT instrument FROM catalog.datasets ORDER BY 1")] == [INSTRUMENT, OTHER]
    print("POSTGRESQL LIVE SLICES GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
