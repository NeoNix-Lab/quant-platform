"""Server authority for dataset admitted inputs: the three K12 seams (issue #391).

ADR-0072 section 1. :func:`admitted_input_family_handlers` takes three
server-owned seams -- ``resolve``, ``members`` and ``read_member`` -- and this
module provides them over the DataGateway and the catalog, for the one input
v1 admits: a *dataset selector* (a :class:`ConsumerMarketDataQuery` for
``trades@1``). Composition in a host is a separate issue (S03); nothing here
opens a connection of its own.

``resolve``. The request is resolved by the Consumer API's own
:func:`resolve_market_data_request`, so the semantic request is identical to a
C03 query, and the sealed manifest is built from the gateway's *open* scan
metadata (:class:`DataScanOpenMetadata`): the scan is opened, never iterated,
and closed, so no record is read. Failures are the Consumer API's own
(``no_coverage``, ``source_not_found``, ``unsupported_representation``),
translated by the same functions it uses; nothing is sealed on a refusal. An
``admit`` that names accepted artifacts is ``invalid_request`` with the reason
``artifact_inputs_unavailable``: no accepted-artifact store exists.

``members`` and ``read_member``. Member *i* is partition *i*. The manifest alone
(it is all those seams receive) names the dataset (its one logical input
identity), the requested interval (its declared coverage) and the partitions;
the catalog is re-queried through the gateway and must still yield exactly the
sealed manifest -- the same request identity, schema, partition manifest hashes,
content hashes and natural partition identities. Anything else (a revised partition, a
changed dataset) is an integrity failure: bytes are never delivered for a
manifest the catalog no longer stands behind. A partition's length is its
file's size, its bytes are that file read through the catalog's storage root;
the path is resolved here and never leaves this module. The transport still
hashes every member against the manifest before anything is retained.

Identities. The dataset identity text is decodable
(``dataset-v1:<layer>:<kind>:<venue>:<instrument>:<schema>``, every field
percent-encoded so no separator, slash, dot or locator can appear) because
``members`` has only the manifest to find the dataset again; a partition's
identity text is ``partition-v1:`` plus the SHA-256 of the canonical encoding of
its natural identity, matched by recomputation, never decoded. Operation and
profile identities are carried verbatim from the request: nothing here
interprets them, and they are never an authorization.

Not provided in v1 (ADR-0072 section 1): accepted-artifact inputs, any
time-based expiry, a closed registry of operation or profile identities.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from pathlib import Path

from quant_platform.canonical import canonical_bytes

from ..access.gateway import DataGateway, DataScanOpenMetadata
from ..access.models import CatalogPartition, DataRequest
from ..data.models import DataGatewayError, DatasetIdentity, NaturalPartitionIdentity
from ..data.parquet import resolve_partition_path
from .admitted_input import AdmittedInputManifestV1
from .admitted_input_transport import (
    AdmittedInputBounds,
    AdmittedInputMember,
    AdmittedInputRequest,
    aligned_partition_members,
)
from .framed_result_transport import FramedResultConfig
from .market_data import (
    TRADES_V1_KIND,
    TRADES_V1_VERSION,
    ApplicationRequestError,
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    NormalizedMarketDataQuery,
    RepresentationRef,
    _gateway_phase_error,
    _request_phase_error,
    resolve_market_data_request,
)

MIB = 1024 * 1024

# ADR-0072 section 1. Configuration, not wire format: the shipped transport
# defaults (3 MiB per member, 4 MiB in total, 8 MiB retained) would refuse any
# realistic daily partition. No real partition size was measured for these
# values; S07 records the measured sizes against them.
ADMITTED_INPUT_BOUNDS_V1 = AdmittedInputBounds(
    max_members=16,
    max_member_bytes=32 * MIB,
    max_total_bytes=64 * MIB,
)
# The J14 store sized by the composition rule the transport enforces
# (``4 * ceil(max_total_bytes / 3)`` plus 8 KiB per member) with headroom.
ADMITTED_INPUT_TRANSFER_CONFIG_V1 = FramedResultConfig(
    retention_seconds=300,
    max_retained_payload_bytes=96 * MIB,
    max_in_flight_transfers=32,
)

DATASET_IDENTITY_PREFIX = "dataset-v1:"
PARTITION_IDENTITY_PREFIX = "partition-v1:"
_LAYER = "canonical"
_DATASET_KIND = "trades"
_RECORD_SCHEMA_ID = "trade-v1"
_SAFE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_HEX = frozenset(b"0123456789ABCDEF")


def _encode(value: str) -> str:
    """Percent-encode every byte but letters, digits, ``-`` and ``_``."""
    return "".join(
        chr(byte) if byte in _SAFE else f"%{byte:02X}" for byte in value.encode("utf-8")
    )


def _decode(value: str) -> str:
    raw = bytearray()
    index = 0
    data = value.encode("ascii")
    while index < len(data):
        byte = data[index]
        if byte == 0x25:  # "%"
            digits = data[index + 1:index + 3]
            if len(digits) != 2 or not all(digit in _HEX for digit in digits):
                raise ValueError("malformed percent-encoding")
            raw.append(int(digits.decode("ascii"), 16))
            index += 3
        elif byte in _SAFE:
            raw.append(byte)
            index += 1
        else:
            raise ValueError("unexpected character")
    return raw.decode("utf-8")


def dataset_identity_text(identity: DatasetIdentity) -> str:
    """The manifest's logical input identity for a ``trades@1`` dataset."""
    if (
        identity.layer != _LAYER
        or identity.dataset_kind != _DATASET_KIND
        or identity.record_schema_id != _RECORD_SCHEMA_ID
    ):
        raise ValueError("only the canonical trades trade-v1 dataset has an identity text")
    return DATASET_IDENTITY_PREFIX + ":".join(
        _encode(part)
        for part in (
            identity.layer, identity.dataset_kind, identity.venue,
            identity.instrument, identity.record_schema_id,
        )
    )


