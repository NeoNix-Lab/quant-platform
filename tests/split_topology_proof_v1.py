"""The hermetic topology proof of ADR-0072 section 7 (S07, issue #397).

``SplitTopologyProofV1`` composes the split server/deck topology on one machine and proves it end to end:

* the API and the worker are **separate processes** started from the commands of the units in ``infra/systemd/``
  (``ExecStart`` and the environment example, with only host-local values overridden: addresses, files, DSNs);
* TLS 1.3 mutual TLS over a CA, a server certificate and client certificates **issued by ``infra/pki/``**;
* a disposable PostgreSQL (catalog, Experiment and runtime schemas) and a catalog built from generated ``trade-v1``
  Parquet partitions;
* the deck is this test process: ``clients/deck/`` for the protocol and ``deck_result_bundle`` for the result, running
  a real replay and a real training over the bytes the server delivered.

The class needs the disposable database (``RUNTIME_TEST_*_DSN``); it is run by
``tests/integration_runtime_stores_postgres.py`` (``PostgresSplitTopology``) and is not collected on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pyarrow as pa
import pyarrow.parquet as pq
import websockets

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tests", ROOT / "tools", ROOT / "clients" / "deck"):
    sys.path.insert(0, str(extra))

import deck_handoff_client as deck  # noqa: E402
from quant_platform.application import strategy_transport as strategy_codec  # noqa: E402
from quant_platform.application import training_transport as training_codec  # noqa: E402
from quant_platform.application import validation_transport as validation_codec  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputState, AdmittedInputStore  # noqa: E402
from quant_platform.application.admitted_input_authority import ADMITTED_INPUT_BOUNDS_V1  # noqa: E402
from quant_platform.application.deck_result_bundle import (  # noqa: E402
    DeckAdmission,
    DeckOutput,
    DeckRunDeclaration,
    FeatureProviderSource,
    WorkloadTree,
    build_deck_result,
    inspect_workload_tree,
)
from quant_platform.application.durable_jobs import DurableJobStore, JobState  # noqa: E402
from quant_platform.application.golden_replay import (  # noqa: E402
    MinimalBreakoutFeatureProvider,
    build_golden_replay_spec,
)
from quant_platform.application.golden_supervised import run_wave5_golden_supervised_proof  # noqa: E402
from quant_platform.application.job_transport import J02_JOB_FAMILY, J02_JOB_REQUEST_VERSION  # noqa: E402
from quant_platform.application.strategy_consumer import execute_strategy_compose  # noqa: E402
from quant_platform.application.training_consumer import execute_supervised_train_evaluate  # noqa: E402
from quant_platform.canonical import canonical_bytes  # noqa: E402
from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.data.parquet import scan_trade_v1  # noqa: E402
from quant_platform.experiments import ExperimentRepository  # noqa: E402
from quant_platform.experiments.identities import (  # noqa: E402
    ComparisonProtocolIdentity,
    IdentityReference,
    StudyIdentity,
)
from quant_platform.replay import HistoricalReplayRuntime  # noqa: E402
import test_deployment_units_v1 as units  # noqa: E402
import test_durable_job_runtime_v1 as j03  # noqa: E402
from test_deck_handoff_client_acceptance_v1 import ConsistentRewrite, FlipOneFrameByte  # noqa: E402
from test_learning_supervised_training_v1 import projection as training_projection  # noqa: E402
from test_pki_operations_v1 import BASH, OPENSSL, fields as pki_fields, run as pki_run  # noqa: E402
from test_strategy_consumer_api_v1 import request as strategy_request  # noqa: E402
from test_training_consumer_v1 import request_for as training_request_for  # noqa: E402
from test_validation_transport_v1 import EXECUTORS, result_payload, vectors as validation_vectors  # noqa: E402

MIB = 1024 * 1024
DAY_ONE, DAY_TWO = date(2024, 1, 15), date(2024, 1, 16)
SELECTOR = {
    "venue": "bybit", "instrument": "BTCUSDT",
    "start": "2024-01-15T00:00:00Z", "end": "2024-01-17T00:00:00Z",
    "representation": {"kind": "trades", "version": 1},
}
MAX_DAYS = (date(2024, 2, 1), date(2024, 2, 2))
MAX_SELECTOR = dict(SELECTOR, start="2024-02-01T00:00:00Z", end="2024-02-03T00:00:00Z")
OPERATION = "strategy-compose-v1"
REPOSITORY_NAME = "NeoNix-Lab/quant-platform"
ANALYST_SCOPES = (
    "j02.strategy.compose", "j02.validation.evaluate", "j02.training.evaluate", "j02.training.register",
    "j02.jobs.submit", "j02.jobs.read",
)
DECK_SCOPES = ("j02.admitted_input.read", "j02.result.submit")
FAMILIES = "strategy,validation,training,jobs,admitted-input,result-import"
DSN_ROLES = ("ADMIN", "API", "WORKER")


# -- generated trade-v1 fixtures ------------------------------------------------------------------


def _timestamps(day: date, count: int) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    step_us = 86_400_000_000 // count  # strictly increasing, inside the day
    return [start + timedelta(microseconds=step_us * index) for index in range(count)]


def trade_table(
    day: date, count: int, *, seed: int, first_id: int = 1, noise: bool = False, instrument: str = "BTCUSDT",
) -> pa.Table:
    """``count`` ordered trade-v1 rows of one UTC day: a seeded walk that rises, then falls."""
    rng = random.Random(seed)
    stamps = _timestamps(day, count)
    prices, price = [], 100.0
    for index in range(count):
        drift = 0.04 if index < count // 2 else -0.04
        price = max(1.0, price + drift + rng.uniform(-0.3, 0.3))
        prices.append(f"{price:.2f}")
    if noise:  # incompressible text, so a partition really occupies its bytes
        prices = [f"{rng.uniform(1, 99999):.6f}" for _ in range(count)]
    sizes = [f"{rng.uniform(0.001, 5):.6f}" if noise else "0.100" for _ in range(count)]
    return pa.table({
        "venue": ["bybit"] * count,
        "instrument": [instrument] * count,
        "exchange_ts": pa.array(stamps, type=pa.timestamp("ns", tz="UTC")),
        "price": prices,
        "size": sizes,
        "aggressor_side": ["buy" if index % 2 == 0 else "sell" for index in range(count)],
        "receive_ts": pa.array([None] * count),
        "trade_id": [str(first_id + index) for index in range(count)],
        "sequence": pa.array([None] * count),
    })


def write_partition(root: Path, rel_root: str, key: str, table: pa.Table) -> tuple[str, Path]:
    rel_path = f"{key}/part-000.parquet"
    target = root / rel_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, target, compression="none")
    return rel_path, target


def seed_dataset(cursor, root: Path, instrument: str, partitions) -> str:
    """Register ``partitions`` (``(day, table)`` pairs) in the canonical trades dataset of ``bybit:<instrument>``."""
    rel_root = f"canonical/trades/bybit/{instrument}/trade-v1"
    cursor.execute(
        "INSERT INTO catalog.schema_registry (schema_id, name, version, json_sha256, body) "
        "VALUES ('trade-v1', 'trade', 1, %s, '{}'::jsonb) ON CONFLICT (schema_id) DO NOTHING", ("a" * 64,),
    )
    cursor.execute(
        "SELECT dataset_id FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' "
        "AND venue = 'bybit' AND instrument = %s", (instrument,),
    )
    found = cursor.fetchone()
    if found is None:
        cursor.execute(
            "INSERT INTO catalog.datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256) "
            "VALUES ('canonical', 'trades', 'bybit', %s, %s, 'trade-v1', %s) RETURNING dataset_id",
            (instrument, rel_root, hashlib.sha256(instrument.encode()).hexdigest()),
        )
        found = cursor.fetchone()
    dataset_id = found[0]
    for day, table in partitions:
        key = f"dt={day.isoformat()}"
        rel_path, target = write_partition(root, rel_root, key, table)
        data = target.read_bytes()
        cursor.execute(
            "INSERT INTO catalog.partitions (dataset_id, partition_key, storage_root_id, rel_path, ts_start, ts_end, "
            "row_count, byte_size, content_sha256, manifest_sha256, state, closed_at, producer, code_ref) "
            "VALUES (%s, %s, 'hot', %s, %s, %s, %s, %s, %s, %s, 'valid', now(), 'split-proof', 'test')",
            (
                dataset_id, key, rel_path, f"{day.isoformat()}T00:00:00Z",
                f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z", table.num_rows, len(data),
                hashlib.sha256(data).hexdigest(), hashlib.sha256(f"{instrument}:{key}".encode()).hexdigest(),
            ),
        )
    return rel_root


# -- measuring ------------------------------------------------------------------------------------


def peak_memory_bytes(pid: int) -> int | None:
    """The peak resident set of a process, or None where the platform cannot say."""
    if sys.platform.startswith("linux"):
        try:
            for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            return None
        return None
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000 | 0x0400, False, pid)  # QUERY_LIMITED_INFORMATION | QUERY_INFORMATION
        if not handle:
            return None
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            if not psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PeakWorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    return None


# -- the processes --------------------------------------------------------------------------------


class Process:
    """A child process with its output drained, so it never blocks on a full pipe."""

    def __init__(self, argv, *, env, cwd):
        self.popen = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.lines: queue.Queue = queue.Queue()
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        for stream, sink, tag in ((self.popen.stdout, self.stdout, "out"), (self.popen.stderr, self.stderr, "err")):
            threading.Thread(target=self._drain, args=(stream, sink, tag), daemon=True).start()

    def _drain(self, stream, sink, tag) -> None:
        for line in stream:
            line = line.rstrip("\n")
            sink.append(line)
            self.lines.put((tag, line))

    @property
    def pid(self) -> int:
        return self.popen.pid

    def wait_for(self, text: str, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.stdout + self.stderr):
                return
            if self.popen.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"{text!r} not seen; exit={self.popen.poll()!r} out={self.stdout!r} err={self.stderr!r}")

    def stop(self) -> None:
        if self.popen.poll() is None:
            self.popen.terminate()
            try:
                self.popen.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait(timeout=15)
        for stream in (self.popen.stdout, self.popen.stderr):
            with contextlib.suppress(Exception):
                stream.close()


def free_port() -> int:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        return holder.getsockname()[1]


def unit_argv(unit: Path) -> list[str]:
    """The ``ExecStart`` of a unit, with the host's interpreter and checkout in place of /opt/market-platform."""
    exec_start = units.single(units.parse_unit(unit)["Service"], "ExecStart")
    interpreter, script = exec_start.split()
    assert interpreter == "/opt/market-platform/.venv/bin/python", interpreter
    assert script.startswith("/opt/market-platform/tools/"), script
    return [sys.executable, "-u", str(ROOT / script.removeprefix("/opt/market-platform/"))]


