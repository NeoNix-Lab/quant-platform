"""Read, admission and delivery of every instrument of the Bybit linear allow-list (issue #485, ADR-0074 sections 1 and 2).

The path is the real one: a ``DataGateway`` over a fake catalog and real files, the K12 authority and the admitted-input
transport with a SQLite admission store. Nothing about the wire or the deck changes; only the instrument does.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_bounded_datagateway_read_v1 import FakeCatalog, instant  # noqa: E402

from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import CatalogDataset, CatalogPartition  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputStore  # noqa: E402
from quant_platform.application.admitted_input_authority import (  # noqa: E402
    ADMITTED_INPUT_TRANSFER_CONFIG_V1,
    DatasetAdmittedInputAuthority,
    dataset_from_identity_text,
)
from quant_platform.application.admitted_input_transport import AdmittedInputTransport  # noqa: E402
from quant_platform.application.framed_result_transport import FramedResultTransferStore  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError  # noqa: E402
from quant_platform.data.models import DatasetIdentity, NaturalPartitionIdentity  # noqa: E402
from quant_platform.source_adapters import bybit  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1, BYBIT_ORDERING_PROVIDER  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
START, MID, END = "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"
FILES = (b"first-partition-bytes" * 40, b"second-partition-bytes" * 70)


def identity_of(instrument: str) -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", "bybit", instrument, "trade-v1")


def rel_root_of(instrument: str) -> str:
    return f"canonical/trades/bybit/{instrument}/trade-v1"


class InstrumentCatalog(FakeCatalog):
    """The fixture catalog, for one instrument instead of the fixed BTCUSDT."""

    def __init__(self, instrument: str, partitions: list[CatalogPartition]):
        super().__init__(partitions)
        self.identity = identity_of(instrument)
        self.dataset = CatalogDataset(
            identity=self.identity, catalog_dataset_id=f"dataset-{instrument}", rel_root=rel_root_of(instrument),
            manifest_sha256="a" * 64, schema_version=1, schema_hash="b" * 64,
        )

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        if identity != self.identity:
            raise AssertionError(identity)
        return self.dataset


class NoRecordsRead:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("a record was read")


class MultiInstrumentReadV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)

    def partitions(self, instrument: str) -> list[CatalogPartition]:
        storage = self.root / "storage" / instrument
        directory = storage / rel_root_of(instrument)
        directory.mkdir(parents=True, exist_ok=True)
        identity = identity_of(instrument)
        items = []
        for number, (key, start, end, data) in enumerate((
            ("hour-00", START, MID, FILES[0]), ("hour-01", MID, END, FILES[1]),
        )):
            (directory / f"{key}.parquet").write_bytes(data)
            items.append(CatalogPartition(
                natural_identity=NaturalPartitionIdentity(identity, key, 1),
                catalog_partition_id=f"partition-{instrument}-{number}",
                storage_root_id="hot",
                storage_root=str(storage),
                dataset_rel_root=rel_root_of(instrument),
                rel_path=f"{key}.parquet",
                ts_start=instant(start),
                ts_end=instant(end),
                row_count=1,
                content_sha256=hashlib.sha256(data).hexdigest(),
                manifest_sha256=hashlib.sha256(b"manifest:" + data).hexdigest(),
                state="valid",
                producer="fixture",
                code_ref="test",
            ))
        return items

    def authority(self, instrument: str) -> DatasetAdmittedInputAuthority:
        gateway = DataGateway(
            InstrumentCatalog(instrument, self.partitions(instrument)),
            batch_reader=NoRecordsRead(),
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )
        return DatasetAdmittedInputAuthority(gateway, deployment_commit=lambda: COMMIT)

    def transport(self, authority) -> AdmittedInputTransport:
        path = self.root / "admissions.sqlite"

        @contextmanager
        def session():
            connection = sqlite3.connect(path)
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()

        return AdmittedInputTransport(
            session, authority.resolve, authority.members, authority.read_member,
            transfers=FramedResultTransferStore(ADMITTED_INPUT_TRANSFER_CONFIG_V1),
        )

    @staticmethod
    def body(instrument: str) -> dict:
        return {
            "dataset": {
                "venue": "bybit", "instrument": instrument, "start": START, "end": END,
                "representation": {"kind": "trades", "version": 1},
            },
            "artifact_identities": [],
            "operation_identity": "strategy-compose-v1",
            "profile_identity": None,
        }

    def admit(self, transport, instrument):
        return transport.execute("admit", transport.decode("admit", self.body(instrument)))

    def test_the_allow_list_is_the_five_adr_instruments(self) -> None:
        self.assertEqual(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"), BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)

    def test_every_instrument_is_admitted_with_its_own_dataset_identity_and_delivered_byte_for_byte(self) -> None:
        manifests = {}
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            with self.subTest(instrument):
                authority = self.authority(instrument)
                transport = self.transport(authority)
                admitted = self.admit(transport, instrument)
                self.assertEqual("SEALED", admitted["state"].upper())
                manifest = admitted["manifest"]
                (dataset_text,) = manifest["logical_input_identities"]
                self.assertEqual(f"dataset-v1:canonical:trades:bybit:{instrument}:trade-v1", dataset_text)
                self.assertEqual(identity_of(instrument), dataset_from_identity_text(dataset_text))
                self.assertEqual(2, len(manifest["natural_partition_identities"]))
                self.assertEqual({"start": START, "end": END}, dict(manifest["declared_coverage"]))
                self.assertEqual(COMMIT, manifest["git_identity"])
                manifests[instrument] = admitted["admission_id"]
                delivered = transport.execute("deliver", transport.decode("deliver", {"admission_id": admitted["admission_id"]}))
                self.assertEqual(2, len(delivered["members"]))
                record = authority.resolve(transport.decode("admit", self.body(instrument)))
                for index, data in enumerate(FILES):
                    self.assertEqual(data, authority.read_member(record, index))
        # Different instruments are different admissions, even over identical bytes and intervals.
        self.assertEqual(len(manifests), len(set(manifests.values())))

    def test_a_symbol_outside_the_allow_list_is_refused_and_nothing_is_sealed(self) -> None:
        # The fixture holds an ADAUSDT dataset: the platform still refuses it, before the catalog.
        authority = self.authority("ADAUSDT")
        transport = self.transport(authority)
        with self.assertRaises(ConsumerApiError):
            self.admit(transport, "ADAUSDT")
        with closing(sqlite3.connect(self.root / "admissions.sqlite")) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if table.startswith("sqlite_"):
                    continue
                self.assertEqual(0, connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], table)

    def test_mutant_an_allow_list_narrowed_to_btcusdt_is_caught(self) -> None:
        with mock.patch.object(bybit, "BYBIT_LINEAR_TRADE_INSTRUMENTS_V1", ("BTCUSDT",)):
            authority = self.authority("ETHUSDT")
            with self.assertRaises(ConsumerApiError):
                self.admit(self.transport(authority), "ETHUSDT")
            self.assertTrue(bybit.bybit_trade_v1_applies_to(identity_of("BTCUSDT")))
            self.assertFalse(bybit.bybit_trade_v1_applies_to(identity_of("ETHUSDT")))


if __name__ == "__main__":
    unittest.main()