def dataset_from_identity_text(text: str) -> DatasetIdentity:
    """Invert :func:`dataset_identity_text`; anything else is a ``ValueError``."""
    if type(text) is not str or not text.startswith(DATASET_IDENTITY_PREFIX):
        raise ValueError("not a dataset identity text")
    parts = text[len(DATASET_IDENTITY_PREFIX):].split(":")
    if len(parts) != 5:
        raise ValueError("not a dataset identity text")
    layer, kind, venue, instrument, schema = (_decode(part) for part in parts)
    identity = DatasetIdentity(layer, kind, venue, instrument, schema)
    if dataset_identity_text(identity) != text:
        raise ValueError("not the canonical dataset identity text")
    return identity


def partition_identity_text(identity: NaturalPartitionIdentity) -> str:
    """The manifest's natural partition identity for a catalog partition."""
    encoded = canonical_bytes(identity.stable_dict(), profile="sorted-compact-ascii-v1", allow_nan=False)
    return PARTITION_IDENTITY_PREFIX + hashlib.sha256(encoded).hexdigest()


def _refusal(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INVALID_REQUEST, "the request is not valid", context={"reason": reason},
    )


def _integrity(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INTEGRITY_FAILURE,
        "the platform cannot produce a trustworthy result",
        context={"reason": reason},
    )


def _too_large(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.RESULT_TOO_LARGE,
        "the admitted input exceeds the configured delivery bounds",
        context={"reason": reason},
    )


