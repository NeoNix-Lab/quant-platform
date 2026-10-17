"""K12 server authority for dataset admitted inputs (issue #391, ADR-0072 section 1).

The authority is exercised over a real ``DataGateway`` (fake catalog, real
files in a temporary storage root, a batch reader that fails the test if a
single record is read) and, for the cases that depend on the transport's own
checks, through ``AdmittedInputTransport`` with a SQLite admission store.
"""

from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_bounded_datagateway_read_v1 import (  # noqa: E402
    DATASET,
    IDENTITY,
    REL_ROOT,
    FakeCatalog,
    instant,
)

from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.access.models import CatalogPartition  # noqa: E402
from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputManifestV1,
    AdmittedInputNotFound,
    AdmittedInputState,
    AdmittedInputStore,
)
from quant_platform.application.admitted_input_authority import (  # noqa: E402
    ADMITTED_INPUT_BOUNDS_V1,
    ADMITTED_INPUT_TRANSFER_CONFIG_V1,
    DATASET_IDENTITY_PREFIX,
    PARTITION_IDENTITY_PREFIX,
    DatasetAdmittedInputAuthority,
    dataset_from_identity_text,
    dataset_identity_text,
    partition_identity_text,
)
from quant_platform.application.admitted_input_transport import (  # noqa: E402
    AdmittedInputBounds,
    AdmittedInputRequest,
    AdmittedInputTransport,
)
from quant_platform.application.framed_result_transport import (  # noqa: E402
    FramedResultConfig,
    FramedResultTransferStore,
)
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    RepresentationRef,
)
from quant_platform.data.models import (  # noqa: E402
    DatasetIdentity,
    DatasetNotFound,
    NaturalPartitionIdentity,
)
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
START, MID, END = "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"
FILE_ONE = b"first-partition-bytes" * 40
FILE_TWO = b"second-partition-bytes" * 70
MIB = 1024 * 1024


def query(**overrides) -> ConsumerMarketDataQuery:
    base = {
        "venue": "bybit", "instrument": "BTCUSDT", "start": START, "end": END,
        "representation": RepresentationRef("trades", 1),
    }
    base.update(overrides)
    return ConsumerMarketDataQuery(**base)


def request(dataset=None, *, artifacts=(), operation="strategy-compose-v1", profile="profile-v1:default"):
    return AdmittedInputRequest(
        dataset=query() if dataset is None and not artifacts else dataset,
        artifact_identities=tuple(artifacts),
        operation_identity=operation,
        profile_identity=profile,
    )