def libpq_connection(role: str, directory: Path) -> str:
    """A key/value DSN for a test role with its password in a passfile, as the units' DSNs do."""
    info = conninfo_to_dict(os.environ[f"RUNTIME_TEST_{role}_DSN"])
    passfile = directory / f"{role.lower()}.pgpass"
    escape = lambda text: str(text).replace("\\", "\\\\").replace(":", "\\:")  # noqa: E731
    passfile.write_text(
        ":".join(escape(info.get(key, "")) for key in ("host", "port", "dbname", "user", "password")) + "\n",
        encoding="utf-8",
    )
    passfile.chmod(0o600)
    return (
        f"host={info['host']} port={info.get('port', '5432')} dbname={info['dbname']} "
        f"user={info['user']} passfile='{passfile.as_posix()}'"
    )


def connect(role: str, *, autocommit: bool = True):
    return psycopg.connect(os.environ[f"RUNTIME_TEST_{role}_DSN"], autocommit=autocommit)


# -- the deck's workload --------------------------------------------------------------------------


class DeliveredGateway:
    """The deck's view of the bytes it was delivered: what ``HistoricalReplayRuntime`` reads through ``scan``."""

    def __init__(self, members, directory: Path):
        self._paths = []
        for index, data in enumerate(members):
            path = directory / f"member-{index}.parquet"
            path.write_bytes(data)
            self._paths.append(path)

    def scan(self, request, *, batch_size: int = 65_536):
        start, end = Instant.parse(request.start), Instant.parse(request.end)
        for path in self._paths:
            yield from scan_trade_v1(path, start, end, batch_size)

    def records(self):
        for path in self._paths:
            for batch in scan_trade_v1(path, Instant.parse("1970-01-01T00:00:00Z"), Instant.parse("2100-01-01T00:00:00Z")):
                yield from batch


