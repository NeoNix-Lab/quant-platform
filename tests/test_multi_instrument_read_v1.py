"""Read, admission and delivery of every instrument of the Bybit linear allow-list (issue #485, ADR-0074 sections 1 and 2).

The path is the real one: canonical ``trade-v1`` Parquet partitions written by the Bybit materializer, a ``DataGateway`` with the
default Parquet reader over a fake catalog, the K12 authority, and the admitted-input transport with a SQLite admission store.
A read consumes the records; a delivery is checked by fetching each member through the J14 transfer its descriptor names and
reassembling it, not by reading the files again. Nothing about the wire or the deck changes; only the instrument does.
"""

from __future__ import annotations

import base64
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

from test_bounded_datagateway_read_v1 import FakeCatalog  # noqa: E402

from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import CatalogDataset, CatalogPartition  # noqa: E402
from quant_platform.application import admitted_input_transport  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputStore  # noqa: E402
from quant_platform.application.admitted_input_authority import (  # noqa: E402
    ADMITTED_INPUT_TRANSFER_CONFIG_V1,
    DatasetAdmittedInputAuthority,
    dataset_from_identity_text,
    partition_identity_text,
)
from quant_platform.application.admitted_input_transport import (  # noqa: E402
    J02_ADMITTED_INPUT_SCOPE,
    AdmittedInputTransport,
)
from quant_platform.application.framed_result_transport import (  # noqa: E402
    FramedResultReceiver,
    FramedResultResumeRequest,
    FramedResultTransferStore,
    FramedResultTransportError,
)
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerApiError,
    ConsumerMarketDataQuery,
    RepresentationRef,
    resolve_market_data_request,
)
from quant_platform.data.models import DatasetIdentity, Instant, NaturalPartitionIdentity, TradeRecord  # noqa: E402
from quant_platform.source_adapters import bybit  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    BYBIT_ORDERING_PROVIDER,
    materialize_bybit_trade_v1,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
START, MID, END = "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"
# Three trades per hour; the same timestamp twice so the trade-id tie-break is exercised.
OFFSETS = (("00:10:00", "a-1"), ("00:10:00", "a-2"), ("00:40:00", "b-1"))


def identity_of(instrument: str) -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", "bybit", instrument, "trade-v1")


def rel_root_of(instrument: str) -> str:
    return f"canonical/trades/bybit/{instrument}/trade-v1"


def records_for(instrument: str, hour: int) -> list[TradeRecord]:
    return [
        TradeRecord(
            venue="bybit", instrument=instrument,
            exchange_ts=Instant.parse(f"2024-01-01T{hour:02d}:{offset[3:]}Z"),
            price=f"{100 + hour}.{index}", size="0.5", aggressor_side="buy" if index % 2 else "sell",
            trade_id=f"{instrument}-{hour}-{trade_id}",
        )
        for index, (offset, trade_id) in enumerate(OFFSETS)
    ]


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
    """The K12 authority resolves from scan metadata only; it must never read a record."""

    def __call__(self, *_args, **_kwargs):
        raise AssertionError("a record was read")