class ReadingForbidden:
    """A batch reader that fails the test: resolving must never read a record."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("a record was read")


class MissingDatasetCatalog(FakeCatalog):
    def resolve_dataset(self, identity):
        raise DatasetNotFound("no such dataset")


class AdmittedInputAuthorityV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.storage = self.root / "storage"
        self.reader = ReadingForbidden()
        self.partitions = [
            self.make_partition("hour-00", START, MID, FILE_ONE, "a"),
            self.make_partition("hour-01", MID, END, FILE_TWO, "b"),
        ]
        self.commit = COMMIT
        self.authority = self.build()

    def make_partition(self, key, start, end, data, ident, *, revision=1) -> CatalogPartition:
        directory = self.storage / REL_ROOT
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.parquet").write_bytes(data)
        return CatalogPartition(
            natural_identity=NaturalPartitionIdentity(IDENTITY, key, revision),
            catalog_partition_id=f"partition-{ident}",
            storage_root_id="hot",
            storage_root=str(self.storage),
            dataset_rel_root=REL_ROOT,
            rel_path=f"{key}.parquet",
            ts_start=instant(start),
            ts_end=instant(end),
            row_count=1,
            content_sha256=hashlib.sha256(data).hexdigest(),
            manifest_sha256=hashlib.sha256(b"manifest:" + data).hexdigest(),
            state="valid",
            producer="fixture",
            code_ref="test",
        )

    def gateway(self, catalog=None) -> DataGateway:
        return DataGateway(
            catalog or FakeCatalog(self.partitions),
            batch_reader=self.reader,
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )

    def build(self, *, catalog=None, bounds=ADMITTED_INPUT_BOUNDS_V1, commit=lambda: None):
        return DatasetAdmittedInputAuthority(
            self.gateway(catalog), deployment_commit=lambda: self.commit, bounds=bounds,
        )

    def refusal(self, call, code, reason=None):
        with self.assertRaises(ConsumerApiError) as raised:
            call()
        self.assertEqual(code, raised.exception.code)
        if reason is not None:
            self.assertEqual(reason, raised.exception.context.get("reason"))
        return raised.exception

    # -- resolve: the manifest of the ADR table ------------------------------------

    def test_resolve_builds_the_adr_manifest_from_open_scan_metadata(self) -> None:
        manifest = self.authority.resolve(request())
        one, two = (item.natural_identity for item in self.partitions)
        self.assertEqual(("dataset-v1:canonical:trades:bybit:BTCUSDT:trade-v1",), manifest.logical_input_identities)
        self.assertEqual(
            (partition_identity_text(one), partition_identity_text(two)),
            manifest.natural_partition_identities,
        )
        self.assertEqual(
            (hashlib.sha256(FILE_ONE).hexdigest(), hashlib.sha256(FILE_TWO).hexdigest()),
            manifest.content_hashes,
        )
        # Only the selected partitions' manifest hashes, in the gateway's order: the
        # dataset's own manifest hash is not part of the sealed manifest (ADR-0072 s1).
        self.assertEqual(
            tuple(item.manifest_sha256 for item in self.partitions), manifest.manifest_hashes,
        )
        self.assertEqual(len(manifest.natural_partition_identities), len(manifest.manifest_hashes))
        self.assertNotIn(DATASET.manifest_sha256, manifest.manifest_hashes)
        self.assertEqual("trade-v1", manifest.schema_identity)
        self.assertEqual("1", manifest.schema_version)
        self.assertEqual(DATASET.schema_hash, manifest.schema_hash)
        self.assertEqual({"start": START, "end": END}, dict(manifest.declared_coverage))
        self.assertTrue(manifest.request_identity and len(manifest.request_identity) == 64)
        self.assertIsNone(manifest.result_identity)
        self.assertEqual((), manifest.definition_identities)
        self.assertIsNone(manifest.implementation_identity)
        self.assertEqual(COMMIT, manifest.git_identity)

    def test_the_request_identity_is_the_gateways_for_the_same_semantic_request(self) -> None:
        from quant_platform.application.market_data import resolve_market_data_request

        expected = resolve_market_data_request(query()).request_identity
        self.assertEqual(expected, self.authority.resolve(request()).request_identity)

    def test_operation_and_profile_identities_are_carried_verbatim_and_not_a_closed_set(self) -> None:
        for operation, profile in (("anything-v9", None), ("a:b:c", "profile-v7:x")):
            manifest = self.authority.resolve(request(operation=operation, profile=profile))
            self.assertEqual(operation, manifest.operation_identity)
            self.assertEqual(profile, manifest.profile_identity)

    def test_resolving_reads_no_record_and_is_deterministic(self) -> None:
        first = self.authority.resolve(request())
        second = self.authority.resolve(request())
        self.assertEqual(first.admission_id, second.admission_id)
        self.authority.members(first)
        self.authority.read_member(first, 0)
        self.assertEqual(0, self.reader.calls)

    def test_the_scan_is_closed_so_provenance_is_never_manufactured(self) -> None:
        opened = []
        gateway = self.gateway()
        original = gateway.scan

        def scan(*args, **kwargs):
            result = original(*args, **kwargs)
            opened.append(result)
            return result

        gateway.scan = scan  # type: ignore[method-assign]
        DatasetAdmittedInputAuthority(gateway, deployment_commit=lambda: COMMIT).resolve(request())
        self.assertEqual(1, len(opened))
        self.assertEqual("aborted", opened[0].state.value)
        self.assertIsNone(opened[0].completed_metadata)

    def test_an_unrecorded_deployment_commit_refuses_with_nothing_to_seal(self) -> None:
        for value in (None, "", "   "):
            self.commit = value
            self.refusal(
                lambda: self.authority.resolve(request()),
                ConsumerErrorCode.INTEGRITY_FAILURE, "deployment_commit_unrecorded",
            )

    # -- resolve: refusals with the Consumer API's own codes ------------------------

    def test_artifact_inputs_are_not_admissible(self) -> None:
        self.refusal(
            lambda: self.authority.resolve(request(artifacts=("feature-artifact-v1:sha256:" + "ab" * 32,))),
            ConsumerErrorCode.INVALID_REQUEST, "artifact_inputs_unavailable",
        )

    def test_incomplete_coverage_is_no_coverage(self) -> None:
        self.partitions.pop()
        self.refusal(lambda: self.authority.resolve(request()), ConsumerErrorCode.NO_COVERAGE)
        self.partitions.clear()
        self.refusal(lambda: self.authority.resolve(request()), ConsumerErrorCode.NO_COVERAGE)

    def test_an_unknown_dataset_is_source_not_found(self) -> None:
        authority = self.build(catalog=MissingDatasetCatalog([]))
        self.refusal(lambda: authority.resolve(request()), ConsumerErrorCode.SOURCE_NOT_FOUND)

    def test_an_unsupported_venue_or_representation_uses_the_consumer_codes(self) -> None:
        self.refusal(
            lambda: self.authority.resolve(request(query(venue="unknown-venue"))),
            ConsumerErrorCode.SOURCE_NOT_FOUND,
        )
        self.refusal(
            lambda: self.authority.resolve(request(query(representation=RepresentationRef("bars", 1)))),
            ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
        )
        self.refusal(
            lambda: self.authority.resolve(request(query(representation=RepresentationRef("trades", 2)))),
            ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
        )

    def test_no_refusal_carries_a_path_or_a_catalog_identifier(self) -> None:
        for call in (
            lambda: self.authority.resolve(request(query(venue="unknown-venue"))),
            lambda: self.authority.resolve(request(artifacts=("x-artifact",))),
        ):
            error = self.refusal(call, error_code(call))
            text = repr((error.message, error.context))
            self.assertNotIn(str(self.root), text)
            self.assertNotIn("partition-a", text)

    # -- members and bytes ---------------------------------------------------------

    def test_members_are_the_partitions_with_file_sizes_and_the_bytes_read_through_the_catalog(self) -> None:
        manifest = self.authority.resolve(request())
        members = self.authority.members(manifest)
        self.assertEqual(manifest.natural_partition_identities, tuple(member.identity for member in members))
        self.assertEqual(manifest.content_hashes, tuple(member.content_hash for member in members))
        self.assertEqual((len(FILE_ONE), len(FILE_TWO)), tuple(member.byte_length for member in members))
        self.assertEqual(FILE_ONE, self.authority.read_member(manifest, 0))
        self.assertEqual(FILE_TWO, self.authority.read_member(manifest, 1))
        for index, member in enumerate(members):
            self.assertEqual(
                member.content_hash, hashlib.sha256(self.authority.read_member(manifest, index)).hexdigest(),
            )

    def test_an_index_outside_the_partitions_is_an_integrity_failure(self) -> None:
        manifest = self.authority.resolve(request())
        # ``True`` is an ``int`` in Python; it is not an index.
        for index in (-1, 2, True, "0", None):
            self.refusal(
                lambda: self.authority.read_member(manifest, index),
                ConsumerErrorCode.INTEGRITY_FAILURE, "member_index_out_of_range",
            )

    def test_a_catalog_that_no_longer_matches_the_manifest_delivers_nothing(self) -> None:
        manifest = self.authority.resolve(request())
        # A revised partition: same key and interval, a new revision, other bytes.
        self.partitions[1] = self.make_partition("hour-01", MID, END, b"revised-bytes", "b", revision=2)
        for call in (
            lambda: self.authority.members(manifest),
            lambda: self.authority.read_member(manifest, 1),
        ):
            self.refusal(call, ConsumerErrorCode.INTEGRITY_FAILURE, "catalog_no_longer_matches_manifest")

    def test_a_changed_content_hash_or_an_added_partition_is_detected(self) -> None:
        manifest = self.authority.resolve(request())
        original = self.partitions[0]
        for changed in (
            [original.__class__(**{**_fields(original), "content_sha256": "e" * 64}), self.partitions[1]],
            [original.__class__(**{**_fields(original), "manifest_sha256": "f" * 64}), self.partitions[1]],
        ):
            self.partitions[:] = changed
            self.refusal(
                lambda: self.authority.members(manifest),
                ConsumerErrorCode.INTEGRITY_FAILURE, "catalog_no_longer_matches_manifest",
            )

    def test_a_manifest_that_differs_in_any_sealed_field_the_catalog_vouches_for_is_refused(self) -> None:
        manifest = self.authority.resolve(request())
        for fields in (
            {"request_identity": "0" * 64},
            {"schema_hash": "9" * 64},
            {"schema_version": "2"},
            {"schema_identity": "trade-v2"},
            {"natural_partition_identities": tuple(reversed(manifest.natural_partition_identities))},
            {"natural_partition_identities": ("partition-v1:" + "0" * 64, manifest.natural_partition_identities[1])},
        ):
            other = AdmittedInputManifestV1(**{**_manifest_fields(manifest), **fields})
            self.refusal(
                lambda: self.authority.members(other),
                ConsumerErrorCode.INTEGRITY_FAILURE, "catalog_no_longer_matches_manifest",
            )

    def test_a_manifest_carrying_the_datasets_hash_is_not_the_adr_manifest(self) -> None:
        manifest = self.authority.resolve(request())
        # The ADR-conformant manifest revalidates (members resolve) ...
        self.assertEqual(2, len(self.authority.members(manifest)))
        # ... and one with the dataset's manifest hash added in front does not.
        extended = AdmittedInputManifestV1(**{
            **_manifest_fields(manifest),
            "manifest_hashes": (DATASET.manifest_sha256,) + manifest.manifest_hashes,
        })
        self.assertNotEqual(manifest.admission_id, extended.admission_id)
        self.refusal(
            lambda: self.authority.members(extended),
            ConsumerErrorCode.INTEGRITY_FAILURE, "catalog_no_longer_matches_manifest",
        )

    def test_a_new_revision_with_identical_bytes_is_a_different_partition(self) -> None:
        manifest = self.authority.resolve(request())
        self.partitions[1] = self.make_partition("hour-01", MID, END, FILE_TWO, "b", revision=2)
        self.refusal(
            lambda: self.authority.members(manifest),
            ConsumerErrorCode.INTEGRITY_FAILURE, "catalog_no_longer_matches_manifest",
        )

    def test_metadata_that_reports_incomplete_coverage_is_never_sealed(self) -> None:
        import dataclasses

        gateway = self.gateway()
        original = gateway.scan

        def scan(*args, **kwargs):
            result = original(*args, **kwargs)
            result.open_metadata = dataclasses.replace(result.open_metadata, coverage_complete=False)
            return result

        gateway.scan = scan  # type: ignore[method-assign]
        authority = DatasetAdmittedInputAuthority(gateway, deployment_commit=lambda: COMMIT)
        self.refusal(lambda: authority.resolve(request()), ConsumerErrorCode.NO_COVERAGE)

    def test_a_manifest_the_authority_did_not_make_is_not_resolvable(self) -> None:
        manifest = self.authority.resolve(request())
        for fields in (
            {"logical_input_identities": ("feature-artifact-v1:sha256:" + "ab" * 32,)},
            {"logical_input_identities": ("dataset-v1:canonical:trades:bybit:BTCUSDT",)},
            {"declared_coverage": None},
            {"declared_coverage": {"start": START}},
        ):
            other = AdmittedInputManifestV1(**{**_manifest_fields(manifest), **fields})
            self.refusal(
                lambda: self.authority.members(other),
                ConsumerErrorCode.INTEGRITY_FAILURE, "manifest_not_resolvable",
            )

    def test_a_missing_or_oversized_file_is_refused_without_naming_the_path(self) -> None:
        manifest = self.authority.resolve(request())
        small = self.build(bounds=AdmittedInputBounds(max_members=16, max_member_bytes=len(FILE_ONE), max_total_bytes=MIB))
        error = self.refusal(
            lambda: small.read_member(manifest, 1), ConsumerErrorCode.RESULT_TOO_LARGE, "member_bytes_exceeded",
        )
        self.assertNotIn(str(self.root), repr((error.message, error.context)))
        (self.storage / REL_ROOT / "hour-00.parquet").unlink()
        error = self.refusal(
            lambda: self.authority.members(manifest), ConsumerErrorCode.INTEGRITY_FAILURE, "member_unreadable",
        )
        self.assertNotIn(str(self.root), repr((error.message, error.context)))

    # -- identity texts ------------------------------------------------------------

    def test_the_dataset_identity_text_round_trips_and_carries_no_locator(self) -> None:
        for venue, instrument in (("bybit", "BTCUSDT"), ("bybit", "BTC/USD"), ("bybit", "A:B.C..d")):
            identity = DatasetIdentity("canonical", "trades", venue, instrument, "trade-v1")
            text = dataset_identity_text(identity)
            self.assertTrue(text.startswith(DATASET_IDENTITY_PREFIX))
            body = text[len(DATASET_IDENTITY_PREFIX):]
            self.assertTrue(all(character.isalnum() or character in "-_%:" for character in body), text)
            self.assertNotIn("/", text)
            self.assertNotIn("..", text)
            self.assertEqual(identity, dataset_from_identity_text(text))

    def test_a_malformed_or_non_canonical_dataset_identity_text_is_refused(self) -> None:
        good = dataset_identity_text(DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"))
        for bad in (
            "", "dataset-v1:", good + ":x", good.replace("bybit", "by%62it"), good.replace("BTCUSDT", "BTC%zz"),
            good.replace("trades", "bars"), "feature-artifact-v1:sha256:" + "ab" * 32, good.lower().replace("btcusdt", "BTCUSDT") + "%",
            "dataset-v1:canonical:trades:bybit:BTC/USD:trade-v1",
        ):
            with self.assertRaises(ValueError, msg=bad):
                dataset_from_identity_text(bad)
        with self.assertRaises(ValueError):
            dataset_identity_text(DatasetIdentity("canonical", "quotes", "bybit", "BTCUSDT", "trade-v1"))

    def test_partition_identities_distinguish_key_revision_and_dataset(self) -> None:
        base = NaturalPartitionIdentity(IDENTITY, "hour-00", 1)
        other_dataset = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
        texts = {
            partition_identity_text(base),
            partition_identity_text(NaturalPartitionIdentity(IDENTITY, "hour-01", 1)),
            partition_identity_text(NaturalPartitionIdentity(IDENTITY, "hour-00", 2)),
            partition_identity_text(NaturalPartitionIdentity(other_dataset, "hour-00", 1)),
        }
        self.assertEqual(4, len(texts))
        self.assertEqual(partition_identity_text(base), partition_identity_text(NaturalPartitionIdentity(IDENTITY, "hour-00", 1)))
        for text in texts:
            self.assertRegex(text, rf"{PARTITION_IDENTITY_PREFIX}[0-9a-f]{{64}}")

    # -- bounds and composition with the transport ---------------------------------

    def test_the_bounds_are_the_adr_values_and_compose_with_the_transport(self) -> None:
        self.assertEqual(
            (16, 512 * MIB, 1024 * MIB),
            (ADMITTED_INPUT_BOUNDS_V1.max_members, ADMITTED_INPUT_BOUNDS_V1.max_member_bytes, ADMITTED_INPUT_BOUNDS_V1.max_total_bytes),
        )
        config = ADMITTED_INPUT_TRANSFER_CONFIG_V1
        self.assertEqual((600, 1536 * MIB, 32), (config.retention_seconds, config.max_retained_payload_bytes, config.max_in_flight_transfers))
        # The transport refuses bounds the transfer store cannot hold: this constructs.
        self.transport(transfers=FramedResultTransferStore(config))
        with self.assertRaises(ValueError):
            self.transport(transfers=FramedResultTransferStore(FramedResultConfig()))  # the shipped 8 MiB store
        # The rule is 4 * ceil(total / 3) plus 8 KiB per member: 1 GiB of members needs about 1.37 GiB retained.
        with self.assertRaises(ValueError):
            self.transport(transfers=FramedResultTransferStore(dataclasses.replace(config, max_retained_payload_bytes=1280 * MIB)))
        self.transport(transfers=FramedResultTransferStore(dataclasses.replace(config, max_retained_payload_bytes=1400 * MIB)))

    def transport(self, *, transfers=None, bounds=ADMITTED_INPUT_BOUNDS_V1, authority=None):
        authority = authority or self.authority
        path = self.root / "admissions.sqlite"

        @contextmanager
        def session():
            connection = sqlite3.connect(path)
            try:
                yield AdmittedInputStore(connection)
            finally:
                connection.close()

        self.transfers = transfers or FramedResultTransferStore(ADMITTED_INPUT_TRANSFER_CONFIG_V1)
        self.session = session
        return AdmittedInputTransport(
            session, authority.resolve, authority.members, authority.read_member,
            transfers=self.transfers, bounds=bounds,
        )

    def admit(self, transport, body):
        return transport.execute("admit", transport.decode("admit", body))

    def body(self, **overrides):
        body = {
            "dataset": {
                "venue": "bybit", "instrument": "BTCUSDT", "start": START, "end": END,
                "representation": {"kind": "trades", "version": 1},
            },
            "artifact_identities": [],
            "operation_identity": "strategy-compose-v1",
            "profile_identity": None,
        }
        body.update(overrides)
        return body

    def test_admit_and_deliver_through_the_transport_publish_every_partition(self) -> None:
        transport = self.transport()
        admitted = self.admit(transport, self.body())
        self.assertEqual("SEALED", admitted["state"].upper())
        document = transport.execute("deliver", transport.decode("deliver", {"admission_id": admitted["admission_id"]}))
        self.assertEqual(2, len(document["members"]))
        self.assertEqual(2, len(self.transfers._transfers))
        with self.session() as store:
            self.assertEqual(AdmittedInputState.DELIVERY_PENDING, store.get(admitted["admission_id"]).state)

    def test_a_refused_admit_seals_nothing(self) -> None:
        transport = self.transport()
        before = self.partitions[:]
        self.partitions.pop()  # coverage no longer complete
        with self.assertRaises(ConsumerApiError) as raised:
            self.admit(transport, self.body())
        self.assertEqual(ConsumerErrorCode.NO_COVERAGE, raised.exception.code)
        self.partitions[:] = before
        manifest = self.authority.resolve(request())
        with self.session() as store:
            with self.assertRaises(AdmittedInputNotFound):
                store.get(manifest.admission_id)

    def test_artifact_inputs_through_the_transport_are_invalid_request_and_seal_nothing(self) -> None:
        transport = self.transport()
        with self.assertRaises(ConsumerApiError) as raised:
            self.admit(transport, self.body(dataset=None, artifact_identities=["feature-artifact-v1:sha256:" + "ab" * 32]))
        self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, raised.exception.code)
        self.assertEqual("artifact_inputs_unavailable", raised.exception.context["reason"])

    def test_bounds_refuse_before_sealing(self) -> None:
        for bounds, reason in (
            (AdmittedInputBounds(max_members=1, max_member_bytes=MIB, max_total_bytes=MIB), "member_count_exceeded"),
            (AdmittedInputBounds(max_members=16, max_member_bytes=len(FILE_ONE), max_total_bytes=MIB), "member_bytes_exceeded"),
            (AdmittedInputBounds(max_members=16, max_member_bytes=len(FILE_TWO), max_total_bytes=len(FILE_ONE) + len(FILE_TWO) - 1), "total_bytes_exceeded"),
        ):
            transport = self.transport(bounds=bounds, transfers=FramedResultTransferStore(FramedResultConfig(max_retained_payload_bytes=96 * MIB)))
            with self.assertRaises(ConsumerApiError) as raised:
                self.admit(transport, self.body())
            self.assertEqual(ConsumerErrorCode.RESULT_TOO_LARGE, raised.exception.code)
            self.assertEqual(reason, raised.exception.context["reason"])
            with self.session() as store:
                with self.assertRaises(AdmittedInputNotFound):
                    store.get(self.authority.resolve(request()).admission_id)

    def test_bytes_that_changed_after_admission_are_not_delivered(self) -> None:
        transport = self.transport()
        admitted = self.admit(transport, self.body())
        (self.storage / REL_ROOT / "hour-01.parquet").write_bytes(FILE_TWO[:-1] + b"X")
        with self.assertRaises(ConsumerApiError) as raised:
            transport.execute("deliver", transport.decode("deliver", {"admission_id": admitted["admission_id"]}))
        self.assertEqual(ConsumerErrorCode.INTEGRITY_FAILURE, raised.exception.code)
        self.assertEqual(0, len(self.transfers._transfers))
        with self.session() as store:
            self.assertEqual(AdmittedInputState.SEALED, store.get(admitted["admission_id"]).state)

    def test_the_constructor_validates_its_parts(self) -> None:
        gateway = self.gateway()
        for kwargs in (
            {"deployment_commit": "not callable"},
            {"deployment_commit": lambda: COMMIT, "path_resolver": "no"},
            {"deployment_commit": lambda: COMMIT, "bounds": object()},
        ):
            with self.assertRaises(TypeError):
                DatasetAdmittedInputAuthority(gateway, **kwargs)
        with self.assertRaises(TypeError):
            DatasetAdmittedInputAuthority(object(), deployment_commit=lambda: COMMIT)


def error_code(call):
    try:
        call()
    except ConsumerApiError as error:
        return error.code
    raise AssertionError("no refusal")


def _fields(partition: CatalogPartition) -> dict:
    return {name: getattr(partition, name) for name in partition.__dataclass_fields__}


def _manifest_fields(manifest: AdmittedInputManifestV1) -> dict:
    return {name: getattr(manifest, name) for name in manifest.__dataclass_fields__}


if __name__ == "__main__":
    unittest.main()