def run_replay(gateway: DeliveredGateway):
    spec = build_golden_replay_spec(start=SELECTOR["start"], end=SELECTOR["end"])
    return HistoricalReplayRuntime(gateway, MinimalBreakoutFeatureProvider(20)).run(spec)


def run_training(gateway: DeliveredGateway, directory: Path):
    """A small supervised training over the first and last candle of the first delivered day."""
    day = [record for record in gateway.records() if record.exchange_ts.isoformat().startswith(DAY_ONE.isoformat())]

    def candle(records):
        prices = [record.price for record in records]
        return {
            "bucket_start": records[0].exchange_ts.isoformat(), "bucket_end": records[-1].exchange_ts.isoformat(),
            "open": prices[0], "high": max(prices, key=float), "low": min(prices, key=float), "close": prices[-1],
        }

    fixture = directory / "delivered-candles.json"
    fixture.write_text(
        json.dumps({"candle": {"first_candle": candle(day[:40]), "last_candle": candle(day[-40:])}}), encoding="utf-8",
    )
    return run_wave5_golden_supervised_proof(fixture_path=fixture, code_ref="split-topology-proof-v1")


def canonical_document(value) -> bytes:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False)


# -- the proof ------------------------------------------------------------------------------------


class SplitTopologyProofV1(unittest.IsolatedAsyncioTestCase):
    """Subclasses add the database cleanup; ``setUpClass`` builds the PKI, the catalog and the deck checkout."""

    evidence: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        for role in DSN_ROLES:
            if not os.environ.get(f"RUNTIME_TEST_{role}_DSN"):
                raise unittest.SkipTest(f"RUNTIME_TEST_{role}_DSN is required")
        if not (BASH and OPENSSL):
            raise unittest.SkipTest("bash and openssl are required for infra/pki")
        cls._scratch = tempfile.TemporaryDirectory(prefix="split_topology_")
        cls.addClassCleanup(cls._scratch.cleanup)
        cls.scratch = Path(cls._scratch.name)
        cls.passfiles = cls.scratch / "pg"
        cls.passfiles.mkdir()
        cls.build_pki()
        cls.build_deck_checkout()
        cls.build_catalog()
        cls.addClassCleanup(cls.drop_catalog)
        cls.addClassCleanup(cls.report_evidence)

    # PKI: infra/pki, the same scripts the runbook names ---------------------------------------------

    @classmethod
    def build_pki(cls) -> None:
        cls.pki = cls.scratch / "pki"
        cls.pki.mkdir()
        pki_run("ca-init.sh", cls.pki.as_posix(), "split-proof-ca")
        pki_run("issue-server.sh", cls.pki.as_posix(), "api", "DNS:localhost,IP:127.0.0.1", "t1")
        cls.fingerprints = {}
        for principal in ("deck-a", "deck-c", "analyst", "deck-unmapped"):
            cls.fingerprints[principal] = pki_fields(pki_run("issue-client.sh", cls.pki.as_posix(), principal, "t1"))[
                "fingerprint"
            ]
        cls.other_pki = cls.scratch / "other-pki"
        cls.other_pki.mkdir()
        pki_run("ca-init.sh", cls.other_pki.as_posix(), "other-ca")
        pki_run("issue-client.sh", cls.other_pki.as_posix(), "deck-a", "t1")

    @classmethod
    def credentials(cls, principal: str, label: str = "t1", *, other_ca: bool = False) -> dict:
        base = cls.other_pki if other_ca else cls.pki
        directory = base / "clients" / principal / label
        return {
            "client_certificate_path": str(directory / "client.crt"),
            "client_private_key_path": str(directory / "client.key"),
            "server_trust_anchor_path": str(cls.pki / "ca" / "ca.crt"),
        }

    # the deck's checkout of the server's commit -------------------------------------------------------

    @classmethod
    def build_deck_checkout(cls) -> None:
        cls.server_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        cls.deck_tree = cls.scratch / "deck-checkout"
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(ROOT), str(cls.deck_tree)], check=True)
        subprocess.run(
            ["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", cls.server_commit],
            cwd=cls.deck_tree, check=True,
        )

    # the catalog: generated trades, registered through the admin login -----------------------------------

    @classmethod
    def build_catalog(cls) -> None:
        cls.storage = cls.scratch / "marketdata"
        cls.storage.mkdir()
        with connect("ADMIN") as admin:
            cls._previous_root = admin.execute(
                "SELECT abs_path FROM catalog.storage_roots WHERE storage_root_id = 'hot'"
            ).fetchone()[0]
            clash = admin.execute(
                "SELECT count(*) FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' "
                "AND instrument = 'BTCUSDT'"
            ).fetchone()[0]
            if clash:
                raise RuntimeError("the disposable catalog already holds the proof's datasets")
            admin.execute("UPDATE catalog.storage_roots SET abs_path = %s WHERE storage_root_id = 'hot'", (str(cls.storage),))
            with admin.cursor() as cursor:
                seed_dataset(cursor, cls.storage, "BTCUSDT", [
                    (DAY_ONE, trade_table(DAY_ONE, 1200, seed=7, first_id=1)),
                    (DAY_TWO, trade_table(DAY_TWO, 1200, seed=8, first_id=1_000_001)),
                ])

    @classmethod
    def drop_catalog(cls) -> None:
        with connect("ADMIN") as admin:
            admin.execute(
                "DELETE FROM catalog.datasets WHERE layer = 'canonical' AND kind = 'trades' AND venue = 'bybit' "
                "AND instrument = 'BTCUSDT'"
            )
            admin.execute("UPDATE catalog.storage_roots SET abs_path = %s WHERE storage_root_id = 'hot'", (cls._previous_root,))

    @classmethod
    def report_evidence(cls) -> None:
        print("SPLIT_TOPOLOGY_EVIDENCE " + json.dumps(cls.evidence, sort_keys=True))
        target = os.environ.get("SPLIT_PROOF_EVIDENCE_FILE")
        if target:
            Path(target).write_text(json.dumps(cls.evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # per test: principals, the two processes --------------------------------------------------------------

    def write_principals(self, entries: dict[str, tuple[str, ...]]) -> None:
        self.principals = self.scratch / "principals.json"
        self.principals.write_text(json.dumps({"version": 1, "principals": [
            {"principal_id": name, "fingerprint": fingerprint, "scopes": list(scopes)}
            for name, (fingerprint, scopes) in entries.items()
        ]}), encoding="utf-8")

    def default_principals(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        return {
            "deck-a": (self.fingerprints["deck-a"], DECK_SCOPES),
            "deck-c": (self.fingerprints["deck-c"], ("j02.admitted_input.read",)),
            "analyst": (self.fingerprints["analyst"], ANALYST_SCOPES),
        }

    def environment(self, unit_env: Path, **overrides) -> dict[str, str]:
        base = {name: value for name, value in os.environ.items() if not name.startswith(("QP_", "CATALOG_DSN"))}
        base.update(units.parse_environment(unit_env))
        base.update(overrides)
        return base

    def start_api(self, **overrides) -> Process:
        server = self.pki / "servers" / "api" / "t1"
        environment = self.environment(
            units.API_ENV,
            CATALOG_DSN=libpq_connection("API", self.passfiles),
            QP_RUNTIME_DSN=libpq_connection("API", self.passfiles),
            QP_API_PORT=str(self.port),
            QP_API_TLS_CERTIFICATE=str(server / "server.crt"),
            QP_API_TLS_PRIVATE_KEY=str(server / "server.key"),
            QP_API_CLIENT_TRUST_ANCHOR=str(self.pki / "ca" / "ca.crt"),
            QP_API_PRINCIPALS_FILE=str(self.principals),
            QP_API_FAMILIES=FAMILIES,  # the example serves the deck's two; the proof also exercises J10/J12/J13/jobs
            **overrides,
        )
        process = Process(unit_argv(units.API_UNIT), env=environment, cwd=ROOT)
        self.addCleanup(process.stop)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.popen.poll() is not None:
                raise AssertionError(f"the API exited {process.popen.returncode}: {process.stdout} {process.stderr}")
            with contextlib.suppress(OSError):
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return process
            time.sleep(0.1)
        raise AssertionError(f"the API never listened: {process.stdout} {process.stderr}")

    def start_worker(self, **overrides) -> Process:
        environment = self.environment(
            units.WORKER_ENV,
            QP_RUNTIME_DSN=libpq_connection("WORKER", self.passfiles),
            QP_WORKER_LOCK_DIR=str(self.lock_directory),
            QP_WORKER_POLL_SECONDS="0.2",
            **overrides,
        )
        process = Process(unit_argv(units.WORKER_UNIT), env=environment, cwd=ROOT)
        self.addCleanup(process.stop)
        return process

    def restart_api(self) -> Process:
        self.api.stop()
        self.api = self.start_api()
        return self.api

    async def asyncSetUp(self) -> None:
        self.port = free_port()
        self.url = f"wss://127.0.0.1:{self.port}"
        self.lock_directory = Path(tempfile.mkdtemp(dir=self.scratch))
        self.write_principals(self.default_principals())
        self.api = self.start_api()
        self.worker = self.start_worker()
        self.worker.wait_for("J03_WORKER: READY")
        self.dispose = contextlib.AsyncExitStack()

    async def asyncTearDown(self) -> None:
        await self.dispose.aclose()

    # the deck -------------------------------------------------------------------------------------

    def client(self, principal: str = "deck-a", *, connect_factory=websockets.connect, label: str = "t1",
               other_ca: bool = False) -> deck.DeckHandoffClient:
        return deck.DeckHandoffClient.from_credentials(
            self.url, connect=connect_factory, **self.credentials(principal, label, other_ca=other_ca),
        )

    def deck_admission(self, admission: deck.Admission) -> DeckAdmission:
        return DeckAdmission(admission.admission_id, admission.manifest_digest, admission.manifest)

    def declaration(self, execution_id: str) -> DeckRunDeclaration:
        study = StudyIdentity(
            "split-topology-proof-v1",
            IdentityReference("research", "research-v1:split-topology"),
            IdentityReference("objective", "objective-v1:split-topology"),
            ComparisonProtocolIdentity(IdentityReference("protocol", "protocol-v1:split-topology")),
        )
        return DeckRunDeclaration(study=study, execution_id=execution_id, run_configuration={"proof": "s07"})

    async def deliver(self, client: deck.DeckHandoffClient, selector=None):
        admission = await client.admit(operation_identity=OPERATION, dataset=dict(selector or SELECTOR))
        return admission, await client.fetch_inputs(admission)

    def workload_results(self, delivered: deck.AdmittedInput, admission: deck.Admission, tag: str):
        """Run the replay and the training over the delivered bytes; the two results to upload and submit."""
        directory = Path(tempfile.mkdtemp(dir=self.scratch))
        gateway = DeliveredGateway(delivered.members, directory)
        replay = run_replay(gateway)
        training = run_training(gateway, directory)
        tree = inspect_workload_tree(self.deck_tree)
        self.assertEqual(WorkloadTree(self.server_commit, True), tree)
        provider = FeatureProviderSource(
            repository=REPOSITORY_NAME, qualified_name="quant_platform.application.golden_replay:MinimalBreakoutFeatureProvider",
            source_sha256=hashlib.sha256(b"split-topology-proof").hexdigest(), parameters={"lookback": 20},
        )
        replay_result = build_deck_result(
            self.deck_admission(admission), tree, self.declaration(f"replay-{tag}"),
            [DeckOutput("replay-result", canonical_document(replay.stable_dict())),
             DeckOutput("replay-summary", canonical_document(replay.summary.stable_dict()))],
            runtime_environment={"python": platform.python_version()}, replay_provider=provider,
        )
        first = training.first
        training_result = build_deck_result(
            self.deck_admission(admission), tree, self.declaration(f"training-{tag}"),
            [DeckOutput("normalizer", canonical_document(first.normalizer.stable_dict())),
             DeckOutput("model", canonical_document(first.model.stable_dict())),
             DeckOutput("predictions", canonical_document([item.stable_dict() for item in first.predictions])),
             DeckOutput("metrics", canonical_document(first.metrics.stable_dict()))],
            runtime_environment={"python": platform.python_version()},
        )
        return replay, replay_result, training_result

    async def upload_and_submit(self, client, result):
        for upload in result.uploads:
            staged = await client.upload_output(upload.payload)
            self.assertEqual(upload.content_checksum, staged.checksum)
        return await client.submit(result.wire)

    # database observations ---------------------------------------------------------------------------

    def count(self, table: str) -> int:
        with connect("ADMIN") as admin:
            return admin.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def experiment_counts(self) -> tuple[int, ...]:
        return tuple(self.count(f"experiment.{table}") for table in ("runs", "artifacts", "artifact_contents"))

    def evidence_counts(self) -> tuple[int, int]:
        return self.count("runtime.governed_result_bundles"), self.count("runtime.governed_result_outputs")

    def admission_state(self, admission_id: str):
        with connect("API") as connection:
            return AdmittedInputStore(connection).get(admission_id)

    # -- acceptance 1: the whole procedure ----------------------------------------------------------------

    async def test_admit_deliver_verify_run_upload_submit_and_an_identical_resubmission(self) -> None:
        async with self.client() as client:
            admission, delivered = await self.deliver(client)
            self.assertEqual(self.server_commit, admission.manifest["git_identity"])
            self.assertEqual(2, len(delivered.members))
            self.assertEqual(AdmittedInputState.DELIVERED, self.admission_state(admission.admission_id).state)
            replay, replay_result, training_result = self.workload_results(delivered, admission, "main")
            self.assertGreater(len(replay.fills), 0, "the proof replay must trade")
            before_experiment, before_evidence = self.experiment_counts(), self.evidence_counts()
            self.assertEqual((0, 0), before_evidence)

            registered = await self.upload_and_submit(client, replay_result)
            self.assertEqual({"replay-result", "replay-summary"}, {item["artifact_role"] for item in registered.outputs})
            training_registered = await self.upload_and_submit(client, training_result)
            self.assertEqual(
                {"normalizer", "model", "predictions", "metrics"},
                {item["artifact_role"] for item in training_registered.outputs},
            )

            # The registration is in the Experiment tables and the evidence ledger, for both bundles.
            runs, artifacts, contents = self.experiment_counts()
            self.assertEqual(before_experiment[0] + 2, runs)
            self.assertEqual(before_experiment[1] + 6, artifacts)
            self.assertEqual((2, 6), self.evidence_counts())
            with connect("ADMIN") as admin:
                digests = {row[0] for row in admin.execute("SELECT bundle_digest FROM runtime.governed_result_bundles")}
            self.assertEqual({registered.bundle_digest, training_registered.bundle_digest}, digests)

            # The same bundle again: the same registration, nothing new.
            again = await client.submit(replay_result.wire)
            self.assertEqual(registered, again)
            self.assertEqual((2, 6), self.evidence_counts())
            self.assertEqual((runs, artifacts, contents), self.experiment_counts())
        # After an API restart nothing is staged at all, and the ledger alone recognises the bundle.
        self.restart_api()
        async with self.client() as fresh:
            self.assertEqual(registered, await fresh.submit(replay_result.wire))
        self.assertEqual((2, 6), self.evidence_counts())

    # -- acceptance 2: J10, J12, J13 over the same listener ---------------------------------------------------

    async def exchange(self, principal: str, messages, *, timeout: float = 60.0):
        context = deck.build_client_ssl_context(**self.credentials(principal))
        responses = []
        async with websockets.connect(self.url, ssl=context, max_size=deck.J02_MAX_WIRE_MESSAGE_BYTES) as socket_:
            for message in messages:
                await socket_.send(message if isinstance(message, str) else json.dumps(message))
                responses.append(json.loads(await asyncio.wait_for(socket_.recv(), timeout)))
        return responses

    async def test_j10_j12_and_j13_over_the_listener_equal_the_in_process_results(self) -> None:
        from decimal import Decimal

        strategy_items = [strategy_request(value=Decimal(text)) for text in ("0.5", "2", "0")]
        messages = [
            json.dumps({
                "message_family": strategy_codec.J02_STRATEGY_FAMILY,
                "schema_version": strategy_codec.J02_STRATEGY_REQUEST_VERSION,
                "operation": "compose", "request_id": f"j10-{index}",
                "request": strategy_codec.encode_strategy_compose_request(item),
            })
            for index, item in enumerate(strategy_items)
        ]
        for item, response in zip(strategy_items, await self.exchange("analyst", messages)):
            self.assertEqual("ok", response["status"])
            self.assertEqual(execute_strategy_compose(item).stable_dict(), response["result"])

        validation = list(validation_vectors())
        messages = [
            json.dumps({
                "message_family": validation_codec.J02_VALIDATION_FAMILY,
                "schema_version": validation_codec.J02_VALIDATION_REQUEST_VERSION,
                "operation": operation, "request_id": f"j12-{index}",
                "request": validation_codec.encode_validation_request(item),
            })
            for index, (operation, item) in enumerate(validation)
        ]
        for (operation, item), response in zip(validation, await self.exchange("analyst", messages)):
            with self.subTest(operation=operation):
                self.assertEqual("ok", response["status"])
                self.assertEqual(
                    canonical_document(result_payload(EXECUTORS[operation](item))),
                    canonical_document(validation_codec.decode_validation_result(response["result"])),
                )

        item = training_request_for(training_projection())
        expected = json.loads(json.dumps(execute_supervised_train_evaluate(item).stable_dict()))
        without, registered, resent = await self.exchange("analyst", [
            training_codec.encode_train_evaluate_message(item, request_id="j13-a"),
            training_codec.encode_train_evaluate_message(item, register=True, request_id="j13-b"),
            training_codec.encode_train_evaluate_message(item, register=True, request_id="j13-c"),
        ])
        for response in (without, registered, resent):
            self.assertEqual("ok", response["status"])
            self.assertEqual(expected, response["result"])
        # register: false wrote nothing; register: true wrote the run once, and a resend added nothing.
        with connect("API") as connection:
            records = ExperimentRepository(connection).list_runs_for_run_spec(item.run_identity.run_spec_identity)
        self.assertEqual(1, len([record for record in records if record.execution_id == item.run_identity.execution_id]))

    async def test_without_the_register_flag_nothing_is_written(self) -> None:
        item = training_request_for(training_projection(), execution_id="not-registered")
        (response,) = await self.exchange("analyst", [training_codec.encode_train_evaluate_message(item, request_id="n")])
        self.assertEqual("ok", response["status"])
        self.assertEqual(0, self.experiment_counts()[0])

    # -- acceptance 3: j02-job-v1 composed, the registry empty --------------------------------------------------

    async def test_the_job_family_is_composed_and_refuses_an_unregistered_kind(self) -> None:
        body = {
            "operation_kind": "catalog-maintenance-v1", "request_identity": "request:split-proof",
            "canonical_parameters": {"partition": "2024-01-15"}, "input_identities": ["dataset:sha256:aaa"],
            "effect_safety_proof_reference": None,
        }
        (response,) = await self.exchange("analyst", [{
            "message_family": J02_JOB_FAMILY, "schema_version": J02_JOB_REQUEST_VERSION,
            "operation": "submit", "request_id": "job-1", "request": body,
        }])
        self.assertEqual("error", response["status"])
        self.assertEqual("invalid_request", response["error"]["code"])
        self.assertEqual("unregistered_operation_kind", response["error"]["context"]["reason"])
        self.assertEqual(0, self.count("runtime.durable_jobs"))

    # -- acceptance 4: faults -------------------------------------------------------------------------------------

    async def test_a_lost_deliver_response_is_ended_by_the_deck_and_redelivered(self) -> None:
        async with self.client() as lost:  # the response to this deliver is never read
            admission = await lost.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))
            await lost._call(deck.ADMITTED_INPUT_FAMILY, "deliver", {"admission_id": admission.admission_id})
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.admission_state(admission.admission_id).state)
        async with self.client() as client:
            ended = await client.end_pending_attempt(admission.admission_id)
            self.assertEqual("DELIVERY_FAILED", ended.state)
            delivered = await client.fetch_inputs(ended)
            self.assertEqual(2, len(delivered.members))
            # A report about the replaced first attempt ends nothing and is refused.
            with self.assertRaises(deck.RemoteConsumerApiError) as raised:
                await client._call(deck.ADMITTED_INPUT_FAMILY, "acknowledge", {
                    "admission_id": admission.admission_id, "delivery_attempt": 1, "outcome": "failed",
                    "manifest_digest": None, "failure_reason": "delivery_interrupted", "failed_member_index": None,
                })
            self.assertEqual("delivery_attempt_mismatch", raised.exception.context.get("reason"))
        self.assertEqual(AdmittedInputState.DELIVERED, self.admission_state(admission.admission_id).state)

    async def test_a_corrupted_frame_and_a_consistently_rewritten_member_are_refused_by_the_client(self) -> None:
        for wrapper, reason in ((FlipOneFrameByte, None), (ConsistentRewrite, "content_hash_mismatch")):
            with self.subTest(wrapper.__name__):
                async with self.client(connect_factory=wrapper(websockets.connect)) as client:
                    admission = await client.admit(
                        operation_identity=OPERATION, profile_identity=f"split-proof-{wrapper.__name__}",
                        dataset=dict(SELECTOR),
                    )
                    with self.assertRaises(deck.DeliveryIntegrityFailure) as raised:
                        await client.fetch_inputs(admission)
                    if reason:
                        self.assertEqual(reason, raised.exception.reason)
                self.assertEqual(AdmittedInputState.DELIVERY_FAILED, self.admission_state(admission.admission_id).state)
                async with self.client() as honest:  # the same sealed admission delivers on a new attempt
                    delivered = await honest.fetch_inputs(admission)
                    self.assertEqual(2, len(delivered.members))

    async def test_a_bundle_with_a_wrong_output_checksum_is_refused_and_nothing_is_registered(self) -> None:
        async with self.client() as client:
            admission, delivered = await self.deliver(client)
            _, replay_result, _ = self.workload_results(delivered, admission, "wrong")
            for upload in replay_result.uploads:
                await client.upload_output(upload.payload)
            before = (self.experiment_counts(), self.evidence_counts())
            wire = json.loads(json.dumps(replay_result.wire))
            wire["outputs"][0]["content_checksum"] = "sha256:" + hashlib.sha256(b"not the uploaded bytes").hexdigest()
            with self.assertRaises(deck.RemoteConsumerApiError):
                await client.submit(wire)
            self.assertEqual(before, (self.experiment_counts(), self.evidence_counts()))

    async def test_a_failed_ledger_write_rolls_back_the_experiment_registration_of_the_same_transaction(self) -> None:
        async with self.client() as client:
            admission, delivered = await self.deliver(client)
            _, replay_result, _ = self.workload_results(delivered, admission, "atomic")
            for upload in replay_result.uploads:
                await client.upload_output(upload.payload)
            before = (self.experiment_counts(), self.evidence_counts())
            with connect("ADMIN") as admin:
                admin.execute("REVOKE INSERT ON runtime.governed_result_bundles FROM runtime_writer")
            try:
                with self.assertRaises(deck.RemoteConsumerApiError):
                    await client.submit(replay_result.wire)
                # The Experiment rows were written before the ledger failed: none of them survives.
                self.assertEqual(before, (self.experiment_counts(), self.evidence_counts()))
            finally:
                with connect("ADMIN") as admin:
                    admin.execute("GRANT INSERT ON runtime.governed_result_bundles TO runtime_writer")
            # With the ledger writable again the same bundle registers, from the same staged bytes.
            registered = await client.submit(replay_result.wire)
            self.assertEqual({"replay-result", "replay-summary"}, {item["artifact_role"] for item in registered.outputs})
            self.assertEqual(before[0][0] + 1, self.experiment_counts()[0])
            self.assertEqual((1, 2), self.evidence_counts())

    async def test_an_api_restart_between_upload_and_submit_leaves_nothing_staged_or_registered(self) -> None:
        async with self.client() as client:
            admission, delivered = await self.deliver(client)
            _, replay_result, _ = self.workload_results(delivered, admission, "restart")
            for upload in replay_result.uploads:
                await client.upload_output(upload.payload)
        before = (self.experiment_counts(), self.evidence_counts())
        self.restart_api()
        async with self.client() as client:
            with self.assertRaises(deck.RemoteConsumerApiError):
                await client.submit(replay_result.wire)
            self.assertEqual(before, (self.experiment_counts(), self.evidence_counts()))
            # The admission survived the restart (it is in PostgreSQL); the result is submitted again in full.
            for upload in replay_result.uploads:
                await client.upload_output(upload.payload)
            registered = await client.submit(replay_result.wire)
            self.assertEqual({"replay-result", "replay-summary"}, {item["artifact_role"] for item in registered.outputs})

    async def test_an_api_restart_between_deliver_and_acknowledge_is_recovered_by_the_deck(self) -> None:
        async with self.client() as first:
            admission = await first.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))
            await first._call(deck.ADMITTED_INPUT_FAMILY, "deliver", {"admission_id": admission.admission_id})
        self.restart_api()
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, self.admission_state(admission.admission_id).state)
        async with self.client() as client:
            ended = await client.end_pending_attempt(admission.admission_id)
            self.assertEqual("DELIVERY_FAILED", ended.state)
            delivered = await client.fetch_inputs(ended)
            self.assertEqual(2, len(delivered.members))
        self.assertEqual(AdmittedInputState.DELIVERED, self.admission_state(admission.admission_id).state)

    async def test_a_worker_on_another_commit_exits_3_and_touches_no_job(self) -> None:
        self.worker.stop()
        connection = connect("WORKER")
        self.addCleanup(connection.close)
        store = DurableJobStore(connection)
        job = store.admit(j03.admission())
        store.transition(job.job_id, JobState.QUEUED)
        store.claim(job.job_id)
        before = (store.get(job.job_id), store.attempts(job.job_id))
        stray = self.start_worker(QP_GIT_COMMIT="b" * 40)
        self.assertEqual(3, stray.popen.wait(timeout=60))
        self.assertTrue(any("differs" in line for line in stray.stderr), stray.stderr)
        self.assertEqual(before, (store.get(job.job_id), store.attempts(job.job_id)))
        self.assertEqual(JobState.RUNNING, store.get(job.job_id).state)

    # -- acceptance 5: security -----------------------------------------------------------------------------------

    async def test_a_certificate_of_another_ca_an_unmapped_fingerprint_and_a_missing_scope_are_denied(self) -> None:
        with self.assertRaises(deck.RemoteSecurityFailure):  # another CA: no handshake
            async with self.client(other_ca=True) as client:
                await client.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))
        with self.assertRaises(deck.RemoteSecurityFailure):  # chains to the CA, maps to no principal
            async with self.client("deck-unmapped") as client:
                await client.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))
        async with self.client("deck-c") as client:  # holds admitted-input only
            admission = await client.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))
            self.assertTrue(admission.admission_id)
        async with self.client("deck-c") as client:
            with self.assertRaises(deck.RemoteSecurityFailure):  # result-import needs j02.result.submit
                await client.upload_output(b"x")
        for family_message in ({
            "message_family": strategy_codec.J02_STRATEGY_FAMILY,
            "schema_version": strategy_codec.J02_STRATEGY_REQUEST_VERSION,
            "operation": "compose", "request_id": "x", "request": {},
        },):
            context = deck.build_client_ssl_context(**self.credentials("deck-a"))
            async with websockets.connect(self.url, ssl=context) as socket_:
                await socket_.send(json.dumps(family_message))  # deck-a holds no strategy scope
                with self.assertRaises(websockets.ConnectionClosed) as closed:
                    await asyncio.wait_for(socket_.recv(), 10)
                self.assertEqual(1008, closed.exception.rcvd.code)

    async def test_rotation_adds_the_new_fingerprint_then_removes_the_old_one(self) -> None:
        new_fingerprint = pki_fields(pki_run("issue-client.sh", self.pki.as_posix(), "deck-a", "t2"))["fingerprint"]
        self.assertNotEqual(self.fingerprints["deck-a"], new_fingerprint)

        async def admits(label):
            async with self.client("deck-a", label=label) as client:
                await client.admit(operation_identity=OPERATION, dataset=dict(SELECTOR))

        # 1. add the new fingerprint, restart: both are accepted
        self.write_principals({
            **self.default_principals(), "deck-a-next": (new_fingerprint, DECK_SCOPES),
        })
        self.restart_api()
        await admits("t1")
        await admits("t2")
        # 2. remove the old one, restart: the old is denied, the new still works
        self.write_principals({
            **{name: value for name, value in self.default_principals().items() if name != "deck-a"},
            "deck-a-next": (new_fingerprint, DECK_SCOPES),
        })
        self.restart_api()
        with self.assertRaises(deck.RemoteSecurityFailure):
            await admits("t1")
        await admits("t2")

    # -- acceptance 6: a large delivery ---------------------------------------------------------------------

    async def test_the_peak_memory_of_the_api_during_a_large_delivery_is_recorded(self) -> None:
        members = 2
        # A fixed size, not the bound: the bounds are in the hundreds of MiB and CI must not generate gigabytes.
        # Deliveries at the bounds were measured once, by hand, and are recorded in ADR-0072 Amendment 1.
        member_target = 28 * MIB
        rows = 120_000
        probe = trade_table(DAY_ONE, rows, seed=1, noise=True)
        with tempfile.TemporaryDirectory(dir=self.scratch) as holder:
            _, probe_path = write_partition(Path(holder), "probe", "dt=probe", probe)
            per_row = probe_path.stat().st_size / rows
        rows = int(member_target / per_row)
        self.assertLessEqual(members * member_target, ADMITTED_INPUT_BOUNDS_V1.max_total_bytes)
        with connect("ADMIN") as admin, admin.cursor() as cursor:
            seed_dataset(cursor, self.storage, "BTCUSDT", [
                (MAX_DAYS[0], trade_table(MAX_DAYS[0], rows, seed=11, noise=True)),
                (MAX_DAYS[1], trade_table(MAX_DAYS[1], rows, seed=12, first_id=5_000_001, noise=True)),
            ])
        baseline = peak_memory_bytes(self.api.pid)
        async with self.client() as client:
            admission = await client.admit(operation_identity=OPERATION, dataset=dict(MAX_SELECTOR))
            sizes = [len(data) for data in (await client.fetch_inputs(admission)).members]
        peak = peak_memory_bytes(self.api.pid)
        self.assertEqual(members, len(sizes))
        for size in sizes:
            self.assertLessEqual(size, ADMITTED_INPUT_BOUNDS_V1.max_member_bytes)
        self.assertLessEqual(sum(sizes), ADMITTED_INPUT_BOUNDS_V1.max_total_bytes)
        self.assertGreater(sum(sizes), 48 * MIB, "the delivery must be large enough to show the memory cost")
        type(self).evidence["api_peak_memory"] = {
            "platform": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
            "measure": "peak working set (Windows) or VmHWM (Linux) of the API process",
            "member_bytes": sizes, "total_bytes": sum(sizes),
            "baseline_peak_bytes": baseline, "peak_bytes_after_delivery": peak,
        }
        if peak is None:
            self.skipTest("this platform cannot report the peak memory of a process")
        self.assertGreater(peak, 0)