class DatasetAdmittedInputAuthority:
    """The ``resolve``, ``members`` and ``read_member`` seams over one gateway.

    ``deployment_commit`` returns the platform commit the API recorded at
    startup (``runtime.deployment_commit``); it is the manifest's
    ``git_identity``. An unrecorded commit is an integrity failure: a manifest
    without a code identity would be refused later by K13 anyway.
    """

    def __init__(
        self,
        gateway: DataGateway,
        *,
        deployment_commit: Callable[[], str | None],
        path_resolver: Callable[[str, str, str], Path] = resolve_partition_path,
        bounds: AdmittedInputBounds = ADMITTED_INPUT_BOUNDS_V1,
    ):
        if not callable(getattr(gateway, "scan", None)):
            raise TypeError("gateway must provide scan(request)")
        if not callable(deployment_commit) or not callable(path_resolver):
            raise TypeError("deployment_commit and path_resolver must be callable")
        if not isinstance(bounds, AdmittedInputBounds):
            raise TypeError("bounds must be AdmittedInputBounds")
        self._gateway = gateway
        self._commit = deployment_commit
        self._path_resolver = path_resolver
        self._bounds = bounds

    # -- resolve ---------------------------------------------------------------

    def resolve(self, request: AdmittedInputRequest) -> AdmittedInputManifestV1:
        if request.dataset is None:
            raise _refusal("artifact_inputs_unavailable")
        metadata, partitions = self._open(request.dataset)
        if not metadata.coverage_complete:
            raise ConsumerApiError(
                ConsumerErrorCode.NO_COVERAGE, "the requested coverage is not available",
                context={"requested_interval": metadata.requested_interval.stable_dict()},
            )
        commit = self._commit()
        if type(commit) is not str or not commit.strip():
            raise _integrity("deployment_commit_unrecorded")
        if (
            not metadata.natural_partitions
            or len(metadata.natural_partitions) != len(metadata.content_hashes)
            or len(partitions) != len(metadata.natural_partitions)
        ):
            raise _integrity("partitions_not_deliverable")
        try:
            return AdmittedInputManifestV1(
                logical_input_identities=(dataset_identity_text(metadata.dataset_identity),),
                natural_partition_identities=tuple(
                    partition_identity_text(item) for item in metadata.natural_partitions
                ),
                schema_identity=metadata.record_schema_id,
                schema_version=str(metadata.schema_version),
                schema_hash=metadata.schema_hash,
                manifest_hashes=tuple(partition.manifest_sha256 for partition in partitions),
                content_hashes=metadata.content_hashes,
                declared_coverage=metadata.requested_interval.stable_dict(),
                request_identity=metadata.request_identity,
                result_identity=None,
                definition_identities=(),
                implementation_identity=None,
                git_identity=commit,
                operation_identity=request.operation_identity,
                profile_identity=request.profile_identity,
            )
        except (ValueError, TypeError) as error:
            raise _integrity("manifest_not_expressible") from error

    # -- members and bytes -----------------------------------------------------

    def members(self, manifest: AdmittedInputManifestV1) -> tuple[AdmittedInputMember, ...]:
        partitions = self._partitions(manifest)
        sizes = tuple(self._size(self._path(partition)) for partition in partitions)
        return aligned_partition_members(manifest, sizes)

    def read_member(self, manifest: AdmittedInputManifestV1, index: int) -> bytes:
        partitions = self._partitions(manifest)
        if type(index) is not int or not 0 <= index < len(partitions):
            raise _integrity("member_index_out_of_range")
        path = self._path(partitions[index])
        if self._size(path) > self._bounds.max_member_bytes:
            raise _too_large("member_bytes_exceeded")
        try:
            return path.read_bytes()
        except OSError as error:
            raise _integrity("member_unreadable") from error

    # -- internals -------------------------------------------------------------

    def _open(self, query: ConsumerMarketDataQuery) -> tuple[DataScanOpenMetadata, tuple[CatalogPartition, ...]]:
        """Open (never iterate) the gateway scan the Consumer API would run, then close it."""
        try:
            data_request: DataRequest = resolve_market_data_request(query)
        except (ApplicationRequestError, DataGatewayError) as error:
            raise _request_phase_error(query, error) from error
        normalized = NormalizedMarketDataQuery(
            venue=data_request.dataset_selector.venue,
            instrument=data_request.dataset_selector.instrument,
            start=data_request.start,
            end=data_request.end,
            representation_kind=query.representation.kind,
            representation_version=query.representation.version,
        )
        try:
            scan = self._gateway.scan(data_request)
        except DataGatewayError as error:
            raise _gateway_phase_error(normalized, normalized.request_identity, error) from error
        try:
            return scan.open_metadata, tuple(scan.partitions)
        finally:
            scan.close()

    def _partitions(self, manifest: AdmittedInputManifestV1) -> tuple[CatalogPartition, ...]:
        """The catalog partitions a sealed manifest names, only if the catalog still agrees."""
        try:
            (dataset_text,) = manifest.logical_input_identities
            dataset = dataset_from_identity_text(dataset_text)
            coverage = manifest.declared_coverage
            if not isinstance(coverage, Mapping) or set(coverage) != {"start", "end"}:
                raise ValueError("declared coverage is not an interval")
            query = ConsumerMarketDataQuery(
                venue=dataset.venue,
                instrument=dataset.instrument,
                start=coverage["start"],
                end=coverage["end"],
                representation=RepresentationRef(TRADES_V1_KIND, TRADES_V1_VERSION),
            )
        except (ValueError, TypeError, KeyError) as error:
            raise _integrity("manifest_not_resolvable") from error
        metadata, partitions = self._open(query)
        if (
            metadata.request_identity != manifest.request_identity
            or metadata.record_schema_id != manifest.schema_identity
            or str(metadata.schema_version) != manifest.schema_version
            or metadata.schema_hash != manifest.schema_hash
            or tuple(partition.manifest_sha256 for partition in partitions) != tuple(manifest.manifest_hashes)
            or tuple(metadata.content_hashes) != tuple(manifest.content_hashes)
            or tuple(partition_identity_text(item) for item in metadata.natural_partitions)
            != tuple(manifest.natural_partition_identities)
            or len(partitions) != len(manifest.content_hashes)
        ):
            raise _integrity("catalog_no_longer_matches_manifest")
        return partitions

    def _path(self, partition: CatalogPartition) -> Path:
        try:
            return Path(self._path_resolver(
                partition.storage_root, partition.dataset_rel_root, partition.rel_path,
            ))
        except Exception as error:  # The resolver's text carries a path.
            raise _integrity("member_unreadable") from error

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError as error:
            raise _integrity("member_unreadable") from error


__all__ = [
    "ADMITTED_INPUT_BOUNDS_V1",
    "ADMITTED_INPUT_TRANSFER_CONFIG_V1",
    "DATASET_IDENTITY_PREFIX",
    "PARTITION_IDENTITY_PREFIX",
    "DatasetAdmittedInputAuthority",
    "dataset_from_identity_text",
    "dataset_identity_text",
    "partition_identity_text",
]