class MultiInstrumentReadV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.files: dict[str, list[bytes]] = {}
        self.catalog_partitions: dict[str, list[CatalogPartition]] = {}

    # -- fixture: real canonical Parquet ----------------------------------------------------------------

    def partitions(self, instrument: str) -> list[CatalogPartition]:
        if instrument in self.catalog_partitions:
            return self.catalog_partitions[instrument]
        storage = self.root / "storage" / instrument
        directory = storage / rel_root_of(instrument)
        directory.mkdir(parents=True, exist_ok=True)
        identity = identity_of(instrument)
        items, files = [], []
        for hour, (key, start, end) in enumerate((("hour-00", START, MID), ("hour-01", MID, END))):
            path = directory / f"{key}.parquet"
            records = records_for(instrument, hour)
            if bybit.bybit_trade_v1_applies_to(identity):
                materialize_bybit_trade_v1(path, records, dataset_identity=identity)
            else:  # a symbol outside the allow-list cannot be materialized under the profile: any bytes will do
                path.write_bytes(b"not-admitted-" + instrument.encode())
            data = path.read_bytes()
            files.append(data)
            items.append(CatalogPartition(
                natural_identity=NaturalPartitionIdentity(identity, key, 1),
                catalog_partition_id=f"partition-{instrument}-{hour}",
                storage_root_id="hot",
                storage_root=str(storage),
                dataset_rel_root=rel_root_of(instrument),
                rel_path=f"{key}.parquet",
                ts_start=Instant.parse(start),
                ts_end=Instant.parse(end),
                row_count=len(records),
                content_sha256=hashlib.sha256(data).hexdigest(),
                manifest_sha256=hashlib.sha256(b"manifest:" + data).hexdigest(),
                state="valid",
                producer="fixture",
                code_ref="test",
            ))
        self.files[instrument] = files
        self.catalog_partitions[instrument] = items
        return items

    def gateway(self, instrument: str, **reader) -> DataGateway:
        return DataGateway(
            InstrumentCatalog(instrument, self.partitions(instrument)),
            ordering_providers=(BYBIT_ORDERING_PROVIDER,), **reader,
        )

    def authority(self, instrument: str) -> DatasetAdmittedInputAuthority:
        return DatasetAdmittedInputAuthority(
            self.gateway(instrument, batch_reader=NoRecordsRead()), deployment_commit=lambda: COMMIT,
        )

    def transport(self, authority) -> AdmittedInputTransport:
        path = self.root / "admissions.sqlite"

        @contextmanager
        def session():
            connection = sqlite3.connect(path)
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()

        self.transfers = FramedResultTransferStore(ADMITTED_INPUT_TRANSFER_CONFIG_V1)
        return AdmittedInputTransport(
            session, authority.resolve, authority.members, authority.read_member, transfers=self.transfers,
        )

    @staticmethod
    def query(instrument: str) -> ConsumerMarketDataQuery:
        return ConsumerMarketDataQuery("bybit", instrument, START, END, RepresentationRef("trades", 1))

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

    # -- the two checks: a real read, and a delivery fetched through its transfers ---------------------------

    def assert_read(self, instrument: str, gateway: DataGateway) -> None:
        result = gateway.read(resolve_market_data_request(self.query(instrument)))
        expected = sorted(
            (record for hour in (0, 1) for record in records_for(instrument, hour)),
            key=lambda record: (record.exchange_ts, record.trade_id),
        )
        self.assertEqual(
            [(r.instrument, r.exchange_ts, r.trade_id, r.price, r.size, r.aggressor_side) for r in expected],
            [(r.instrument, r.exchange_ts, r.trade_id, r.price, r.size, r.aggressor_side) for r in result.records],
        )
        metadata = result.metadata
        self.assertEqual(identity_of(instrument), metadata.dataset_identity)
        self.assertTrue(metadata.coverage_complete)
        self.assertEqual((), metadata.coverage_gaps)
        self.assertEqual(6, metadata.row_count)

    def fetch_members(self, delivered: dict) -> list[dict]:
        """What the deck does with the deliver response: resume each named transfer and reassemble it."""
        documents = []
        for descriptor in delivered["members"]:
            frames = self.transfers.resume(
                FramedResultResumeRequest(descriptor["transfer_id"], 0), owner=J02_ADMITTED_INPUT_SCOPE,
            )
            receiver = FramedResultReceiver()
            document = None
            for frame in frames:
                document = receiver.receive(frame)
            self.assertIsNotNone(document, "the transfer did not reassemble")
            documents.append(document)
        return documents

    def assert_delivered(self, instrument: str, admitted: dict, delivered: dict) -> None:
        manifest = admitted["manifest"]
        documents = self.fetch_members(delivered)
        self.assertEqual(len(self.files[instrument]), len(documents))
        partitions = [partition_identity_text(item.natural_identity) for item in self.partitions(instrument)]
        self.assertEqual(partitions, list(manifest["natural_partition_identities"]))
        for index, (document, descriptor, data) in enumerate(zip(documents, delivered["members"], self.files[instrument])):
            self.assertEqual(admitted["admission_id"], document["admission_id"])
            self.assertEqual(index, document["member_index"])
            self.assertEqual(partitions[index], document["member_identity"])
            self.assertEqual(descriptor["member_identity"], document["member_identity"])
            payload = base64.b64decode(document["bytes"], validate=True)
            self.assertEqual(data, payload)  # byte for byte, from the transfer, not from the file
            self.assertEqual(manifest["content_hashes"][index], hashlib.sha256(payload).hexdigest())

    def admit_and_deliver(self, instrument: str):
        transport = self.transport(self.authority(instrument))
        admitted = transport.execute("admit", transport.decode("admit", self.body(instrument)))
        delivered = transport.execute("deliver", transport.decode("deliver", {"admission_id": admitted["admission_id"]}))
        return admitted, delivered

    # -- the acceptance --------------------------------------------------------------------------------------

    def test_the_allow_list_is_the_five_adr_instruments(self) -> None:
        self.assertEqual(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"), BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)

    def test_every_instrument_is_read_through_the_gateway_record_by_record(self) -> None:
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:  # BTCUSDT included: the regression
            with self.subTest(instrument):
                self.assert_read(instrument, self.gateway(instrument))

    def test_every_instrument_is_admitted_and_delivered_byte_for_byte_through_its_transfers(self) -> None:
        admissions = set()
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            with self.subTest(instrument):
                admitted, delivered = self.admit_and_deliver(instrument)
                self.assertEqual("SEALED", admitted["state"].upper())
                (dataset_text,) = admitted["manifest"]["logical_input_identities"]
                self.assertEqual(f"dataset-v1:canonical:trades:bybit:{instrument}:trade-v1", dataset_text)
                self.assertEqual(identity_of(instrument), dataset_from_identity_text(dataset_text))
                self.assertEqual(COMMIT, admitted["manifest"]["git_identity"])
                self.assert_delivered(instrument, admitted, delivered)
                admissions.add(admitted["admission_id"])
        self.assertEqual(len(BYBIT_LINEAR_TRADE_INSTRUMENTS_V1), len(admissions))

    def test_a_symbol_outside_the_allow_list_is_refused_and_nothing_is_sealed(self) -> None:
        # The fixture holds an ADAUSDT dataset: the platform still refuses it, before the catalog.
        transport = self.transport(self.authority("ADAUSDT"))
        with self.assertRaises(ConsumerApiError):
            transport.execute("admit", transport.decode("admit", self.body("ADAUSDT")))
        with self.assertRaises(Exception) as refused:
            self.gateway("ADAUSDT").read(resolve_market_data_request(self.query("ADAUSDT")))
        self.assertEqual("UnsupportedDatasetKind", type(refused.exception).__name__)
        with closing(sqlite3.connect(self.root / "admissions.sqlite")) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                if not table.startswith("sqlite_"):
                    self.assertEqual(0, connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], table)

    # -- the checks fail when the property is weakened ---------------------------------------------------------

    def test_mutant_an_allow_list_narrowed_to_btcusdt_is_caught(self) -> None:
        self.partitions("ETHUSDT")  # materialized while the instrument is admitted
        with mock.patch.object(bybit, "BYBIT_LINEAR_TRADE_INSTRUMENTS_V1", ("BTCUSDT",)):
            with self.assertRaises(ConsumerApiError):
                self.admit_and_deliver("ETHUSDT")
            with self.assertRaises(Exception):
                self.assert_read("ETHUSDT", self.gateway("ETHUSDT"))

    def test_mutant_a_broken_or_silent_reader_fails_the_read_check(self) -> None:
        def broken(*_args, **_kwargs):
            raise OSError("all record reads broken")

        def silent(*_args, **_kwargs):
            return iter(())

        with self.assertRaises(OSError):  # the reader's failure reaches the read
            self.assert_read("ETHUSDT", self.gateway("ETHUSDT", batch_reader=broken))
        with self.assertRaises(AssertionError):  # a reader that returns nothing is not a read
            self.assert_read("ETHUSDT", self.gateway("ETHUSDT", batch_reader=silent))

    def test_mutant_corrupted_delivery_descriptors_fail_the_delivery_check(self) -> None:
        real = admitted_input_transport.AdmittedInputTransport._deliver

        def corrupted(self_, store, admission_id):
            result = real(self_, store, admission_id)
            result["members"] = [
                {**item, "transfer_id": "0" * 64, "member_identity": "wrong"} for item in result["members"]
            ]
            return result

        with mock.patch.object(admitted_input_transport.AdmittedInputTransport, "_deliver", corrupted):
            admitted, delivered = self.admit_and_deliver("ETHUSDT")
        with self.assertRaises(FramedResultTransportError) as refused:  # no such transfer
            self.assert_delivered("ETHUSDT", admitted, delivered)
        self.assertEqual("transfer_expired", refused.exception.outcome)
        # A transfer of another member, named in place of the right one, is refused too.
        admitted, delivered = self.admit_and_deliver("SOLUSDT")
        swapped = {**delivered, "members": list(reversed(delivered["members"]))}
        with self.assertRaises(AssertionError):
            self.assert_delivered("SOLUSDT", admitted, swapped)


if __name__ == "__main__":
    unittest.main()
